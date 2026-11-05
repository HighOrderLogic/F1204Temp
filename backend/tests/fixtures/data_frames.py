from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture
def sample_df() -> pd.DataFrame:
    """Standard 20-record test DataFrame with varied dimensions and dates."""
    dates = pd.date_range("2023-01-01", periods=20, freq="D")
    rows = []
    for i, d in enumerate(dates):
        rows.append(
            {
                "eid": f"982 123536700{i:03d}",
                "date": d,
                "breed": "Brahman X" if i % 2 == 0 else "Angus",
                "treatment": "Drench" if i % 3 == 0 else None,
                "mob": "Front" if i < 10 else "Back",
                "sex": "Steer" if i % 2 == 0 else "Heifer",
                "finalpweight": 500.0 + i * 5.0,
                "methane": 100.0 + i,
            }
        )
    return pd.DataFrame(rows)


@pytest.fixture
def cohort_sample_df() -> pd.DataFrame:
    """10 animals with varied record counts and longitudinal averages for cohort testing."""
    rows = []
    # EID_01 (High performer, avg 650)
    for d, w in [("2023-01-01", 640.0), ("2023-01-10", 650.0), ("2023-01-20", 660.0)]:
        rows.append(
            {
                "eid": "EID_01",
                "date": pd.to_datetime(d),
                "finalpweight": w,
                "sex": "Steer",
                "treatment": "Drench",
                "breed": "Angus",
                "mob": "Front",
            }
        )
    # EID_02 through EID_08 (Medium performers, avg 500-560)
    for idx in range(2, 9):
        eid = f"EID_{idx:02d}"
        base_w = 480.0 + idx * 10.0
        for d, offset in [("2023-01-01", 0.0), ("2023-01-10", 5.0), ("2023-01-20", 10.0)]:
            rows.append(
                {
                    "eid": eid,
                    "date": pd.to_datetime(d),
                    "finalpweight": base_w + offset,
                    "sex": "Steer",
                    "treatment": None,
                    "breed": "Angus",
                    "mob": "Front",
                }
            )
    # EID_09 (Low performer, avg 400)
    for d, w in [("2023-01-01", 390.0), ("2023-01-10", 400.0), ("2023-01-20", 410.0)]:
        rows.append(
            {
                "eid": "EID_09",
                "date": pd.to_datetime(d),
                "finalpweight": w,
                "sex": "Heifer",
                "treatment": "Drench",
                "breed": "Angus",
                "mob": "Front",
            }
        )
    # EID_10 (Single observation animal, w=700)
    rows.append(
        {
            "eid": "EID_10",
            "date": pd.to_datetime("2023-01-15"),
            "finalpweight": 700.0,
            "sex": "Steer",
            "treatment": None,
            "breed": "Angus",
            "mob": "Front",
        }
    )
    return pd.DataFrame(rows)


@pytest.fixture
def distribution_calendar_df() -> pd.DataFrame:
    """Dataset to test true 7-day calendar window [max_date - 6, max_date]."""
    records = [
        ("2023-01-10", 300.0),
        ("2023-02-10", 350.0),
        ("2023-03-10", 400.0),
        ("2023-04-01", 450.0),
        ("2023-04-15", 500.0),
        ("2023-04-18", 510.0),
        ("2023-04-20", 520.0),
    ]
    return pd.DataFrame(
        [
            {
                "eid": "ANIMAL_A",
                "date": pd.to_datetime(d),
                "finalpweight": w,
                "sex": "Steer",
                "treatment": "Drench",
                "breed": "Angus",
                "mob": "One",
            }
            for d, w in records
        ]
    )
