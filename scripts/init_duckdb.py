#!/usr/bin/env python3
"""Initialize and seed local embedded DuckDB database for offline development."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.auth.passwords import hash_password
from app.db import DUCKDB_UPLOAD_TABLES_DDL, FORECASTS_DDL

CSV_COLUMN_MAP = {
    "eid": "eid",
    "date": "date",
    "breed": "breed",
    "treatment": "treatment",
    "mob": "mob",
    "sex": "sex",
    "weight": "weight",
    "pweight": "pweight",
    "growthpbs": "growthpbs",
    "finalpweight": "finalpweight",
    "finalgrowthpbs": "finalgrowthpbs",
    "finaldailygrowth": "finaldailygrowth",
    "feedintakekgd": "feedintakekgd",
    "feedintakepct": "feedintakepct",
    "methane": "methane",
    "animalvalue": "animalvalue",
    "animalprod": "animalprod",
    "feedintakekgdsum": "feedintakekgdsum",
    "finalgrowthpbssum": "finalgrowthpbssum",
    "animalprodsum": "animalprodsum",
    "methanesum": "methanesum",
    "methanesupplsum": "methanesupplsum",
    "carcassweight": "carcassweight",
    "dressedcarcass": "dressedcarcass",
}

NUMERIC_COLS = [c for c in CSV_COLUMN_MAP.values() if c not in ("eid", "date", "breed", "treatment", "mob", "sex")]


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    col_map = {}
    for col in df.columns:
        key = col.strip().lower().replace(" ", "")
        for csv_key, db_col in CSV_COLUMN_MAP.items():
            if key == csv_key:
                col_map[col] = db_col
                break
    return df.rename(columns=col_map)


def create_schema(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE SEQUENCE IF NOT EXISTS users_id_seq START 1;
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY DEFAULT nextval('users_id_seq'),
            username VARCHAR(50) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            is_admin BOOLEAN DEFAULT FALSE,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS farms (
            farm_id VARCHAR(50) PRIMARY KEY,
            farm_name VARCHAR(100) NOT NULL,
            slug VARCHAR(100) UNIQUE,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS user_farm_access (
            user_id INTEGER,
            farm_id VARCHAR(50),
            PRIMARY KEY (user_id, farm_id)
        );

        CREATE TABLE IF NOT EXISTS animal_data (
            farm_id VARCHAR(50) NOT NULL,
            eid VARCHAR(50) NOT NULL,
            date DATE NOT NULL,
            breed VARCHAR(50),
            treatment VARCHAR(100),
            mob VARCHAR(50),
            sex VARCHAR(20) DEFAULT 'Unknown',
            weight DOUBLE,
            pweight DOUBLE,
            growthpbs DOUBLE,
            finalpweight DOUBLE,
            finalgrowthpbs DOUBLE,
            finaldailygrowth DOUBLE,
            feedintakekgd DOUBLE,
            feedintakepct DOUBLE,
            methane DOUBLE,
            animalvalue DOUBLE,
            animalprod DOUBLE,
            feedintakekgdsum DOUBLE,
            finalgrowthpbssum DOUBLE,
            animalprodsum DOUBLE,
            methanesum DOUBLE,
            methanesupplsum DOUBLE,
            carcassweight DOUBLE,
            dressedcarcass DOUBLE,
            PRIMARY KEY (farm_id, eid, date)
        );

        CREATE SEQUENCE IF NOT EXISTS email_schedules_id_seq START 1;
        CREATE TABLE IF NOT EXISTS email_schedules (
            id INTEGER PRIMARY KEY DEFAULT nextval('email_schedules_id_seq'),
            farm_id VARCHAR(50),
            recipient_email VARCHAR(255) NOT NULL,
            schedule_name VARCHAR(255),
            frequency VARCHAR(50) NOT NULL,
            send_time TIME NOT NULL,
            send_date DATE,
            day_of_week INTEGER,
            day_of_month INTEGER,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            next_send_at TIMESTAMP,
            last_sent TIMESTAMP,
            email_subject VARCHAR(500),
            email_body TEXT,
            report_filters TEXT,
            report_charts VARCHAR(500),
            report_format VARCHAR(50) DEFAULT 'pdf',
            created_by VARCHAR(100)
        );

        CREATE TABLE IF NOT EXISTS upload_snapshots (
            snapshot_id VARCHAR(100) PRIMARY KEY,
            farm_id VARCHAR(50) NOT NULL,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            original_filename VARCHAR(255) NOT NULL,
            row_count INTEGER NOT NULL,
            file_path VARCHAR(255)
        );
    """)
    conn.execute(FORECASTS_DDL)
    conn.execute(DUCKDB_UPLOAD_TABLES_DDL)


