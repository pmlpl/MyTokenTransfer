from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin
from app.models.enums import KeyStatus


class ApiKey(Base, TimestampMixin):
    """一条 API 密钥。key_value 为 Fernet 加密后的密文，明文不落库。"""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_id: Mapped[int] = mapped_column(
        ForeignKey("providers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("key_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
    account_label: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 所属账号/组织
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    # 密文与掩码；掩码在写入时计算，列表页无需解密即可展示
    key_value: Mapped[str] = mapped_column(Text, nullable=False)
    key_mask: Mapped[str] = mapped_column(String(64), nullable=False)  # 如 sk-…ab3f

    status: Mapped[KeyStatus] = mapped_column(default=KeyStatus.active, nullable=False, index=True)
    balance: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 余额/额度（口径因供应商而异，存文本）
    weight: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    tags: Mapped[str | None] = mapped_column(String(255), nullable=True)

    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    rate_limited_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # 冷却到期
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    provider = relationship("Provider", lazy="selectin")
    group = relationship("KeyGroup", lazy="selectin")

    @property
    def provider_name(self) -> str | None:
        return self.provider.name if self.provider else None

    @property
    def group_name(self) -> str | None:
        return self.group.name if self.group else None
