from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.auth.passwords import hash_password


@pytest.mark.integration
def test_api_login_success(client: TestClient, monkeypatch):
    """US01: Verifies that valid login returns 200 and a valid JWT bearer token."""
    pwd_hash = hash_password("admin123")
    monkeypatch.setattr(
        "app.routers.auth.fetch_one",
        lambda q, p: {"id": 1, "username": "admin", "password_hash": pwd_hash, "is_admin": True, "is_active": True},
    )
    monkeypatch.setattr(
        "app.routers.auth.get_user_farms",
        lambda uid: [{"farm_id": "KF", "farm_name": "Killara Feedlot", "slug": "kf", "is_active": True}],
    )
    res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["user"]["username"] == "admin"
    assert data["user"]["is_admin"] is True


@pytest.mark.integration
def test_api_login_invalid_credentials(client: TestClient, monkeypatch):
    """US01: Verifies that incorrect password returns 401 Unauthorized."""
    pwd_hash = hash_password("correctPassword")
    monkeypatch.setattr(
        "app.routers.auth.fetch_one",
        lambda q, p: {"id": 1, "username": "admin", "password_hash": pwd_hash, "is_admin": True, "is_active": True},
    )
    res = client.post("/api/auth/login", json={"username": "admin", "password": "wrongPassword"})
    assert res.status_code == 401


@pytest.mark.integration
def test_api_auth_me_profile(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    """US01: Verifies /api/auth/me resolves current user profile with farm access list."""
    monkeypatch.setattr(
        "app.routers.auth.get_user_farms",
        lambda uid: [{"farm_id": "KF", "farm_name": "Killara Feedlot", "slug": "kf", "is_active": True}],
    )
    res = client.get("/api/auth/me", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["username"] == "admin"
    assert "KF" in [f["farm_id"] for f in data["farms"]]


@pytest.mark.integration
def test_api_unauthorized_token_rejection(client: TestClient):
    """US01: Verifies request without bearer token is rejected with 401."""
    res = client.get("/api/auth/me")
    assert res.status_code == 401


@pytest.mark.integration
def test_api_deprecated_auth_username_forbidden_for_non_admin(client: TestClient, user_headers: dict[str, str]):
    """Verifies that deprecated /api/auth/username is protected by require_credential_admin (HTTP 403)."""
    res = client.put("/api/auth/username", json={"user_id": 2, "new_username": "newuser"}, headers=user_headers)
    assert res.status_code == 403


@pytest.mark.integration
def test_api_deprecated_auth_username_rejects_renaming_to_owner(
    client: TestClient,
    admin_headers: dict[str, str],
):
    """Verifies that deprecated /api/auth/username prohibits renaming a user to 'owner'."""
    res = client.put("/api/auth/username", json={"user_id": 2, "new_username": "owner"}, headers=admin_headers)
    assert res.status_code == 400
    assert "owner" in res.json()["detail"].lower()
