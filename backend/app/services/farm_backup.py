from __future__ import annotations

import logging
from typing import Any

from app.db import execute, execute_returning, fetch_all, fetch_one, get_active_engine, get_conn, table_column_types

logger = logging.getLogger(__name__)

ANIMAL_DATA_SNAPSHOT_COLS = [
    "farm_id",
    "eid",
    "date",
    "breed",
    "treatment",
    "mob",
    "sex",
    "weight",
    "pweight",
    "growthpbs",
    "finalpweight",
    "finalgrowthpbs",
    "finaldailygrowth",
    "feedintakekgd",
    "feedintakepct",
    "methane",
    "animalvalue",
    "animalprod",
    "feedintakekgdsum",
    "finalgrowthpbssum",
    "animalprodsum",
    "methanesum",
    "methanesupplsum",
    "carcassweight",
    "dressedcarcass",
]

SNAPSHOT_TABLES_DDL = """
    CREATE SEQUENCE IF NOT EXISTS animal_data_snapshots_id_seq START 1;
    CREATE TABLE IF NOT EXISTS animal_data_snapshots (
        id BIGINT PRIMARY KEY DEFAULT nextval('animal_data_snapshots_id_seq'),
        farm_id VARCHAR(50) NOT NULL,
        snapshot_name VARCHAR NOT NULL,
        record_count INTEGER NOT NULL,
        storage_path VARCHAR,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        created_by VARCHAR
    );
    CREATE TABLE IF NOT EXISTS animal_data_snapshots_data (
        snapshot_id BIGINT NOT NULL,
        farm_id VARCHAR(50) NOT NULL,
        eid VARCHAR(50) NOT NULL,
        date DATE NOT NULL,
        breed VARCHAR(50),
        treatment VARCHAR(100),
        mob VARCHAR(50),
        sex VARCHAR(20),
        weight DOUBLE PRECISION,
        pweight DOUBLE PRECISION,
        growthpbs DOUBLE PRECISION,
        finalpweight DOUBLE PRECISION,
        finalgrowthpbs DOUBLE PRECISION,
        finaldailygrowth DOUBLE PRECISION,
        feedintakekgd DOUBLE PRECISION,
        feedintakepct DOUBLE PRECISION,
        methane DOUBLE PRECISION,
        animalvalue DOUBLE PRECISION,
        animalprod DOUBLE PRECISION,
        feedintakekgdsum DOUBLE PRECISION,
        finalgrowthpbssum DOUBLE PRECISION,
        animalprodsum DOUBLE PRECISION,
        methanesum DOUBLE PRECISION,
        methanesupplsum DOUBLE PRECISION,
        carcassweight DOUBLE PRECISION,
        dressedcarcass DOUBLE PRECISION
    );
    CREATE INDEX IF NOT EXISTS idx_snapshots_data_id ON animal_data_snapshots_data (snapshot_id);
"""


