# -*- coding: utf-8 -*-
"""End-to-end acceptance: user -> case -> report+CDR -> review -> graph ->
connection answer (cited) -> missing-financial prompt -> upload financial ->
re-answer updated -> report download."""
import pytest
from fastapi.testclient import TestClient

import app.core.case_store as cs_mod
from app.config import settings

FIR = ("FIR 2026-E2E. Accused Ravi Nair called Suspect Faiz Sheikh at +919898111111. "
       "FIR-2026-E2E.")
CDR = ("calling_number,receiving_number,call_start_time,duration_seconds\n"
       "+919898111111,+919898222222,2026-09-21 09:00:00,300\n"
       "+919898222222,+919898111111,2026-09-21 09:10:00,200\n").encode()
FIN = ("sender_account,receiver_account,amount,transaction_time,transaction_id\n"
       "ACC821111111,ACC822222222,500000,2026-09-21 10:00:00,TX-E2E-1\n").encode()


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CASES_DB", str(tmp_path / "cases.db"))
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    cs_mod._store = None
    import app.main as main
    with TestClient(main.app) as c:
        yield c
    cs_mod._store = None


def test_full_arc(setup):
    c = setup
    # 1. create user (admin creates), 2. create case
    admin = {"Authorization": "Bearer " + c.post(
        "/api/auth/login", json={"username": "admin", "password": "AdminPassword123!"}).json()["access_token"]}
    r = c.post("/api/users", headers=admin,
               json={"username": "e2e_inv", "password": "Password123!", "role": "INVESTIGATOR"})
    assert r.status_code in (200, 201), r.text
    inv = {"Authorization": "Bearer " + c.post(
        "/api/auth/login", json={"username": "e2e_inv", "password": "Password123!"}).json()["access_token"]}
    case_id = c.post("/api/cases", headers=inv, json={"title": "E2E Case"}).json()["id"]

    # 3-4. upload + process synthetic report and CDR
    fir = c.post(f"/api/cases/{case_id}/documents", headers=inv,
                 files={"file": ("fir.txt", FIR.encode(), "text/plain")}).json()["document"]["id"]
    assert c.post(f"/api/documents/{fir}/process", headers=inv).json()["status"] == "succeeded"
    cdr = c.post(f"/api/cases/{case_id}/documents", headers=inv,
                 files={"file": ("cdr.csv", CDR, "text/csv")}).json()["document"]["id"]
    cdr_imp = c.post(f"/api/documents/{cdr}/process", headers=inv).json()["import_id"]

    # 5-7. review extractions, confirm CDR mapping, build graph
    for e in c.get(f"/api/cases/{case_id}/extractions", headers=inv).json()["extractions"]:
        c.post(f"/api/cases/{case_id}/extractions/{e['id']}/review", headers=inv,
               json={"review_status": "analyst-reviewed"})
    assert c.post(f"/api/cases/{case_id}/imports/{cdr_imp}/confirm", headers=inv,
                  json={"mapping": {}}).json()["imported"] == 2
    assert c.post(f"/api/cases/{case_id}/graph/build", headers=inv).json()["edges_created"] >= 1

    # 8-10. graph visible; connection answer cites source records
    assert c.get(f"/api/network/node/PERSON_RAVI_NAIR/expand", headers=inv).status_code == 200
    ans = c.post(f"/api/cases/{case_id}/assistant/query", headers=inv,
                 json={"query": "How is Ravi Nair connected to Faiz Sheikh?"}).json()
    assert "potential connection" in ans["answer"]
    assert ans["evidence"], "connection answer must cite source records"

    # 11-12. missing financial analysis asks for the dataset + columns
    fin_q = c.post(f"/api/cases/{case_id}/assistant/query", headers=inv,
                   json={"query": "Show financial links for Ravi Nair"}).json()
    assert "sender_account" in fin_q["answer"] and fin_q["missing"]

    # 13-15. upload financial dataset, confirm, re-ask -> updated grounded answer
    fin_doc = c.post(f"/api/cases/{case_id}/documents", headers=inv,
                     files={"file": ("fin.csv", FIN, "text/csv")}).json()["document"]["id"]
    fin_imp = c.post(f"/api/documents/{fin_doc}/process", headers=inv).json()["import_id"]
    assert c.post(f"/api/cases/{case_id}/imports/{fin_imp}/confirm", headers=inv,
                  json={"mapping": {}}).json()["imported"] == 1
    fin_q2 = c.post(f"/api/cases/{case_id}/assistant/query", headers=inv,
                    json={"query": "Show transactions involving ACC821111111"}).json()
    assert "TX-E2E-1" in fin_q2["answer"] or "ACC821111111" in fin_q2["answer"]
    assert fin_q2["evidence"]

    # 16. report generates + downloads with readiness notes
    rep = c.post(f"/api/cases/{case_id}/reports", headers=inv, json={"output_format": "json"}).json()
    dl = c.get(f"/api/cases/{case_id}/reports/{rep['report']['id']}/download", headers=inv)
    assert dl.status_code == 200
