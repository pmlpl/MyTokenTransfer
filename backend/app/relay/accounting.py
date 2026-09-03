"""计费与调用日志：按价表算 cost、写 CallLog。

用独立会话写，即使流式响应中途客户端断开、主会话已关闭，日志也能落库。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import CallLog, ModelPricing


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def write_call_log(
    *,
    channel_id: int | None,
    tier_order: int | None,
    provider_id: int | None,
    key_id: int | None,
    virtual_model: str | None,
    native_model: str | None,
    usage: dict | None,
    latency_ms: int,
    status: str,
    error: str | None,
    stream: bool,
) -> None:
    """用独立会话写调用日志（流式时主会话已关闭）。按价表算 cost。"""
    u = usage or {}
    pt = int(u.get("prompt_tokens", 0) or 0)
    ct = int(u.get("completion_tokens", 0) or 0)
    # 缓存命中 tokens：兼容 OpenAI (prompt_tokens_details.cached_tokens) 和 DeepSeek (prompt_cache_hit_tokens)
    cached = 0
    ptd = u.get("prompt_tokens_details")
    if isinstance(ptd, dict):
        cached = int(ptd.get("cached_tokens", 0) or 0)
    if not cached:
        cached = int(u.get("prompt_cache_hit_tokens", 0) or 0)
    async with AsyncSessionLocal() as s:
        cost = 0.0
        model_for_price = native_model or virtual_model
        if model_for_price and (pt or ct):
            p = await s.scalar(
                select(ModelPricing).where(ModelPricing.model_name == model_for_price)
            )
            if p is not None:
                uncached = max(0, pt - cached)
                cost = round(
                    cached * float(p.cached_input_price) / 1_000_000
                    + uncached * float(p.input_price) / 1_000_000
                    + ct * float(p.output_price) / 1_000_000,
                    6,
                )
        s.add(
            CallLog(
                created_at=_now(),
                channel_id=channel_id,
                tier_order=tier_order,
                provider_id=provider_id,
                key_id=key_id,
                virtual_model=virtual_model,
                native_model=native_model,
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=int(u.get("total_tokens", 0) or 0),
                cached_tokens=cached,
                cost=cost,
                latency_ms=latency_ms,
                status=status,
                error=(error[:500] if error else None),
                stream=stream,
            )
        )
        await s.commit()
