"""Shared auth for cron callers (Vercel Cron and cron-job.org)."""

from __future__ import annotations

import hmac

from fastapi import HTTPException

from app.config import get_settings


def _secret_matches(candidate: str | None, expected: str) -> bool:
    if not candidate or not expected:
        return False
    return hmac.compare_digest(candidate.encode(), expected.encode())


def assert_cron_authorized(
    *,
    x_cron_secret: str | None = None,
    x_vercel_cron: str | None = None,
    authorization: str | None = None,
    cron_secret: str | None = None,
) -> None:
    """Accept any one of the cron credentials used by this project.

    - Authorization: Bearer <CRON_SECRET>
    - X-Cron-Secret or ?cron_secret= matching CRON_SECRET
    - x-vercel-cron: 1 (legacy Vercel header)
    """
    expected = get_settings().cron_secret
    bearer = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    secret_ok = _secret_matches(x_cron_secret or cron_secret, expected) or _secret_matches(bearer, expected)
    if secret_ok or (x_vercel_cron == "1" and not get_settings().is_production):
        return
    raise HTTPException(status_code=401, detail="Invalid cron secret")
