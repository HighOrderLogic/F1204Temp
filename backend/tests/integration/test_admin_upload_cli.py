from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADMIN_CLI = REPO_ROOT / "scripts" / "admin.py"

UPDATED = {"status": "updated", "animals": 376, "rows": 68432, "data_through": "2026-10-20",
           "model_version": "xgb-recursive-v2", "seconds": 2.1}


@pytest.fixture
def admin_cli():
    spec = importlib.util.spec_from_file_location("admin_cli", ADMIN_CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def upload_args(tmp_path):
    csv = tmp_path / "KF_2026-10-20.csv"
    csv.write_text("EID,Date\n982 1,20/10/2026\n")

    def make(skip_forecast=False):
        return argparse.Namespace(farm="KF", file=str(csv), mode="skip", api="http://localhost:8000", token="t",
                                  skip_forecast=skip_forecast)

    return make


@pytest.fixture
def server(monkeypatch):
    """Stands in for the API: records each POST and answers with `status` and `body`."""
    fake = SimpleNamespace(posts=[], status=200, body={"rows_inserted": 1, "forecasts": UPDATED})

    def post(url, **kwargs):
        fake.posts.append({"url": url, **kwargs})
        return SimpleNamespace(ok=fake.status < 400, status_code=fake.status, json=lambda: fake.body,
                               text=str(fake.body))

    monkeypatch.setattr("requests.post", post)
    return fake


@pytest.mark.integration
def test_upload_asks_the_server_to_refresh_forecasts(admin_cli, upload_args, server, capsys):
    admin_cli.cmd_upload(upload_args())
    assert server.posts[0]["url"].endswith("/api/admin/farms/KF/upload")
    assert server.posts[0]["data"]["forecast"] == "true"
    assert "Forecasts refreshed for farm KF: 376 animals" in capsys.readouterr().out


@pytest.mark.integration
def test_failed_upload_exits_with_an_error(admin_cli, upload_args, server):
    server.status, server.body = 400, {"detail": "bad file"}
    with pytest.raises(SystemExit, match="Upload failed"):
        admin_cli.cmd_upload(upload_args())


@pytest.mark.integration
def test_skip_forecast_flag(admin_cli, upload_args, server, capsys):
    server.body = {"rows_inserted": 1, "forecasts": {"status": "skipped"}}
    admin_cli.cmd_upload(upload_args(skip_forecast=True))
    assert server.posts[0]["data"]["forecast"] == "false"
    out = capsys.readouterr().out
    assert "admin.py forecast --farm KF" in out
    assert "--token t" not in out  # the hint never echoes the token


@pytest.mark.integration
def test_forecast_failure_after_upload_says_how_to_retry(admin_cli, upload_args, server):
    detail = "Forecasts were not refreshed: database unavailable. Retry with POST /api/admin/farms/KF/forecasts/refresh"
    server.body = {"rows_inserted": 1, "forecasts": {"status": "failed", "detail": detail}}
    with pytest.raises(SystemExit) as exc:
        admin_cli.cmd_upload(upload_args())
    assert "Upload succeeded" in str(exc.value)
    assert "database unavailable" in str(exc.value)


@pytest.mark.integration
def test_forecast_command_calls_the_refresh_endpoint(admin_cli, server, capsys):
    server.body = UPDATED
    admin_cli.cmd_forecast(argparse.Namespace(farm="KF", api="http://localhost:8000", token="t"))
    assert server.posts[0]["url"] == "http://localhost:8000/api/admin/farms/KF/forecasts/refresh"
    assert server.posts[0]["headers"] == {"Authorization": "Bearer t"}
    assert "Forecasts refreshed for farm KF" in capsys.readouterr().out


@pytest.mark.integration
def test_forecast_command_failure_exits_with_an_error(admin_cli, server):
    server.status, server.body = 500, {"detail": "Forecasts were not refreshed: boom."}
    with pytest.raises(SystemExit, match="HTTP 500"):
        admin_cli.cmd_forecast(argparse.Namespace(farm="KF", api="http://localhost:8000", token="t"))
