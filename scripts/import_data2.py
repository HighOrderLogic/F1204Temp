#!/usr/bin/env python3
"""Import Data2.xlsx as farm WOW (WOW Feedlot Killara) for admin and owner accounts."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from psycopg2.extras import execute_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
DEFAULT_XLSX = PROJECT_ROOT / "Data2.xlsx"

sys.path.insert(0, str(BACKEND_ROOT))

from app.db import execute, fetch_all, fetch_one, get_conn

FARM_ID = "WOW"
FARM_NAME = "WOW Feedlot Killara"
FARM_SLUG = "wow-feedlot-killara"

# Spreadsheet headers that do not match the DB names after lowercasing.
COLUMN_ALIASES = {
    "eid": "eid",
    "date": "date",
    "breed": "breed",
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
    "feedintakekgdsum": "feedintakekgdsum",
    "finalgrowthpbssum": "finalgrowthpbssum",
    "animal_value": "animalvalue",
    "animalvalue": "animalvalue",
    "animal_prod": "animalprod",
    "animalprod": "animalprod",
}

NUMERIC_COLS = [
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


def _nullable_float(val):
    if val is None or (isinstance(val, float) and pd.isna(val)) or pd.isna(val):
        return None
    return float(val)


def load_rows(xlsx_path: Path) -> list[tuple]:
    df = pd.read_excel(xlsx_path, sheet_name=0)
    renamed = {}
    for col in df.columns:
        key = str(col).strip().lower().replace(" ", "")
        if key in COLUMN_ALIASES:
            renamed[col] = COLUMN_ALIASES[key]
    df = df.rename(columns=renamed)
    if "eid" not in df.columns or "date" not in df.columns:
        raise ValueError("Workbook must contain EID and date columns")

    df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce")
    df = df.dropna(subset=["eid", "date"])
    df["eid"] = df["eid"].astype(str).str.strip()
    df = df[df["eid"] != ""]
    df = df.drop_duplicates(subset=["eid", "date"], keep="last")

    rows: list[tuple] = []
    for rec in df.to_dict(orient="records"):
        values = [
            FARM_ID,
            str(rec["eid"]).strip(),
            rec["date"].date() if hasattr(rec["date"], "date") else rec["date"],
            rec.get("breed") if pd.notna(rec.get("breed")) else None,
            None,  # treatment is not in this workbook
            None,  # mob is not in this workbook
            rec.get("sex") if pd.notna(rec.get("sex")) else "Unknown",
        ]
        for col in NUMERIC_COLS:
            raw = rec.get(col)
            values.append(_nullable_float(raw) if raw is not None else None)
        rows.append(tuple(values))
    return rows


def ensure_farm_access() -> None:
    execute(
        """
        INSERT INTO farms (farm_id, farm_name, slug)
        VALUES (%s, %s, %s)
        ON CONFLICT (farm_id) DO UPDATE
          SET farm_name = EXCLUDED.farm_name, slug = EXCLUDED.slug, is_active = TRUE
        """,
        (FARM_ID, FARM_NAME, FARM_SLUG),
    )
    admins = fetch_all("SELECT id, username FROM users WHERE is_admin = TRUE AND is_active = TRUE")
    for user in admins:
        execute(
            "INSERT INTO user_farm_access (user_id, farm_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (user["id"], FARM_ID),
        )
    names = ", ".join(u["username"] for u in admins) or "(none)"
    print(f"Farm {FARM_ID} ({FARM_NAME}) granted to admin accounts: {names}")


def import_animals(xlsx_path: Path, force: bool = False) -> None:
    if not xlsx_path.exists():
        raise FileNotFoundError(f"Workbook not found: {xlsx_path}")
    print(f"Reading {xlsx_path.name}...", flush=True)
    rows = load_rows(xlsx_path)
    expected = len(rows)
    existing = fetch_one("SELECT COUNT(*) AS cnt FROM animal_data WHERE farm_id = %s", (FARM_ID,))
    current = existing["cnt"] if existing else 0
    if current == expected and not force:
        print(f"{FARM_ID} already has {current} rows — skipping", flush=True)
        return
    if current > 0:
        print(f"Replacing {current} existing rows with {expected}...", flush=True)
        execute("DELETE FROM animal_data WHERE farm_id = %s", (FARM_ID,))

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

    final = fetch_one("SELECT COUNT(*) AS cnt FROM animal_data WHERE farm_id = %s", (FARM_ID,))
    print(f"Seeded {final['cnt']} animal records for {FARM_ID}", flush=True)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Import Data2.xlsx as farm WOW")
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    ensure_farm_access()
    import_animals(args.xlsx, force=args.force)


if __name__ == "__main__":
    main()
