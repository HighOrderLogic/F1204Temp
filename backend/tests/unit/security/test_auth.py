from __future__ import annotations

import pytest

from app.auth.passwords import hash_password, verify_password


@pytest.mark.unit
def test_scrypt_password_hash_and_verify():
    """US01: Verifies that scrypt password hashing generates verifiable hashes."""
    pwd = "securePassword123!"
    h = hash_password(pwd)
    assert h != pwd
    assert verify_password(pwd, h) is True


@pytest.mark.unit
def test_scrypt_password_reject_incorrect():
    """US01: Verifies that incorrect passwords fail verification."""
    h = hash_password("correctPassword")
    assert verify_password("wrongPassword", h) is False
    assert verify_password("", h) is False
