"""
Vercel ASGI entrypoint for charts / data / reports / email (heavy deps).

Auth, health, and filter choices are served by api/light/index.py instead.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.key_guard import install_pseudonym_key_guard
from app.routers import keepalive

settings = get_settings()

app = FastAPI(title="Livestock Dashboard API", version=settings.app_version)
install_pseudonym_key_guard(app)  # before CORS, so CORS still wraps its 503 responses
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?|https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(keepalive.router, prefix="/api/health", tags=["health"])

_loaded: set[str] = set()


def _load(group: str) -> None:
    """Import router groups lazily. charts/summary avoid pandas; animals/reports do not."""
    if group in _loaded:
        return
    if group == "charts":
        from app.routers import charts, summary

        app.include_router(charts.router, prefix="/api/charts", tags=["charts"])
        app.include_router(summary.router, prefix="/api/summary", tags=["summary"])
    elif group == "data":
        from app.routers import animals, farms, forecasts

        app.include_router(farms.router, prefix="/api/farms", tags=["farms"])
        app.include_router(forecasts.router, prefix="/api/forecasts", tags=["forecasts"])
        app.include_router(animals.router, prefix="/api/data", tags=["data"])
    elif group == "heavy":
        from app.routers import admin, anomalies, cohorts, email_schedules, reports

        app.include_router(cohorts.router, prefix="/api/cohorts", tags=["cohorts"])
        app.include_router(anomalies.router, prefix="/api/anomalies", tags=["anomalies"])
        app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
        app.include_router(email_schedules.router, prefix="/api/email", tags=["email"])
        app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
    _loaded.add(group)


@app.middleware("http")
async def ensure_routers(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/health"):
        pass
    elif path.startswith("/api/charts") or path.startswith("/api/summary"):
        _load("charts")
    elif path.startswith("/api/data") or path.startswith("/api/farms") or path.startswith("/api/forecasts"):
        _load("data")
    elif path.startswith("/api/"):
        _load("charts")
        _load("data")
        _load("heavy")
    return await call_next(request)
