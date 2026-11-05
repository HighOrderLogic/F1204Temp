from __future__ import annotations

import io
import pytest
from starlette.testclient import TestClient

from app.db import execute, fetch_all, fetch_one
from app.services.choices_service import get_filter_choices
from app.services.variable_service import (
    REQUIRED_WEIGHT_MEASURES,
    RESERVED_FILTER_NAMES,
    VariableService,
    invalidate_active_columns,
)

_CONFIGURED_FARMS = ("KF", "TEST_FARM", "TEST_NULL", "TEST_HEUR", "CONFIGURED_FARM", "FALLBACK_FARM")


@pytest.fixture(autouse=True)
def _reset_farm_columns():
    """Delete farm configurations these tests save so they don't leak into other tests."""
    yield
    for farm_id in _CONFIGURED_FARMS:
        try:
            execute("DELETE FROM farm_columns WHERE farm_id = %s", (farm_id,))
        except Exception:
            pass
    invalidate_active_columns()


def test_classify_data_type():
    """Verify mapping of column names and SQL data types to kinds."""
    assert VariableService.classify_data_type("eid", "VARCHAR(50)") == "id"
    assert VariableService.classify_data_type("farm_id", "VARCHAR(50)") == "id"
    assert VariableService.classify_data_type("date", "DATE") == "date"

    # Numeric types
    assert VariableService.classify_data_type("weight", "DOUBLE PRECISION") == "numeric"
    assert VariableService.classify_data_type("count", "INTEGER") == "numeric"
    assert VariableService.classify_data_type("val", "BIGINT") == "numeric"
    assert VariableService.classify_data_type("score", "FLOAT") == "numeric"
    assert VariableService.classify_data_type("pct", "DECIMAL(10,2)") == "numeric"
    assert VariableService.classify_data_type("tiny", "TINYINT") == "numeric"

    # Categorical types
    assert VariableService.classify_data_type("breed", "VARCHAR(100)") == "categorical"
    assert VariableService.classify_data_type("treatment", "TEXT") == "categorical"
    assert VariableService.classify_data_type("code", "CHAR(5)") == "categorical"
    assert VariableService.classify_data_type("is_active", "BOOLEAN") == "categorical"

    # Other types
    assert VariableService.classify_data_type("raw_blob", "BLOB") == "other"
    assert VariableService.classify_data_type("metadata", "JSON") == "other"


def test_distinct_count_with_null():
    """Verify distinct count treating NULL as an independent category (+1)."""
    VariableService.ensure_schema()
    execute("DELETE FROM animal_data WHERE farm_id = 'TEST_NULL'")
    execute("INSERT INTO animal_data (farm_id, eid, date, treatment, finalpweight) VALUES ('TEST_NULL', 'E1', '2023-01-01', 'TRT_A', 450.0)")
    execute("INSERT INTO animal_data (farm_id, eid, date, treatment, finalpweight) VALUES ('TEST_NULL', 'E2', '2023-01-02', 'TRT_A', 460.0)")
    execute("INSERT INTO animal_data (farm_id, eid, date, treatment, finalpweight) VALUES ('TEST_NULL', 'E3', '2023-01-03', NULL, 470.0)")

    cols = VariableService.inspect_and_suggest_columns("TEST_NULL", force_rescan=True)
    col_map = {c["column_name"]: c for c in cols}
    assert "treatment" in col_map
    # Distinct non-null is 1 ('TRT_A'), plus NULL increment = 2
    assert col_map["treatment"]["distinct_count"] == 2


