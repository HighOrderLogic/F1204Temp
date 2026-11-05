from __future__ import annotations

import csv
import io
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.auth.dependencies import CurrentUser, assert_farm_access, get_current_user
from app.models.schemas import CohortRequest, FilterState
from app.services.chart_service import CohortService
from app.services.data_service import DataService
from app.utils.anonymize import anonymize_records

router = APIRouter()


@router.post("/analyze")
def analyze_cohorts(body: CohortRequest, user: Annotated[CurrentUser, Depends(get_current_user)]):
    assert_farm_access(user, body.farm_id)
    # Cohorts only need filtered rows — skip build_grouped_data (combo explosion).
    filtered, _, _, _ = DataService.get_filtered_data(body, user.can_view_eid, build_groups=False)
    measures = body.measures if (hasattr(body, "measures") and body.measures) else [body.measure]
    charts = []
    primary_res = None
    for m in measures:
        res = CohortService.analyze(filtered, filtered, m, body.percentile, body)
        for tier in ("high", "medium", "low", "top", "bottom"):
            if tier in res and "animals" in res[tier]:
                res[tier]["animals"] = anonymize_records(res[tier]["animals"], user.can_view_eid)
        if primary_res is None:
            primary_res = res
        charts.append({"measure": m, **res})
    response_payload = dict(primary_res) if primary_res else {}
    response_payload["charts"] = charts
    return response_payload


@router.get("/export.csv")
def export_cohort(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    cohort: str = Query(...),
    farm_id: str = Query(...),
    year: int = Query(...),
    measure: str = Query("finalpweight"),
    percentile: int = Query(10),
):
    if not user.can_export_raw:
        raise HTTPException(status_code=403, detail="Admin access required to export cohort data")

    assert_farm_access(user, farm_id)
    filters = FilterState(farm_id=farm_id, year=year, measure=measure, cohort_percentile=percentile)
    filtered, _, _, _ = DataService.get_filtered_data(filters, user.can_view_eid, build_groups=False)
    result = CohortService.analyze(filtered, filtered, filters.measure, percentile, filters)

    output = io.StringIO()

    if cohort.lower() == "all":
        # Full dataset with appended cohort column
        high_eids = {a["eid"] for a in result.get("high", {}).get("animals", [])}
        low_eids = {a["eid"] for a in result.get("low", {}).get("animals", [])}
        med_eids = {a["eid"] for a in result.get("medium", {}).get("animals", [])}

        def get_cohort_tag(eid_val):
            if eid_val in high_eids:
                return "High"
            if eid_val in low_eids:
                return "Low"
            if eid_val in med_eids:
                return "Medium"
            return "NA"

        export_df = filtered.copy()
        export_df["cohort"] = export_df["eid"].apply(get_cohort_tag)
        export_df.to_csv(output, index=False, lineterminator="\n")
    else:
        tier_map = {
            "top": "high",
            "bottom": "low",
            "high": "high",
            "medium": "medium",
            "low": "low",
        }
        target_tier = tier_map.get(cohort.lower(), cohort.lower())
        tier_block = result.get(target_tier) or result.get(cohort.lower(), {"animals": []})
        animals = tier_block.get("animals", [])
        fieldnames = list(animals[0].keys()) if animals else ["eid", "avg_measure"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(animals)

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="cohort_{cohort}.csv"'},
    )
