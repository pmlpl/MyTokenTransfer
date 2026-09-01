from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import TimestampMixin
from app.models.enums import ProbeKind
from app.database import Base


class Provider(Base, TimestampMixin):
    """AI 供应商模板：OpenAI / Anthropic / Gemini ……"""

    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # 展示名
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # litellm 前缀，如 openai / anthropic / gemini
    base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)     # 接口基址覆盖
    probe_kind: Mapped[ProbeKind] = mapped_column(default=ProbeKind.openai_models, nullable=False)
    models: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)       # 支持的 native 模型清单
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
