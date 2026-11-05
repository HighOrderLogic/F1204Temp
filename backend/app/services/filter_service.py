from __future__ import annotations

import itertools
import time
from typing import Any

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore[assignment]

from app.constants import MONTH_NAMES
from app.models.schemas import FilterState
from app.services.active_window import resolve_active_window
from app.services.labels import (
    full_label_from_combo,
    label_from_combo,
    treatment_display,
)

# Warm serverless instances keep this across requests — avoids re-pulling ~12k rows
# from Supabase on every chart/summary call.
_FARM_DF_CACHE: dict[str, tuple[float, pd.DataFrame]] = {}
_FARM_DF_TTL_SEC = 180

_BASE_DATAFRAME_COLS = [
    "eid", "date", "breed", "treatment", "mob", "sex", "weight", "pweight", "growthpbs",
    "finalpweight", "finalgrowthpbs", "finaldailygrowth", "feedintakekgd", "feedintakepct",
    "methane", "animalvalue", "animalprod", "feedintakekgdsum", "finalgrowthpbssum",
    "animalprodsum", "methanesum", "methanesupplsum", "carcassweight", "dressedcarcass",
]


def invalidate_farm_dataframe(farm_id: str) -> None:
    """Drop a farm's cached dataframe after its records are mutated (upload/revert)."""
    _FARM_DF_CACHE.pop(farm_id, None)


