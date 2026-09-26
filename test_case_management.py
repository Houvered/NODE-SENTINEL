# -*- coding: utf-8 -*-
"""Case management: CRUD, membership, isolation, retention-guarded delete."""
import os
import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ALLOW_PUBLIC_REGISTER", "false")

import app.core.case_store as cs_mod
from app.config import settings


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CASES_DB", str(tmp_path / "cases.db"))
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    cs_mod._store = None
    import app.main as main
    with TestClient(main.app) as c:
        yield c
    cs_mod._store = None


def login(client, username, password):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


def test_case_crud_and_members(client):
    admin = login(client, "admin", "AdminPassword123!")
    r = client.post("/api/cases", headers=admin,
                    json={"title": "Op Test", "description": "synthetic", "fir_no": "FIR-TEST-1"})
    assert r.status_code == 201, r.text
    case_id = r.json()["id"]
    assert r.json()["my_role"] in ("lead", "admin")

    r = client.get("/api/cases", headers=admin)
    assert any(c["id"] == case_id for c in r.json())

    r = client.patch("/api/cases/" + case_id, headers=admin, json={"status": "under_review"})
    assert r.status_code == 200 and r.json()["status"] == "under_review"

    # add investigator as member
    r = client.post(f"/api/cases/{case_id}/members", headers=admin,
                    json={"username": "investigator", "case_role": "investigator"})
    assert r.status_code == 201, r.text

    inv = login(client, "investigator", "Investigator123!")
    r = client.get(f"/api/cases/{case_id}", headers=inv)
    assert r.status_code == 200

    # outsider (analyst) cannot access
    ana = login(client, "analyst", "Analyst123!")
    r = client.get(f"/api/cases/{case_id}", headers=ana)
    assert r.status_code == 403, r.text

    # audit trail has creation event
    r = client.get(f"/api/cases/{case_id}/audit-logs", headers=admin)
    assert r.status_code == 200
    actions = [e.get("action") for e in (r.json().get("logs") or r.json().get("items") or [])]
    assert "CASE_CREATED" in actions


def test_viewer_readonly_and_delete_guards(client):
    admin = login(client, "admin", "AdminPassword123!")
    case_id = client.post("/api/cases", headers=admin, json={"title": "Guards"}).json()["id"]
    client.post(f"/api/cases/{case_id}/members", headers=admin,
                json={"username": "viewer", "case_role": "viewer"})
    viewer = login(client, "viewer", "Viewer123!")

    r = client.patch(f"/api/cases/{case_id}", headers=viewer, json={"title": "hijack"})
    assert r.status_code == 403

    # non-admin cannot delete
    inv = login(client, "investigator", "Investigator123!")
    r = client.delete(f"/api/cases/{case_id}?confirm=true", headers=inv)
    assert r.status_code == 403

    # admin delete without confirm -> 400; unarchived -> 409
    assert client.delete(f"/api/cases/{case_id}", headers=admin).status_code == 400
    assert client.delete(f"/api/cases/{case_id}?confirm=true", headers=admin).status_code == 409

    # archive then delete works
    client.patch(f"/api/cases/{case_id}", headers=admin, json={"status": "archived"})
    r = client.delete(f"/api/cases/{case_id}?confirm=true", headers=admin)
    assert r.status_code == 200
    assert client.get(f"/api/cases/{case_id}", headers=admin).status_code == 404


def test_public_register_disabled_by_default(client):
    r = client.post("/api/auth/register", json={"username": "newbie1", "password": "Password123!"})
    assert r.status_code == 403