def test_auto_suggestion_heuristics():
    """Verify auto-suggestion bounds: 2 <= distinct <= 50 for categoricals; numerics shown; reserved excluded."""
    VariableService.ensure_schema()
    execute("DELETE FROM animal_data WHERE farm_id = 'TEST_HEUR'")
    # Insert 1 distinct category (monomorphic constant: should NOT be filter)
    execute("INSERT INTO animal_data (farm_id, eid, date, breed, sex, finalpweight) VALUES ('TEST_HEUR', 'E1', '2023-01-01', 'Angus', 'Steer', 500.0)")
    execute("INSERT INTO animal_data (farm_id, eid, date, breed, sex, finalpweight) VALUES ('TEST_HEUR', 'E2', '2023-01-02', 'Angus', 'Heifer', 510.0)")

    cols = VariableService.inspect_and_suggest_columns("TEST_HEUR", force_rescan=True)
    col_map = {c["column_name"]: c for c in cols}

    # breed has 1 distinct value (Angus) -> is_filter should be False
    assert col_map["breed"]["distinct_count"] == 1
    assert col_map["breed"]["is_filter"] is False

    # sex has 2 distinct values (Steer, Heifer) -> is_filter should be True
    assert col_map["sex"]["distinct_count"] == 2
    assert col_map["sex"]["is_filter"] is True

    # finalpweight is numeric -> is_shown = True, is_filter = False
    assert col_map["finalpweight"]["kind"] == "numeric"
    assert col_map["finalpweight"]["is_shown"] is True
    assert col_map["finalpweight"]["is_filter"] is False

    # date and eid are reserved/id -> is_filter = False
    assert col_map["date"]["is_filter"] is False
    assert col_map["eid"]["is_filter"] is False


def test_weight_measure_guard_validation():
    """Verify ValueError is raised when configuration omits weight measures."""
    columns_without_weight = [
        {"column_name": "sex", "kind": "categorical", "is_filter": True, "is_shown": False, "distinct_count": 2},
        {"column_name": "methane", "kind": "numeric", "is_filter": False, "is_shown": True, "distinct_count": 10},
    ]
    with pytest.raises(ValueError, match="Configuration must include at least one weight measure"):
        VariableService.save_farm_columns("TEST_FARM", columns_without_weight)

    columns_with_weight = [
        {"column_name": "sex", "kind": "categorical", "is_filter": True, "is_shown": False, "distinct_count": 2},
        {"column_name": "finalpweight", "kind": "numeric", "is_filter": False, "is_shown": True, "distinct_count": 10},
    ]
    # Should succeed without error
    VariableService.save_farm_columns("TEST_FARM", columns_with_weight)


def test_get_columns_endpoint(client: TestClient, admin_headers: dict[str, str], user_headers: dict[str, str]):
    """Verify GET /api/admin/farms/{farm_id}/columns RBAC and schema."""
    # Non-admin -> 403 Forbidden
    res_user = client.get("/api/admin/farms/KF/columns", headers=user_headers)
    assert res_user.status_code == 403

    # Admin -> 200 OK with FarmColumnsResponse contract
    res_admin = client.get("/api/admin/farms/KF/columns", headers=admin_headers)
    assert res_admin.status_code == 200
    data = res_admin.json()
    assert data["farm_id"] == "KF"
    assert "filters_count" in data
    assert "measures_count" in data
    assert "active_filters" in data
    assert "active_measures" in data
    assert "columns" in data
    assert isinstance(data["columns"], list)


def test_put_columns_weight_guard(client: TestClient, admin_headers: dict[str, str]):
    """Verify PUT /api/admin/farms/{farm_id}/columns returns 422 if weight measure is omitted."""
    # Attempt updating with no weight measures
    invalid_body = {
        "columns": [
            {"column_name": "sex", "kind": "categorical", "is_filter": True, "is_shown": False, "distinct_count": 2},
            {"column_name": "methane", "kind": "numeric", "is_filter": False, "is_shown": True, "distinct_count": 5},
            {"column_name": "finalpweight", "kind": "numeric", "is_filter": False, "is_shown": False, "distinct_count": 10},
        ]
    }
    res = client.put("/api/admin/farms/KF/columns", json=invalid_body, headers=admin_headers)
    assert res.status_code == 422
    assert "weight measure" in res.json()["detail"].lower()

    # Valid payload with finalpweight enabled
    valid_body = {
        "columns": [
            {"column_name": "sex", "kind": "categorical", "is_filter": True, "is_shown": False, "distinct_count": 2},
            {"column_name": "finalpweight", "kind": "numeric", "is_filter": False, "is_shown": True, "distinct_count": 10},
        ]
    }
    res_valid = client.put("/api/admin/farms/KF/columns", json=valid_body, headers=admin_headers)
    assert res_valid.status_code == 200
    data = res_valid.json()
    assert "finalpweight" in data["active_measures"]
    assert "sex" in data["active_filters"]


