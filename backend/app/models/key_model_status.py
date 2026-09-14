"""密钥×模型 细粒度运行时状态。

同一个密钥在不同模型上的可用性可能不同（额度按模型隔离、速率限制按模型隔离）。
ApiKey.status 只记录密钥级别的全局状态（鉴权失效、手动停用等），
本表记录每个密钥在每个原生模型上的独立状态，路由时按当前模型过滤。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin
from app.models.enums import KeyStatus


class KeyModelStatus(Base, TimestampMixin):
    """密钥在某个原生模型上的运行时状态。"""

    __tablename__ = "key_model_statuses"
    __table_args__ = (UniqueConstraint("key_id", "model_name", name="uq_key_model"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key_id: Mapped[int] = mapped_column(
        ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)  # 原生模型名
    status: Mapped[KeyStatus] = mapped_column(default=KeyStatus.active, nullable=False, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    rate_limited_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    api_key = relationship("ApiKey", lazy="selectin")
    provider = relationship("Provider", lazy="selectin")
