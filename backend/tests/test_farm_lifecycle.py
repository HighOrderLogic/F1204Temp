from __future__ import annotations

import io
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.db import execute, fetch_all, fetch_one

DATA_DIR = Path(__file__).resolve().parent / "data"


@pytest.fixture(autouse=True)
def clean_test_farms():
    """Ensure test farms are cleaned before and after tests."""
    for fid in ("TESTFARM", "TESTEXCEL"):
        execute("DELETE FROM animal_data WHERE farm_id = %s", (fid,))
        execute("DELETE FROM animal_data_snapshots_data WHERE farm_id = %s", (fid,))
        execute("DELETE FROM animal_data_snapshots WHERE farm_id = %s", (fid,))
        execute("DELETE FROM data_uploads WHERE farm_id = %s", (fid,))
        execute("DELETE FROM farms WHERE farm_id = %s", (fid,))
    yield
    for fid in ("TESTFARM", "TESTEXCEL"):
        execute("DELETE FROM animal_data WHERE farm_id = %s", (fid,))
        execute("DELETE FROM animal_data_snapshots_data WHERE farm_id = %s", (fid,))
        execute("DELETE FROM animal_data_snapshots WHERE farm_id = %s", (fid,))
        execute("DELETE FROM data_uploads WHERE farm_id = %s", (fid,))
        execute("DELETE FROM farms WHERE farm_id = %s", (fid,))


def test_farm_creation_and_overview(client: TestClient, admin_headers: dict[str, str]):
    """Verify farm creation endpoint and overview summary list."""
    res = client.post(
        "/api/admin/farms",
        json={"farm_id": "TESTFARM", "farm_name": "Test Lifecycle Farm", "slug": "testfarm"},
        headers=admin_headers,
    )
    assert res.status_code == 200
    assert res.json()["farm_id"] == "TESTFARM"

    overview_res = client.get("/api/admin/farms/overview", headers=admin_headers)
    assert overview_res.status_code == 200
    farms = overview_res.json()
    farm_entry = next((f for f in farms if f["farm_id"] == "TESTFARM"), None)
    assert farm_entry is not None
    assert farm_entry["farm_name"] == "Test Lifecycle Farm"
    assert farm_entry["record_count"] == 0
    assert farm_entry["is_active"] is True


def test_vectorized_csv_ingestion(client: TestClient, admin_headers: dict[str, str]):
    """Verify 100-row real CSV dataset ingestion and snapshot generation."""
    client.post(
        "/api/admin/farms",
        json={"farm_id": "TESTFARM", "farm_name": "Test Farm", "slug": "testfarm"},
        headers=admin_headers,
    )

    csv_path = DATA_DIR / "test_wow_sample.csv"
    with open(csv_path, "rb") as f:
        file_bytes = f.read()

    files = {"file": ("TESTFARM_2026-06-23.csv", io.BytesIO(file_bytes), "text/csv")}
    data = {"duplicate_mode": "skip", "forecast": "false"}
    res = client.post("/api/admin/farms/TESTFARM/upload", files=files, data=data, headers=admin_headers)

    assert res.status_code == 200
    res_data = res.json()
    assert res_data["rows_inserted"] == 100
    assert res_data["rows_skipped"] == 0
    assert res_data["rows_overwritten"] == 0
    assert res_data["snapshot_id"] is not None

    count_row = fetch_one("SELECT COUNT(*) AS cnt FROM animal_data WHERE farm_id = 'TESTFARM'")
    assert count_row["cnt"] == 100


