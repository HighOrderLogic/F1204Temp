#!/usr/bin/env python3
"""Write 182-day FinalPweight forecasts for one farm into the `forecasts` table.

The API already does this after every upload, and on POST /api/admin/farms/<farm_id>/forecasts/refresh
(python scripts/admin.py forecast). This script runs the same code
(backend/app/services/forecast_service.py) directly against whichever database backend/.env selects
(Supabase or the dev DuckDB): use it to preview a farm's forecasts (--dry-run), to try a model in
another folder (--models-dir), or to backfill a database without going through the site. It creates the
`forecasts` table if it does not exist yet, so the database user needs permission to create tables.

Usage:
    python scripts/forecast_job.py --farm KF --dry-run   # summary and samples, writes nothing
    python scripts/forecast_job.py --farm KF
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import get_active_engine  # noqa: E402
from app.services.forecast_service import (  # noqa: E402
    MODELS_DIR,
    FarmForecast,
    NoForecastData,
    forecast,
    write_forecasts,
)
from ml.forecasting import HORIZON_DAYS, TIER_FULL, TIER_SHORT  # noqa: E402


def print_summary(farm_id: str, result: FarmForecast) -> None:
    data, forecasts, stats, meta = result.data, result.forecasts, result.stats, result.meta
    animals = forecasts.drop_duplicates("eid")
    tiers = animals["tier"].value_counts()
    print(f"Farm {farm_id}: {data.eid.nunique()} animals with FinalPweight, "
          f"{data.date.min():%Y-%m-%d} .. {data.date.max():%Y-%m-%d}")
    print(f"  complete 14-day history: {tiers.get(TIER_FULL, 0)} animals")
    print(f"  short history (fewer days): {tiers.get(TIER_SHORT, 0)} animals")
    curve = ", ".join(f"{w:.0f} kg {g:+.2f}" for w, g in zip(stats.weights, stats.gains))
    print(f"  typical daily gain by weight (kg/day): {curve}")
    print(f"  daily change clipped to [{stats.clip[0]:+.2f}, {stats.clip[1]:+.2f}] kg/day")
    print(f"  model {meta['model_version']} (trained on {meta['training_data']}), {HORIZON_DAYS} days per animal")
    for tier in (TIER_FULL, TIER_SHORT):
        sample = forecasts[forecasts.tier == tier]
        if sample.empty:
            continue
        one = sample[sample.eid == sample.eid.iloc[0]].set_index("horizon")
        last = data.loc[data.eid == one.eid.iloc[0], "weight"].iloc[-1]
        picks = ", ".join(f"day {h}: {one.loc[h, 'pred']:.1f}" for h in (1, 30, 90, HORIZON_DAYS))
        print(f"  sample {tier}: {one.eid.iloc[0]} last {last:.1f} kg on "
              f"{one.data_through.iloc[0]:%Y-%m-%d} -> {picks}")


def explain_error(exc: BaseException) -> str | None:
    """A plain-language fix for errors admins are likely to hit, or None."""
    text = str(exc)
    if "already open" in text or "being used by another process" in text or "Could not set lock" in text:
        return (
            "The dev DuckDB file is open in another process, usually the running app server: DuckDB allows "
            "only one process to write to it. Either refresh through that server instead "
            "(python scripts/admin.py forecast --farm <farm_id> ...), or stop it, run this job, and start "
            "it again. (Supabase does not have this limit.)"
        )
    return None


def describe_database() -> str:
    """Which database the job will use, e.g. 'postgres (aws-0-ap-southeast-2.pooler.supabase.com)'."""
    settings = get_settings()
    if get_active_engine() == "duckdb":
        return f"duckdb ({settings.resolved_duckdb_path})"
    return f"postgres ({urlparse(settings.database_url).hostname})"


def run(farm_id: str, dry_run: bool = False, models_dir: Path = MODELS_DIR) -> int:
    """Forecast one farm and, unless dry_run, replace its rows. Returns the number of forecast rows."""
    t0 = time.time()
    print(f"Database: {describe_database()}")
    try:
        result = forecast(farm_id, models_dir)
    except NoForecastData as exc:
        raise SystemExit(str(exc)) from exc
    print_summary(farm_id, result)

    rows = result.rows
    if dry_run:
        print(f"Dry run: would replace farm {farm_id}'s forecasts with {len(rows)} rows ({time.time() - t0:.1f}s).")
        return len(rows)
    write_forecasts(farm_id, rows)
    print(f"Wrote {len(rows)} forecast rows for farm {farm_id} ({time.time() - t0:.1f}s).")
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--farm", required=True, help="farm_id, e.g. KF")
    ap.add_argument("--dry-run", action="store_true", help="print the summary without writing")
    ap.add_argument("--models-dir", type=Path, default=MODELS_DIR,
                    help="folder with xgb_weight.json and model_meta.json")
    args = ap.parse_args()
    try:
        run(args.farm, args.dry_run, args.models_dir)
    except Exception as exc:
        fix = explain_error(exc)
        if fix is None:
            raise
        sys.exit(f"{fix}\nOriginal error: {str(exc).splitlines()[0]}")


if __name__ == "__main__":
    main()
