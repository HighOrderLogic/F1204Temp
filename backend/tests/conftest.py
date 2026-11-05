from __future__ import annotations

import warnings

import pytest

with warnings.catch_warnings():
    # Starlette warns about its own httpx usage on import; not actionable in tests.
    warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")
    from starlette.testclient import TestClient

from app.auth.dependencies import CurrentUser, create_access_token
from app.main import app

pytest_plugins = [
    "tests.fixtures.data_frames",
    "tests.fixtures.service_mocks",
]


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Derives test markers automatically based on filesystem hierarchy."""
    for item in items:
        node = item.nodeid.replace("\\", "/")
        if "tests/api/" in node:
            item.add_marker(pytest.mark.api)
        elif "tests/unit/" in node:
            item.add_marker(pytest.mark.unit)
        elif "tests/integration/" in node:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True)
def pseudonym_key(monkeypatch):
    """The API refuses requests without EID_PSEUDONYM_KEY (see app.key_guard); give every test one."""
    monkeypatch.setenv("EID_PSEUDONYM_KEY", "test-pseudonym-key")


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient instance."""
    return TestClient(app)


@pytest.fixture
def admin_headers() -> dict[str, str]:
    """Authorization header with valid Admin JWT token for farm KF."""
    token, _ = create_access_token(user_id=1, username="admin", is_admin=True, farm_ids=["KF", "DEMO"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user_headers() -> dict[str, str]:
    """Authorization header with valid Viewer (non-admin) JWT token for farm KF."""
    token, _ = create_access_token(user_id=2, username="user", is_admin=False, farm_ids=["KF"], role="user")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def owner_headers() -> dict[str, str]:
    """Authorization header with valid Farm Owner (non-admin) JWT token for farm KF."""
    token, _ = create_access_token(user_id=3, username="owner", is_admin=False, farm_ids=["KF"], role="owner")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def mock_auth_db_dependency(monkeypatch):
    """Autouse fixture to mock database calls during JWT authentication in tests."""
    import app.db as db_mod
    original_fetch_one = db_mod.fetch_one

    def fake_fetch_one(query: str, params: tuple = ()):
        if "FROM users WHERE id =" in query or "FROM users WHERE username =" in query:
            uid = params[0] if params else 1
            if uid in (1, "admin"):
                return {
                    "id": 1,
                    "username": "admin",
                    "is_admin": True,
                    "is_active": True,
                    "password_hash": "$scrypt$ln=16,r=8,p=1$fake",
                }
            elif uid in (3, "owner"):
                return {
                    "id": 3,
                    "username": "owner",
                    "role": "owner",
                    "is_admin": False,
                    "is_active": True,
                    "password_hash": "$scrypt$ln=16,r=8,p=1$fake",
                }
            elif uid in (2, "user"):
                return {
                    "id": 2,
                    "username": "user",
                    "role": "user",
                    "is_admin": False,
                    "is_active": True,
                    "password_hash": "$scrypt$ln=16,r=8,p=1$fake",
                }
        return original_fetch_one(query, params)

    def fake_get_user_farms(user_id: int):
        return [{"farm_id": "KF", "farm_name": "Killara Feedlot", "slug": "kf", "is_active": True}]

    monkeypatch.setattr("app.auth.dependencies.fetch_one", fake_fetch_one)
    monkeypatch.setattr("app.auth.dependencies.get_user_farms", fake_get_user_farms)
    monkeypatch.setattr("app.routers.auth.get_user_farms", fake_get_user_farms)
    monkeypatch.setattr("app.db.fetch_one", fake_fetch_one)
    monkeypatch.setattr("app.db.get_user_farms", fake_get_user_farms)
