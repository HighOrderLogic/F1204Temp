from __future__ import annotations

import itertools
from datetime import date, datetime, timedelta
from typing import Any

from app.constants import MEASURE_UNITS, MONTH_NAMES
from app.models.schemas import FilterState
from app.services.labels import (
    format_measure_display,
    friendly_label,
    full_label_from_combo,
    label_from_combo,
    treatment_display,
)
from app.services.variable_service import SAFE_COLUMN_NAME


def _validate_measure(measure: str) -> str:
    # FilterState already limited measures to the farm's enabled columns; this guards the
    # f-string interpolation for callers that pass a measure separately. SQL quotes the name,
    # since an uploaded column can be called e.g. "order".
    if not isinstance(measure, str) or not SAFE_COLUMN_NAME.match(measure):
        raise ValueError(f"Invalid measure: {measure}")
    return measure


def _apply_extra_filters(filters: FilterState, clauses: list[str], params: list[Any]) -> None:
    """Narrow by admin-enabled extra categorical columns (keys were validated by FilterState)."""
    for col, selected in sorted((filters.extra_filters or {}).items()):
        specifics = [str(v) for v in selected or [] if v != "Overall"]
        if "Overall" in (selected or []) or not specifics:
            continue
        clauses.append(f'CAST("{col}" AS VARCHAR) = ANY(%s)')
        params.append(specifics)


def expand_dims_from_filters(filters: FilterState, is_admin: bool) -> dict[str, list[str]]:
    def expand(selected: list[str] | None) -> list[str]:
        selected = selected or ["Overall"]
        has_all = "Overall" in selected
        specifics = [v for v in selected if v != "Overall"]
        if has_all and not specifics:
            return ["Overall"]
        if has_all and specifics:
            return ["Overall"] + specifics
        if specifics:
            return specifics
        return ["Overall"]

    return {
        "sex": expand(filters.sex),
        "treatment": expand(filters.treatment),
        "breed": expand(filters.breed),
        "mob": expand(filters.mob),
        "eid": expand(filters.eid) if is_admin else ["Overall"],
    }


def base_where_sql(filters: FilterState, is_admin: bool) -> tuple[str, list[Any]]:
    clauses = ["farm_id = %s", "date >= %s::date", "date < %s::date"]
    params: list[Any] = [
        filters.farm_id,
        f"{filters.year}-01-01",
        f"{filters.year + 1}-01-01",
    ]

    if filters.month and filters.month != "All":
        clauses.append("EXTRACT(MONTH FROM date)::int = %s")
        params.append(MONTH_NAMES.index(filters.month))

    if filters.day and filters.day != "All":
        clauses.append("EXTRACT(DAY FROM date)::int = %s")
        params.append(int(filters.day))

    def apply_dim(col: str, selected: list[str] | None, *, allow_null_token: str | None = None) -> None:
        selected = selected or ["Overall"]
        if "Overall" in selected:
            return
        specifics = [v for v in selected if v != "Overall"]
        if not specifics:
            return
        if allow_null_token and allow_null_token in specifics:
            others = [v for v in specifics if v != allow_null_token]
            if others:
                clauses.append(f"({col} IS NULL OR {col} = ANY(%s))")
                params.append(others)
            else:
                clauses.append(f"{col} IS NULL")
        else:
            clauses.append(f"{col} = ANY(%s)")
            params.append(specifics)

    apply_dim("sex", filters.sex)
    apply_dim("treatment", filters.treatment, allow_null_token="No Treatment")
    apply_dim("breed", filters.breed)
    apply_dim("mob", filters.mob)
    if is_admin:
        apply_dim("eid", filters.eid)
    _apply_extra_filters(filters, clauses, params)

    if getattr(filters, "active_only", False):
        from app.services.active_window import resolve_active_window

        window = resolve_active_window(filters.farm_id, filters)
        if window:
            w_start, w_end = window
            clauses.append(
                "eid IN (SELECT eid FROM animal_data WHERE farm_id = %s AND date >= %s::date AND date <= %s::date AND eid IS NOT NULL)"
            )
            params.extend([filters.farm_id, w_start.isoformat(), w_end.isoformat()])

    return " AND ".join(clauses), params


