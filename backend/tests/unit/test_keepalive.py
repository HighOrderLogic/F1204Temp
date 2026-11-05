from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.config import get_settings
from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_keepalive_requires_secret(client: TestClient):
    res = client.get("/api/health/db")
    assert res.status_code == 401


def test_keepalive_rejects_wrong_secret(client: TestClient):
    res = client.get("/api/health/db", params={"cron_secret": "nope"})
    assert res.status_code == 401


def test_keepalive_queries_database(client: TestClient, monkeypatch):
    monkeypatch.setattr("app.routers.keepalive.fetch_one", lambda q, p=None: {"ok": 1})
    res = client.get("/api/health/db", params={"cron_secret": get_settings().cron_secret})
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "database": "up"}


def test_keepalive_reports_unreachable(client: TestClient, monkeypatch):
    def boom(q, p=None):
        raise RuntimeError("down")

    monkeypatch.setattr("app.routers.keepalive.fetch_one", boom)
    res = client.get("/api/health/db", params={"cron_secret": get_settings().cron_secret})
    assert res.status_code == 503


def test_keepalive_in_production_rejects_header_without_secret(client: TestClient, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("VERCEL_ENV", "production")
    res = client.get("/api/health/db", headers={"x-vercel-cron": "1"})
    assert res.status_code == 401


def test_keepalive_in_dev_accepts_simulated_cron_header(client: TestClient, monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.setattr("app.routers.keepalive.fetch_one", lambda q, p=None: {"ok": 1})
    res = client.get("/api/health/db", headers={"x-vercel-cron": "1"})
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "database": "up"}

