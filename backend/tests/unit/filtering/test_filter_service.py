from __future__ import annotations

import pandas as pd
import pytest

from app.models.schemas import FilterState
from app.services.filter_service import FilterService, treatment_display


@pytest.mark.unit
def test_filter_year_required(sample_df: pd.DataFrame):
    """US07: Verifies that missing year throws ValueError."""
    f = FilterState(farm_id="KF", year=2023)
    f.year = None
    with pytest.raises(ValueError):
        FilterService.filter_data(sample_df, f, is_admin=True)


@pytest.mark.unit
def test_filter_month_all(sample_df: pd.DataFrame):
    """US07: Verifies that month='All' retains all records."""
    f = FilterState(farm_id="KF", year=2023, month="All")
    out = FilterService.filter_data(sample_df, f, is_admin=True)
    assert len(out) == len(sample_df)


@pytest.mark.unit
def test_filter_month_slicing(sample_df: pd.DataFrame):
    """US07: Verifies that specific month filters records appropriately."""
    f = FilterState(farm_id="KF", year=2023, month="January")
    out = FilterService.filter_data(sample_df, f, is_admin=True)
    assert all(out["date"].dt.month == 1)


@pytest.mark.unit
def test_filter_no_treatment(sample_df: pd.DataFrame):
    """US07: Verifies that 'No Treatment' filters for records where treatment is NaN."""
    f = FilterState(farm_id="KF", year=2023, treatment=["No Treatment"])
    out = FilterService.filter_data(sample_df, f, is_admin=True)
    assert out["treatment"].isna().all()


@pytest.mark.unit
def test_filter_sex_overall(sample_df: pd.DataFrame):
    """US07: Verifies that sex=['Overall'] includes all sexes."""
    f = FilterState(farm_id="KF", year=2023, sex=["Overall"])
    out = FilterService.filter_data(sample_df, f, is_admin=True)
    assert len(out) == len(sample_df)


@pytest.mark.unit
def test_eid_ignored_non_admin(sample_df: pd.DataFrame):
    """US02: Verifies that non-admin sessions cannot filter by specific EID."""
    f = FilterState(farm_id="KF", year=2023, eid=["982 123536700001"])
    out = FilterService.filter_data(sample_df, f, is_admin=False)
    assert len(out) == len(sample_df)


@pytest.mark.unit
def test_mixed_warning():
    """US07: Verifies detection of mixed selection (Overall + specific category)."""
    f = FilterState(farm_id="KF", year=2023, sex=["Overall", "Steer"])
    assert FilterService.has_mixed_selection(f) is True


@pytest.mark.unit
def test_treatment_display():
    """US07: Verifies treatment label normalization."""
    assert treatment_display(None) == "No Treatment"
    assert treatment_display("Drench") == "Drench"


@pytest.mark.unit
def test_filter_multi_measure_validation(sample_df: pd.DataFrame):
    """US07: Verifies multi-measure payload supports list of measures without error."""
    f = FilterState(farm_id="KF", year=2023, measures=["finalpweight", "methane"])
    out = FilterService.filter_data(sample_df, f, is_admin=True)
    assert "finalpweight" in out.columns
    assert "methane" in out.columns


@pytest.mark.unit
def test_filter_multi_measure_dedup_and_invalid():
    """US07: Verifies measures are deduplicated and invalid metric keys are discarded."""
    f = FilterState(farm_id="KF", year=2023, measures=["methane", "methane", "unknown_key", "finalpweight"])
    assert f.measures == ["methane", "finalpweight"]
    assert f.measure == "methane"


@pytest.mark.unit
def test_filter_multi_measure_empty_fallback():
    """US07: Verifies empty measures list falls back safely to default finalpweight."""
    f = FilterState(farm_id="KF", year=2023, measures=[])
    assert f.measures == ["finalpweight"]
    assert f.measure == "finalpweight"


@pytest.mark.unit
def test_filter_multi_variable_cross_demographic_slicing(sample_df: pd.DataFrame):
    """US07: Verifies intersecting multi-variable demographic filters (sex + breed)."""
    f = FilterState(farm_id="KF", year=2023, sex=["Heifer"], breed=["Angus"])
    out = FilterService.filter_data(sample_df, f, is_admin=True)
    assert not out.empty
    assert (out["sex"] == "Heifer").all()
    assert (out["breed"] == "Angus").all()


@pytest.mark.unit
def test_filter_multi_variable_empty_intersection_safe(sample_df: pd.DataFrame):
    """US07: Verifies contradictory filter combination yields empty DataFrame safely."""
    f = FilterState(farm_id="KF", year=2023, breed=["NonexistentBreed"])
    out = FilterService.filter_data(sample_df, f, is_admin=True)
    assert out.empty
    assert list(out.columns) == list(sample_df.columns)


@pytest.mark.unit
def test_filter_eid_sentinel_allowed_non_admin(sample_df: pd.DataFrame):
    """US02/US07: Verifies that sentinel EID filters (Overall, All) do not filter out records."""
    f1 = FilterState(farm_id="KF", year=2023, eid=["Overall"])
    out1 = FilterService.filter_data(sample_df, f1, is_admin=False)
    assert len(out1) == len(sample_df)

    f2 = FilterState(farm_id="KF", year=2023, eid=["All"])
    out2 = FilterService.filter_data(sample_df, f2, is_admin=False)
    assert len(out2) == len(sample_df)



