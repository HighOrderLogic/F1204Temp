#!/usr/bin/env python3
"""Admin CLI for Livestock Dashboard."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))
sys.path.insert(0, str(PROJECT_ROOT))


def _get_token(args: argparse.Namespace) -> str:
    token = getattr(args, "token", None) or os.environ.get("ADMIN_TOKEN")
    if not token:
        sys.exit("Error: Admin token required. Provide --token or set ADMIN_TOKEN environment variable.")
    return token


def _get_api(args: argparse.Namespace) -> str:
    api = getattr(args, "api", None) or os.environ.get("ADMIN_API_URL") or "http://localhost:8000"
    return api.rstrip("/")


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _add_api_and_token_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL (default: http://localhost:8000)")
    parser.add_argument("--token", default=None, help="Admin JWT token (or set ADMIN_TOKEN environment variable)")


def _handle_api_error(r, operation: str) -> None:
    try:
        data = r.json()
        detail = data.get("detail", str(data))
    except Exception:
        detail = r.text or f"HTTP {r.status_code}"
    sys.exit(f"{operation} failed (HTTP {r.status_code}): {detail}")


def _format_date(val: Any) -> str:
    if not val:
        return "Never"
    s = str(val).strip()
    if not s or s.lower() == "none":
        return "Never"
    return s[:19].replace("T", " ")


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(cell)))
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in col_widths))
    for row in rows:
        padded = [str(c) for c in row] + [""] * (len(headers) - len(row))
        print(fmt.format(*padded[:len(headers)]))


def _print_response(r) -> dict:
    try:
        body = r.json()
    except ValueError:
        print(r.text)
        return {}
    print(body)
    return body if isinstance(body, dict) else {}


def _print_forecasts(farm: str, result: dict) -> None:
    print(f"Forecasts refreshed for farm {farm}: {result['animals']} animals, data through "
          f"{result['data_through']}, model {result['model_version']} ({result['seconds']}s).")


def _forecast_command(args: argparse.Namespace) -> str:
    api = getattr(args, "api", "http://localhost:8000")
    return f"python scripts/admin.py forecast --farm {args.farm} --api {api} --token <admin JWT>"


def cmd_upload(args: argparse.Namespace) -> None:
    """Upload a CSV. The server then refreshes the farm's forecasts as part of the same request."""
    import requests

    token = _get_token(args)
    api = _get_api(args)
    with open(args.file, "rb") as f:
        r = requests.post(
            f"{api}/api/admin/farms/{args.farm}/upload",
            headers=_auth_headers(token),
            files={"file": f},
            data={"duplicate_mode": args.mode, "forecast": "false" if args.skip_forecast else "true"},
        )
    body = _print_response(r)
    if not r.ok:
        sys.exit(f"Upload failed (HTTP {r.status_code}); forecasts were not refreshed.")
    forecasts = body.get("forecasts", {})
    if forecasts.get("status") == "skipped":
        print(f"Forecasts not refreshed (--skip-forecast). Later run: {_forecast_command(args)}")
    elif forecasts.get("status") == "updated":
        _print_forecasts(args.farm, forecasts)
    else:
        sys.exit("Upload succeeded. " + forecasts.get(
            "detail", f"The server did not report refreshing forecasts. Run: {_forecast_command(args)}"
        ))


def cmd_forecast(args: argparse.Namespace) -> None:
    """Ask the server to recompute a farm's forecasts: to retry, or after deploying a new model."""
    import requests

    token = _get_token(args)
    api = _get_api(args)
    r = requests.post(
        f"{api}/api/admin/farms/{args.farm}/forecasts/refresh",
        headers=_auth_headers(token),
    )
    body = _print_response(r)
    if not r.ok:
        sys.exit(f"Refreshing forecasts failed (HTTP {r.status_code}).")
    _print_forecasts(args.farm, body)


def cmd_seed(args: argparse.Namespace) -> None:
    from scripts.seed import main
    main()


def cmd_logos(args: argparse.Namespace) -> None:
    from scripts.manage_logos import main as logos_main
    sys.argv = [sys.argv[0]] + (getattr(args, "logo_args", None) or [])
    logos_main()


