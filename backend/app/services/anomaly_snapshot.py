"""Durable anomaly analyze snapshots so cold Vercel instances skip 3–5s scoring.

Read/write paths stay free of pandas so api/light can serve snapshots quickly.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.db import fetch_one, get_active_engine, get_conn

logger = logging.getLogger(__name__)

ANOMALY_SNAPSHOTS_DDL = """
CREATE TABLE IF NOT EXISTS anomaly_snapshots (
    farm_id VARCHAR(50) NOT NULL,
    weight_col VARCHAR(50) NOT NULL,
    sensitivity VARCHAR(20) NOT NULL,
    year INTEGER NOT NULL,
    month VARCHAR(20) NOT NULL,
    day VARCHAR(20) NOT NULL,
    payload TEXT NOT NULL,
    computed_at TIMESTAMP NOT NULL,
    PRIMARY KEY (farm_id, weight_col, sensitivity, year, month, day)
);
"""


def ensure_anomaly_snapshots_table() -> None:
    with get_conn() as conn:
        cur = conn.cursor()
        for stmt in ANOMALY_SNAPSHOTS_DDL.strip().split(";"):
            sql = stmt.strip()
            if sql:
                cur.execute(sql)


def _is_overall(values: Any) -> bool:
    if values is None:
        return True
    if isinstance(values, str):
        return values in ("Overall", "All", "")
    if isinstance(values, (list, tuple)):
        if not values:
            return True
        return all(str(v) in ("Overall", "All") for v in values)
    return False


def is_snapshot_eligible(body: Any) -> bool:
    """Default sidebar state that cron precomputes (Overall dims, full month/day)."""
    if getattr(body, "active_only", False):
        return False
    if str(getattr(body, "month", "All") or "All") not in ("All",):
        return False
    if str(getattr(body, "day", "All") or "All") not in ("All",):
        return False
    for attr in ("sex", "treatment", "breed", "mob", "eid"):
        if not _is_overall(getattr(body, attr, None)):
            return False
    return True


def read_snapshot(
    *,
    farm_id: str,
    weight_col: str,
    sensitivity: str,
    year: int,
    month: str = "All",
    day: str = "All",
) -> dict[str, Any] | None:
    ensure_anomaly_snapshots_table()
    row = fetch_one(
        """
        SELECT payload, computed_at FROM anomaly_snapshots
        WHERE farm_id = %s AND weight_col = %s AND sensitivity = %s
          AND year = %s AND month = %s AND day = %s
        """,
        (farm_id, weight_col, sensitivity, int(year), str(month), str(day)),
    )
    if not row or not row.get("payload"):
        return None
    try:
        payload = json.loads(row["payload"])
    except (TypeError, json.JSONDecodeError):
        logger.warning("corrupt anomaly snapshot for farm %s", farm_id)
        return None
    if isinstance(payload, dict):
        payload = dict(payload)
        computed = row.get("computed_at")
        if computed is not None and hasattr(computed, "isoformat"):
            payload["snapshot_at"] = computed.isoformat()
        else:
            payload["snapshot_at"] = str(computed) if computed else None
        payload["from_snapshot"] = True
    return payload


def write_snapshot(
    *,
    farm_id: str,
    weight_col: str,
    sensitivity: str,
    year: int,
    month: str,
    day: str,
    payload: dict[str, Any],
) -> None:
    ensure_anomaly_snapshots_table()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    body = json.dumps(payload, separators=(",", ":"), default=str)
    engine = get_active_engine()
    with get_conn() as conn:
        cur = conn.cursor()
        if engine == "duckdb":
            cur.execute(
                """
                INSERT OR REPLACE INTO anomaly_snapshots
                (farm_id, weight_col, sensitivity, year, month, day, payload, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [farm_id, weight_col, sensitivity, int(year), str(month), str(day), body, now],
            )
        else:
            cur.execute(
                """
                INSERT INTO anomaly_snapshots
                (farm_id, weight_col, sensitivity, year, month, day, payload, computed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (farm_id, weight_col, sensitivity, year, month, day)
                DO UPDATE SET payload = EXCLUDED.payload, computed_at = EXCLUDED.computed_at
                """,
                (farm_id, weight_col, sensitivity, int(year), str(month), str(day), body, now),
            )


def delete_farm_snapshots(farm_id: str) -> None:
    try:
        ensure_anomaly_snapshots_table()
    except Exception:
        return
    with get_conn() as conn:
        cur = conn.cursor()
        ph = "?" if get_active_engine() == "duckdb" else "%s"
        cur.execute(f"DELETE FROM anomaly_snapshots WHERE farm_id = {ph}", [farm_id])


def farm_max_year(farm_id: str, farm_df=None) -> int | None:
    from app.services.filter_service import FilterService

    df = farm_df if farm_df is not None else FilterService.load_farm_dataframe(farm_id, use_cache=True)
    if df is None or df.empty or "date" not in df.columns:
        return None
    return int(df["date"].dt.year.max())


def _sanitize_records(records: list[dict]) -> list[dict]:
    import numpy as np
    import pandas as pd

    clean = []
    for r in records:
        row = {}
        for k, v in r.items():
            if v is None or pd.isna(v):
                row[k] = None
            elif hasattr(v, "isoformat"):
                row[k] = v.isoformat()
            elif isinstance(v, (int, np.integer)):
                row[k] = int(v)
            elif isinstance(v, (float, np.floating)):
                row[k] = round(float(v), 2)
            else:
                row[k] = v
        clean.append(row)
    return clean


