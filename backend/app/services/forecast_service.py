"""Weight forecasts for the Prediction page: run the trained model over a farm's data and store them.

Runs inside the API, after every upload and when an admin asks for a refresh (app/routers/admin.py),
and from scripts/forecast_job.py. The forecasting code and the model live in ml/ (ml/forecasting.py).
The model runs through ml/tree_model.py, which needs only numpy: xgboost and scipy would not fit in
the Vercel function.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.config import PROJECT_ROOT
from app.db import FORECASTS_DDL, fetch_all, get_active_engine, get_conn

if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))  # ml/ sits next to backend/ and is deployed with it

from ml.forecasting import GrowthStats, forecast_farm, prepare  # noqa: E402
from ml.tree_model import MODEL_JSON, TreeModel  # noqa: E402

MODELS_DIR = PROJECT_ROOT / "ml" / "models"
TARGET = "finalpweight"
logger = logging.getLogger(__name__)
COLUMNS = [
    "farm_id", "eid", "target", "date", "pred", "horizon", "tier",
    "history_days", "data_through", "model_version", "validated_days", "generated_at",
]
REQUIRED_META = ("model_version", "fade_days")


class NoForecastData(ValueError):
    """The farm has no FinalPweight records, so there is nothing to forecast."""


@dataclass
class FarmForecast:
    data: pd.DataFrame  # the farm's weights, see ml.forecasting.prepare
    forecasts: pd.DataFrame  # long rows from ml.forecasting.forecast_farm
    stats: GrowthStats
    meta: dict  # ml/models/model_meta.json
    rows: pd.DataFrame  # the forecasts as rows of the `forecasts` table


def load_model(models_dir: Path = MODELS_DIR) -> tuple[TreeModel, dict]:
    """The model and its model_meta.json, whose `fade_days` is how far ahead the model was tested.

    Forecasts use the model for that many days, and the Prediction page marks them as tested, so
    a retrained model that was backtested further ahead brings its own range.
    """
    meta = json.loads((models_dir / "model_meta.json").read_text())
    missing = [key for key in REQUIRED_META if key not in meta]
    if missing:
        raise ValueError(f"{models_dir / 'model_meta.json'} is missing {', '.join(missing)}")
    return TreeModel.load(models_dir / MODEL_JSON), meta


def load_farm(farm_id: str) -> pd.DataFrame:
    rows = fetch_all(
        "SELECT eid, date, breed, finalpweight FROM animal_data WHERE farm_id = %s AND finalpweight IS NOT NULL",
        (farm_id,),
    )
    return prepare(pd.DataFrame(rows, columns=["eid", "date", "breed", "finalpweight"]))


def to_rows(forecasts: pd.DataFrame, farm_id: str, meta: dict) -> pd.DataFrame:
    rows = forecasts.assign(
        farm_id=farm_id,
        target=TARGET,
        model_version=meta["model_version"],
        validated_days=int(meta["fade_days"]),  # stored with the rows: the range of the model that made them
        generated_at=datetime.now(timezone.utc).replace(tzinfo=None),
        date=forecasts["date"].dt.date,
        data_through=forecasts["data_through"].dt.date,
        pred=forecasts["pred"].round(2),
    )
    return rows[COLUMNS]


def forecast(farm_id: str, models_dir: Path = MODELS_DIR) -> FarmForecast:
    """Forecast every animal on the farm from its last record, without writing anything."""
    model, meta = load_model(models_dir)
    data = load_farm(farm_id)
    if data.empty:
        raise NoForecastData(f"No FinalPweight records for farm {farm_id}; nothing to forecast.")
    forecasts, stats = forecast_farm(data, model, fade_days=int(meta["fade_days"]))
    return FarmForecast(data, forecasts, stats, meta, to_rows(forecasts, farm_id, meta))


def write_forecasts(farm_id: str, rows: pd.DataFrame) -> None:
    """Replace the farm's forecast rows in one transaction, creating the table on first use."""
    cols = ", ".join(COLUMNS)
    with get_conn() as conn:
        if get_active_engine() == "duckdb":
            cur = conn.cursor()  # its own DuckDB connection, so the transaction stays out of other requests
            cur.execute("BEGIN TRANSACTION")
            try:
                cur.execute(FORECASTS_DDL)
                cur.execute("DELETE FROM forecasts WHERE farm_id = ? AND target = ?", [farm_id, TARGET])
                cur.register("new_forecasts", rows)
                cur.execute(f"INSERT INTO forecasts ({cols}) SELECT {cols} FROM new_forecasts")
                cur.unregister("new_forecasts")
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
            return

        from psycopg2.extras import execute_values

        with conn.cursor() as cur:
            cur.execute(FORECASTS_DDL)
            cur.execute("DELETE FROM forecasts WHERE farm_id = %s AND target = %s", (farm_id, TARGET))
            values = [tuple(r) for r in rows.to_dict("split")["data"]]  # native Python types for psycopg2
            execute_values(cur, f"INSERT INTO forecasts ({cols}) VALUES %s", values, page_size=5000)


def refresh_farm(farm_id: str, models_dir: Path = MODELS_DIR) -> dict:
    """Recompute and store one farm's forecasts. Returns a summary for API responses."""
    started = time.perf_counter()
    try:
        result = forecast(farm_id, models_dir)
    except NoForecastData as exc:
        # Expected after reverts/uploads of data without final weights — not a failure.
        logger.info("No forecastable data for farm %s: %s", farm_id, exc)
        return {"status": "no_data", "detail": str(exc)}
    write_forecasts(farm_id, result.rows)
    return {
        "animals": int(result.forecasts["eid"].nunique()),
        "rows": len(result.rows),
        "data_through": result.data["date"].max().date().isoformat(),
        "model_version": result.meta["model_version"],
        "validated_days": int(result.meta["fade_days"]),
        "seconds": round(time.perf_counter() - started, 1),
    }
