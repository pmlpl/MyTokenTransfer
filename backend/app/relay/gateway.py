from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_value
from app.database import AsyncSessionLocal
from app.models import ApiKey, CallLog, Channel, ChannelKey, ChannelTier, KeyStatus, ModelMapping
from app.models.enums import KeyStrategy, ProbeKind
from app.services.health_check import _DEFAULT_BASES, _join

_RATE_LIMIT_COOLDOWN = 60


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _base_url(provider) -> str:
    return provider.base_url or _DEFAULT_BASES.get(provider.slug, "")


async def _resolve_native(virtual_model: str, provider_id: int, db: AsyncSession) -> str:
    mm = await db.scalar(
        select(ModelMapping).where(
            ModelMapping.virtual_model == virtual_model, ModelMapping.provider_id == provider_id
        )
    )
    return mm.native_model if mm else virtual_model


async def _ordered_keys(tier: ChannelTier, strategy: KeyStrategy, db: AsyncSession) -> list[ApiKey]:
    """层内按策略排序的可用密钥（跳过非 active / 限流冷却中）。"""
    rows = await db.execute(select(ChannelKey).where(ChannelKey.channel_tier_id == tier.id))
    cks = list(rows.scalars())
    pairs: list[tuple[Any, ApiKey]] = []
    for ck in cks:
        ak = await db.get(ApiKey, ck.key_id)
        if not ak:
            continue
        if ak.status != KeyStatus.active:
            continue
        if ak.rate_limited_until and ak.rate_limited_until > _now():
            continue
        pairs.append((ck, ak))

    if strategy == KeyStrategy.priority:
        pairs.sort(key=lambda x: (-x[0].priority, x[0].id))
    elif strategy == KeyStrategy.weighted:
        # 按 weight 重复后打散（确定性随机）
        import random

        pool: list[tuple[Any, ApiKey]] = []
        for p in pairs:
            pool.extend([p] * max(1, p[0].weight))
        random.shuffle(pool)
        seen: set[int] = set()
        ordered = []
        for p in pool:
            if p[1].id in seen:
                continue
            seen.add(p[1].id)
            ordered.append(p)
        # 补上未被随机选中的
        for p in pairs:
            if p[1].id not in seen:
                ordered.append(p)
                seen.add(p[1].id)
        pairs = ordered
    else:  # round_robin：稳定顺序即可，跨请求的轮转交给上游 key 状态变化
        pairs.sort(key=lambda x: x[0].id)
    return [p[1] for p in pairs]


def _classify_error(status_code: int, body_text: str) -> tuple[bool, KeyStatus | None, str]:
    """返回 (是否可重试/切换, 标记的状态, 错误描述)。"""
    text = (body_text or "").lower()
    if status_code in (401, 403):
        return True, KeyStatus.invalid, f"HTTP {status_code}: {body_text[:200]}"
    if status_code == 429:
        # 429 里带 quota/额度关键词的是真耗尽（冷却也救不回来），标 exhausted
        if any(k in text for k in ("quota", "entitlement", "余额", "exhausted", "insufficient")):
            return True, KeyStatus.exhausted, f"HTTP 429 quota: {body_text[:200]}"
        return True, KeyStatus.rate_limited, f"HTTP 429: {body_text[:200]}"
    if status_code == 400 and any(k in text for k in ("insufficient_quota", "quota", "余额", "exceeded", "insufficient")):
        return True, KeyStatus.exhausted, f"HTTP 400 quota: {body_text[:200]}"
    if status_code >= 500:
        return True, None, f"HTTP {status_code}: {body_text[:200]}"
    # 400 等客户端错误：不可重试（请求本身有问题，换 key 也没用）
    return False, None, f"HTTP {status_code}: {body_text[:200]}"


async def _mark_key(
    key: ApiKey,
    status: KeyStatus | None,
    db: AsyncSession,
    *,
    message: str | None = None,
) -> None:
    if status is None:
        return
    before = key.status
    key.status = status
    if status == KeyStatus.rate_limited:
        from datetime import timedelta

        key.rate_limited_until = _now() + timedelta(seconds=_RATE_LIMIT_COOLDOWN)
    # 状态劣化到 exhausted/invalid 时写一条告警（由调用方随同 commit）
    from app.services.alerts import emit_key_alert

    await emit_key_alert(
        db,
        key_id=key.id,
        provider_id=key.provider_id,
        key_name=key.name,
        before=before,
        after=status,
        message=message or "上游请求失败",
    )
    await db.commit()


async def _write_log(
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
    from app.models import ModelPricing  # 局部导入避免循环

    u = usage or {}
    pt = int(u.get("prompt_tokens", 0) or 0)
    ct = int(u.get("completion_tokens", 0) or 0)
    async with AsyncSessionLocal() as s:
        cost = 0.0
        model_for_price = native_model or virtual_model
        if model_for_price and (pt or ct):
            p = await s.scalar(
                select(ModelPricing).where(ModelPricing.model_name == model_for_price)
            )
            if p is not None:
                cost = round(
                    pt * float(p.input_price) / 1_000_000 + ct * float(p.output_price) / 1_000_000,
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
                cost=cost,
                latency_ms=latency_ms,
                status=status,
                error=(error[:500] if error else None),
                stream=stream,
            )
        )
        await s.commit()


