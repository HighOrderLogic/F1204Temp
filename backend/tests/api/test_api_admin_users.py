from __future__ import annotations

import os
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from app.auth.dependencies import create_access_token
from app.auth.passwords import hash_password
import app.db as db_mod
import app.auth.dependencies as deps_mod
import app.routers.auth as auth_router
import app.routers.admin as admin_router
from app.main import app


@pytest.fixture(autouse=True)
def setup_real_duckdb_test_env(monkeypatch, tmp_path):
    """
    Ensures real DuckDB database is initialized and overrides conftest mock
    so integration tests run against true SQL queries.
    """
    env_path = os.environ.get("DUCKDB_PATH")
    if env_path:
        p = Path(env_path)
        test_db_file = p if p.is_absolute() else (Path(__file__).resolve().parents[2] / p)
        test_db_file.parent.mkdir(parents=True, exist_ok=True)
    else:
        test_db_file = tmp_path / "test_admin_users.duckdb"
    
    # Configure backend to use DuckDB with this test db
    monkeypatch.setenv("DATABASE_BACKEND", "duckdb")
    monkeypatch.setenv("DUCKDB_PATH", str(test_db_file))
    
    # Reset connection and unlink old file if possible
    db_mod.reset_active_engine()
    if test_db_file.exists():
        try:
            test_db_file.unlink()
        except Exception:
            pass

    # Reconnect to initialize schema
    with db_mod.get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DROP VIEW IF EXISTS user_farms;")
        cursor.execute("DROP TABLE IF EXISTS user_farm_access;")
        cursor.execute("DROP TABLE IF EXISTS users;")
        cursor.execute("DROP TABLE IF EXISTS farms;")

        cursor.execute("""
            CREATE SEQUENCE IF NOT EXISTS users_id_seq START 1;
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY DEFAULT nextval('users_id_seq'),
                username VARCHAR(50) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                is_admin BOOLEAN DEFAULT FALSE,
                role VARCHAR(20) DEFAULT 'user',
                is_active BOOLEAN DEFAULT TRUE,
                last_login TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS farms (
                farm_id VARCHAR(50) PRIMARY KEY,
                farm_name VARCHAR(100) NOT NULL,
                slug VARCHAR(100) UNIQUE,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS user_farm_access (
                user_id INTEGER,
                farm_id VARCHAR(50),
                PRIMARY KEY (user_id, farm_id)
            );

            CREATE VIEW IF NOT EXISTS user_farms AS SELECT user_id, farm_id FROM user_farm_access;
        """)

        # Seed farms
        cursor.execute("INSERT INTO farms (farm_id, farm_name, slug) VALUES ('KF', 'Killara Feedlot', 'kf') ON CONFLICT DO NOTHING")
        cursor.execute("INSERT INTO farms (farm_id, farm_name, slug) VALUES ('WOW', 'WOW Feedlot Killara', 'wow') ON CONFLICT DO NOTHING")

        # Seed admin, owner, user
        admin_hash = hash_password("admin123")
        owner_hash = hash_password("owner123")
        user_hash = hash_password("user123")
        
        cursor.execute(
            "INSERT INTO users (username, password_hash, is_admin, role, is_active) VALUES ('admin', ?, TRUE, 'admin', TRUE) ON CONFLICT DO NOTHING",
            (admin_hash,),
        )
        cursor.execute(
            "INSERT INTO users (username, password_hash, is_admin, role, is_active) VALUES ('owner', ?, FALSE, 'owner', TRUE) ON CONFLICT DO NOTHING",
            (owner_hash,),
        )
        cursor.execute(
            "INSERT INTO users (username, password_hash, is_admin, role, is_active) VALUES ('user', ?, FALSE, 'user', TRUE) ON CONFLICT DO NOTHING",
            (user_hash,),
        )

        admin_row = cursor.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        user_row = cursor.execute("SELECT id FROM users WHERE username = 'user'").fetchone()
        owner_row = cursor.execute("SELECT id FROM users WHERE username = 'owner'").fetchone()

        admin_id = admin_row[0] if isinstance(admin_row, tuple) else admin_row["id"]
        user_id = user_row[0] if isinstance(user_row, tuple) else user_row["id"]
        owner_id = owner_row[0] if isinstance(owner_row, tuple) else owner_row["id"]

        # Only bind user and owner to KF; leave admin with 0 bindings to test universal bypass
        cursor.execute("INSERT INTO user_farm_access (user_id, farm_id) VALUES (?, 'KF') ON CONFLICT DO NOTHING", (user_id,))
        cursor.execute("INSERT INTO user_farm_access (user_id, farm_id) VALUES (?, 'KF') ON CONFLICT DO NOTHING", (owner_id,))

    # Real fetch_one implementation using fetch_all directly (bypassing conftest fake_fetch_one)
    def real_fetch_one(query: str, params: tuple | list | dict | None = None) -> dict | None:
        rows = db_mod.fetch_all(query, params)
        return rows[0] if rows else None

    def real_get_user_farms(uid: int) -> list[dict]:
        u = real_fetch_one("SELECT is_admin, role FROM users WHERE id = %s", (uid,))
        if u and (u.get("is_admin") or (u.get("role") or "").lower() == "admin"):
            return db_mod.fetch_all(
                "SELECT f.farm_id, f.farm_name, f.slug FROM farms f WHERE f.is_active = TRUE ORDER BY f.farm_name"
            )
        return db_mod.fetch_all(
            """
            SELECT f.farm_id, f.farm_name, f.slug
            FROM farms f
            JOIN user_farm_access ufa ON ufa.farm_id = f.farm_id
            WHERE ufa.user_id = %s AND f.is_active = TRUE
            ORDER BY f.farm_name
            """,
            (uid,),
        )

    # Restore real DB functions across modules
    monkeypatch.setattr(db_mod, "fetch_one", real_fetch_one)
    monkeypatch.setattr(db_mod, "get_user_farms", real_get_user_farms)
    monkeypatch.setattr(deps_mod, "fetch_one", real_fetch_one)
    monkeypatch.setattr(deps_mod, "get_user_farms", real_get_user_farms)
    monkeypatch.setattr(auth_router, "fetch_one", real_fetch_one)
    monkeypatch.setattr(auth_router, "get_user_farms", real_get_user_farms)
    monkeypatch.setattr(admin_router, "fetch_one", real_fetch_one)

    yield test_db_file

    db_mod.reset_active_engine()


