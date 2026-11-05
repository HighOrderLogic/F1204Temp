from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.key_guard import install_pseudonym_key_guard
from app.routers import (
    admin,
    animals,
    anomaly_fast,
    anomalies,
    auth,
    charts,
    cohorts,
    email_schedules,
    farms,
    forecasts,
    keepalive,
    filters,
    reports,
    summary,
)

settings = get_settings()
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
IS_VERCEL = bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))

app = FastAPI(title="Livestock Dashboard API", version=settings.app_version)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Browser-facing 404s get a styled HTML page; API routes stay JSON."""
    # A StaticFiles sub-mount rewrites the scope path, so rebuild the full request path.
    scope = request.scope
    full_path = "/" + (scope.get("root_path", "") + scope.get("path", "")).lstrip("/")
    if exc.status_code == 404 and not full_path.startswith("/api"):
        page = FRONTEND_DIR / "404.html"
        if page.is_file():
            return HTMLResponse(status_code=404, content=page.read_text(encoding="utf-8"))
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)

install_pseudonym_key_guard(app)  # before CORS, so CORS still wraps its 503 responses
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?|https://.*\.vercel\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": settings.app_version}


app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(farms.router, prefix="/api/farms", tags=["farms"])
app.include_router(forecasts.router, prefix="/api/forecasts", tags=["forecasts"])
app.include_router(filters.router, prefix="/api/filters", tags=["filters"])
app.include_router(animals.router, prefix="/api/data", tags=["data"])
app.include_router(summary.router, prefix="/api/summary", tags=["summary"])
app.include_router(charts.router, prefix="/api/charts", tags=["charts"])
app.include_router(cohorts.router, prefix="/api/cohorts", tags=["cohorts"])
app.include_router(anomaly_fast.router, prefix="/api/anomalies", tags=["anomalies"])
app.include_router(anomalies.router, prefix="/api/anomalies", tags=["anomalies"])
app.include_router(reports.router, prefix="/api/reports", tags=["reports"])
app.include_router(email_schedules.router, prefix="/api/email", tags=["email"])
app.include_router(keepalive.router, prefix="/api/health", tags=["health"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])

# Serve HTML/CSS/JS from FastAPI only for local one-process mode.
# On Vercel, static files come from vercel.json routes — do not mount here.
if not IS_VERCEL and FRONTEND_DIR.is_dir():

    @app.get("/")
    def root():
        return RedirectResponse(url="/login.html")

    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
