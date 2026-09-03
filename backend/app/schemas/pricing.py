from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PricingBase(BaseModel):
    model_name: str
    input_price: float = 0.0
    cached_input_price: float = 0.0
    output_price: float = 0.0
    context_length: int = 0
    currency: str = "CNY"
    note: str | None = None


class PricingCreate(PricingBase):
    pass


class PricingUpdate(BaseModel):
    input_price: float | None = None
    cached_input_price: float | None = None
    output_price: float | None = None
    context_length: int | None = None
    currency: str | None = None
    note: str | None = None


class PricingOut(PricingBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime
