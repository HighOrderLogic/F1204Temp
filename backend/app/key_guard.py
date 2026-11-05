"""Refuse API requests while EID_PSEUDONYM_KEY is missing, with a message that says how to fix it.

Without the key, masked EIDs could be reversed by anyone who knows the old public default, so
the API stays down (HTTP 503) instead of serving them. The frontend shows the message in a
banner; the server logs it at startup.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.utils.anonymize import KEY_MISSING_CODE, KEY_MISSING_MESSAGE, pseudonym_key_missing

logger = logging.getLogger("uvicorn.error")


def install_pseudonym_key_guard(app: FastAPI) -> None:
    if pseudonym_key_missing():
        logger.error(KEY_MISSING_MESSAGE)

    @app.middleware("http")
    async def require_pseudonym_key(request: Request, call_next):
        if request.url.path.startswith("/api/") and pseudonym_key_missing():
            return JSONResponse(status_code=503, content={"detail": KEY_MISSING_MESSAGE, "code": KEY_MISSING_CODE})
        return await call_next(request)
