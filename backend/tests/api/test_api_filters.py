from __future__ import annotations

import pytest
from starlette.testclient import TestClient


@pytest.mark.integration
def test_api_choices_schema(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    """US07: Verifies /api/filters/choices returns filter dimensions."""
    fake_choices = {
        "farm_id": "KF",
        "years": [2023],
        "months": ["All", "January"],
        "days": ["All", 1],
        "sexes": ["Overall", "Steer", "Heifer"],
        "treatments": ["Overall", "No Treatment", "Drench"],
        "breeds": ["Overall", "Angus"],
        "mobs": ["Overall", "Front"],
        "measures": [{"key": "finalpweight", "label": "Final weight (kg)"}],
        "max_year": 2023,
    }
    monkeypatch.setattr("app.routers.filters.get_filter_choices", lambda farm_id, is_admin: fake_choices)
    res = client.get("/api/filters/choices?farm_id=KF", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert "years" in data
    assert "sexes" in data
    assert "measures" in data


@pytest.mark.integration
def test_api_bootstrap_schema(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    """US08: Verifies /api/filters/bootstrap returns initial paint choices and summary KPIs."""
    fake_choices = {"farm_id": "KF", "years": [2023], "max_year": 2023}
    fake_summary = {"groups": [], "record_count": 100}
    monkeypatch.setattr("app.routers.filters.get_filter_choices", lambda farm_id, is_admin: fake_choices)
    monkeypatch.setattr("app.services.sql_agg.summary_sql", lambda f, a, m: fake_summary)
    res = client.get("/api/filters/bootstrap?farm_id=KF&measure=finalpweight", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert "choices" in data
    assert "summary" in data


@pytest.mark.integration
def test_api_farm_access_authorization(client: TestClient, user_headers: dict[str, str]):
    """US04: Verifies user without access to farm is blocked with 403 Forbidden."""
    res = client.get("/api/filters/choices?farm_id=UNAUTHORIZED_FARM", headers=user_headers)
    assert res.status_code == 403


@pytest.mark.integration
def test_api_choices_active_herd_count(client: TestClient, admin_headers: dict[str, str]):
    """FR-ACT-04: Verifies /api/filters/choices includes active animal count vs total herd count."""
    res = client.get("/api/filters/choices?farm_id=KF", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert "active_animal_count" in data
    assert "total_animal_count" in data


@pytest.mark.integration
def test_api_choices_active_counts_matrix(client: TestClient, admin_headers: dict[str, str]):
    """FR-ACT-04: Verifies /api/filters/choices includes precomputed active_counts matrix for 0ms UI switches."""
    res = client.get("/api/filters/choices?farm_id=KF", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert "active_counts" in data
    matrix = data["active_counts"]
    for mode in ["auto", "max", "today"]:
        for days in [7, 14, 30]:
            assert f"{mode}_{days}" in matrix
            assert isinstance(matrix[f"{mode}_{days}"], int)


@pytest.mark.integration
def test_api_active_count_endpoint(client: TestClient, admin_headers: dict[str, str]):
    """FR-ACT-04: Verifies dedicated /api/filters/active-count endpoint returns active and total head."""
    res = client.get(
        "/api/filters/active-count?farm_id=KF&active_days=14&active_reference_mode=auto",
        headers=admin_headers,
    )
    assert res.status_code == 200
    data = res.json()
    assert "active_animal_count" in data
    assert "total_animal_count" in data
    assert isinstance(data["active_animal_count"], int)
    assert isinstance(data["total_animal_count"], int)

