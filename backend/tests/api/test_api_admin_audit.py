"""Tests for credential audit logging and disaster recovery revert (Blindside 4)."""
import pytest
from fastapi.testclient import TestClient

from app.auth.passwords import hash_password, verify_password
from app.db import execute, fetch_all, fetch_one
from app.main import app

client = TestClient(app)


def test_credential_audit_trail_and_revert(admin_headers):
    # Pre-clean isolated test user
    test_uname = "audit_runner_user"
    execute("DELETE FROM user_credentials_history WHERE username = %s", (test_uname,))
    execute("DELETE FROM user_farm_access WHERE user_id IN (SELECT id FROM users WHERE username = %s)", (test_uname,))
    execute("DELETE FROM users WHERE username = %s", (test_uname,))

    # Create fresh user with known password
    initial_hash = hash_password("InitialPassword123!")
    execute(
        "INSERT INTO users (username, password_hash, role, is_admin, is_active) VALUES (%s, %s, 'user', FALSE, TRUE)",
        (test_uname, initial_hash),
    )
    user = fetch_one("SELECT id, username, role, password_hash FROM users WHERE username = %s", (test_uname,))
    assert user is not None
    user_id = user["id"]

    # Assign farm access
    execute("INSERT INTO user_farm_access (user_id, farm_id) VALUES (%s, 'KF')", (user_id,))

    # 1. Update password
    r_pwd = client.put(
        "/api/admin/credentials/password",
        json={"user_id": user_id, "new_password": "NewAuditPassword123!"},
        headers=admin_headers,
    )
    assert r_pwd.status_code == 200

    # 2. Update role to owner
    r_role = client.put(
        f"/api/admin/users/{user_id}/role",
        json={"role": "owner"},
        headers=admin_headers,
    )
    assert r_role.status_code == 200

    # 3. Query audit log
    r_audit = client.get("/api/admin/users/audit-log", headers=admin_headers)
    assert r_audit.status_code == 200
    logs = r_audit.json()

    user_logs = [l for l in logs if l["user_id"] == user_id]
    assert len(user_logs) >= 2

    # Earliest entry captures pre-mutation state (InitialPassword123! and role='user')
    earliest_id = min(l["id"] for l in user_logs)

    # 4. Revert credential to earliest history entry
    r_revert = client.post(
        f"/api/admin/users/{user_id}/revert-credential/{earliest_id}",
        headers=admin_headers,
    )
    assert r_revert.status_code == 200
    assert "Reverted user" in r_revert.json()["detail"]

    # 5. Verify restored state in database
    restored = fetch_one("SELECT role, password_hash FROM users WHERE id = %s", (user_id,))
    assert restored["role"] == "user"
    assert restored["password_hash"] == initial_hash

    # Clean up
    execute("DELETE FROM user_credentials_history WHERE user_id = %s", (user_id,))
    execute("DELETE FROM user_farm_access WHERE user_id = %s", (user_id,))
    execute("DELETE FROM users WHERE id = %s", (user_id,))

