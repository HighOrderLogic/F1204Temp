"""Tests for defense-in-depth tenant scoping assertions (Blindside 6)."""
import pytest
from fastapi import HTTPException

from app.auth.dependencies import CurrentUser, assert_farm_access


def test_assert_farm_access_blank_rejected():
    admin = CurrentUser(id=1, username="admin", is_admin=True, farm_ids=["KF"])
    user = CurrentUser(id=2, username="user", is_admin=False, farm_ids=["KF"])

    # Blank/missing farm_id must raise 400 Bad Request for both admin and user
    for bad_id in [None, "", "   "]:
        with pytest.raises(HTTPException) as exc_info:
            assert_farm_access(admin, bad_id)
        assert exc_info.value.status_code == 400
        assert "Missing or invalid farm_id" in exc_info.value.detail

        with pytest.raises(HTTPException) as exc_info:
            assert_farm_access(user, bad_id)
        assert exc_info.value.status_code == 400


def test_assert_farm_access_unauthorized_rejected():
    user = CurrentUser(id=2, username="user", is_admin=False, farm_ids=["KF"])

    # Accessing unauthorized farm must raise 403 Forbidden
    with pytest.raises(HTTPException) as exc_info:
        assert_farm_access(user, "UNAUTHORIZED_FARM")
    assert exc_info.value.status_code == 403
    assert "No access to this farm" in exc_info.value.detail


def test_assert_farm_access_authorized_passes():
    user = CurrentUser(id=2, username="user", is_admin=False, farm_ids=["KF"])
    admin = CurrentUser(id=1, username="admin", is_admin=True, farm_ids=["KF"])

    # Valid authorized farm access must not raise
    assert_farm_access(user, "KF")
    assert_farm_access(admin, "KF")
    assert_farm_access(admin, "ANY_FARM")

