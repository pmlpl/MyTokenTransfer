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
    final_resort_keys,
    last_resort_keys,
    mark_key,
    mark_model_quota_limited,
    mark_tier_rate_limited,
    model_quota_is_limited,
    ordered_keys,
    resolve_native,
    routing_affinity,
    tier_is_rate_limited,
)
from app.relay.errors import classify_error, is_model_pool_quota
from app.services.health_check import _join

log = structlog.get_logger("relay")


def _normalize_thinking(payload: dict) -> None:
    """修正 thinking 与 reasoning_effort 的组合，避免上游 400 invalid thinking type。

    智谱 API 约束：reasoning_effort=none 时 thinking 必须 disabled；
    reasoning_effort≠none 时 thinking 必须 enabled。部分客户端（如 zcode）
    会发矛盾组合，这里自动对齐。
    """
    thinking = payload.get("thinking")
    if thinking is None:
        return
    effort = payload.get("reasoning_effort")
    effort_is_none = effort is None or str(effort).lower() in ("none", "disabled", "")
    # 归一化 thinking 值：字符串/布尔都接受
    if isinstance(thinking, bool):
        thinking_val = "enabled" if thinking else "disabled"
    else:
        thinking_val = str(thinking).lower()
    if effort_is_none:
        if thinking_val != "disabled":
            payload["thinking"] = "disabled"
    else:
        if thinking_val != "enabled":
            payload["thinking"] = "enabled"


def _build_payload(body: dict, native_model: str, stream: bool) -> dict:
    """构造发给上游的请求体：透传 OpenAI 参数，覆盖 model/stream。"""
    payload = dict(body)
    payload["model"] = native_model
    payload["stream"] = stream
    # 修正 thinking/reasoning_effort 组合（zcode 等客户端易发矛盾参数）
    _normalize_thinking(payload)
    # 流式时尽量带上 usage 统计
    if stream and "stream_options" not in payload:
        payload["stream_options"] = {"include_usage": True}
    return payload


def _headers(raw_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw_key}", "Content-Type": "application/json"}


# 网络层瞬时故障重试：上游域名偶发 TLS 握手被掐/连接重置（SNI 阻断、NAT 抖动），
# 同池所有 key 指向同一上游域名，换 key 重试没有意义，原地重试才有用。
# 仅重试连接/发送阶段的异常（流未打开、请求未发出），HTTP 状态码一律交给 classify_error。
_NETWORK_RETRY = 3
_NETWORK_BACKOFF = 1.0  # 秒


async def _send_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    json: dict,
    stream: bool = False,
):
    for attempt in range(_NETWORK_RETRY):
        try:
            if stream:
                req = client.build_request("POST", url, headers=headers, json=json)
                return await client.send(req, stream=True)
            return await client.post(url, headers=headers, json=json)
        except Exception as e:
            if attempt == _NETWORK_RETRY - 1:
                raise
            log.warning("network_retry", url=url, attempt=attempt + 1,
                        error=f"{type(e).__name__}: {e}")
            await asyncio.sleep(_NETWORK_BACKOFF)


async def _build_attempts(
    channel: Channel, db: AsyncSession, affinity: int = 0
) -> list[tuple[ChannelTier, ApiKey]]:
    """按 Tier 升序，每层取策略排序后的可用 key（受 max_retries 限）。

    affinity 为内容哈希：同一会话固定同一 key，最大化上游前缀缓存命中。
    全部 key 处于 exhausted 时，尾部追加最久未测的 exhausted 密钥做最后
    尝试（额度窗口可能已重置），避免直接 503。
    """
    max_retries = max(1, channel.max_retries)
    tiers = sorted(channel.tiers, key=lambda t: t.order)
    virtual = channel.virtual_model
    attempts: list[tuple[ChannelTier, ApiKey]] = []
    for tier in tiers:
        if tier_is_rate_limited(tier.id):
            continue  # 同池短路：整层处于 TPM 冷却，跳过
        keys = await ordered_keys(tier, channel.strategy, db, affinity)
        # 模型池冷却：该 key 上本模型的积分池刚报 429，跳过（其他模型不受影响）
        for k in keys[:max_retries]:
            if model_quota_is_limited(k.id, virtual):
                continue
            attempts.append((tier, k))
    # 全池耗尽兜底：active 尝试之后再追加最久未测的 exhausted 密钥。
    # 额度窗口可能已滚动重置，试一次的代价是快速 429，收益是少一次 503；
    # 仅在前面的尝试可重试失败后才会轮到，正常路径零影响。
    for tier in tiers:
        if tier_is_rate_limited(tier.id):
            continue
        for k in await last_resort_keys(tier, db, max_retries):
            attempts.append((tier, k))
    # 全池模型池冷却兜底：小池子（如 deepseek 仅 2 把 key）在所有 key 的
    # (key,模型) 积分池同时进入 300 秒冷却时，上面产出空 attempts 会直接
    # 503。积分池按窗口滚动、恢复常快于冷却期，追加冷却中的组合做最后
    # 尝试，失败代价只是再吃一次快速 429，与 exhausted 兜底同理。
    if not attempts:
        for tier in tiers:
            if tier_is_rate_limited(tier.id):
                continue
            keys = await ordered_keys(tier, channel.strategy, db, affinity)
            for k in keys[:max_retries]:
                if model_quota_is_limited(k.id, virtual):
                    attempts.append((tier, k))
    # 最终兜底：所有层都在 tier TPM 冷却时（高并发客户端瞬时打满共享
    # TPM → 整层 60 秒冷却，各级兜底全被守卫跳过），无视冷却标记取层内
    # 密钥试最后一轮；503 前的最后一搏，窗口滚动后这一把常能成功。
    if not attempts:
        for tier in tiers:
            for k in await final_resort_keys(tier, db, max_retries):
                attempts.append((tier, k))
    if attempts and all(k.status != KeyStatus.active for _, k in attempts):
        log.warning("no_active_key_last_resort", channel_id=channel.id,
                    virtual_model=channel.virtual_model, exhausted_attempts=len(attempts))
    return attempts


