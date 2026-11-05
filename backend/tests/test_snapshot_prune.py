"""Tests for snapshot retention pruning and explicit snapshot deletion (Blindside 3)."""
import pytest
from fastapi.testclient import TestClient

from app.db import execute, fetch_all, fetch_one
from app.main import app
from app.services.farm_backup import (
    create_partition_snapshot,
    delete_partition_snapshot,
    list_farm_snapshots,
    prune_partition_snapshots,
)

client = TestClient(app)


def test_snapshot_pruning_and_deletion(admin_headers):
    farm_id = "KF"

    # Pre-clean existing pre_upload test snapshots
    execute("DELETE FROM animal_data_snapshots WHERE farm_id = %s AND snapshot_name LIKE 'pre_upload_test_%'", (farm_id,))

    # Create 5 automatic pre_upload snapshots (auto-pruning triggers during create)
    s1 = create_partition_snapshot(farm_id, "pre_upload_test_1")
    s2 = create_partition_snapshot(farm_id, "pre_upload_test_2")
    s3 = create_partition_snapshot(farm_id, "pre_upload_test_3")
    s4 = create_partition_snapshot(farm_id, "pre_upload_test_4")
    s5 = create_partition_snapshot(farm_id, "pre_upload_test_5")

    # Create 1 manual named snapshot (must never be auto-pruned)
    manual_snap = create_partition_snapshot(farm_id, "manual_golden_baseline")

    # Query active pre_upload snapshots count for farm_id
    rows = fetch_all(
        "SELECT id FROM animal_data_snapshots WHERE farm_id = %s AND snapshot_name LIKE 'pre_upload_%'",
        (farm_id,),
    )
    # Automated FIFO retention pruner keeps <= 3 pre_upload snapshots
    assert len(rows) <= 3

    # Prune explicitly with tighter limit max_pre_upload=1
    pruned = prune_partition_snapshots(farm_id, max_pre_upload=1)
    assert len(pruned) >= 1

    # Verify manual snapshot is preserved
    manual_row = fetch_one(
        "SELECT id FROM animal_data_snapshots WHERE farm_id = %s AND id = %s",
        (farm_id, manual_snap),
    )
    assert manual_row is not None

    # Test explicit DELETE endpoint on the manual snapshot
    r = client.delete(f"/api/admin/farms/{farm_id}/snapshots/{manual_snap}", headers=admin_headers)
    assert r.status_code == 200
    assert fetch_one("SELECT id FROM animal_data_snapshots WHERE id = %s", (manual_snap,)) is None

