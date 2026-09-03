from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import require_auth
from app.core.security import (
    change_master_password,
    create_session_token,
    verify_master_password,
)
from app.schemas.auth import ChangePasswordRequest, LoginRequest, LoginResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest) -> LoginResponse:
    if not verify_master_password(req.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "密码错误")
    return LoginResponse(token=create_session_token())


@router.get("/me")
async def me(user: dict = Depends(require_auth)) -> dict:
    return {"user": user.get("sub", "admin")}


@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    user: dict = Depends(require_auth),
) -> dict:
    """修改主密码。旧密码正确 + 新密码 ≥8 位才生效；成功后下一次登录用新密码。"""
    try:
        change_master_password(req.old_password, req.new_password)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"ok": True, "message": "密码已更新，下次登录请使用新密码"}
