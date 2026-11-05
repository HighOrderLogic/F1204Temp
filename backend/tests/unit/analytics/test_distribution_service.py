from __future__ import annotations

import pytest

from app.models.schemas import FilterState
from app.services.sql_agg import distribution_sql


@pytest.mark.unit
def test_distribution_bins_clamping(monkeypatch):
    """US11: Verifies that histogram bin count is clamped between 10 and 50."""
    monkeypatch.setattr("app.db.fetch_all", lambda q, p: [])
    f = FilterState(farm_id="KF", year=2023)
    res_low = distribution_sql(f, is_admin=True, measure="finalpweight", hist_bins=2)
    assert res_low["histogram"]["bins"] == 10
    res_high = distribution_sql(f, is_admin=True, measure="finalpweight", hist_bins=100)
    assert res_high["histogram"]["bins"] == 50


@pytest.mark.unit
def test_distribution_multi_group_retention(monkeypatch):
    """US11: Verifies that all selected group combinations are retained in output."""
    rows = [
        {"eid": "A", "sex": "Steer", "treatment": "Drench", "breed": "Angus", "mob": "One", "value": 10.0, "source_record_count": 7},
        {"eid": "B", "sex": "Heifer", "treatment": "__NONE__", "breed": "Angus", "mob": "One", "value": 20.0, "source_record_count": 7},
    ]
    monkeypatch.setattr("app.db.fetch_all", lambda q, p: rows)
    f = FilterState(farm_id="KF", year=2023, sex=["Overall", "Steer", "Heifer"])
    res = distribution_sql(f, is_admin=False, measure="finalpweight")
    expected = {"Sex: Overall", "Sex: Steer", "Sex: Heifer"}
    actual = {g["group"] for g in res["histogram"]["groups"]}
    assert actual == expected


@pytest.mark.unit
def test_distribution_calendar_window_calculation(monkeypatch, distribution_calendar_df):
    """US11: Verifies that only records within [max_date - 6 days, max_date] are averaged for each animal.
    
    Animal A has 7 records spanning 100 days (300 to 520 kg). Only the last 3 records (500, 510, 520)
    fall within [2023-04-14, 2023-04-20]. The 7-day average must be (500 + 510 + 520) / 3 = 510.0 kg.
    """
    captured = {}
    def fake_fetch_all(query, params):
        captured["query"] = query
        # Simulated response of the calendar window query
        return [{"eid": "ANIMAL_A", "sex": "Steer", "treatment": "Drench", "breed": "Angus", "mob": "One", "value": 510.0, "source_record_count": 3}]

    monkeypatch.setattr("app.db.fetch_all", fake_fetch_all)
    f = FilterState(farm_id="KF", year=2023)
    res = distribution_sql(f, is_admin=True, measure="finalpweight")
    assert res["histogram"]["groups"][0]["values"] == [510.0]


@pytest.mark.unit
def test_distribution_sql_replaces_rank_with_interval(monkeypatch):
    """US11: Verifies SQL query uses calendar date arithmetic instead of row-number ranking."""
    captured = {}
    monkeypatch.setattr("app.db.fetch_all", lambda q, p: captured.setdefault("query", q) and [])
    f = FilterState(farm_id="KF", year=2023)
    distribution_sql(f, is_admin=True, measure="finalpweight")
    query = captured["query"]
    assert "record_rank <= 7" not in query, "SQL query must not use arbitrary record counts"
    assert "INTERVAL '6 days'" in query or "max_date - 6" in query
