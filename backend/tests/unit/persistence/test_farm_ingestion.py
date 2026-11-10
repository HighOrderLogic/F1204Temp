from __future__ import annotations

import pandas as pd
import pytest
from fastapi import HTTPException

from app.services.farm_ingestion import validate_and_prepare_dataset


def test_duplicate_headers_are_rejected_before_numeric_cast():
    """weight and Weight both clean to weight; selecting that column is a DataFrame, which to_numeric rejects."""
    raw = pd.DataFrame(
        [["A1", "01/02/2024", "10.5", "11.0"]],
        columns=["EID", "Date", "weight", "Weight"],
    )

    with pytest.raises(HTTPException) as exc_info:
        validate_and_prepare_dataset(raw, "KFE")

    assert exc_info.value.status_code == 400
    assert "duplicate columns" in exc_info.value.detail
    assert "weight" in exc_info.value.detail


def test_collapsed_alias_collision_is_rejected():
    """pweight and p_weight collapse to the same canonical column."""
    raw = pd.DataFrame(
        [["A1", "01/02/2024", "10", "4"]],
        columns=["EID", "Date", "pweight", "p_weight"],
    )

    with pytest.raises(HTTPException) as exc_info:
        validate_and_prepare_dataset(raw, "KFE")

    assert exc_info.value.status_code == 400
    assert "pweight" in exc_info.value.detail
