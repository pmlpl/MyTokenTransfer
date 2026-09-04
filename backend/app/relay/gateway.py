"""转发网关 HTTP 编排层。

把请求拆成有序的 (tier, key) 尝试序列（balancer 负责），逐个发上游，
命中可恢复错误就标状态并换下一个、整池失败沿 Tier 链兜底，
成功/失败都让 accounting 写 CallLog。流式响应中途出错不再切换。
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import structlog
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_value
from app.models import ApiKey, Channel, ChannelTier, KeyStatus
from app.relay.accounting import write_call_log
from app.relay.balancer import (
    base_url_for,
    mark_key,
    ordered_keys,
    resolve_native,
    routing_affinity,
)
from app.relay.errors import classify_error
from app.services.health_check import _join

log = structlog.get_logger("relay")


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


async def _build_attempts(
    channel: Channel, db: AsyncSession, affinity: int = 0
) -> list[tuple[ChannelTier, ApiKey]]:
    """按 Tier 升序，每层取策略排序后的可用 key（受 max_retries 限）。

    affinity 为内容哈希：同一会话固定同一 key，最大化上游前缀缓存命中。
    """
    max_retries = max(1, channel.max_retries)
    tiers = sorted(channel.tiers, key=lambda t: t.order)
    attempts: list[tuple[ChannelTier, ApiKey]] = []
    for tier in tiers:
        keys = await ordered_keys(tier, channel.strategy, db, affinity)
        for k in keys[:max_retries]:
            attempts.append((tier, k))
    return attempts


def _no_key_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error": {"message": "无可用密钥（全部失效/限流）", "type": "no_available_key"}},
    )


async def route_chat_completions(
    channel: Channel, body: dict, db: AsyncSession
) -> JSONResponse | StreamingResponse:
    virtual = channel.virtual_model
    stream = bool(body.get("stream"))
    # 缓存亲和：由虚拟模型 + 消息稳定前缀派生路由哈希，同一会话固定同一 key
    affinity = routing_affinity(virtual, body)

    attempts = await _build_attempts(channel, db, affinity)
    if not attempts:
        log.warning("no_available_key", channel_id=channel.id, virtual_model=virtual)
        return _no_key_response()

    last_error = "无可用上游"
    for tier, key in attempts:
        native = await resolve_native(virtual, tier.provider_id, db)
        base = base_url_for(tier.provider)
        if not base:
            last_error = f"供应商 {tier.provider.slug} 缺少 base_url"
            continue
        url = _join(base, "chat/completions")
        try:
            raw = decrypt_value(key.key_value)
        except Exception:
            await mark_key(key, KeyStatus.invalid, db, message="密文解密失败")
            continue

        payload = _build_payload(body, native, stream)
        t0 = time.time()

        if stream:
            client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0))
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
                retry, mark, msg = classify_error(response.status_code, err_text)
                await mark_key(key, mark, db, message=msg)
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
                async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
                    resp = await client.post(url, headers=_headers(raw), json=payload)
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                continue
            latency_ms = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                await write_call_log(
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
            retry, mark, msg = classify_error(resp.status_code, resp.text)
            await mark_key(key, mark, db, message=msg)
            await write_call_log(
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

    log.warning(
        "all_upstreams_failed",
        channel_id=channel.id,
        virtual_model=virtual,
        error=last_error,
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
            write_call_log(
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


async def route_embeddings(channel: Channel, body: dict, db: AsyncSession) -> JSONResponse:
    """/v1/embeddings：复用同一套 Tier fallback + 池内负载均衡 + key 状态机。

    与 chat 不同的是：embeddings 不支持流式，响应 usage 仅含 prompt/total tokens。
    """
    virtual = channel.virtual_model
    # 缓存亲和：embeddings 按 input 前缀派生路由哈希
    affinity = routing_affinity(virtual, body)

    attempts = await _build_attempts(channel, db, affinity)
    if not attempts:
        log.warning("no_available_key", channel_id=channel.id, virtual_model=virtual)
        return _no_key_response()

    last_error = "无可用上游"
    for tier, key in attempts:
        native = await resolve_native(virtual, tier.provider_id, db)
        base = base_url_for(tier.provider)
        if not base:
            last_error = f"供应商 {tier.provider.slug} 缺少 base_url"
            continue
        url = _join(base, "embeddings")
        try:
            raw = decrypt_value(key.key_value)
        except Exception:
            await mark_key(key, KeyStatus.invalid, db, message="密文解密失败")
            continue

        # embeddings 不支持 stream，仅透传 model 与 input 等参数
        payload = dict(body)
        payload["model"] = native
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
                resp = await client.post(url, headers=_headers(raw), json=payload)
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            continue
        latency_ms = int((time.time() - t0) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            await write_call_log(
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
        retry, mark, msg = classify_error(resp.status_code, resp.text)
        await mark_key(key, mark, db, message=msg)
        await write_call_log(
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

    log.warning(
        "all_upstreams_failed",
        channel_id=channel.id,
        virtual_model=virtual,
        error=last_error,
    )
    return JSONResponse(
        status_code=502,
        content={"error": {"message": f"所有上游均失败：{last_error}", "type": "all_upstreams_failed"}},
    )
