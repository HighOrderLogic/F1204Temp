from __future__ import annotations

import io
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from app.config import get_settings


@pytest.mark.integration
def test_api_farms_logos_custom(client: TestClient, user_headers: dict[str, str]):
    """Verifies that farm KF with existing custom logo returns its custom logo."""
    res = client.get("/api/farms/KF/logos", headers=user_headers)
    assert res.status_code == 200
    data = res.json()
    assert "logos" in data
    assert len(data["logos"]) >= 1
    filenames = [l["filename"] for l in data["logos"]]
    assert "01_killara.png" in filenames
    kf_logo = next(l for l in data["logos"] if l["filename"] == "01_killara.png")
    assert kf_logo["is_default"] is False
    assert "01_killara.png" in kf_logo["url"]


@pytest.mark.integration
def test_api_farms_logos_fallback_to_partner_defaults(client: TestClient, admin_headers: dict[str, str], tmp_path: Path, monkeypatch):
    """Verifies that a farm without custom logos falls back to default partner logos (USYD & MLA)."""
    settings = get_settings()
    # Create temporary logos root with a default directory
    logos_root = tmp_path / "logos"
    default_dir = logos_root / "default"
    default_dir.mkdir(parents=True)
    (default_dir / "01_logo_usyd.png").write_bytes(b"dummy_usyd")
    (default_dir / "02_logo_mla.png").write_bytes(b"dummy_mla")

    # Empty farm dir for KF
    (logos_root / "KF").mkdir(parents=True)

    monkeypatch.setattr(settings, "logos_local_path", str(logos_root))

    monkeypatch.setattr("app.routers.farms.list_db_logos", lambda fid: [])
    res = client.get("/api/farms/KF/logos", headers=admin_headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data["logos"]) == 2
    assert data["logos"][0]["filename"] == "01_logo_usyd.png"
    assert data["logos"][0]["is_default"] is True
    assert data["logos"][1]["filename"] == "02_logo_mla.png"
    assert data["logos"][1]["is_default"] is True




@pytest.mark.integration
def test_api_admin_logos_workflow(client: TestClient, admin_headers: dict[str, str], user_headers: dict[str, str], tmp_path: Path, monkeypatch):
    """Verifies full admin logo management workflow: inspect, upload, delete, and reset."""
    settings = get_settings()
    logos_root = tmp_path / "logos"
    default_dir = logos_root / "default"
    default_dir.mkdir(parents=True)
    (default_dir / "01_logo_usyd.png").write_bytes(b"usyd")

    monkeypatch.setattr(settings, "logos_local_path", str(logos_root))

    # 1. Non-admin is blocked
    non_admin_res = client.get("/api/admin/farms/TEST/logos", headers=user_headers)
    assert non_admin_res.status_code == 403

    # 2. Inspect farm logos initially (falls back to defaults)
    res = client.get("/api/admin/farms/TEST/logos", headers=admin_headers)
    assert res.status_code == 200
    assert res.json()["has_custom_logos"] is False

    # 3. Reject invalid file extension
    bad_upload = client.post(
        "/api/admin/farms/TEST/logos",
        headers=admin_headers,
        files={"file": ("malicious.exe", io.BytesIO(b"fake"), "application/octet-stream")},
    )
    assert bad_upload.status_code == 400
    assert "Invalid image type" in bad_upload.json()["detail"]

    # 4. Upload valid PNG logo
    fake_png = b"\x89PNG\r\n\x1a\nfake"
    upload_res = client.post(
        "/api/admin/farms/TEST/logos",
        headers=admin_headers,
        files={"file": ("farm_banner.png", io.BytesIO(fake_png), "image/png")},
    )
    assert upload_res.status_code == 200
    assert upload_res.json()["filename"] == "farm_banner.png"
    file_url = upload_res.json()["url"]
    assert file_url == "/api/farms/TEST/logos/file/farm_banner.png"

    # 4b. Stream and verify binary payload from database endpoint
    stream_res = client.get(file_url)
    assert stream_res.status_code == 200
    assert stream_res.headers["content-type"] == "image/png"
    assert stream_res.content == fake_png

    # 5. Verify farm now has custom logos
    res_after = client.get("/api/admin/farms/TEST/logos", headers=admin_headers)
    assert res_after.json()["has_custom_logos"] is True
    assert len(res_after.json()["logos"]) == 1
    assert res_after.json()["logos"][0]["filename"] == "farm_banner.png"

    # 6. Delete invalid logo path
    del_bad = client.delete("/api/admin/farms/TEST/logos/.._evil.png", headers=admin_headers)
    assert del_bad.status_code in (400, 404)

    # 7. Delete existing logo
    del_res = client.delete("/api/admin/farms/TEST/logos/farm_banner.png", headers=admin_headers)
    assert del_res.status_code == 200

    # 8. Reset endpoint clears custom logos
    (logos_root / "TEST").mkdir(parents=True, exist_ok=True)
    (logos_root / "TEST" / "temp.svg").write_bytes(b"<svg></svg>")
    reset_res = client.post("/api/admin/farms/TEST/logos/reset", headers=admin_headers)
    assert reset_res.status_code == 200
    assert reset_res.json()["deleted_count"] >= 1
