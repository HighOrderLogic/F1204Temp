"""Lightweight active herd sliding window resolution — SQL only, no pandas.

Safe for Vercel serverless light runtime (api/light/index.py).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


def resolve_active_window(
    farm_id: str,
    filters: Any,
    max_date: Any | None = None,
) -> tuple[date, date] | None:
    if isinstance(filters, dict):
        active_only = bool(filters.get("active_only", False))
        mode = filters.get("active_reference_mode", "auto") or "auto"
        days = int(filters.get("active_days", 30) or 30)
    else:
        active_only = bool(getattr(filters, "active_only", False))
        mode = getattr(filters, "active_reference_mode", "auto") or "auto"
        days = int(getattr(filters, "active_days", 30) or 30)

    if not active_only:
        return None

    today = datetime.now(ZoneInfo("Australia/Sydney")).date()

    if max_date is None:
        from app.db import fetch_one

        row = fetch_one("SELECT MAX(date) AS max_d FROM animal_data WHERE farm_id = %s", (farm_id,))
        max_date = row.get("max_d") if row else None

    if max_date is None:
        return None

    if isinstance(max_date, str):
        max_date = date.fromisoformat(str(max_date)[:10])
    elif isinstance(max_date, datetime) or hasattr(max_date, "date"):
        max_date = max_date.date()

    if mode == "today":
        t_ref = today
    elif mode == "max":
        t_ref = max_date
    else:  # "auto"
        t_ref = max_date if (today - max_date).days > 1 else today

    window_start = t_ref - timedelta(days=days - 1)
    return window_start, t_ref

