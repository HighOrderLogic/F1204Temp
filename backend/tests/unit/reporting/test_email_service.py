from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.services.email_service import compute_next_send, generate_filter_summary

TZ = ZoneInfo("Australia/Sydney")


@pytest.mark.unit
def test_compute_next_send_daily():
    """US14: Verifies next send calculation for daily schedule."""
    now = datetime(2026, 10, 13, 8, 0, tzinfo=TZ)
    result = compute_next_send({"frequency": "daily", "send_time": "09:00"}, now=now)
    assert result == datetime(2026, 10, 13, 9, 0, tzinfo=TZ)


@pytest.mark.unit
def test_compute_next_send_daily_rolls_to_tomorrow():
    now = datetime(2026, 10, 13, 10, 48, tzinfo=TZ)
    result = compute_next_send({"frequency": "daily", "send_time": "09:00"}, now=now)
    assert result == datetime(2026, 10, 14, 9, 0, tzinfo=TZ)


@pytest.mark.unit
def test_compute_next_send_weekly():
    """US14: Verifies next send calculation for weekly schedule."""
    # 2026-10-13 is a Tuesday (isoweekday=2)
    now = datetime(2026, 10, 13, 8, 0, tzinfo=TZ)
    result = compute_next_send(
        {
            "frequency": "weekly",
            "send_time": time(9, 0),
            "day_of_week": 1,
        },
        now=now,
    )
    assert result == datetime(2026, 10, 19, 9, 0, tzinfo=TZ)


@pytest.mark.unit
def test_generate_filter_summary_multi_measure():
    """US14: Verifies filter summary correctly concatenates multiple measures."""
    filters = {
        "farm_id": "KF",
        "year": 2023,
        "measures": ["finalpweight", "methane"],
        "sex": ["Heifer"],
    }
    summary = generate_filter_summary(filters)
    assert "Final processed weight (kg)" in summary["measure"]
    assert "Methane production (g/day)" in summary["measure"]
    assert summary["sex"] == "Heifer"


@pytest.mark.unit
def test_generate_filter_summary_single_measure():
    """US14: Verifies single-measure fallback works as expected."""
    filters = {
        "farm_id": "KF",
        "year": 2023,
        "measure": "finalpweight",
    }
    summary = generate_filter_summary(filters)
    assert summary["measure"] == "Final processed weight (kg)"

