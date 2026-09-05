"""计费与调用日志：按价表算 cost、写 CallLog。

用独立会话写，即使流式响应中途客户端断开、主会话已关闭，日志也能落库。
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import CallLog, ModelPricing

# 空闲时段（北京时间 00:30–08:30，含头不含尾）：DeepSeek 官方按此时段给折扣价。
# 价表里空闲价填 0 表示该模型无分时定价，全天按标准价计费。
_OFF_PEAK_START = time(0, 30)
_OFF_PEAK_END = time(8, 30)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _in_off_peak(now_utc: datetime) -> bool:
    """UTC 时间换算北京时间后判断是否落在空闲时段（窗口跨午夜，直接比较 time）。"""
    bj = now_utc + timedelta(hours=8)
    return _OFF_PEAK_START <= bj.time() < _OFF_PEAK_END


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
                # 空闲时段用折扣价；某档空闲价未填（0）则沿用标准价
                if _in_off_peak(_now()):
                    in_price = float(p.off_peak_input_price) or float(p.input_price)
                    out_price = float(p.off_peak_output_price) or float(p.output_price)
                else:
                    in_price = float(p.input_price)
                    out_price = float(p.output_price)
                cost = round(
                    cached * float(p.cached_input_price) / 1_000_000
                    + uncached * in_price / 1_000_000
                    + ct * out_price / 1_000_000,
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
