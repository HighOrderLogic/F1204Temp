from __future__ import annotations

import datetime
import os
import numpy as np
import pandas as pd
import pytest

from app.services.anomaly_service import (
    ANOMALY_CACHE_SIZE,
    _ANOMALY_CACHE,
    anomaly_detail_series,
    anomaly_period,
    anomaly_reference,
    anomaly_source_note,
    classify_anomalies,
    clear_anomaly_cache,
    get_anomaly_scores,
    herd_shift_note,
    load_anomaly_weights,
    prepare_weights,
)
from app.services.filter_service import FilterService


@pytest.fixture(autouse=True)
def clean_cache():
    clear_anomaly_cache()
    yield
    clear_anomaly_cache()


def _make_farm_df(n: int = 15, days: int = 40) -> pd.DataFrame:
    dates = [datetime.date(2024, 3, 15) - datetime.timedelta(days=d) for d in range(days - 1, -1, -1)]
    records = []
    eids = [f"EID_{i:02d}" for i in range(1, n + 1)]
    for idx, eid in enumerate(eids):
        for day_idx, d in enumerate(dates):
            records.append({
                "eid": eid,
                "date": d,
                "weight": 400.0 + idx * 5.0 + day_idx * 0.8,
                "pweight": 399.0 + idx * 5.0 + day_idx * 0.8,
                "finalpweight": 400.0 + idx * 5.0 + day_idx * 0.8,
                "breed": "Angus" if idx % 2 == 0 else "Brahman",
                "treatment": "Drench" if idx % 3 == 0 else None,
                "mob": "Front",
                "sex": "Steer",
            })
    return pd.DataFrame(records)


# ============================================================
# Period Resolution Tests
# ============================================================

def test_anomaly_period_full_year():
    p = anomaly_period(2024, "All", "All")
    assert p is not None
    assert p["from"] == datetime.date(2024, 1, 1)
    assert p["to"] == datetime.date(2024, 12, 31)
    assert p["label"] == "2024"


def test_anomaly_period_month_leap_year():
    p = anomaly_period(2024, "February", "All")
    assert p is not None
    assert p["from"] == datetime.date(2024, 2, 1)
    assert p["to"] == datetime.date(2024, 2, 29)
    assert p["label"] == "February 2024"


def test_anomaly_period_valid_day():
    p = anomaly_period(2024, "February", 15)
    assert p is not None
    assert p["from"] == datetime.date(2024, 2, 15)
    assert p["to"] == datetime.date(2024, 2, 15)
    assert "15 February 2024" in p["label"]


def test_anomaly_period_invalid_day_falls_back_to_month():
    # Feb 31 does not exist -> falls back to full February
    p = anomaly_period(2024, "February", 31)
    assert p is not None
    assert p["from"] == datetime.date(2024, 2, 1)
    assert p["to"] == datetime.date(2024, 2, 29)


def test_anomaly_period_invalid_year_returns_none():
    assert anomaly_period(None) is None
    assert anomaly_period("invalid") is None


# ============================================================
# Weight Loading & Exclusivity Tests
# ============================================================

def test_load_anomaly_weights_rejects_non_weight_columns():
    df = _make_farm_df()
    with pytest.raises(ValueError, match="not an allowed weight source"):
        load_anomaly_weights(df, weight_col="finalpweight")
    with pytest.raises(ValueError, match="not an allowed weight source"):
        load_anomaly_weights(df, weight_col="methane")


def test_load_anomaly_weights_slices_28_day_window_across_month_boundary():
    df = _make_farm_df(n=5, days=40)
    period = anomaly_period(2024, "March", 10)
    loaded = load_anomaly_weights(df, period=period, weight_col="weight")
    assert loaded["available"] is True
    assert loaded["latest_date"] == datetime.date(2024, 3, 10)
    # 28 days ending on March 10 begins in February (Feb 12)
    assert loaded["data"]["date"].min() == datetime.date(2024, 2, 12)
    assert loaded["data"]["date"].max() == datetime.date(2024, 3, 10)


