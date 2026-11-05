from __future__ import annotations

import pandas as pd
import pytest
from starlette.testclient import TestClient

from app.services.filter_service import FilterService
from app.utils.anonymize import anonymize_eid


@pytest.fixture(autouse=True)
def anomaly_farm_data(monkeypatch):
    """Hermetic farm data: two animals, 40 consecutive days in 2026, weight + pweight columns.

    Without this, the tests silently depend on the developer's local DuckDB having a `weight`
    column, and fail everywhere else.
    """
    dates = pd.date_range("2026-08-13", periods=40, freq="D")
    rows = []
    for eid in ("982 123536700233", "982 123536700478"):
        for i, d in enumerate(dates):
            w = 500.0 + (i % 3) * 1.5
            rows.append({
                "eid": eid,
                "date": d,
                "weight": w,
                "pweight": w + 5.0,
                "sex": "Steer",
                "treatment": "Drench",
                "breed": "Angus",
                "mob": "Front",
            })
    df = pd.DataFrame(rows)
    monkeypatch.setattr(
        FilterService, "load_farm_dataframe", lambda farm_id, use_cache=True: df.copy()
    )


def test_api_anomalies_warm_requires_secret(client: TestClient):
    res = client.get("/api/anomalies/warm")
    assert res.status_code == 401


def test_api_anomalies_warm_ok(client: TestClient):
    from app.config import get_settings

    res = client.get("/api/anomalies/warm", params={"cron_secret": get_settings().cron_secret})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["warm"] == "anomalies"
    assert body["preloaded_rows"] is None
    assert isinstance(body.get("farm_ids"), list)