def _no_key_response() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error": {"message": "无可用密钥（全部失效/限流）", "type": "no_available_key"}},
    )


async def _revive_if_exhausted(key: ApiKey, db: AsyncSession) -> None:
    """最后尝试拿到 200 = 真实流量证明额度窗口已恢复，直接把密钥拉回 active。

    比探针复测（最长等 10 分钟巡检）更快回池；告警随 mark_key 自动 resolve。
    """
    if key.status == KeyStatus.exhausted:
        await mark_key(key, KeyStatus.active, db, message="真实请求成功，自动恢复")


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
    # attempts 全部带着模型池冷却标记时（兜底场景）不跳过，否则兜底白加；
    # 存在无冷却组合时仍跳过冷却中的，避免浪费重试次数。tier 冷却同理：
    # 最终兜底拿到的组合全部处于整层冷却时豁免守卫。
    allow_quota_limited = bool(attempts) and all(
        model_quota_is_limited(k.id, virtual) for _, k in attempts
    )
    allow_tier_limited = bool(attempts) and all(
        tier_is_rate_limited(t.id) for t, _ in attempts
    )
    for tier, key in attempts:
        if tier_is_rate_limited(tier.id) and not allow_tier_limited:
            continue  # 同池短路：该层已在本次请求内被标记 TPM 冷却
        if model_quota_is_limited(key.id, virtual) and not allow_quota_limited:
            continue  # 模型池冷却：该 key 上本模型的积分池刚报 429
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
                response = await _send_with_retry(
                    client, url, headers=_headers(raw), json=payload, stream=True
                )
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
                if mark == KeyStatus.rate_limited:
                    mark_tier_rate_limited(tier.id)  # 同池短路：整层 TPM 冷却
                elif mark == KeyStatus.exhausted and is_model_pool_quota(err_text):
                    # 模型池级额度耗尽：只冷却 (key, 模型) 组合，不把整把 key 标死
                    mark_model_quota_limited(key.id, virtual)
                else:
                    await mark_key(key, mark, db, message=msg)
                last_error = msg
                if retry:
                    continue
                return JSONResponse(
                    status_code=response.status_code,
                    content={"error": {"message": msg, "type": "upstream_error"}},
                )
            # 200：流已打开，故障转移窗口关闭
            await _revive_if_exhausted(key, db)
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
                    resp = await _send_with_retry(client, url, headers=_headers(raw), json=payload)
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                continue
            latency_ms = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                await _revive_if_exhausted(key, db)
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
            if mark == KeyStatus.rate_limited:
                mark_tier_rate_limited(tier.id)  # 同池短路：整层 TPM 冷却
            elif mark == KeyStatus.exhausted and is_model_pool_quota(resp.text):
                # 模型池级额度耗尽：只冷却 (key, 模型) 组合，不把整把 key 标死
                mark_model_quota_limited(key.id, virtual)
            else:
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
    # 同 chat：全冷却时豁免跳过，兜底组合才有机会执行
    allow_quota_limited = bool(attempts) and all(
        model_quota_is_limited(k.id, virtual) for _, k in attempts
    )
    allow_tier_limited = bool(attempts) and all(
        tier_is_rate_limited(t.id) for t, _ in attempts
    )
    for tier, key in attempts:
        if tier_is_rate_limited(tier.id) and not allow_tier_limited:
            continue  # 同池短路：该层已被标记 TPM 冷却
        if model_quota_is_limited(key.id, virtual) and not allow_quota_limited:
            continue  # 模型池冷却：该 key 上本模型的积分池刚报 429
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
                resp = await _send_with_retry(client, url, headers=_headers(raw), json=payload)
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            continue
        latency_ms = int((time.time() - t0) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            await _revive_if_exhausted(key, db)
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
        if mark == KeyStatus.rate_limited:
            mark_tier_rate_limited(tier.id)  # 同池短路：整层 TPM 冷却
        elif mark == KeyStatus.exhausted and is_model_pool_quota(resp.text):
            # 模型池级额度耗尽：只冷却 (key, 模型) 组合，不把整把 key 标死
            mark_model_quota_limited(key.id, virtual)
        else:
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
