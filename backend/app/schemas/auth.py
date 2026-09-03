from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    token_type: str = "Bearer"


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., description="当前主密码")
    new_password: str = Field(..., description="新密码，至少 8 位")