def test_load_anomaly_weights_reports_empty_period():
    df = _make_farm_df(n=5, days=10)
    period = anomaly_period(2020, "January", "All")
    loaded = load_anomaly_weights(df, period=period, weight_col="weight")
    assert loaded["available"] is False
    assert "no values in 'weight' in the selected period" in loaded["message"]


def test_load_anomaly_weights_reports_missing_columns():
    df = pd.DataFrame({"eid": ["A"], "something_else": [123]})
    loaded = load_anomaly_weights(df, weight_col="weight")
    assert loaded["available"] is False
    assert "missing" in loaded["message"].lower() or "no" in loaded["message"].lower()


# ============================================================
# Reference Scope & Fallback Tests
# ============================================================

def test_anomaly_reference_selected_scope_for_10_or_more_animals():
    df = _make_farm_df(n=15)
    selected = [f"EID_{i:02d}" for i in range(1, 12)]  # 11 animals
    ref = anomaly_reference(df, selected_eids=selected)
    assert ref["scope"] == "selected"
    assert ref["label"] == "all selected animals"
    assert len(ref["shown"]) == 11
    assert ref["data"]["eid"].nunique() == 11


def test_anomaly_reference_farm_scope_for_fewer_than_10_animals():
    df = _make_farm_df(n=15)
    selected = ["EID_01", "EID_02"]  # 2 animals
    ref = anomaly_reference(df, selected_eids=selected)
    assert ref["scope"] == "farm"
    assert ref["label"] == "whole farm"
    assert ref["shown"] == ["EID_01", "EID_02"]
    # Whole farm data is retained for calibration
    assert ref["data"]["eid"].nunique() == 15


def test_anomaly_reference_empty_selected_returns_farm_scope_with_empty_shown():
    df = _make_farm_df(n=15)
    ref = anomaly_reference(df, selected_eids=[])
    assert ref["scope"] == "farm"
    assert ref["shown"] == []
    assert ref["data"]["eid"].nunique() == 15


# ============================================================
# LRU Cache & get_anomaly_scores Tests
# ============================================================

def test_get_anomaly_scores_caches_results():
    df = _make_farm_df(n=15, days=30)
    period = anomaly_period(2024, "March", "All")

    res1 = get_anomaly_scores("FARM_A", df, period=period, weight_col="weight")
    assert len(_ANOMALY_CACHE) == 1

    # Second call hits cache
    res2 = get_anomaly_scores("FARM_A", df, period=period, weight_col="weight")
    assert len(_ANOMALY_CACHE) == 1
    assert res1["scores"] is res2["scores"]


def test_get_anomaly_scores_separates_weight_and_pweight_cache():
    df = _make_farm_df(n=15, days=30)
    period = anomaly_period(2024, "March", "All")

    get_anomaly_scores("FARM_A", df, period=period, weight_col="weight")
    get_anomaly_scores("FARM_A", df, period=period, weight_col="pweight")
    assert len(_ANOMALY_CACHE) == 2


def test_get_anomaly_scores_lru_eviction():
    df = _make_farm_df(n=15, days=30)
    # Generate 25 distinct entries
    for i in range(25):
        p = anomaly_period(2000 + i, "All", "All")
        df_copy = df.copy()
        df_copy["date"] = df_copy["date"].apply(lambda d: datetime.date(2000 + i, d.month, min(d.day, 28)))
        get_anomaly_scores(f"FARM_{i}", df_copy, period=p, weight_col="weight")

    assert len(_ANOMALY_CACHE) <= ANOMALY_CACHE_SIZE


# ============================================================
# Diagnostic Notes & Detail Series Tests
# ============================================================

