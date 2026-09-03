from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin
from app.models.enums import KeyStrategy


class Channel(Base, TimestampMixin):
    """转发通道：对外暴露一个虚拟模型名，路由到一个分层密钥池计划。"""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    virtual_model: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)  # 客户端发的 model
    strategy: Mapped[KeyStrategy] = mapped_column(default=KeyStrategy.round_robin, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    tiers: Mapped[list["ChannelTier"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan", lazy="selectin",
        order_by="ChannelTier.order",
    )


class ChannelTier(Base, TimestampMixin):
    """通道的一层：对应一个供应商的密钥池。order 越小优先级越高。"""

    __tablename__ = "channel_tiers"
    __table_args__ = (UniqueConstraint("channel_id", "order", name="uq_channel_tier_order"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), nullable=False, index=True)
    order: Mapped[int] = mapped_column(Integer, nullable=False)  # 0 为主层，1.. 为 fallback 层
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False)

    channel = relationship("Channel", back_populates="tiers")
    provider = relationship("Provider", lazy="selectin")
    keys: Mapped[list["ChannelKey"]] = relationship(
        back_populates="tier", cascade="all, delete-orphan", lazy="selectin"
    )


class ChannelKey(Base, TimestampMixin):
    """层内的一条密钥成员。"""

    __tablename__ = "channel_keys"
    __table_args__ = (UniqueConstraint("channel_tier_id", "key_id", name="uq_tier_key"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel_tier_id: Mapped[int] = mapped_column(
        ForeignKey("channel_tiers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 数字越大越优先

    tier = relationship("ChannelTier", back_populates="keys")
    api_key = relationship("ApiKey", lazy="selectin")


class ModelMapping(Base, TimestampMixin):
    """跨供应商模型翻译：虚拟模型 × 供应商 → 原生模型名。"""

    __tablename__ = "model_mappings"
    __table_args__ = (UniqueConstraint("virtual_model", "provider_id", name="uq_model_mapping"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    virtual_model: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id", ondelete="CASCADE"), nullable=False)
    native_model: Mapped[str] = mapped_column(String(128), nullable=False)


class CallLog(Base):
    """一次转发调用的记录（落库前脱敏，不含真实密钥与完整请求体）。"""

    __tablename__ = "call_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    channel_id: Mapped[int | None] = mapped_column(ForeignKey("channels.id", ondelete="SET NULL"), nullable=True, index=True)
    tier_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    key_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    virtual_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    native_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="success", nullable=False)  # success/error
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    stream: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
