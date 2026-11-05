"""Tests for cascading farm decommissioning (Blindside 1)."""
import pytest
from fastapi.testclient import TestClient

from app.db import execute, fetch_all, fetch_one
from app.main import app

client = TestClient(app)


def test_delete_kf_without_force_fails(admin_headers):
    r = client.delete("/api/admin/farms/KF", headers=admin_headers)
    assert r.status_code == 400
    assert "requires force=true" in r.json()["detail"]


def test_delete_nonexistent_farm_404(admin_headers):
    r = client.delete("/api/admin/farms/NONEXISTENT_FARM_XYZ", headers=admin_headers)
    assert r.status_code == 404


def test_cascading_farm_decommissioning(admin_headers):
    test_fid = "TEST_DECOM_FARM"

    # Pre-clean any leftover fixture state across tables
    for tbl in [
        "forecasts",
        "animal_data_snapshots_data",
        "animal_data_snapshots",
        "data_uploads",
        "animal_data",
        "farm_columns",
        "user_farm_access",
        "logo_assets",
        "email_schedules",
        "farms",
    ]:
        execute(f"DELETE FROM {tbl} WHERE farm_id = %s", (test_fid,))

    # Seed data across all tables for TEST_DECOM_FARM
    execute("INSERT INTO farms (farm_id, farm_name, slug) VALUES (%s, %s, %s)", (test_fid, "Test Farm", "test-farm"))
    execute(
        "INSERT INTO animal_data (farm_id, eid, date, weight) VALUES (%s, %s, '2026-01-01', 450.0)",
        (test_fid, "EID001"),
    )
    execute(
        "INSERT INTO data_uploads (farm_id, filename, rows_inserted) VALUES (%s, 'test.csv', 1)",
        (test_fid,),
    )
    execute(
        "INSERT INTO farm_columns (farm_id, column_name, kind, is_filter) VALUES (%s, 'weight', 'numeric', TRUE)",
        (test_fid,),
    )
    execute(
        "INSERT INTO forecasts (farm_id, eid, target, date, pred, horizon, tier, history_days, data_through, model_version, generated_at) VALUES (%s, 'EID001', 'weight', '2026-01-02', 452.0, 1, 'high', 10, '2026-01-01', 'v1', CURRENT_TIMESTAMP)",
        (test_fid,),
    )

    # Verify rows exist
    assert fetch_one("SELECT 1 FROM farms WHERE farm_id = %s", (test_fid,)) is not None
    assert fetch_one("SELECT 1 FROM animal_data WHERE farm_id = %s", (test_fid,)) is not None

    # Call DELETE endpoint
    r = client.delete(f"/api/admin/farms/{test_fid}", headers=admin_headers)
    assert r.status_code == 200
    assert "decommissioned successfully" in r.json()["detail"]

    # Assert 100% cascading cleanup
    assert fetch_one("SELECT 1 FROM farms WHERE farm_id = %s", (test_fid,)) is None
    assert fetch_one("SELECT 1 FROM animal_data WHERE farm_id = %s", (test_fid,)) is None
    assert fetch_one("SELECT 1 FROM data_uploads WHERE farm_id = %s", (test_fid,)) is None
    assert fetch_one("SELECT 1 FROM farm_columns WHERE farm_id = %s", (test_fid,)) is None
    assert fetch_one("SELECT 1 FROM forecasts WHERE farm_id = %s", (test_fid,)) is None

