from __future__ import annotations

import pandas as pd
import pytest
from starlette.testclient import TestClient


def test_api_cohorts_analyze_baseline_200(
    client: TestClient,
    admin_headers: dict[str, str],
    cohort_sample_df: pd.DataFrame,
    mock_filtered_data,
):
    """US12: Verifies /api/cohorts/analyze returns 200 with cohort data structure."""
    fake = mock_filtered_data(cohort_sample_df)
    payload = {"farm_id": "KF", "year": 2023, "measure": "finalpweight", "percentile": 10}
    res = client.post("/api/cohorts/analyze", json=payload, headers=admin_headers)
    assert res.status_code == 200
    fake.assert_called_once()
    data = res.json()
    assert "total_animals" in data
    assert "timeline" in data


def test_api_cohorts_export_single_baseline(
    client: TestClient,
    admin_headers: dict[str, str],
    cohort_sample_df: pd.DataFrame,
    mock_filtered_data,
):
    """US13: Verifies baseline /api/cohorts/export.csv streams attachment."""
    fake = mock_filtered_data(cohort_sample_df)
    res = client.get("/api/cohorts/export.csv?farm_id=KF&year=2023&cohort=top", headers=admin_headers)
    assert res.status_code == 200
    fake.assert_called_once()
    assert "text/csv" in res.headers["content-type"]
    assert "eid,avg_measure" in res.text


def test_api_cohorts_3tier_schema(
    client: TestClient,
    admin_headers: dict[str, str],
    cohort_sample_df: pd.DataFrame,
    mock_filtered_data,
):
    """US12: Verifies API response returns high, medium, and low performance cohorts."""
    fake = mock_filtered_data(cohort_sample_df)
    payload = {"farm_id": "KF", "year": 2023, "measure": "finalpweight", "percentile": 10}
    res = client.post("/api/cohorts/analyze", json=payload, headers=admin_headers)
    assert res.status_code == 200
    fake.assert_called_once()
    data = res.json()
    assert "high" in data
    assert "medium" in data
    assert "low" in data


def test_api_cohorts_export_tagged_dataset(
    client: TestClient,
    admin_headers: dict[str, str],
    cohort_sample_df: pd.DataFrame,
    mock_filtered_data,
):
    """US13: Verifies export streams full dataset with an appended 'cohort' classification column."""
    fake = mock_filtered_data(cohort_sample_df)
    res = client.get("/api/cohorts/export.csv?farm_id=KF&year=2023&cohort=all", headers=admin_headers)
    assert res.status_code == 200
    fake.assert_called_once()
    lines = res.text.strip().split("\n")
    header = lines[0].split(",")
    assert "cohort" in header


def test_api_cohorts_export_forbidden_for_regular_user(
    client: TestClient,
    user_headers: dict[str, str],
    cohort_sample_df: pd.DataFrame,
    mock_filtered_data,
):
    """US02/US03: Verifies non-admin regular users are forbidden from exporting cohort CSV data (HTTP 403)."""
    fake = mock_filtered_data(cohort_sample_df)
    res = client.get("/api/cohorts/export.csv?farm_id=KF&year=2023&cohort=all", headers=user_headers)
    assert res.status_code == 403
    fake.assert_not_called()
    assert "Admin access required" in res.json()["detail"]


def test_api_cohorts_non_admin_receives_32bit_pseudonymized_eids(
    client: TestClient,
    user_headers: dict[str, str],
    cohort_sample_df: pd.DataFrame,
    mock_filtered_data,
):
    """US02: Verifies non-admin sessions receive 32-bit pseudonymized EIDs matching ID-XXXXXXXX."""
    import re

    mock_filtered_data(cohort_sample_df)
    payload = {"farm_id": "KF", "year": 2023, "measure": "finalpweight", "percentile": 10}
    res = client.post("/api/cohorts/analyze", json=payload, headers=user_headers)
    assert res.status_code == 200
    data = res.json()
    eids = []
    for tier in ("high", "medium", "low"):
        for a in data.get(tier, {}).get("animals", []):
            eid = a["eid"]
            assert re.match(r"^ID-[0-9A-F]{8}$", eid) is not None, f"Expected 32-bit hex ID, got {eid}"
            assert eid != "*****"
            eids.append(eid)
    assert len(eids) > 0
    assert len(eids) == len(set(eids)), "Pseudonymized cohort EIDs must not collide"


def test_api_cohorts_admin_receives_raw_unmasked_eids(
    client: TestClient,
    admin_headers: dict[str, str],
    cohort_sample_df: pd.DataFrame,
    mock_filtered_data,
):
    """US02: Verifies admin sessions receive raw, unmasked EIDs."""
    mock_filtered_data(cohort_sample_df)
    payload = {"farm_id": "KF", "year": 2023, "measure": "finalpweight", "percentile": 10}
    res = client.post("/api/cohorts/analyze", json=payload, headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    raw_eids = set(cohort_sample_df["eid"].dropna().unique())
    observed_eids = set()
    for tier in ("high", "medium", "low"):
        for a in data.get(tier, {}).get("animals", []):
            observed_eids.add(a["eid"])
    assert observed_eids.issubset(raw_eids)