def _build_payload(body: dict, native_model: str, stream: bool) -> dict:
    """构造发给上游的请求体：透传 OpenAI 参数，覆盖 model/stream。"""
    payload = dict(body)
    payload["model"] = native_model
    payload["stream"] = stream
    # 流式时尽量带上 usage 统计
    if stream and "stream_options" not in payload:
        payload["stream_options"] = {"include_usage": True}
    return payload


def _headers(raw_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw_key}", "Content-Type": "application/json"}


async def route_chat_completions(
    channel: Channel, body: dict, db: AsyncSession
) -> JSONResponse | StreamingResponse:
    virtual = channel.virtual_model
    stream = bool(body.get("stream"))
    max_retries = max(1, channel.max_retries)

    # 构建尝试序列：按 tier.order 升序，每层按策略排序的可用 key
    tiers = sorted(channel.tiers, key=lambda t: t.order)
    attempts: list[tuple[ChannelTier, ApiKey]] = []
    for tier in tiers:
        keys = await _ordered_keys(tier, channel.strategy, db)
        for k in keys[:max_retries]:
            attempts.append((tier, k))

    if not attempts:
        return JSONResponse(
            status_code=503,
            content={"error": {"message": "无可用密钥（全部失效/限流）", "type": "no_available_key"}},
        )

    last_error = "无可用上游"
    for tier, key in attempts:
        native = await _resolve_native(virtual, tier.provider_id, db)
        base = _base_url(tier.provider)
        if not base:
            last_error = f"供应商 {tier.provider.slug} 缺少 base_url"
            continue
        url = _join(base, "chat/completions")
        try:
            raw = decrypt_value(key.key_value)
        except Exception:
            await _mark_key(key, KeyStatus.invalid, db, message="密文解密失败")
            continue

        payload = _build_payload(body, native, stream)
        t0 = time.time()

        if stream:
            client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))
            try:
                req = client.build_request("POST", url, headers=_headers(raw), json=payload)
                response = await client.send(req, stream=True)
            except Exception as e:
                await client.aclose()
                last_error = f"{type(e).__name__}: {e}"
                continue  # 瞬时网络错误，换下一个
            if response.status_code != 200:
                await response.aread()
                err_text = response.text
                await response.aclose()
                await client.aclose()
                retry, mark, msg = _classify_error(response.status_code, err_text)
                await _mark_key(key, mark, db, message=msg)
                last_error = msg
                if retry:
                    continue
                return JSONResponse(
                    status_code=response.status_code,
                    content={"error": {"message": msg, "type": "upstream_error"}},
                )
            # 200：流已打开，故障转移窗口关闭
            meta = {
                "channel_id": channel.id,
                "tier_order": tier.order,
                "provider_id": tier.provider_id,
                "key_id": key.id,
                "virtual_model": virtual,
                "native_model": native,
                "t0": t0,
            }
            return StreamingResponse(
                _stream_passthrough(client, response, meta), media_type="text/event-stream"
            )
        else:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
                    resp = await client.post(url, headers=_headers(raw), json=payload)
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                continue
            latency_ms = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                await _write_log(
                    channel_id=channel.id,
                    tier_order=tier.order,
                    provider_id=tier.provider_id,
                    key_id=key.id,
                    virtual_model=virtual,
                    native_model=native,
                    usage=data.get("usage"),
                    latency_ms=latency_ms,
                    status="success",
                    error=None,
                    stream=False,
                )
                return JSONResponse(status_code=200, content=data)
            retry, mark, msg = _classify_error(resp.status_code, resp.text)
            await _mark_key(key, mark, db, message=msg)
            await _write_log(
                channel_id=channel.id,
                tier_order=tier.order,
                provider_id=tier.provider_id,
                key_id=key.id,
                virtual_model=virtual,
                native_model=native,
                usage=None,
                latency_ms=latency_ms,
                status="error",
                error=msg,
                stream=False,
            )
            last_error = msg
            if retry:
                continue
            return JSONResponse(
                status_code=resp.status_code,
                content={"error": {"message": msg, "type": "upstream_error"}},
            )

    return JSONResponse(
        status_code=502,
        content={"error": {"message": f"所有上游均失败：{last_error}", "type": "all_upstreams_failed"}},
    )


async def _stream_passthrough(client: httpx.AsyncClient, response: httpx.Response, meta: dict):
    usage: dict | None = None
    status = "success"
    err = None
    try:
        async for line in response.aiter_lines():
            yield line + "\n"
            if line.startswith("data: ") and '"usage"' in line:
                try:
                    obj = json.loads(line[6:])
                    if obj.get("usage"):
                        usage = obj["usage"]
                except Exception:
                    pass
    except Exception as e:
        status = "error"
        err = f"{type(e).__name__}: {e}"
        # 流中途出错：返回 SSE 错误事件（已发送的内容无法回收）
        try:
            yield f'data: {json.dumps({"error": {"message": str(e), "type": "stream_error"}})}\n\n'
        except Exception:
            pass
    finally:
        try:
            await response.aclose()
        except Exception:
            pass
        await client.aclose()
        latency_ms = int((time.time() - meta["t0"]) * 1000)
        # 用独立任务写日志：即使客户端中途断开导致生成器被取消，日志也能落库
        asyncio.create_task(
            _write_log(
                channel_id=meta["channel_id"],
                tier_order=meta["tier_order"],
                provider_id=meta["provider_id"],
                key_id=meta["key_id"],
                virtual_model=meta["virtual_model"],
                native_model=meta["native_model"],
                usage=usage,
                latency_ms=latency_ms,
                status=status,
                error=err,
                stream=True,
            )
        )
