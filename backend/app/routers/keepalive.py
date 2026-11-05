"""Daily database ping so a free Supabase project does not pause for inactivity."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Query

from app.auth.cron import assert_cron_authorized
from app.db import fetch_one

logger = logging.getLogger(__name__)

router = APIRouter()


@router.api_route("/db", methods=["GET", "POST"])
def keepalive_db(
    x_cron_secret: str | None = Header(None, alias="X-Cron-Secret"),
    x_vercel_cron: str | None = Header(None, alias="x-vercel-cron"),
    authorization: str | None = Header(None),
    cron_secret: str | None = Query(None),
):
    """Run SELECT 1 so a free Supabase project counts as active.

    cron-job.org (daily GET):
    https://YOUR-APP.vercel.app/api/health/db?cron_secret=YOUR_CRON_SECRET
    """
    assert_cron_authorized(
        x_cron_secret=x_cron_secret,
        x_vercel_cron=x_vercel_cron,
        authorization=authorization,
        cron_secret=cron_secret,
    )
    try:
        row = fetch_one("SELECT 1 AS ok")
    except Exception:
        logger.exception("database keepalive query failed")
        raise HTTPException(status_code=503, detail="Database unreachable") from None
    if not row:
        raise HTTPException(status_code=503, detail="Database unreachable")
    return {"status": "ok", "database": "up"}
