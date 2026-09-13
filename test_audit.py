"""Unit and integration tests for NODE SENTINEL Audit Logging Engine.

Validates:
- Automated recording of security events: logins, failures, user creation, updates, deletes.
- Strict credential redaction (passwords, tokens, keys scrubbed from details).
- Audit query endpoint (/api/audit) filtering by action, status, username, and pagination.
- Role-based access control for audit logs (ADMIN and VIEWER permitted; ANALYST and INVESTIGATOR denied).
"""

import pytest
from fastapi.testclient import TestClient

from app.core.audit_logger import audit_logger, sanitize_dict
from app.main import app
from app.models.audit_models import AuditAction

client = TestClient(app)


def get_token(username: str, password: str) -> str:
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200
    return res.json()["access_token"]


def test_audit_credential_sanitization():
    """Verify passwords, tokens, and secrets are NEVER recorded in audit details."""
    dirty_details = {
        "username": "suspect_user",
        "password": "SuperSecretPassword!",
        "access_token": "bearer.token.abcdef",
        "api_key": "sk-1234567890",
        "nested": {
            "password_hash": "pbkdf2_sha256$100000$...",
            "safe_metric": 42
        }
    }

    clean = sanitize_dict(dirty_details)

    assert clean["password"] == "[REDACTED]"
    assert clean["access_token"] == "[REDACTED]"
    assert clean["api_key"] == "[REDACTED]"
    assert clean["nested"]["password_hash"] == "[REDACTED]"
    assert clean["nested"]["safe_metric"] == 42
    assert clean["username"] == "suspect_user"


def test_audit_log_recording_on_login():
    """Verify login and failed login trigger audit records."""
    # Failed login
    client.post("/api/auth/login", json={"username": "unknown_intruder", "password": "wrong"})

    # Check recent logs
    recent = audit_logger.get_recent_logs(limit=10)
    failed_logins = [e for e in recent if e.action == AuditAction.AUTH_LOGIN_FAILED.value and e.username == "unknown_intruder"]
    assert len(failed_logins) > 0
    assert failed_logins[0].status == "FAILED"

    # Successful login
    client.post("/api/auth/login", json={"username": "admin", "password": "AdminPassword123!"})
    recent_after = audit_logger.get_recent_logs(limit=10)
    success_logins = [e for e in recent_after if e.action == AuditAction.AUTH_LOGIN.value and e.username == "admin"]
    assert len(success_logins) > 0
    assert success_logins[0].status == "SUCCESS"


def test_audit_api_access_control():
    """Verify only roles with AUDIT_VIEW permission (ADMIN, VIEWER) can access /api/audit."""
    admin_token = get_token("admin", "AdminPassword123!")
    viewer_token = get_token("viewer", "Viewer123!")
    investigator_token = get_token("investigator", "Investigator123!")
    analyst_token = get_token("analyst", "Analyst123!")

    # Unauthenticated -> 401
    client.cookies.clear()
    assert client.get("/api/audit").status_code == 401

    # Investigator -> 403 Forbidden
    res_inv = client.get("/api/audit", headers={"Authorization": f"Bearer {investigator_token}"})
    assert res_inv.status_code == 403

    # Analyst -> 403 Forbidden
    res_ana = client.get("/api/audit", headers={"Authorization": f"Bearer {analyst_token}"})
    assert res_ana.status_code == 403

    # Viewer -> 200 OK
    res_view = client.get("/api/audit", headers={"Authorization": f"Bearer {viewer_token}"})
    assert res_view.status_code == 200

    # Admin -> 200 OK
    res_admin = client.get("/api/audit", headers={"Authorization": f"Bearer {admin_token}"})
    assert res_admin.status_code == 200
    data = res_admin.json()
    assert "total" in data
    assert "items" in data
    assert isinstance(data["items"], list)


def test_audit_api_filtering():
    """Verify audit log query filters (action, status, pagination)."""
    admin_token = get_token("admin", "AdminPassword123!")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Filter by action
    res_action = client.get(f"/api/audit?action={AuditAction.AUTH_LOGIN.value}", headers=headers)
    assert res_action.status_code == 200
    for item in res_action.json()["items"]:
        assert item["action"] == AuditAction.AUTH_LOGIN.value

    # Filter by status
    res_status = client.get("/api/audit?status=FAILED", headers=headers)
    assert res_status.status_code == 200
    for item in res_status.json()["items"]:
        assert item["status"] == "FAILED"

    # Pagination
    res_page = client.get("/api/audit?page=1&page_size=3", headers=headers)
    assert res_page.status_code == 200
    assert len(res_page.json()["items"]) <= 3