def test_vectorized_xlsx_ingestion(client: TestClient, admin_headers: dict[str, str]):
    """Verify .xlsx real data ingestion and auto-format detection."""
    client.post(
        "/api/admin/farms",
        json={"farm_id": "TESTEXCEL", "farm_name": "Excel Farm", "slug": "testexcel"},
        headers=admin_headers,
    )

    xlsx_path = DATA_DIR / "test_wow_sample.xlsx"
    with open(xlsx_path, "rb") as f:
        file_bytes = f.read()

    files = {
        "file": (
            "TESTEXCEL_2026-06-23.xlsx",
            io.BytesIO(file_bytes),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    data = {"duplicate_mode": "skip", "forecast": "false"}
    res = client.post("/api/admin/farms/TESTEXCEL/upload", files=files, data=data, headers=admin_headers)

    assert res.status_code == 200
    res_data = res.json()
    assert res_data["rows_inserted"] == 100
    assert res_data["rows_skipped"] == 0

    count_row = fetch_one("SELECT COUNT(*) AS cnt FROM animal_data WHERE farm_id = 'TESTEXCEL'")
    assert count_row["cnt"] == 100


def test_duplicate_modes_skip_and_overwrite(client: TestClient, admin_headers: dict[str, str]):
    """Verify duplicate skip and overwrite modes with modified weights."""
    client.post(
        "/api/admin/farms",
        json={"farm_id": "TESTFARM", "farm_name": "Test Farm", "slug": "testfarm"},
        headers=admin_headers,
    )

    # Initial 100 rows
    sample_csv = DATA_DIR / "test_wow_sample.csv"
    with open(sample_csv, "rb") as f:
        files = {"file": ("TESTFARM_2026-06-23.csv", io.BytesIO(f.read()), "text/csv")}
        client.post("/api/admin/farms/TESTFARM/upload", files=files, data={"forecast": "false"}, headers=admin_headers)

    # 1. Skip mode with 50 duplicate rows
    dup_csv = DATA_DIR / "test_wow_duplicates.csv"
    with open(dup_csv, "rb") as f:
        files = {"file": ("TESTFARM_2026-06-23.csv", io.BytesIO(f.read()), "text/csv")}
        res_skip = client.post(
            "/api/admin/farms/TESTFARM/upload",
            files=files,
            data={"duplicate_mode": "skip", "forecast": "false"},
            headers=admin_headers,
        )
    assert res_skip.status_code == 200
    data_skip = res_skip.json()
    assert data_skip["rows_skipped"] == 50
    assert data_skip["rows_inserted"] == 0
    assert data_skip["rows_overwritten"] == 0

    # 2. Overwrite mode with 50 duplicate rows
    with open(dup_csv, "rb") as f:
        files = {"file": ("TESTFARM_2026-06-23.csv", io.BytesIO(f.read()), "text/csv")}
        res_ow = client.post(
            "/api/admin/farms/TESTFARM/upload",
            files=files,
            data={"duplicate_mode": "overwrite", "forecast": "false"},
            headers=admin_headers,
        )
    assert res_ow.status_code == 200
    data_ow = res_ow.json()
    assert data_ow["rows_overwritten"] == 50
    assert data_ow["rows_inserted"] == 0
    assert data_ow["rows_skipped"] == 0


def test_invalid_upload_rejections(client: TestClient, admin_headers: dict[str, str]):
    """Verify rejection of files missing mandatory fields or invalid filenames."""
    # 1. Invalid filename pattern
    files = {"file": ("invalid_name.csv", io.BytesIO(b"EID,Date,weight\n1,01/01/2023,500\n"), "text/csv")}
    res = client.post("/api/admin/farms/TESTFARM/upload", files=files, headers=admin_headers)
    assert res.status_code == 400
    assert "Filename must match" in res.json()["detail"]

    # 2. Missing weight measure
    no_weight_path = DATA_DIR / "test_invalid_no_weight.csv"
    with open(no_weight_path, "rb") as f:
        files = {"file": ("TESTFARM_2026-06-23.csv", io.BytesIO(f.read()), "text/csv")}
        res = client.post("/api/admin/farms/TESTFARM/upload", files=files, headers=admin_headers)
    assert res.status_code == 400
    assert "weight" in res.json()["detail"].lower()

    # 3. Missing EID
    no_eid_path = DATA_DIR / "test_invalid_missing_eid.csv"
    with open(no_eid_path, "rb") as f:
        files = {"file": ("TESTFARM_2026-06-23.csv", io.BytesIO(f.read()), "text/csv")}
        res = client.post("/api/admin/farms/TESTFARM/upload", files=files, headers=admin_headers)
    assert res.status_code == 400
    assert "eid" in res.json()["detail"].lower()


def test_snapshots_lifecycle_and_revert(client: TestClient, admin_headers: dict[str, str], monkeypatch):
    """Verify snapshot capture on upload, snapshots listing, and atomic partition rollback."""
    client.post(
        "/api/admin/farms",
        json={"farm_id": "TESTFARM", "farm_name": "Test Farm", "slug": "testfarm"},
        headers=admin_headers,
    )

    # Ingest 100 rows
    sample_csv = DATA_DIR / "test_wow_sample.csv"
    with open(sample_csv, "rb") as f:
        files = {"file": ("TESTFARM_2026-06-23.csv", io.BytesIO(f.read()), "text/csv")}
        client.post("/api/admin/farms/TESTFARM/upload", files=files, data={"forecast": "false"}, headers=admin_headers)

    # Ingest overwrite to create a second snapshot
    dup_csv = DATA_DIR / "test_wow_duplicates.csv"
    with open(dup_csv, "rb") as f:
        files = {"file": ("TESTFARM_2026-06-23.csv", io.BytesIO(f.read()), "text/csv")}
        client.post(
            "/api/admin/farms/TESTFARM/upload",
            files=files,
            data={"duplicate_mode": "overwrite", "forecast": "false"},
            headers=admin_headers,
        )

    # List snapshots
    snap_res = client.get("/api/admin/farms/TESTFARM/snapshots", headers=admin_headers)
    assert snap_res.status_code == 200
    snapshots = snap_res.json()
    assert len(snapshots) >= 2

    # The snapshot before the second upload had 100 records
    target_snapshot = next(s for s in snapshots if s["record_count"] == 100)
    target_id = target_snapshot["id"]

    # Revert partition back to target snapshot
    monkeypatch.setattr("app.routers.admin.refresh_forecasts", lambda farm_id: {"status": "skipped"})
    revert_res = client.post(f"/api/admin/farms/TESTFARM/snapshots/{target_id}/revert", headers=admin_headers)
    assert revert_res.status_code == 200
    revert_data = revert_res.json()
    assert revert_data["status"] == "success"
    assert revert_data["reverted_to_snapshot_id"] == target_id
    assert revert_data["restored_record_count"] == 100

    # Verify animal_data has 100 records
    count_row = fetch_one("SELECT COUNT(*) AS cnt FROM animal_data WHERE farm_id = 'TESTFARM'")
    assert count_row["cnt"] == 100



def test_upload_invalidates_server_farm_caches(client: TestClient, admin_headers: dict[str, str]):
    """Cached pre-upload dataframe must not be served after ingestion (TTL 180s)."""
    from app.services.filter_service import FilterService

    client.post(
        "/api/admin/farms",
        json={"farm_id": "TESTFARM", "farm_name": "Test Farm", "slug": "testfarm"},
        headers=admin_headers,
    )
    assert len(FilterService.load_farm_dataframe("TESTFARM", use_cache=True)) == 0

    with open(DATA_DIR / "test_wow_sample.csv", "rb") as f:
        file_bytes = f.read()
    files = {"file": ("TESTFARM_2026-06-23.csv", io.BytesIO(file_bytes), "text/csv")}
    res = client.post(
        "/api/admin/farms/TESTFARM/upload",
        files=files,
        data={"duplicate_mode": "skip", "forecast": "false"},
        headers=admin_headers,
    )
    assert res.status_code == 200
    assert len(FilterService.load_farm_dataframe("TESTFARM", use_cache=True)) == 100
