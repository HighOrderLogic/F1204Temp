"""Light-tier anomaly snapshot reads (no pandas) for fast Vercel responses."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from app.auth.dependencies import CurrentUser, assert_farm_access, get_current_user
from app.models.schemas import AnomalyAnalyzeRequest
from app.services import anomaly_snapshot
from app.utils.anonymize import anonymize_records

router = APIRouter()

# Edge-cache default snapshots briefly. Auth still required; CDN key includes the cookie/auth
# variation on Vercel only for anonymous — we set private so browsers reuse within the session.
_SNAPSHOT_CACHE_HEADERS = {
    "Cache-Control": "private, max-age=60, stale-while-revalidate=300",
}


def _snapshot_response(
    *,
    farm_id: str,
    year: int,
    month: str,
    day: str,
    weight_col: str,
    sensitivity: str,
    user: CurrentUser,
) -> JSONResponse:
    assert_farm_access(user, farm_id)
    body = AnomalyAnalyzeRequest(
        farm_id=farm_id,
        year=year,
        month=month,
        day=day,
        weight_col=weight_col,  # type: ignore[arg-type]
        sensitivity=sensitivity,  # type: ignore[arg-type]
    )
    if not anomaly_snapshot.is_snapshot_eligible(body):
        raise HTTPException(status_code=404, detail="No anomaly snapshot for these filters")

    cached = anomaly_snapshot.read_snapshot(
        farm_id=farm_id,
        weight_col=weight_col,
        sensitivity=sensitivity,
        year=int(year),
        month=str(month or "All"),
        day=str(day or "All"),
    )
    if cached is None:
        raise HTTPException(status_code=404, detail="Anomaly snapshot not ready")

    out = dict(cached)
    out["animals"] = anonymize_records(list(cached.get("animals") or []), user.can_view_eid)
    out["from_snapshot"] = True
    return JSONResponse(content=out, headers=_SNAPSHOT_CACHE_HEADERS)


@router.get("/snapshot")
def get_anomaly_snapshot(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    farm_id: str = Query(...),
    year: int = Query(...),
    month: str = Query("All"),
    day: str = Query("All"),
    weight_col: Literal["weight", "pweight"] = Query("weight"),
    sensitivity: Literal["High", "Medium", "Low"] = Query("Medium"),
):
    """GET variant for browser cache reuse after the first load in a session."""
    return _snapshot_response(
        farm_id=farm_id,
        year=year,
        month=month,
        day=day,
        weight_col=weight_col,
        sensitivity=sensitivity,
        user=user,
    )


@router.post("/snapshot")
def read_anomaly_snapshot(
    body: AnomalyAnalyzeRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    """Return a precomputed analyze payload when cron has written one.

    Served from api/light so it stays fast even when the heavy function is cold.
    Returns 404 when no snapshot exists (client should fall back to /anomalies/analyze).
    """
    if body.year is None:
        raise HTTPException(status_code=404, detail="No anomaly snapshot for these filters")
    return _snapshot_response(
        farm_id=body.farm_id,
        year=int(body.year),
        month=str(body.month or "All"),
        day=str(body.day or "All"),
        weight_col=body.weight_col,
        sensitivity=body.sensitivity,
        user=user,
    )