def test_api_anomalies_analyze_uses_snapshot_when_present(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    from app.services import anomaly_snapshot

    snap = {
        "available": True,
        "message": None,
        "farm_id": "KF",
        "weight_col": "weight",
        "sensitivity": "Medium",
        "counts": {"high": 1, "medium": 0, "low": 0, "normal": 0, "insufficient": 0, "total": 1},
        "animals": [{"eid": "E1", "severity": "High", "anomaly_types": "Sustained decline", "reason": "x",
                     "n_readings": 12, "change_kg_7d": -1.0, "change_pct_7d": -1.0}],
        "window_start": None,
        "window_end": None,
        "period_label": "2026",
        "scope": "farm",
        "n_reference": 1,
        "sigma": 1.0,
        "source_note": None,
        "herd_shift_note": None,
        "pweight_warning": None,
    }

    monkeypatch.setattr(
        anomaly_snapshot,
        "read_snapshot",
        lambda **kwargs: dict(snap),
    )
    res = client.post(
        "/api/anomalies/analyze",
        json={"farm_id": "KF", "year": 2026, "month": "All", "day": "All", "weight_col": "weight", "sensitivity": "Medium"},
        headers=admin_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["from_snapshot"] is True
    assert body["counts"]["total"] == 1
    assert body["animals"][0]["eid"] == "E1"


def test_api_anomalies_analyze_observed_weight_200(client: TestClient, admin_headers: dict[str, str]):
    payload = {
        "farm_id": "KF",
        "year": 2026,
        "month": "All",
        "day": "All",
        "weight_col": "weight",
        "sensitivity": "Medium",
    }
    res = client.post("/api/anomalies/analyze", json=payload, headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is True
    assert data["weight_col"] == "weight"
    assert data["sensitivity"] == "Medium"
    assert "counts" in data
    assert "animals" in data
    assert data["pweight_warning"] is None


def test_api_anomalies_analyze_pweight_has_warning(client: TestClient, admin_headers: dict[str, str]):
    payload = {
        "farm_id": "KF",
        "year": 2026,
        "month": "All",
        "day": "All",
        "weight_col": "pweight",
        "sensitivity": "Medium",
    }
    res = client.post("/api/anomalies/analyze", json=payload, headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["available"] is True
    assert data["weight_col"] == "pweight"
    assert data["pweight_warning"] is not None
    assert "pweight is smoothed" in data["pweight_warning"]


def test_api_anomalies_rejects_non_weight_measures(client: TestClient, admin_headers: dict[str, str]):
    # finalpweight is rejected with 422
    payload = {"farm_id": "KF", "year": 2026, "weight_col": "finalpweight"}
    res = client.post("/api/anomalies/analyze", json=payload, headers=admin_headers)
    assert res.status_code == 422

    # methane is rejected with 422
    payload_m = {"farm_id": "KF", "year": 2026, "weight_col": "methane"}
    res_m = client.post("/api/anomalies/analyze", json=payload_m, headers=admin_headers)
    assert res_m.status_code == 422


def test_api_anomalies_rejects_invalid_sensitivity(client: TestClient, admin_headers: dict[str, str]):
    payload = {"farm_id": "KF", "year": 2026, "weight_col": "weight", "sensitivity": "Ultra"}
    res = client.post("/api/anomalies/analyze", json=payload, headers=admin_headers)
    assert res.status_code == 422


def test_api_anomalies_ignores_sidebar_measure_picker(client: TestClient, admin_headers: dict[str, str]):
    # If the user has measure="methane" in their sidebar state, anomalies ignores it and uses weight_col
    payload = {
        "farm_id": "KF",
        "year": 2026,
        "measure": "methane",
        "measures": ["methane"],
        "weight_col": "weight",
    }
    res = client.post("/api/anomalies/analyze", json=payload, headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["weight_col"] == "weight"


def test_api_anomalies_admin_receives_raw_eids(client: TestClient, admin_headers: dict[str, str]):
    payload = {"farm_id": "KF", "year": 2026, "weight_col": "weight"}
    res = client.post("/api/anomalies/analyze", json=payload, headers=admin_headers)
    assert res.status_code == 200
    animals = res.json()["animals"]
    assert len(animals) > 0
    # Admins see raw EID strings
    assert not any(a["eid"].startswith("ID-") for a in animals)


def test_api_anomalies_user_receives_pseudonymized_eids(client: TestClient, user_headers: dict[str, str]):
    payload = {"farm_id": "KF", "year": 2026, "weight_col": "weight"}
    res = client.post("/api/anomalies/analyze", json=payload, headers=user_headers)
    assert res.status_code == 200
    animals = res.json()["animals"]
    assert len(animals) > 0
    # Regular users see masked ID-XXXXXXXX
    assert all(a["eid"].startswith("ID-") for a in animals)


def test_api_anomalies_owner_receives_raw_eids(client: TestClient, owner_headers: dict[str, str]):
    payload = {"farm_id": "KF", "year": 2026, "weight_col": "weight"}
    res = client.post("/api/anomalies/analyze", json=payload, headers=owner_headers)
    assert res.status_code == 200
    animals = res.json()["animals"]
    assert len(animals) > 0
    # Farm Owners see raw EID strings
    assert not any(a["eid"].startswith("ID-") for a in animals)


def test_api_anomalies_snapshot_owner_receives_raw_eids(
    client: TestClient, owner_headers: dict[str, str], user_headers: dict[str, str], monkeypatch
):
    from app.services import anomaly_snapshot

    snap = {
        "available": True,
        "farm_id": "KF",
        "year": 2026,
        "month": "All",
        "day": "All",
        "weight_col": "weight",
        "sensitivity": "Medium",
        "counts": {"high": 1, "medium": 0, "low": 0, "normal": 0, "insufficient": 0, "total": 1},
        "animals": [{"eid": "982 123834311455", "severity": "High", "weight": 400.0, "z_score": 3.0}],
    }
    monkeypatch.setattr(
        anomaly_snapshot,
        "read_snapshot",
        lambda **kwargs: dict(snap),
    )
    # Owner on snapshot path receives raw EID
    res_owner = client.post(
        "/api/anomalies/analyze",
        json={"farm_id": "KF", "year": 2026, "month": "All", "day": "All", "weight_col": "weight", "sensitivity": "Medium"},
        headers=owner_headers,
    )
    assert res_owner.status_code == 200
    assert res_owner.json()["animals"][0]["eid"] == "982 123834311455"

    # User on snapshot path receives masked EID
    res_user = client.post(
        "/api/anomalies/analyze",
        json={"farm_id": "KF", "year": 2026, "month": "All", "day": "All", "weight_col": "weight", "sensitivity": "Medium"},
        headers=user_headers,
    )
    assert res_user.status_code == 200
    assert res_user.json()["animals"][0]["eid"].startswith("ID-")

    # Fast snapshot GET route also respects owner unmasking
    res_fast_owner = client.get("/api/anomalies/snapshot?farm_id=KF&year=2026", headers=owner_headers)
    assert res_fast_owner.status_code == 200
    assert res_fast_owner.json()["animals"][0]["eid"] == "982 123834311455"

    # Fast snapshot GET route masks for user
    res_fast_user = client.get("/api/anomalies/snapshot?farm_id=KF&year=2026", headers=user_headers)
    assert res_fast_user.status_code == 200
    assert res_fast_user.json()["animals"][0]["eid"].startswith("ID-")


def test_api_anomalies_cross_farm_forbidden(client: TestClient, user_headers: dict[str, str]):
    # User is only authorized on KF, not DEMO or WOW
    payload = {"farm_id": "WOW", "year": 2026, "weight_col": "weight"}
    res = client.post("/api/anomalies/analyze", json=payload, headers=user_headers)
    assert res.status_code == 403


def test_api_anomalies_unauthorized_returns_401(client: TestClient):
    payload = {"farm_id": "KF", "year": 2026, "weight_col": "weight"}
    res = client.post("/api/anomalies/analyze", json=payload)
    assert res.status_code == 401


def test_api_anomalies_detail_admin(client: TestClient, admin_headers: dict[str, str]):
    analyze_res = client.post("/api/anomalies/analyze", json={"farm_id": "KF", "year": 2026}, headers=admin_headers)
    first_eid = analyze_res.json()["animals"][0]["eid"]

    payload = {"farm_id": "KF", "year": 2026, "animal": first_eid, "weight_col": "weight"}
    res = client.post("/api/anomalies/detail", json=payload, headers=admin_headers)
    assert res.status_code == 200
    detail = res.json()
    assert detail["eid"] == first_eid
    assert "daily" in detail
    assert "cusum_path" in detail


def test_api_anomalies_detail_user_with_masked_id(client: TestClient, user_headers: dict[str, str]):
    analyze_res = client.post("/api/anomalies/analyze", json={"farm_id": "KF", "year": 2026}, headers=user_headers)
    masked_id = analyze_res.json()["animals"][0]["eid"]
    assert masked_id.startswith("ID-")

    payload = {"farm_id": "KF", "year": 2026, "animal": masked_id, "weight_col": "weight"}
    res = client.post("/api/anomalies/detail", json=payload, headers=user_headers)
    assert res.status_code == 200
    detail = res.json()
    assert detail["eid"] == masked_id
    assert "daily" in detail


def test_api_anomalies_detail_user_raw_eid_probing_fails(client: TestClient, admin_headers: dict[str, str], user_headers: dict[str, str]):
    analyze_res = client.post("/api/anomalies/analyze", json={"farm_id": "KF", "year": 2026}, headers=admin_headers)
    raw_eid = analyze_res.json()["animals"][0]["eid"]

    # Non-admin user querying with raw EID must receive 404
    payload = {"farm_id": "KF", "year": 2026, "animal": raw_eid, "weight_col": "weight"}
    res = client.post("/api/anomalies/detail", json=payload, headers=user_headers)
    assert res.status_code == 404


def test_api_anomalies_detail_unknown_animal_returns_404(client: TestClient, admin_headers: dict[str, str]):
    payload = {"farm_id": "KF", "year": 2026, "animal": "NON_EXISTENT", "weight_col": "weight"}
    res = client.post("/api/anomalies/detail", json=payload, headers=admin_headers)
    assert res.status_code == 404


def test_api_anomalies_export_csv_admin(client: TestClient, admin_headers: dict[str, str]):
    payload = {"farm_id": "KF", "year": 2026, "weight_col": "weight", "sensitivity": "Medium"}
    res = client.post("/api/anomalies/export.csv", json=payload, headers=admin_headers)
    assert res.status_code == 200
    assert "text/csv" in res.headers["content-type"]
    assert "attachment" in res.headers["content-disposition"]
    header = res.text.strip().split("\n")[0].split(",")
    assert "eid" in header
    assert "severity" in header
    assert "anomaly_types" in header
    assert "shortfall_kg" in header


def test_api_anomalies_export_csv_forbidden_for_user(client: TestClient, user_headers: dict[str, str]):
    payload = {"farm_id": "KF", "year": 2026, "weight_col": "weight"}
    res = client.post("/api/anomalies/export.csv", json=payload, headers=user_headers)
    assert res.status_code == 403
    assert "Admin access required" in res.json()["detail"]


def test_api_anomalies_key_guard_triggers_503(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    from app.config import get_settings
    monkeypatch.delenv("EID_PSEUDONYM_KEY", raising=False)
    monkeypatch.setattr(get_settings(), "eid_pseudonym_key", "")
    payload = {"farm_id": "KF", "year": 2026, "weight_col": "weight"}
    res = client.post("/api/anomalies/analyze", json=payload, headers=admin_headers)
    assert res.status_code == 503