def _active_group_cols(dims: dict[str, list[str]]) -> list[str]:
    cols: list[str] = []
    for dim, vals in dims.items():
        if vals == ["Overall"]:
            continue
        if "Overall" in vals or len(vals) > 1:
            cols.append(dim)
    return cols


def _combo_match(row: dict, combo: dict, group_cols: list[str] | None = None) -> bool:
    """Match a grain row to a filter combo.

    Dimensions collapsed in SQL (constant via WHERE) are not re-checked against
    placeholder literals like sex='Overall'.
    """
    grouped = set(group_cols or ("sex", "treatment", "breed", "mob", "eid"))

    if combo["sex"] != "Overall" and "sex" in grouped and row["sex"] != combo["sex"]:
        return False
    if combo["treatment"] != "Overall" and "treatment" in grouped:
        treat = treatment_display(None if row["treatment"] == "__NONE__" else row["treatment"])
        if treat != combo["treatment"]:
            return False
    if combo["breed"] != "Overall" and "breed" in grouped and row["breed"] != combo["breed"]:
        return False
    if combo["mob"] != "Overall" and "mob" in grouped and row["mob"] != combo["mob"]:
        return False
    if combo["eid"] != "Overall" and "eid" in grouped and row.get("eid") != combo["eid"]:
        return False
    return True


def _as_date(val: Any) -> date:
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return date.fromisoformat(str(val)[:10])


def _need_eid(filters: FilterState, is_admin: bool) -> bool:
    return is_admin and any(v != "Overall" for v in (filters.eid or ["Overall"]))


def _fetch_daily_grain(filters: FilterState, is_admin: bool, measure: str) -> list[dict]:
    from app.db import fetch_all

    measure = _validate_measure(measure)
    where, params = base_where_sql(filters, is_admin)
    dims = expand_dims_from_filters(filters, is_admin)
    group_cols = _active_group_cols(dims)

    def select_expr(col: str) -> str:
        if col in group_cols:
            if col == "treatment":
                return "COALESCE(treatment, '__NONE__') AS treatment"
            return col
        if col == "treatment":
            return "'__NONE__'::text AS treatment"
        return f"'Overall'::text AS {col}"

    select_parts = ["date"] + [select_expr(c) for c in ("sex", "treatment", "breed", "mob", "eid")]
    if group_cols:
        group_sql = ", ".join(
            ["date"]
            + ["COALESCE(treatment, '__NONE__')" if c == "treatment" else c for c in group_cols]
        )
    else:
        group_sql = "date"

    return fetch_all(
        f"""
        SELECT
          {", ".join(select_parts)},
          AVG("{measure}")::float AS value,
          COUNT(*)::int AS count,
          MIN("{measure}")::float AS min_v,
          MAX("{measure}")::float AS max_v
        FROM animal_data
        WHERE {where} AND "{measure}" IS NOT NULL
        GROUP BY {group_sql}
        ORDER BY date
        """,
        tuple(params),
    )


