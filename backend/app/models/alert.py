from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class AlertEvent(Base, TimestampMixin):
    """告警事件：密钥状态劣化等需人工关注的记录。"""

    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # key_exhausted / key_invalid
    severity: Mapped[str] = mapped_column(String(16), default="warning", nullable=False)  # warning / critical
    # 宽松外键（同 CallLog）：密钥/供应商被删后告警仍留存作为审计
    key_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    provider_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