def cmd_users_list(args: argparse.Namespace) -> None:
    import requests

    token = _get_token(args)
    api = _get_api(args)
    r = requests.get(f"{api}/api/admin/users", headers=_auth_headers(token))
    if not r.ok:
        _handle_api_error(r, "Fetching users")
    users = r.json()
    if not isinstance(users, list) or not users:
        print("No users found.")
        return

    headers = ["ID", "Username", "Role", "Status", "Last Login", "Assigned Farms"]
    rows = []
    for u in users:
        uid = str(u.get("id", ""))
        uname = str(u.get("username", ""))
        role = str(u.get("role") or ("admin" if u.get("is_admin") else "user")).capitalize()
        status = "Active" if u.get("is_active", True) else "Inactive"
        last_login = _format_date(u.get("last_login"))
        if u.get("is_admin") or role.lower() == "admin":
            farms = "ALL (Universal)"
        else:
            f_list = u.get("farm_ids") or []
            farms = ", ".join(sorted(f_list)) if f_list else "None (Locked Out)"
        rows.append([uid, uname, role, status, last_login, farms])

    _print_table(headers, rows)


def cmd_users_set_username(args: argparse.Namespace) -> None:
    import requests

    token = _get_token(args)
    api = _get_api(args)
    r = requests.put(
        f"{api}/api/admin/credentials/username",
        headers=_auth_headers(token),
        json={"user_id": args.user_id, "new_username": args.username},
    )
    if not r.ok:
        _handle_api_error(r, "Updating username")
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    new_name = data.get("username", args.username)
    print(f"Username for user ID {args.user_id} updated successfully to '{new_name}'.")


def cmd_users_set_password(args: argparse.Namespace) -> None:
    import requests

    pwd = args.password
    if pwd is None:
        import getpass
        pwd = getpass.getpass("Enter new password: ")
    if not pwd or not pwd.strip():
        sys.exit("Error: Password cannot be empty.")

    token = _get_token(args)
    api = _get_api(args)
    r = requests.put(
        f"{api}/api/admin/credentials/password",
        headers=_auth_headers(token),
        json={"user_id": args.user_id, "new_password": pwd},
    )
    if not r.ok:
        _handle_api_error(r, "Updating password")
    print(f"Password for user ID {args.user_id} updated successfully.")


def cmd_users_create(args: argparse.Namespace) -> None:
    import requests

    token = _get_token(args)
    api = _get_api(args)
    payload = {
        "username": args.username,
        "password": args.password,
        "farm_ids": args.farms,
        "role": getattr(args, "role", "user") or "user",
    }
    r = requests.post(
        f"{api}/api/admin/users",
        headers=_auth_headers(token),
        json=payload,
    )
    if not r.ok:
        _handle_api_error(r, "Creating user")
    data = r.json()
    uid = data.get("id")
    uname = data.get("username", args.username)
    role = data.get("role", payload["role"])
    farms = ", ".join(data.get("farm_ids", args.farms))
    print(f"User '{uname}' (ID: {uid}) created with role '{role}' and assigned farms: {farms}.")


def cmd_users_set_status(args: argparse.Namespace) -> None:
    import requests

    active_bool = str(args.active).lower() in ("true", "1", "yes", "active")
    token = _get_token(args)
    api = _get_api(args)
    r = requests.put(
        f"{api}/api/admin/users/{args.user_id}/status",
        headers=_auth_headers(token),
        json={"is_active": active_bool},
    )
    if not r.ok:
        _handle_api_error(r, "Updating user status")
    status_label = "Active" if active_bool else "Inactive"
    print(f"Status for user ID {args.user_id} updated to {status_label}.")


def cmd_users_assign_farms(args: argparse.Namespace) -> None:
    import requests

    token = _get_token(args)
    api = _get_api(args)
    r = requests.put(
        f"{api}/api/admin/users/{args.user_id}/farms",
        headers=_auth_headers(token),
        json={"farm_ids": args.farms},
    )
    if not r.ok:
        _handle_api_error(r, "Assigning farms")
    print(f"Assigned farms to user ID {args.user_id}: {', '.join(args.farms)}.")


def cmd_users_delete(args: argparse.Namespace) -> None:
    import requests

    if not getattr(args, "yes", False):
        confirm = input(f"Are you sure you want to delete user ID {args.user_id}? Type 'yes' to confirm: ")
        if confirm.strip().lower() not in ("yes", "y"):
            print("Deletion cancelled.")
            return

    token = _get_token(args)
    api = _get_api(args)
    r = requests.delete(
        f"{api}/api/admin/users/{args.user_id}",
        headers=_auth_headers(token),
    )
    if not r.ok:
        _handle_api_error(r, "Deleting user")
    print(f"User ID {args.user_id} deleted successfully.")


