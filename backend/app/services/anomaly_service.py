from __future__ import annotations

import datetime
from typing import Any, Iterable, Literal

import numpy as np
import pandas as pd

# ============================================================
# Settings & Calibration Constants
# ============================================================

ANOMALY_WINDOW_DAYS: int = 28
ANOMALY_MIN_GROUP_SIZE: int = 10

ANOMALY_NOISE_HALF_WINDOW: int = 3
ANOMALY_NOISE_MIN_READINGS: int = 4

ANOMALY_GROWTH_MIN_READINGS: int = 10
ANOMALY_GROWTH_MIN_SPAN_DAYS: int = 21

ANOMALY_CUSUM_BASELINE_WEEKS: int = 2
ANOMALY_CUSUM_MIN_BASELINE: int = 7
ANOMALY_CUSUM_MIN_MONITORED: int = 3
ANOMALY_CUSUM_K: float = 0.5
ANOMALY_CUSUM_CAP: float = 2.0
ANOMALY_CUSUM_PERMUTATIONS: int = 20
ANOMALY_CUSUM_SEED: int = 3988

ANOMALY_MIN_RESIDUALS: int = 200

ANOMALY_SENSITIVITY: dict[str, dict[str, float]] = {
    "High": {"cusum_alpha": 0.05, "peer_z": -2.0, "point_quantile": 0.005},
    "Medium": {"cusum_alpha": 0.02, "peer_z": -2.5, "point_quantile": 0.001},
    "Low": {"cusum_alpha": 0.005, "peer_z": -3.0, "point_quantile": 0.0005},
}

ANOMALY_SEVERITY_LEVELS: list[str] = ["High", "Medium", "Low", "Normal", "Not enough data"]

ANOMALY_SEVERITY_COLOURS: dict[str, str] = {
    "High": "#7B241C",
    "Medium": "#B08968",
    "Low": "#95A5A6",
    "Normal": "#2D6A4F",
    "Not enough data": "#64748B",
}

ANOMALY_GROUP_CANDIDATES: list[str] = ["breed", "sex", "mob", "treatment"]

ANOMALY_WEIGHT_SOURCES: dict[str, str] = {
    "weight": "Observed weight (recommended)",
    "pweight": "Smoothed weight (pweight)",
}

ANOMALY_CACHE_SIZE: int = 20


# ============================================================
# Step 1: Prepare weights and remove herd-wide day effects
# ============================================================

def normalise_group_values(series: pd.Series) -> pd.Series:
    """Blank strings, 'None', and 'Unknown' represent unrecorded metadata."""
    s = series.astype(str).str.strip()
    mask = s.isna() | (s == "") | (s.str.lower() == "unknown") | (s == "none") | (s == "nan")
    out = s.copy()
    out[mask] = None
    return out


