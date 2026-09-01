from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import KeyStatus


class ApiKeyCreate(BaseModel):
    provider_id: int
    group_id: int | None = None
    account_label: str | None = None
    name: str
    key_value: str            # 明文，落库前加密
    weight: int = 1
    tags: str | None = None


class ApiKeyUpdate(BaseModel):
    provider_id: int | None = None
    group_id: int | None = None
    account_label: str | None = None
    name: str | None = None
    key_value: str | None = None   # 若提供则重新加密
    weight: int | None = None
    tags: str | None = None
    status: KeyStatus | None = None


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    provider_id: int
    provider_name: str | None = None
    group_id: int | None = None
    group_name: str | None = None
    account_label: str | None = None
    name: str
    key_mask: str
    status: KeyStatus
    balance: str | None = None
    weight: int
    tags: str | None = None
    last_tested_at: datetime | None = None
    rate_limited_until: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class ApiKeyReveal(BaseModel):
    id: int
    key_value: str


class ExportRequest(BaseModel):
    password: str  # 导出包加密口令（独立于登录密码，不落库）


class ImportRequest(BaseModel):
    password: str
    bundle: dict  # 导出包（encrypt_bundle 返回的结构）


class ImportResult(BaseModel):
    providers: int
    groups: int
    keys_imported: int
    keys_skipped: int
