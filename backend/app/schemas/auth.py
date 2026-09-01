from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    token_type: str = "Bearer"
