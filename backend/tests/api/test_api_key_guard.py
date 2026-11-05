from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.config import get_settings


@pytest.fixture
def no_pseudonym_key(monkeypatch):
    monkeypatch.delenv("EID_PSEUDONYM_KEY", raising=False)
    monkeypatch.setattr(get_settings(), "eid_pseudonym_key", "")


@pytest.mark.integration
@pytest.mark.parametrize("method,path", [("get", "/api/health"), ("post", "/api/auth/login")])
def test_api_refuses_requests_without_pseudonym_key(client: TestClient, no_pseudonym_key, method, path):
    """Every API route answers 503 with a fix-it message while EID_PSEUDONYM_KEY is missing."""
    res = getattr(client, method)(path)
    assert res.status_code == 503
    body = res.json()
    assert body["code"] == "EID_PSEUDONYM_KEY_MISSING"
    assert "EID_PSEUDONYM_KEY" in body["detail"]
    assert "backend/.env" in body["detail"]


@pytest.mark.integration
def test_api_serves_requests_with_pseudonym_key(client: TestClient):
    res = client.get("/api/health")
    assert res.status_code == 200
