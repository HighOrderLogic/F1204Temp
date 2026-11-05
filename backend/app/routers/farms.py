from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.auth.dependencies import CurrentUser, assert_farm_access, get_current_user
from app.config import get_settings
from app.db import execute, fetch_all, fetch_one, get_active_engine

router = APIRouter()

ALLOWED_LOGO_EXTENSIONS = {".png", ".jpg", ".jpeg", ".svg"}
LOGO_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}
MAX_LOGO_BYTES = 2 * 1024 * 1024


def logo_api_url(farm_id: str, filename: str) -> str:
    return f"/api/farms/{quote(farm_id)}/logos/file/{quote(filename)}"


def _ensure_logo_table() -> None:
    data_type = "BYTEA" if get_active_engine() == "postgres" else "BLOB"
    execute(
        f"""
        CREATE TABLE IF NOT EXISTS logo_assets (
            farm_id VARCHAR(50) NOT NULL,
            filename VARCHAR(200) NOT NULL,
            content_type VARCHAR(100) NOT NULL,
            data {data_type} NOT NULL,
            PRIMARY KEY (farm_id, filename)
        )
        """
    )


def save_logo(farm_id: str, filename: str, content_type: str, data: bytes) -> None:
    _ensure_logo_table()
    execute("DELETE FROM logo_assets WHERE farm_id = %s AND filename = %s", (farm_id, filename))
    execute(
        "INSERT INTO logo_assets (farm_id, filename, content_type, data) VALUES (%s, %s, %s, %s)",
        (farm_id, filename, content_type, data),
    )


def list_db_logos(farm_id: str) -> list[dict]:
    try:
        _ensure_logo_table()
        return fetch_all(
            "SELECT farm_id, filename, content_type FROM logo_assets WHERE farm_id = %s ORDER BY filename",
            (farm_id,),
        )
    except Exception:
        return []


def get_db_logo(farm_id: str, filename: str) -> dict | None:
    _ensure_logo_table()
    return fetch_one(
        "SELECT farm_id, filename, content_type, data FROM logo_assets WHERE farm_id = %s AND filename = %s",
        (farm_id, filename),
    )


def delete_db_logos(farm_id: str, filename: str | None = None) -> int:
    # Count first: DuckDB rowcount is -1 for DELETE.
    _ensure_logo_table()
    if filename is None:
        rows = fetch_all("SELECT filename FROM logo_assets WHERE farm_id = %s", (farm_id,))
        execute("DELETE FROM logo_assets WHERE farm_id = %s", (farm_id,))
        return len(rows)
    exists = fetch_one(
        "SELECT 1 AS one FROM logo_assets WHERE farm_id = %s AND filename = %s",
        (farm_id, filename),
    )
    execute("DELETE FROM logo_assets WHERE farm_id = %s AND filename = %s", (farm_id, filename))
    return 1 if exists else 0


@router.get("")
def list_farms(user: Annotated[CurrentUser, Depends(get_current_user)]):
    if user.is_admin:
        return fetch_all(
            "SELECT f.farm_id, f.farm_name, f.slug, f.is_active FROM farms f WHERE f.is_active = TRUE ORDER BY f.farm_name"
        )
    return fetch_all(
        """
        SELECT f.farm_id, f.farm_name, f.slug, f.is_active
        FROM farms f
        JOIN user_farm_access ufa ON ufa.farm_id = f.farm_id
        WHERE ufa.user_id = %s AND f.is_active = TRUE
        ORDER BY f.farm_name
        """,
        (user.id,),
    )


@router.get("/{farm_id}")
def get_farm(farm_id: str, user: Annotated[CurrentUser, Depends(get_current_user)]):
    assert_farm_access(user, farm_id)
    farm = fetch_one("SELECT * FROM farms WHERE farm_id = %s", (farm_id,))
    logos = _get_logos(farm_id)
    return {**farm, "logos": logos}


@router.get("/{farm_id}/logos")
def get_logos(farm_id: str, user: Annotated[CurrentUser, Depends(get_current_user)]):
    assert_farm_access(user, farm_id)
    return {"logos": _get_logos(farm_id)}


def _get_logos(farm_id: str) -> list[dict]:
    settings = get_settings()
    logos = []
    seen: set[str] = set()
    for row in list_db_logos(farm_id):
        fname = row["filename"]
        seen.add(fname)
        logos.append({
            "filename": fname,
            "url": logo_api_url(farm_id, fname),
            "is_default": False,
        })
    logo_dir = os.path.join(str(settings.logos_path), farm_id)
    if os.path.isdir(logo_dir):
        for fname in sorted(os.listdir(logo_dir)):
            if fname.lower().endswith((".png", ".jpg", ".jpeg", ".svg")) and fname not in seen:
                seen.add(fname)
                logos.append({
                    "filename": fname,
                    "url": f"/assets/logos/{farm_id}/{fname}",
                    "is_default": False,
                })
    logos.sort(key=lambda l: l["filename"])
    if not logos:
        default_dir = os.path.join(str(settings.logos_path), "default")
        if os.path.isdir(default_dir):
            for fname in sorted(os.listdir(default_dir)):
                if fname.lower().endswith((".png", ".jpg", ".jpeg", ".svg")):
                    logos.append({
                        "filename": fname,
                        "url": f"/assets/logos/default/{fname}",
                        "is_default": True,
                    })
    return logos


@router.get("/{farm_id}/logos/file/{filename}")
def get_logo_file(farm_id: str, filename: str):
    # Public like the static /assets defaults: brand images, no sensitive data.
    safe_name = Path(filename).name
    if safe_name != filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    row = get_db_logo(farm_id, safe_name)
    if row is None:
        settings = get_settings()
        disk_path = Path(settings.logos_path) / farm_id / safe_name
        if not disk_path.is_file():
            raise HTTPException(status_code=404, detail=f"Logo '{safe_name}' not found for farm '{farm_id}'")
        data = disk_path.read_bytes()
        ext = disk_path.suffix.lower()
        content_type = LOGO_CONTENT_TYPES.get(ext, "application/octet-stream")
    else:
        data = row["data"]
        if not isinstance(data, (bytes, bytearray)):
            data = bytes(data)
        content_type = row["content_type"]
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=60",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )

