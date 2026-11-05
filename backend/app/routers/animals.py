from __future__ import annotations

import csv
import io
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.auth.dependencies import CurrentUser, assert_farm_access, get_current_user
from app.models.schemas import DataQueryRequest, FilterState
from app.services.data_service import DataService
from app.services.filter_service import FilterService
from app.utils.anonymize import anonymize_records

router = APIRouter()

# Columns returned for the Data Management table / CSV (admin full view).
DATA_MGMT_COLUMNS = [
    "date",
    "eid",
    "sex",
    "breed",
    "treatment",
    "mob",
    "weight",
    "pweight",
    "finalpweight",
    "finalgrowthpbs",
    "finaldailygrowth",
    "feedintakekgd",
    "methane",
    "animalvalue",
    "animalprod",
    "carcassweight",
]


def _json_safe(value):
    """Coerce NaN/Inf/pandas NA to None for JSON responses."""
    if value is None:
        return None
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    try:
        import math

        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
    except Exception:
        pass
    return value


def _query_response(body: DataQueryRequest, user: CurrentUser) -> dict:
    assert_farm_access(user, body.farm_id)

    if not body.include_rows:
        from app.services import sql_agg

        counts = sql_agg.count_filtered(body, user.can_view_eid)
        return {
            "record_count": counts.get("record_count", 0),
            "total_records": counts.get("total_records", 0),
            "active_animal_count": counts.get("active_animal_count", 0),
            "total_animal_count": counts.get("total_animal_count", 0),
            "common_filters_note": None,
            "filtered": [],
            "processed": [],
            "grouped": [],
            "page": body.page or 1,
            "page_size": body.page_size or 20,
            "page_count": 0,
        }

    # Full row listing is restricted to admin and owner (EID + downloadable farm data).
    if not user.can_export_raw:
        raise HTTPException(status_code=403, detail="Admin access required to view raw data")

    filtered, _, _, total_records = DataService.get_filtered_data(
        body, user.can_view_eid, build_groups=False
    )
    record_count = len(filtered)
    page = max(1, int(body.page or 1))
    page_size = max(5, min(100, int(body.page_size or 20)))
    page_count = max(1, (record_count + page_size - 1) // page_size) if record_count else 0
    if page_count and page > page_count:
        page = page_count

    filtered_records = FilterService.df_to_records(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    page_rows = filtered_records[start:end]

    # Prefer a stable column set for the data table.
    slim_rows = []
    for row in page_rows:
        slim = {col: _json_safe(row.get(col)) for col in DATA_MGMT_COLUMNS}
        if slim.get("treatment") is None:
            slim["treatment"] = "No Treatment"
        slim_rows.append(slim)

    return {
        "record_count": record_count,
        "total_records": total_records,
        "common_filters_note": None,
        "filtered": anonymize_records(slim_rows, user.can_view_eid),
        "processed": [],
        "grouped": [],
        "page": page,
        "page_size": page_size,
        "page_count": page_count,
        "columns": DATA_MGMT_COLUMNS,
    }


@router.post("/query")
def data_query(body: DataQueryRequest, user: Annotated[CurrentUser, Depends(get_current_user)]):
    return _query_response(body, user)


@router.get("/export.csv")
def export_csv(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    farm_id: str = Query(...),
    year: int = Query(...),
    month: str = Query("All"),
    day: str = Query("All"),
    sex: str = Query("Overall"),
    treatment: str = Query("Overall"),
    breed: str = Query("Overall"),
    mob: str = Query("Overall"),
    eid: str = Query("Overall"),
    extra_filters: str = Query("{}", description='JSON object, e.g. {"paddock": ["P1"]}'),
):
    assert_farm_access(user, farm_id)
    if not user.can_export_raw:
        raise HTTPException(status_code=403, detail="Admin access required to download CSV")

    def split_multi(val: str) -> list[str]:
        return [v.strip() for v in val.split("|") if v.strip()]

    try:
        extra = json.loads(extra_filters)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="extra_filters must be a JSON object") from exc

    filters = FilterState(
        farm_id=farm_id,
        year=year,
        month=month,
        day=day if day != "All" else "All",
        sex=split_multi(sex),
        treatment=split_multi(treatment),
        breed=split_multi(breed),
        mob=split_multi(mob),
        eid=split_multi(eid),
        extra_filters=extra,
    )
    filtered, _, _, _ = DataService.get_filtered_data(filters, user.can_view_eid, build_groups=False)
    records = FilterService.df_to_records(filtered)

    # Stable column order for CSV; include any extra fields after the known set.
    fieldnames: list[str] = []
    if records:
        extras = [k for k in records[0].keys() if k not in DATA_MGMT_COLUMNS]
        fieldnames = [c for c in DATA_MGMT_COLUMNS if c in records[0]] + extras
        for row in records:
            if row.get("treatment") is None:
                row["treatment"] = "No Treatment"

    output = io.StringIO()
    if records and fieldnames:
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
    else:
        output.write("No data\n")

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{farm_id}_export.csv"'},
    )
