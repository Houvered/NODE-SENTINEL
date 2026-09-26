# -*- coding: utf-8 -*-
"""Review queue: approve/reject, duplicates+merge, evidence-linked graph build."""
import pytest
from fastapi.testclient import TestClient

import app.core.case_store as cs_mod
from app.config import settings

FIR_TEXT = (
    "FIR No. 2026-SYN-77 filed on 2026-09-21. Accused Zaid Ansari called +919990001111 "
    "and met Faiz Sheikh at Okhla. Zaid Ansari drove vehicle DL99XY1234. "
    "Zaid Ansari transferred INR 50000 to ACC821001122."
)


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CASES_DB", str(tmp_path / "cases.db"))
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    cs_mod._store = None
    import app.main as main
    with TestClient(main.app) as c:
        r = c.post("/api/auth/login", json={"username": "investigator", "password": "Investigator123!"})
        assert r.status_code == 200
        inv = {"Authorization": "Bearer " + r.json()["access_token"]}
        r = c.post("/api/cases", headers=inv, json={"title": "Review Case"})
        case_id = r.json()["id"]
        r = c.post(f"/api/cases/{case_id}/documents", headers=inv,
                   files={"file": ("fir.txt", FIR_TEXT.encode(), "text/plain")})
        doc_id = r.json()["document"]["id"]
        r = c.post(f"/api/documents/{doc_id}/process", headers=inv)
        assert r.json()["status"] == "succeeded"
        yield c, inv, case_id, doc_id
    cs_mod._store = None


def test_review_and_build(setup):
    c, inv, case_id, doc_id = setup
    items = c.get(f"/api/cases/{case_id}/extractions", headers=inv).json()
    assert items["counts"]["ai-extracted"] >= 3
    exts = items["extractions"]

    # viewer cannot review
    r = c.post("/api/auth/login", json={"username": "viewer", "password": "Viewer123!"})
    viewer = {"Authorization": "Bearer " + r.json()["access_token"]}
    c.post(f"/api/cases/{case_id}/members", headers=inv,
           json={"username": "viewer", "case_role": "viewer"})
    one = exts[0]["id"]
    assert c.post(f"/api/cases/{case_id}/extractions/{one}/review", headers=viewer,
                  json={"review_status": "verified"}).status_code == 403

    # approve one entity + verify one relationship; reject the rest
    ent = next(e for e in exts if e["kind"] == "entity" and e["etype"] == "Person")
    rel = next((e for e in exts if e["kind"] == "relationship"), None)
    assert c.post(f"/api/cases/{case_id}/extractions/{ent['id']}/review", headers=inv,
                  json={"review_status": "analyst-reviewed"}).status_code == 200
    if rel:
        assert c.post(f"/api/cases/{case_id}/extractions/{rel['id']}/review", headers=inv,
                      json={"review_status": "verified"}).status_code == 200
    for e in exts:
        if e["id"] not in (ent["id"], rel["id"] if rel else None):
            c.post(f"/api/cases/{case_id}/extractions/{e['id']}/review", headers=inv,
                   json={"review_status": "rejected"})

    r = c.post(f"/api/cases/{case_id}/graph/build", headers=inv).json()
    assert r["nodes_created"] >= 1
    # promoted node carries provenance
    from app.core.graph_engine import get_graph_engine
    g = get_graph_engine()
    node = g.get_node("PERSON_ZAID_ANSARI")
    assert node is not None
    assert node.properties.get("case_id") == case_id
    assert node.properties.get("review_status") == "analyst-reviewed"
    assert node.properties.get("source_document_id") == doc_id
    assert node.properties.get("evidence_text")
    # rejected rows never entered the graph for THIS case: Faiz Sheikh was
    # rejected here (the node may exist from another test's case — the
    # shared engine is global — so assert this case never tagged it).
    faiz = g.get_node("PERSON_FAIZ_SHEIKH")
    assert faiz is None or faiz.properties.get("case_id") != case_id
    # rebuild is idempotent (no dupes)
    r2 = c.post(f"/api/cases/{case_id}/graph/build", headers=inv).json()
    assert r2["nodes_created"] == 0 and r2["edges_created"] == 0


def test_duplicates_and_merge(setup):
    c, inv, case_id, doc_id = setup
    items = c.get(f"/api/cases/{case_id}/extractions", headers=inv).json()["extractions"]
    persons = [e for e in items if e["kind"] == "entity" and e["etype"] == "Person"
               and e["normalized"] == "ZAID ANSARI"]
    assert len(persons) >= 1
    # fabricate a near-duplicate extraction awaiting merge decision
    from app.core.case_store import get_case_store
    store = get_case_store()
    dupe = store.add_extraction(case_id=case_id, document_id=doc_id, kind="entity",
                                etype="Person", value="Zaid  Ansari", normalized="ZAID  ANSARI",
                                external_id="PERSON_Zaid__Ansari", evidence_text="alias row",
                                confidence=0.5, provider="rule-regex-v1")
    # approve both, build, then merge
    for e in (persons[0]["id"], dupe["id"]):
        c.post(f"/api/cases/{case_id}/extractions/{e}/review", headers=inv,
               json={"review_status": "analyst-reviewed"})
    c.post(f"/api/cases/{case_id}/graph/build", headers=inv)
    from app.core.graph_engine import get_graph_engine
    assert get_graph_engine().get_node("PERSON_ZAID_ANSARI") is not None

    groups = c.get(f"/api/cases/{case_id}/duplicates", headers=inv).json()["groups"]
    assert isinstance(groups, list)
    r = c.post(f"/api/cases/{case_id}/extractions/merge", headers=inv,
               json={"keep_id": persons[0]["id"], "merge_ids": [dupe["id"]]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "merged"
