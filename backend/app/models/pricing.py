from __future__ import annotations

from sqlalchemy import Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class ModelPricing(Base, TimestampMixin):
    """模型计价：每 1M tokens 的输入/输出单价（单位：元/美元，由你自填）。"""

    __tablename__ = "model_pricings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    input_price: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)   # 每 1M 输入 tokens（高峰/标准档）
    cached_input_price: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)  # 每 1M 缓存命中输入 tokens
    output_price: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)  # 每 1M 输出 tokens（高峰/标准档）
    # 空闲时段折扣价：0 = 该档未设置，计费时沿用标准价（部分供应商如 DeepSeek
    # 官方对空闲时段给折扣价，供应商无分时定价的模型这两列留 0 即可）
    off_peak_input_price: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)   # 每 1M 输入 tokens
    off_peak_output_price: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)  # 每 1M 输出 tokens
    context_length: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 上下文长度（tokens），0=未设置
    currency: Mapped[str] = mapped_column(String(8), default="CNY", nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
