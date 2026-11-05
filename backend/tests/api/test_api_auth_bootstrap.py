"""Tests for bootstrap status and initial password rotation (Blindside 2)."""
import pytest
from fastapi.testclient import TestClient

from app.auth.passwords import hash_password, verify_password
from app.db import execute, fetch_one
from app.main import app

client = TestClient(app)


def test_bootstrap_status():
    r = client.get("/api/auth/bootstrap-status")
    assert r.status_code == 200
    data = r.json()
    assert "needs_setup" in data
    assert "user_count" in data
    assert isinstance(data["needs_setup"], bool)


def test_rotate_initial_password():
    # Set must_change_password = True on user
    execute("UPDATE users SET must_change_password = TRUE WHERE username = 'user'")

    # Login to verify must_change_password flag is returned in UserInfo and get fresh token
    login_resp = client.post("/api/auth/login", json={"username": "user", "password": "user123"})
    assert login_resp.status_code == 200
    user_info = login_resp.json()["user"]
    assert user_info["must_change_password"] is True
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Rotate initial password
    r = client.post(
        "/api/auth/rotate-initial-password",
        json={"new_password": "NewUserPassword2026!"},
        headers=headers,
    )
    assert r.status_code == 200
    assert "successfully rotated" in r.json()["detail"]

    # Verify database state
    user_row = fetch_one("SELECT password_hash, must_change_password FROM users WHERE username = 'user'")
    assert user_row["must_change_password"] is False
    assert verify_password("NewUserPassword2026!", user_row["password_hash"])

    # Reset back to user123 for other tests
    execute("UPDATE users SET password_hash = %s WHERE username = 'user'", (hash_password("user123"),))

