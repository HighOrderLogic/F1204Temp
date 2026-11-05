from __future__ import annotations

import pandas as pd
import pytest

from app.models.schemas import FilterState
from app.services.chart_service import CohortService


@pytest.mark.unit
def test_cohort_empty_dataframe_safe():
    """US12: Verifies that an empty DataFrame safely returns zero counts without throwing ZeroDivisionError."""
    empty_df = pd.DataFrame(columns=["eid", "date", "finalpweight"])
    f = FilterState(farm_id="KF", year=2023)
    res = CohortService.analyze(empty_df, empty_df, "finalpweight", 10, f)
    assert res["total_animals"] == 0
    assert res["top"]["count"] == 0
    assert res["bottom"]["count"] == 0


@pytest.mark.unit
def test_cohort_percentile_clamping(cohort_sample_df: pd.DataFrame):
    """US12: Verifies that percentiles are clamped between 10 and 20."""
    f = FilterState(farm_id="KF", year=2023)
    res_high = CohortService.analyze(cohort_sample_df, cohort_sample_df, "finalpweight", 99, f)
    assert res_high["percentile"] == 20
    res_low = CohortService.analyze(cohort_sample_df, cohort_sample_df, "finalpweight", 1, f)
    assert res_low["percentile"] == 10


@pytest.mark.unit
def test_cohort_baseline_top_bottom(cohort_sample_df: pd.DataFrame):
    """US12: Baseline test verifying current 2-tier top and bottom extraction."""
    f = FilterState(farm_id="KF", year=2023)
    res = CohortService.analyze(cohort_sample_df, cohort_sample_df, "finalpweight", 10, f)
    assert "top" in res
    assert "bottom" in res
    assert res["top"]["count"] >= 1
    assert res["bottom"]["count"] >= 1


@pytest.mark.unit
def test_cohort_three_tier_partitioning(cohort_sample_df: pd.DataFrame):
    """US12: Verifies output partitions population into High, Medium, and Low cohorts."""
    f = FilterState(farm_id="KF", year=2023)
    res = CohortService.analyze(cohort_sample_df, cohort_sample_df, "finalpweight", 10, f)
    assert "high" in res
    assert "medium" in res
    assert "low" in res
    # Sum of cohorts must equal qualified animal count
    total_classified = res["high"]["count"] + res["medium"]["count"] + res["low"]["count"]
    assert total_classified == 9, "All 9 qualified animals must be assigned to high, medium, or low"


@pytest.mark.unit
def test_cohort_longitudinal_guard(cohort_sample_df: pd.DataFrame):
    """US12: Verifies that animals with only 1 observation (EID_10) are excluded from cohort assignment."""
    f = FilterState(farm_id="KF", year=2023)
    res = CohortService.analyze(cohort_sample_df, cohort_sample_df, "finalpweight", 10, f)
    # EID_10 has weight=700 (highest in dataset), but must NOT be in High cohort due to n=1
    high_eids = [a["eid"] for a in res["high"]["animals"]]
    assert "EID_10" not in high_eids, "Single-measurement animal must not qualify for cohort assignment"


@pytest.mark.unit
def test_cohort_disjoint_partitions(cohort_sample_df: pd.DataFrame):
    """US12: Verifies zero intersection between High and Low cohorts."""
    f = FilterState(farm_id="KF", year=2023)
    res = CohortService.analyze(cohort_sample_df, cohort_sample_df, "finalpweight", 10, f)
    high_set = {a["eid"] for a in res["high"]["animals"]}
    low_set = {a["eid"] for a in res["low"]["animals"]}
    assert high_set.isdisjoint(low_set) is True


@pytest.mark.unit
def test_cohort_timeline_three_curves(cohort_sample_df: pd.DataFrame):
    """US13: Verifies timeline contains trajectory data points for high, medium, and low cohorts."""
    f = FilterState(farm_id="KF", year=2023)
    res = CohortService.analyze(cohort_sample_df, cohort_sample_df, "finalpweight", 10, f)
    cohorts_in_timeline = {t["cohort"] for t in res["timeline"]}
    assert cohorts_in_timeline == {"high", "medium", "low"}

@pytest.mark.unit
def test_cohort_enriched_animal_metadata(cohort_sample_df: pd.DataFrame):
    """PR #75 parity: Verifies cohort members contain grouping metadata, latest_date, latest_value, and record counts."""
    f = FilterState(farm_id="KF", year=2023)
    res = CohortService.analyze(cohort_sample_df, cohort_sample_df, "finalpweight", 10, f)
    top_animal = res["high"]["animals"][0]
    assert "eid" in top_animal
    assert "avg_measure" in top_animal
    assert "latest_date" in top_animal
    assert "latest_value" in top_animal
    assert "total_records" in top_animal
    assert top_animal["total_records"] >= 2
    assert "sex" in top_animal
    assert "breed" in top_animal
    assert "mob" in top_animal
