from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import ProbeKind


class ProviderBase(BaseModel):
    name: str
    slug: str
    base_url: str | None = None
    probe_kind: ProbeKind = ProbeKind.openai_models
    models: list[Any] | None = None
    is_active: bool = True
    note: str | None = None


class ProviderCreate(ProviderBase):
    pass


class ProviderUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    base_url: str | None = None
    probe_kind: ProbeKind | None = None
    models: list[Any] | None = None
    is_active: bool | None = None
    note: str | None = None


class ProviderOut(ProviderBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime
