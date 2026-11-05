#!/usr/bin/env python3
"""Manage and validate farm logos, mirroring R dashboard admin scripts."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import get_settings
from app.db import fetch_all

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg"}


def get_logos_root() -> Path:
    settings = get_settings()
    return Path(settings.logos_path)


def get_configured_farms() -> list[dict]:
    try:
        rows = fetch_all("SELECT farm_id, farm_name, is_active FROM farms ORDER BY farm_id")
        if rows:
            return rows
    except Exception:
        pass
    # Fallback to directories if database is unreachable
    logos_root = get_logos_root()
    farms = []
    if logos_root.is_dir():
        for item in sorted(os.listdir(logos_root)):
            p = logos_root / item
            if p.is_dir() and item != "default":
                farms.append({"farm_id": item, "farm_name": item, "is_active": True})
    if not farms:
        farms.append({"farm_id": "KF", "farm_name": "Killara Feedlot", "is_active": True})
    return farms


def cmd_status(args):
    logos_root = get_logos_root()
    farms = get_configured_farms()
    default_dir = logos_root / "default"

    print("\n========================================")
    print("  FARM LOGO STATUS & VALIDATION")
    print("========================================")
    print(f"Logos root directory: {logos_root}\n")

    # Check partner default logos
    print("Partner Defaults (fallback):")
    if default_dir.is_dir():
        default_files = [f for f in sorted(os.listdir(default_dir)) if Path(f).suffix.lower() in ALLOWED_EXTENSIONS]
        if default_files:
            for f in default_files:
                sz = (default_dir / f).stat().st_size
                print(f"  - {f} ({sz:,} bytes)")
        else:
            print("  [WARNING] 'default/' directory exists but contains no valid images.")
    else:
        print("  [WARNING] 'default/' directory not found. Fallback will use generic SVG.")

    print(f"\nConfigured Farms ({len(farms)} total):")
    issues = []
    warnings = []

    for f in farms:
        fid = f["farm_id"]
        fname = f.get("farm_name", fid)
        fdir = logos_root / fid
        if not fdir.exists():
            warnings.append(f"Farm '{fid}': logo directory missing ({fdir})")
            print(f"\n[{fid}] {fname}")
            print("  Directory: [MISSING] (resolving to partner defaults)")
            continue

        files = [x for x in sorted(os.listdir(fdir)) if Path(x).suffix.lower() in ALLOWED_EXTENSIONS]
        other_files = [x for x in sorted(os.listdir(fdir)) if Path(x).suffix.lower() not in ALLOWED_EXTENSIONS]

        print(f"\n[{fid}] {fname}")
        if files:
            print(f"  Custom logos ({len(files)}):")
            for idx, item in enumerate(files, 1):
                sz = (fdir / item).stat().st_size
                print(f"    {idx}. {item} ({sz:,} bytes)")
        else:
            print("  Custom logos: None (resolving to partner defaults)")

        if other_files:
            issues.append(f"Farm '{fid}': non-image files in logo directory: {other_files}")
            print(f"  [INVALID FILES]: {', '.join(other_files)}")

    print("\n========================================")
    print("  SUMMARY")
    print("========================================")
    if not issues and not warnings:
        print("All farm logo configurations are valid.\n")
    else:
        if warnings:
            print(f"Warnings ({len(warnings)}):")
            for w in warnings:
                print(f"  - {w}")
        if issues:
            print(f"Issues ({len(issues)}):")
            for i in issues:
                print(f"  - {i}")
        print()


def cmd_scaffold(args):
    logos_root = get_logos_root()
    farms = get_configured_farms()
    logos_root.mkdir(parents=True, exist_ok=True)
    default_dir = logos_root / "default"
    default_dir.mkdir(parents=True, exist_ok=True)

    print("\nScaffolding farm logo directories...")
    created = 0
    for f in farms:
        fid = f["farm_id"]
        fdir = logos_root / fid
        if not fdir.exists():
            fdir.mkdir(parents=True, exist_ok=True)
            print(f"  Created: {fdir}")
            created += 1
            if args.sync_defaults and default_dir.is_dir():
                for df in os.listdir(default_dir):
                    if Path(df).suffix.lower() in ALLOWED_EXTENSIONS:
                        shutil.copy2(default_dir / df, fdir / df)
                        print(f"    Copied default logo: {df}")
        else:
            print(f"  Exists:  {fdir}")

    print(f"\nScaffold complete. Created {created} directory/directories.\n")


def cmd_set(args):
    src = Path(args.file).resolve()
    if not src.is_file():
        print(f"Error: file '{src}' does not exist.", file=sys.stderr)
        sys.exit(1)

    ext = src.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        print(f"Error: invalid image type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}", file=sys.stderr)
        sys.exit(1)

    logos_root = get_logos_root()
    target_dir = logos_root / args.farm
    target_dir.mkdir(parents=True, exist_ok=True)

    clean_stem = re.sub(r"[^a-zA-Z0-9_\-]", "_", src.stem)
    if args.order is not None:
        clean_stem = re.sub(r"^\d+_", "", clean_stem)
        filename = f"{args.order:02d}_{clean_stem}{ext}"
    else:
        filename = f"{clean_stem}{ext}"

    dest = target_dir / filename
    shutil.copy2(src, dest)
    print(f"Successfully deployed logo to: {dest}")


def cmd_clear(args):
    logos_root = get_logos_root()
    target_dir = logos_root / args.farm
    if not target_dir.is_dir():
        print(f"No custom logos directory found for farm '{args.farm}'.")
        return

    removed = 0
    for fname in os.listdir(target_dir):
        p = target_dir / fname
        if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS:
            p.unlink()
            removed += 1
            print(f"  Removed: {fname}")

    print(f"\nCleared {removed} logo(s) for farm '{args.farm}'. Reverted to partner defaults.\n")


def cmd_sync_defaults(args):
    logos_root = get_logos_root()
    default_dir = logos_root / "default"
    if not default_dir.is_dir():
        print(f"Error: default logo directory not found at {default_dir}", file=sys.stderr)
        sys.exit(1)

    default_files = [f for f in sorted(os.listdir(default_dir)) if Path(f).suffix.lower() in ALLOWED_EXTENSIONS]
    if not default_files:
        print(f"Error: no valid logo files in {default_dir}", file=sys.stderr)
        sys.exit(1)

    targets = [args.farm] if args.farm else [f["farm_id"] for f in get_configured_farms()]
    print(f"Syncing default partner logos to {len(targets)} farm(s)...")
    for fid in targets:
        fdir = logos_root / fid
        fdir.mkdir(parents=True, exist_ok=True)
        for df in default_files:
            shutil.copy2(default_dir / df, fdir / df)
            print(f"  [{fid}] Copied: {df}")

    print("\nDefault logo synchronization complete.\n")


def main():
    parser = argparse.ArgumentParser(description="Farm Logo Management Utility")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Audit and validate all farm logo directories")

    scaffold_p = sub.add_parser("scaffold", help="Create missing logo directories for all farms")
    scaffold_p.add_argument("--sync-defaults", action="store_true", help="Copy partner logos to new directories")

    set_p = sub.add_parser("set", help="Set/deploy a logo file for a specific farm")
    set_p.add_argument("--farm", required=True, help="Target farm ID (e.g. KF)")
    set_p.add_argument("--file", required=True, help="Path to image file (.png, .jpg, .jpeg, .svg)")
    set_p.add_argument("--order", type=int, help="Numerical prefix order (e.g. 1 for 01_...)")

    clear_p = sub.add_parser("clear", help="Clear custom logos for a farm and revert to defaults")
    clear_p.add_argument("--farm", required=True, help="Target farm ID")

    sync_p = sub.add_parser("sync-defaults", help="Copy partner defaults into farm directory")
    sync_p.add_argument("--farm", help="Target farm ID (optional; defaults to all configured farms)")

    args = parser.parse_args()
    if args.command == "status":
        cmd_status(args)
    elif args.command == "scaffold":
        cmd_scaffold(args)
    elif args.command == "set":
        cmd_set(args)
    elif args.command == "clear":
        cmd_clear(args)
    elif args.command == "sync-defaults":
        cmd_sync_defaults(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
