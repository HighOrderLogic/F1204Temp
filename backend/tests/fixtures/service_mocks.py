from __future__ import annotations

from unittest.mock import create_autospec
import pytest

from app.services.data_service import DataService


@pytest.fixture
def mock_filtered_data(monkeypatch):
    """Centralized, contract-safe mock factory for DataService.get_filtered_data."""

    def _install(filtered, grouped=None, base=None, total=10):
        grouped_df = grouped if grouped is not None else filtered
        base_df = base if base is not None else filtered
        fake = create_autospec(
            DataService.get_filtered_data,
            return_value=(filtered, grouped_df, base_df, total),
        )
        monkeypatch.setattr(DataService, "get_filtered_data", fake)
        return fake

    return _install
