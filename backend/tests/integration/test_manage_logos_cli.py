from __future__ import annotations

import argparse
from pathlib import Path
import pytest

from app.config import get_settings
from scripts.manage_logos import (
    cmd_clear,
    cmd_scaffold,
    cmd_set,
    cmd_status,
    cmd_sync_defaults,
)


@pytest.mark.integration
def test_manage_logos_cli_workflow(tmp_path: Path, monkeypatch, capsys):
    """Verifies manage_logos.py CLI operations: status, scaffold, set, sync-defaults, clear."""
    settings = get_settings()
    logos_root = tmp_path / "logos"
    default_dir = logos_root / "default"
    default_dir.mkdir(parents=True)
    (default_dir / "01_logo_usyd.png").write_bytes(b"usyd_bytes")
    (default_dir / "02_logo_mla.png").write_bytes(b"mla_bytes")

    monkeypatch.setattr(settings, "logos_local_path", str(logos_root))
    monkeypatch.setattr(
        "scripts.manage_logos.get_configured_farms",
        lambda: [{"farm_id": "FARM_A", "farm_name": "Farm A", "is_active": True}],
    )

    # 1. Status command initially reports missing directory
    cmd_status(argparse.Namespace())
    out, _ = capsys.readouterr()
    assert "FARM_A" in out
    assert "MISSING" in out

    # 2. Scaffold creates missing farm directory
    cmd_scaffold(argparse.Namespace(sync_defaults=False))
    out, _ = capsys.readouterr()
    assert "Created" in out
    assert (logos_root / "FARM_A").is_dir()

    # 3. Set adds custom logo with order prefix
    sample_logo = tmp_path / "sample_badge.png"
    sample_logo.write_bytes(b"\x89PNG\r\n\x1a\ncustom")
    cmd_set(argparse.Namespace(farm="FARM_A", file=str(sample_logo), order=1))
    out, _ = capsys.readouterr()
    assert "Successfully deployed" in out
    assert (logos_root / "FARM_A" / "01_sample_badge.png").is_file()

    # 4. Status now reports custom logo
    cmd_status(argparse.Namespace())
    out, _ = capsys.readouterr()
    assert "Custom logos (1)" in out
    assert "01_sample_badge.png" in out

    # 5. Clear removes custom logo
    cmd_clear(argparse.Namespace(farm="FARM_A"))
    out, _ = capsys.readouterr()
    assert "Cleared 1 logo" in out
    assert not (logos_root / "FARM_A" / "01_sample_badge.png").exists()

    # 6. Sync defaults copies partner logos into farm directory
    cmd_sync_defaults(argparse.Namespace(farm="FARM_A"))
    out, _ = capsys.readouterr()
    assert "Copied: 01_logo_usyd.png" in out
    assert (logos_root / "FARM_A" / "01_logo_usyd.png").is_file()