class FilterService:
    @staticmethod
    def load_farm_dataframe(farm_id: str, *, use_cache: bool = True) -> pd.DataFrame:
        from app.db import fetch_all
        from app.services.variable_service import BUILTIN_FILTER_COLUMNS, VariableService

        # Enabled extra columns from uploads: filters cast to text so pandas filtering matches the
        # SQL path, and numeric measures beyond the canonical set.
        active = VariableService.get_active_columns(farm_id)
        extra_filters = [c for c in active["filters"] if c not in BUILTIN_FILTER_COLUMNS]
        extra_measures = [c for c in active["measures"] if c not in _BASE_DATAFRAME_COLS]
        extra_cols = extra_filters + extra_measures

        now = time.monotonic()
        if use_cache and farm_id in _FARM_DF_CACHE:
            cached_at, cached_df = _FARM_DF_CACHE[farm_id]
            # A frame cached before an admin enabled a column lacks it; reload instead.
            has_extras = cached_df.empty or all(c in cached_df.columns for c in extra_cols)
            if now - cached_at < _FARM_DF_TTL_SEC and has_extras:
                return cached_df

        extra_select = "".join(f', CAST("{c}" AS VARCHAR) AS "{c}"' for c in extra_filters)
        extra_select += "".join(f', "{c}"' for c in extra_measures)
        rows = fetch_all(
            f"""
            SELECT {", ".join(_BASE_DATAFRAME_COLS)}{extra_select}
            FROM animal_data WHERE farm_id = %s
            """,
            (farm_id,),
        )
        if not rows:
            df = pd.DataFrame()
        else:
            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"])
        if use_cache:
            _FARM_DF_CACHE[farm_id] = (now, df)
        return df

    resolve_active_window = staticmethod(resolve_active_window)

    @staticmethod
    def filter_data(df: pd.DataFrame, filters: FilterState, is_admin: bool) -> pd.DataFrame:
        if filters.year is None:
            raise ValueError("Year is required")
        if df.empty:
            return df

        out = df[df["date"].dt.year == filters.year].copy()

        if getattr(filters, "active_only", False) and not df.empty and "date" in df.columns and "eid" in df.columns:
            max_d = df["date"].max()
            window = FilterService.resolve_active_window(filters.farm_id, filters, max_date=max_d)
            if window:
                w_start, w_end = window
                w_start_ts = pd.Timestamp(w_start)
                w_end_ts = pd.Timestamp(w_end) + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
                in_window = (df["date"] >= w_start_ts) & (df["date"] <= w_end_ts)
                active_eids = set(df.loc[in_window, "eid"].dropna().unique())
                out = out[out["eid"].isin(active_eids)]

        if filters.month and filters.month != "All":
            month_num = MONTH_NAMES.index(filters.month)
            out = out[out["date"].dt.month == month_num]

        if filters.day and filters.day != "All":
            out = out[out["date"].dt.day == int(filters.day)]

        if filters.sex and "Overall" not in filters.sex:
            out = out[out["sex"].isin(filters.sex)]

        if filters.treatment and "Overall" not in filters.treatment:
            if "No Treatment" in filters.treatment:
                non_null = [t for t in filters.treatment if t != "No Treatment"]
                if non_null:
                    out = out[out["treatment"].isna() | out["treatment"].isin(non_null)]
                else:
                    out = out[out["treatment"].isna()]
            else:
                out = out[out["treatment"].isin(filters.treatment)]

        if filters.breed and "Overall" not in filters.breed:
            out = out[out["breed"].isin(filters.breed)]

        if filters.mob and "Overall" not in filters.mob:
            mob_strs = {str(v) for v in filters.mob}
            out = out[out["mob"].astype(str).isin(mob_strs) | out["mob"].isin(filters.mob)]

        if is_admin and filters.eid and "Overall" not in filters.eid:
            out = out[out["eid"].isin(filters.eid)]

        for col, selected in (filters.extra_filters or {}).items():
            if selected and col in out.columns:
                target_vals = (
                    [v for v in selected if v != "Overall"]
                    if ("Overall" in selected and any(v != "Overall" for v in selected))
                    else ([v for v in selected if v != "Overall"] if "Overall" not in selected else [])
                )
                if target_vals:
                    val_strs = {str(v) for v in target_vals}
                    out = out[out[col].astype(str).isin(val_strs) | out[col].isin(target_vals)]

        return out

    @staticmethod
    def _expand_dimension(selected: list[str], unique_values: list[str]) -> list[str]:
        selected = selected or ["Overall"]
        has_all = "Overall" in selected
        specifics = [v for v in selected if v != "Overall"]
        if has_all and not specifics:
            return ["Overall"]
        if has_all and specifics:
            return ["Overall"] + specifics
        if specifics:
            # One or more specific values selected (no Overall)
            return specifics
        return unique_values if unique_values else ["Overall"]

    @staticmethod
    def expand_filter_dimensions(
        base_df: pd.DataFrame, filters: FilterState, is_admin: bool
    ) -> dict[str, list[str]]:
        def unique_treatments(df: pd.DataFrame) -> list[str]:
            return sorted({treatment_display(t) for t in df["treatment"].unique()}) or ["No Treatment"]

        return {
            "sex": FilterService._expand_dimension(
                filters.sex, sorted(base_df["sex"].dropna().unique().tolist())
            ),
            "treatment": FilterService._expand_dimension(
                filters.treatment, unique_treatments(base_df)
            ),
            "breed": FilterService._expand_dimension(
                filters.breed, sorted(base_df["breed"].dropna().unique().tolist())
            ),
            "mob": FilterService._expand_dimension(
                filters.mob, sorted(base_df["mob"].dropna().unique().tolist())
            ),
            "eid": (
                FilterService._expand_dimension(
                    filters.eid, sorted(base_df["eid"].dropna().unique().tolist())
                )
                if is_admin
                else ["Overall"]
            ),
        }

    @staticmethod
    def build_grouped_data(
        base_df: pd.DataFrame, filters: FilterState, is_admin: bool
    ) -> pd.DataFrame:
        if base_df.empty:
            return base_df.copy()

        dims = FilterService.expand_filter_dimensions(base_df, filters, is_admin)
        sex_vals = dims["sex"]
        treatment_vals = dims["treatment"]
        breed_vals = dims["breed"]
        mob_vals = dims["mob"]
        eid_vals = dims["eid"]

        combo_dims = {
            "sex": sex_vals,
            "treatment": treatment_vals,
            "breed": breed_vals,
            "mob": mob_vals,
            "eid": eid_vals,
        }
        varying_dims = [d for d, vals in combo_dims.items() if len(vals) > 1]
        all_overall = all(vals == ["Overall"] for vals in combo_dims.values())

        # Fast path: single Overall group — no combo explosion / repeated copies.
        if all_overall:
            out = base_df.copy()
            out["treatment_display"] = out["treatment"].apply(treatment_display)
            combo = {
                "sex": "Overall",
                "treatment": "Overall",
                "breed": "Overall",
                "mob": "Overall",
                "eid": "Overall",
            }
            out["group"] = label_from_combo(combo, varying_dims, all_overall)
            out["full_group"] = full_label_from_combo(combo, is_admin)
            return out

        combos = list(itertools.product(sex_vals, treatment_vals, breed_vals, mob_vals, eid_vals))
        parts: list[pd.DataFrame] = []

        for sex_v, treat_v, breed_v, mob_v, eid_v in combos:
            subset = base_df
            if sex_v != "Overall":
                subset = subset[subset["sex"] == sex_v]
            if treat_v != "Overall":
                if treat_v == "No Treatment":
                    subset = subset[subset["treatment"].isna()]
                else:
                    subset = subset[subset["treatment"] == treat_v]
            if breed_v != "Overall":
                subset = subset[subset["breed"] == breed_v]
            if mob_v != "Overall":
                subset = subset[subset["mob"] == mob_v]
            if eid_v != "Overall":
                subset = subset[subset["eid"] == eid_v]
            if subset.empty:
                continue
            subset = subset.copy()
            subset["treatment_display"] = subset["treatment"].apply(treatment_display)
            combo = {
                "sex": sex_v,
                "treatment": treat_v,
                "breed": breed_v,
                "mob": mob_v,
                "eid": eid_v if is_admin else "Overall",
            }
            subset["group"] = label_from_combo(combo, varying_dims, all_overall)
            subset["full_group"] = full_label_from_combo(combo, is_admin)
            parts.append(subset)

        if not parts:
            return pd.DataFrame()
        return pd.concat(parts, ignore_index=True)

    @staticmethod
    def has_mixed_selection(filters: FilterState) -> bool:
        dims = [filters.sex, filters.treatment, filters.breed, filters.mob, filters.eid]
        if getattr(filters, "extra_filters", None):
            dims.extend(filters.extra_filters.values())
        for selected in dims:
            if selected and "Overall" in selected and any(v != "Overall" for v in selected):
                return True
        return False

    @staticmethod
    def df_to_records(df: pd.DataFrame) -> list[dict]:
        if df.empty:
            return []
        out = df.copy()
        if "date" in out.columns:
            out["date"] = out["date"].dt.strftime("%Y-%m-%d")
        # Replace NaN/NaT with None so FastAPI/json.dumps won't fail.
        records = out.astype(object).where(pd.notnull(out), None).to_dict(orient="records")
        cleaned: list[dict] = []
        for row in records:
            cleaned.append({
                k: (None if (isinstance(v, float) and (v != v or v in (float("inf"), float("-inf")))) else v)
                for k, v in row.items()
            })
        return cleaned
