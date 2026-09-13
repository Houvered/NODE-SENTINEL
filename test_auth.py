"""Unit and integration tests for NODE SENTINEL Authentication system.

Validates:
- PBKDF2-HMAC-SHA256 password hashing and constant-time verification.
- HMAC-SHA256 signed bearer tokens, expiry checking, and revocation.
- /api/auth/login with valid and invalid credentials.
- /api/auth/me with valid, invalid, and missing tokens.
- /api/auth/logout and session revocation.
- Assurance that password hashes are never exposed in public API responses.
"""

from datetime import timedelta
import time
import pytest
from fastapi.testclient import TestClient

from app.core.auth_service import PasswordHasher, TokenManager, user_repo, auth_service
from app.main import app
from app.models.auth_models import Role

client = TestClient(app)


def test_password_hasher():
    """Test PBKDF2 password hashing and verification."""
    raw_pw = "SecurePassword2026!"
    hashed = PasswordHasher.hash_password(raw_pw)

    assert hashed.startswith("pbkdf2_sha256$100000$")
    assert PasswordHasher.verify_password(raw_pw, hashed) is True
    assert PasswordHasher.verify_password("WrongPassword", hashed) is False
    assert PasswordHasher.verify_password("", hashed) is False

    # Unique salts produce different hashes
    hashed_second = PasswordHasher.hash_password(raw_pw)
    assert hashed != hashed_second
    assert PasswordHasher.verify_password(raw_pw, hashed_second) is True


def test_token_manager_lifecycle():
    """Test token generation, signature validation, expiry, and revocation."""
    tm = TokenManager(secret_key="test_secret_key_12345", expire_minutes=60)
    admin_user = user_repo.get_by_username("admin")
    assert admin_user is not None

    token = tm.create_access_token(admin_user)
    assert isinstance(token, str)
    assert "." in token

    payload = tm.decode_access_token(token)
    assert payload is not None
    assert payload.sub == admin_user.user_id
    assert payload.username == "admin"
    assert payload.role == Role.ADMIN

    # Test tampering with token
    tampered = token[:-4] + "ABCD"
    assert tm.decode_access_token(tampered) is None

    # Test revocation
    tm.revoke_token(token)
    assert tm.decode_access_token(token) is None


def test_token_expiration():
    """Test token expires properly."""
    tm = TokenManager(secret_key="test_secret_key_12345", expire_minutes=1)
    admin_user = user_repo.get_by_username("admin")
    assert admin_user is not None

    # Create expired token (negative delta)
    expired_token = tm.create_access_token(admin_user, expires_delta=timedelta(seconds=-5))
    assert tm.decode_access_token(expired_token) is None


def test_login_success_and_failure():
    """Test /api/auth/login endpoint."""
    # Test valid admin login
    res = client.post("/api/auth/login", json={
        "username": "admin",
        "password": "AdminPassword123!"
    })
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["username"] == "admin"
    assert data["user"]["role"] == "ADMIN"
    assert "password_hash" not in data["user"]
    assert len(data["user"]["permissions"]) > 0

    # Test valid investigator login
    res_inv = client.post("/api/auth/login", json={
        "username": "investigator",
        "password": "Investigator123!"
    })
    assert res_inv.status_code == 200
    assert res_inv.json()["user"]["role"] == "INVESTIGATOR"

    # Test invalid password
    bad_res = client.post("/api/auth/login", json={
        "username": "admin",
        "password": "IncorrectPassword"
    })
    assert bad_res.status_code == 401

    # Test nonexistent user
    non_res = client.post("/api/auth/login", json={
        "username": "nonexistent_officer",
        "password": "SomePassword123!"
    })
    assert non_res.status_code == 401


def test_auth_me_endpoint():
    """Test /api/auth/me profile retrieval."""
    client.cookies.clear()
    # Unauthenticated call fails
    res_anon = client.get("/api/auth/me")
    assert res_anon.status_code == 401

    # Authenticated call succeeds
    login_res = client.post("/api/auth/login", json={
        "username": "analyst",
        "password": "Analyst123!"
    })
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]

    res_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res_me.status_code == 200
    data = res_me.json()
    assert data["username"] == "analyst"
    assert data["role"] == "ANALYST"
    assert "password_hash" not in data
    assert "VIEW_GRAPH" in data["permissions"]


def test_logout_endpoint():
    """Test /api/auth/logout revokes the token."""
    login_res = client.post("/api/auth/login", json={
        "username": "viewer",
        "password": "Viewer123!"
    })
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Profile works before logout
    res = client.get("/api/auth/me", headers=headers)
    assert res.status_code == 200

    # Logout
    logout_res = client.post("/api/auth/logout", headers=headers)
    assert logout_res.status_code == 200

    # Subsequent request with same token is rejected
    res_after = client.get("/api/auth/me", headers=headers)
    assert res_after.status_code == 401


def test_frontend_auth_elements_present():
    """Verify that frontend UI contains all required auth, badge, and admin console components."""
    index_res = client.get("/")
    assert index_res.status_code == 200
    html = index_res.text

    assert 'id="authUserBadge"' in html
    assert 'id="loginModal"' in html
    assert 'id="userCreateModal"' in html
    assert 'id="nav-admin"' in html
    assert "quickLogin('admin')" in html
    assert "quickLogin('investigator')" in html
    assert "quickLogin('analyst')" in html
    assert "quickLogin('viewer')" in html
    assert "loadUsersTable()" in html
    assert "loadAuditLogsTable()" in html

