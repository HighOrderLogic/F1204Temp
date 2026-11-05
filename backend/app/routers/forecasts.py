"""FinalPweight forecasts for the Prediction page (rows written by app/services/forecast_service.py
after each upload, see app/routers/admin.py).

Admins pick animals by real EID. Everyone else gets masked IDs (ID-XXXXXXXX) and sends one
back; the server resolves it to the real EID, and responses to non-admins never contain a
real EID. Admins and owners can also download the whole farm's forecasts as CSV (export.csv).
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.auth.dependencies import CurrentUser, assert_farm_access, get_current_user, require_raw_export
from app.db import fetch_all
from app.utils.anonymize import PseudonymCollisionError, anonymize_eid, resolve_animal_id

router = APIRouter()
logger = logging.getLogger(__name__)

TARGET = "finalpweight"
HISTORY_DAYS = 60

TABLE_MISSING = (
    "No forecasts have been generated in this database yet. They are made after every data upload; "
    "an admin can also run python scripts/admin.py forecast --farm <farm_id>, which creates the forecasts table."
)
OUTDATED = (
    "These forecasts were made by an older version of the app, which did not record how far ahead the "
    "model was tested. An admin needs to refresh them: upload data, or run "
    "python scripts/admin.py forecast --farm <farm_id>."
)
COLLISION = "Two animals on this farm share a masked ID, so forecasts cannot be shown. Please contact an administrator."


def _query(sql: str, params: tuple) -> list[dict]:
    try:
        return fetch_all(sql, params)
    except Exception as exc:
        text = str(exc)
        if "validated_days" in text:  # a forecasts table from before that column; the next refresh adds it
            raise HTTPException(status_code=503, detail=OUTDATED) from exc
        if "forecasts" in text and ("does not exist" in text or "Catalog Error" in text):
            raise HTTPException(status_code=503, detail=TABLE_MISSING) from exc
        raise


def _try_auto_refresh(farm_id: str) -> bool:
    """Attempt on-demand forecast generation if forecasts are missing or outdated."""
    try:
        from app.services import forecast_service

        forecast_service.refresh_farm(farm_id)
        return True
    except Exception:
        logger.warning("On-demand forecast refresh failed for farm %s", farm_id, exc_info=True)
        return False


def _farm_animals(farm_id: str, allow_refresh: bool = True) -> list[dict]:
    try:
        rows = _query(
            """
            SELECT eid, MAX(tier) AS tier, MAX(history_days) AS history_days, MAX(data_through) AS data_through,
                   MAX(generated_at) AS generated_at, MAX(model_version) AS model_version
            FROM forecasts WHERE farm_id = %s AND target = %s
            GROUP BY eid
            """,
            (farm_id, TARGET),
        )
    except HTTPException as exc:
        if allow_refresh and exc.status_code == 503 and _try_auto_refresh(farm_id):
            return _farm_animals(farm_id, allow_refresh=False)
        raise

    if not rows and allow_refresh:
        if _try_auto_refresh(farm_id):
            return _farm_animals(farm_id, allow_refresh=False)

    return rows


@router.get("/animals")
async def list_animals(farm_id: Annotated[str, Query()], user: Annotated[CurrentUser, Depends(get_current_user)]):
    """Animals with a forecast: real EIDs for admins, masked IDs for everyone else."""
    assert_farm_access(user, farm_id)
    rows = _farm_animals(farm_id)
    if not rows:  # never forecasted (e.g. script-seeded data): compute now instead of telling the user to run the CLI
        from app.routers.admin import refresh_forecasts

        if (await run_in_threadpool(refresh_forecasts, farm_id))["status"] == "updated":
            rows = _farm_animals(farm_id)
    animals = []
    seen: dict[str, str] = {}
    for r in rows:
        animal_id = r["eid"] if user.can_view_eid else anonymize_eid(r["eid"])
        if seen.setdefault(animal_id, r["eid"]) != r["eid"]:
            logger.error("Masked ID collision on farm %s: %s", farm_id, animal_id)
            raise HTTPException(status_code=500, detail=COLLISION)
        animals.append(
            {
                "id": animal_id,
                "tier": r["tier"],
                "history_days": r["history_days"],
                "data_through": r["data_through"],
            }
        )
    animals.sort(key=lambda a: a["id"])  # by the ID the user sees, so mask order reveals nothing
    return {
        "farm_id": farm_id,
        "target": TARGET,
        "masked": not user.can_view_eid,
        "animals": animals,
        "generated_at": max((r["generated_at"] for r in rows), default=None),
        "model_version": rows[0]["model_version"] if rows else None,
        "message": None if rows else (
            f"No forecasts for farm {farm_id} yet. They are made after every data upload, or an admin "
            f"can run: python scripts/admin.py forecast --farm {farm_id}"
        ),
    }


@router.get("/series")
def get_series(
    farm_id: Annotated[str, Query()],
    animal: Annotated[str, Query(description="Real EID (admins) or masked ID (everyone else)")],
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    """Recent actual weights plus the forecast for one animal."""
    assert_farm_access(user, farm_id)
    rows = _farm_animals(farm_id)
    try:
        eid = resolve_animal_id(animal, [r["eid"] for r in rows], user.can_view_eid)
    except PseudonymCollisionError as exc:
        logger.error("Farm %s: %s", farm_id, exc)
        raise HTTPException(status_code=500, detail=COLLISION) from exc
    if eid is None:
        raise HTTPException(status_code=404, detail="No forecast for that animal on this farm")

    try:
        forecast = _query(
            """
            SELECT date, pred, horizon, tier, history_days, data_through, model_version, validated_days, generated_at
            FROM forecasts WHERE farm_id = %s AND target = %s AND eid = %s
            ORDER BY horizon
            """,
            (farm_id, TARGET, eid),
        )
    except HTTPException as exc:
        if exc.status_code == 503 and _try_auto_refresh(farm_id):
            forecast = _query(
                """
                SELECT date, pred, horizon, tier, history_days, data_through, model_version, validated_days, generated_at
                FROM forecasts WHERE farm_id = %s AND target = %s AND eid = %s
                ORDER BY horizon
                """,
                (farm_id, TARGET, eid),
            )
        else:
            raise

    meta = forecast[0]
    if meta["validated_days"] is None:  # rows kept from before the column existed
        if _try_auto_refresh(farm_id):
            forecast = _query(
                """
                SELECT date, pred, horizon, tier, history_days, data_through, model_version, validated_days, generated_at
                FROM forecasts WHERE farm_id = %s AND target = %s AND eid = %s
                ORDER BY horizon
                """,
                (farm_id, TARGET, eid),
            )
            meta = forecast[0]
        if meta["validated_days"] is None:
            raise HTTPException(status_code=503, detail=OUTDATED)
    history = fetch_all(
        """
        SELECT date, finalpweight FROM animal_data
        WHERE farm_id = %s AND eid = %s AND finalpweight IS NOT NULL AND date >= %s AND date <= %s
        ORDER BY date
        """,
        (farm_id, eid, meta["data_through"] - timedelta(days=HISTORY_DAYS - 1), meta["data_through"]),
    )
    return {
        "farm_id": farm_id,
        "animal": animal,  # exactly what the caller sent: a masked ID for non-admins
        "target": TARGET,
        "tier": meta["tier"],
        "history_days": meta["history_days"],
        "data_through": meta["data_through"],
        "generated_at": meta["generated_at"],
        "model_version": meta["model_version"],
        "validated_days": meta["validated_days"],  # the tested range of the model that made these rows
        "history": [{"date": h["date"], "weight": h["finalpweight"]} for h in history],
        "forecast": [{"date": f["date"], "weight": f["pred"], "horizon": f["horizon"]} for f in forecast],
    }


EXPORT_COLUMNS = [
    "farm_id", "eid", "date", "horizon_days", "predicted_finalpweight_kg", "validated",
    "tier", "history_days", "data_through", "model_version", "generated_at",
]


@router.get("/export.csv")
def export_forecasts(
    farm_id: Annotated[str, Query()],
    user: Annotated[CurrentUser, Depends(require_raw_export)],
):
    """Every forecast row for the farm, by real EID (admins and owners only)."""
    assert_farm_access(user, farm_id)
    rows = _query(
        """
        SELECT eid, date, horizon, pred, tier, history_days, data_through, validated_days, model_version, generated_at
        FROM forecasts WHERE farm_id = %s AND target = %s
        ORDER BY eid, horizon
        """,
        (farm_id, TARGET),
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"No forecasts for farm {farm_id} yet")

    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    for r in rows:
        # validated: inside the range the model was tested on; blank for rows from before validated_days existed
        validated = "" if r["validated_days"] is None else ("yes" if r["horizon"] <= r["validated_days"] else "no")
        writer.writerow([
            farm_id, r["eid"], r["date"], r["horizon"], round(r["pred"], 1), validated,
            r["tier"], r["history_days"], r["data_through"], r["model_version"], r["generated_at"],
        ])

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{farm_id}_forecasts.csv"'},
    )
