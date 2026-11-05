from __future__ import annotations

import json
import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.auth.cron import assert_cron_authorized
from app.auth.dependencies import CurrentUser, assert_farm_access, get_current_user
from app.db import execute, execute_returning, fetch_all, fetch_one
from app.models.schemas import EmailScheduleCreate, EmailSendNow, FilterState, SchedulePatch
from app.services.data_service import DataService
from app.services.email_service import EmailService, compute_next_send

logger = logging.getLogger(__name__)

router = APIRouter()


def _serialize_schedule(row: dict) -> dict:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    sydney = ZoneInfo("Australia/Sydney")
    out = dict(row)
    for key in ("send_time", "send_date", "next_send_at", "last_sent", "created_at"):
        val = out.get(key)
        if val is None:
            continue
        if key == "next_send_at" and isinstance(val, datetime):
            if val.tzinfo is None:
                val = val.replace(tzinfo=sydney)
            out[key] = val.isoformat()
        elif hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


@router.get("/schedules")
def list_schedules(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    farm_id: str | None = Query(None),
):
    """List schedules for one farm, or for every farm the user can access when farm_id is omitted."""
    try:
        if farm_id:
            assert_farm_access(user, farm_id)
            rows = fetch_all(
                "SELECT * FROM email_schedules WHERE farm_id = %s ORDER BY created_at DESC",
                (farm_id,),
            )
        else:
            if not user.farm_ids:
                return []
            rows = fetch_all(
                """
                SELECT * FROM email_schedules
                WHERE farm_id IN %s
                ORDER BY created_at DESC
                """,
                (tuple(user.farm_ids),),
            )
        return [_serialize_schedule(r) for r in rows]
    except Exception as exc:
        logger.warning("Failed to query email_schedules (local dev schema fallback): %s", exc)
        return []


@router.post("/schedules")
def create_schedule(body: EmailScheduleCreate, user: Annotated[CurrentUser, Depends(get_current_user)]):
    assert_farm_access(user, body.farm_id)
    if not (body.recipient_email or "").strip() or "@" not in body.recipient_email:
        raise HTTPException(status_code=400, detail="Enter a valid recipient email")
    # Normalize HTML <input type="time"> values ("09:00" or "09:00:00") to HH:MM:SS
    raw_time = (body.send_time or "09:00").strip()
    parts = raw_time.split(":")
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail="Send time must be HH:MM")
    try:
        send_time = f"{int(parts[0]):02d}:{int(parts[1]):02d}:00"
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Send time must be HH:MM") from exc

    sched_dict = body.model_dump()
    sched_dict["send_time"] = send_time
    if body.frequency == "daily":
        sched_dict["day_of_week"] = None
        sched_dict["day_of_month"] = None
    elif body.frequency == "weekly":
        sched_dict["day_of_month"] = None
        if not sched_dict.get("day_of_week"):
            sched_dict["day_of_week"] = 1
    elif body.frequency == "monthly":
        sched_dict["day_of_week"] = None
        if not sched_dict.get("day_of_month"):
            sched_dict["day_of_month"] = 1

    next_at = compute_next_send(sched_dict)
    row = execute_returning(
        """
        INSERT INTO email_schedules (
            farm_id, recipient_email, schedule_name, frequency, send_time,
            send_date, day_of_week, day_of_month, next_send_at, email_subject,
            email_body, report_filters, report_charts, report_format, created_by
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING *
        """,
        (
            body.farm_id,
            body.recipient_email.strip(),
            body.schedule_name,
            body.frequency,
            send_time,
            body.send_date,
            sched_dict.get("day_of_week"),
            sched_dict.get("day_of_month"),
            next_at,
            body.email_subject,
            body.email_body,
            json.dumps(body.report_filters) if body.report_filters else None,
            body.report_charts,
            body.report_format,
            user.username,
        ),
    )
    return _serialize_schedule(row)


