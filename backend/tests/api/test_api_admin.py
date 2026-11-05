from __future__ import annotations

import io
import pytest
from starlette.testclient import TestClient


@pytest.mark.integration
def test_api_admin_csv_upload_duplicate_skip(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    """US06: Verifies batch CSV upload skips existing records when duplicate_mode='skip'."""
    csv_content = b"EID,Date,Sex,Breed,Treatment,Mob,Final Pweight\n982 123536700001,01/01/2023,Steer,Angus,Drench,Front,500.0\n"
    
    monkeypatch.setattr("app.routers.admin.fetch_one", lambda q, p: {"cnt": 1, "id": 100})
    monkeypatch.setattr("app.routers.admin.execute_returning", lambda q, p: {"id": 1})
    monkeypatch.setattr("app.routers.admin.execute", lambda q, p: None)
    monkeypatch.setattr("app.routers.admin.refresh_forecasts", lambda farm_id: {"status": "updated"})

    files = {"file": ("KF_2023-01-01.csv", io.BytesIO(csv_content), "text/csv")}
    data = {"duplicate_mode": "skip"}
    res = client.post("/api/admin/farms/KF/upload", files=files, data=data, headers=admin_headers)
    assert res.status_code == 200
    res_data = res.json()
    assert res_data["rows_skipped"] == 1
    assert res_data["rows_inserted"] == 0


@pytest.mark.integration
def test_api_admin_csv_upload_overwrite(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    """US06: Verifies batch CSV upload overwrites existing records when duplicate_mode='overwrite'."""
    csv_content = b"EID,Date,Sex,Breed,Treatment,Mob,Final Pweight\n982 123536700001,01/01/2023,Steer,Angus,Drench,Front,520.0\n"
    
    monkeypatch.setattr("app.routers.admin.fetch_one", lambda q, p: {"cnt": 1, "id": 100})
    monkeypatch.setattr("app.routers.admin.execute_returning", lambda q, p: {"id": 1})
    monkeypatch.setattr("app.routers.admin.execute", lambda q, p: None)
    monkeypatch.setattr("app.routers.admin.refresh_forecasts", lambda farm_id: {"status": "updated"})

    files = {"file": ("KF_2023-01-01.csv", io.BytesIO(csv_content), "text/csv")}
    data = {"duplicate_mode": "overwrite"}
    res = client.post("/api/admin/farms/KF/upload", files=files, data=data, headers=admin_headers)
    assert res.status_code == 200
    res_data = res.json()
    assert res_data["rows_overwritten"] == 1


NEW_ROW_CSV = b"EID,Date,Final Pweight\n982 123536700001,01/01/2023,500.0\n"
REFRESHED = {"animals": 1, "rows": 182, "data_through": "2023-01-01", "model_version": "v", "seconds": 0.1}


@pytest.fixture
def new_rows_db(monkeypatch):
    """Every uploaded row is new; nothing touches a real database."""
    monkeypatch.setattr("app.routers.admin.fetch_one", lambda q, p: None)
    monkeypatch.setattr("app.routers.admin.execute_returning", lambda q, p: {"id": 1})
    monkeypatch.setattr("app.routers.admin.execute", lambda q, p: None)


def upload(client: TestClient, headers: dict[str, str], **data):
    files = {"file": ("KF_2023-01-01.csv", io.BytesIO(NEW_ROW_CSV), "text/csv")}
    return client.post("/api/admin/farms/KF/upload", files=files, data=data, headers=headers)


@pytest.mark.integration
def test_upload_refreshes_the_farms_forecasts(client, admin_headers, new_rows_db, monkeypatch):
    refreshed = []
    monkeypatch.setattr(
        "app.services.forecast_service.refresh_farm", lambda farm_id: refreshed.append(farm_id) or REFRESHED
    )
    res = upload(client, admin_headers)
    assert res.status_code == 200
    assert res.json()["rows_inserted"] == 1
    assert res.json()["forecasts"] == {"status": "updated", **REFRESHED}
    assert refreshed == ["KF"]


@pytest.mark.integration
def test_upload_can_skip_the_forecast_refresh(client, admin_headers, new_rows_db, monkeypatch):
    monkeypatch.setattr("app.routers.admin.refresh_forecasts", lambda farm_id: pytest.fail("should not forecast"))
    res = upload(client, admin_headers, forecast="false")
    assert res.status_code == 200
    assert res.json()["forecasts"] == {"status": "skipped"}


@pytest.mark.integration
def test_forecast_failure_does_not_fail_the_upload(client, admin_headers, new_rows_db, monkeypatch):
    """The records are saved by then: report the failure and how to retry instead of a 500."""

    def broken(farm_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("app.services.forecast_service.refresh_farm", broken)
    res = upload(client, admin_headers)
    assert res.status_code == 200
    assert res.json()["rows_inserted"] == 1
    forecasts = res.json()["forecasts"]
    assert forecasts["status"] == "failed"
    assert "database unavailable" in forecasts["detail"]
    assert "/api/admin/farms/KF/forecasts/refresh" in forecasts["detail"]


@pytest.mark.integration
def test_refresh_forecasts_endpoint(client, admin_headers, monkeypatch):
    monkeypatch.setattr("app.services.forecast_service.refresh_farm", lambda farm_id: REFRESHED)
    res = client.post("/api/admin/farms/KF/forecasts/refresh", headers=admin_headers)
    assert res.status_code == 200
    assert res.json() == {"status": "updated", **REFRESHED}


@pytest.mark.integration
def test_refresh_forecasts_endpoint_reports_failures(client, admin_headers, monkeypatch):
    def broken(farm_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("app.services.forecast_service.refresh_farm", broken)
    res = client.post("/api/admin/farms/KF/forecasts/refresh", headers=admin_headers)
    assert res.status_code == 500
    assert "database unavailable" in res.json()["detail"]


@pytest.mark.integration
def test_refresh_forecasts_endpoint_is_admin_only(client, user_headers):
    res = client.post("/api/admin/farms/KF/forecasts/refresh", headers=user_headers)
    assert res.status_code == 403


@pytest.mark.integration
def test_api_admin_users_list(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    """US03: Verifies admin users listing endpoint returns user accounts."""
    fake_users = [{"id": 1, "username": "admin", "is_admin": True, "is_active": True, "created_at": "2023-01-01"}]
    monkeypatch.setattr("app.routers.admin.fetch_all", lambda q: fake_users)
    res = client.get("/api/admin/users", headers=admin_headers)
    assert res.status_code == 200
    assert len(res.json()) == 1
    assert res.json()[0]["username"] == "admin"


@pytest.mark.integration
def test_api_admin_credentials_reject_duplicate_username(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    def fake_fetch_one(query, params):
        if "lower(username)" in query:
            return {"id": 2}
        return {"id": 1, "username": "admin"}

    monkeypatch.setattr("app.routers.admin.fetch_one", fake_fetch_one)
    res = client.put(
        "/api/admin/credentials/username",
        headers=admin_headers,
        json={"user_id": 1, "new_username": "user"},
    )
    assert res.status_code == 409


@pytest.mark.integration
def test_api_admin_credentials_hidden_from_viewer(client: TestClient, user_headers: dict[str, str]):
    res = client.get("/api/admin/credentials/users", headers=user_headers)
    assert res.status_code == 403


@pytest.mark.integration
def test_api_admin_credentials_reject_renaming_owner(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    monkeypatch.setattr("app.routers.admin.fetch_one", lambda q, p=None: {"id": 2, "username": "owner"})
    res = client.put(
        "/api/admin/credentials/username",
        headers=admin_headers,
        json={"user_id": 2, "new_username": "new_owner"},
    )
    assert res.status_code == 400
    assert "owner" in res.json()["detail"].lower()


@pytest.mark.integration
def test_api_admin_credentials_reject_renaming_to_owner(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    monkeypatch.setattr("app.routers.admin.fetch_one", lambda q, p=None: {"id": 3, "username": "user"})
    res = client.put(
        "/api/admin/credentials/username",
        headers=admin_headers,
        json={"user_id": 3, "new_username": "Owner"},
    )
    assert res.status_code == 400
    assert "owner" in res.json()["detail"].lower()


@pytest.mark.integration
def test_api_admin_credentials_reject_username_over_50_chars(client: TestClient, admin_headers: dict[str, str]):
    long_username = "a" * 51
    res = client.put(
        "/api/admin/credentials/username",
        headers=admin_headers,
        json={"user_id": 3, "new_username": long_username},
    )
    assert res.status_code == 400
    assert "1–50 characters" in res.json()["detail"]


@pytest.mark.integration
def test_api_admin_credentials_reject_password_under_8_chars(client: TestClient, admin_headers: dict[str, str]):
    res = client.put(
        "/api/admin/credentials/password",
        headers=admin_headers,
        json={"user_id": 3, "new_password": "short12"},
    )
    assert res.status_code == 400
    assert "at least 8 characters" in res.json()["detail"]


@pytest.mark.integration
def test_api_admin_credentials_accept_valid_password(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    monkeypatch.setattr("app.routers.admin.fetch_one", lambda q, p=None: {"id": 3, "username": "user"})
    monkeypatch.setattr("app.routers.admin.execute", lambda q, p=None: None)
    res = client.put(
        "/api/admin/credentials/password",
        headers=admin_headers,
        json={"user_id": 3, "new_password": "valid_secure_pass_123"},
    )
    assert res.status_code == 200
    assert res.json()["detail"] == "Password updated"

