from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.auth.dependencies import CurrentUser, assert_farm_access, get_current_user
from app.models.schemas import FilterState
from app.services import sql_agg
from app.services.choices_service import get_active_herd_count, get_filter_choices

router = APIRouter()


@router.get("/choices")
def filter_choices(
    farm_id: str = Query(...),
    user: Annotated[CurrentUser, Depends(get_current_user)] = None,
):
    assert_farm_access(user, farm_id)
    return get_filter_choices(farm_id, user.can_view_eid)


@router.get("/active-count")
def filter_active_count(
    farm_id: str = Query(...),
    active_days: int = Query(30),
    active_reference_mode: str = Query("auto"),
    user: Annotated[CurrentUser, Depends(get_current_user)] = None,
):
    assert_farm_access(user, farm_id)
    return get_active_herd_count(farm_id, active_days, active_reference_mode)



@router.get("/bootstrap")
def filter_bootstrap(
    farm_id: str = Query(...),
    measure: str = Query("finalpweight"),
    user: Annotated[CurrentUser, Depends(get_current_user)] = None,
):
    """Fast first paint: choices + summary KPIs (no full grain payload).

    Grain for timeseries is fetched client-side after Summary paints.
    """
    assert_farm_access(user, farm_id)
    choices = get_filter_choices(farm_id, user.can_view_eid)
    year = choices.get("max_year") or (choices.get("years") or [None])[0]
    summary = None
    if year:
        filters = FilterState(
            farm_id=farm_id,
            year=int(year),
            month="All",
            day="All",
            sex=["Overall"],
            treatment=["Overall"],
            breed=["Overall"],
            mob=["Overall"],
            eid=["Overall"],
            measure=measure,
        )
        # filters.measure, not the raw query param: it is limited to the farm's enabled measures.
        summary = sql_agg.summary_sql(filters, user.can_view_eid, filters.measure)
    return {"choices": choices, "summary": summary}
