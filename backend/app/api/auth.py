from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import require_auth
from app.core.security import create_session_token, verify_master_password
from app.schemas.auth import LoginRequest, LoginResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest) -> LoginResponse:
    if not verify_master_password(req.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "密码错误")
    return LoginResponse(token=create_session_token())


@router.get("/me")
async def me(user: dict = Depends(require_auth)) -> dict:
    return {"user": user.get("sub", "admin")}
