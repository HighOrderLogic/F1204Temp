"""Shared FinalPweight forecasting code: weight-only features and the recursive XGBoost forecast.

Used by ml/train_xgb_recursive.py (training and backtest) and by the forecast job that writes
predictions for the dashboard, so both build features exactly the same way.

How a forecast works:
  - The "baseline" is the farm's typical daily gain *at the animal's current weight*: the median
    daily gain of the farm's records in each GAIN_BAND_KG weight band, interpolated between bands
    and held flat beyond the lightest and heaviest bands. Heavier cattle gain less, so the
    projection bends instead of running in a straight line.
  - Each model predicts tomorrow's weight change relative to that baseline, from the animal's last
    WINDOW days of weights. The prediction is added to the history and the step repeats,
    HORIZON_DAYS times, looking the baseline up again at the new weight every day.
  - The model's adjustment fades out linearly over the first FADE_DAYS days; after that the animal
    follows the baseline curve. Without the fade, predictions feeding back into the features drift
    badly (backtest: MAE 13 kg instead of 43 kg at 58 days ahead).
  - The baseline and the allowed range of daily changes come from the farm being forecast, so a
    model trained on one farm can be applied to another whose cattle grow at a different rate.

One model serves every animal: it is trained on histories cut short at random (1 to WINDOW
days, missing days left as NaN, which XGBoost handles natively), so it forecasts animals seen
for a few days as well as those with a complete history. The tier is only a label for the
dashboard: "full" when all WINDOW days before the last record are observed, otherwise "short".
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

WINDOW = 14
HORIZON_DAYS = 182
FADE_DAYS = 20  # the range the backtest validates; saved as `fade_days` in model_meta.json
GAIN_BAND_KG = 50
MIN_BAND_CHANGES = 30  # weight bands with fewer daily changes are skipped (neighbours fill in)
CLIP_QUANTILES = (0.01, 0.99)
DEFAULT_CLIP = (-3.0, 3.0)  # kg/day, only used when a farm has no consecutive-day records at all

MODEL_FILE = "xgb_weight.ubj"  # in ml/models, next to model_meta.json

TIER_FULL = "full"
TIER_SHORT = "short"

FEATURES = [
    "weight",
    "n_days",
    *[f"e{i}" for i in range(1, 8)],
    "mean_e3",
    "mean_e7",
    f"mean_e{WINDOW - 1}",
    "std_d7",
    f"std_d{WINDOW - 1}",
]


@dataclass
class GrowthStats:
    """A farm's typical daily gain by weight and its plausible range of daily changes."""

    weights: tuple[float, ...]  # band centres (kg), ascending
    gains: tuple[float, ...]  # typical daily gain at each centre (kg/day)
    clip: tuple[float, float]

    def baseline_at(self, weight) -> np.ndarray:
        """Typical daily gain at each weight: interpolated between bands, flat beyond the ends."""
        return np.interp(np.asarray(weight, dtype=float), self.weights, self.gains)

    def to_dict(self) -> dict:
        return {"weights": list(self.weights), "gains": list(self.gains), "clip": list(self.clip)}


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise a long table to columns eid, date, breed, weight (one row per animal per day)."""
    cols = {c.lower(): c for c in df.columns}
    out = pd.DataFrame(
        {
            "eid": df[cols["eid"]].astype(str),
            "date": pd.to_datetime(df[cols["date"]]).dt.normalize(),
            "breed": df[cols["breed"]].fillna("Unknown").astype(str) if "breed" in cols else "Unknown",
            "weight": pd.to_numeric(df[cols["finalpweight"]], errors="coerce"),
        }
    )
    out = out.dropna(subset=["weight"])
    return out.drop_duplicates(["eid", "date"], keep="last").sort_values(["eid", "date"]).reset_index(drop=True)


def daily_changes(df: pd.DataFrame) -> pd.DataFrame:
    """Weight change between consecutive calendar days, with the weight on the first day."""
    g = df.groupby("eid", sort=False)
    consecutive = g["date"].diff() == pd.Timedelta(days=1)
    previous = g["weight"].shift()
    return pd.DataFrame(
        {
            "eid": df.loc[consecutive, "eid"],
            "weight": previous[consecutive],
            "change": (df["weight"] - previous)[consecutive],
        }
    )


BASELINE_MODES = ("curve", "capped", "flat")
BASELINE_MODE = "capped"  # used by the trained models and the dashboard forecasts


def growth_stats(df: pd.DataFrame, mode: str = BASELINE_MODE) -> GrowthStats:
    """The farm's typical daily gain.

    mode "curve":  median gain per weight band
         "capped": the same curve, never above the farm-wide median (it can only slow growth)
         "flat":   one farm-wide median (straight-line projections)
    """
    changes = daily_changes(df)
    if changes.empty:
        return GrowthStats((0.0,), (0.0,), DEFAULT_CLIP)
    lo, hi = changes["change"].quantile(list(CLIP_QUANTILES))
    clip = (float(lo), float(hi))
    median = float(changes["change"].median())
    bands = changes.groupby((changes["weight"] // GAIN_BAND_KG).astype(int))["change"].agg(["median", "size"])
    bands = bands[bands["size"] >= MIN_BAND_CHANGES]
    if mode == "flat" or bands.empty:
        return GrowthStats((0.0,), (median,), clip)
    gains = bands["median"].to_numpy()
    if mode == "capped":
        gains = np.minimum(gains, median)
    centres = (bands.index.to_numpy() + 0.5) * GAIN_BAND_KG
    return GrowthStats(tuple(map(float, centres)), tuple(map(float, gains)), clip)


def make_features(windows: np.ndarray, baseline: np.ndarray) -> np.ndarray:
    """One feature row per window. windows: (n, WINDOW) weights, oldest first, NaN = no record."""
    w = np.asarray(windows, dtype=float)
    d = np.diff(w, axis=1)  # daily changes, NaN wherever either day is missing
    e = d - baseline[:, None]  # change above/below the farm's typical gain
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)  # all-NaN slices -> NaN
        cols = [
            w[:, -1],
            np.sum(~np.isnan(w), axis=1).astype(float),
            *[e[:, -i] for i in range(1, 8)],
            np.nanmean(e[:, -3:], axis=1),
            np.nanmean(e[:, -7:], axis=1),
            np.nanmean(e, axis=1),
            _nanstd(d[:, -7:]),
            _nanstd(d),
        ]
    return np.column_stack(cols)


def _nanstd(a: np.ndarray) -> np.ndarray:
    out = np.nanstd(a, axis=1)
    out[np.sum(~np.isnan(a), axis=1) < 2] = np.nan
    return out


def latest_windows(df: pd.DataFrame) -> pd.DataFrame:
    """Per animal: the WINDOW calendar days ending at its last record, plus tier and history size."""
    rows = []
    for eid, g in df.groupby("eid", sort=True):
        last = g["date"].iloc[-1]
        days = pd.date_range(end=last, periods=WINDOW, freq="D")
        window = g.set_index("date")["weight"].reindex(days).to_numpy(dtype=float)
        rows.append(
            {
                "eid": eid,
                "breed": g["breed"].iloc[-1],
                "data_through": last,
                "history_days": len(g),
                "tier": TIER_FULL if not np.isnan(window).any() else TIER_SHORT,
                "window": window,
            }
        )
    return pd.DataFrame(rows)


def _model_share(step: int, fade_days: int | None) -> float:
    if fade_days is None:
        return 1.0
    if fade_days <= 0:
        return 0.0
    return max(0.0, 1.0 - step / fade_days)


def recursive_forecast(
    booster, windows: np.ndarray, stats: GrowthStats, days: int = HORIZON_DAYS, fade_days: int | None = FADE_DAYS
) -> np.ndarray:
    """Forecast `days` daily weights per window, feeding each prediction back in as history.

    booster: xgboost.Booster. fade_days=None disables the fade and fade_days=0 ignores the model
    (baseline curve only); both are for backtest comparisons.
    Returns an (n, days) array; column 0 is the day after the last record.
    """
    w = np.array(windows, dtype=float)
    out = np.empty((len(w), days))
    for step in range(days):
        baseline = stats.baseline_at(w[:, -1])
        share = _model_share(step, fade_days)
        excess = booster.inplace_predict(make_features(w, baseline)) if share > 0 else 0.0
        change = np.clip(baseline + share * excess, stats.clip[0], stats.clip[1])
        nxt = w[:, -1] + change
        out[:, step] = nxt
        w = np.concatenate([w[:, 1:], nxt[:, None]], axis=1)
    return out


def forecast_farm(
    df: pd.DataFrame, booster, days: int = HORIZON_DAYS, fade_days: int = FADE_DAYS
) -> tuple[pd.DataFrame, GrowthStats]:
    """Forecast every animal in a farm table (columns eid, date, breed, weight; see `prepare`).

    booster: xgboost.Booster, or anything with the same inplace_predict. fade_days: the model's
    tested range, normally the trained model's `fade_days` from model_meta.json. Returns long rows
    (eid, tier, history_days, data_through, horizon, date, pred) and the farm's growth stats.
    """
    stats = growth_stats(df)
    animals = latest_windows(df)
    cols = ["eid", "tier", "history_days", "data_through", "horizon", "date", "pred"]
    if animals.empty:
        return pd.DataFrame(columns=cols), stats
    preds = recursive_forecast(booster, np.vstack(animals["window"].to_numpy()), stats, days, fade_days)
    parts = [
        pd.DataFrame(
            {
                "eid": a["eid"],
                "tier": a["tier"],
                "history_days": a["history_days"],
                "data_through": a["data_through"],
                "horizon": np.arange(1, days + 1),
                "date": pd.date_range(a["data_through"] + pd.Timedelta(days=1), periods=days, freq="D"),
                "pred": p,
            }
        )
        for (_, a), p in zip(animals.iterrows(), preds)
    ]
    return pd.concat(parts, ignore_index=True)[cols], stats