def ensure_snapshot_tables() -> None:
    """Ensure in-database snapshot metadata and partitioned data tables exist."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(SNAPSHOT_TABLES_DDL)
    except Exception as exc:
        logger.warning("Could not verify snapshot tables: %s", exc)


MAX_SNAPSHOTS_PER_FARM = 3


def delete_partition_snapshot(farm_id: str, snapshot_id: int) -> bool:
    """Atomically delete snapshot data rows and metadata record."""
    ensure_snapshot_tables()
    execute("DELETE FROM animal_data_snapshots_data WHERE snapshot_id = %s", (snapshot_id,))
    execute("DELETE FROM animal_data_snapshots WHERE id = %s AND farm_id = %s", (snapshot_id, farm_id))
    return True


def prune_partition_snapshots(farm_id: str, max_pre_upload: int = MAX_SNAPSHOTS_PER_FARM) -> list[int]:
    """Prune oldest pre_upload_* snapshots beyond max_pre_upload while preserving manual snapshots."""
    ensure_snapshot_tables()
    rows = fetch_all(
        """
        SELECT id, snapshot_name
        FROM animal_data_snapshots
        WHERE farm_id = %s AND snapshot_name LIKE 'pre_upload_%'
        ORDER BY created_at DESC, id DESC
        """,
        (farm_id,),
    )
    pruned_ids: list[int] = []
    if len(rows) > max_pre_upload:
        excess = rows[max_pre_upload:]
        for r in excess:
            snap_id = int(r["id"])
            delete_partition_snapshot(farm_id, snap_id)
            pruned_ids.append(snap_id)
        logger.info("Pruned %d excess pre_upload snapshots for farm %s: %s", len(pruned_ids), farm_id, pruned_ids)
    return pruned_ids


def snapshot_columns() -> list[str]:
    """Columns copied into and out of snapshots: the canonical set plus any extra columns uploads
    added (farm_ingestion.sync_extra_columns adds them to both tables)."""
    try:
        live = {r["column_name"] for r in table_column_types("animal_data")}
        stored = {r["column_name"] for r in table_column_types("animal_data_snapshots_data")}
    except Exception as exc:
        logger.warning("Could not read snapshot columns, using the canonical set: %s", exc)
        return list(ANIMAL_DATA_SNAPSHOT_COLS)
    extras = sorted(c for c in live & stored if c not in ANIMAL_DATA_SNAPSHOT_COLS and c != "snapshot_id")
    return ANIMAL_DATA_SNAPSHOT_COLS + extras
def create_partition_snapshot(farm_id: str, snapshot_name: str, created_by: str | None = None) -> int:
    """Atomically record farm partition metadata and copy all rows to snapshot storage."""
    ensure_snapshot_tables()
    count_row = fetch_one("SELECT COUNT(*) AS cnt FROM animal_data WHERE farm_id = %s", (farm_id,))
    count = int(count_row["cnt"]) if count_row and count_row.get("cnt") is not None else 0

    snap = execute_returning(
        """
        INSERT INTO animal_data_snapshots (farm_id, snapshot_name, record_count, created_by)
        VALUES (%s, %s, %s, %s) RETURNING id
        """,
        (farm_id, snapshot_name, count, created_by or "system"),
    )
    if snap and "id" in snap:
        snapshot_id = int(snap["id"])
    else:
        row = fetch_one("SELECT MAX(id) as id FROM animal_data_snapshots WHERE farm_id = %s", (farm_id,))
        snapshot_id = int(row["id"]) if row and row.get("id") else 1

    if count > 0:
        cols_str = ", ".join(f'"{c}"' for c in snapshot_columns())
        execute(
            f"""
            INSERT INTO animal_data_snapshots_data (snapshot_id, {cols_str})
            SELECT %s, {cols_str}
            FROM animal_data
            WHERE farm_id = %s
            """,
            (snapshot_id, farm_id),
        )

    if snapshot_name.startswith("pre_upload_"):
        try:
            prune_partition_snapshots(farm_id, MAX_SNAPSHOTS_PER_FARM)
        except Exception as p_err:
            logger.warning("Could not prune excess snapshots for farm %s: %s", farm_id, p_err)
    return snapshot_id


def list_farm_snapshots(farm_id: str) -> list[dict[str, Any]]:
    """Retrieve chronologically ordered snapshots for a specific farm partition."""
    ensure_snapshot_tables()
    rows = fetch_all(
        """
        SELECT id, farm_id, snapshot_name, record_count, storage_path, created_at, created_by
        FROM animal_data_snapshots
        WHERE farm_id = %s
        ORDER BY created_at DESC, id DESC
        """,
        (farm_id,),
    )
    for r in rows:
        if r.get("created_at") and hasattr(r["created_at"], "isoformat"):
            r["created_at"] = r["created_at"].isoformat()
    return rows


def restore_partition_snapshot(farm_id: str, snapshot_id: int, reverted_by: str | None = None) -> dict[str, Any]:
    """Atomically restore animal_data partition to target snapshot after taking safety snapshot."""
    ensure_snapshot_tables()
    snap = fetch_one(
        "SELECT id, snapshot_name, record_count FROM animal_data_snapshots WHERE farm_id = %s AND id = %s",
        (farm_id, snapshot_id),
    )
    if not snap:
        raise ValueError(f"Snapshot #{snapshot_id} not found for farm {farm_id}")

    # Safety snapshot of current partition state prior to revert
    safety_snap_id = create_partition_snapshot(
        farm_id=farm_id,
        snapshot_name=f"pre_revert_safety_{snapshot_id}",
        created_by=reverted_by or "system",
    )

    cols_str = ", ".join(f'"{c}"' for c in snapshot_columns())
    engine = get_active_engine()

    with get_conn() as conn:
        cur = conn.cursor()
        if engine == "duckdb":
            cur.execute("BEGIN TRANSACTION")
        try:
            cur.execute("DELETE FROM animal_data WHERE farm_id = %s", (farm_id,))
            cur.execute(
                f"""
                INSERT INTO animal_data ({cols_str})
                SELECT {cols_str}
                FROM animal_data_snapshots_data
                WHERE snapshot_id = %s
                """,
                (snapshot_id,),
            )
            if engine == "duckdb":
                cur.execute("COMMIT")
        except Exception:
            if engine == "duckdb":
                cur.execute("ROLLBACK")
            raise

    count_row = fetch_one("SELECT COUNT(*) AS cnt FROM animal_data WHERE farm_id = %s", (farm_id,))
    restored_count = int(count_row["cnt"]) if count_row and count_row.get("cnt") is not None else 0

    return {
        "status": "success",
        "farm_id": farm_id,
        "reverted_to_snapshot_id": snapshot_id,
        "safety_snapshot_id": safety_snap_id,
        "restored_record_count": restored_count,
    }

