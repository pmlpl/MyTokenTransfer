from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.models.enums import KeyStrategy


# ---- 输出 ----


class ChannelKeyOut(BaseModel):
    id: int
    key_id: int
    key_name: str | None
    key_mask: str | None
    status: str | None
    weight: int
    priority: int


class ChannelTierOut(BaseModel):
    id: int
    order: int
    provider_id: int
    provider_name: str | None
    keys: list[ChannelKeyOut]


class ModelMappingOut(BaseModel):
    id: int
    provider_id: int
    provider_name: str | None
    native_model: str


class ChannelOut(BaseModel):
    id: int
    name: str
    virtual_model: str
    strategy: str
    max_retries: int
    is_active: bool
    note: str | None
    tiers: list[ChannelTierOut]
    mappings: list[ModelMappingOut]
    created_at: datetime
    updated_at: datetime


# ---- 输入 ----


class ChannelKeyIn(BaseModel):
    key_id: int
    weight: int = 1
    priority: int = 0


class ChannelTierIn(BaseModel):
    provider_id: int
    order: int | None = None  # 为空时按列表顺序自动编号
    keys: list[ChannelKeyIn]


class ModelMappingIn(BaseModel):
    provider_id: int
    native_model: str


class ChannelCreate(BaseModel):
    name: str
    virtual_model: str
    strategy: KeyStrategy = KeyStrategy.round_robin
    max_retries: int = 3
    is_active: bool = True
    note: str | None = None
    tiers: list[ChannelTierIn]
    mappings: list[ModelMappingIn] = []


class ChannelUpdate(BaseModel):
    name: str | None = None
    strategy: KeyStrategy | None = None
    max_retries: int | None = None
    is_active: bool | None = None
    note: str | None = None
    tiers: list[ChannelTierIn] | None = None
    mappings: list[ModelMappingIn] | None = None
