# -*- coding: utf-8 -*-
"""Evidence pipeline: upload validation, processing, review queue data, dataset mapping."""
import pytest
from fastapi.testclient import TestClient

import app.core.case_store as cs_mod
from app.config import settings

FIR_TEXT = (
    "FIR No. 2026-SYN-99 filed at Connaught Place police station on 2026-09-20. "
    "Accused Arjun Mehta (alias Monty) was seen calling +919811223344 from a black Toyota Fortuner "
    "bearing DL01AB9988 near Okhla. Co-accused Kabir Mirza allegedly moved funds through ACC990188231. "
    "Sections applied: FIR-2026-99."
)


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CASES_DB", str(tmp_path / "cases.db"))
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    cs_mod._store = None
    import app.main as main
    with TestClient(main.app) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": "AdminPassword123!"})
        assert r.status_code == 200
        admin = {"Authorization": "Bearer " + r.json()["access_token"]}
        r = c.post("/api/cases", headers=admin, json={"title": "Evidence Case"})
        assert r.status_code == 201
        yield c, admin, r.json()["id"]
    cs_mod._store = None


def test_upload_validate_process(setup):
    c, admin, case_id = setup
    # bad extension rejected
    r = c.post(f"/api/cases/{case_id}/documents", headers=admin,
               files={"file": ("evil.exe", b"MZ" + b"\x00" * 100, "application/octet-stream")})
    assert r.status_code == 422
    # magic mismatch rejected
    r = c.post(f"/api/cases/{case_id}/documents", headers=admin,
               files={"file": ("fake.pdf", b"not a pdf at all", "application/pdf")})
    assert r.status_code == 422
    # good txt upload
    r = c.post(f"/api/cases/{case_id}/documents", headers=admin,
               files={"file": ("fir.txt", FIR_TEXT.encode(), "text/plain")})
    assert r.status_code == 201, r.text
    doc_id = r.json()["document"]["id"]
    # duplicate detected
    r = c.post(f"/api/cases/{case_id}/documents", headers=admin,
               files={"file": ("fir-copy.txt", FIR_TEXT.encode(), "text/plain")})
    assert r.json()["status"] == "duplicate"
    # process
    r = c.post(f"/api/documents/{doc_id}/process", headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "succeeded"
    assert r.json()["entities"] >= 3
    # metadata + download roundtrip
    r = c.get(f"/api/documents/{doc_id}", headers=admin)
    assert r.json()["document"]["status"] == "awaiting_review"
    r = c.get(f"/api/documents/{doc_id}/download", headers=admin)
    assert r.status_code == 200 and b"Arjun Mehta" in r.content
    # processing again is a clean 409, not a silent dup
    assert c.post(f"/api/documents/{doc_id}/process", headers=admin).status_code == 409


def test_dataset_quality_gate(setup):
    c, admin, case_id = setup
    bad_csv = b"caller,receiver\n+911111111111,+912222222222\n"
    r = c.post(f"/api/cases/{case_id}/documents", headers=admin,
               files={"file": ("calls.csv", bad_csv, "text/csv")})
    assert r.status_code == 201
    doc_id = r.json()["document"]["id"]
    r = c.post(f"/api/documents/{doc_id}/process", headers=admin)
    assert r.json()["status"] == "succeeded"
    import_id = r.json()["import_id"]
    assert r.json()["quality"]["can_continue"] is False
    assert "call_start_time" in r.json()["quality"]["missing"]

    detail = c.get(f"/api/cases/{case_id}/imports/{import_id}", headers=admin).json()
    assert detail["quality"]["row_count"] == 1
    assert len(detail["preview"]) == 1

    # confirm blocked while requirements unmet
    r = c.post(f"/api/cases/{case_id}/imports/{import_id}/confirm", headers=admin, json={"mapping": {}})
    assert r.status_code == 422

    good_csv = ("calling_number,receiving_number,call_start_time,duration_seconds\n"
                "+911111111111,+912222222222,2026-09-20 10:00:00,120\n"
                "+911111111111,+912222222222,2026-09-20 10:00:00,120\n").encode()
    r = c.post(f"/api/cases/{case_id}/documents", headers=admin,
               files={"file": ("calls2.csv", good_csv, "text/csv")})
    doc2 = r.json()["document"]["id"]
    r = c.post(f"/api/documents/{doc2}/process", headers=admin).json()
    assert r["quality"]["can_continue"] is True
    assert r["quality"]["duplicate_rows"] == 1
    r = c.post(f"/api/cases/{case_id}/imports/{r['import_id']}/confirm", headers=admin,
               json={"mapping": {}})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] >= 1