def cmd_farms_status(args: argparse.Namespace) -> None:
    import requests

    token = _get_token(args)
    api = _get_api(args)
    r = requests.get(f"{api}/api/admin/farms/overview", headers=_auth_headers(token))
    if not r.ok:
        _handle_api_error(r, "Fetching farms overview")
    farms = r.json()
    if not isinstance(farms, list) or not farms:
        print("No farms found.")
        return

    headers = ["Farm ID", "Farm Name", "Status", "Records", "Snapshots", "Last Upload"]
    rows = []
    for f in farms:
        fid = str(f.get("farm_id", ""))
        fname = str(f.get("farm_name") or fid)
        status = "Active" if f.get("is_active", True) else "Inactive"
        recs = f"{f.get('record_count', 0):,}"
        snaps = str(f.get("snapshots_count", 0))
        last_up = _format_date(f.get("last_upload"))
        rows.append([fid, fname, status, recs, snaps, last_up])

    _print_table(headers, rows)


def cmd_farms_snapshots(args: argparse.Namespace) -> None:
    import requests

    token = _get_token(args)
    api = _get_api(args)
    r = requests.get(f"{api}/api/admin/farms/{args.farm}/snapshots", headers=_auth_headers(token))
    if not r.ok:
        _handle_api_error(r, f"Fetching snapshots for farm '{args.farm}'")
    snaps = r.json()
    if not isinstance(snaps, list) or not snaps:
        print(f"No snapshots found for farm '{args.farm}'.")
        return

    headers = ["ID", "Snapshot Name", "Records", "Created By", "Created At"]
    rows = []
    for s in snaps:
        sid = str(s.get("id", ""))
        name = str(s.get("snapshot_name", ""))
        recs = f"{s.get('record_count', 0):,}" if s.get("record_count") is not None else "-"
        created_by = str(s.get("created_by") or "-")
        created_at = _format_date(s.get("created_at"))
        rows.append([sid, name, recs, created_by, created_at])

    _print_table(headers, rows)


def cmd_farms_revert(args: argparse.Namespace) -> None:
    import requests

    if not getattr(args, "yes", False):
        confirm = input(f"Are you sure you want to revert farm '{args.farm}' to snapshot {args.snapshot_id}? Type 'yes' to confirm: ")
        if confirm.strip().lower() not in ("yes", "y"):
            print("Revert cancelled.")
            return

    token = _get_token(args)
    api = _get_api(args)
    r = requests.post(
        f"{api}/api/admin/farms/{args.farm}/snapshots/{args.snapshot_id}/revert",
        headers=_auth_headers(token),
    )
    if not r.ok:
        _handle_api_error(r, f"Reverting farm '{args.farm}'")
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    print(f"Successfully reverted farm '{args.farm}' to snapshot {args.snapshot_id}.")
    if "restored_record_count" in data:
        print(f"Restored record count: {data['restored_record_count']:,}")
    forecasts = data.get("forecasts", {})
    if forecasts.get("status") == "updated":
        _print_forecasts(args.farm, forecasts)
    elif forecasts.get("status") == "skipped":
        print("Forecasts not refreshed.")



def cmd_farms_delete(args: argparse.Namespace) -> None:
    import requests

    farm_id = args.farm_id.strip()
    if farm_id.upper() == "KF" and not args.force:
        print("ERROR: Deletion of baseline farm 'KF' requires --force.")
        sys.exit(1)

    if not getattr(args, "yes", False):
        confirm = input(f"Are you sure you want to PERMANENTLY decommission farm '{farm_id}' and all records? Type 'yes': ")
        if confirm.strip().lower() not in ("yes", "y"):
            print("Decommission cancelled.")
            return

    token = _get_token(args)
    api = _get_api(args)
    url = f"{api}/api/admin/farms/{farm_id}"
    if args.force:
        url += "?force=true"
    r = requests.delete(url, headers=_auth_headers(token))
    if not r.ok:
        _handle_api_error(r, f"Decommissioning farm '{farm_id}'")
    print(f"Successfully decommissioned farm '{farm_id}' across all tables.")


def cmd_farms_prune_snapshots(args: argparse.Namespace) -> None:
    from app.services.farm_backup import prune_partition_snapshots
    pruned = prune_partition_snapshots(args.farm_id, max_pre_upload=args.keep)
    print(f"Pruned {len(pruned)} excess pre_upload snapshots for farm '{args.farm_id}': {pruned}")