def prepare_weights(
    df: pd.DataFrame,
    id_col: str = "eid",
    date_col: str = "date",
    weight_col: str = "weight",
    group_cols: Iterable[str] | None = None,
    window_days: int = ANOMALY_WINDOW_DAYS,
) -> pd.DataFrame:
    """Prepares 28-day window of observed weights with integer day and week.

    Only observed weights may be passed in - never linearly interpolated measures.
    """
    missing = [c for c in (id_col, date_col, weight_col) if c not in df.columns]
    if missing:
        raise ValueError(f"prepare_weights(): missing column(s): {', '.join(missing)}")

    valid_group_cols = [c for c in (group_cols or []) if c in df.columns]

    if df.empty:
        cols = ["eid", "date", "weight"] + valid_group_cols + ["day", "week"]
        return pd.DataFrame(columns=cols)

    out = pd.DataFrame()
    out["eid"] = df[id_col].astype(str).str.strip()
    out["date"] = pd.to_datetime(df[date_col]).dt.date
    out["weight"] = pd.to_numeric(df[weight_col], errors="coerce")

    for g in valid_group_cols:
        out[g] = normalise_group_values(df[g])

    # Filter invalid records: missing EID, blank EID, missing date, missing/non-positive weight
    mask = (
        out["eid"].notna()
        & (out["eid"] != "")
        & (out["eid"].str.lower() != "nan")
        & (out["eid"].str.lower() != "none")
        & out["date"].notna()
        & out["weight"].notna()
        & np.isfinite(out["weight"])
        & (out["weight"] > 0)
    )
    out = out[mask].copy()

    if out.empty:
        cols = ["eid", "date", "weight"] + valid_group_cols + ["day", "week"]
        return pd.DataFrame(columns=cols)

    window_end = out["date"].max()
    window_start = window_end - datetime.timedelta(days=window_days - 1)
    out = out[out["date"] >= window_start].copy()

    if out.empty:
        cols = ["eid", "date", "weight"] + valid_group_cols + ["day", "week"]
        return pd.DataFrame(columns=cols)

    def _first_non_missing(s: pd.Series) -> Any:
        valid = s.dropna()
        return valid.iloc[0] if len(valid) > 0 else None

    agg_dict: dict[str, Any] = {"weight": "mean"}
    for g in valid_group_cols:
        agg_dict[g] = _first_non_missing

    out = out.groupby(["eid", "date"], as_index=False).agg(agg_dict)

    out["day"] = out["date"].apply(lambda d: (d - window_start).days + 1).astype(int)
    out["week"] = out["day"].apply(lambda d: (d - 1) // 7 + 1).astype(int)
    out = out.sort_values(["eid", "date"]).reset_index(drop=True)
    return out


def adjust_herd(d: pd.DataFrame, min_animals: int = ANOMALY_MIN_GROUP_SIZE) -> pd.DataFrame:
    """Subtracts shared daily herd shift (median animal deviation) on days with >= min_animals."""
    if d.empty:
        out = d.copy()
        out["herd_effect"] = pd.Series(dtype=float)
        out["adj"] = pd.Series(dtype=float)
        return out

    out = d.copy()
    # .dev = weight - median(weight) per animal
    medians = out.groupby("eid")["weight"].transform("median")
    out[".dev"] = out["weight"] - medians

    # herd_effect = median(.dev) on days with >= min_animals else 0.0
    day_counts = out.groupby("day")[".dev"].transform("count")
    day_medians = out.groupby("day")[".dev"].transform("median")
    out["herd_effect"] = np.where(day_counts >= min_animals, day_medians, 0.0)
    out["adj"] = out["weight"] - out["herd_effect"]
    out = out.drop(columns=[".dev"])
    return out


def herd_effect_series(d: pd.DataFrame) -> pd.DataFrame:
    """One row per day summarizing the daily herd effect."""
    if d.empty or "herd_effect" not in d.columns:
        return pd.DataFrame(columns=["date", "herd_effect", "n_animals"])
    series = (
        d.groupby("date")
        .agg(herd_effect=("herd_effect", "first"), n_animals=("eid", "nunique"))
        .reset_index()
    )
    return series


# ============================================================
# Step 2: Noise scale and single-day (point) scores
# ============================================================

def estimate_noise(
    d: pd.DataFrame,
    half_window: int = ANOMALY_NOISE_HALF_WINDOW,
    min_readings: int = ANOMALY_NOISE_MIN_READINGS,
    min_residuals: int = ANOMALY_MIN_RESIDUALS,
) -> dict[str, Any]:
    """Calculates 7-day centered window residuals and robust noise scale sigma = 1.4826 * MAD(r)."""
    if d.empty:
        out = d.copy()
        out["resid"] = pd.Series(dtype=float)
        return {"data": out, "sigma": None, "n_residuals": 0}

    # Self-join per eid within +/- half_window days
    sub = d[["eid", "day", "adj"]].copy()
    merged = pd.merge(sub, sub, on="eid", suffixes=("", ".nb"))
    in_win = merged[np.abs(merged["day.nb"] - merged["day"]) <= half_window]
    local_stats = (
        in_win.groupby(["eid", "day"])
        .agg(local_median=("adj.nb", "median"), n_local=("adj.nb", "count"))
        .reset_index()
    )

    out = pd.merge(d, local_stats, on=["eid", "day"], how="left")
    out["resid"] = np.where(out["n_local"] >= min_readings, out["adj"] - out["local_median"], np.nan)
    out = out.drop(columns=["local_median", "n_local"])

    valid_r = out["resid"].dropna().values
    n_residuals = len(valid_r)

    if n_residuals >= min_residuals:
        med = np.median(valid_r)
        mad = np.median(np.abs(valid_r - med))
        sigma = float(1.4826 * mad)
        if sigma <= 0 or not np.isfinite(sigma):
            sigma = None
    else:
        sigma = None

    return {"data": out, "sigma": sigma, "n_residuals": n_residuals}


def score_point(
    d: pd.DataFrame,
    sigma: float | None,
    quantiles: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Scores isolated scale drops against empirical quantiles of daily residuals."""
    if quantiles is None:
        quantiles = {level: preset["point_quantile"] for level, preset in ANOMALY_SENSITIVITY.items()}

    empty_scores = pd.DataFrame(columns=["eid", "point_z", "point_date", "point_resid_kg"])
    na_cutoffs = {k: None for k in quantiles}

    if d.empty or sigma is None or not np.isfinite(sigma) or "resid" not in d.columns:
        return {"scores": empty_scores, "cutoffs": na_cutoffs}

    valid_mask = d["resid"].notna()
    if not valid_mask.any():
        return {"scores": empty_scores, "cutoffs": na_cutoffs}

    z_vals = d.loc[valid_mask, "resid"].values / sigma
    cutoffs = {}
    for level, q in quantiles.items():
        cutoffs[level] = float(np.quantile(z_vals, q, method="linear"))

    valid_df = d.loc[valid_mask, ["eid", "date", "resid"]].copy()
    valid_df["z"] = valid_df["resid"] / sigma

    # Lowest z per animal
    idx = valid_df.groupby("eid")["z"].idxmin()
    lowest = valid_df.loc[idx].copy()
    scores = lowest.rename(columns={"z": "point_z", "date": "point_date", "resid": "point_resid_kg"})
    scores = scores[["eid", "point_z", "point_date", "point_resid_kg"]].reset_index(drop=True)

    return {"scores": scores, "cutoffs": cutoffs}


# ============================================================
# Step 3: Whole-month growth rate compared with peers
# ============================================================

def describe_peer_group(cols: list[str], values: list[Any], whole_label: str = "whole herd") -> str:
    if not cols:
        return whole_label
    return ", ".join(f"{c} = {v}" for c, v in zip(cols, values))


def _calc_slope(x: np.ndarray, y: np.ndarray) -> float:
    dx = x - np.mean(x)
    denom = np.sum(dx**2)
    if denom > 0:
        return float(np.sum(dx * (y - np.mean(y))) / denom)
    return np.nan


def score_peer_growth(
    d: pd.DataFrame,
    group_cols: list[str] | None = None,
    min_peers: int = ANOMALY_MIN_GROUP_SIZE,
    min_readings: int = ANOMALY_GROWTH_MIN_READINGS,
    min_span: int = ANOMALY_GROWTH_MIN_SPAN_DAYS,
    whole_label: str = "whole herd",
) -> pd.DataFrame:
    """Computes OLS slope of herd-adjusted weight over the window vs demographic peers."""
    valid_group_cols = [c for c in (group_cols or []) if c in d.columns]
    template_cols = [
        "eid", "n_readings", "growth_slope", "raw_growth", "peer_definition",
        "n_peers", "peer_median", "peer_raw_median", "peer_z"
    ]
    if d.empty:
        return pd.DataFrame(columns=template_cols)

    def _first_val(s: pd.Series) -> Any:
        valid = s.dropna()
        return valid.iloc[0] if len(valid) > 0 else None

    # Compute per-animal summary
    records = []
    for eid, grp in d.groupby("eid"):
        days = grp["day"].values
        span = int(np.max(days) - np.min(days))
        n_readings = len(grp)
        g_slope = _calc_slope(days, grp["adj"].values)
        r_growth = _calc_slope(days, grp["weight"].values)
        row: dict[str, Any] = {
            "eid": eid,
            "n_readings": n_readings,
            "span": span,
            "growth_slope": g_slope,
            "raw_growth": r_growth,
        }
        for g in valid_group_cols:
            row[g] = _first_val(grp[g])
        records.append(row)

    animals = pd.DataFrame(records)
    animals["eligible"] = (
        (animals["n_readings"] >= min_readings)
        & (animals["span"] >= min_span)
        & animals["growth_slope"].notna()
    )

    animals["peer_definition"] = None
    animals["n_peers"] = np.nan
    animals["peer_median"] = np.nan
    animals["peer_raw_median"] = np.nan
    animals["peer_z"] = np.nan

    # Hierarchical fallback: try full group set down to whole herd
    for k in range(len(valid_group_cols), -1, -1):
        cols = valid_group_cols[:k]
        todo = animals["eligible"] & animals["peer_definition"].isna()
        if not todo.any():
            break

        if k == 0:
            has_values = pd.Series(True, index=animals.index)
            key = pd.Series("", index=animals.index)
        else:
            has_values = animals[cols].notna().all(axis=1)
            # pandas 3 keeps NaN through astype(str), so join only the complete rows.
            key = pd.Series("", index=animals.index)
            if has_values.any():
                key[has_values] = animals.loc[has_values, cols].astype(str).agg("\r".join, axis=1)

        members = animals["eligible"] & has_values
        target_keys = key[todo & has_values].unique()

        for g in target_keys:
            in_group = members & (key == g)
            if in_group.sum() < min_peers:
                continue

            slopes = animals.loc[in_group, "growth_slope"].values
            peer_med = float(np.median(slopes))
            mad = float(np.median(np.abs(slopes - peer_med)))
            peer_spread = 1.4826 * mad
            assign_rows = in_group & todo

            if k == 0:
                values: list[Any] = []
            else:
                first_idx = animals[in_group].index[0]
                values = [animals.loc[first_idx, col] for col in cols]

            desc = describe_peer_group(cols, values, whole_label)
            n_p = int(in_group.sum())
            peer_raw_med = float(np.median(animals.loc[in_group, "raw_growth"].values))

            animals.loc[assign_rows, "peer_definition"] = desc
            animals.loc[assign_rows, "n_peers"] = n_p
            animals.loc[assign_rows, "peer_median"] = peer_med
            animals.loc[assign_rows, "peer_raw_median"] = peer_raw_med

            if peer_spread > 0:
                z_vals = (animals.loc[assign_rows, "growth_slope"] - peer_med) / peer_spread
                animals.loc[assign_rows, "peer_z"] = z_vals
            else:
                animals.loc[assign_rows, "peer_z"] = np.nan

    # Set non-eligible slopes to NaN
    animals.loc[~animals["eligible"], "growth_slope"] = np.nan
    animals.loc[~animals["eligible"], "raw_growth"] = np.nan

    out = animals[template_cols].copy()
    out["n_peers"] = out["n_peers"].astype("Int64")
    return out


# ============================================================
# Step 4: Sustained decline (robust one-sided CUSUM)
# ============================================================

def cusum_path(r: np.ndarray | list[float], k: float = ANOMALY_CUSUM_K) -> np.ndarray:
    """Lower one-sided CUSUM accumulation path: S_t = max(0, S_{t-1} - r_t - k)."""
    s = np.zeros(len(r), dtype=float)
    acc = 0.0
    for i, val in enumerate(r):
        acc = max(0.0, acc - val - k)
        s[i] = acc
    return s


def cusum_rows(
    d: pd.DataFrame,
    sigma: float,
    k: float = ANOMALY_CUSUM_K,
    cap: float = ANOMALY_CUSUM_CAP,
    baseline_weeks: int = ANOMALY_CUSUM_BASELINE_WEEKS,
    min_baseline: int = ANOMALY_CUSUM_MIN_BASELINE,
    min_monitored: int = ANOMALY_CUSUM_MIN_MONITORED,
) -> pd.DataFrame:
    """Prepares monitored rows with baseline, capped residual r, and CUSUM score."""
    group_vars = [v for v in [".perm", "eid"] if v in d.columns]
    if not group_vars:
        group_vars = ["eid"]

    out_chunks = []
    for _, grp in d.groupby(group_vars):
        n_b = (grp["week"] <= baseline_weeks).sum()
        n_m = (grp["week"] > baseline_weeks).sum()
        if n_b < min_baseline or n_m < min_monitored:
            continue

        baseline = float(grp.loc[grp["week"] <= baseline_weeks, "adj"].mean())
        monitored = grp[grp["week"] > baseline_weeks].sort_values("day").copy()
        monitored["baseline"] = baseline

        r_uncapped = (monitored["adj"].values - baseline) / sigma
        monitored["r"] = np.clip(r_uncapped, -cap, cap)
        monitored["cusum"] = cusum_path(monitored["r"].values, k)
        out_chunks.append(monitored)

    if not out_chunks:
        cols = list(d.columns) + ["baseline", "r", "cusum"]
        return pd.DataFrame(columns=cols)

    return pd.concat(out_chunks, ignore_index=True)


def score_cusum(
    d: pd.DataFrame,
    sigma: float | None,
    k: float = ANOMALY_CUSUM_K,
    cap: float = ANOMALY_CUSUM_CAP,
) -> dict[str, Any]:
    """Computes peak CUSUM score, run bounds, and mean shortfall in kg."""
    empty_scores = pd.DataFrame(
        columns=["eid", "cusum_max", "run_start", "run_end", "run_days", "shortfall_kg"]
    )
    empty_path = pd.DataFrame(columns=["eid", "date", "cusum"])

    if d.empty or sigma is None or not np.isfinite(sigma):
        return {"scores": empty_scores, "path": empty_path}

    rows = cusum_rows(d, sigma, k=k, cap=cap)
    if rows.empty:
        return {"scores": empty_scores, "path": empty_path}

    records = []
    for eid, grp in rows.groupby("eid"):
        grp = grp.sort_values("date").reset_index(drop=True)
        cusum_vals = grp["cusum"].values
        peak_idx = int(np.argmax(cusum_vals))
        peak_val = float(cusum_vals[peak_idx])

        if peak_val <= 0:
            records.append({
                "eid": eid,
                "cusum_max": 0.0,
                "run_start": None,
                "run_end": None,
                "run_days": 0,
                "shortfall_kg": 0.0,
            })
            continue

        start_idx = peak_idx
        while start_idx > 0 and cusum_vals[start_idx - 1] > 0:
            start_idx -= 1

        end_idx = peak_idx
        while end_idx < len(cusum_vals) - 1 and cusum_vals[end_idx + 1] > 0:
            end_idx += 1

        run_rows = grp.iloc[start_idx : end_idx + 1]
        run_start = run_rows["date"].iloc[0]
        run_end = run_rows["date"].iloc[-1]
        run_days = (run_end - run_start).days + 1
        shortfall_kg = float((run_rows["baseline"] - run_rows["adj"]).mean())

        records.append({
            "eid": eid,
            "cusum_max": peak_val,
            "run_start": run_start,
            "run_end": run_end,
            "run_days": run_days,
            "shortfall_kg": shortfall_kg,
        })

    scores = pd.DataFrame(records)
    path = rows[["eid", "date", "cusum"]].copy()
    return {"scores": scores, "path": path}


def calibrate_cusum(
    d: pd.DataFrame,
    sigma: float | None,
    alphas: dict[str, float] | None = None,
    permutations: int = ANOMALY_CUSUM_PERMUTATIONS,
    seed: int = ANOMALY_CUSUM_SEED,
    k: float = ANOMALY_CUSUM_K,
    cap: float = ANOMALY_CUSUM_CAP,
) -> dict[str, float | None]:
    """Permutation-calibrates empirical CUSUM decision limits h without mutating global RNG."""
    if alphas is None:
        alphas = {level: preset["cusum_alpha"] for level, preset in ANOMALY_SENSITIVITY.items()}

    na_limits = {k: None for k in alphas}
    if d.empty or sigma is None or not np.isfinite(sigma):
        return na_limits

    # Isolated generator: deterministic, zero interference with global random seed
    rng = np.random.default_rng(seed)

    base = d[["eid", "day", "week", "adj"]].copy()
    chunks = []
    for p in range(1, permutations + 1):
        perm_df = base.copy()
        perm_df[".perm"] = p
        # Shuffle adj within each eid
        shuffled_adjs = []
        for _, grp in perm_df.groupby("eid", sort=False):
            shuffled_adjs.append(rng.permutation(grp["adj"].values))
        perm_df["adj"] = np.concatenate(shuffled_adjs)
        chunks.append(perm_df)

    all_perms = pd.concat(chunks, ignore_index=True)
    rows = cusum_rows(all_perms, sigma, k=k, cap=cap)
    if rows.empty:
        return na_limits

    null_max = rows.groupby([".perm", "eid"])["cusum"].max().values
    limits = {}
    for level, alpha in alphas.items():
        limits[level] = float(np.quantile(null_max, 1.0 - alpha, method="linear"))
    return limits


# ============================================================
# Step 5: Weekly Change & Classification
# ============================================================

def latest_weekly_change(d: pd.DataFrame, min_readings: int = 3) -> pd.DataFrame:
    """Compares mean raw weight in Week 4 vs Week 3 when both have >= 3 readings."""
    template = pd.DataFrame(columns=["eid", "change_kg_7d", "change_pct_7d"])
    if d.empty:
        return template

    w34 = d[d["week"] >= 3].copy()
    if w34.empty:
        return template

    records = []
    for eid, grp in w34.groupby("eid"):
        w4 = grp[grp["week"] == 4]
        w3 = grp[grp["week"] == 3]
        n_last = len(w4)
        n_prev = len(w3)
        if n_last >= min_readings and n_prev >= min_readings:
            last_m = float(w4["weight"].mean())
            prev_m = float(w3["weight"].mean())
            diff = last_m - prev_m
            pct = 100.0 * diff / prev_m if prev_m != 0 else np.nan
            records.append({"eid": eid, "change_kg_7d": diff, "change_pct_7d": pct})
        else:
            records.append({"eid": eid, "change_kg_7d": np.nan, "change_pct_7d": np.nan})

    return pd.DataFrame(records)


def run_anomaly_scoring(
    df: pd.DataFrame,
    id_col: str = "eid",
    date_col: str = "date",
    weight_col: str = "weight",
    group_cols: list[str] | None = None,
    window_days: int = ANOMALY_WINDOW_DAYS,
) -> dict[str, Any]:
    """Runs data preparation, herd adjustment, noise estimation, point and CUSUM scoring."""
    valid_group_cols = [c for c in (group_cols or []) if c in df.columns]
    prep = prepare_weights(df, id_col, date_col, weight_col, valid_group_cols, window_days)
    d = adjust_herd(prep)
    noise = estimate_noise(d)
    d_with_resid = noise["data"]
    sigma = noise["sigma"]

    window_start = d_with_resid["date"].min() if not d_with_resid.empty else None
    window_end = d_with_resid["date"].max() if not d_with_resid.empty else None

    return {
        "data": d_with_resid,
        "window_start": window_start,
        "window_end": window_end,
        "sigma": sigma,
        "n_residuals": noise["n_residuals"],
        "herd": herd_effect_series(d_with_resid),
        "point": score_point(d_with_resid, sigma),
        "cusum": score_cusum(d_with_resid, sigma),
        "cusum_limits": calibrate_cusum(d_with_resid, sigma),
        "weekly": latest_weekly_change(d_with_resid),
    }


def _format_kg(x: float | None) -> str:
    if x is None or pd.isna(x):
        return "-"
    return f"{x:.1f}"


def _format_day(d: datetime.date | None) -> str:
    if d is None or pd.isna(d):
        return "-"
    return d.strftime("%d %b")


def classify_anomalies(
    scores: dict[str, Any],
    peer: pd.DataFrame,
    sensitivity: Literal["High", "Medium", "Low"] = "Medium",
) -> pd.DataFrame:
    """Combines CUSUM, Peer Growth, and Point drop into High/Medium/Low/Normal/Not enough data."""
    if sensitivity not in ANOMALY_SENSITIVITY:
        raise ValueError(
            f"classify_anomalies(): sensitivity must be one of {', '.join(ANOMALY_SENSITIVITY)}"
        )

    preset = ANOMALY_SENSITIVITY[sensitivity]
    h = scores["cusum_limits"].get(sensitivity)
    point_cut = scores["point"]["cutoffs"].get(sensitivity)
    d = scores["data"]

    empty_df = pd.DataFrame(
        columns=[
            "eid", "severity", "anomaly_types", "reason", "n_readings",
            "first_date", "last_date", "change_kg_7d", "change_pct_7d"
        ]
    )
    if d.empty:
        return empty_df

    animals = (
        d.groupby("eid")
        .agg(n_readings=("date", "count"), first_date=("date", "min"), last_date=("date", "max"))
        .reset_index()
    )

    if not scores["weekly"].empty:
        animals = pd.merge(animals, scores["weekly"], on="eid", how="left")
    else:
        animals["change_kg_7d"] = np.nan
        animals["change_pct_7d"] = np.nan

    if not scores["point"]["scores"].empty:
        animals = pd.merge(animals, scores["point"]["scores"], on="eid", how="left")
    else:
        animals["point_z"] = np.nan
        animals["point_date"] = None
        animals["point_resid_kg"] = np.nan

    if not scores["cusum"]["scores"].empty:
        animals = pd.merge(animals, scores["cusum"]["scores"], on="eid", how="left")
    else:
        animals["cusum_max"] = np.nan
        animals["run_start"] = None
        animals["run_end"] = None
        animals["run_days"] = 0
        animals["shortfall_kg"] = np.nan

    if not peer.empty:
        peer_cols = [c for c in peer.columns if c != "n_readings"]
        animals = pd.merge(animals, peer[peer_cols], on="eid", how="left")
    else:
        animals["growth_slope"] = np.nan
        animals["raw_growth"] = np.nan
        animals["peer_definition"] = None
        animals["n_peers"] = np.nan
        animals["peer_median"] = np.nan
        animals["peer_raw_median"] = np.nan
        animals["peer_z"] = np.nan

    has_cusum = animals["cusum_max"].notna() & (h is not None) & pd.notna(h)
    has_peer = animals["peer_z"].notna()
    has_point = animals["point_z"].notna() & (point_cut is not None) & pd.notna(point_cut)

    is_high = has_cusum & (animals["cusum_max"] > (h if h is not None else np.inf))
    is_medium = has_peer & (animals["peer_z"] < preset["peer_z"])
    is_low = has_point & (animals["point_z"] < (point_cut if point_cut is not None else -np.inf))

    severities = []
    for ih, im, il, hc, hp in zip(is_high, is_medium, is_low, has_cusum, has_peer):
        if ih:
            severities.append("High")
        elif im:
            severities.append("Medium")
        elif il:
            severities.append("Low")
        elif hc or hp:
            severities.append("Normal")
        else:
            severities.append("Not enough data")

    animals["severity"] = severities

    sigma_txt = _format_kg(scores["sigma"])
    window_end = scores["window_end"]

    types_list = []
    reasons_list = []

    for _, row in animals.iterrows():
        sev = row["severity"]
        t_arr = []
        r_arr = []

        if is_high.loc[_]:
            t_arr.append("Sustained decline")
            still_below = row["run_end"] is not None and row["run_end"] == window_end
            ending_txt = ", and it has not caught up" if still_below else f", until {_format_day(row['run_end'])}"
            r_arr.append(
                f"Falling behind the herd: since {_format_day(row['run_start'])} this animal has been on average "
                f"{_format_kg(row['shortfall_kg'])} kg further behind the rest of the herd than it was in the first two weeks, "
                f"for {int(row['run_days'])} days{ending_txt}. Normal day-to-day variation on this farm is about ±{sigma_txt} kg."
            )

        if is_medium.loc[_]:
            t_arr.append("Low growth vs peers")
            r_arr.append(
                f"Low growth compared with similar animals: growing {_format_kg(row['raw_growth'])} kg/day this month, "
                f"versus a median of {_format_kg(row['peer_raw_median'])} kg/day for {int(row['n_peers'])} similar animals ({row['peer_definition']})."
            )

        if is_low.loc[_]:
            t_arr.append("Single unusual reading")
            pct_fmt = f"{100 * preset['point_quantile']:g}"
            r_arr.append(
                f"Single unusual reading: on {_format_day(row['point_date'])} the weight was "
                f"{_format_kg(abs(row['point_resid_kg']))} kg below this animal's usual level, "
                f"among the lowest {pct_fmt}% of daily readings on this farm."
            )

        if sev == "Not enough data":
            r_arr.append(
                f"Not enough data: the month-long checks need at least {ANOMALY_GROWTH_MIN_READINGS} readings "
                f"spread over {ANOMALY_GROWTH_MIN_SPAN_DAYS} days (or {ANOMALY_CUSUM_MIN_BASELINE} in the first two weeks); "
                f"this animal has {int(row['n_readings'])} readings in the latest {ANOMALY_WINDOW_DAYS} days, "
                f"between {_format_day(row['first_date'])} and {_format_day(row['last_date'])}."
            )

        types_list.append(", ".join(t_arr))
        reasons_list.append("\n".join(r_arr))

    animals["anomaly_types"] = types_list
    animals["reason"] = reasons_list

    # Sorting: severity rank (High -> Medium -> Low -> Normal -> Not enough data),
    # change_kg_7d ascending (NaNs last), cusum_max desc, peer_z asc, point_z asc
    severity_order = {s: i for i, s in enumerate(ANOMALY_SEVERITY_LEVELS)}
    animals[".sev_rank"] = animals["severity"].map(severity_order)
    animals[".chg_sort"] = np.where(animals["change_kg_7d"].isna(), 1e9, animals["change_kg_7d"])
    animals[".cusum_sort"] = np.where(animals["cusum_max"].isna(), -1e9, animals["cusum_max"])
    animals[".peer_sort"] = np.where(animals["peer_z"].isna(), 1e9, animals["peer_z"])
    animals[".point_sort"] = np.where(animals["point_z"].isna(), 1e9, animals["point_z"])

    animals = animals.sort_values(
        [".sev_rank", ".chg_sort", ".cusum_sort", ".peer_sort", ".point_sort"],
        ascending=[True, True, False, True, True],
    ).drop(columns=[".sev_rank", ".chg_sort", ".cusum_sort", ".peer_sort", ".point_sort"]).reset_index(drop=True)

    return animals


# ============================================================
# Step 6 & 7: Period, Data Loading, Reference & LRU Cache
# ============================================================

from collections import OrderedDict
import calendar
import hashlib

_ANOMALY_CACHE: OrderedDict[str, dict[str, Any]] = OrderedDict()


def clear_anomaly_cache() -> None:
    """Clears the LRU cache (used for unit tests and upload invalidation)."""
    _ANOMALY_CACHE.clear()


def anomaly_period(
    year: int | str | None = None,
    month: str | int | None = None,
    day: str | int | None = None,
) -> dict[str, Any] | None:
    """Resolves period range [from, to] and label from year, month, day."""
    if year is None:
        return None
    try:
        y = int(year)
    except (ValueError, TypeError):
        return None

    m_val: int | None = None
    if month is not None and str(month).strip().lower() not in {"all", "none", ""}:
        if isinstance(month, int) and 1 <= month <= 12:
            m_val = month
        else:
            m_str = str(month).strip()
            # Try month name lookup
            month_names = list(calendar.month_name)[1:]
            if m_str in month_names:
                m_val = month_names.index(m_str) + 1
            else:
                try:
                    parsed_m = int(m_str)
                    if 1 <= parsed_m <= 12:
                        m_val = parsed_m
                except ValueError:
                    m_val = None

    if m_val is None:
        return {
            "from": datetime.date(y, 1, 1),
            "to": datetime.date(y, 12, 31),
            "label": str(y),
        }

    last_day_of_month = calendar.monthrange(y, m_val)[1]
    from_date = datetime.date(y, m_val, 1)
    to_date = datetime.date(y, m_val, last_day_of_month)

    d_val: int | None = None
    if day is not None and str(day).strip().lower() not in {"all", "none", ""}:
        try:
            parsed_d = int(day)
            if 1 <= parsed_d <= last_day_of_month:
                d_val = parsed_d
        except (ValueError, TypeError):
            d_val = None

    if d_val is not None:
        the_day = datetime.date(y, m_val, d_val)
        return {
            "from": the_day,
            "to": the_day,
            "label": the_day.strftime("%d %B %Y").lstrip("0"),
        }

    return {
        "from": from_date,
        "to": to_date,
        "label": from_date.strftime("%B %Y"),
    }


def load_anomaly_weights(
    farm_df: pd.DataFrame,
    period: dict[str, Any] | None = None,
    weight_col: str = "weight",
    group_candidates: list[str] | None = None,
    window_days: int = ANOMALY_WINDOW_DAYS,
) -> dict[str, Any]:
    """Extracts 28-day window of observed/smoothed weights ending on the period's latest date."""
    if weight_col not in ANOMALY_WEIGHT_SOURCES:
        raise ValueError(
            f"load_anomaly_weights(): '{weight_col}' is not an allowed weight source. "
            f"Must be one of: {', '.join(ANOMALY_WEIGHT_SOURCES)}"
        )

    if group_candidates is None:
        group_candidates = ANOMALY_GROUP_CANDIDATES

    required = ["eid", "date", weight_col]
    missing = [c for c in required if c not in farm_df.columns]
    if missing:
        return {
            "data": pd.DataFrame(columns=["eid", "date", "weight"]),
            "group_cols": [],
            "available": False,
            "message": f"Anomaly detection needs a weight column, but data has no '{', '.join(missing)}' column.",
            "latest_date": None,
        }

    group_cols = [c for c in group_candidates if c in farm_df.columns]

    sub = farm_df[farm_df[weight_col].notna()].copy()
    if sub.empty:
        return {
            "data": pd.DataFrame(columns=["eid", "date", "weight"] + group_cols),
            "group_cols": group_cols,
            "available": False,
            "message": f"This farm's data has no values in '{weight_col}' yet.",
            "latest_date": None,
        }

    sub["_date"] = pd.to_datetime(sub["date"]).dt.date
    if period is not None:
        p_from = period["from"]
        p_to = period["to"]
        in_period = sub[(sub["_date"] >= p_from) & (sub["_date"] <= p_to)]
        if in_period.empty:
            return {
                "data": pd.DataFrame(columns=["eid", "date", "weight"] + group_cols),
                "group_cols": group_cols,
                "available": False,
                "message": f"There are no values in '{weight_col}' in the selected period ({period['label']}).",
                "latest_date": None,
            }
        latest_date = in_period["_date"].max()
    else:
        latest_date = sub["_date"].max()

    window_start = latest_date - datetime.timedelta(days=window_days - 1)
    window_mask = (sub["_date"] >= window_start) & (sub["_date"] <= latest_date)
    window_df = sub[window_mask].copy()

    rename_map = {weight_col: "weight", "_date": "date"}
    select_cols = ["eid", "date", weight_col] + group_cols
    out = window_df[select_cols].rename(columns=rename_map)
    out["eid"] = out["eid"].astype(str)

    return {
        "data": out,
        "group_cols": group_cols,
        "available": True,
        "message": None,
        "latest_date": latest_date,
    }


def anomaly_reference(
    data: pd.DataFrame,
    selected_eids: Iterable[Any] | None = None,
    min_animals: int = ANOMALY_MIN_GROUP_SIZE,
) -> dict[str, Any]:
    """Resolves whether comparison is among selected animals or whole farm."""
    all_eids = list(data["eid"].dropna().astype(str).unique())
    if selected_eids is None:
        shown = all_eids
    else:
        sel_set = {str(e).strip() for e in selected_eids if str(e).strip()}
        shown = [e for e in all_eids if e in sel_set]

    if len(shown) >= min_animals:
        ref_data = data[data["eid"].isin(shown)].copy()
        return {
            "data": ref_data,
            "scope": "selected",
            "shown": shown,
            "label": "all selected animals",
        }
    else:
        return {
            "data": data.copy(),
            "scope": "farm",
            "shown": shown,
            "label": "whole farm",
        }


def get_anomaly_scores(
    farm_id: str,
    farm_df: pd.DataFrame,
    period: dict[str, Any] | None = None,
    selected_eids: Iterable[Any] | None = None,
    weight_col: str = "weight",
    min_animals: int = ANOMALY_MIN_GROUP_SIZE,
) -> dict[str, Any]:
    """Retrieves or calculates anomaly scores, cached up to ANOMALY_CACHE_SIZE."""
    loaded = load_anomaly_weights(farm_df, period=period, weight_col=weight_col)
    if not loaded["available"] or loaded["data"].empty:
        return {
            "loaded": loaded,
            "scores": None,
            "peer": None,
            "scope": "farm",
            "shown": [],
            "n_reference": 0,
        }

    ref = anomaly_reference(loaded["data"], selected_eids, min_animals=min_animals)
    eids_sorted = sorted(ref["data"]["eid"].dropna().unique())
    eids_hash = hashlib.sha256("".join(eids_sorted).encode("utf-8")).hexdigest()[:12]
    max_d_str = str(loaded["latest_date"])
    sum_w = round(float(loaded["data"]["weight"].sum()), 3)

    cache_key = f"{farm_id}|{weight_col}|{len(loaded['data'])}|{max_d_str}|{sum_w}|{ref['scope']}|{eids_hash}"

    if cache_key in _ANOMALY_CACHE:
        cached = _ANOMALY_CACHE[cache_key]
        _ANOMALY_CACHE.move_to_end(cache_key)
    else:
        scores = run_anomaly_scoring(ref["data"], group_cols=loaded["group_cols"])
        peer = score_peer_growth(scores["data"], group_cols=loaded["group_cols"], whole_label=ref["label"])
        cached = {"scores": scores, "peer": peer}
        _ANOMALY_CACHE[cache_key] = cached
        while len(_ANOMALY_CACHE) > ANOMALY_CACHE_SIZE:
            _ANOMALY_CACHE.popitem(last=False)

    return {
        "loaded": loaded,
        "scores": cached["scores"],
        "peer": cached["peer"],
        "scope": ref["scope"],
        "shown": ref["shown"],
        "n_reference": len(eids_sorted),
    }


def anomaly_source_note(weight_col: str, scores: dict[str, Any] | None) -> str | None:
    """Returns contextual guidance on smoothing or noise limitation."""
    if scores is None or scores["data"].empty:
        return None
    if scores.get("sigma") is None:
        return (
            "There were too few readings to measure day-to-day variation, so the sustained-decline and "
            "single-reading checks cannot run; only growth compared with similar animals is checked."
        )
    if weight_col != "weight":
        return (
            f"{weight_col} is smoothed by the data provider. The sensitivity settings were calibrated on "
            "observed weights; smoothed values are correlated from day to day, so expect noticeably more "
            "animals flagged High than the chosen sensitivity suggests, and single-reading flags mean little. "
            "Use observed weight for the calibrated analysis."
        )
    return None


def herd_shift_note(scores: dict[str, Any] | None, who: str = "the whole herd") -> str | None:
    """Returns a herd-shift note if a collective day shift exceeds 2 sigma beyond normal growth."""
    if scores is None:
        return None
    herd = scores.get("herd")
    sigma = scores.get("sigma")
    if herd is None or len(herd) < 3 or sigma is None:
        return None

    # Linear trend through herd_effect
    days = np.array([(d - herd["date"].iloc[0]).days for d in herd["date"]], dtype=float)
    y = herd["herd_effect"].values.astype(float)
    if np.sum((days - np.mean(days))**2) > 0:
        slope = float(np.sum((days - np.mean(days)) * (y - np.mean(y))) / np.sum((days - np.mean(days))**2))
        trend = np.mean(y) + slope * (days - np.mean(days))
    else:
        trend = np.full_like(y, np.mean(y))

    shift = y - trend
    max_idx = int(np.argmax(np.abs(shift)))
    max_shift = float(abs(shift[max_idx]))

    if max_shift <= 2 * sigma:
        return None

    date_str = _format_day(herd["date"].iloc[max_idx])
    return (
        f"On some days {who} moved together by up to {_format_kg(max_shift)} kg beyond normal growth "
        f"(largest on {date_str}). Shared changes like this (feed, water, weather or the scale) "
        "are removed before individual animals are checked, so they do not flag every animal."
    )


def anomaly_detail_series(
    scores: dict[str, Any],
    animal_id: str,
    sensitivity: Literal["High", "Medium", "Low"] = "Medium",
) -> dict[str, Any] | None:
    """Constructs Plotly-ready series for raw/adjusted weights and CUSUM trajectory."""
    d = scores["data"]
    a = d[d["eid"] == animal_id].sort_values("date").reset_index(drop=True)
    if a.empty:
        return None

    # Trailing 7-day average of raw weight
    dates = a["date"].values
    weights = a["weight"].values
    avg_7d = []
    for i, cur_d in enumerate(dates):
        mask = [(cur_d - d_prev).days <= 7 and (cur_d - d_prev).days >= 0 for d_prev in dates[: i + 1]]
        avg_7d.append(float(np.mean(weights[: i + 1][mask])))
    a["avg_7d"] = avg_7d

    # Baseline & expected weight
    baseline_days = a["week"] <= ANOMALY_CUSUM_BASELINE_WEEKS
    if baseline_days.sum() >= ANOMALY_CUSUM_MIN_BASELINE:
        baseline_val = float(a.loc[baseline_days, "adj"].mean())
        a["expected"] = baseline_val + a["herd_effect"]
    else:
        baseline_val = None
        a["expected"] = np.nan

    # Animal daily rows
    daily_records = []
    for _, row in a.iterrows():
        daily_records.append({
            "date": row["date"].isoformat(),
            "day": int(row["day"]),
            "week": int(row["week"]),
            "weight": round(float(row["weight"]), 2),
            "adj": round(float(row["adj"]), 2),
            "herd_effect": round(float(row["herd_effect"]), 2),
            "avg_7d": round(float(row["avg_7d"]), 2),
            "expected": round(float(row["expected"]), 2) if pd.notna(row["expected"]) else None,
        })

    # CUSUM path & threshold
    cusum_path_df = scores["cusum"].get("path")
    cusum_series = []
    if cusum_path_df is not None and not cusum_path_df.empty:
        a_cusum = cusum_path_df[cusum_path_df["eid"] == animal_id].sort_values("date")
        for _, row in a_cusum.iterrows():
            cusum_series.append({
                "date": row["date"].isoformat(),
                "cusum": round(float(row["cusum"]), 3),
            })

    h = scores["cusum_limits"].get(sensitivity)

    # Point drop marker
    point_scores = scores["point"]["scores"]
    point_drop = None
    if not point_scores.empty and animal_id in point_scores["eid"].values:
        p_row = point_scores[point_scores["eid"] == animal_id].iloc[0]
        if pd.notna(p_row["point_z"]):
            point_drop = {
                "date": p_row["point_date"].isoformat(),
                "z": round(float(p_row["point_z"]), 2),
                "resid_kg": round(float(p_row["point_resid_kg"]), 2),
            }

    # CUSUM run bounds
    cusum_scores = scores["cusum"]["scores"]
    run_bounds = None
    if not cusum_scores.empty and animal_id in cusum_scores["eid"].values:
        c_row = cusum_scores[cusum_scores["eid"] == animal_id].iloc[0]
        if pd.notna(c_row["run_start"]) and c_row["run_days"] > 0:
            run_bounds = {
                "start": c_row["run_start"].isoformat(),
                "end": c_row["run_end"].isoformat(),
                "days": int(c_row["run_days"]),
                "shortfall_kg": round(float(c_row["shortfall_kg"]), 2),
                "cusum_max": round(float(c_row["cusum_max"]), 2),
            }

    return {
        "eid": animal_id,
        "daily": daily_records,
        "cusum_path": cusum_series,
        "cusum_threshold": round(float(h), 2) if h is not None and pd.notna(h) else None,
        "baseline_kg": round(baseline_val, 2) if baseline_val is not None else None,
        "run_bounds": run_bounds,
        "point_drop": point_drop,
    }

