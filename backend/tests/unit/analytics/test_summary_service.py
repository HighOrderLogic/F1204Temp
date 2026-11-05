from __future__ import annotations

import pandas as pd
import pytest

from app.services.summary_service import SummaryService, kpi_block, subset_by_window


@pytest.mark.unit
def test_kpi_block_empty():
    """US08: Verifies that empty series returns safe 'No data' payload without division error."""
    df = pd.DataFrame({"finalpweight": []})
    result = kpi_block(df, "finalpweight")
    assert result["display"] == "No data"
    assert result["count"] == 0


@pytest.mark.unit
def test_kpi_block_values(sample_df: pd.DataFrame):
    """US08: Verifies accurate statistical aggregation over valid values."""
    result = kpi_block(sample_df, "finalpweight")
    assert result["count"] == len(sample_df)
    assert result["mean"] > 0
    assert result["min"] <= result["median"] <= result["max"]


@pytest.mark.unit
def test_subset_by_window_last_day(sample_df: pd.DataFrame):
    """US08: Verifies 1-day window extracts only latest date."""
    out = subset_by_window(sample_df, 1)
    assert len(out) == 1
    assert out["date"].max() == sample_df["date"].max()


@pytest.mark.unit
def test_subset_by_window_15_days(sample_df: pd.DataFrame):
    """US08: Verifies 15-day window extraction."""
    out = subset_by_window(sample_df, 15)
    assert len(out) == 15


@pytest.mark.unit
def test_compute_stats_windows(sample_df: pd.DataFrame):
    """US08: Verifies compute_stats returns last_day, last_15_days, last_month, and overall."""
    df = sample_df.copy()
    df["full_group"] = "Overall Average"
    stats = SummaryService.compute_stats(df, "finalpweight")
    assert len(stats) >= 1
    windows = stats[0]["windows"]
    assert "last_day" in windows
    assert "last_15_days" in windows
    assert "last_month" in windows
    assert "overall" in windows


@pytest.mark.unit
def test_summary_stats_invariant_under_pseudonymization(sample_df: pd.DataFrame):
    """US02/US08: Verifies statistical KPIs are strictly invariant under pseudonymization."""
    from app.utils.anonymize import anonymize_records

    df_raw = sample_df.copy()
    df_raw["full_group"] = "Overall Average"
    stats_raw = SummaryService.compute_stats(df_raw, "finalpweight")

    records = df_raw.to_dict(orient="records")
    anon_records = anonymize_records(records, is_admin=False)
    df_anon = pd.DataFrame(anon_records)
    stats_anon = SummaryService.compute_stats(df_anon, "finalpweight")

    assert stats_raw == stats_anon


@pytest.mark.unit
def test_distinct_animal_count_parity_under_pseudonymization(sample_df: pd.DataFrame):
    """US02/US09: Verifies n_distinct(eid) parity between raw and pseudonymized datasets."""
    from app.utils.anonymize import anonymize_records

    records = sample_df.to_dict(orient="records")
    anon_records = anonymize_records(records, is_admin=False)
    df_anon = pd.DataFrame(anon_records)

    raw_distinct = sample_df["eid"].nunique(dropna=True)
    anon_distinct = df_anon["eid"].nunique(dropna=True)
    assert raw_distinct == anon_distinct
    assert (df_anon["eid"] != sample_df["eid"]).all()

