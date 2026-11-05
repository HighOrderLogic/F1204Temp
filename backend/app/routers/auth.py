from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.auth.dependencies import CurrentUser, create_access_token, get_current_user, require_credential_admin
from app.auth.passwords import hash_password, verify_password
from app.db import execute, fetch_one, get_user_farms, record_user_login
from app.models.schemas import FarmInfo, LoginRequest, LoginResponse, PasswordUpdate, UserInfo, UsernameUpdate, RotateInitialPasswordRequest

router = APIRouter()


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest):
    try:
        user = fetch_one(
            "SELECT id, username, password_hash, is_admin, role, is_active, must_change_password FROM users WHERE username = %s",
            (body.username,),
        )
    except Exception:
        try:
            user = fetch_one(
                "SELECT id, username, password_hash, is_admin, role, is_active FROM users WHERE username = %s",
                (body.username,),
            )
        except Exception:
            user = fetch_one(
                "SELECT id, username, password_hash, is_admin FROM users WHERE username = %s",
                (body.username,),
            )
    if not user or not user.get("is_active", True):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    record_user_login(user["id"])
    farms = get_user_farms(user["id"])
    farm_ids = [f["farm_id"] for f in farms]
    role = user.get("role")
    if not role:
        if user["username"].lower() == "owner":
            role = "owner"
        elif user.get("is_admin"):
            role = "admin"
        else:
            role = "user"
    is_admin_flag = (role == "admin" or bool(user.get("is_admin")))
    if not is_admin_flag and len(farm_ids) == 0:
        raise HTTPException(
            status_code=403,
            detail="No farm access assigned. Contact system administrator.",
        )
    token, expires_in = create_access_token(user["id"], user["username"], is_admin_flag, farm_ids, role=role)
    return LoginResponse(
        access_token=token,
        expires_in=expires_in,
        user=UserInfo(
            id=user["id"],
            username=user["username"],
            is_admin=is_admin_flag,
            role=role,
            must_change_password=bool(user.get("must_change_password", False)),
            farms=[FarmInfo(**f) for f in farms],
        ),
    )


@router.get("/me", response_model=UserInfo)
def me(user: Annotated[CurrentUser, Depends(get_current_user)]):
    farms = get_user_farms(user.id)
    try:
        u_row = fetch_one("SELECT must_change_password FROM users WHERE id = %s", (user.id,))
        must_change = bool(u_row.get("must_change_password", False)) if u_row else False
    except Exception:
        must_change = False
    return UserInfo(
        id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        role=user.role,
        must_change_password=must_change,
        farms=[FarmInfo(**f) for f in farms],
    )


@router.post("/logout")
def logout():
    return {"detail": "logged out"}


@router.put("/password")
def update_password(body: PasswordUpdate, user: Annotated[CurrentUser, Depends(get_current_user)]):
    if not user.is_admin and body.user_id != user.id:
        raise HTTPException(status_code=403, detail="Cannot change other user's password")
    if not body.new_password or not body.new_password.strip():
        raise HTTPException(status_code=400, detail="Password cannot be empty")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    execute(
        "UPDATE users SET password_hash = %s, updated_at = NOW() WHERE id = %s",
        (hash_password(body.new_password), body.user_id),
    )
    return {"detail": "Password updated"}


@router.put("/username", deprecated=True)
def update_username(body: UsernameUpdate, user: Annotated[CurrentUser, Depends(require_credential_admin)]):
    """Deprecated: Use PUT /api/admin/credentials/username instead."""
    from app.routers.admin import change_credential_username

    return change_credential_username(body, user)


@router.get("/bootstrap-status")
def get_bootstrap_status():
    row = fetch_one("SELECT COUNT(*) AS cnt FROM users")
    cnt = row["cnt"] if row and row.get("cnt") is not None else 0
    return {"needs_setup": cnt == 0, "user_count": cnt}


@router.post("/rotate-initial-password")
def rotate_initial_password(body: RotateInitialPasswordRequest, user: Annotated[CurrentUser, Depends(get_current_user)]):
    if not body.new_password or len(body.new_password.strip()) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    from app.db import record_credential_history
    record_credential_history(user.id, "rotate_initial_password", user.username)
    execute(
        "UPDATE users SET password_hash = %s, must_change_password = FALSE, updated_at = NOW() WHERE id = %s",
        (hash_password(body.new_password.strip()), user.id),
    )
    return {"detail": "Initial password successfully rotated. Account active."}