def test_anomaly_source_note():
    # Observed weight with valid scores returns None
    scores = {"data": pd.DataFrame({"a": [1]}), "sigma": 5.0}
    assert anomaly_source_note("weight", scores) is None

    # Smoothed pweight returns warning note
    p_note = anomaly_source_note("pweight", scores)
    assert p_note is not None
    assert "pweight is smoothed" in p_note
    assert "Use observed weight for the calibrated analysis" in p_note

    # Missing sigma note
    no_sigma = {"data": pd.DataFrame({"a": [1]}), "sigma": None}
    ns_note = anomaly_source_note("weight", no_sigma)
    assert "too few readings to measure day-to-day variation" in ns_note


def test_herd_shift_note_detects_extreme_shift():
    dates = [datetime.date(2024, 3, 1) + datetime.timedelta(days=i) for i in range(28)]
    herd_effects = [0.0] * 28
    herd_effects[15] = -25.0  # Big collective drop
    herd_df = pd.DataFrame({"date": dates, "herd_effect": herd_effects, "n_animals": [20] * 28})

    scores = {"herd": herd_df, "sigma": 5.0}
    note = herd_shift_note(scores)
    assert note is not None
    assert "moved together by up to 25.0 kg" in note or "moved together by up to 24" in note or "moved together" in note


def test_herd_shift_note_returns_none_for_ordinary_noise():
    dates = [datetime.date(2024, 3, 1) + datetime.timedelta(days=i) for i in range(28)]
    herd_df = pd.DataFrame({"date": dates, "herd_effect": [1.0] * 28, "n_animals": [20] * 28})
    scores = {"herd": herd_df, "sigma": 5.0}
    assert herd_shift_note(scores) is None


def test_anomaly_detail_series_returns_complete_payload():
    df = _make_farm_df(n=15, days=30)
    period = anomaly_period(2024, "March", "All")
    res = get_anomaly_scores("FARM_A", df, period=period, weight_col="weight")
    scores = res["scores"]

    detail = anomaly_detail_series(scores, "EID_01", sensitivity="Medium")
    assert detail is not None
    assert detail["eid"] == "EID_01"
    assert "daily" in detail
    assert len(detail["daily"]) > 0
    assert "cusum_path" in detail
    assert "cusum_threshold" in detail
    assert "baseline_kg" in detail

    # Unknown animal returns None
    assert anomaly_detail_series(scores, "NON_EXISTENT") is None


# ============================================================
# DuckDB Real Database Parity Smoke Tests
# ============================================================

def test_duckdb_real_data_smoke_kf():
    from app.services.filter_service import FilterService
    df = FilterService.load_farm_dataframe("KF", use_cache=True)
    if df.empty or "weight" not in df.columns:
        pytest.skip("DuckDB KF animal_data not available or empty")

    active_year = int(df["date"].dt.year.max())
    period = anomaly_period(active_year, "All", "All")
    res = get_anomaly_scores("KF", df, period=period, weight_col="weight")
    if not res["loaded"]["available"]:
        pytest.skip("DuckDB KF data not usable for anomaly smoke test (no weight values)")
    scores = res["scores"]
    assert scores is not None

    cl = classify_anomalies(scores, res["peer"], sensitivity="Medium")
    assert len(cl) > 0
    assert set(cl["severity"].unique()).issubset({"High", "Medium", "Low", "Normal", "Not enough data"})


def test_duckdb_real_data_smoke_wow():
    from app.services.filter_service import FilterService
    df = FilterService.load_farm_dataframe("WOW", use_cache=True)
    if df.empty or "weight" not in df.columns:
        pytest.skip("DuckDB WOW animal_data not available or empty")

    active_year = int(df["date"].dt.year.max())
    period = anomaly_period(active_year, "All", "All")
    res = get_anomaly_scores("WOW", df, period=period, weight_col="pweight")
    assert res["loaded"]["available"] is True
    scores = res["scores"]
    assert scores is not None

    cl = classify_anomalies(scores, res["peer"], sensitivity="Medium")
    assert len(cl) > 0
