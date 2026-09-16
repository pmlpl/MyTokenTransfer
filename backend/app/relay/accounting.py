"""计费与调用日志：按价表算 cost、写 CallLog。

用独立会话写，即使流式响应中途客户端断开、主会话已关闭，日志也能落库。
"""
from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import select

from app.core.cache import cache_get, cache_set
from app.database import AsyncSessionLocal
from app.models import CallLog, ModelPricing

# 峰谷时段（DeepSeek 官方定义）：北京时间周一至周五 9:00–12:00、14:00–18:00 为
# 高峰，其余时间（夜间、周末）为空闲，空闲价约为高峰一半。
# 价表 input_price/output_price 存高峰价，off_peak_* 存空闲价；
# 空闲价填 0 表示该模型无分时定价，全天按标准（高峰）价计费。
_PEAK_WINDOWS = ((time(9, 0), time(12, 0)), (time(14, 0), time(18, 0)))


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _in_peak(now_utc: datetime) -> bool:
    """UTC 换算北京时间后判断是否处于高峰时段（周一至周五 9–12 / 14–18 点）。"""
    bj = now_utc + timedelta(hours=8)
    if bj.weekday() >= 5:  # 5=周六 6=周日，周末全天空闲
        return False
    t = bj.time()
    return any(start <= t < end for start, end in _PEAK_WINDOWS)


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
            # Redis 缓存定价（纯配置数据，变更频率低）；
            # "none" 哨兵表示该模型无定价行，避免重复空查 DB
            cache_key = f"price:{model_for_price}"
            cached_price = await cache_get(cache_key)
            p_data: dict | None = None
            if cached_price is not None:
                if cached_price != "none":
                    p_data = json.loads(cached_price)
            else:
                p = await s.scalar(
                    select(ModelPricing).where(ModelPricing.model_name == model_for_price)
                )
                if p is not None:
                    p_data = {
                        "input_price": float(p.input_price),
                        "cached_input_price": float(p.cached_input_price),
                        "output_price": float(p.output_price),
                        "off_peak_input_price": float(p.off_peak_input_price),
                        "off_peak_output_price": float(p.off_peak_output_price),
                    }
                    await cache_set(cache_key, json.dumps(p_data))
                else:
                    await cache_set(cache_key, "none")
            if p_data:
                uncached = max(0, pt - cached)
                # 高峰档用标准价，空闲档用折扣价；某档空闲价未填（0）则沿用高峰价
                if _in_peak(_now()):
                    in_price = p_data["input_price"]
                    out_price = p_data["output_price"]
                else:
                    in_price = p_data["off_peak_input_price"] or p_data["input_price"]
                    out_price = p_data["off_peak_output_price"] or p_data["output_price"]
                cost = round(
                    cached * p_data["cached_input_price"] / 1_000_000
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