def _smooth_series(series: list[dict]) -> list[dict]:
    by_group: dict[str, list[dict]] = {}
    for row in series:
        by_group.setdefault(row["group"], []).append(row)
    extra: list[dict] = []
    for group, pts in by_group.items():
        pts = sorted(pts, key=lambda r: r["date"])
        if len(pts) < 3:
            continue
        window = max(3, min(15, len(pts) // 5 or 3))
        values = [p["value"] for p in pts]
        for i, p in enumerate(pts):
            lo = max(0, i - window // 2)
            hi = min(len(values), i + window // 2 + 1)
            chunk = values[lo:hi]
            extra.append(
                {
                    "date": p["date"],
                    "group": f"{group} (trend)",
                    "value": round(sum(chunk) / len(chunk), 2),
                    "count": p["count"],
                }
            )
    return series + extra


def _series_for_combos(
    grain: list[dict], filters: FilterState, is_admin: bool
) -> tuple[list[dict], dict, str | None]:
    dims = expand_dims_from_filters(filters, is_admin)
    group_cols = _active_group_cols(dims)
    combos = list(
        itertools.product(dims["sex"], dims["treatment"], dims["breed"], dims["mob"], dims["eid"])
    )
    varying = [d for d, vals in dims.items() if len(vals) > 1]
    all_overall = all(vals == ["Overall"] for vals in dims.values())

    series: list[dict] = []
    present: list[str] = []
    missing: list[str] = []

    for sex_v, treat_v, breed_v, mob_v, eid_v in combos:
        combo = {
            "sex": sex_v,
            "treatment": treat_v,
            "breed": breed_v,
            "mob": mob_v,
            "eid": eid_v if is_admin else "Overall",
        }
        label = label_from_combo(combo, varying, all_overall)
        buckets: dict[str, list[float]] = {}
        counts: dict[str, int] = {}
        for row in grain:
            if not _combo_match(row, combo, group_cols):
                continue
            d = _as_date(row["date"]).isoformat()
            c = int(row["count"] or 0)
            v = float(row["value"])
            if d not in buckets:
                buckets[d] = [0.0, 0.0]
            buckets[d][0] += v * c
            buckets[d][1] += c
            counts[d] = counts.get(d, 0) + c

        if not buckets:
            missing.append(label)
            continue
        present.append(label)
        for d in sorted(buckets):
            total_w, total_c = buckets[d]
            series.append(
                {
                    "date": d,
                    "group": label,
                    "value": round(total_w / total_c, 2) if total_c else 0.0,
                    "count": counts[d],
                }
            )

    coverage = {
        "expected": len(combos),
        "present": len(present),
        "missing": len(missing),
        "present_groups": present,
        "missing_groups": missing,
    }
    note = None
    if len(present) > 1 and varying:
        if len(varying) == 1:
            note = f"Comparing groups by {friendly_label(varying[0])} only"
        else:
            note = f"Comparing groups by {', '.join(friendly_label(v) for v in varying)}"
    return series, coverage, note


def timeseries_sql(
    filters: FilterState, is_admin: bool, measure: str, show_smooth: bool = False
) -> dict:
    measure = _validate_measure(measure)
    grain = _fetch_daily_grain(filters, is_admin, measure)
    series, coverage, note = _series_for_combos(grain, filters, is_admin)
    if show_smooth:
        series = _smooth_series(series)

    return {
        "series": series,
        "y_label": format_measure_display(measure),
        "combo_coverage": coverage,
        "common_filters_note": note,
        "record_count": sum(int(r["count"]) for r in grain),
    }


# Cache full dimensional grain so checkbox toggles can be recomputed in the browser.
_TS_GRAIN_CACHE: dict[tuple, tuple[float, dict]] = {}
_TS_GRAIN_TTL = 300.0


def invalidate_timeseries_grain(farm_id: str) -> None:
    """Drop a farm's cached timeseries grain after its records are mutated (upload/revert)."""
    for key in [k for k in _TS_GRAIN_CACHE if k[0] == farm_id]:
        _TS_GRAIN_CACHE.pop(key, None)


def _scope_where_sql(filters: FilterState) -> tuple[str, list[Any]]:
    """Year/month/day + extra-filter scope — built-in dimension filters applied client-side.

    Extra filters are applied here because the grain carries no columns for them.
    """
    clauses = ["farm_id = %s", "date >= %s::date", "date < %s::date"]
    params: list[Any] = [
        filters.farm_id,
        f"{filters.year}-01-01",
        f"{filters.year + 1}-01-01",
    ]
    if filters.month and filters.month != "All":
        clauses.append("EXTRACT(MONTH FROM date)::int = %s")
        params.append(MONTH_NAMES.index(filters.month))
    if filters.day and filters.day != "All":
        clauses.append("EXTRACT(DAY FROM date)::int = %s")
        params.append(int(filters.day))
    if getattr(filters, "active_only", False):
        from app.services.active_window import resolve_active_window

        window = resolve_active_window(filters.farm_id, filters)
        if window:
            w_start, w_end = window
            clauses.append(
                "eid IN (SELECT eid FROM animal_data WHERE farm_id = %s AND date >= %s::date AND date <= %s::date AND eid IS NOT NULL)"
            )
            params.extend([filters.farm_id, w_start.isoformat(), w_end.isoformat()])
    _apply_extra_filters(filters, clauses, params)
    return " AND ".join(clauses), params


def _extra_filters_key(filters: FilterState) -> tuple:
    return tuple(
        (col, tuple(sorted(str(v) for v in vals)))
        for col, vals in sorted((filters.extra_filters or {}).items())
        if vals and "Overall" not in vals
    )


def timeseries_grain_sql(filters: FilterState, measure: str) -> dict:
    """Return date×dim grain for client-side series assembly (checkbox-fast path).

    Month/day are intentionally ignored here — the client caches year×measure grain
    and filters month/day locally so those controls stay interactive on Vercel.
    """
    import time

    from app.db import fetch_all

    measure = _validate_measure(measure)
    # Year-only scope key (month/day applied in the browser).
    active_only = bool(getattr(filters, "active_only", False))
    active_days = int(getattr(filters, "active_days", 30) or 30) if active_only else None
    active_mode = getattr(filters, "active_reference_mode", "auto") if active_only else None
    cache_key = (
        filters.farm_id,
        filters.year,
        measure,
        active_only,
        active_days,
        active_mode,
        _extra_filters_key(filters),
    )
    now = time.monotonic()
    cached = _TS_GRAIN_CACHE.get(cache_key)
    if cached and now - cached[0] < _TS_GRAIN_TTL:
        return cached[1]

    # Force year-wide grain regardless of request month/day.
    year_filters = filters.model_copy(update={"month": "All", "day": "All"})
    where, params = _scope_where_sql(year_filters)
    rows = fetch_all(
        f"""
        SELECT
          CAST(date AS VARCHAR) AS d,
          sex AS s,
          CASE WHEN treatment IS NULL THEN 'No Treatment' ELSE treatment END AS t,
          breed AS b,
          mob AS m,
          ROUND(AVG("{measure}")::numeric, 4)::float AS v,
          COUNT(*)::int AS c,
          MIN("{measure}")::float AS mn,
          MAX("{measure}")::float AS mx
        FROM animal_data
        WHERE {where} AND "{measure}" IS NOT NULL
        GROUP BY date, sex, treatment, breed, mob
        ORDER BY date
        """,
        tuple(params),
    )
    payload = {
        "grain": rows,
        "y_label": format_measure_display(measure),
        "record_count": sum(int(r["c"]) for r in rows),
        "scope": {
            "farm_id": filters.farm_id,
            "year": filters.year,
            "month": "All",
            "day": "All",
            "measure": measure,
        },
    }
    _TS_GRAIN_CACHE[cache_key] = (now, payload)
    return payload


def _fetch_measure_observations(
    filters: FilterState, is_admin: bool, measure: str
) -> list[dict]:
    """One row per animal_data observation (for row-level summary KPIs)."""
    from app.db import fetch_all

    measure = _validate_measure(measure)
    where, params = base_where_sql(filters, is_admin)
    return fetch_all(
        f"""
        SELECT
          date,
          sex,
          COALESCE(treatment, '__NONE__') AS treatment,
          breed,
          mob,
          eid,
          "{measure}"::float AS value
        FROM animal_data
        WHERE {where} AND "{measure}" IS NOT NULL
        """,
        tuple(params),
    )


def _kpi_from_values(values: list[float], label: str, unit: str) -> dict:
    """Row-level KPI block — matches R / SummaryService.kpi_block semantics."""
    if not values:
        return {
            "mean": 0,
            "min": 0,
            "max": 0,
            "median": 0,
            "count": 0,
            "unit": unit,
            "label": label,
        }
    values = sorted(values)
    n = len(values)
    mid = n // 2
    if n % 2:
        med = values[mid]
    else:
        med = (values[mid - 1] + values[mid]) / 2
    return {
        "mean": round(sum(values) / n, 2),
        "min": round(values[0], 2),
        "max": round(values[-1], 2),
        "median": round(med, 2),
        "count": n,
        "unit": unit,
        "label": label,
    }


def summary_sql_fast(filters: FilterState, is_admin: bool, measure: str) -> dict:
    """Observation-level KPIs via SQL aggregates (no row download).

    Used by /filters/bootstrap for first paint. Correct mean/median/min/max/count.
    """
    from app.db import fetch_one

    measure = _validate_measure(measure)
    where, params = base_where_sql(filters, is_admin)
    unit = MEASURE_UNITS.get(measure, "")
    row = fetch_one(
        f"""
        WITH bounded AS (
          SELECT date, "{measure}"::float AS m
          FROM animal_data
          WHERE {where} AND "{measure}" IS NOT NULL
        ),
        bounds AS (
          SELECT MAX(date) AS max_d FROM bounded
        )
        SELECT
          (SELECT max_d FROM bounds) AS max_d,
          ROUND(AVG(m)::numeric, 2)::float AS mean_all,
          ROUND(MIN(m)::numeric, 2)::float AS min_all,
          ROUND(MAX(m)::numeric, 2)::float AS max_all,
          ROUND((PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY m))::numeric, 2)::float AS med_all,
          COUNT(*)::int AS c_all,
          ROUND(AVG(m) FILTER (WHERE date = (SELECT max_d FROM bounds))::numeric, 2)::float AS mean_1,
          ROUND(MIN(m) FILTER (WHERE date = (SELECT max_d FROM bounds))::numeric, 2)::float AS min_1,
          ROUND(MAX(m) FILTER (WHERE date = (SELECT max_d FROM bounds))::numeric, 2)::float AS max_1,
          ROUND((
            SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY m)
            FROM bounded WHERE date = (SELECT max_d FROM bounds)
          )::numeric, 2)::float AS med_1,
          COUNT(*) FILTER (WHERE date = (SELECT max_d FROM bounds))::int AS c_1,
          ROUND(AVG(m) FILTER (WHERE date >= (SELECT max_d FROM bounds) - 14)::numeric, 2)::float AS mean_15,
          ROUND(MIN(m) FILTER (WHERE date >= (SELECT max_d FROM bounds) - 14)::numeric, 2)::float AS min_15,
          ROUND(MAX(m) FILTER (WHERE date >= (SELECT max_d FROM bounds) - 14)::numeric, 2)::float AS max_15,
          ROUND((
            SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY m)
            FROM bounded WHERE date >= (SELECT max_d FROM bounds) - 14
          )::numeric, 2)::float AS med_15,
          COUNT(*) FILTER (WHERE date >= (SELECT max_d FROM bounds) - 14)::int AS c_15,
          ROUND(AVG(m) FILTER (WHERE date >= (SELECT max_d FROM bounds) - 30)::numeric, 2)::float AS mean_31,
          ROUND(MIN(m) FILTER (WHERE date >= (SELECT max_d FROM bounds) - 30)::numeric, 2)::float AS min_31,
          ROUND(MAX(m) FILTER (WHERE date >= (SELECT max_d FROM bounds) - 30)::numeric, 2)::float AS max_31,
          ROUND((
            SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY m)
            FROM bounded WHERE date >= (SELECT max_d FROM bounds) - 30
          )::numeric, 2)::float AS med_31,
          COUNT(*) FILTER (WHERE date >= (SELECT max_d FROM bounds) - 30)::int AS c_31
        FROM bounded
        """,
        tuple(params),
    )
    if not row or not row.get("c_all"):
        return {"groups": [], "record_count": 0}

    max_d = _as_date(row["max_d"])
    last_label = f"Last Day ({max_d.strftime('%d/%m/%Y')})"

    def block(prefix: str, label: str) -> dict:
        return {
            "mean": float(row[f"mean_{prefix}"] or 0),
            "min": float(row[f"min_{prefix}"] or 0),
            "max": float(row[f"max_{prefix}"] or 0),
            "median": float(row[f"med_{prefix}"] or 0),
            "count": int(row[f"c_{prefix}"] or 0),
            "unit": unit,
            "label": label,
        }

    full = full_label_from_combo(
        {
            "sex": "Overall",
            "treatment": "Overall",
            "breed": "Overall",
            "mob": "Overall",
            "eid": "Overall",
        },
        is_admin,
    )
    return {
        "groups": [
            {
                "full_group": full,
                "windows": {
                    "last_day": block("1", last_label),
                    "last_15_days": block("15", "Last 15 Days"),
                    "last_month": block("31", "Last Month"),
                    "overall": block("all", "Overall"),
                },
            }
        ],
        "record_count": int(row["c_all"] or 0),
    }


def summary_sql(filters: FilterState, is_admin: bool, measure: str) -> dict:
    """Summary KPIs at observation level (mean/median/min/max/count over rows).

    Grain-based median (median of daily/group means) does not match R kpi_block /
    pandas Series.median() on raw measure values — this path uses observations.
    """
    dims = expand_dims_from_filters(filters, is_admin)
    if all(vals == ["Overall"] for vals in dims.values()):
        return summary_sql_fast(filters, is_admin, measure)

    measure = _validate_measure(measure)
    obs = _fetch_measure_observations(filters, is_admin, measure)
    group_cols = ["sex", "treatment", "breed", "mob", "eid"]
    combos = list(
        itertools.product(dims["sex"], dims["treatment"], dims["breed"], dims["mob"], dims["eid"])
    )
    unit = MEASURE_UNITS.get(measure, "")
    groups_out: list[dict] = []

    for sex_v, treat_v, breed_v, mob_v, eid_v in combos:
        combo = {
            "sex": sex_v,
            "treatment": treat_v,
            "breed": breed_v,
            "mob": mob_v,
            "eid": eid_v if is_admin else "Overall",
        }
        matched = [r for r in obs if _combo_match(r, combo, group_cols)]
        if not matched:
            continue
        full = full_label_from_combo(combo, is_admin)
        max_d = max(_as_date(r["date"]) for r in matched)

        def window_values(days: int | None, matched_rows=matched, max_date=max_d) -> list[float]:
            if days is None:
                rows = matched_rows
            else:
                start = max_date - timedelta(days=days - 1)
                rows = [r for r in matched_rows if _as_date(r["date"]) >= start]
            return [float(r["value"]) for r in rows]

        groups_out.append(
            {
                "full_group": full,
                "windows": {
                    "last_day": _kpi_from_values(
                        window_values(1), f"Last Day ({max_d.strftime('%d/%m/%Y')})", unit
                    ),
                    "last_15_days": _kpi_from_values(window_values(15), "Last 15 Days", unit),
                    "last_month": _kpi_from_values(window_values(31), "Last Month", unit),
                    "overall": _kpi_from_values(window_values(None), "Overall", unit),
                },
            }
        )

    return {"groups": groups_out, "record_count": len(obs)}



def distribution_sql(
    filters: FilterState, is_admin: bool, measure: str, hist_bins: int = 20
) -> dict:
    from app.db import fetch_all

    measure = _validate_measure(measure)
    hist_bins = max(10, min(50, hist_bins))
    where, params = base_where_sql(filters, is_admin)
    dims = expand_dims_from_filters(filters, is_admin)
    combos = list(
        itertools.product(dims["sex"], dims["treatment"], dims["breed"], dims["mob"], dims["eid"])
    )
    varying = [d for d, vals in dims.items() if len(vals) > 1]
    all_overall = all(vals == ["Overall"] for vals in dims.values())
    rows = fetch_all(
        f"""
        WITH ranked AS (
            SELECT eid,
                   date,
                   sex,
                   COALESCE(treatment, '__NONE__') AS treatment,
                   breed,
                   mob,
                   "{measure}"::float AS value,
                   MAX(date) OVER (
                       PARTITION BY eid
                   ) AS max_animal_date
            FROM animal_data
            WHERE {where} AND "{measure}" IS NOT NULL
        )
        SELECT eid,
               (ARRAY_AGG(sex ORDER BY date DESC))[1] AS sex,
               (ARRAY_AGG(treatment ORDER BY date DESC))[1] AS treatment,
               (ARRAY_AGG(breed ORDER BY date DESC))[1] AS breed,
               (ARRAY_AGG(mob ORDER BY date DESC))[1] AS mob,
               AVG(value) FILTER (WHERE date >= max_animal_date - INTERVAL '6 days')::float AS value,
               COUNT(*)::int AS source_record_count
        FROM ranked
        GROUP BY eid
        ORDER BY eid
        """,
        tuple(params),
    )

    hist_groups = []
    box_groups = []
    # Overall reference lines use each EID once, even when "Overall" plus
    # specific groups create overlapping comparison traces.
    all_values = [float(row["value"]) for row in rows]

    for sex_v, treat_v, breed_v, mob_v, eid_v in combos:
        combo = {
            "sex": sex_v,
            "treatment": treat_v,
            "breed": breed_v,
            "mob": mob_v,
            "eid": eid_v if is_admin else "Overall",
        }
        label = label_from_combo(combo, varying, all_overall)
        # Each row is one EID: its value is the mean of its latest seven
        # matching observations and its group dimensions come from the latest.
        vals = [float(r["value"]) for r in rows if _combo_match(r, combo, None)]
        if not vals:
            continue
        sample = vals if len(vals) <= 500 else vals[:: max(1, len(vals) // 500)][:500]
        hist_groups.append({"group": label, "values": sample})
        s = sorted(vals)
        n = len(s)

        def pct(p: float) -> float:
            if n == 1:
                return s[0]
            idx = p * (n - 1)
            lo = int(idx)
            hi = min(lo + 1, n - 1)
            frac = idx - lo
            return s[lo] * (1 - frac) + s[hi] * frac

        box_groups.append(
            {
                "group": label,
                "min": round(s[0], 2),
                "q1": round(pct(0.25), 2),
                "median": round(pct(0.5), 2),
                "q3": round(pct(0.75), 2),
                "max": round(s[-1], 2),
                "values": sample,
            }
        )

    mean_val = round(sum(all_values) / len(all_values), 2) if all_values else 0
    med_sorted = sorted(all_values)
    if not med_sorted:
        median_val = 0.0
    elif len(med_sorted) % 2:
        median_val = round(med_sorted[len(med_sorted) // 2], 2)
    else:
        m = len(med_sorted) // 2
        median_val = round((med_sorted[m - 1] + med_sorted[m]) / 2, 2)

    return {
        "histogram": {
            "bins": hist_bins,
            "groups": hist_groups,
            "mean": mean_val,
            "median": median_val,
        },
        "boxplot": {"groups": box_groups},
        "record_count": sum(int(row.get("source_record_count") or 0) for row in rows),
        "eid_count": len(rows),
    }


def count_filtered(filters: FilterState, is_admin: bool) -> dict:
    from app.db import fetch_one

    where, params = base_where_sql(filters, is_admin)
    filtered = fetch_one(
        f"""
        SELECT
          COUNT(*)::int AS record_count,
          COUNT(DISTINCT eid)::int AS active_animal_count
        FROM animal_data
        WHERE {where}
        """,
        tuple(params),
    ) or {}
    total = fetch_one(
        """
        SELECT
          COUNT(*)::int AS total_records,
          COUNT(DISTINCT eid)::int AS total_animal_count
        FROM animal_data
        WHERE farm_id = %s
        """,
        (filters.farm_id,),
    ) or {}
    return {
        "record_count": int(filtered.get("record_count") or 0),
        "total_records": int(total.get("total_records") or 0),
        "active_animal_count": int(filtered.get("active_animal_count") or 0),
        "total_animal_count": int(total.get("total_animal_count") or 0),
    }
