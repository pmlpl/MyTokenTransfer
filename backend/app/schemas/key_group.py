from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class KeyGroupBase(BaseModel):
    name: str
    description: str | None = None


class KeyGroupCreate(KeyGroupBase):
    pass


class KeyGroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class KeyGroupOut(KeyGroupBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime
