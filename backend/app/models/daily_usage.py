from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class DailyUsage(Base, TimestampMixin):
    """按日预聚合的用量：每个 (日期, 密钥, 模型) 一行。

    由 rollup 从 CallLog 汇总而来，仪表盘查这张小表而非扫描明细表，
    避免 call_logs 膨胀后统计查询变慢。
    """

    __tablename__ = "daily_usage"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # 宽松外键（同 CallLog）：密钥被删后聚合行仍留存作历史
    key_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost: Mapped[float] = mapped_column(Numeric(12, 6), default=0, nullable=False)
