from __future__ import annotations

import re
import pandas as pd
import pytest
from starlette.testclient import TestClient


def test_owner_auth_me_profile(client: TestClient, owner_headers: dict[str, str]):
    """Verifies that owner session returns role='owner' and is_admin=False."""
    res = client.get("/api/auth/me", headers=owner_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["username"] == "owner"
    assert data["role"] == "owner"
    assert data["is_admin"] is False


def test_owner_query_raw_rows_success(
    client: TestClient,
    owner_headers: dict[str, str],
    sample_df: pd.DataFrame,
    mock_filtered_data,
):
    """Verifies owner role can access /api/data/query with include_rows=True and inspect unmasked EIDs."""
    fake = mock_filtered_data(sample_df)
    payload = {"farm_id": "KF", "year": 2023, "page": 1, "page_size": 10, "include_rows": True}
    res = client.post("/api/data/query", json=payload, headers=owner_headers)
    assert res.status_code == 200
    fake.assert_called_once()
    data = res.json()
    assert "filtered" in data
    assert len(data["filtered"]) > 0
    raw_eids = set(sample_df["eid"].dropna().unique())
    first_eid = data["filtered"][0]["eid"]
    assert first_eid in raw_eids
    assert not re.match(r"^ID-[0-9A-F]{8}$", first_eid)


def test_owner_export_raw_csv_success(
    client: TestClient,
    owner_headers: dict[str, str],
    sample_df: pd.DataFrame,
    mock_filtered_data,
):
    """Verifies owner role can export raw CSV data matching admin capability."""
    fake = mock_filtered_data(sample_df)
    res = client.get("/api/data/export.csv?farm_id=KF&year=2023", headers=owner_headers)
    assert res.status_code == 200
    fake.assert_called_once()
    assert "text/csv" in res.headers["content-type"]
    assert "eid" in res.text


def test_owner_cohorts_analyze_unmasked_eids(
    client: TestClient,
    owner_headers: dict[str, str],
    cohort_sample_df: pd.DataFrame,
    mock_filtered_data,
):
    """Verifies owner role receives unmasked physical EIDs in cohort analysis."""
    mock_filtered_data(cohort_sample_df)
    payload = {"farm_id": "KF", "year": 2023, "measure": "finalpweight", "percentile": 10}
    res = client.post("/api/cohorts/analyze", json=payload, headers=owner_headers)
    assert res.status_code == 200
    data = res.json()
    raw_eids = set(cohort_sample_df["eid"].dropna().unique())
    observed_eids = set()
    for tier in ("high", "medium", "low"):
        for a in data.get(tier, {}).get("animals", []):
            observed_eids.add(a["eid"])
            assert not re.match(r"^ID-[0-9A-F]{8}$", a["eid"])
    assert observed_eids.issubset(raw_eids)


def test_owner_cohorts_export_csv_success(
    client: TestClient,
    owner_headers: dict[str, str],
    cohort_sample_df: pd.DataFrame,
    mock_filtered_data,
):
    """Verifies owner role can export cohort CSV data with appended cohort column."""
    mock_filtered_data(cohort_sample_df)
    res = client.get("/api/cohorts/export.csv?farm_id=KF&year=2023&cohort=all", headers=owner_headers)
    assert res.status_code == 200
    assert "text/csv" in res.headers["content-type"]
    assert "cohort" in res.text


def test_owner_filter_choices_includes_eids(
    client: TestClient,
    owner_headers: dict[str, str],
    monkeypatch,
):
    """Verifies owner receives animal EID choices in filter options."""
    monkeypatch.setattr(
        "app.routers.filters.get_filter_choices",
        lambda farm_id, is_admin: {"eids": ["EID_001", "EID_002"], "years": [2023]},
    )
    res = client.get("/api/filters/choices?farm_id=KF", headers=owner_headers)
    assert res.status_code == 200
    data = res.json()
    assert "eids" in data
    assert data["eids"] == ["EID_001", "EID_002"]


@pytest.mark.parametrize(
    "endpoint,method,payload",
    [
        ("/api/admin/users", "GET", None),
        ("/api/admin/credentials/users", "GET", None),
        ("/api/admin/credentials/username", "PUT", {"user_id": 2, "new_username": "newuser"}),
        ("/api/admin/credentials/password", "PUT", {"user_id": 2, "new_password": "newpassword123"}),
        ("/api/admin/farms/KF/logos", "GET", None),
        ("/api/admin/farms/KF/upload", "POST", None),
    ],
)
def test_owner_forbidden_from_admin_endpoints(
    client: TestClient,
    owner_headers: dict[str, str],
    endpoint: str,
    method: str,
    payload: dict | None,
):
    """RBAC verification: owner is strictly decoupled and barred from administrative endpoints (HTTP 403)."""
    if method == "GET":
        res = client.get(endpoint, headers=owner_headers)
    elif method == "PUT":
        res = client.put(endpoint, json=payload, headers=owner_headers)
    elif method == "POST":
        res = client.post(endpoint, json=payload, headers=owner_headers)
    assert res.status_code == 403
    assert "Admin access required" in res.json()["detail"]


def test_admin_credentials_list_identifies_owner_role(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch,
):
    """Verifies that the admin credential manager surfaces 'owner' as role for owner account."""
    monkeypatch.setattr(
        "app.routers.admin.fetch_all",
        lambda query, params=(): [
            {"id": 1, "username": "admin", "is_admin": True},
            {"id": 2, "username": "user", "is_admin": False},
            {"id": 3, "username": "owner", "is_admin": False},
        ],
    )
    res = client.get("/api/admin/credentials/users", headers=admin_headers)
    assert res.status_code == 200
    users = res.json()
    role_map = {u["username"]: u["role"] for u in users}
    assert role_map["admin"] == "admin"
    assert role_map["user"] == "user"
    assert role_map["owner"] == "owner"

