"""Role-Based Access Control (RBAC) and User Management tests for NODE SENTINEL.

Validates:
- 4-tier Role hierarchy: ADMIN, INVESTIGATOR, ANALYST, VIEWER.
- Admin-only User Management endpoints (/api/users).
- 403 Forbidden enforcement for non-admin users attempting administrative actions.
- User creation, listing, updating (role, status, name), and deletion.
- Safety guards: Admins cannot delete or deactivate their own accounts.
"""

import pytest
from fastapi.testclient import TestClient

from app.core.auth_service import user_repo
from app.main import app
from app.models.auth_models import Role, ROLE_PERMISSIONS, Permission

client = TestClient(app)


def get_token(username: str, password: str) -> str:
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, f"Login failed for {username}"
    return res.json()["access_token"]


def test_permission_matrix_integrity():
    """Verify that roles have expected permissions assigned."""
    # ADMIN has all permissions
    admin_perms = ROLE_PERMISSIONS[Role.ADMIN]
    assert Permission.USER_MANAGEMENT in admin_perms
    assert Permission.AUDIT_VIEW in admin_perms
    assert Permission.DATA_INGEST in admin_perms
    assert Permission.REPORT_GENERATE in admin_perms

    # INVESTIGATOR has analytical and operational permissions, but NOT user management
    inv_perms = ROLE_PERMISSIONS[Role.INVESTIGATOR]
    assert Permission.USER_MANAGEMENT not in inv_perms
    assert Permission.AUDIT_VIEW not in inv_perms
    assert Permission.DATA_INGEST in inv_perms
    assert Permission.REPORT_GENERATE in inv_perms

    # ANALYST can analyze but not ingest raw data or manage users
    analyst_perms = ROLE_PERMISSIONS[Role.ANALYST]
    assert Permission.USER_MANAGEMENT not in analyst_perms
    assert Permission.DATA_INGEST not in analyst_perms
    assert Permission.FINANCIAL_ANALYZE in analyst_perms
    assert Permission.CDR_ANALYZE in analyst_perms

    # VIEWER can only inspect
    viewer_perms = ROLE_PERMISSIONS[Role.VIEWER]
    assert Permission.USER_MANAGEMENT not in viewer_perms
    assert Permission.DATA_INGEST not in viewer_perms
    assert Permission.GRAPH_VIEW in viewer_perms
    assert Permission.AUDIT_VIEW in viewer_perms


def test_rbac_user_management_access_control():
    """Verify that only ADMIN can access /api/users."""
    admin_token = get_token("admin", "AdminPassword123!")
    inv_token = get_token("investigator", "Investigator123!")
    analyst_token = get_token("analyst", "Analyst123!")
    viewer_token = get_token("viewer", "Viewer123!")

    # Unauthenticated
    client.cookies.clear()
    res = client.get("/api/users")
    assert res.status_code == 401

    # Investigator -> 403 Forbidden
    res = client.get("/api/users", headers={"Authorization": f"Bearer {inv_token}"})
    assert res.status_code == 403

    # Analyst -> 403 Forbidden
    res = client.get("/api/users", headers={"Authorization": f"Bearer {analyst_token}"})
    assert res.status_code == 403

    # Viewer -> 403 Forbidden
    res = client.get("/api/users", headers={"Authorization": f"Bearer {viewer_token}"})
    assert res.status_code == 403

    # Admin -> 200 OK
    res = client.get("/api/users", headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 200
    users = res.json()
    assert len(users) >= 4
    # Ensure password hashes are never returned
    for u in users:
        assert "password_hash" not in u
        assert "password" not in u


def test_user_crud_lifecycle_by_admin():
    """Test full user creation, update, and deletion by Admin."""
    admin_token = get_token("admin", "AdminPassword123!")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. Create a new officer
    new_user_payload = {
        "username": "officer_patel",
        "password": "PatelPassword123!",
        "full_name": "Officer Vikram Patel",
        "role": "INVESTIGATOR",
        "is_active": True
    }
    res = client.post("/api/users", json=new_user_payload, headers=headers)
    assert res.status_code == 201
    created = res.json()
    user_id = created["user_id"]
    assert created["username"] == "officer_patel"
    assert created["role"] == "INVESTIGATOR"
    assert "password_hash" not in created

    # 2. Login as newly created user
    patel_token = get_token("officer_patel", "PatelPassword123!")
    assert patel_token is not None

    # 3. Update user role and status
    update_payload = {
        "full_name": "Senior Officer Vikram Patel",
        "role": "ANALYST",
        "is_active": True
    }
    res_update = client.patch(f"/api/users/{user_id}", json=update_payload, headers=headers)
    assert res_update.status_code == 200
    assert res_update.json()["full_name"] == "Senior Officer Vikram Patel"
    assert res_update.json()["role"] == "ANALYST"

    # 4. Delete user
    res_del = client.delete(f"/api/users/{user_id}", headers=headers)
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "success"

    # 5. Verify deleted user cannot log in
    res_bad_login = client.post("/api/auth/login", json={
        "username": "officer_patel",
        "password": "PatelPassword123!"
    })
    assert res_bad_login.status_code == 401


def test_admin_self_protection_guards():
    """Ensure admins cannot delete or deactivate their own accounts."""
    admin_token = get_token("admin", "AdminPassword123!")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Get admin's own ID
    me_res = client.get("/api/auth/me", headers=headers)
    admin_id = me_res.json()["user_id"]

    # Attempt to deactivate self -> 400 Bad Request
    res_deact = client.patch(f"/api/users/{admin_id}", json={"is_active": False}, headers=headers)
    assert res_deact.status_code == 400

    # Attempt to demote self -> 400 Bad Request
    res_demote = client.patch(f"/api/users/{admin_id}", json={"role": "VIEWER"}, headers=headers)
    assert res_demote.status_code == 400

    # Attempt to delete self -> 400 Bad Request
    res_del = client.delete(f"/api/users/{admin_id}", headers=headers)
    assert res_del.status_code == 400
