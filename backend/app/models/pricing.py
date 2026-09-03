from __future__ import annotations

from sqlalchemy import Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class ModelPricing(Base, TimestampMixin):
    """模型计价：每 1M tokens 的输入/输出单价（单位：元/美元，由你自填）。"""

    __tablename__ = "model_pricings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    input_price: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)   # 每 1M 输入 tokens
    cached_input_price: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)  # 每 1M 缓存命中输入 tokens
    output_price: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)  # 每 1M 输出 tokens
    currency: Mapped[str] = mapped_column(String(8), default="CNY", nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
