from __future__ import annotations

import pandas as pd

from app.constants import MEASURE_UNITS


def subset_by_window(df: pd.DataFrame, days: int) -> pd.DataFrame:
    if df.empty:
        return df
    dmax = df["date"].max()
    return df[(df["date"] >= dmax - pd.Timedelta(days=days - 1)) & (df["date"] <= dmax)]


def kpi_block(df: pd.DataFrame, measure: str) -> dict:
    if df.empty or measure not in df.columns or not pd.api.types.is_numeric_dtype(df[measure]):
        return {
            "mean": 0,
            "min": 0,
            "max": 0,
            "median": 0,
            "count": 0,
            "unit": MEASURE_UNITS.get(measure, ""),
            "display": "No data",
        }
    m = df[measure].dropna()
    if m.empty:
        return {
            "mean": 0,
            "min": 0,
            "max": 0,
            "median": 0,
            "count": 0,
            "unit": MEASURE_UNITS.get(measure, ""),
            "display": "No data",
        }
    unit = MEASURE_UNITS.get(measure, "")
    return {
        "mean": round(float(m.mean()), 2),
        "min": round(float(m.min()), 2),
        "max": round(float(m.max()), 2),
        "median": round(float(m.median()), 2),
        "count": int(m.count()),
        "unit": unit,
    }


def format_kpi_label(window_name: str, df: pd.DataFrame) -> str:
    if df.empty:
        return window_name
    dmax = df["date"].max()
    if window_name == "last_day":
        return f"Last Day ({dmax.strftime('%d/%m/%Y')})"
    if window_name == "last_15_days":
        return "Last 15 Days"
    if window_name == "last_month":
        return "Last Month"
    return "Overall"


class SummaryService:
    @staticmethod
    def compute_stats(grouped_df: pd.DataFrame, measure: str) -> list[dict]:
        if grouped_df.empty:
            return []
        groups = []
        for full_group, gdf in grouped_df.groupby("full_group", sort=False):
            windows = {}
            for key, days in [
                ("last_day", 1),
                ("last_15_days", 15),
                ("last_month", 31),
                ("overall", None),
            ]:
                if days is None:
                    wdf = gdf
                    label = "Overall"
                else:
                    wdf = subset_by_window(gdf, days)
                    label = format_kpi_label(key, gdf)
                kpi = kpi_block(wdf, measure)
                kpi["label"] = label
                windows[key] = kpi
            groups.append({"full_group": full_group, "windows": windows})
        return groups