@pytest.fixture
def test_client() -> TestClient:
    return TestClient(app)


def test_admin_universal_bypass(test_client: TestClient):
    """Admin universal bypass: returns all active farms even with 0 rows in user_farm_access."""
    # Ensure admin has 0 rows in user_farm_access
    admin_user = db_mod.fetch_one("SELECT id FROM users WHERE username = 'admin'")
    assert admin_user is not None
    admin_id = admin_user["id"]
    db_mod.execute("DELETE FROM user_farm_access WHERE user_id = %s", (admin_id,))

    # Login as admin
    login_res = test_client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert login_res.status_code == 200
    token = login_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Admin querying /api/farms gets all active farms
    farms_res = test_client.get("/api/farms", headers=headers)
    assert farms_res.status_code == 200
    farms = farms_res.json()
    farm_ids = [f["farm_id"] for f in farms]
    assert "KF" in farm_ids
    assert "WOW" in farm_ids

    # assert_farm_access universal bypass: admin can access WOW
    farm_detail_res = test_client.get("/api/farms/WOW", headers=headers)
    assert farm_detail_res.status_code == 200
    assert farm_detail_res.json()["farm_id"] == "WOW"


def test_non_admin_fail_closed_zero_farms(test_client: TestClient):
    """Non-admin user with 0 assigned farms fails closed with HTTP 403."""
    pwd_hash = hash_password("password123")
    db_mod.execute(
        "INSERT INTO users (username, password_hash, is_admin, role, is_active) VALUES ('orphan', %s, FALSE, 'user', TRUE)",
        (pwd_hash,),
    )
    orphan = db_mod.fetch_one("SELECT id FROM users WHERE username = 'orphan'")
    assert orphan is not None
    db_mod.execute("DELETE FROM user_farm_access WHERE user_id = %s", (orphan["id"],))

    # Login attempt must fail with 403 Forbidden
    login_res = test_client.post("/api/auth/login", json={"username": "orphan", "password": "password123"})
    assert login_res.status_code == 403
    assert "No farm access assigned" in login_res.json()["detail"]

    # In-flight token verification must also fail-closed with 403
    token, _ = create_access_token(orphan["id"], "orphan", False, [], role="user")
    me_res = test_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_res.status_code == 403
    assert "No farm access assigned" in me_res.json()["detail"]


def test_account_deactivation_and_immediate_session_revocation(test_client: TestClient):
    """Deactivating user rejects login and immediately invalidates active sessions with HTTP 401."""
    # Obtain admin token
    admin_login = test_client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    admin_token = admin_login.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Obtain user token while active
    user_login = test_client.post("/api/auth/login", json={"username": "user", "password": "user123"})
    assert user_login.status_code == 200
    user_token = user_login.json()["access_token"]
    user_headers = {"Authorization": f"Bearer {user_token}"}
    user_id = user_login.json()["user"]["id"]

    # Active token works
    assert test_client.get("/api/auth/me", headers=user_headers).status_code == 200

    # Admin deactivates user
    deact_res = test_client.put(
        f"/api/admin/users/{user_id}/status",
        headers=admin_headers,
        json={"is_active": False},
    )
    assert deact_res.status_code == 200
    assert deact_res.json()["is_active"] is False

    # Immediate session revocation: subsequent requests with active JWT fail with 401
    revoked_res = test_client.get("/api/auth/me", headers=user_headers)
    assert revoked_res.status_code == 401
    assert "inactive" in revoked_res.json()["detail"].lower()

    # Subsequent login attempt is rejected with 401
    login_attempt = test_client.post("/api/auth/login", json={"username": "user", "password": "user123"})
    assert login_attempt.status_code == 401

    # Reactivate user
    react_res = test_client.put(
        f"/api/admin/users/{user_id}/status",
        headers=admin_headers,
        json={"is_active": True},
    )
    assert react_res.status_code == 200
    assert react_res.json()["is_active"] is True

    # Login succeeds again
    react_login = test_client.post("/api/auth/login", json={"username": "user", "password": "user123"})
    assert react_login.status_code == 200


