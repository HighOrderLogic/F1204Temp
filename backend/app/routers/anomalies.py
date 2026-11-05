from __future__ import annotations

import csv
import io
import logging
from typing import Annotated

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.auth.cron import assert_cron_authorized
from app.auth.dependencies import CurrentUser, assert_farm_access, get_current_user
from app.config import get_settings
from app.models.schemas import AnomalyAnalyzeRequest, AnomalyDetailRequest
from app.services.anomaly_service import (
    anomaly_detail_series,
    anomaly_period,
    anomaly_source_note,
    classify_anomalies,
    get_anomaly_scores,
    herd_shift_note,
)
from app.services import anomaly_snapshot
from app.services.filter_service import FilterService
from app.utils.anonymize import (
    PseudonymCollisionError,
    anonymize_eid,
    anonymize_records,
    resolve_animal_id,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.api_route("/warm", methods=["GET", "POST"])
def warm_anomaly_runtime(
    x_cron_secret: str | None = Header(None, alias="X-Cron-Secret"),
    x_vercel_cron: str | None = Header(None, alias="x-vercel-cron"),
    authorization: str | None = Header(None),
    cron_secret: str | None = Query(None),
    farm_id: str | None = Query(None),
    preload: bool = Query(False),
    compute: bool = Query(False),
):
    """Keep the heavy Vercel function warm and optionally precompute analyze snapshots.

    cron-job.org (every 5 minutes) — recommended:
    https://YOUR-APP.vercel.app/api/anomalies/warm?cron_secret=YOUR_CRON_SECRET&preload=1&compute=1

    ``compute=1`` writes durable DB snapshots so Anomalies tab loads stay fast even when
    Vercel routes the user to a different cold instance.
    """
    assert_cron_authorized(
        x_cron_secret=x_cron_secret,
        x_vercel_cron=x_vercel_cron,
        authorization=authorization,
        cron_secret=cron_secret,
    )
    # Touch pandas + the scoring module so cold imports happen on the cron, not the user.
    _ = pd.__version__
    from app.services import anomaly_service as _anomaly_service  # noqa: F401

    settings = get_settings()
    farm_ids: list[str]
    if farm_id and farm_id.strip():
        farm_ids = [farm_id.strip()]
    elif preload or compute:
        from app.db import fetch_all

        try:
            rows = fetch_all("SELECT farm_id FROM farms ORDER BY farm_id")
            farm_ids = [str(r["farm_id"]) for r in rows if r.get("farm_id")]
        except Exception:
            logger.exception("anomaly warm could not list farms")
            farm_ids = [(settings.default_farm_id or "KF").strip()]
    else:
        farm_ids = [(settings.default_farm_id or "KF").strip()]

    preloaded: dict[str, int] = {}
    if preload:
        for fid in farm_ids:
            try:
                df = FilterService.load_farm_dataframe(fid, use_cache=True)
                preloaded[fid] = int(len(df))
            except Exception:
                logger.exception("anomaly warm preload failed for farm %s", fid)
                raise HTTPException(status_code=503, detail="Anomaly warm preload failed") from None

    snapshots: list[dict] = []
    if compute:
        for fid in farm_ids:
            try:
                snapshots.append(anomaly_snapshot.refresh_farm_snapshot(fid))
            except Exception:
                logger.exception("anomaly snapshot compute failed for farm %s", fid)
                raise HTTPException(status_code=503, detail="Anomaly snapshot compute failed") from None

    return {
        "status": "ok",
        "warm": "anomalies",
        "farm_ids": farm_ids,
        "preloaded_rows": preloaded or None,
        "snapshots": snapshots or None,
    }


def _sanitize_records(records: list[dict]) -> list[dict]:
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


@router.post("/analyze")
def analyze_anomalies(
    body: AnomalyAnalyzeRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    assert_farm_access(user, body.farm_id)

    # Fast path: durable snapshot written by cron (?compute=1). Any cold instance can serve this.
    if body.year is not None and anomaly_snapshot.is_snapshot_eligible(body):
        cached = anomaly_snapshot.read_snapshot(
            farm_id=body.farm_id,
            weight_col=body.weight_col,
            sensitivity=body.sensitivity,
            year=int(body.year),
            month=str(body.month or "All"),
            day=str(body.day or "All"),
        )
        if cached is not None:
            animals = anonymize_records(list(cached.get("animals") or []), user.can_view_eid)
            out = dict(cached)
            out["animals"] = animals
            out["from_snapshot"] = True
            return out

    period = anomaly_period(body.year, body.month, body.day)
    farm_df = FilterService.load_farm_dataframe(body.farm_id, use_cache=True)
    if farm_df.empty:
        return {
            "available": False,
            "message": f"No data found for farm {body.farm_id}.",
            "animals": [],
            "counts": {"high": 0, "medium": 0, "low": 0, "normal": 0, "insufficient": 0, "total": 0},
        }

    # Filter dataframe using active sidebar filters to obtain selected animals
    filtered_df = FilterService.filter_data(farm_df, body, user.can_view_eid)
    selected_eids = list(filtered_df["eid"].dropna().astype(str).unique()) if not filtered_df.empty else []

    res = get_anomaly_scores(
        body.farm_id,
        farm_df,
        period=period,
        selected_eids=selected_eids,
        weight_col=body.weight_col,
    )

    loaded = res["loaded"]
    if not loaded["available"] or res["scores"] is None:
        return {
            "available": False,
            "message": loaded.get("message") or "Anomaly detection is not available for this period.",
            "animals": [],
            "counts": {"high": 0, "medium": 0, "low": 0, "normal": 0, "insufficient": 0, "total": 0},
        }

    classified = classify_anomalies(res["scores"], res["peer"], sensitivity=body.sensitivity)

    # Restrict displayed animals to `shown` if reference fell back to whole farm
    if res["scope"] == "farm":
        shown_set = set(res["shown"])
        display_df = classified[classified["eid"].isin(shown_set)].copy()
    else:
        display_df = classified.copy()

    # Calculate severity counts across the displayed animals
    counts = {
        "high": int((display_df["severity"] == "High").sum()),
        "medium": int((display_df["severity"] == "Medium").sum()),
        "low": int((display_df["severity"] == "Low").sum()),
        "normal": int((display_df["severity"] == "Normal").sum()),
        "insufficient": int((display_df["severity"] == "Not enough data").sum()),
        "total": len(display_df),
    }

    raw_records = _sanitize_records(display_df.to_dict(orient="records"))
    masked_records = anonymize_records(raw_records, user.can_view_eid)

    source_note = anomaly_source_note(body.weight_col, res["scores"])
    shift_note = herd_shift_note(res["scores"])

    pweight_warning = (
        "pweight is smoothed by the data provider. The sensitivity settings were calibrated on observed weights; "
        "smoothed values are correlated from day to day, so expect noticeably more animals flagged High, "
        "and single-reading flags mean little."
        if body.weight_col == "pweight"
        else None
    )

    w_start = res["scores"]["window_start"]
    w_end = res["scores"]["window_end"]

    # Persist default Overall/All runs so later cold instances (and farm switches) stay fast.
    if body.year is not None and anomaly_snapshot.is_snapshot_eligible(body):
        try:
            anomaly_snapshot.write_snapshot(
                farm_id=body.farm_id,
                weight_col=body.weight_col,
                sensitivity=body.sensitivity,
                year=int(body.year),
                month=str(body.month or "All"),
                day=str(body.day or "All"),
                payload={
                    "available": True,
                    "message": None,
                    "farm_id": body.farm_id,
                    "weight_col": body.weight_col,
                    "sensitivity": body.sensitivity,
                    "window_start": w_start.isoformat() if w_start else None,
                    "window_end": w_end.isoformat() if w_end else None,
                    "period_label": period["label"] if period else None,
                    "scope": res["scope"],
                    "n_reference": res["n_reference"],
                    "sigma": round(float(res["scores"]["sigma"]), 2) if res["scores"]["sigma"] is not None else None,
                    "counts": counts,
                    "source_note": source_note,
                    "herd_shift_note": shift_note,
                    "pweight_warning": pweight_warning,
                    "animals": raw_records,
                },
            )
        except Exception:
            logger.exception("failed to persist anomaly snapshot for %s", body.farm_id)

    return {
        "available": True,
        "message": None,
        "farm_id": body.farm_id,
        "weight_col": body.weight_col,
        "sensitivity": body.sensitivity,
        "window_start": w_start.isoformat() if w_start else None,
        "window_end": w_end.isoformat() if w_end else None,
        "period_label": period["label"] if period else None,
        "scope": res["scope"],
        "n_reference": res["n_reference"],
        "sigma": round(float(res["scores"]["sigma"]), 2) if res["scores"]["sigma"] is not None else None,
        "counts": counts,
        "source_note": source_note,
        "herd_shift_note": shift_note,
        "pweight_warning": pweight_warning,
        "animals": masked_records,
    }


@router.post("/detail")
def animal_detail(
    body: AnomalyDetailRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    assert_farm_access(user, body.farm_id)

    farm_df = FilterService.load_farm_dataframe(body.farm_id, use_cache=True)
    if farm_df.empty:
        raise HTTPException(status_code=404, detail=f"No data for farm {body.farm_id}")

    farm_eids = list(farm_df["eid"].dropna().astype(str).unique())
    try:
        real_eid = resolve_animal_id(body.animal, farm_eids, user.can_view_eid)
    except PseudonymCollisionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if real_eid is None:
        raise HTTPException(status_code=404, detail="Animal not found on this farm")

    period = anomaly_period(body.year, body.month, body.day)
    filtered_df = FilterService.filter_data(farm_df, body, user.can_view_eid)
    selected_eids = list(filtered_df["eid"].dropna().astype(str).unique()) if not filtered_df.empty else []

    res = get_anomaly_scores(
        body.farm_id,
        farm_df,
        period=period,
        selected_eids=selected_eids,
        weight_col=body.weight_col,
    )

    if not res["loaded"]["available"] or res["scores"] is None:
        raise HTTPException(status_code=404, detail="Anomaly scores unavailable for the selected period")

    detail = anomaly_detail_series(res["scores"], real_eid, sensitivity=body.sensitivity)
    if detail is None:
        raise HTTPException(status_code=404, detail="No anomaly detail series for that animal")

    if not user.can_view_eid:
        detail["eid"] = anonymize_eid(real_eid)

    return detail


@router.post("/export.csv")
def export_anomalies_csv(
    body: AnomalyAnalyzeRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    if not user.can_export_raw:
        raise HTTPException(status_code=403, detail="Admin access required to export anomaly data")

    assert_farm_access(user, body.farm_id)
    period = anomaly_period(body.year, body.month, body.day)
    farm_df = FilterService.load_farm_dataframe(body.farm_id, use_cache=True)
    if farm_df.empty:
        raise HTTPException(status_code=400, detail="No data available for export")

    filtered_df = FilterService.filter_data(farm_df, body, user.can_view_eid)
    selected_eids = list(filtered_df["eid"].dropna().astype(str).unique()) if not filtered_df.empty else []

    res = get_anomaly_scores(
        body.farm_id,
        farm_df,
        period=period,
        selected_eids=selected_eids,
        weight_col=body.weight_col,
    )

    if not res["loaded"]["available"] or res["scores"] is None:
        raise HTTPException(status_code=400, detail="Anomaly scores unavailable for the selected period")

    classified = classify_anomalies(res["scores"], res["peer"], sensitivity=body.sensitivity)

    if res["scope"] == "farm":
        shown_set = set(res["shown"])
        export_df = classified[classified["eid"].isin(shown_set)].copy()
    else:
        export_df = classified.copy()

    export_cols = [
        "eid", "severity", "anomaly_types", "n_readings", "change_kg_7d", "change_pct_7d",
        "shortfall_kg", "run_days", "raw_growth", "peer_z", "peer_definition",
        "cusum_max", "point_z", "point_date", "reason",
    ]
    for c in export_cols:
        if c not in export_df.columns:
            export_df[c] = None

    export_df = export_df[export_cols]

    output = io.StringIO()
    export_df.to_csv(output, index=False, lineterminator="\n")
    output.seek(0)

    filename = f"anomalies_{body.farm_id}_{body.weight_col}_{body.sensitivity.lower()}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
