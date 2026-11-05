"""
Slim Vercel entrypoint for login / health / filters / summary / charts grain.

No pandas/matplotlib/reportlab. First dashboard paint reuses the same warm instance as login.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.key_guard import install_pseudonym_key_guard
from app.routers import anomaly_fast, auth, charts, filters, keepalive, summary

settings = get_settings()

app = FastAPI(title="Livestock Dashboard API (light)", version=settings.app_version)
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
    return {"status": "ok", "version": settings.app_version, "tier": "light"}


app.include_router(keepalive.router, prefix="/api/health", tags=["health"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(filters.router, prefix="/api/filters", tags=["filters"])
app.include_router(summary.router, prefix="/api/summary", tags=["summary"])
app.include_router(charts.router, prefix="/api/charts", tags=["charts"])
app.include_router(anomaly_fast.router, prefix="/api/anomalies", tags=["anomalies"])
