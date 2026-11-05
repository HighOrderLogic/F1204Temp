from __future__ import annotations

import pandas as pd

from app.models.schemas import FilterState
from app.services.filter_service import FilterService


class DataService:
    @staticmethod
    def get_filtered_data(
        filters: FilterState, is_admin: bool, *, build_groups: bool = True
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, int]:
        base_df = FilterService.load_farm_dataframe(filters.farm_id)
        total_records = len(base_df)
        filtered = FilterService.filter_data(base_df, filters, is_admin)
        if build_groups:
            grouped = FilterService.build_grouped_data(filtered, filters, is_admin)
        else:
            grouped = pd.DataFrame()
        return filtered, grouped, base_df, total_records