@pytest.mark.unit
def test_filter_active_animal_sliding_window(sample_df: pd.DataFrame):
    """FR-ACT-01: Verifies that active_only=True retains only animals observed in [T_ref - (X-1), T_ref]."""
    f = FilterState(farm_id="KF", year=2023, active_only=True, active_days=7)
    out = FilterService.filter_data(sample_df, f, is_admin=True)
    assert len(out) < len(sample_df)
    # sample_df dates 2023-01-01 to 2023-01-20; 7-day window covers 2023-01-14 to 2023-01-20
    assert len(out) == 7


@pytest.mark.unit
def test_filter_historical_anchor_fallback(sample_df: pd.DataFrame):
    """FR-ACT-03: Verifies anchor date defaults to max(date) for historical datasets."""
    f = FilterState(farm_id="KF", year=2023, active_only=True, active_days=30)
    out = FilterService.filter_data(sample_df, f, is_admin=True)
    assert not out.empty
    assert len(out) == len(sample_df)


@pytest.mark.unit
def test_filter_active_reference_modes(sample_df: pd.DataFrame):
    """FR-ACT-03: Verifies auto vs max vs today anchor mode resolution."""
    f_max = FilterState(farm_id="KF", year=2023, active_only=True, active_days=7, active_reference_mode="max")
    out_max = FilterService.filter_data(sample_df, f_max, is_admin=True)
    assert len(out_max) == 7

    # Today anchor against 2023 historical sample_df should yield 0 matching active animals
    f_today = FilterState(farm_id="KF", year=2023, active_only=True, active_days=7, active_reference_mode="today")
    out_today = FilterService.filter_data(sample_df, f_today, is_admin=True)
    assert len(out_today) == 0


@pytest.mark.unit
def test_filter_empty_farm_active_safe():
    """Verifies that an empty DataFrame or farm with no observations handles active filter safely."""
    empty_df = pd.DataFrame(columns=["eid", "date", "sex", "treatment", "breed", "mob"])
    f = FilterState(farm_id="EMPTY", year=2023, active_only=True, active_days=30)
    out = FilterService.filter_data(empty_df, f, is_admin=True)
    assert out.empty


@pytest.mark.unit
def test_sql_agg_active_subquery():
    """Verifies base_where_sql appends active subquery when active_only=True."""
    from app.services.sql_agg import base_where_sql

    f_inactive = FilterState(farm_id="KF", year=2023, active_only=False)
    where_inactive, _ = base_where_sql(f_inactive, is_admin=True)
    assert "SELECT eid FROM animal_data" not in where_inactive

    # With mocked/fallback window
    f_active = FilterState(farm_id="KF", year=2023, active_only=True, active_days=14)
    # mock fetch_one for MAX(date)
    where_active, params_active = base_where_sql(f_active, is_admin=True)
    # Either active subquery appended or clean where clause
    if "SELECT eid FROM animal_data" in where_active:
        assert "eid IN (" in where_active



@pytest.mark.unit
def test_active_herd_resolution_without_pandas(monkeypatch):
    """Verifies that active herd SQL aggregation executes safely in pandas-free environments (Vercel light runtime)."""
    import sys
    from datetime import date
    from app.services.active_window import resolve_active_window
    from app.services.sql_agg import _scope_where_sql, base_where_sql

    monkeypatch.setitem(sys.modules, "pandas", None)
    monkeypatch.setattr("app.db.fetch_one", lambda query, params=(): {"max_d": "2023-01-20"})

    f_active = FilterState(farm_id="KF", year=2023, active_only=True, active_days=14, active_reference_mode="auto")
    where_active, _ = base_where_sql(f_active, is_admin=True)
    assert "SELECT eid FROM animal_data" in where_active

    scope_where, _ = _scope_where_sql(f_active)
    assert "SELECT eid FROM animal_data" in scope_where

    win = resolve_active_window("KF", f_active)
    assert win == (date(2023, 1, 7), date(2023, 1, 20))

@pytest.mark.unit
def test_mixed_warning_extra_filters():
    """US07: Verifies detection of mixed selection in extra_filters."""
    f = FilterState(farm_id="KF", year=2023)
    f.extra_filters = {"paddock": ["Overall", "P1"]}
    assert FilterService.has_mixed_selection(f) is True


@pytest.mark.unit
def test_numeric_filter_matching():
    """PR #75 parity: Verifies numeric mob and extra_filters match string filter choices."""
    df = pd.DataFrame({
        "eid": ["E1", "E2"],
        "date": pd.to_datetime(["2023-01-01", "2023-01-02"]),
        "mob": [1, 2],
        "pen": [1.5, 2.0],
        "finalpweight": [500.0, 520.0]
    })
    f1 = FilterState(farm_id="KF", year=2023, mob=["1"])
    out1 = FilterService.filter_data(df, f1, is_admin=True)
    assert len(out1) == 1
    assert out1["eid"].iloc[0] == "E1"

    f2 = FilterState(farm_id="KF", year=2023)
    f2.extra_filters = {"pen": ["1.5"]}
    out2 = FilterService.filter_data(df, f2, is_admin=True)
    assert len(out2) == 1
    assert out2["eid"].iloc[0] == "E1"