def test_guard_cannot_delete_or_deactivate_admin(test_client: TestClient):
    """Guard against deleting or deactivating the system admin account (HTTP 400)."""
    admin_login = test_client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    admin_id = admin_login.json()["user"]["id"]
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}

    # Attempt to deactivate admin
    deact_res = test_client.put(
        f"/api/admin/users/{admin_id}/status",
        headers=admin_headers,
        json={"is_active": False},
    )
    assert deact_res.status_code == 400
    assert "Cannot deactivate the system administrator account" in deact_res.json()["detail"]

    # Attempt to delete admin
    del_res = test_client.delete(f"/api/admin/users/{admin_id}", headers=admin_headers)
    assert del_res.status_code == 400
    assert "Cannot delete the system administrator account" in del_res.json()["detail"]

    # Verify admin is still active and in db
    admin_db = db_mod.fetch_one("SELECT is_active, is_admin FROM users WHERE id = %s", (admin_id,))
    assert admin_db is not None
    assert admin_db["is_active"] is True
    assert admin_db["is_admin"] is True


def test_cascading_deletion_of_non_admin(test_client: TestClient):
    """Deleting a non-admin cascades to remove associated user_farm_access rows."""
    admin_login = test_client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}

    # Create user
    create_res = test_client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"username": "tempdel", "password": "password123", "farm_ids": ["KF", "WOW"]},
    )
    assert create_res.status_code == 200
    new_user_id = create_res.json()["id"]

    # Verify rows in user_farm_access
    access_rows = db_mod.fetch_all("SELECT * FROM user_farm_access WHERE user_id = %s", (new_user_id,))
    assert len(access_rows) == 2

    # Delete user
    del_res = test_client.delete(f"/api/admin/users/{new_user_id}", headers=admin_headers)
    assert del_res.status_code == 200

    # Verify user record is gone
    assert db_mod.fetch_one("SELECT id FROM users WHERE id = %s", (new_user_id,)) is None

    # Verify user_farm_access records cascaded
    remaining_access = db_mod.fetch_all("SELECT * FROM user_farm_access WHERE user_id = %s", (new_user_id,))
    assert len(remaining_access) == 0


def test_farm_assignment_updates(test_client: TestClient):
    """Admin can atomically update assigned farms for a user; non-admin requires >= 1 farm."""
    admin_login = test_client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}

    create_res = test_client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"username": "farmedit", "password": "password123", "farm_ids": ["KF"]},
    )
    assert create_res.status_code == 200
    uid = create_res.json()["id"]

    # Check GET /api/admin/users/{id}/farms
    get_farms_res = test_client.get(f"/api/admin/users/{uid}/farms", headers=admin_headers)
    assert get_farms_res.status_code == 200
    assert get_farms_res.json()["farm_ids"] == ["KF"]

    # Update to WOW only
    update_res = test_client.put(
        f"/api/admin/users/{uid}/farms",
        headers=admin_headers,
        json={"farm_ids": ["WOW"]},
    )
    assert update_res.status_code == 200
    assert update_res.json()["farm_ids"] == ["WOW"]

    # Verify persistence
    get_farms_res2 = test_client.get(f"/api/admin/users/{uid}/farms", headers=admin_headers)
    assert get_farms_res2.json()["farm_ids"] == ["WOW"]

    # Attempt to clear farms on non-admin -> HTTP 400
    empty_res = test_client.put(
        f"/api/admin/users/{uid}/farms",
        headers=admin_headers,
        json={"farm_ids": []},
    )
    assert empty_res.status_code == 400
    assert "at least one farm" in empty_res.json()["detail"]

    # Attempt unknown farm -> HTTP 400
    bad_farm_res = test_client.put(
        f"/api/admin/users/{uid}/farms",
        headers=admin_headers,
        json={"farm_ids": ["INVALID_FARM"]},
    )
    assert bad_farm_res.status_code == 400
    assert "Unknown farm id" in bad_farm_res.json()["detail"]


def test_last_login_and_user_list_enrichment(test_client: TestClient):
    """Successful login records last_login and is returned in GET /api/admin/users."""
    # Login as user
    test_client.post("/api/auth/login", json={"username": "user", "password": "user123"})

    # Admin queries users list
    admin_login = test_client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}

    users_res = test_client.get("/api/admin/users", headers=admin_headers)
    assert users_res.status_code == 200
    users = users_res.json()
    user_entry = next((u for u in users if u["username"] == "user"), None)
    assert user_entry is not None
    assert user_entry["last_login"] is not None
    assert user_entry["role"] == "user"
    assert "KF" in user_entry["farm_ids"]
