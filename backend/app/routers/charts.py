from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.auth.dependencies import CurrentUser, assert_farm_access, get_current_user
from app.models.schemas import DistributionRequest, TimeseriesRequest
from app.services import sql_agg

router = APIRouter()


@router.post("/timeseries")
def timeseries(body: TimeseriesRequest, user: Annotated[CurrentUser, Depends(get_current_user)]):
    assert_farm_access(user, body.farm_id)
    try:
        measures = body.measures if (hasattr(body, "measures") and body.measures) else [body.measure]
        charts = []
        primary_res = None
        for m in measures:
            res = sql_agg.timeseries_sql(body, user.can_view_eid, m, body.show_smooth)
            if primary_res is None:
                primary_res = res
            charts.append({"measure": m, **res})
        
        response_payload = dict(primary_res) if primary_res else {}
        response_payload["charts"] = charts
        return response_payload
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/timeseries-grain")
def timeseries_grain(body: TimeseriesRequest, user: Annotated[CurrentUser, Depends(get_current_user)]):
    """Dimensional daily grain for client-side chart assembly (sub-second checkbox updates)."""
    assert_farm_access(user, body.farm_id)
    try:
        return sql_agg.timeseries_grain_sql(body, body.measure)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/distribution")
def distribution(body: DistributionRequest, user: Annotated[CurrentUser, Depends(get_current_user)]):
    assert_farm_access(user, body.farm_id)
    try:
        measures = body.measures if (hasattr(body, "measures") and body.measures) else [body.measure]
        charts = []
        primary_res = None
        for m in measures:
            res = sql_agg.distribution_sql(body, user.can_view_eid, m, body.hist_bins)
            if primary_res is None:
                primary_res = res
            charts.append({"measure": m, **res})
        response_payload = dict(primary_res) if primary_res else {}
        response_payload["charts"] = charts
        return response_payload
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/custom")
def custom_chart(body: dict, user: Annotated[CurrentUser, Depends(get_current_user)]):
    from app.models.schemas import CustomChartRequest
    from app.services.data_service import DataService

    req = CustomChartRequest(**body)
    assert_farm_access(user, req.farm_id)
    filtered, grouped, _, _ = DataService.get_filtered_data(req, user.can_view_eid)
    df = grouped if not grouped.empty else filtered

    chart_type = req.chart_type
    x_col = req.x_col
    y_col = req.y_col
    group_col = req.group_col

    traces = []
    if chart_type in ("line", "bar", "area", "scatter"):
        gcols = [x_col]
        if group_col and group_col in df.columns:
            gcols.append(group_col)
        agg = df.groupby(gcols)[y_col].agg(req.agg_fun).reset_index()
        if group_col and group_col in agg.columns:
            for grp, gdf in agg.groupby(group_col):
                traces.append(
                    {
                        "x": gdf[x_col].astype(str).tolist(),
                        "y": gdf[y_col].tolist(),
                        "type": chart_type if chart_type != "area" else "scatter",
                        "mode": "lines" if chart_type in ("line", "area") else "markers",
                        "fill": "tozeroy" if chart_type == "area" else None,
                        "name": str(grp),
                    }
                )
        else:
            traces.append(
                {
                    "x": agg[x_col].astype(str).tolist(),
                    "y": agg[y_col].tolist(),
                    "type": chart_type if chart_type != "area" else "scatter",
                    "mode": "lines" if chart_type in ("line", "area") else "markers",
                    "name": req.title,
                }
            )
    elif chart_type == "hist":
        vals = df[y_col].dropna().tolist()
        traces.append({"x": vals, "type": "histogram", "nbinsx": req.hist_bins, "name": req.title})
    elif chart_type == "box":
        if group_col and group_col in df.columns:
            for grp, gdf in df.groupby(group_col):
                traces.append({"y": gdf[y_col].dropna().tolist(), "type": "box", "name": str(grp)})
        else:
            traces.append({"y": df[y_col].dropna().tolist(), "type": "box", "name": req.title})

    layout = {"title": req.title, "xaxis": {"title": x_col}, "yaxis": {"title": y_col}}
    return {"data": traces, "layout": layout}