def test_post_columns_inspect_endpoint(client: TestClient, admin_headers: dict[str, str], user_headers: dict[str, str]):
    """Verify POST /api/admin/farms/{farm_id}/columns/inspect."""
    res_user = client.post("/api/admin/farms/KF/columns/inspect", headers=user_headers)
    assert res_user.status_code == 403

    res_admin = client.post("/api/admin/farms/KF/columns/inspect", headers=admin_headers)
    assert res_admin.status_code == 200
    data = res_admin.json()
    assert data["farm_id"] == "KF"
    assert isinstance(data["columns"], list)


def test_upload_auto_seeds_columns(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    """Verify upload endpoint auto-seeds farm_columns and reports new columns."""
    csv_content = b"EID,Date,Sex,Breed,Treatment,Mob,Final Pweight\n982 123536700001,01/01/2023,Steer,Angus,Drench,Front,500.0\n"

    monkeypatch.setattr("app.routers.admin.fetch_one", lambda q, p: {"cnt": 1, "id": 100})
    monkeypatch.setattr("app.routers.admin.execute_returning", lambda q, p: {"id": 1})
    monkeypatch.setattr("app.routers.admin.execute", lambda q, p: None)
    monkeypatch.setattr("app.routers.admin.refresh_forecasts", lambda farm_id: {"status": "updated"})
    monkeypatch.setattr(
        "app.services.variable_service.VariableService.on_farm_upload",
        lambda farm_id: {"new_columns_detected": ["treatment", "mob"], "initial_ingest": False},
    )

    files = {"file": ("KF_2023-01-01.csv", io.BytesIO(csv_content), "text/csv")}
    res = client.post("/api/admin/farms/KF/upload", files=files, data={"duplicate_mode": "skip"}, headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert "new_columns_detected" in data
    assert data["new_columns_detected"] == ["treatment", "mob"]
    assert data["initial_ingest"] is False


def test_choices_service_dynamic_fallback():
    """Verify ChoicesService dynamic lookup and graceful canonical fallback."""
    VariableService.ensure_schema()
    # 1. Fallback case: Farm with no farm_columns records
    execute("DELETE FROM farm_columns WHERE farm_id = 'FALLBACK_FARM'")
    choices = get_filter_choices("FALLBACK_FARM", is_admin=False)
    assert "active_filter_columns" in choices
    assert "active_measure_columns" in choices
    assert "dynamic_filters" in choices
    assert "sex" in choices["active_filter_columns"]
    assert "treatment" in choices["active_filter_columns"]
    assert "finalpweight" in choices["active_measure_columns"]

    # 2. Dynamic configured case: Custom columns configured
    configured_columns = [
        {"column_name": "breed", "kind": "categorical", "is_filter": True, "is_shown": False, "distinct_count": 3},
        {"column_name": "finalpweight", "kind": "numeric", "is_filter": False, "is_shown": True, "distinct_count": 10},
    ]
    VariableService.save_farm_columns("CONFIGURED_FARM", configured_columns)
    dyn_choices = get_filter_choices("CONFIGURED_FARM", is_admin=False)
    assert dyn_choices["active_filter_columns"] == ["breed"]
    assert dyn_choices["active_measure_columns"] == ["finalpweight"]
    assert "breed" in dyn_choices["dynamic_filters"]


def test_cold_start_empty_farm_suggests_weight_measure():
    """Verify that a farm with zero rows in animal_data still suggests at least one weight measure."""
    VariableService.ensure_schema()
    empty_farm = "TEST_EMPTY_COLD"
    try:
        execute("DELETE FROM farm_columns WHERE farm_id = %s", (empty_farm,))
        cols = VariableService.inspect_and_suggest_columns(empty_farm)
        # Verify that at least one weight measure is shown even with 0 animal rows
        shown_weight = [
            c for c in cols
            if c["is_shown"] and c["kind"] == "numeric" and c["column_name"] in REQUIRED_WEIGHT_MEASURES
        ]
        assert len(shown_weight) >= 1, "Cold-start farm must have at least one weight measure suggested"

        # Verify seed_farm_columns_if_missing succeeds without ValueError
        seeded = VariableService.seed_farm_columns_if_missing(empty_farm)
        assert seeded is True
        resp = VariableService.get_farm_columns_response(empty_farm)
        assert len(resp["columns"]) > 0
        assert resp["measures_count"] >= 1
    finally:
        try:
            execute("DELETE FROM farm_columns WHERE farm_id = %s", (empty_farm,))
        except Exception:
            pass
