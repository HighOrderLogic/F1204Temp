from __future__ import annotations

import pytest
from starlette.testclient import TestClient


@pytest.mark.integration
def test_api_timeseries_single_measure(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    """US10: Verifies /api/charts/timeseries returns valid series and label."""
    fake_ts = {"series": [{"date": "2023-01-01", "group": "Overall Average", "value": 500.0, "count": 10}], "y_label": "Final weight (kg)"}
    monkeypatch.setattr("app.services.sql_agg.timeseries_sql", lambda body, is_admin, measure, smooth: fake_ts)
    payload = {"farm_id": "KF", "year": 2023, "measure": "finalpweight"}
    res = client.post("/api/charts/timeseries", json=payload, headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert "series" in data
    assert "y_label" in data


@pytest.mark.integration
def test_api_timeseries_grain_caching(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    """US08: Verifies /api/charts/timeseries-grain returns daily dimensional grain."""
    fake_grain = {"grain": [{"d": "2023-01-01", "s": "Steer", "t": "Drench", "b": "Angus", "m": "Front", "v": 500.0, "c": 1}], "y_label": "Weight (kg)", "record_count": 1}
    monkeypatch.setattr("app.services.sql_agg.timeseries_grain_sql", lambda body, measure: fake_grain)
    payload = {"farm_id": "KF", "year": 2023, "measure": "finalpweight"}
    res = client.post("/api/charts/timeseries-grain", json=payload, headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert "grain" in data
    assert "record_count" in data


@pytest.mark.integration
def test_api_distribution_baseline(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    """US11: Verifies /api/charts/distribution returns histogram and boxplot structures."""
    fake_dist = {
        "histogram": {"bins": 20, "mean": 510.0, "median": 505.0, "groups": [{"group": "Overall", "values": [500.0, 520.0]}]},
        "boxplot": {"groups": [{"group": "Overall", "values": [500.0, 520.0]}]},
        "record_count": 2,
    }
    monkeypatch.setattr("app.services.sql_agg.distribution_sql", lambda body, is_admin, measure, hist_bins: fake_dist)
    payload = {"farm_id": "KF", "year": 2023, "measure": "finalpweight"}
    res = client.post("/api/charts/distribution", json=payload, headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert "histogram" in data
    assert "boxplot" in data


@pytest.mark.integration
def test_api_timeseries_multi_measure(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    """US10: Verifies /api/charts/timeseries supports querying multiple measures simultaneously."""
    def fake_ts(body, is_admin, measure, smooth):
        return {
            "series": [{"date": "2023-01-01", "group": "Overall Average", "value": 500.0, "count": 10}],
            "y_label": f"{measure} (unit)",
            "combo_coverage": {},
            "common_filters_note": None,
            "record_count": 10,
        }
    monkeypatch.setattr("app.services.sql_agg.timeseries_sql", fake_ts)
    payload = {"farm_id": "KF", "year": 2023, "measures": ["finalpweight", "methane"]}
    res = client.post("/api/charts/timeseries", json=payload, headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data.get("charts", [])) == 2
    assert "series" in data
    assert "y_label" in data


@pytest.mark.integration
def test_api_timeseries_three_concurrent_measures(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    """US10: Verifies timeseries endpoint supports 3 concurrent measures with distinct labels."""
    def fake_ts(body, is_admin, measure, smooth):
        return {
            "series": [{"date": "2023-01-01", "group": "Overall Average", "value": 100.0, "count": 5}],
            "y_label": f"{measure} (unit)",
            "combo_coverage": {},
            "common_filters_note": None,
            "record_count": 5,
        }
    monkeypatch.setattr("app.services.sql_agg.timeseries_sql", fake_ts)
    payload = {"farm_id": "KF", "year": 2023, "measures": ["finalpweight", "methane", "finalgrowthpbs"]}
    res = client.post("/api/charts/timeseries", json=payload, headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    charts = data.get("charts", [])
    assert len(charts) == 3
    measures_returned = [c["measure"] for c in charts]
    assert measures_returned == ["finalpweight", "methane", "finalgrowthpbs"]
    assert charts[0]["y_label"] == "finalpweight (unit)"
    assert charts[1]["y_label"] == "methane (unit)"
    assert charts[2]["y_label"] == "finalgrowthpbs (unit)"


@pytest.mark.integration
def test_api_timeseries_non_admin_authorized(client: TestClient, user_headers: dict[str, str], monkeypatch):
    """US02/US10: Verifies non-admin sessions can query timeseries aggregates without exposing animal EIDs."""
    def fake_ts(body, is_admin, measure, smooth):
        return {
            "series": [{"date": "2023-01-01", "group": "Overall Average", "value": 500.0, "count": 10}],
            "y_label": f"{measure} (unit)",
            "combo_coverage": {},
            "common_filters_note": None,
            "record_count": 10,
        }
    monkeypatch.setattr("app.services.sql_agg.timeseries_sql", fake_ts)
    payload = {"farm_id": "KF", "year": 2023, "measures": ["finalpweight"]}
    res = client.post("/api/charts/timeseries", json=payload, headers=user_headers)
    assert res.status_code == 200
    data = res.json()
    assert "series" in data
    # Aggregated series has group names, not individual EIDs
    for pt in data["series"]:
        assert "eid" not in pt