def compute_analyze_dict(
    *,
    farm_id: str,
    year: int,
    month: str = "All",
    day: str = "All",
    weight_col: str = "weight",
    sensitivity: str = "Medium",
    farm_df=None,
) -> dict[str, Any]:
    """Build the analyze API payload with raw EIDs (admin view) for durable storage."""
    from app.models.schemas import AnomalyAnalyzeRequest
    from app.services.anomaly_service import (
        anomaly_period,
        anomaly_source_note,
        classify_anomalies,
        get_anomaly_scores,
        herd_shift_note,
    )
    from app.services.filter_service import FilterService

    body = AnomalyAnalyzeRequest(
        farm_id=farm_id,
        year=year,
        month=month,
        day=day,
        sex=["Overall"],
        treatment=["Overall"],
        breed=["Overall"],
        mob=["Overall"],
        eid=["Overall"],
        active_only=False,
        weight_col=weight_col,
        sensitivity=sensitivity,
    )
    df = farm_df if farm_df is not None else FilterService.load_farm_dataframe(farm_id, use_cache=True)
    if df.empty:
        return {
            "available": False,
            "message": f"No data found for farm {farm_id}.",
            "animals": [],
            "counts": {"high": 0, "medium": 0, "low": 0, "normal": 0, "insufficient": 0, "total": 0},
            "farm_id": farm_id,
            "weight_col": weight_col,
            "sensitivity": sensitivity,
        }

    period = anomaly_period(body.year, body.month, body.day)
    filtered_df = FilterService.filter_data(df, body, is_admin=True)
    selected_eids = list(filtered_df["eid"].dropna().astype(str).unique()) if not filtered_df.empty else []

    res = get_anomaly_scores(
        farm_id,
        df,
        period=period,
        selected_eids=selected_eids,
        weight_col=weight_col,
    )
    loaded = res["loaded"]
    if not loaded["available"] or res["scores"] is None:
        return {
            "available": False,
            "message": loaded.get("message") or "Anomaly detection is not available for this period.",
            "animals": [],
            "counts": {"high": 0, "medium": 0, "low": 0, "normal": 0, "insufficient": 0, "total": 0},
            "farm_id": farm_id,
            "weight_col": weight_col,
            "sensitivity": sensitivity,
        }

    classified = classify_anomalies(res["scores"], res["peer"], sensitivity=sensitivity)
    if res["scope"] == "farm":
        shown_set = set(res["shown"])
        display_df = classified[classified["eid"].isin(shown_set)].copy()
    else:
        display_df = classified.copy()

    counts = {
        "high": int((display_df["severity"] == "High").sum()),
        "medium": int((display_df["severity"] == "Medium").sum()),
        "low": int((display_df["severity"] == "Low").sum()),
        "normal": int((display_df["severity"] == "Normal").sum()),
        "insufficient": int((display_df["severity"] == "Not enough data").sum()),
        "total": len(display_df),
    }
    raw_records = _sanitize_records(display_df.to_dict(orient="records"))
    w_start = res["scores"]["window_start"]
    w_end = res["scores"]["window_end"]
    pweight_warning = (
        "pweight is smoothed by the data provider. The sensitivity settings were calibrated on observed weights; "
        "smoothed values are correlated from day to day, so expect noticeably more animals flagged High, "
        "and single-reading flags mean little."
        if weight_col == "pweight"
        else None
    )
    return {
        "available": True,
        "message": None,
        "farm_id": farm_id,
        "weight_col": weight_col,
        "sensitivity": sensitivity,
        "window_start": w_start.isoformat() if w_start else None,
        "window_end": w_end.isoformat() if w_end else None,
        "period_label": period["label"] if period else None,
        "scope": res["scope"],
        "n_reference": res["n_reference"],
        "sigma": round(float(res["scores"]["sigma"]), 2) if res["scores"]["sigma"] is not None else None,
        "counts": counts,
        "source_note": anomaly_source_note(weight_col, res["scores"]),
        "herd_shift_note": herd_shift_note(res["scores"]),
        "pweight_warning": pweight_warning,
        "animals": raw_records,
    }


def refresh_farm_snapshot(
    farm_id: str,
    *,
    weight_col: str = "weight",
    sensitivity: str = "Medium",
) -> dict[str, Any]:
    """Compute and persist the default Overall/All snapshot for a farm's latest year."""
    from app.services.filter_service import FilterService

    df = FilterService.load_farm_dataframe(farm_id, use_cache=True)
    year = farm_max_year(farm_id, df)
    if year is None:
        return {"farm_id": farm_id, "ok": False, "message": "no data"}
    payload = compute_analyze_dict(
        farm_id=farm_id,
        year=year,
        month="All",
        day="All",
        weight_col=weight_col,
        sensitivity=sensitivity,
        farm_df=df,
    )
    write_snapshot(
        farm_id=farm_id,
        weight_col=weight_col,
        sensitivity=sensitivity,
        year=year,
        month="All",
        day="All",
        payload=payload,
    )
    return {
        "farm_id": farm_id,
        "ok": True,
        "year": year,
        "available": payload.get("available"),
        "total": (payload.get("counts") or {}).get("total"),
    }
