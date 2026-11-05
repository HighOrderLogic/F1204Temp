from __future__ import annotations

import pandas as pd
from starlette.testclient import TestClient


def test_api_animals_query_raw_rows_admin_success(
    client: TestClient,
    admin_headers: dict[str, str],
    sample_df: pd.DataFrame,
    mock_filtered_data,
):
    """US02: Verifies admin users can access /api/data/query with include_rows=True and view raw records."""
    fake = mock_filtered_data(sample_df)
    payload = {"farm_id": "KF", "year": 2023, "page": 1, "page_size": 10, "include_rows": True}
    res = client.post("/api/data/query", json=payload, headers=admin_headers)
    assert res.status_code == 200
    fake.assert_called_once()
    data = res.json()
    assert "filtered" in data
    assert len(data["filtered"]) > 0
    raw_eids = set(sample_df["eid"].dropna().unique())
    assert data["filtered"][0]["eid"] in raw_eids


def test_api_animals_query_raw_rows_non_admin_forbidden(
    client: TestClient,
    user_headers: dict[str, str],
    sample_df: pd.DataFrame,
    mock_filtered_data,
):
    """US02: Verifies non-admin users receive 403 when requesting raw rows via /api/data/query."""
    fake = mock_filtered_data(sample_df)
    payload = {"farm_id": "KF", "year": 2023, "page": 1, "page_size": 10, "include_rows": True}
    res = client.post("/api/data/query", json=payload, headers=user_headers)
    assert res.status_code == 403
    fake.assert_not_called()
    assert "Admin access required" in res.json()["detail"]



def test_api_animals_export_csv_admin_success(
    client: TestClient,
    admin_headers: dict[str, str],
    sample_df: pd.DataFrame,
    mock_filtered_data,
):
    """US02: Verifies admin users can export raw animal CSV data."""
    fake = mock_filtered_data(sample_df)
    res = client.get("/api/data/export.csv?farm_id=KF&year=2023", headers=admin_headers)
    assert res.status_code == 200
    fake.assert_called_once()
    assert "text/csv" in res.headers["content-type"]
    assert "eid" in res.text


def test_api_animals_export_csv_non_admin_forbidden(
    client: TestClient,
    user_headers: dict[str, str],
    sample_df: pd.DataFrame,
    mock_filtered_data,
):
    """US02: Verifies non-admin users receive 403 when requesting /api/data/export.csv."""
    fake = mock_filtered_data(sample_df)
    res = client.get("/api/data/export.csv?farm_id=KF&year=2023", headers=user_headers)
    assert res.status_code == 403
    fake.assert_not_called()
    assert "Admin access required" in res.json()["detail"]


def test_api_animals_query_aggregate_count(
    client: TestClient,
    user_headers: dict[str, str],
    monkeypatch,
):
    """US02: Verifies /api/data/query returns aggregate record counts without exposing rows."""
    def fake_count(body, is_admin):
        return {"record_count": 42, "total_records": 100}

    monkeypatch.setattr("app.services.sql_agg.count_filtered", fake_count)
    payload = {"farm_id": "KF", "year": 2023, "include_rows": False}
    res = client.post("/api/data/query", json=payload, headers=user_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["record_count"] == 42
    assert data["total_records"] == 100
    assert data["filtered"] == []


def test_api_animals_query_active_counts(
    client: TestClient,
    admin_headers: dict[str, str],
):
    """FR-ACT-04: Verifies /api/data/query returns active_animal_count and total_animal_count."""
    payload = {"farm_id": "KF", "year": 2023, "include_rows": False}
    res = client.post("/api/data/query", json=payload, headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert "active_animal_count" in data
    assert "total_animal_count" in data
    assert isinstance(data["active_animal_count"], int)
    assert isinstance(data["total_animal_count"], int)


