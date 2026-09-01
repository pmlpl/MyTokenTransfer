from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    severity: str
    key_id: int | None = None
    provider_id: int | None = None
    message: str
    resolved: bool
    created_at: datetime
    resolved_at: datetime | None = None


class AlertListResponse(BaseModel):
    items: list[AlertOut]
    total: int
    unresolved: int
