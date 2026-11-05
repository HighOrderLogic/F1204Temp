from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.models.schemas import FilterState
from app.services.filter_service import FilterService
from app.services.labels import format_measure_display


def sample_for_distribution(df: pd.DataFrame, max_per_group: int = 500) -> pd.DataFrame:
    if len(df) <= 2000:
        return df
    return (
        df.groupby("group", group_keys=False)
        .apply(lambda g: g.sample(n=min(max_per_group, len(g)), random_state=42))
        .reset_index(drop=True)
    )


class DistributionService:
    @staticmethod
    def compute(grouped_df: pd.DataFrame, measure: str, hist_bins: int = 20) -> dict:
        if grouped_df.empty:
            return {"histogram": {"bins": [], "groups": [], "mean": 0, "median": 0}, "boxplot": {"groups": []}}

        hist_bins = max(10, min(50, hist_bins))
        sample_df = grouped_df
        if len(grouped_df) > 5000:
            sample_df = sample_for_distribution(grouped_df, 500)
        elif len(grouped_df) > 2000:
            sample_df = sample_for_distribution(grouped_df, 300)

        hist_groups = []
        all_values = []
        for group, gdf in sample_df.groupby("group"):
            vals = gdf[measure].dropna().tolist()
            if len(vals) > 3000:
                vals = list(np.random.default_rng(42).choice(vals, size=200, replace=False))
            hist_groups.append({"group": group, "values": vals})
            all_values.extend(vals)

        box_groups = []
        for group, gdf in grouped_df.groupby("group"):
            vals = gdf[measure].dropna()
            if vals.empty:
                continue
            q1, med, q3 = vals.quantile([0.25, 0.5, 0.75])
            box_groups.append(
                {
                    "group": group,
                    "min": round(float(vals.min()), 2),
                    "q1": round(float(q1), 2),
                    "median": round(float(med), 2),
                    "q3": round(float(q3), 2),
                    "max": round(float(vals.max()), 2),
                }
            )

        mean_val = round(float(np.mean(all_values)), 2) if all_values else 0
        median_val = round(float(np.median(all_values)), 2) if all_values else 0

        return {
            "histogram": {
                "bins": hist_bins,
                "groups": hist_groups,
                "mean": mean_val,
                "median": median_val,
            },
            "boxplot": {"groups": box_groups},
        }


class TimeseriesService:
    @staticmethod
    def compute(grouped_df: pd.DataFrame, measure: str, show_smooth: bool = False) -> dict:
        if grouped_df.empty:
            return {"series": [], "y_label": format_measure_display(measure)}

        daily = (
            grouped_df.groupby(["date", "group"], sort=True)[measure]
            .agg(value="mean", count="count")
            .reset_index()
        )
        daily = daily.sort_values(["group", "date"])
        daily["value"] = daily["value"].round(2)
        daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")
        series = daily.to_dict(orient="records")

        if show_smooth and len(daily) > 3:
            for group in daily["group"].unique():
                gdf = daily[daily["group"] == group].sort_values("date")
                if len(gdf) >= 3:
                    window = max(3, min(15, len(gdf) // 5 or 3))
                    smoothed = gdf["value"].rolling(window=window, center=True, min_periods=1).mean()
                    for (_, row), sm in zip(gdf.iterrows(), smoothed):
                        series.append(
                            {
                                "date": row["date"],
                                "group": f"{group} (trend)",
                                "value": round(float(sm), 2),
                                "count": int(row["count"]),
                            }
                        )

        return {"series": series, "y_label": format_measure_display(measure)}


class CohortService:
    @staticmethod
    def analyze(
        filtered_df: pd.DataFrame, grouped_df: pd.DataFrame, measure: str, percentile: int, filters: FilterState
    ) -> dict:
        percentile = max(10, min(20, percentile))
        if percentile not in (10, 15, 20):
            percentile = 10

        mixed = FilterService.has_mixed_selection(filters)

        empty_stat = {"count": 0, "average": 0, "min": 0, "max": 0, "animals": []}
        if filtered_df.empty:
            return {
                "percentile": percentile,
                "total_animals": 0,
                "high": empty_stat,
                "medium": empty_stat,
                "low": empty_stat,
                "top": empty_stat,
                "bottom": empty_stat,
                "show_mixed_warning": mixed,
                "timeline": [],
            }

        # Longitudinal quality guard: animals require >= 2 observations to qualify
        extra_keys = list(getattr(filters, "extra_filters", {}).keys()) if hasattr(filters, "extra_filters") and filters.extra_filters else []
        meta_cols = [c for c in ["sex", "breed", "treatment", "mob"] + extra_keys if c in filtered_df.columns]

        sort_cols = ["date"] if "date" in filtered_df.columns else []
        sorted_df = filtered_df.sort_values(sort_cols) if sort_cols else filtered_df

        agg_kwargs: dict[str, Any] = {
            "avg_measure": (measure, "mean"),
            "count": (measure, "count"),
        }
        if "date" in filtered_df.columns:
            agg_kwargs["latest_date"] = ("date", "max")
            agg_kwargs["latest_value"] = (measure, "last")
        for c in meta_cols:
            agg_kwargs[c] = (c, "first")

        animal_counts = sorted_df.groupby("eid", as_index=False).agg(**agg_kwargs)
        animal_counts["total_records"] = animal_counts["count"]
        qualified = animal_counts[animal_counts["count"] >= 2].copy()
        qualified = qualified.sort_values("avg_measure", ascending=False)
        n = len(qualified)

        if n == 0:
            return {
                "percentile": percentile,
                "total_animals": 0,
                "high": empty_stat,
                "medium": empty_stat,
                "low": empty_stat,
                "top": empty_stat,
                "bottom": empty_stat,
                "show_mixed_warning": mixed,
                "timeline": [],
            }

        k = max(1, math.ceil(n * percentile / 100))
        if 2 * k > n:
            k = max(1, n // 2)

        high = qualified.head(k)
        low = qualified.tail(k)
        low = low[~low["eid"].isin(high["eid"])]
        medium = qualified[(~qualified["eid"].isin(high["eid"])) & (~qualified["eid"].isin(low["eid"]))]

        def cohort_stats(cdf: pd.DataFrame) -> dict:
            if cdf.empty:
                return {"count": 0, "average": 0, "min": 0, "max": 0, "animals": []}
            clean_cdf = cdf.copy()
            clean_cdf["avg_measure"] = clean_cdf["avg_measure"].round(2)
            if "latest_value" in clean_cdf.columns:
                clean_cdf["latest_value"] = clean_cdf["latest_value"].round(2)
            if "latest_date" in clean_cdf.columns:
                if pd.api.types.is_datetime64_any_dtype(clean_cdf["latest_date"]):
                    clean_cdf["latest_date"] = clean_cdf["latest_date"].dt.strftime("%Y-%m-%d")
                else:
                    clean_cdf["latest_date"] = clean_cdf["latest_date"].astype(str)
            animals = clean_cdf.astype(object).where(pd.notnull(clean_cdf), None).to_dict(orient="records")
            return {
                "count": len(cdf),
                "average": round(float(cdf["avg_measure"].mean()), 2),
                "min": round(float(cdf["avg_measure"].min()), 2),
                "max": round(float(cdf["avg_measure"].max()), 2),
                "animals": animals,
            }

        timeline = []
        high_eids = set(high["eid"])
        medium_eids = set(medium["eid"])
        low_eids = set(low["eid"])
        for cohort_name, eids in [("high", high_eids), ("medium", medium_eids), ("low", low_eids)]:
            if not eids:
                continue
            cdf = filtered_df[filtered_df["eid"].isin(eids)]
            if cdf.empty:
                continue
            daily = cdf.groupby("date")[measure].mean().reset_index().sort_values("date")
            daily_recs = (
                daily.assign(
                    date=lambda df_: df_["date"].dt.strftime("%Y-%m-%d"),
                    cohort=cohort_name,
                    value=lambda df_: df_[measure].round(2),
                )[["date", "cohort", "value"]]
                .to_dict(orient="records")
            )
            timeline.extend(daily_recs)

        high_res = cohort_stats(high)
        medium_res = cohort_stats(medium)
        low_res = cohort_stats(low)

        return {
            "percentile": percentile,
            "total_animals": n,
            "high": high_res,
            "medium": medium_res,
            "low": low_res,
            "top": high_res,
            "bottom": low_res,
            "show_mixed_warning": mixed,
            "timeline": timeline,
        }
