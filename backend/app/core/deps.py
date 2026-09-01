from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status

from app.config import settings
from app.core.security import verify_session_token


async def require_auth(request: Request) -> dict:
    """依赖：管理台登录态校验（Authorization: Bearer <session-token>）。"""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未认证")
    token = auth.split(" ", 1)[1].strip()
    data = verify_session_token(token)
    if not data:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "会话已失效，请重新登录")
    return data


async def require_gateway(request: Request) -> str:
    """依赖：网关统一密钥校验（与登录态分开，客户端用 GATEWAY_TOKEN 调 /v1/*）。"""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing gateway token")
    token = auth.split(" ", 1)[1].strip()
    if not hmac.compare_digest(token, settings.GATEWAY_TOKEN):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid gateway token")
    return token
