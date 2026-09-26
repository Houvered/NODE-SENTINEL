# -*- coding: utf-8 -*-
"""Case reports: scoped subjects, readiness notes, authorized download."""
import pytest
from fastapi.testclient import TestClient

import app.core.case_store as cs_mod
from app.config import settings

FIR = ("FIR 2026-R. Accused Ravi Nair called +919898989898. FIR-2026-R.")


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CASES_DB", str(tmp_path / "cases.db"))
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    cs_mod._store = None
    import app.main as main
    with TestClient(main.app) as c:
        r = c.post("/api/auth/login", json={"username": "investigator", "password": "Investigator123!"})
        inv = {"Authorization": "Bearer " + r.json()["access_token"]}
        case_id = c.post("/api/cases", headers=inv, json={"title": "Report Case"}).json()["id"]
        d = c.post(f"/api/cases/{case_id}/documents", headers=inv,
                   files={"file": ("fir.txt", FIR.encode(), "text/plain")}).json()["document"]["id"]
        c.post(f"/api/documents/{d}/process", headers=inv)
        for e in c.get(f"/api/cases/{case_id}/extractions", headers=inv).json()["extractions"]:
            c.post(f"/api/cases/{case_id}/extractions/{e['id']}/review", headers=inv,
                   json={"review_status": "analyst-reviewed"})
        c.post(f"/api/cases/{case_id}/graph/build", headers=inv)
        yield c, inv, case_id
    cs_mod._store = None


def test_case_report_json_and_download(setup):
    c, inv, case_id = setup
    r = c.post(f"/api/cases/{case_id}/reports", headers=inv,
               json={"output_format": "json", "notes": "Synthetic verification run."})
    assert r.status_code == 201, r.text
    rep_id = r.json()["report"]["id"]
    assert r.json()["sections_included"]

    r = c.get(f"/api/cases/{case_id}/reports", headers=inv)
    assert any(x["id"] == rep_id for x in r.json()["reports"])

    r = c.get(f"/api/cases/{case_id}/reports/{rep_id}/download", headers=inv)
    assert r.status_code == 200
    body = r.json()
    assert "Missing data" in body.get("investigator_notes", "") or "Case evidence" in body.get("investigator_notes", "")


def test_out_of_case_subject_rejected(setup):
    c, inv, case_id = setup
    r = c.post(f"/api/cases/{case_id}/reports", headers=inv,
               json={"entity_id": "PERSON_TARIQ_AHMAD", "output_format": "json"})
    assert r.status_code == 404
