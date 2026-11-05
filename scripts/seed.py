#!/usr/bin/env python3
"""Seed Killara Feedlot (KF) users and animal data from Data.csv."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
DEFAULT_CSV = PROJECT_ROOT / "Data.csv"

sys.path.insert(0, str(BACKEND_ROOT))

from app.auth.passwords import hash_password
from app.db import execute, fetch_one, get_conn

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


def _nullable_float(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    return float(val)


def load_csv_rows(csv_path: Path, farm_id: str = "KF") -> list[tuple]:
    df = pd.read_csv(csv_path)
    df = normalize_columns(df)
    if "eid" not in df.columns or "date" not in df.columns:
        raise ValueError("CSV must contain EID and Date columns")

    df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["eid", "date"])
    if "sex" not in df.columns:
        df["sex"] = "Unknown"
    if "treatment" in df.columns:
        df["treatment"] = df["treatment"].replace("", None)
    else:
        df["treatment"] = None

    rows: list[tuple] = []
    for _, row in df.iterrows():
        values = [
            farm_id,
            str(row["eid"]).strip(),
            row["date"].date(),
            row.get("breed") if pd.notna(row.get("breed")) else None,
            row.get("treatment") if pd.notna(row.get("treatment")) else None,
            row.get("mob") if pd.notna(row.get("mob")) else None,
            row.get("sex") if pd.notna(row.get("sex")) else "Unknown",
        ]
        for col in NUMERIC_COLS:
            values.append(_nullable_float(row[col]) if col in row.index else None)
        rows.append(tuple(values))
    return rows


def seed_users():
    users = [
        ("admin", "admin123", True),
        ("owner", "owner123", False),
        ("user", "user123", False),
    ]
    for username, password, is_admin in users:
        execute(
            """
            INSERT INTO users (username, password_hash, is_admin)
            VALUES (%s, %s, %s)
            ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash, is_admin = EXCLUDED.is_admin
            """,
            (username, hash_password(password), is_admin),
        )
    print("Seeded users: admin, owner, user")


def seed_farm():
    execute(
        """
        INSERT INTO farms (farm_id, farm_name, slug) VALUES ('KF', 'Killara Feedlot', 'killara-feedlot')
        ON CONFLICT (farm_id) DO NOTHING
        """
    )
    execute(
        """
        INSERT INTO farms (farm_id, farm_name, slug) VALUES ('WOW', 'WOW Feedlot Killara', 'wow-feedlot-killara')
        ON CONFLICT (farm_id) DO NOTHING
        """
    )
    for username in ("admin", "owner", "user"):
        user = fetch_one("SELECT id, is_admin FROM users WHERE username = %s", (username,))
        if user:
            execute(
                "INSERT INTO user_farm_access (user_id, farm_id) VALUES (%s, 'KF') ON CONFLICT DO NOTHING",
                (user["id"],),
            )
            if user["is_admin"]:
                execute(
                    "INSERT INTO user_farm_access (user_id, farm_id) VALUES (%s, 'WOW') ON CONFLICT DO NOTHING",
                    (user["id"],),
                )
    print("Seeded farms KF (Killara Feedlot) and WOW (WOW Feedlot Killara)")


def seed_animals(csv_path: Path, force: bool = False):
    if not csv_path.exists():
        print(f"Data file not found at {csv_path} — skipping animal data seed.")
        return

    rows = load_csv_rows(csv_path)
    expected = len(rows)

    existing = fetch_one("SELECT COUNT(*) AS cnt FROM animal_data WHERE farm_id = 'KF'")
    current = existing["cnt"] if existing else 0

    if current == expected and not force:
        print(f"KF already has {current} rows (matches {csv_path.name}) — skipping", flush=True)
        return

    if current > 0:
        print(f"Replacing {current} existing rows with {expected} from {csv_path.name}...", flush=True)
        execute("DELETE FROM animal_data WHERE farm_id = 'KF'")

    insert_sql = """
        INSERT INTO animal_data (
            farm_id, eid, date, breed, treatment, mob, sex,
            weight, pweight, growthpbs, finalpweight, finalgrowthpbs,
            finaldailygrowth, feedintakekgd, feedintakepct, methane,
            animalvalue, animalprod, feedintakekgdsum, finalgrowthpbssum,
            animalprodsum, methanesum, methanesupplsum, carcassweight, dressedcarcass
        ) VALUES %s
        ON CONFLICT (farm_id, eid, date) DO NOTHING
    """
    batch_size = 1000
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        with get_conn() as conn:
            with conn.cursor() as cur:
                execute_values(cur, insert_sql, batch, page_size=len(batch))
        print(f"  Inserted {min(i + batch_size, len(rows))}/{len(rows)} rows...", flush=True)

    final = fetch_one("SELECT COUNT(*) AS cnt FROM animal_data WHERE farm_id = 'KF'")
    print(f"Seeded {final['cnt']} animal records for KF from {csv_path.name}", flush=True)


def seed_logos():
    """Seed default partner logos and baseline farm assets into logo_assets table."""
    from app.routers.farms import (
        ALLOWED_LOGO_EXTENSIONS,
        LOGO_CONTENT_TYPES,
        _ensure_logo_table,
        save_logo,
    )

    _ensure_logo_table()
    assets_root = PROJECT_ROOT / "frontend" / "assets" / "logos"
    if not assets_root.is_dir():
        print(f"Logos directory {assets_root} not found — skipping logo seeding.")
        return

    default_dir = assets_root / "default"
    if default_dir.is_dir():
        for p in default_dir.iterdir():
            if p.suffix.lower() in ALLOWED_LOGO_EXTENSIONS:
                save_logo("default", p.name, LOGO_CONTENT_TYPES[p.suffix.lower()], p.read_bytes())
        print("Seeded default partner logos (USYD, MLA)")

    for farm_dir in assets_root.iterdir():
        if farm_dir.is_dir() and farm_dir.name != "default":
            farm_id = farm_dir.name
            for p in farm_dir.iterdir():
                if p.suffix.lower() in ALLOWED_LOGO_EXTENSIONS:
                    save_logo(farm_id, p.name, LOGO_CONTENT_TYPES[p.suffix.lower()], p.read_bytes())
            print(f"Seeded baseline logos for farm {farm_id}")


def main():
    parser = argparse.ArgumentParser(description="Seed users and KF animal data")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Path to Data.csv")
    parser.add_argument("--force", action="store_true", help="Re-import even if row count matches")
    args = parser.parse_args()

    seed_users()
    seed_farm()
    seed_logos()
    seed_animals(args.csv, force=args.force)
    print("Seed complete.")


if __name__ == "__main__":
    main()