def seed_default_users(conn: duckdb.DuckDBPyConnection) -> None:
    users = [
        ("admin", "admin123", True),
        ("owner", "owner123", False),
        ("user", "user123", False),
    ]
    for username, password, is_admin in users:
        conn.execute(
            """
            INSERT INTO users (username, password_hash, is_admin)
            VALUES (?, ?, ?)
            ON CONFLICT (username) DO UPDATE SET password_hash = excluded.password_hash, is_admin = excluded.is_admin
            """,
            (username, hash_password(password), is_admin),
        )
    print("Seeded dev users: admin, owner, user")


def seed_default_farm(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        INSERT INTO farms (farm_id, farm_name, slug)
        VALUES ('KF', 'Killara Feedlot', 'killara-feedlot')
        ON CONFLICT (farm_id) DO NOTHING
    """)
    conn.execute("""
        INSERT INTO farms (farm_id, farm_name, slug)
        VALUES ('WOW', 'WOW Feedlot Killara', 'wow-feedlot-killara')
        ON CONFLICT (farm_id) DO NOTHING
    """)
    for username in ("admin", "owner", "user"):
        row = conn.execute("SELECT id, is_admin FROM users WHERE username = ?", (username,)).fetchone()
        if row:
            conn.execute(
                "INSERT INTO user_farm_access (user_id, farm_id) VALUES (?, 'KF') ON CONFLICT DO NOTHING",
                (row[0],),
            )
            if row[1]:  # is_admin
                conn.execute(
                    "INSERT INTO user_farm_access (user_id, farm_id) VALUES (?, 'WOW') ON CONFLICT DO NOTHING",
                    (row[0],),
                )
    print("Seeded dev farms KF (Killara Feedlot) and WOW (WOW Feedlot Killara)")


def ingest_csv(conn: duckdb.DuckDBPyConnection, csv_path: Path, farm_id: str = "KF") -> None:
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    print(f"Reading {csv_path}...")
    df = pd.read_csv(csv_path)
    df = normalize_columns(df)
    if "eid" not in df.columns or "date" not in df.columns:
        raise ValueError("CSV must contain EID and Date columns")

    df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce").dt.date
    df = df.dropna(subset=["eid", "date"])
    if "sex" not in df.columns:
        df["sex"] = "Unknown"
    if "treatment" in df.columns:
        df["treatment"] = df["treatment"].replace("", None)
    else:
        df["treatment"] = None

    df["farm_id"] = farm_id
    df["eid"] = df["eid"].astype(str).str.strip()

    # Ensure all expected columns exist
    for col in NUMERIC_COLS:
        if col not in df.columns:
            df[col] = None

    target_cols = [
        "farm_id", "eid", "date", "breed", "treatment", "mob", "sex",
        "weight", "pweight", "growthpbs", "finalpweight", "finalgrowthpbs",
        "finaldailygrowth", "feedintakekgd", "feedintakepct", "methane",
        "animalvalue", "animalprod", "feedintakekgdsum", "finalgrowthpbssum",
        "animalprodsum", "methanesum", "methanesupplsum", "carcassweight", "dressedcarcass",
    ]
    df_clean = df[target_cols]

    conn.register("incoming_csv_df", df_clean)
    conn.execute("""
        INSERT INTO animal_data
        SELECT * FROM incoming_csv_df
        ON CONFLICT (farm_id, eid, date) DO NOTHING
    """)
    conn.unregister("incoming_csv_df")

    count = conn.execute("SELECT COUNT(*) FROM animal_data WHERE farm_id = ?", (farm_id,)).fetchone()[0]
    print(f"Successfully ingested telemetry into animal_data (Total KF records: {count})")


def import_legacy_duckdb(conn: duckdb.DuckDBPyConnection, legacy_path: Path, farm_id: str = "KF") -> None:
    if not legacy_path.exists():
        raise FileNotFoundError(f"Legacy DuckDB file not found: {legacy_path}")

    print(f"Importing animal_data from legacy DuckDB: {legacy_path}...")
    escaped_path = str(legacy_path).replace("'", "''")
    conn.execute(f"ATTACH '{escaped_path}' AS legacy_db (READ_ONLY)")

    # Inspect columns in legacy database
    legacy_cols = [row[0] for row in conn.execute("DESCRIBE legacy_db.animal_data").fetchall()]
    print(f"Legacy animal_data columns: {legacy_cols}")

    if "farm_id" in legacy_cols:
        conn.execute("""
            INSERT INTO animal_data
            SELECT * FROM legacy_db.animal_data
            ON CONFLICT (farm_id, eid, date) DO NOTHING
        """)
    else:
        # Prepend default farm_id
        non_farm_cols = [c for c in legacy_cols if c != "farm_id"]
        col_list = ", ".join(non_farm_cols)
        conn.execute(f"""
            INSERT INTO animal_data (farm_id, {col_list})
            SELECT '{farm_id}', {col_list} FROM legacy_db.animal_data
            ON CONFLICT (farm_id, eid, date) DO NOTHING
        """)

    conn.execute("DETACH legacy_db")
    count = conn.execute("SELECT COUNT(*) FROM animal_data WHERE farm_id = ?", (farm_id,)).fetchone()[0]
    print(f"Successfully imported legacy records (Total KF records: {count})")


def main():
    parser = argparse.ArgumentParser(description="Initialize offline DuckDB database for local development")
    parser.add_argument("--csv", type=Path, default=None, help="Path to developer CSV dataset")
    parser.add_argument("--from-duckdb", type=Path, default=None, help="Path to legacy DuckDB file to import")
    parser.add_argument("--output", type=Path, default=BACKEND_ROOT / "data" / "dev.duckdb", help="Target DuckDB file")
    parser.add_argument("--force", action="store_true", help="Overwrite existing dev database")
    args = parser.parse_args()

    target_path = args.output.resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if target_path.exists() and args.force:
        print(f"Removing existing dev database: {target_path}")
        target_path.unlink()

    print(f"Connecting to DuckDB: {target_path}")
    conn = duckdb.connect(str(target_path))

    create_schema(conn)
    seed_default_users(conn)
    seed_default_farm(conn)

    if args.from_duckdb:
        import_legacy_duckdb(conn, args.from_duckdb.resolve())
    elif args.csv:
        ingest_csv(conn, args.csv.resolve())
    else:
        # Check if legacy data exists in parent workspace as a convenient default
        parent_legacy = PROJECT_ROOT.parent / "COMP3988_F12_04_P34" / "src" / "data.duckdb"
        parent_csv = PROJECT_ROOT.parent / "COMP3988_F12_04_P34" / "src" / "wowByrne_v2.csv"
        if parent_legacy.exists():
            print(f"Discovered reference dataset: {parent_legacy}")
            import_legacy_duckdb(conn, parent_legacy)
        elif parent_csv.exists():
            print(f"Discovered reference CSV: {parent_csv}")
            ingest_csv(conn, parent_csv)
        else:
            print("No initial dataset provided. Database initialized with schema and default credentials only.")
            print("To load data, run: python scripts/init_duckdb.py --csv path/to/your_data.csv")

    conn.close()
    print("Dev DuckDB initialization complete.")


if __name__ == "__main__":
    main()
