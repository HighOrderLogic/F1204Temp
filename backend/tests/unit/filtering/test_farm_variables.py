"""Admin-configured filters and measures (farm_columns) applied to every FilterState."""
from __future__ import annotations

import pandas as pd
import pytest

from app.db import execute
from app.models.schemas import FilterState
from app.services.filter_service import FilterService
from app.services.sql_agg import _extra_filters_key, _scope_where_sql, _validate_measure, base_where_sql
from app.services.variable_service import VariableService, invalidate_active_columns

SAVE_FARM = "VARS_TEST"


@pytest.fixture
def farm_config(monkeypatch):
    """Pretend the farm's admin enabled exactly these columns."""

    def configure(filters: list[str], measures: list[str]) -> None:
        active = {"filters": filters, "measures": measures}
        monkeypatch.setattr(VariableService, "get_active_columns", classmethod(lambda cls, farm_id: active))

    return configure


@pytest.fixture
def saved_farm():
    yield SAVE_FARM
    execute("DELETE FROM farm_columns WHERE farm_id = %s", (SAVE_FARM,))
    invalidate_active_columns()


@pytest.mark.unit
def test_measures_limited_to_enabled_columns(farm_config):
    """Any enabled numeric column is a measure (not just the original seven); disabled ones are dropped."""
    farm_config(["sex"], ["methane", "pweight", "weight"])
    f = FilterState(farm_id="KF", year=2023, measures=["methane", "finalpweight", "pweight"])
    assert f.measures == ["methane", "pweight"]
    assert f.measure == "methane"


@pytest.mark.unit
def test_disabled_measure_falls_back_to_farm_default(farm_config):
    """With finalpweight disabled, the default is the farm's first enabled weight measure."""
    farm_config(["sex"], ["methane", "weight"])
    assert FilterState(farm_id="KF", year=2023, measure="finalpweight").measures == ["weight"]
    assert FilterState(farm_id="KF", year=2023, measures=[]).measure == "weight"


@pytest.mark.unit
def test_default_measure_preference():
    assert VariableService.default_measure(["methane", "finalpweight", "weight"]) == "finalpweight"
    assert VariableService.default_measure(["pweight", "weight"]) == "weight"
    assert VariableService.default_measure(["methane", "pweight"]) == "pweight"
    assert VariableService.default_measure(["methane"]) == "methane"


@pytest.mark.unit
def test_disabled_builtin_filter_resets_to_overall(farm_config):
    farm_config(["sex"], ["finalpweight"])
    f = FilterState(farm_id="KF", year=2023, sex=["Steer"], mob=["Front"], breed=["Angus"])
    assert f.sex == ["Steer"]
    assert f.mob == ["Overall"]
    assert f.breed == ["Overall"]


@pytest.mark.unit
def test_extra_filters_keep_only_enabled_columns(farm_config):
    farm_config(["sex", "paddock"], ["finalpweight"])
    f = FilterState(
        farm_id="KF",
        year=2023,
        extra_filters={"paddock": ["P1"], "pen": ["A"], "sex": ["Steer"], 'x"; DROP TABLE y; --': ["1"]},
    )
    assert f.extra_filters == {"paddock": ["P1"]}


@pytest.mark.unit
def test_extra_filter_narrows_sql_paths(farm_config):
    farm_config(["sex", "paddock"], ["finalpweight"])
    f = FilterState(farm_id="KF", year=2023, extra_filters={"paddock": ["P1", "P2"]})

    where, params = base_where_sql(f, is_admin=True)
    assert 'CAST("paddock" AS VARCHAR) = ANY(%s)' in where
    assert ["P1", "P2"] in params

    # The timeseries grain is filtered client-side by the built-in dims only, so extras apply here.
    scope_where, scope_params = _scope_where_sql(f)
    assert 'CAST("paddock" AS VARCHAR) = ANY(%s)' in scope_where
    assert ["P1", "P2"] in scope_params


@pytest.mark.unit
def test_extra_filter_overall_is_a_no_op(farm_config):
    farm_config(["sex", "paddock"], ["finalpweight"])
    f = FilterState(farm_id="KF", year=2023, extra_filters={"paddock": ["Overall", "P1"]})
    where, _ = base_where_sql(f, is_admin=True)
    assert "paddock" not in where
    assert _extra_filters_key(f) == ()


@pytest.mark.unit
def test_grain_cache_key_distinguishes_extra_filters(farm_config):
    farm_config(["sex", "paddock"], ["finalpweight"])
    p1 = FilterState(farm_id="KF", year=2023, extra_filters={"paddock": ["P1"]})
    p2 = FilterState(farm_id="KF", year=2023, extra_filters={"paddock": ["P2"]})
    assert _extra_filters_key(p1) != _extra_filters_key(p2)


@pytest.mark.unit
def test_extra_filter_narrows_pandas_path(farm_config, sample_df: pd.DataFrame):
    farm_config(["sex", "paddock"], ["finalpweight"])
    df = sample_df.assign(paddock=["P1" if i < 5 else "P2" for i in range(len(sample_df))])
    f = FilterState(farm_id="KF", year=2023, extra_filters={"paddock": ["P1"]})
    out = FilterService.filter_data(df, f, is_admin=True)
    assert len(out) == 5
    assert (out["paddock"] == "P1").all()


@pytest.mark.unit
def test_validate_measure_guards_sql_interpolation():
    assert _validate_measure("pweight") == "pweight"
    with pytest.raises(ValueError):
        _validate_measure("finalpweight); DROP TABLE animal_data; --")


@pytest.mark.unit
def test_save_rejects_unknown_columns(saved_farm):
    with pytest.raises(ValueError, match="Unknown animal_data column"):
        VariableService.save_farm_columns(
            saved_farm,
            [
                {"column_name": "finalpweight", "kind": "numeric", "is_shown": True},
                {"column_name": "no_such_column", "kind": "categorical", "is_filter": True},
            ],
        )


@pytest.mark.unit
def test_save_takes_kind_from_schema_and_refreshes_active_columns(saved_farm):
    # Cache the defaults first: saving must invalidate them.
    assert "methane" in VariableService.get_active_columns(saved_farm)["measures"]

    VariableService.save_farm_columns(
        saved_farm,
        [
            {"column_name": "weight", "kind": "numeric", "is_shown": True},
            # A client claiming a text column is numeric can't make it a measure.
            {"column_name": "breed", "kind": "numeric", "is_shown": True, "is_filter": True},
            {"column_name": "sex", "kind": "categorical", "is_filter": False},
        ],
    )
    active = VariableService.get_active_columns(saved_farm)
    assert active["measures"] == ["weight"]
    assert active["filters"] == ["breed"]
