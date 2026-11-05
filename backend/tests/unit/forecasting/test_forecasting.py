from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))  # repo root, for the top-level ml package

from ml.forecasting import (  # noqa: E402
    DEFAULT_CLIP,
    FADE_DAYS,
    FEATURES,
    TIER_FULL,
    TIER_SHORT,
    WINDOW,
    GrowthStats,
    forecast_farm,
    growth_stats,
    latest_windows,
    make_features,
    recursive_forecast,
)


class ConstantBooster:
    """Stands in for xgboost.Booster: predicts the same excess daily gain for every row."""

    def __init__(self, excess: float) -> None:
        self.excess = excess

    def inplace_predict(self, x: np.ndarray) -> np.ndarray:
        assert x.shape[1] == len(FEATURES)
        return np.full(len(x), self.excess)


def farm(days_by_animal: dict[str, int], gain: float = 1.0, start: float = 400.0, end: str = "2026-09-21") -> pd.DataFrame:
    rows = []
    for eid, n in days_by_animal.items():
        dates = pd.date_range(end=end, periods=n, freq="D")
        rows += [{"eid": eid, "date": d, "breed": "AA", "weight": start + gain * i} for i, d in enumerate(dates)]
    return pd.DataFrame(rows)


@pytest.mark.unit
def test_make_features_complete_and_partial_windows():
    full = np.arange(WINDOW, dtype=float)[None, :] + 400  # +1 kg/day
    partial = full.copy()
    partial[0, : WINDOW - 3] = np.nan  # only the last 3 days observed
    x = make_features(np.vstack([full, partial]), baseline=np.array([0.5, 0.5]))
    col = {name: i for i, name in enumerate(FEATURES)}
    assert x[0, col["weight"]] == full[0, -1]
    assert x[0, col["n_days"]] == WINDOW and x[1, col["n_days"]] == 3
    assert x[0, col["e1"]] == pytest.approx(0.5)  # 1 kg/day change - 0.5 baseline
    assert not np.isnan(x[0]).any()
    assert np.isnan(x[1, col["e7"]]), "days before the history are missing, not zero"


@pytest.mark.unit
def test_latest_windows_assigns_tiers_from_the_last_14_days():
    df = farm({"long": 30, "short": 5})
    gap = farm({"gap": 30})
    gap = gap[gap.date != gap.date.max() - pd.Timedelta(days=3)]  # one missing day inside the window
    animals = latest_windows(pd.concat([df, gap], ignore_index=True)).set_index("eid")
    assert animals.loc["long", "tier"] == TIER_FULL
    assert animals.loc["short", "tier"] == TIER_SHORT
    assert animals.loc["gap", "tier"] == TIER_SHORT
    assert animals.loc["short", "history_days"] == 5
    assert animals.loc["long", "data_through"] == pd.Timestamp("2026-09-21")


@pytest.mark.unit
def test_recursive_forecast_clips_and_fades_to_the_baseline():
    windows = np.full((1, WINDOW), 500.0)
    clipped = recursive_forecast(ConstantBooster(100.0), windows, GrowthStats((0.0,), (1.0,), (-2.0, 3.0)), days=5)
    assert np.allclose(np.diff(np.concatenate([[500.0], clipped[0]])), 3.0), "huge predictions are clipped"

    stats = GrowthStats((0.0,), (1.0,), (-10.0, 10.0))
    out = recursive_forecast(ConstantBooster(0.5), windows, stats, days=FADE_DAYS + 10)
    changes = np.diff(np.concatenate([[500.0], out[0]]))
    assert changes[0] == pytest.approx(1.5), "day 1: baseline + full model adjustment"
    assert np.allclose(changes[FADE_DAYS:], 1.0), "after the fade: baseline only"
    assert out.shape == (1, FADE_DAYS + 10)


@pytest.mark.unit
def test_capped_growth_curve_slows_heavy_animals_and_never_exceeds_the_median():
    light = farm({f"l{i}": 20 for i in range(5)}, gain=2.0, start=400)  # 400-450 kg band
    heavy = farm({f"h{i}": 20 for i in range(5)}, gain=0.5, start=650)  # 650-700 kg band
    df = pd.concat([light, heavy], ignore_index=True)
    median = 1.25  # half the daily changes are 2.0, half 0.5

    curve, capped, flat = (growth_stats(df, mode) for mode in ("curve", "capped", "flat"))
    assert curve.baseline_at([425, 675]) == pytest.approx([2.0, 0.5])
    assert capped.baseline_at([425, 675]) == pytest.approx([median, 0.5]), "capped at the farm median"
    assert capped.baseline_at(900) == pytest.approx(0.5), "flat beyond the heaviest band"
    assert flat.baseline_at([425, 675]) == pytest.approx([median, median])

    sparse = farm({"s": 3}, gain=0.0, start=1000)  # 2 daily changes: too few for its own band
    with_sparse = growth_stats(pd.concat([df, sparse], ignore_index=True), "curve")
    assert with_sparse.baseline_at(1000) == pytest.approx(0.5), "sparse bands are skipped"

    no_changes = growth_stats(farm({"c1": 1, "c2": 1}))
    assert no_changes.gains == (0.0,) and no_changes.clip == DEFAULT_CLIP


@pytest.mark.unit
def test_projection_bends_as_animals_get_heavier():
    stats = GrowthStats((425.0, 675.0), (2.0, 0.5), (-10.0, 10.0))
    out = recursive_forecast(None, np.full((1, WINDOW), 450.0), stats, days=120, fade_days=0)
    changes = np.diff(np.concatenate([[450.0], out[0]]))
    assert changes[0] == pytest.approx(1.85)  # interpolated between 2.0 at 425 kg and 0.5 at 675 kg
    assert np.all(np.diff(changes) <= 1e-9), "daily gain never rises as the animal gets heavier"
    assert changes[-1] < 1.0


@pytest.mark.unit
def test_forecast_farm_rows_start_the_day_after_each_animals_last_record():
    df = pd.concat([farm({"long": 30}), farm({"short": 3}, end="2026-08-01")], ignore_index=True)
    out, _ = forecast_farm(df, ConstantBooster(0.0), days=10)
    assert len(out) == 20
    short = out[out.eid == "short"].sort_values("horizon")
    assert short["tier"].iloc[0] == TIER_SHORT
    assert short["date"].iloc[0] == pd.Timestamp("2026-08-02")
    assert list(short["horizon"]) == list(range(1, 11))


@pytest.mark.unit
def test_forecast_farm_uses_the_model_for_the_given_tested_range():
    class CountingBooster(ConstantBooster):
        calls = 0

        def inplace_predict(self, x: np.ndarray) -> np.ndarray:
            self.calls += 1
            return super().inplace_predict(x)

    df = farm({"a": 30})
    for fade_days in (5, 35, FADE_DAYS):
        booster = CountingBooster(0.0)
        forecast_farm(df, booster, days=60, fade_days=fade_days)
        assert booster.calls == fade_days  # one call per day while the model still has a share
