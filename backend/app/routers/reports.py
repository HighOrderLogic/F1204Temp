from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse

from app.auth.dependencies import CurrentUser, assert_farm_access, get_current_user
from app.db import fetch_one
from app.models.schemas import ChartPngRequest, ReportGenerateRequest
from app.services.data_service import DataService

router = APIRouter()


@router.post("/chart.png")
def chart_png(body: ChartPngRequest, user: Annotated[CurrentUser, Depends(get_current_user)]):
    from app.services.report_generator import ReportGenerator

    assert_farm_access(user, body.filters.farm_id)
    filtered, grouped, _, _ = DataService.get_filtered_data(body.filters, user.can_view_eid)
    png = ReportGenerator.generate_chart_png(
        grouped, body.chart_source, body.filters.measure, body.filters, filtered_df=filtered
    )
    return Response(content=png, media_type="image/png")


@router.post("/generate")
def generate_report(body: ReportGenerateRequest, user: Annotated[CurrentUser, Depends(get_current_user)]):
    from app.services.report_generator import ReportGenerator

    assert_farm_access(user, body.farm_id)
    farm = fetch_one("SELECT farm_name FROM farms WHERE farm_id = %s", (body.farm_id,))
    farm_name = farm["farm_name"] if farm else body.farm_id
    filtered, grouped, _, _ = DataService.get_filtered_data(body.filters, user.can_view_eid)
    if filtered.empty:
        raise HTTPException(
            status_code=400,
            detail="No data available for the selected filters. Adjust your filters and try again.",
        )

    if body.format.upper() == "HTML":
        html = ReportGenerator.generate_html(
            body.filters, grouped, body.charts, farm_name, filtered_df=filtered
        )
        return StreamingResponse(
            iter([html]),
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="{body.filename}.html"'},
        )

    pdf = ReportGenerator.generate_pdf(
        body.filters, grouped, body.charts, farm_name, filtered_df=filtered
    )
    return StreamingResponse(
        iter([pdf]),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{body.filename}.pdf"'},
    )
