from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.auth.dependencies import CurrentUser, assert_farm_access, get_current_user
from app.models.schemas import FilterState
from app.services import sql_agg

router = APIRouter()


@router.post("/stats")
def summary_stats(body: FilterState, user: Annotated[CurrentUser, Depends(get_current_user)]):
    assert_farm_access(user, body.farm_id)
    try:
        return sql_agg.summary_sql(body, user.can_view_eid, body.measure)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
