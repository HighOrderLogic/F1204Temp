from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADMIN_CLI = REPO_ROOT / "scripts" / "admin.py"


@pytest.fixture
def admin_cli():
    spec = importlib.util.spec_from_file_location("admin_cli_unit", ADMIN_CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fake_api(monkeypatch):
    """Intercepts requests.get, post, put, delete and records them."""
    state = SimpleNamespace(
        calls=[],
        status_code=200,
        response_body={},
        headers={"content-type": "application/json"},
    )

    def _make_resp():
        return SimpleNamespace(
            ok=state.status_code < 400,
            status_code=state.status_code,
            json=lambda: state.response_body,
            text=str(state.response_body),
            headers=state.headers,
        )

    def _record(method, url, **kwargs):
        state.calls.append({"method": method, "url": url, **kwargs})
        return _make_resp()

    monkeypatch.setattr("requests.get", lambda url, **kw: _record("GET", url, **kw))
    monkeypatch.setattr("requests.post", lambda url, **kw: _record("POST", url, **kw))
    monkeypatch.setattr("requests.put", lambda url, **kw: _record("PUT", url, **kw))
    monkeypatch.setattr("requests.delete", lambda url, **kw: _record("DELETE", url, **kw))

    return state


# -----------------------------------------------------------------------------
# Parser Tests
# -----------------------------------------------------------------------------


def test_parser_users_list(admin_cli):
    parser = admin_cli.build_parser()
    args = parser.parse_args(["users", "list", "--token", "tok123"])
    assert args.command == "users"
    assert args.users_command == "list"
    assert args.token == "tok123"
    assert args.api == "http://localhost:8000"


def test_parser_users_set_username(admin_cli):
    parser = admin_cli.build_parser()
    args = parser.parse_args(["users", "set-username", "--user-id", "42", "--username", "alice"])
    assert args.command == "users"
    assert args.users_command == "set-username"
    assert args.user_id == 42
    assert args.username == "alice"


def test_parser_users_set_password(admin_cli):
    parser = admin_cli.build_parser()
    args = parser.parse_args(["users", "set-password", "--user-id", "5", "--password", "newpass123"])
    assert args.command == "users"
    assert args.users_command == "set-password"
    assert args.user_id == 5
    assert args.password == "newpass123"


def test_parser_users_create(admin_cli):
    parser = admin_cli.build_parser()
    args = parser.parse_args([
        "users", "create",
        "--username", "bob",
        "--password", "secret888",
        "--farms", "KF", "WOW",
        "--role", "owner",
    ])
    assert args.command == "users"
    assert args.users_command == "create"
    assert args.username == "bob"
    assert args.password == "secret888"
    assert args.farms == ["KF", "WOW"]
    assert args.role == "owner"


def test_parser_users_set_status(admin_cli):
    parser = admin_cli.build_parser()
    args = parser.parse_args(["users", "set-status", "--user-id", "10", "--active", "false"])
    assert args.command == "users"
    assert args.users_command == "set-status"
    assert args.user_id == 10
    assert args.active == "false"


def test_parser_users_assign_farms(admin_cli):
    parser = admin_cli.build_parser()
    args = parser.parse_args(["users", "assign-farms", "--user-id", "10", "--farms", "KF"])
    assert args.command == "users"
    assert args.users_command == "assign-farms"
    assert args.user_id == 10
    assert args.farms == ["KF"]


def test_parser_users_delete(admin_cli):
    parser = admin_cli.build_parser()
    args = parser.parse_args(["users", "delete", "--user-id", "10", "--yes"])
    assert args.command == "users"
    assert args.users_command == "delete"
    assert args.user_id == 10
    assert args.yes is True


def test_parser_farms_status(admin_cli):
    parser = admin_cli.build_parser()
    args = parser.parse_args(["farms", "status"])
    assert args.command == "farms"
    assert args.farms_command == "status"


def test_parser_farms_snapshots(admin_cli):
    parser = admin_cli.build_parser()
    args = parser.parse_args(["farms", "snapshots", "--farm", "KF"])
    assert args.command == "farms"
    assert args.farms_command == "snapshots"
    assert args.farm == "KF"


def test_parser_farms_revert(admin_cli):
    parser = admin_cli.build_parser()
    args = parser.parse_args(["farms", "revert", "--farm", "KF", "--snapshot-id", "3", "--yes"])
    assert args.command == "farms"
    assert args.farms_command == "revert"
    assert args.farm == "KF"
    assert args.snapshot_id == 3
    assert args.yes is True


# -----------------------------------------------------------------------------
# Token Resolution & Fallback Tests
# -----------------------------------------------------------------------------


def test_token_resolution_from_flag(admin_cli):
    args = argparse.Namespace(token="flag_token")
    assert admin_cli._get_token(args) == "flag_token"


def test_token_resolution_from_env(admin_cli, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "env_token")
    args = argparse.Namespace(token=None)
    assert admin_cli._get_token(args) == "env_token"


def test_token_resolution_missing_exits(admin_cli, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    args = argparse.Namespace(token=None)
    with pytest.raises(SystemExit, match="Admin token required"):
        admin_cli._get_token(args)


# -----------------------------------------------------------------------------
# Users Subcommands Dispatch Tests
# -----------------------------------------------------------------------------


def test_users_list_dispatches_and_formats_table(admin_cli, fake_api, capsys):
    fake_api.response_body = [
        {
            "id": 1,
            "username": "admin",
            "role": "admin",
            "is_admin": True,
            "is_active": True,
            "last_login": "2026-10-24T05:12:00",
            "farm_ids": ["KF", "WOW"],
        },
        {
            "id": 2,
            "username": "user1",
            "role": "user",
            "is_admin": False,
            "is_active": False,
            "last_login": None,
            "farm_ids": ["KF"],
        },
    ]

    args = argparse.Namespace(api="http://localhost:8000", token="jwt_token")
    admin_cli.cmd_users_list(args)

    assert len(fake_api.calls) == 1
    call = fake_api.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "http://localhost:8000/api/admin/users"
    assert call["headers"]["Authorization"] == "Bearer jwt_token"

    out = capsys.readouterr().out
    assert "admin" in out
    assert "ALL (Universal)" in out
    assert "Active" in out
    assert "user1" in out
    assert "Inactive" in out
    assert "KF" in out
    assert "Never" in out


def test_users_list_empty(admin_cli, fake_api, capsys):
    fake_api.response_body = []
    args = argparse.Namespace(api="http://localhost:8000", token="jwt_token")
    admin_cli.cmd_users_list(args)
    assert "No users found" in capsys.readouterr().out


def test_users_set_username(admin_cli, fake_api, capsys):
    fake_api.response_body = {"id": 2, "username": "new_alice"}
    args = argparse.Namespace(user_id=2, username="new_alice", api="http://localhost:8000", token="jwt_token")
    admin_cli.cmd_users_set_username(args)

    assert len(fake_api.calls) == 1
    call = fake_api.calls[0]
    assert call["method"] == "PUT"
    assert call["url"] == "http://localhost:8000/api/admin/credentials/username"
    assert call["json"] == {"user_id": 2, "new_username": "new_alice"}
    assert "new_alice" in capsys.readouterr().out


def test_users_set_password_with_flag(admin_cli, fake_api, capsys):
    fake_api.response_body = {"detail": "Password updated", "id": 3}
    args = argparse.Namespace(user_id=3, password="securepass123", api="http://localhost:8000", token="jwt_token")
    admin_cli.cmd_users_set_password(args)

    assert len(fake_api.calls) == 1
    call = fake_api.calls[0]
    assert call["method"] == "PUT"
    assert call["url"] == "http://localhost:8000/api/admin/credentials/password"
    assert call["json"] == {"user_id": 3, "new_password": "securepass123"}
    assert "Password for user ID 3 updated successfully" in capsys.readouterr().out


def test_users_set_password_prompt_fallback(admin_cli, fake_api, monkeypatch, capsys):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "prompted_pass")
    args = argparse.Namespace(user_id=4, password=None, api="http://localhost:8000", token="jwt_token")
    admin_cli.cmd_users_set_password(args)

    assert len(fake_api.calls) == 1
    assert fake_api.calls[0]["json"] == {"user_id": 4, "new_password": "prompted_pass"}


def test_users_set_password_empty_aborts(admin_cli, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "")
    args = argparse.Namespace(user_id=4, password=None, api="http://localhost:8000", token="jwt_token")
    with pytest.raises(SystemExit, match="Password cannot be empty"):
        admin_cli.cmd_users_set_password(args)


def test_users_create(admin_cli, fake_api, capsys):
    fake_api.response_body = {
        "id": 10,
        "username": "manager",
        "role": "owner",
        "is_admin": False,
        "is_active": True,
        "farm_ids": ["KF", "WOW"],
    }
    args = argparse.Namespace(
        username="manager",
        password="password123",
        farms=["KF", "WOW"],
        role="owner",
        api="http://localhost:8000",
        token="jwt_token",
    )
    admin_cli.cmd_users_create(args)

    assert len(fake_api.calls) == 1
    call = fake_api.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "http://localhost:8000/api/admin/users"
    assert call["json"] == {
        "username": "manager",
        "password": "password123",
        "farm_ids": ["KF", "WOW"],
        "role": "owner",
    }
    out = capsys.readouterr().out
    assert "User 'manager' (ID: 10) created" in out
    assert "role 'owner'" in out


def test_users_set_status(admin_cli, fake_api, capsys):
    fake_api.response_body = {"detail": "Status updated", "id": 5, "is_active": False}
    args = argparse.Namespace(user_id=5, active="false", api="http://localhost:8000", token="jwt_token")
    admin_cli.cmd_users_set_status(args)

    assert len(fake_api.calls) == 1
    call = fake_api.calls[0]
    assert call["method"] == "PUT"
    assert call["url"] == "http://localhost:8000/api/admin/users/5/status"
    assert call["json"] == {"is_active": False}
    assert "Status for user ID 5 updated to Inactive" in capsys.readouterr().out


def test_users_assign_farms(admin_cli, fake_api, capsys):
    fake_api.response_body = {"detail": "Farms updated", "id": 6, "farm_ids": ["KF", "WOW"]}
    args = argparse.Namespace(user_id=6, farms=["KF", "WOW"], api="http://localhost:8000", token="jwt_token")
    admin_cli.cmd_users_assign_farms(args)

    assert len(fake_api.calls) == 1
    call = fake_api.calls[0]
    assert call["method"] == "PUT"
    assert call["url"] == "http://localhost:8000/api/admin/users/6/farms"
    assert call["json"] == {"farm_ids": ["KF", "WOW"]}
    assert "Assigned farms to user ID 6: KF, WOW" in capsys.readouterr().out


def test_users_delete_with_yes_flag(admin_cli, fake_api, capsys):
    fake_api.response_body = {"detail": "User deleted", "id": 7}
    args = argparse.Namespace(user_id=7, yes=True, api="http://localhost:8000", token="jwt_token")
    admin_cli.cmd_users_delete(args)

    assert len(fake_api.calls) == 1
    call = fake_api.calls[0]
    assert call["method"] == "DELETE"
    assert call["url"] == "http://localhost:8000/api/admin/users/7"
    assert "User ID 7 deleted successfully" in capsys.readouterr().out


def test_users_delete_interactive_confirmed(admin_cli, fake_api, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    args = argparse.Namespace(user_id=8, yes=False, api="http://localhost:8000", token="jwt_token")
    admin_cli.cmd_users_delete(args)

    assert len(fake_api.calls) == 1
    assert fake_api.calls[0]["method"] == "DELETE"
    assert "User ID 8 deleted successfully" in capsys.readouterr().out


def test_users_delete_interactive_cancelled(admin_cli, fake_api, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")
    args = argparse.Namespace(user_id=8, yes=False, api="http://localhost:8000", token="jwt_token")
    admin_cli.cmd_users_delete(args)

    assert len(fake_api.calls) == 0
    assert "Deletion cancelled" in capsys.readouterr().out


# -----------------------------------------------------------------------------
# Farms Subcommands Dispatch Tests
# -----------------------------------------------------------------------------


def test_farms_status_overview(admin_cli, fake_api, capsys):
    fake_api.response_body = [
        {
            "farm_id": "KF",
            "farm_name": "Kilmarnock Farm",
            "is_active": True,
            "record_count": 34295,
            "snapshots_count": 2,
            "last_upload": "2026-10-20T12:00:00",
        },
        {
            "farm_id": "WOW",
            "farm_name": "Warragamba",
            "is_active": False,
            "record_count": 0,
            "snapshots_count": 0,
            "last_upload": None,
        },
    ]

    args = argparse.Namespace(api="http://localhost:8000", token="jwt_token")
    admin_cli.cmd_farms_status(args)

    assert len(fake_api.calls) == 1
    call = fake_api.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "http://localhost:8000/api/admin/farms/overview"

    out = capsys.readouterr().out
    assert "KF" in out
    assert "Kilmarnock Farm" in out
    assert "34,295" in out
    assert "Active" in out
    assert "WOW" in out
    assert "Inactive" in out
    assert "Never" in out


def test_farms_snapshots(admin_cli, fake_api, capsys):
    fake_api.response_body = [
        {
            "id": 1,
            "snapshot_name": "pre_upload_KF_2026-10-20.csv",
            "record_count": 34295,
            "created_by": "admin",
            "created_at": "2026-10-20T12:00:00",
        }
    ]

    args = argparse.Namespace(farm="KF", api="http://localhost:8000", token="jwt_token")
    admin_cli.cmd_farms_snapshots(args)

    assert len(fake_api.calls) == 1
    call = fake_api.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "http://localhost:8000/api/admin/farms/KF/snapshots"

    out = capsys.readouterr().out
    assert "pre_upload_KF_2026-10-20.csv" in out
    assert "34,295" in out
    assert "admin" in out


def test_farms_revert_with_yes_flag(admin_cli, fake_api, capsys):
    fake_api.response_body = {
        "detail": "Restored successfully",
        "restored_record_count": 34295,
        "forecasts": {
            "status": "updated",
            "animals": 376,
            "data_through": "2026-10-20",
            "model_version": "xgb-recursive-v2",
            "seconds": 2.1,
        },
    }

    args = argparse.Namespace(farm="KF", snapshot_id=2, yes=True, api="http://localhost:8000", token="jwt_token")
    admin_cli.cmd_farms_revert(args)

    assert len(fake_api.calls) == 1
    call = fake_api.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "http://localhost:8000/api/admin/farms/KF/snapshots/2/revert"

    out = capsys.readouterr().out
    assert "Successfully reverted farm 'KF' to snapshot 2" in out
    assert "Restored record count: 34,295" in out
    assert "Forecasts refreshed for farm KF" in out


def test_farms_revert_interactive_cancelled(admin_cli, fake_api, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")
    args = argparse.Namespace(farm="KF", snapshot_id=2, yes=False, api="http://localhost:8000", token="jwt_token")
    admin_cli.cmd_farms_revert(args)

    assert len(fake_api.calls) == 0
    assert "Revert cancelled" in capsys.readouterr().out


# -----------------------------------------------------------------------------
# API Error Handling Tests
# -----------------------------------------------------------------------------


def test_api_error_exits_with_detail(admin_cli, fake_api):
    fake_api.status_code = 400
    fake_api.response_body = {"detail": "Cannot delete root admin account"}
    args = argparse.Namespace(user_id=1, yes=True, api="http://localhost:8000", token="jwt_token")

    with pytest.raises(SystemExit, match="Cannot delete root admin account"):
        admin_cli.cmd_users_delete(args)


# -----------------------------------------------------------------------------
# CLI Entrypoint main() Dispatch Tests
# -----------------------------------------------------------------------------


def test_cli_main_users_list_dispatch(admin_cli, fake_api, capsys):
    fake_api.response_body = []
    admin_cli.main(["users", "list", "--token", "tok"])
    assert len(fake_api.calls) == 1
    assert fake_api.calls[0]["url"] == "http://localhost:8000/api/admin/users"
    assert "No users found" in capsys.readouterr().out


def test_cli_main_farms_status_dispatch(admin_cli, fake_api, capsys):
    fake_api.response_body = []
    admin_cli.main(["farms", "status", "--token", "tok"])
    assert len(fake_api.calls) == 1
    assert fake_api.calls[0]["url"] == "http://localhost:8000/api/admin/farms/overview"
    assert "No farms found" in capsys.readouterr().out
