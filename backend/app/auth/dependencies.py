from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.db import fetch_one, get_user_farms

security = HTTPBearer(auto_error=False)


class CurrentUser:
    def __init__(
        self,
        id: int,
        username: str,
        is_admin: bool,
        farm_ids: list[str],
        role: str | None = None,
    ):
        self.id = id
        self.username = username
        self.farm_ids = farm_ids
        if role:
            self.role = role
        elif username.lower() == "owner":
            self.role = "owner"
        elif is_admin:
            self.role = "admin"
        else:
            self.role = "user"
        self.is_admin = (self.role == "admin" or is_admin)

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @property
    def can_view_eid(self) -> bool:
        return self.is_admin or self.is_owner

    @property
    def can_export_raw(self) -> bool:
        return self.is_admin or self.is_owner


def create_access_token(
    user_id: int,
    username: str,
    is_admin: bool,
    farm_ids: list[str],
    role: str | None = None,
) -> tuple[str, int]:
    settings = get_settings()
    expires_in = settings.jwt_expiry_hours * 3600
    if role is None:
        if username.lower() == "owner":
            role = "owner"
        elif is_admin:
            role = "admin"
        else:
            role = "user"
    is_admin_flag = (role == "admin")
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "is_admin": is_admin_flag,
        "farm_ids": farm_ids,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, expires_in


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> CurrentUser:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    settings = get_settings()
    try:
        payload = jwt.decode(credentials.credentials, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    user_id = int(payload["sub"])
    try:
        user = fetch_one("SELECT id, username, is_admin, role, is_active FROM users WHERE id = %s", (user_id,))
    except Exception:
        user = fetch_one("SELECT id, username, is_admin FROM users WHERE id = %s", (user_id,))
    if not user or not user.get("is_active", True):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive")
    role = user.get("role") or payload.get("role")
    if not role:
        if user["username"].lower() == "owner":
            role = "owner"
        elif user.get("is_admin"):
            role = "admin"
        else:
            role = "user"
    is_admin_flag = (role == "admin" or bool(user.get("is_admin")))
    farms = get_user_farms(user_id)
    farm_ids = [f["farm_id"] for f in farms]
    if not is_admin_flag and len(farm_ids) == 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No farm access assigned. Contact system administrator.",
        )
    return CurrentUser(
        id=user["id"],
        username=user["username"],
        is_admin=is_admin_flag,
        farm_ids=farm_ids,
        role=role,
    )


def require_admin(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def require_credential_admin(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
    """Account management is limited to the admin login, not the owner or viewer."""
    if not user.is_admin or user.is_owner or user.username.lower() == "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def require_raw_export(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
    if not user.can_export_raw:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or owner access required to view or export raw data",
        )
    return user


def assert_farm_access(user: CurrentUser, farm_id: str | None) -> None:
    if not farm_id or not str(farm_id).strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing or invalid farm_id")
    clean_fid = str(farm_id).strip()
    if user.is_admin:
        return
    if clean_fid not in user.farm_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No access to this farm")