@router.post("/send-now")
def send_now(body: EmailSendNow, user: Annotated[CurrentUser, Depends(get_current_user)]):
    assert_farm_access(user, body.farm_id)
    if not (body.recipient_email or "").strip() or "@" not in body.recipient_email:
        raise HTTPException(status_code=400, detail="Enter a valid recipient email")

    farm = fetch_one("SELECT farm_name FROM farms WHERE farm_id = %s", (body.farm_id,))
    farm_name = farm["farm_name"] if farm else body.farm_id

    filters_data = dict(body.report_filters or {})
    filters_data.setdefault("farm_id", body.farm_id)
    if not filters_data.get("year"):
        filters_data["year"] = date.today().year
    # Coerce common UI shapes
    if "day" in filters_data and filters_data["day"] is None:
        filters_data["day"] = "All"
    try:
        filters = FilterState(**filters_data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid report filters: {exc}") from exc

    try:
        filtered, grouped, _, _ = DataService.get_filtered_data(filters, user.can_view_eid)
        from app.services.report_generator import ReportGenerator

        charts = body.report_charts or ["Summary Statistics", "Distribution"]
        pdf = ReportGenerator.generate_pdf(
            filters, grouped, charts, farm_name, filtered_df=filtered
        )
        measures = list(dict.fromkeys(filters.measures or [filters.measure]))
        stats_summary = ReportGenerator.compute_executive_summary_stats(filtered, measures)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to build report PDF: {exc}") from exc

    subject = body.email_subject or f"Automated Livestock Report - {farm_name}"
    html = EmailService.build_email_body(
        filters_data, body.email_body or "", farm_name, stats_summary=stats_summary
    )
    ok, status = EmailService.send_report_email(body.recipient_email, subject, html, pdf)
    if not ok:
        raise HTTPException(status_code=500, detail=status or "Failed to send email")
    if status == "dry_run":
        return {
            "detail": "Email dry-run only (not sent). Set EMAIL_DRY_RUN=false in Vercel env to send for real.",
            "dry_run": True,
        }
    return {"detail": "Email sent", "dry_run": False}


@router.patch("/schedules/{schedule_id}")
def patch_schedule(
    schedule_id: int,
    body: SchedulePatch,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    sched = fetch_one("SELECT * FROM email_schedules WHERE id = %s", (schedule_id,))
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")
    assert_farm_access(user, sched["farm_id"])
    if body.is_active is not None:
        if body.is_active:
            # Recalculate next fire so a paused schedule does not send immediately
            # or keep a stale next_send_at after reactivation.
            next_at = compute_next_send(dict(sched))
            execute(
                "UPDATE email_schedules SET is_active = TRUE, next_send_at = %s WHERE id = %s",
                (next_at, schedule_id),
            )
        else:
            execute(
                "UPDATE email_schedules SET is_active = FALSE WHERE id = %s",
                (schedule_id,),
            )
    return {"detail": "Updated"}


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: int, user: Annotated[CurrentUser, Depends(get_current_user)]):
    sched = fetch_one("SELECT * FROM email_schedules WHERE id = %s", (schedule_id,))
    if not sched:
        raise HTTPException(status_code=404, detail="Schedule not found")
    assert_farm_access(user, sched["farm_id"])
    execute("DELETE FROM email_schedules WHERE id = %s", (schedule_id,))
    return {"detail": "Deleted"}


@router.api_route("/process-due", methods=["GET", "POST"])
def process_due(
    x_cron_secret: str | None = Header(None, alias="X-Cron-Secret"),
    x_vercel_cron: str | None = Header(None, alias="x-vercel-cron"),
    authorization: str | None = Header(None),
    cron_secret: str | None = Query(None),
):
    """Process due email schedules.

    Vercel Cron invokes this path with GET (not POST). Auth (any one):
    - Authorization: Bearer <CRON_SECRET> (Vercel sets this when CRON_SECRET env is set)
    - X-Cron-Secret / ?cron_secret= matching CRON_SECRET
    - x-vercel-cron: 1 (legacy Vercel header)
    """
    assert_cron_authorized(
        x_cron_secret=x_cron_secret,
        x_vercel_cron=x_vercel_cron,
        authorization=authorization,
        cron_secret=cron_secret,
    )
    return EmailService.process_due_emails()
