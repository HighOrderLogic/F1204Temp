from __future__ import annotations

import contextlib
import json
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))  # repo root, for the top-level ml package

from app.db import _DuckDBConnectionWrapper  # noqa: E402
from app.services import forecast_service  # noqa: E402
from ml.forecasting import HORIZON_DAYS  # noqa: E402


class RecordingConnection:
    """Stands in for a psycopg2 connection and records the SQL it is given."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def cursor(self):
        return self

    def execute(self, sql: str, params=None) -> None:
        self.statements.append(" ".join(sql.split()))

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        pass


def forecast_rows(farm_id: str, pred: float) -> pd.DataFrame:
    row = [farm_id, "982 1", "finalpweight", date(2026, 9, 22), pred, 1, "full", 30, date(2026, 9, 21),
           "xgb-recursive-v2", 20, datetime(2026, 10, 20, 3, 0)]
    return pd.DataFrame([row], columns=forecast_service.COLUMNS)


def two_animals() -> list[dict]:
    """What fetch_all returns for a farm: one animal with 20 days, one with 3."""
    return [
        {"eid": eid, "date": d, "breed": "AA", "finalpweight": 400.0 + i}
        for eid, n in (("982 full", 20), ("982 short", 3))
        for i, d in enumerate(pd.date_range(end="2026-09-21", periods=n, freq="D").date)
    ]


def models_dir_with(tmp_path: Path, **meta_changes) -> Path:
    """A copy of ml/models whose model_meta.json has `meta_changes` applied (None removes a key)."""
    meta = json.loads((forecast_service.MODELS_DIR / "model_meta.json").read_text())
    for key, value in meta_changes.items():
        if value is None:
            meta.pop(key)
        else:
            meta[key] = value
    (tmp_path / "model_meta.json").write_text(json.dumps(meta))
    shutil.copy(forecast_service.MODELS_DIR / forecast_service.MODEL_JSON, tmp_path)
    return tmp_path


@pytest.fixture
def duck(monkeypatch):
    """An in-memory DuckDB standing in for the dev database."""
    raw = duckdb.connect()
    monkeypatch.setattr(forecast_service, "get_active_engine", lambda: "duckdb")
    monkeypatch.setattr(forecast_service, "get_conn", lambda: contextlib.nullcontext(_DuckDBConnectionWrapper(raw)))
    yield raw
    raw.close()


@pytest.mark.unit
def test_postgres_write_creates_the_table_before_replacing_rows(monkeypatch):
    """The first run on Supabase must not need anyone to create the forecasts table by hand."""
    conn = RecordingConnection()
    inserted = []
    monkeypatch.setattr(forecast_service, "get_active_engine", lambda: "postgres")
    monkeypatch.setattr(forecast_service, "get_conn", lambda: contextlib.nullcontext(conn))
    monkeypatch.setattr("psycopg2.extras.execute_values", lambda cur, sql, values, page_size: inserted.append(values))

    rows = forecast_rows("KF", 501.5)
    forecast_service.write_forecasts("KF", rows)

    assert conn.statements[0].startswith("CREATE TABLE IF NOT EXISTS forecasts")
    assert "ADD COLUMN IF NOT EXISTS validated_days" in conn.statements[0]  # upgrades an older table
    assert conn.statements[1].startswith("DELETE FROM forecasts WHERE farm_id")
    assert inserted == [[tuple(rows.iloc[0])]]


@pytest.mark.unit
def test_duckdb_write_replaces_only_that_farms_rows(duck):
    forecast_service.write_forecasts("KF", forecast_rows("KF", 500.0))
    forecast_service.write_forecasts("WOW", forecast_rows("WOW", 600.0))
    forecast_service.write_forecasts("KF", forecast_rows("KF", 501.0))
    assert duck.execute("SELECT farm_id, pred FROM forecasts ORDER BY farm_id").fetchall() == [
        ("KF", 501.0),
        ("WOW", 600.0),
    ]


@pytest.mark.unit
def test_write_upgrades_a_table_from_before_validated_days(duck):
    """A forecasts table created by an earlier version (e.g. on Supabase) gains the column on the next write."""
    duck.execute("""
        CREATE TABLE forecasts (
            farm_id VARCHAR(50) NOT NULL, eid VARCHAR(50) NOT NULL, target VARCHAR(50) NOT NULL, date DATE NOT NULL,
            pred DOUBLE PRECISION NOT NULL, horizon INTEGER NOT NULL, tier VARCHAR(10) NOT NULL,
            history_days INTEGER NOT NULL, data_through DATE NOT NULL, model_version VARCHAR(50) NOT NULL,
            generated_at TIMESTAMP NOT NULL, PRIMARY KEY (farm_id, eid, target, date)
        )
    """)
    duck.execute("INSERT INTO forecasts VALUES ('WOW', '982 9', 'finalpweight', '2026-09-22', 600, 1, 'full', 30, "
                 "'2026-09-21', 'xgb-recursive-v1', '2026-10-01')")

    forecast_service.write_forecasts("KF", forecast_rows("KF", 500.0))

    assert duck.execute("SELECT farm_id, validated_days FROM forecasts ORDER BY farm_id").fetchall() == [
        ("KF", 20),
        ("WOW", None),  # not refreshed yet: the Prediction page asks for a refresh instead of guessing
    ]


@pytest.mark.unit
def test_refresh_farm_forecasts_every_animal_with_the_committed_model(duck, monkeypatch):
    monkeypatch.setattr(forecast_service, "fetch_all", lambda sql, params: two_animals())

    summary = forecast_service.refresh_farm("KF")

    meta = json.loads((forecast_service.MODELS_DIR / "model_meta.json").read_text())
    assert summary["animals"] == 2
    assert summary["rows"] == 2 * HORIZON_DAYS
    assert summary["data_through"] == "2026-09-21"
    assert summary["model_version"] == meta["model_version"]
    assert summary["validated_days"] == meta["fade_days"]
    stored = duck.execute(
        "SELECT eid, tier, MIN(date), COUNT(*), MAX(pred) > 400 FROM forecasts GROUP BY eid, tier ORDER BY eid"
    ).fetchall()
    assert stored == [
        ("982 full", "full", date(2026, 9, 22), HORIZON_DAYS, True),
        ("982 short", "short", date(2026, 9, 22), HORIZON_DAYS, True),
    ]


@pytest.mark.unit
def test_tested_range_comes_from_the_model_metadata(duck, monkeypatch, tmp_path):
    """A model backtested further ahead brings its own range: no constant to edit in the app."""
    monkeypatch.setattr(forecast_service, "fetch_all", lambda sql, params: two_animals())
    used = []
    real_forecast_farm = forecast_service.forecast_farm

    def recording_forecast_farm(data, model, fade_days):
        used.append(fade_days)
        return real_forecast_farm(data, model, fade_days=fade_days)

    monkeypatch.setattr(forecast_service, "forecast_farm", recording_forecast_farm)

    summary = forecast_service.refresh_farm("KF", models_dir=models_dir_with(tmp_path, fade_days=35))

    assert used == [35]
    assert summary["validated_days"] == 35
    assert duck.execute("SELECT DISTINCT validated_days FROM forecasts").fetchall() == [(35,)]


@pytest.mark.unit
def test_metadata_without_a_tested_range_is_refused(tmp_path):
    with pytest.raises(ValueError, match="missing fade_days"):
        forecast_service.load_model(models_dir_with(tmp_path, fade_days=None))


@pytest.mark.unit
def test_farm_without_weights_is_reported(monkeypatch):
    monkeypatch.setattr(forecast_service, "fetch_all", lambda sql, params: [])
    with pytest.raises(forecast_service.NoForecastData, match="farm XX"):
        forecast_service.forecast("XX")