def cmd_users_audit_log(args: argparse.Namespace) -> None:
    import requests
    token = _get_token(args)
    api = _get_api(args)
    r = requests.get(f"{api}/api/admin/users/audit-log?limit={args.limit}", headers=_auth_headers(token))
    if not r.ok:
        _handle_api_error(r, "Fetching user audit log")
    entries = r.json()
    if not entries:
        print("No audit log entries found.")
        return
    headers = ["ID", "User ID", "Username", "Role", "Action", "Changed By", "Timestamp"]
    rows = [
        [
            str(e.get("id")),
            str(e.get("user_id")),
            e.get("username", ""),
            e.get("role", ""),
            e.get("action", ""),
            e.get("changed_by", ""),
            str(e.get("created_at", "")),
        ]
        for e in entries
    ]
    _print_table(headers, rows)


def cmd_users_recover(args: argparse.Namespace) -> None:
    from app.db import execute, fetch_all, fetch_one
    from app.auth.passwords import hash_password

    username = args.username.strip()
    user = fetch_one("SELECT id, username, is_admin, role FROM users WHERE lower(username) = lower(%s)", (username,))
    if not user:
        print(f"User '{username}' not found in database.")
        sys.exit(1)

    if args.new_password:
        pw_hash = hash_password(args.new_password)
        execute(
            "UPDATE users SET password_hash = %s, is_active = TRUE, must_change_password = FALSE, updated_at = NOW() WHERE id = %s",
            (pw_hash, user["id"]),
        )
        print(f"Disaster Recovery: Reset password for '{user['username']}' and restored account to active status.")
        return

    history = fetch_all(
        "SELECT * FROM user_credentials_history WHERE user_id = %s ORDER BY id DESC LIMIT 5",
        (user["id"],),
    )
    if not history:
        print(f"No history records found for user '{username}'. Use --new-password <pass> to reset directly.")
        return
    last = history[0]
    execute(
        "UPDATE users SET role = %s, password_hash = %s, is_active = TRUE, updated_at = NOW() WHERE id = %s",
        (last["role"], last["password_hash"], user["id"]),
    )
    print(f"Disaster Recovery: Restored '{user['username']}' to audit entry #{last['id']} (role={last['role']}).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Livestock Dashboard Admin CLI")
    sub = parser.add_subparsers(dest="command")

    # upload
    upload = sub.add_parser("upload", help="Upload CSV for a farm, then refresh its weight forecasts")
    upload.add_argument("--farm", required=True)
    upload.add_argument("--file", required=True)
    upload.add_argument("--mode", default="skip", choices=["skip", "overwrite"])
    _add_api_and_token_args(upload)
    upload.add_argument(
        "--skip-forecast",
        action="store_true",
        help="only upload; refresh the forecasts later with the forecast command",
    )

    # forecast
    forecast = sub.add_parser("forecast", help="Ask the server to refresh a farm's weight forecasts")
    forecast.add_argument("--farm", required=True)
    _add_api_and_token_args(forecast)

    # seed
    sub.add_parser("seed", help="Seed database with users and KF sample data")

    # logos
    logos = sub.add_parser("logos", help="Manage and audit farm logos")
    logos.add_argument("logo_args", nargs=argparse.REMAINDER, help="Arguments passed to manage_logos.py")

    # users
    users_parser = sub.add_parser("users", help="User account and credential administration")
    users_sub = users_parser.add_subparsers(dest="users_command")

    # users list
    u_list = users_sub.add_parser("list", help="List all users with roles, status, and assigned farms")
    _add_api_and_token_args(u_list)

    # users set-username
    u_name = users_sub.add_parser("set-username", help="Change a user's username")
    u_name.add_argument("--user-id", type=int, required=True, help="User ID to update")
    u_name.add_argument("--username", required=True, help="New username")
    _add_api_and_token_args(u_name)

    # users set-password
    u_pwd = users_sub.add_parser("set-password", help="Update a user's password")
    u_pwd.add_argument("--user-id", type=int, required=True, help="User ID to update")
    u_pwd.add_argument("--password", default=None, help="New password (prompted if omitted)")
    _add_api_and_token_args(u_pwd)

    # users create
    u_create = users_sub.add_parser("create", help="Create a new user account with assigned farms")
    u_create.add_argument("--username", required=True, help="Username for new account")
    u_create.add_argument("--password", required=True, help="Password for new account")
    u_create.add_argument("--farms", nargs="+", required=True, help="One or more farm IDs to assign")
    u_create.add_argument("--role", choices=["user", "owner"], default="user", help="Account role (default: user)")
    _add_api_and_token_args(u_create)

    # users set-status
    u_status = users_sub.add_parser("set-status", help="Activate or deactivate a user account")
    u_status.add_argument("--user-id", type=int, required=True, help="User ID to update")
    u_status.add_argument("--active", required=True, choices=["true", "false", "1", "0", "yes", "no"], help="Status flag")
    _add_api_and_token_args(u_status)

    # users assign-farms
    u_farms = users_sub.add_parser("assign-farms", help="Assign farm access to a user account")
    u_farms.add_argument("--user-id", type=int, required=True, help="User ID to update")
    u_farms.add_argument("--farms", nargs="+", required=True, help="One or more farm IDs to assign")
    _add_api_and_token_args(u_farms)

    # users delete
    u_del = users_sub.add_parser("delete", help="Delete a user account")
    u_del.add_argument("--user-id", type=int, required=True, help="User ID to delete")
    u_del.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    _add_api_and_token_args(u_del)
    # users audit-log
    u_audit = users_sub.add_parser("audit-log", help="Display credential and role mutation audit log")
    u_audit.add_argument("--limit", type=int, default=50, help="Number of entries to show (default: 50)")
    _add_api_and_token_args(u_audit)

    # users recover
    u_recover = users_sub.add_parser("recover", help="Offline disaster recovery to restore an account")
    u_recover.add_argument("username", help="Username of account to recover")
    u_recover.add_argument("--new-password", default=None, help="Optional new password to force-reset")


    # farms
    farms_parser = sub.add_parser("farms", help="Farm overview and snapshot governance")
    farms_sub = farms_parser.add_subparsers(dest="farms_command")

    # farms status
    f_status = farms_sub.add_parser("status", help="Display overview status of all registered farms")
    _add_api_and_token_args(f_status)

    # farms snapshots
    f_snaps = farms_sub.add_parser("snapshots", help="List snapshots for a farm")
    f_snaps.add_argument("--farm", required=True, help="Target farm ID")
    _add_api_and_token_args(f_snaps)

    # farms revert
    f_revert = farms_sub.add_parser("revert", help="Revert a farm to a previous snapshot")
    f_revert.add_argument("--farm", required=True, help="Target farm ID")
    f_revert.add_argument("--snapshot-id", type=int, required=True, help="Snapshot ID to revert to")
    f_revert.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    _add_api_and_token_args(f_revert)
    # farms delete
    f_delete = farms_sub.add_parser("delete", help="Decommission a farm and cascade deletion across all 10 tables")
    f_delete.add_argument("farm_id", help="Farm identifier (e.g. NEWF)")
    f_delete.add_argument("--force", action="store_true", help="Force deletion of baseline farm KF")
    f_delete.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompt")
    _add_api_and_token_args(f_delete)

    # farms prune-snapshots
    f_prune = farms_sub.add_parser("prune-snapshots", help="Prune oldest pre_upload snapshots beyond retention limit")
    f_prune.add_argument("farm_id", help="Farm identifier (e.g. KF)")
    f_prune.add_argument("--keep", type=int, default=3, help="Number of pre_upload snapshots to retain (default: 3)")


    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "upload":
        cmd_upload(args)
    elif args.command == "forecast":
        cmd_forecast(args)
    elif args.command == "seed":
        cmd_seed(args)
    elif args.command == "logos":
        cmd_logos(args)
    elif args.command == "users":
        cmd = getattr(args, "users_command", None)
        if cmd == "list":
            cmd_users_list(args)
        elif cmd == "set-username":
            cmd_users_set_username(args)
        elif cmd == "set-password":
            cmd_users_set_password(args)
        elif cmd == "create":
            cmd_users_create(args)
        elif cmd == "set-status":
            cmd_users_set_status(args)
        elif cmd == "assign-farms":
            cmd_users_assign_farms(args)
        elif cmd == "delete":
            cmd_users_delete(args)
        else:
            parser.parse_args(["users", "--help"])
    elif args.command == "farms":
        cmd = getattr(args, "farms_command", None)
        if cmd == "status":
            cmd_farms_status(args)
        elif cmd == "snapshots":
            cmd_farms_snapshots(args)
        elif cmd == "revert":
            cmd_farms_revert(args)
        else:
            parser.parse_args(["farms", "--help"])
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
