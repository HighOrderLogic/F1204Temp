from unittest.mock import patch
from fastapi.testclient import TestClient

from app.auth.dependencies import CurrentUser, get_current_user
from app.auth.passwords import hash_password
from app.db import user_has_farm, get_user_farms
from app.main import app

client = TestClient(app)


def test_login_falls_back_when_role_column_missing():
    # Simulate DB raising an exception on first query because 'role' column is undefined
    legacy_user = {
        "id": 1,
        "username": "admin",
        "password_hash": hash_password("admin123"),
        "is_admin": True,
    }

    def mock_fetch_one(query, params=None):
        if "role" in query:
            raise Exception('column "role" does not exist')
        if "password_hash" in query:
            return legacy_user
        if "is_admin" in query:
            return {"id": 1, "username": "admin", "is_admin": True}
        return None

    with patch("app.routers.auth.fetch_one", side_effect=mock_fetch_one), \
         patch("app.routers.auth.record_user_login") as mock_record, \
         patch("app.routers.auth.get_user_farms", return_value=[{"farm_id": "KF", "farm_name": "Killara Feedlot", "slug": "kf"}]):
        res = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        assert res.status_code == 200
        data = res.json()
        assert "access_token" in data
        assert data["user"]["role"] == "admin"
        assert data["user"]["is_admin"] is True


def test_get_current_user_falls_back_when_role_missing():
    legacy_user = {
        "id": 2,
        "username": "owner",
        "is_admin": False,
    }

    def mock_fetch_one(query, params=None):
        if "role" in query:
            raise Exception('column "role" does not exist')
        return legacy_user

    class DummyCreds:
        credentials = "mock_token"

    with patch("jwt.decode", return_value={"sub": "2", "username": "owner", "farm_ids": ["KF"]}), \
         patch("app.auth.dependencies.fetch_one", side_effect=mock_fetch_one), \
         patch("app.auth.dependencies.get_user_farms", return_value=[{"farm_id": "KF", "farm_name": "Killara Feedlot", "slug": "kf"}]):
        current_user = get_current_user(DummyCreds())
        assert current_user.id == 2
        assert current_user.role == "owner"
        assert current_user.username == "owner"


def test_db_user_has_farm_falls_back_when_role_missing():
    def mock_fetch_one(query, params=None):
        if "role" in query:
            raise Exception('column "role" does not exist')
        if "is_admin" in query:
            return {"is_admin": True}
        return None

    with patch("app.db.fetch_one", side_effect=mock_fetch_one):
        assert user_has_farm(1, "KF") is True
