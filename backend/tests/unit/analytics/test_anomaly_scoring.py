from __future__ import annotations

import datetime
import numpy as np
import pandas as pd
import pytest

from app.services.anomaly_service import (
    ANOMALY_CUSUM_MIN_BASELINE,
    ANOMALY_GROWTH_MIN_READINGS,
    ANOMALY_GROWTH_MIN_SPAN_DAYS,
    ANOMALY_MIN_RESIDUALS,
    ANOMALY_WINDOW_DAYS,
    adjust_herd,
    calibrate_cusum,
    classify_anomalies,
    cusum_path,
    estimate_noise,
    herd_effect_series,
    latest_weekly_change,
    prepare_weights,
    run_anomaly_scoring,
    score_cusum,
    score_peer_growth,
    score_point,
)


def make_herd(
    n: int = 20,
    days: int = 28,
    adg: float = 1.0,
    noise_sd: float = 0.0,
    end: datetime.date = datetime.date(2026, 9, 21),
    seed: int = 1,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = [end - datetime.timedelta(days=d) for d in range(days - 1, -1, -1)]
    records = []
    eids = [f"A{i:02d}" for i in range(1, n + 1)]

    for idx, eid in enumerate(eids):
        start_w = 400.0 + 10.0 * idx
        for day_idx, d in enumerate(dates):
            noise = float(rng.normal(0, noise_sd)) if noise_sd > 0 else 0.0
            w = start_w + adg * day_idx + noise
            breed = "AA" if (idx * len(dates) + day_idx) % 2 == 0 else "AXH"
            records.append({"eid": eid, "date": d, "weight": w, "breed": breed})

    return pd.DataFrame(records)


def flat_herd(n: int = 20) -> pd.DataFrame:
    return adjust_herd(prepare_weights(make_herd(n=n, adg=1.0, noise_sd=0.0)))


def noisy_herd(n: int = 60, seed: int = 12) -> tuple[pd.DataFrame, float]:
    d = adjust_herd(prepare_weights(make_herd(n=n, adg=1.0, noise_sd=8.0, seed=seed)))
    sigma = estimate_noise(d)["sigma"]
    return d, sigma


# ============================================================
# Step 1 Tests: prepare_weights & adjust_herd
# ============================================================

def test_prepare_weights_keeps_latest_28_days_and_adds_day_week():
    df = make_herd(n=2, days=40)
    d = prepare_weights(df)
    assert d["date"].nunique() == 28
    assert d["day"].min() == 1
    assert d["day"].max() == 28
    assert sorted(d["week"].unique().tolist()) == [1, 2, 3, 4]
    assert d["date"].max() == df["date"].max()


def test_prepare_weights_averages_same_day_readings():
    df = pd.DataFrame({
        "eid": ["A", "A", "A"],
        "date": [datetime.date(2026, 9, 1), datetime.date(2026, 9, 1), datetime.date(2026, 9, 2)],
        "weight": [400.0, 410.0, 420.0],
    })
    d = prepare_weights(df)
    assert len(d) == 2
    match_row = d[d["date"] == datetime.date(2026, 9, 1)]
    assert match_row["weight"].iloc[0] == 405.0


def test_prepare_weights_drops_missing_weights_and_supports_custom_columns():
    dates = [datetime.date(2026, 9, 1) + datetime.timedelta(days=i) for i in range(3)]
    df = pd.DataFrame({"EID": ["A", "A", "B"], "Date": dates, "Weight": [400.0, np.nan, 300.0]})
    d = prepare_weights(df, id_col="EID", date_col="Date", weight_col="Weight")
    assert len(d) == 2
    assert not d["weight"].isna().any()


def test_prepare_weights_errors_clearly_when_column_missing():
    with pytest.raises(ValueError, match="weight"):
        prepare_weights(pd.DataFrame({"eid": ["A"], "date": [datetime.date.today()]}))


def test_prepare_weights_normalises_blank_and_unknown_groups():
    df = pd.DataFrame({
        "eid": ["A", "B", "C"],
        "date": [datetime.date(2026, 9, 1)] * 3,
        "weight": [400.0, 400.0, 400.0],
        "mob": ["Front", "", "Unknown"],
    })
    d = prepare_weights(df, group_cols=["mob", "not_a_column"])
    # pandas 3 stores missing group values as NaN rather than None; both mean "unrecorded".
    vals = d["mob"].tolist()
    assert vals[0] == "Front"
    assert pd.isna(vals[1]) and pd.isna(vals[2])
    assert "not_a_column" not in d.columns


def test_prepare_weights_handles_empty_dataframe():
    d = prepare_weights(pd.DataFrame({"eid": [], "date": [], "weight": []}))
    assert len(d) == 0
    assert "day" in d.columns
    assert "week" in d.columns


def test_adjust_herd_removes_shared_day_shift():
    df = make_herd(n=20, adg=1.0)
    shifted_day = df["date"].max() - datetime.timedelta(days=10)
    df.loc[df["date"] == shifted_day, "weight"] -= 25.0
    d = adjust_herd(prepare_weights(df))

    # Without noise every animal grows identically, so adj - mean(adj per eid) is identical
    means = d.groupby("eid")["adj"].transform("mean")
    spread_by_day = d.groupby("day")["adj"].apply(lambda s: (s - means.loc[s.index]).max() - (s - means.loc[s.index]).min())
    assert (spread_by_day < 1e-8).all()

    he = herd_effect_series(d)
    val_shifted = he.loc[he["date"] == shifted_day, "herd_effect"].iloc[0]
    val_prev = he.loc[he["date"] == shifted_day - datetime.timedelta(days=1), "herd_effect"].iloc[0]
    assert val_shifted < val_prev - 20.0


def test_adjust_herd_preserves_individual_animal_drop():
    df = make_herd(n=20, adg=1.0)
    late = df["date"] >= df["date"].max() - datetime.timedelta(days=6)
    df.loc[(df["eid"] == "A01") & late, "weight"] -= 30.0
    d = adjust_herd(prepare_weights(df))

    a01 = d[d["eid"] == "A01"]
    a02 = d[d["eid"] == "A02"]
    drop_a01 = a01.loc[a01["week"] == 4, "adj"].mean() - a01.loc[a01["week"] == 1, "adj"].mean()
    drop_a02 = a02.loc[a02["week"] == 4, "adj"].mean() - a02.loc[a02["week"] == 1, "adj"].mean()
    assert (drop_a01 - drop_a02) < -25.0


def test_adjust_herd_skips_when_too_few_animals():
    df = make_herd(n=3)
    d = adjust_herd(prepare_weights(df))
    assert (d["herd_effect"] == 0.0).all()
    assert (d["adj"] == d["weight"]).all()


# ============================================================
# Step 2 Tests: estimate_noise & score_point
# ============================================================

def test_estimate_noise_recovers_true_sd():
    df = make_herd(n=40, adg=1.0, noise_sd=8.0, seed=2)
    noise = estimate_noise(adjust_herd(prepare_weights(df)))
    assert noise["sigma"] is not None
    assert noise["sigma"] > 8.0 * 0.8
    assert noise["sigma"] < 8.0 * 1.1


def test_estimate_noise_is_not_inflated_by_extreme_outliers():
    df = make_herd(n=40, adg=1.0, noise_sd=8.0, seed=3)
    base_sigma = estimate_noise(adjust_herd(prepare_weights(df)))["sigma"]
    rng = np.random.default_rng(42)
    extreme_indices = rng.choice(len(df), size=10, replace=False)
    df.loc[extreme_indices, "weight"] -= 200.0
    spiked = estimate_noise(adjust_herd(prepare_weights(df)))

    assert abs(spiked["sigma"] - base_sigma) / base_sigma < 0.10
    assert spiked["data"]["resid"].std() > 1.5 * base_sigma


def test_estimate_noise_leaves_sparse_readings_as_nan():
    df = make_herd(n=40, adg=1.0, noise_sd=8.0)
    # A99 weighed only every 5th day: never 4 readings within +/- 3 days
    sparse_dates = [df["date"].max() - datetime.timedelta(days=d) for d in range(0, 26, 5)]
    sparse = pd.DataFrame({"eid": ["A99"] * len(sparse_dates), "date": sparse_dates, "weight": [500.0] * len(sparse_dates), "breed": ["AA"] * len(sparse_dates)})
    combined = pd.concat([df, sparse], ignore_index=True)
    noise = estimate_noise(adjust_herd(prepare_weights(combined)))
    a99_resids = noise["data"].loc[noise["data"]["eid"] == "A99", "resid"]
    assert a99_resids.isna().all()
    assert noise["sigma"] is not None


def test_estimate_noise_returns_none_when_under_200_residuals():
    df = make_herd(n=3, days=28, noise_sd=5.0)
    noise = estimate_noise(adjust_herd(prepare_weights(df)))
    assert noise["n_residuals"] < ANOMALY_MIN_RESIDUALS
    assert noise["sigma"] is None


def test_estimate_noise_handles_empty_dataframe():
    empty_df = pd.DataFrame({"eid": [], "date": [], "weight": []})
    noise = estimate_noise(adjust_herd(prepare_weights(empty_df)))
    assert noise["sigma"] is None
    assert noise["n_residuals"] == 0


def test_score_point_finds_single_low_day():
    df = make_herd(n=40, adg=1.0, noise_sd=8.0, seed=4)
    dip_date = df["date"].max() - datetime.timedelta(days=12)
    df.loc[(df["eid"] == "A05") & (df["date"] == dip_date), "weight"] -= 80.0
    noise = estimate_noise(adjust_herd(prepare_weights(df)))
    pts = score_point(noise["data"], noise["sigma"])

    a05 = pts["scores"][pts["scores"]["eid"] == "A05"].iloc[0]
    assert a05["point_date"] == dip_date
    lowest_eid = pts["scores"].loc[pts["scores"]["point_z"].idxmin(), "eid"]
    assert lowest_eid == "A05"
    assert a05["point_z"] < pts["cutoffs"]["Low"]


def test_score_point_cutoffs_monotonic_order():
    df = make_herd(n=40, adg=1.0, noise_sd=8.0, seed=5)
    noise = estimate_noise(adjust_herd(prepare_weights(df)))
    cut = score_point(noise["data"], noise["sigma"])["cutoffs"]
    assert set(cut.keys()) == {"High", "Medium", "Low"}
    assert cut["High"] > cut["Medium"] > cut["Low"]


def test_score_point_returns_empty_when_sigma_none():
    df = make_herd(n=3, noise_sd=5.0)
    noise = estimate_noise(adjust_herd(prepare_weights(df)))
    pts = score_point(noise["data"], noise["sigma"])
    assert len(pts["scores"]) == 0
    assert all(v is None for v in pts["cutoffs"].values())


# ============================================================
# Step 3 Tests: score_peer_growth
# ============================================================

def make_growth_herd(adg: list[float], breed: list[str] | None = None, noise_sd: float = 3.0, seed: int = 6) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ids = [f"G{i:02d}" for i in range(1, len(adg) + 1)]
    dates = [datetime.date(2026, 9, 21) - datetime.timedelta(days=d) for d in range(27, -1, -1)]
    records = []
    if breed is None:
        breed = ["AA"] * len(adg)

    for i, eid in enumerate(ids):
        rate = adg[i]
        b = breed[i]
        for day_idx, d in enumerate(dates):
            noise = float(rng.normal(0, noise_sd)) if noise_sd > 0 else 0.0
            w = 450.0 + rate * day_idx + noise
            records.append({"eid": eid, "date": d, "weight": w, "breed": b})

    df = pd.DataFrame(records)
    return adjust_herd(prepare_weights(df, group_cols=["breed"]))


def test_score_peer_growth_flags_stalled_growth():
    rng = np.random.default_rng(7)
    adg = [0.0] + list(rng.normal(1.0, 0.15, 29))
    pg = score_peer_growth(make_growth_herd(adg), group_cols=["breed"])
    g01 = pg[pg["eid"] == "G01"].iloc[0]

    assert g01["peer_definition"] == "breed = AA"
    assert g01["n_peers"] == 30
    assert g01["growth_slope"] < -0.8
    assert g01["peer_z"] < -3.0
    assert pg.loc[pg["peer_z"].idxmin(), "eid"] == "G01"
    assert abs(g01["raw_growth"]) < 0.3
    assert 0.8 < g01["peer_raw_median"] < 1.2


def test_score_peer_growth_falls_back_to_whole_herd_for_small_groups():
    rng = np.random.default_rng(8)
    adg = list(rng.normal(1.0, 0.15, 17))
    breed = ["AA"] * 12 + ["AXH"] * 5
    pg = score_peer_growth(make_growth_herd(adg, breed=breed), group_cols=["breed"])

    aa_eids = [f"G{i:02d}" for i in range(1, 13)]
    axh_eids = [f"G{i:02d}" for i in range(13, 18)]
    assert (pg.loc[pg["eid"].isin(aa_eids), "peer_definition"] == "breed = AA").all()
    assert (pg.loc[pg["eid"].isin(axh_eids), "peer_definition"] == "whole herd").all()
    assert (pg.loc[pg["peer_definition"] == "whole herd", "n_peers"] == 17).all()


def test_score_peer_growth_sends_missing_group_values_to_fallback():
    rng = np.random.default_rng(9)
    adg = list(rng.normal(1.0, 0.15, 15))
    breed = [None] + ["AA"] * 14
    pg = score_peer_growth(make_growth_herd(adg, breed=breed), group_cols=["breed"])
    assert pg.loc[pg["eid"] == "G01", "peer_definition"].iloc[0] == "whole herd"
    assert (pg.loc[pg["eid"] != "G01", "peer_definition"] == "breed = AA").all()


def test_score_peer_growth_uses_whole_herd_with_no_group_cols():
    rng = np.random.default_rng(10)
    adg = list(rng.normal(1.0, 0.15, 12))
    pg = score_peer_growth(make_growth_herd(adg))
    assert (pg["peer_definition"] == "whole herd").all()


def test_score_peer_growth_requires_minimum_readings_and_span():
    rng = np.random.default_rng(11)
    adg = list(rng.normal(1.0, 0.15, 12))
    d = make_growth_herd(adg)
    # G01: 9 readings only
    d = d[~((d["eid"] == "G01") & (d["day"] > 9))]
    # G02: 19 readings but spans only 18 days
    d = d[~((d["eid"] == "G02") & (d["day"] < 10))]

    pg = score_peer_growth(d)
    g01 = pg[pg["eid"] == "G01"].iloc[0]
    g02 = pg[pg["eid"] == "G02"].iloc[0]
    assert pd.isna(g01["growth_slope"]) and pd.isna(g01["peer_z"])
    assert pd.isna(g02["growth_slope"]) and pd.isna(g02["peer_z"])
    assert pg["peer_z"].notna().sum() == 10

    # Under 10 animals in entire herd
    small_adg = list(rng.normal(1.0, 0.15, 9))
    small = score_peer_growth(make_growth_herd(small_adg))
    assert small["peer_z"].isna().all()
    assert small["peer_definition"].isna().all()


def test_score_peer_growth_returns_nan_on_identical_growth():
    pg = score_peer_growth(make_growth_herd([1.0] * 12, noise_sd=0.0))
    assert pg["peer_z"].isna().all()


def test_score_peer_growth_handles_empty_dataframe():
    empty_df = pd.DataFrame({"eid": [], "date": [], "weight": []})
    pg = score_peer_growth(adjust_herd(prepare_weights(empty_df)))
    assert len(pg) == 0
    assert "peer_z" in pg.columns


# ============================================================
# Step 4 Tests: cusum_path, score_cusum & calibrate_cusum
# ============================================================

def test_cusum_path_accumulates_and_drains():
    res1 = cusum_path([-2.0, -2.0, -2.0])
    np.testing.assert_allclose(res1, [1.5, 3.0, 4.5])

    res2 = cusum_path([-2.0, 0.0, 0.0, 0.0, 0.0])
    np.testing.assert_allclose(res2, [1.5, 1.0, 0.5, 0.0, 0.0])

    res3 = cusum_path([1.0, 0.4, -0.4])
    np.testing.assert_allclose(res3, [0.0, 0.0, 0.0])


def test_score_cusum_returns_zero_for_flat_herd():
    cs = score_cusum(flat_herd(), sigma=5.0)
    assert (cs["scores"]["cusum_max"] == 0.0).all()
    assert cs["scores"]["run_start"].isna().all()


def test_score_cusum_accumulates_for_sustained_drop():
    d = flat_herd()
    hit = (d["eid"] == "A01") & (d["week"] >= 3)
    d.loc[hit, "adj"] -= 10.0  # 2 sigma with sigma = 5
    cs = score_cusum(d, sigma=5.0)

    a01 = cs["scores"][cs["scores"]["eid"] == "A01"].iloc[0]
    assert a01["cusum_max"] == 14 * 1.5
    assert a01["run_start"] == d.loc[d["week"] == 3, "date"].min()
    assert a01["run_days"] == 14
    assert a01["shortfall_kg"] == 10.0
    assert (cs["scores"].loc[cs["scores"]["eid"] != "A01", "cusum_max"] == 0.0).all()


def test_score_cusum_caps_single_extreme_day():
    d = flat_herd()
    one_day = (d["eid"] == "A01") & (d["day"] == 20)
    d.loc[one_day, "adj"] -= 100.0  # 20 sigma, capped to 2
    cs = score_cusum(d, sigma=5.0)
    a01 = cs["scores"][cs["scores"]["eid"] == "A01"].iloc[0]
    assert a01["cusum_max"] == 1.5
    assert a01["run_days"] == 3  # 1.5, 1.0, 0.5 then 0


def test_score_cusum_skips_sparse_animals():
    d = flat_herd()
    # A01: drop 8 baseline readings -> only 6 baseline readings remaining
    d = d[~((d["eid"] == "A01") & (d["day"].isin(range(1, 9))))]
    # A02: drop readings >= day 17 -> only 2 monitored readings remaining
    d = d[~((d["eid"] == "A02") & (d["day"] >= 17))]
    cs = score_cusum(d, sigma=5.0)
    assert "A01" not in cs["scores"]["eid"].values
    assert "A02" not in cs["scores"]["eid"].values
    assert len(cs["scores"]) == 18
    assert all(c in cs["path"].columns for c in ["eid", "date", "cusum"])


def test_score_cusum_returns_empty_without_sigma():
    cs = score_cusum(flat_herd(), sigma=None)
    assert len(cs["scores"]) == 0
    assert len(cs["path"]) == 0


def test_calibrate_cusum_reproducible_and_preserves_rng_state():
    d, sigma = noisy_herd()
    np_state_before = np.random.get_state()
    lim1 = calibrate_cusum(d, sigma)
    np_state_after = np.random.get_state()

    # Verify global RNG state was not altered
    assert np_state_before[1].tolist() == np_state_after[1].tolist()

    lim2 = calibrate_cusum(d, sigma)
    assert lim1 == lim2
    assert set(lim1.keys()) == {"High", "Medium", "Low"}
    assert lim1["High"] < lim1["Medium"] < lim1["Low"]


def test_calibrated_cusum_flags_growth_stalls_rarely_flags_healthy():
    d, sigma = noisy_herd(n=60, seed=13)
    lim = calibrate_cusum(d, sigma)
    healthy = score_cusum(d, sigma)["scores"]
    assert (healthy["cusum_max"] > lim["Medium"]).mean() <= 0.10

    stalled = [f"A{i:02d}" for i in range(1, 6)]
    d_stalled = d.copy()
    hit = d_stalled["eid"].isin(stalled) & (d_stalled["day"] >= 15)
    d_stalled.loc[hit, "adj"] -= 1.6 * (d_stalled.loc[hit, "day"] - 14)
    sc = score_cusum(d_stalled, sigma)["scores"]
    flagged = sc.loc[sc["eid"].isin(stalled), "cusum_max"] > lim["Medium"]
    assert flagged.sum() >= 4


def test_calibrate_cusum_returns_none_limits_without_sigma_or_data():
    assert all(v is None for v in calibrate_cusum(flat_herd(), None).values())
    empty_df = flat_herd().iloc[0:0]
    assert all(v is None for v in calibrate_cusum(empty_df, 5.0).values())


# ============================================================
# Step 5 Tests: latest_weekly_change & classify_anomalies
# ============================================================

def test_latest_weekly_change_compares_last_two_weeks():
    d = prepare_weights(make_herd(n=2, adg=1.0, noise_sd=0.0))
    wc = latest_weekly_change(d)
    assert wc["change_kg_7d"].tolist() == [7.0, 7.0]
    np.testing.assert_allclose(wc["change_pct_7d"].iloc[0], 100.0 * 7.0 / (400.0 + 17.0))


def test_latest_weekly_change_requires_3_readings_each_week():
    d = prepare_weights(make_herd(n=2, adg=1.0, noise_sd=0.0))
    d = d[~((d["eid"] == "A01") & (d["week"] == 4) & (d["day"] > 23))]
    wc = latest_weekly_change(d)
    a01 = wc.loc[wc["eid"] == "A01", "change_kg_7d"].iloc[0]
    a02 = wc.loc[wc["eid"] == "A02", "change_kg_7d"].iloc[0]
    assert pd.isna(a01)
    assert not pd.isna(a02)


def _fake_scores_bundle() -> tuple[dict, pd.DataFrame]:
    d = prepare_weights(make_herd(n=4, adg=1.0, noise_sd=0.0))
    d["adj"] = d["weight"]
    end = d["date"].max()

    scores = {
        "data": d,
        "window_start": d["date"].min(),
        "window_end": end,
        "sigma": 7.0,
        "weekly": latest_weekly_change(d),
        "point": {
            "scores": pd.DataFrame({
                "eid": ["A01", "A02", "A03", "A04"],
                "point_z": [-1.0, -1.0, -9.0, -1.0],
                "point_date": [end - datetime.timedelta(days=5)] * 4,
                "point_resid_kg": [-7.0, -7.0, -63.0, -7.0],
            }),
            "cutoffs": {"High": -5.0, "Medium": -8.0, "Low": -10.0},
        },
        "cusum": {
            "scores": pd.DataFrame({
                "eid": ["A01", "A02", "A03"],
                "cusum_max": [15.0, 0.0, 0.0],
                "run_start": [end - datetime.timedelta(days=10), None, None],
                "run_end": [end, None, None],
                "run_days": [11, 0, 0],
                "shortfall_kg": [18.0, 0.0, 0.0],
            }),
            "path": pd.DataFrame(),
        },
        "cusum_limits": {"High": 7.0, "Medium": 9.0, "Low": 16.0},
    }

    peer = pd.DataFrame({
        "eid": ["A01", "A02", "A03", "A04"],
        "n_readings": [28, 28, 28, 28],
        "growth_slope": [-1.0, -1.5, 0.0, np.nan],
        "raw_growth": [-0.2, -0.6, 0.8, np.nan],
        "peer_definition": ["breed = AA", "breed = AA", "breed = AA", None],
        "n_peers": [30, 30, 30, np.nan],
        "peer_median": [0.0, 0.0, 0.0, np.nan],
        "peer_raw_median": [0.8, 0.8, 0.8, np.nan],
        "peer_z": [-2.7, -3.2, 0.0, np.nan],
    })

    return scores, peer


def test_classify_anomalies_precedence_and_reasons():
    scores, peer = _fake_scores_bundle()
    cl = classify_anomalies(scores, peer, "Medium")
    sev_map = dict(zip(cl["eid"], cl["severity"]))

    assert sev_map["A01"] == "High"
    assert sev_map["A02"] == "Medium"
    assert sev_map["A03"] == "Low"
    assert sev_map["A04"] == "Not enough data"

    a01 = cl[cl["eid"] == "A01"].iloc[0]
    assert a01["anomaly_types"] == "Sustained decline, Low growth vs peers"
    assert "Falling behind the herd: since" in a01["reason"]
    assert "18.0 kg further behind" in a01["reason"]
    assert "for 11 days, and it has not caught up" in a01["reason"]
    assert "Low growth compared with similar animals: growing -0.2 kg/day this month" in a01["reason"]
    assert cl["eid"].iloc[0] == "A01"


def test_classify_anomalies_responds_to_sensitivity():
    scores, peer = _fake_scores_bundle()
    cl_low = classify_anomalies(scores, peer, "Low")
    cl_high = classify_anomalies(scores, peer, "High")

    sev_low = dict(zip(cl_low["eid"], cl_low["severity"]))
    assert sev_low["A01"] == "Normal"
    assert sev_low["A03"] == "Normal"
    assert sev_low["A02"] == "Medium"

    high_flagged = (cl_high["severity"].isin(["High", "Medium", "Low"])).sum()
    low_flagged = (cl_low["severity"].isin(["High", "Medium", "Low"])).sum()
    assert high_flagged >= low_flagged

    with pytest.raises(ValueError, match="sensitivity"):
        classify_anomalies(scores, peer, "Extreme")  # type: ignore[arg-type]


def test_classify_anomalies_explains_not_enough_data():
    scores, peer = _fake_scores_bundle()
    cl = classify_anomalies(scores, peer, "Medium")
    a04_reason = cl.loc[cl["eid"] == "A04", "reason"].iloc[0]
    assert "Not enough data: the month-long checks need at least 10 readings spread over 21 days" in a04_reason
    assert "this animal has 28 readings in the latest 28 days" in a04_reason


def test_run_anomaly_scoring_and_classify_anomalies_end_to_end():
    df = make_herd(n=60, adg=1.0, noise_sd=8.0, seed=14)
    late = (df["eid"] == "A01") & (df["date"] >= df["date"].max() - datetime.timedelta(days=13))
    date_offsets = (df.loc[late, "date"] - (df["date"].max() - datetime.timedelta(days=14))).apply(lambda delta: delta.days)
    df.loc[late, "weight"] -= 1.6 * date_offsets

    s = run_anomaly_scoring(df, group_cols=["breed"])
    assert s["window_end"] == df["date"].max()
    assert s["sigma"] is not None

    peer = score_peer_growth(s["data"], ["breed"])
    cl = classify_anomalies(s, peer, "Medium")
    assert len(cl) == 60
    assert cl.loc[cl["eid"] == "A01", "severity"].iloc[0] == "High"
    assert (cl["severity"] == "High").sum() <= 6


def test_sparse_uploads_end_up_as_not_enough_data_without_errors():
    eids = [f"S{i:02d}" for i in range(1, 31)]
    records = []
    base_date = datetime.date(2026, 9, 21)
    for eid in eids:
        for offset in [0, 9, 20]:
            records.append({"eid": eid, "date": base_date - datetime.timedelta(days=offset), "weight": 400.0})
    df = pd.DataFrame(records)

    s = run_anomaly_scoring(df)
    peer = score_peer_growth(s["data"])
    cl = classify_anomalies(s, peer, "Medium")
    assert len(cl) == 30
    assert (cl["severity"] == "Not enough data").all()
