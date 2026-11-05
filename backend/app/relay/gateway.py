"""转发网关 HTTP 编排层。

把请求拆成有序的 (tier, key) 尝试序列（balancer 负责），逐个发上游，
命中可恢复错误就标状态并换下一个、整池失败沿 Tier 链兜底，
成功/失败都让 accounting 写 CallLog。流式响应中途出错不再切换。
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import httpx
import structlog
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_value
from app.models import ApiKey, Channel, ChannelTier, KeyStatus
from app.relay.accounting import write_call_log
from app.relay.balancer import (
    account_is_rate_limited,
    account_rate_limited_remaining,
    base_url_for,
    final_resort_keys,
    last_resort_keys,
    mark_account_rate_limited,
    mark_key,
    mark_key_model_status,
    mark_model_quota_limited,
    model_quota_is_limited,
    ordered_keys,
    reset_account_backoff,
    resolve_native,
    routing_affinity,
)
from app.relay.errors import classify_error, extract_error_code, is_model_pool_quota
from app.relay.token_bucket import (
    acquire_rpm,
    ceil_div_seconds,
    max_wait_seconds,
    release_rpm,
    tpm_on_rate_limit,
    tpm_release,
    tpm_reserve,
    tpm_settle,
    tpm_wait,
)
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


# 支持 thinking/reasoning_effort 的供应商 base_url 关键词
# 仅智谱原生 API（open.bigmodel.cn）支持，其他供应商一律剥离
_THINKING_SUPPORTED_PROVIDERS = ("bigmodel.cn",)


def _strip_unsupported_params(payload: dict, base_url: str) -> None:
    """按供应商剥离不兼容的参数，避免上游 400 invalid arguments。

    ZCode 更新后会发 thinking/reasoning_effort，但转售商（SenseNova 等）
    的 OpenAI 兼容端点不接受这些非标参数，只对智谱原生 API 保留。
    """
    base_lower = (base_url or "").lower()
    if not any(k in base_lower for k in _THINKING_SUPPORTED_PROVIDERS):
        payload.pop("thinking", None)
        payload.pop("reasoning_effort", None)


def _build_payload(body: dict, native_model: str, stream: bool, base_url: str = "") -> dict:
    """构造发给上游的请求体：透传 OpenAI 参数，覆盖 model/stream。"""
    payload = dict(body)
    payload["model"] = native_model
    payload["stream"] = stream
    # 先按供应商剥离不兼容参数，再修正剩余的 thinking 组合
    _strip_unsupported_params(payload, base_url)
    _normalize_thinking(payload)
    # 流式时尽量带上 usage 统计
    if stream and "stream_options" not in payload:
        payload["stream_options"] = {"include_usage": True}
    return payload


def _headers(raw_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw_key}", "Content-Type": "application/json"}


# 上游网关读取请求体失败（实测 B.AI 网关对 >1MB 请求体偶发失败，返回
# 400001 "read body failed"；同一上游换 key 无意义，应原地重试一次）。
# 这不是 key 的状态问题，重试成功率高。
_GATEWAY_READ_BODY_ERRORS = ("read body failed", "read_body_failed", "reading request body")


def _is_gateway_read_body_error(err_text: str) -> bool:
    text = (err_text or "").lower()
    return any(m in text for m in _GATEWAY_READ_BODY_ERRORS)


# 网络层瞬时故障重试：上游域名偶发 TLS 握手被掐/连接重置（SNI 阻断、NAT 抖动），
# 同池所有 key 指向同一上游域名，换 key 重试没有意义，原地重试才有用。
# 仅重试连接/发送阶段的异常（流未打开、请求未发出），HTTP 状态码一律交给 classify_error。
_NETWORK_RETRY = 3
_NETWORK_BACKOFF = 1.0  # 秒

# 连接阶段超时（秒）。上游按 SNI 阻断时表现为连不上，而不是连上后报错，
# 所以这个值直接决定"卡住"的时长。原来 10 秒 × 3 次重试 × N 把 key，
# 6 把 key 最坏要 180 秒才返回 502——客户端只会看到长时间无响应后断连。
_NETWORK_CONNECT_TIMEOUT = 5.0

# 域名级熔断：网络层（连接超时/拒绝/TLS 中断）失败是按**域名**发生的，
# 与用哪把 key 无关。一旦某域名的连接失败，同一请求内不再对它换 key 重试，
# 直接整体失败返回 503，让客户端快速重连——比挂着连接干等 3 分钟好得多。
# 熔断时长取 30 秒：覆盖一次典型阻断抖动，又不至于把瞬时抖动放大成长期不可用。
_NETWORK_BREAKER_SEC = 30.0
_domain_blocked_until: dict[str, float] = {}


def _domain_of(url: str) -> str:
    """从 URL 里取 host 作为熔断键。"""
    try:
        return url.split("://", 1)[1].split("/", 1)[0]
    except IndexError:
        return url


def _domain_is_cooling(url: str) -> bool:
    until = _domain_blocked_until.get(_domain_of(url))
    return until is not None and until > time.monotonic()


def _trip_domain_breaker(url: str) -> None:
    host = _domain_of(url)
    _domain_blocked_until[host] = time.monotonic() + _NETWORK_BREAKER_SEC
    log.warning("network_breaker_tripped", host=host,
                cooldown_s=_NETWORK_BREAKER_SEC)

# 账号限流冷却短路阈值（秒）：所有候选 key 冷却剩余 >此值时直接返回 429，
# 跳过 final_resort。设 0 = 只要全在冷却中就短路——final_resort 对 DeepSeek
# 等持续限流的模型从未成功，只徒增 doomed 请求并重置冷却计时器导致死循环。
# 冷却过期后 key 自然回到 attempts，不需要 final_resort 赌窗口。
_FRESH_COOLDOWN_SEC = 0

# 全池冷却时，剩余冷却不超过此值就在网关内部等掉再发，而不是返回 429。
# 实测上游 429 后 20–32 秒恢复，退避首档 20 秒正好落在这个范围内：
# 原地等 20 秒大概率成功，而返回 429 会让客户端先做自己的退避 + 重连，
# 用户看到的就是 ZCode 的"重新连接中 5/10"。
_SHORT_COOLDOWN_WAIT = float(os.environ.get("MTT_RELAY_SHORT_COOLDOWN_WAIT") or 25.0)


def billable_tokens(usage: dict | None) -> int:
    """本次请求实际占用上游配额的 token 数。

    上游（SenseNova）的 TPM 口径只算**未命中前缀缓存**的输入 + 输出：
    实测同一段 48k 文本连发，命中缓存后不再计入限流额度；用户侧统计也
    印证——日用量 5500 万 token 里 5400 万是"输入（命中）"，全程未限流。
    因此把 prompt_tokens 全额计入会把自己限得比上游还死：
    一段 70k prompt / 69k 命中的真实 agent 请求，对上游只占 ~1k，
    按全额记账却要吃掉整个分钟预算。
    """
    if not usage:
        return 0
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    details = usage.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens") or 0) if isinstance(details, dict) else 0
    return max(0, prompt - cached) + completion


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
                _trip_domain_breaker(url)
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
        native = await resolve_native(virtual, tier.provider_id, db)
        keys = await ordered_keys(tier, channel.strategy, db, affinity, model_name=native)
        # 模型池冷却：该 key 上本模型的积分池刚报 429，跳过（其他模型不受影响）
        for k in keys[:max_retries]:
            if account_is_rate_limited(k.provider_id, k.account_label):
                continue  # 账号级短路：同账号密钥共享上游 TPM，一个限流全限流
            if model_quota_is_limited(k.id, virtual):
                continue
            attempts.append((tier, k))
    # 全池耗尽兜底：active 尝试之后再追加最久未测的 exhausted 密钥。
    # 额度窗口可能已滚动重置，试一次的代价是快速 429，收益是少一次 503；
    # 仅在前面的尝试可重试失败后才会轮到，正常路径零影响。
    for tier in tiers:
        for k in await last_resort_keys(tier, db, max_retries):
            if account_is_rate_limited(k.provider_id, k.account_label):
                continue
            attempts.append((tier, k))
    # 全池模型池冷却兜底：小池子（如 deepseek 仅 2 把 key）在所有 key 的
    # (key,模型) 积分池同时进入 300 秒冷却时，上面产出空 attempts 会直接
    # 503。积分池按窗口滚动、恢复常快于冷却期，追加冷却中的组合做最后
    # 尝试，失败代价只是再吃一次快速 429，与 exhausted 兜底同理。
    if not attempts:
        for tier in tiers:
            native = await resolve_native(virtual, tier.provider_id, db)
            keys = await ordered_keys(tier, channel.strategy, db, affinity, model_name=native)
            for k in keys[:max_retries]:
                if model_quota_is_limited(k.id, virtual):
                    if account_is_rate_limited(k.provider_id, k.account_label):
                        continue  # 账号仍在 TPM 冷却：L3 仍守卫，留给 L4 赌
                    attempts.append((tier, k))
    # 最终兜底：所有层都在账号 TPM 冷却时（高并发客户端瞬时打满共享
    # TPM → 同账号 60 秒冷却，各级兜底全被守卫跳过），无视冷却标记取层内
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


def _cooldown_exemptions(
    attempts: list[tuple[ChannelTier, ApiKey]], virtual: str
) -> tuple[bool, bool]:
    """兜底组合的守卫豁免判定，返回 (allow_quota_limited, allow_account_limited)。

    attempts 全部带着模型池冷却标记时（兜底场景）不跳过，否则兜底白加；
    存在无冷却组合时仍跳过冷却中的，避免浪费重试次数。账号 TPM 冷却同理：
    最终兜底拿到的组合全部处于账号冷却时豁免守卫。
    """
    allow_quota_limited = bool(attempts) and all(
        model_quota_is_limited(k.id, virtual) for _, k in attempts
    )
    allow_account_limited = bool(attempts) and all(
        account_is_rate_limited(k.provider_id, k.account_label) for _, k in attempts
    )
    return allow_quota_limited, allow_account_limited


async def _revive_if_exhausted(key: ApiKey, db: AsyncSession) -> None:
    """最后尝试拿到 200 = 真实流量证明额度窗口已恢复，直接把密钥拉回 active。

    比探针复测（最长等 10 分钟巡检）更快回池；告警随 mark_key 自动 resolve。
    """
    if key.status == KeyStatus.exhausted:
        await mark_key(key, KeyStatus.active, db, message="真实请求成功，自动恢复")


# TPM 控速的输出 token 预估默认值。
#
# 关键：max_tokens 是**上限**，不是实际生成量。ZCode 会照常带
# max_tokens=128000，但一次工具调用实际只吐几百 token。按上限预扣会让
# 每条请求都预占 12.8 万——比整分钟预算还大，直接把每条请求推进
# "超大请求放行一次"分支，事前控速彻底失效（实测诊断日志
# estimate_calc: prompt_est=97298, output_est=128000, reserved=225298）。
#
# 上游 TPM 记的是真实产生的 output，所以预估该用"典型输出量"而不是上限。
# 实测 agent 工具调用步骤多在数百 token 量级，取 1024 作为保守估计；
# 真正生成更多时，结算会用实际 usage 补差。
_DEFAULT_OUTPUT_ESTIMATE = 1024
_MAX_OUTPUT_ESTIMATE_CEIL = 8192


def _estimate_parts(body: dict) -> tuple[int, int]:
    """粗估一次请求的 (prompt token, output token)，用于 TPM 事前控速。

    prompt: 约 3 字符/token（对中英文混合的保守估计），未乘校准系数；
    output: 取 max_tokens 与常见输出量的较小者——max_tokens 只是上限，
            实际生成通常远小于它，按上限预扣会严重超占预算。
    """
    prompt_chars = 0
    for m in body.get("messages") or []:
        if not isinstance(m, dict):
            continue
        content = m.get("content", "")
        if isinstance(content, str):
            prompt_chars += len(content)
        elif isinstance(content, list):
            for seg in content:
                if isinstance(seg, dict):
                    prompt_chars += len(str(seg.get("text", "")))
    prompt_est = max(1, prompt_chars // 3)
    max_output = body.get("max_tokens") or body.get("max_completion_tokens")
    if max_output:
        output_est = min(int(max_output), _MAX_OUTPUT_ESTIMATE_CEIL)
    else:
        output_est = _DEFAULT_OUTPUT_ESTIMATE
    return prompt_est, output_est


# 预估偏差校准：len/3 对中文是明显低估（一个汉字约 1 token，不是 1/3）。
# 按原生模型维护一个 actual/预估 的 EMA，下次预估乘上它，让 TPM 记账贴近真实。
# 钳制在 0.5–5.0，避免单次异常把系数带飞。
_EST_FACTOR: dict[str, float] = {}
_EST_FACTOR_MIN = 0.5
_EST_FACTOR_MAX = 5.0
_EST_FACTOR_ALPHA = 0.3

# 前缀缓存命中率估计：上游 TPM 只算未命中缓存的部分，而网关在**发请求前**
# 根本不知道这次会命中多少（同一段 prompt，前缀没变就几乎全命中）。
# 因此按模型维护一个 cached/prompt 的 EMA，事前预扣时按 (1 - 命中率) 折算。
#
# 不这么做会自锁：真实 ZCode 请求 70k prompt 里 69k 命中，实际只占 1.3k 配额，
# 但事前按 70k 预扣 → 两条就吃满整个分钟预算 → 后续请求被自己的预算挡住
# （实测压测里第 7 条起连续 429「本分钟 token 预算不足」）。
# 从 0 起学（保守先按全额预扣），命中后自然下降。
_CACHE_RATIO: dict[str, float] = {}
_CACHE_RATIO_MAX = 0.98
_CACHE_RATIO_ALPHA = 0.4


def _apply_est_factor(model: str, est: int) -> int:
    factor = _EST_FACTOR.get(model)
    if not factor:
        return est
    return max(1, int(est * factor))


def _reserve_estimate(model: str, prompt_est: int, output_est: int) -> int:
    """事前预扣量：按校准系数放大 prompt，再按(1-缓存命中率)折算，加输出。

    输出 token 不可能命中缓存，必须全额计入。
    """
    scaled_prompt = _apply_est_factor(model, prompt_est)
    ratio = _CACHE_RATIO.get(model, 0.0)
    billable_prompt = int(scaled_prompt * (1.0 - ratio))
    return max(1, billable_prompt + output_est)


def _update_est_factor(model: str, base_est: int, actual: int) -> None:
    """用实际 usage 修正该模型的预估系数（base_est 为未乘系数的原值）。"""
    if base_est <= 0 or actual <= 0:
        return
    ratio = min(_EST_FACTOR_MAX, max(_EST_FACTOR_MIN, actual / base_est))
    prev = _EST_FACTOR.get(model)
    _EST_FACTOR[model] = (
        ratio if prev is None else prev * (1 - _EST_FACTOR_ALPHA) + ratio * _EST_FACTOR_ALPHA
    )


def _update_cache_ratio(model: str, usage: dict | None) -> None:
    """用实际 usage 更新该模型的前缀缓存命中率估计。

    命中率 = cached_tokens / prompt_tokens。冷请求会把它往下拉、热请求往上带，
    EMA 收敛到该会话的真实命中水平；预扣时据此折算，避免按全额预扣把自己锁死。
    """
    if not usage:
        return
    prompt = int(usage.get("prompt_tokens") or 0)
    if prompt <= 0:
        return
    details = usage.get("prompt_tokens_details") or {}
    if not isinstance(details, dict):
        return
    cached = int(details.get("cached_tokens") or 0)
    if cached < 0:
        return
    ratio = min(_CACHE_RATIO_MAX, max(0.0, cached / prompt))
    prev = _CACHE_RATIO.get(model)
    _CACHE_RATIO[model] = (
        ratio if prev is None else prev * (1 - _CACHE_RATIO_ALPHA) + ratio * _CACHE_RATIO_ALPHA
    )


def _refund_budget(key: ApiKey, native: str, reserved: int) -> None:
    """请求没发出去或失败：把 RPM 令牌和 TPM 预留都退回。

    失败请求不应该占用限速预算——否则连续失败会把桶抽干，
    让后续正常请求也被迫排队。
    """
    release_rpm(key.id, native)
    if reserved > 0:
        tpm_release(key.provider_id, key.account_label, native, reserved)


async def route_chat_completions(
    channel: Channel, body: dict, db: AsyncSession
) -> JSONResponse | StreamingResponse:
    virtual = channel.virtual_model
    stream = bool(body.get("stream"))
    # TPM 事前控速：prompt 与 output 分开估。prompt 会按缓存命中率折算，
    # output 必须全额（不可能命中缓存）。
    prompt_est, output_est = _estimate_parts(body)
    # 缓存亲和：由虚拟模型 + 消息稳定前缀派生路由哈希，同一会话固定同一 key
    affinity = routing_affinity(virtual, body)

    attempts = await _build_attempts(channel, db, affinity)
    if not attempts:
        log.warning("no_available_key", channel_id=channel.id, virtual_model=virtual)
        return _no_key_response()

    last_error = "无可用上游"
    allow_quota_limited, allow_account_limited = _cooldown_exemptions(attempts, virtual)

    # ── TPM 预算准入 ────────────────────────────────────────────────
    # 先看有没有哪把候选 key 当前预算放得下本请求。全放不下时：
    # 等得不久（≤ MTT_TOKEN_BUCKET_TPM_MAX_WAIT）就短暂等待再进主循环，
    # 等太久则直接 429 + 精确 Retry-After——不把注定 429 的请求发出去。
    min_wait: float | None = None
    for tier, key in attempts:
        if account_is_rate_limited(key.provider_id, key.account_label) and not allow_account_limited:
            continue
        if model_quota_is_limited(key.id, virtual) and not allow_quota_limited:
            continue
        native_check = await resolve_native(virtual, tier.provider_id, db)
        wait = tpm_wait(
            key.provider_id, key.account_label, native_check,
            _reserve_estimate(native_check, prompt_est, output_est),
        )
        if wait <= 0.0:
            min_wait = 0.0
            break
        min_wait = wait if min_wait is None else min(min_wait, wait)

    if min_wait and min_wait > 0.0:
        if min_wait > max_wait_seconds():
            secs = ceil_div_seconds(min_wait)
            log.info("tpm_budget_exhausted_429", channel_id=channel.id,
                     virtual_model=virtual, wait_s=round(min_wait, 1))
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(secs)},
                content={"error": {
                    "message": f"本分钟 token 预算不足，请 {secs} 秒后重试",
                    "type": "rate_limited",
                }},
            )
        log.info("tpm_wait", channel_id=channel.id, virtual_model=virtual,
                 wait_s=round(min_wait, 2))
        await asyncio.sleep(min_wait + 0.05)  # 多等 50ms 让窗口真正滚过去

    # 全部账号在限流冷却中。
    #
    # 剩余冷却很短时（≤ MTT_RELAY_SHORT_COOLDOWN_WAIT）**在网关内部等掉**再发，
    # 而不是甩 429 让客户端重连。这是消除 ZCode「重新连接中 N/10」的关键：
    # 客户端拿到 429 会先做自己的重试退避（通常几秒到十几秒）+ 重建连接，
    # 比我们原地睡 20 秒慢得多，还会让用户看到连续的"重连"提示。
    # 实测上游 429 后 20–32 秒就恢复，等待的成功率远高于让客户端重试。
    #
    # 冷却还很久（退避已升到 60/90 秒）时不等待：占着连接 90 秒不如直接
    # 返回 429 + 真实 Retry-After，让客户端明确知道该等多久。
    if attempts and all(
        account_rate_limited_remaining(k.provider_id, k.account_label) > _FRESH_COOLDOWN_SEC
        for _, k in attempts
    ):
        remaining = max(
            account_rate_limited_remaining(k.provider_id, k.account_label)
            for _, k in attempts
        )
        if remaining <= _SHORT_COOLDOWN_WAIT:
            log.info("cooldown_wait_inline", channel_id=channel.id,
                     virtual_model=virtual, wait_s=round(remaining, 1))
            await asyncio.sleep(remaining + 0.1)
        else:
            log.info("all_fresh_cooldown_429", channel_id=channel.id,
                     virtual_model=virtual, retry_after_s=int(remaining))
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(ceil_div_seconds(remaining))},
                content={"error": {"message": "所有上游账号处于限流冷却期", "type": "rate_limited"}},
            )

    budget_blocked_wait: float | None = None
    network_failed = False  # 本次请求内是否发生过网络层失败（域名熔断依据）

    # ── 短冷却优先：等首选 key，别切到冷的 key ──────────────────────
    # 大 prompt 的连续性会话里，首选 key 的缓存前缀命中率最高（实测 66k prompt
    # 命中 65.8k，实际只占 232 token），换 key 要重算整段前缀。首选 key 只是
    # 短暂冷却（≤ MTT_RELAY_SHORT_COOLDOWN_WAIT）时，等它比换一把划算得多。
    # attempts 已按亲和顺序排列，attempts[0] 就是该会话的首选 key。
    if attempts:
        _pref_tier, _pref_key = attempts[0]
        _pref_wait = account_rate_limited_remaining(_pref_key.provider_id, _pref_key.account_label)
        if 0 < _pref_wait <= _SHORT_COOLDOWN_WAIT and not allow_account_limited:
            log.info("affinity_wait_for_key", channel_id=channel.id,
                     key_id=_pref_key.id, wait_s=round(_pref_wait, 1))
            await asyncio.sleep(_pref_wait + 0.1)

    # 429 的扇出次数上限：一次用户请求最多向上游发几次。
    # 每次失败重试都会消耗另一把 key 的 RPM 配额。实测每把 key 每分钟只放行
    # 4–5 次，而一次请求失败会连打 3 把 key——失败本身就在制造新的失败
    # （越 429 越紧张）。限成 2 次：留一次换 key 的机会，又不会把一场
    # 429 放大成三场。
    _MAX_UPSTREAM_ATTEMPTS = 2
    _upstream_calls = 0

    for tier, key in attempts:
        if account_is_rate_limited(key.provider_id, key.account_label) and not allow_account_limited:
            continue  # 同池短路：该层已在本次请求内被标记 TPM 冷却
        if model_quota_is_limited(key.id, virtual) and not allow_quota_limited:
            continue  # 模型池冷却：该 key 上本模型的积分池刚报 429
        native = await resolve_native(virtual, tier.provider_id, db)
        # 令牌桶事前控速：RPM（按 key）+ TPM 预算（按账号×模型）。
        # 预算不够只跳过这把 key；全部跳过时在循环外统一返回 429。
        if not acquire_rpm(key.id, native):
            continue  # RPM 桶空：换下一把，不占预算
        reserved = _reserve_estimate(native, prompt_est, output_est)
        if _upstream_calls == 0:
            # 诊断：每次请求只打一条，用来核对预扣是否真的吃到了缓存率折算。
            # ZCode 场景 prompt 动辄 20 万 token 而命中率 99%，预扣若不折算会
            # 直接高于整分钟预算，把每条请求都推到"超大请求放行一次"分支。
            log.info("estimate_calc", channel_id=channel.id, native_model=native,
                     prompt_est=prompt_est, output_est=output_est,
                     est_factor=round(_EST_FACTOR.get(native, 0.0), 3),
                     cache_ratio=round(_CACHE_RATIO.get(native, 0.0), 3),
                     reserved=reserved)
        allowed, wait = tpm_reserve(
            key.provider_id, key.account_label, native, reserved,
        )
        if not allowed:
            release_rpm(key.id, native)  # 预算不足，RPM 令牌退回
            budget_blocked_wait = (
                wait if budget_blocked_wait is None else min(budget_blocked_wait, wait)
            )
            continue
        base = base_url_for(tier.provider)
        if not base:
            last_error = f"供应商 {tier.provider.slug} 缺少 base_url"
            _refund_budget(key, native, reserved)
            continue
        url = _join(base, "chat/completions")
        # 域名熔断：同一域名刚发生过网络层失败，且本次请求已经因为网络问题
        # 失败过 → 换 key 没有意义（同池 key 指向同一域名），立刻整体失败。
        if network_failed and _domain_is_cooling(url):
            _refund_budget(key, native, reserved)
            last_error = "上游域名网络不可达（已熔断）"
            log.warning("network_breaker_skip", channel_id=channel.id,
                        host=_domain_of(url), key_id=key.id)
            break
        try:
            raw = decrypt_value(key.key_value)
        except Exception:
            await mark_key(key, KeyStatus.invalid, db, message="密文解密失败")
            _refund_budget(key, native, reserved)
            continue

        payload = _build_payload(body, native, stream, base_url=base)
        # 扇出上限：本请求此前的尝试已经耗掉别的 key 的配额，再换 key 只会
        # 制造更多 429。到上限就带着已知的 last_error 收尾。
        if _upstream_calls >= _MAX_UPSTREAM_ATTEMPTS:
            _refund_budget(key, native, reserved)
            log.info("upstream_attempt_cap_reached", channel_id=channel.id,
                     virtual_model=virtual, attempts=_upstream_calls,
                     last_error=str(last_error)[:120])
            break
        _upstream_calls += 1
        t0 = time.time()

        if stream:
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(600.0, connect=_NETWORK_CONNECT_TIMEOUT)
            )
            same_key_retry = 0  # 上游网关 read body failed 原地重试计数
            should_continue = False  # 需要换下一个 key 时置 True
            while True:
                try:
                    response = await _send_with_retry(
                        client, url, headers=_headers(raw), json=payload, stream=True
                    )
                except Exception as e:
                    await client.aclose()
                    last_error = f"{type(e).__name__}: {e}"
                    network_failed = True  # 标记：同域名换 key 无意义
                    should_continue = True  # 网络异常不原地重试，走外层换 key
                    _refund_budget(key, native, reserved)
                    break
                if response.status_code != 200:
                    await response.aread()
                    err_text = response.text
                    await response.aclose()
                    # 上游网关读 body 失败（B.AI 对 >1MB 请求体偶发）：
                    # 同一上游换 key 无意义，原地重试一次，成功率高
                    if _is_gateway_read_body_error(err_text) and same_key_retry < 1:
                        same_key_retry += 1
                        log.warning(
                            "gateway_read_body_retry",
                            channel_id=channel.id, key_id=key.id, provider_id=tier.provider_id,
                            virtual_model=virtual, native_model=native,
                            err_text=err_text[:200],
                        )
                        await asyncio.sleep(3)
                        continue
                    await client.aclose()
                    retry, mark, msg = classify_error(response.status_code, err_text)
                    log.info("upstream_error_classified",
                             channel_id=channel.id, key_id=key.id,
                             provider_id=tier.provider_id,
                             virtual_model=virtual, native_model=native,
                             http_status=response.status_code,
                             error_code=extract_error_code(err_text),
                             mark=mark.value if mark else None, retry=retry)
                    _is_model_quota = is_model_pool_quota(err_text) or "entitlement" in (err_text or "").lower()
                    if response.status_code == 429:
                        # 429 说明不是"没发出去"，但也没消耗 token：退回预留，
                        # 同时让自适应预算按 AIMD 下调。
                        tpm_on_rate_limit(key.provider_id, key.account_label, native, reserved)
                        _refund_budget(key, native, reserved)
                    else:
                        _refund_budget(key, native, reserved)
                    if mark == KeyStatus.rate_limited:
                        mark_account_rate_limited(key.provider_id, key.account_label)  # 账号短路：同账号 TPM 冷却
                        await mark_key_model_status(db, key_id=key.id, provider_id=tier.provider_id, model_name=native, status=mark, message=msg)
                    elif mark == KeyStatus.exhausted and _is_model_quota:
                        # 模型池级额度耗尽（含 SenseNova token plan entitlement）：只标记 (key, 模型)，不把整把 key 标死
                        mark_model_quota_limited(key.id, virtual)
                        await mark_key_model_status(db, key_id=key.id, provider_id=tier.provider_id, model_name=native, status=mark, message=msg)
                    elif mark == KeyStatus.invalid and "no access to model" in (err_text or "").lower():
                        # 模型访问权限不足：密钥本身有效，只标记模型级 invalid
                        await mark_key_model_status(db, key_id=key.id, provider_id=tier.provider_id, model_name=native, status=mark, message=msg)
                    else:
                        await mark_key(key, mark, db, message=msg)
                    last_error = msg
                    if retry:
                        should_continue = True  # 换下一个 key（外层 for 继续）
                        break
                    # 400 诊断：记录被拒的 payload keys，定位不兼容参数
                    if response.status_code == 400:
                        log.warning("upstream_400_invalid",
                                    channel_id=channel.id, key_id=key.id,
                                    provider_id=tier.provider_id, slug=tier.provider.slug,
                                    virtual_model=virtual, native_model=native,
                                    payload_keys=list(payload.keys()),
                                    err_text=err_text[:300])
                    await write_call_log(
                        channel_id=channel.id,
                        tier_order=tier.order,
                        provider_id=tier.provider_id,
                        key_id=key.id,
                        virtual_model=virtual,
                        native_model=native,
                        usage=None,
                        latency_ms=int((time.time() - t0) * 1000),
                        status="error",
                        error=msg,
                        stream=True,
                    )
                    return JSONResponse(
                        status_code=response.status_code,
                        content={"error": {"message": msg, "type": "upstream_error"}},
                    )
                # 200：流已打开，退出 while
                break
            if should_continue:
                continue  # 网络异常或可重试错误，换下一个 key
            # 200：流已打开，故障转移窗口关闭
            await _revive_if_exhausted(key, db)
            reset_account_backoff(key.provider_id, key.account_label)
            meta = {
                "channel_id": channel.id,
                "tier_order": tier.order,
                "provider_id": tier.provider_id,
                "key_id": key.id,
                "virtual_model": virtual,
                "native_model": native,
                "t0": t0,
                "account_label": key.account_label,
                "est_tokens": reserved,       # 事前预扣量（已按缓存率折算），结算时原样替换
                "est_base": prompt_est,       # 未乘系数的 prompt 预估，用于校准 len/3 偏差
            }
            return StreamingResponse(
                _stream_passthrough(client, response, meta), media_type="text/event-stream"
            )
        else:
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(600.0, connect=_NETWORK_CONNECT_TIMEOUT)
                ) as client:
                    resp = await _send_with_retry(client, url, headers=_headers(raw), json=payload)
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                network_failed = True  # 标记：同域名换 key 无意义
                _refund_budget(key, native, reserved)
                continue
            latency_ms = int((time.time() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                await _revive_if_exhausted(key, db)
                reset_account_backoff(key.provider_id, key.account_label)
                # TPM 结算：把窗口里的预估值替换成实际占用额（只算未命中缓存
                # 的部分）。校准用 prompt 全额对 prompt 预估，缓存率单独学——
                # 两者混在一起会让「命中率高」被误当成「预估高估」。
                _usage = data.get("usage")
                if _usage:
                    tpm_settle(key.provider_id, key.account_label, native,
                               reserved, billable_tokens(_usage))
                    _update_est_factor(native, prompt_est, int(_usage.get("prompt_tokens") or 0))
                    _update_cache_ratio(native, _usage)
                # 无 usage 时保留预留值（请求确实消耗了 token，按估算记账比退回更保守）
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
            log.info("upstream_error_classified",
                     channel_id=channel.id, key_id=key.id,
                     provider_id=tier.provider_id,
                     virtual_model=virtual, native_model=native,
                     http_status=resp.status_code,
                     error_code=extract_error_code(resp.text),
                     mark=mark.value if mark else None, retry=retry)
            _is_model_quota = is_model_pool_quota(resp.text) or "entitlement" in (resp.text or "").lower()
            if resp.status_code == 429:
                tpm_on_rate_limit(key.provider_id, key.account_label, native, reserved)
            _refund_budget(key, native, reserved)
            if mark == KeyStatus.rate_limited:
                mark_account_rate_limited(key.provider_id, key.account_label)  # 账号短路：同账号 TPM 冷却
                await mark_key_model_status(db, key_id=key.id, provider_id=tier.provider_id, model_name=native, status=mark, message=msg)
            elif mark == KeyStatus.exhausted and _is_model_quota:
                # 模型池级额度耗尽（含 SenseNova token plan entitlement）：只标记 (key, 模型)，不把整把 key 标死
                mark_model_quota_limited(key.id, virtual)
                await mark_key_model_status(db, key_id=key.id, provider_id=tier.provider_id, model_name=native, status=mark, message=msg)
            elif mark == KeyStatus.invalid and "no access to model" in (resp.text or "").lower():
                # 模型访问权限不足：密钥本身有效，只标记模型级 invalid
                await mark_key_model_status(db, key_id=key.id, provider_id=tier.provider_id, model_name=native, status=mark, message=msg)
            else:
                await mark_key(key, mark, db, message=msg)
            # 400 诊断：记录被拒的 payload keys，定位不兼容参数
            if resp.status_code == 400:
                log.warning("upstream_400_invalid",
                            channel_id=channel.id, key_id=key.id,
                            provider_id=tier.provider_id, slug=tier.provider.slug,
                            virtual_model=virtual, native_model=native,
                            payload_keys=list(payload.keys()),
                            err_text=(resp.text or "")[:300])
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

    # 网络层失败（ConnectTimeout / ConnectError / TLS 中断）：换 key 解决不了
    # 同一域名连不上的问题。返回 503 + Retry-After，让客户端快速重连，
    # 而不是给 502 让它以为网关挂了立刻猛重试。
    if network_failed:
        log.warning("all_upstreams_network_failed", channel_id=channel.id,
                    virtual_model=virtual, error=last_error)
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": str(int(_NETWORK_BREAKER_SEC))},
            content={"error": {
                "message": f"上游网络不可达（{last_error}），请稍后重试",
                "type": "upstream_unreachable",
            }},
        )

    # 全部尝试失败。若失败原因是限流且所有账号现在都在冷却中，返回 429 而非 502：
    # 502 会让客户端当成"网关故障"立刻猛重试，429 + 真实 Retry-After 才是正确信号。
    # （同一请求内 6 个账号被逐个打冷后尚未重新进入 attempt 循环，短路检查来不及生效。）
    cooldown = max(
        (account_rate_limited_remaining(k.provider_id, k.account_label) for _, k in attempts),
        default=0.0,
    )
    if cooldown > 0 and last_error.startswith("HTTP 429"):
        secs = ceil_div_seconds(cooldown)
        log.info("all_failed_accounts_cooling_429", channel_id=channel.id,
                 virtual_model=virtual, retry_after_s=secs)
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(secs)},
            content={"error": {
                "message": f"上游账号均已限流，请 {secs} 秒后重试",
                "type": "rate_limited",
            }},
        )

    if budget_blocked_wait is not None:
        # 有候选 key 但全部被 TPM 预算挡住（走到这里说明等待也没等到）。
        secs = ceil_div_seconds(budget_blocked_wait)
        log.info("tpm_budget_blocked_429", channel_id=channel.id,
                 virtual_model=virtual, wait_s=round(budget_blocked_wait, 1))
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(secs)},
            content={"error": {
                "message": f"本分钟 token 预算不足，请 {secs} 秒后重试",
                "type": "rate_limited",
            }},
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
        # TPM 结算：流正常结束且有 usage 时按实际值覆盖预留并校准预估；
        # 流中途断了则退回预留（没真正消耗完，不该占用预算）。
        _reserved = meta.get("est_tokens", 0)
        _native = meta["native_model"]
        _pid = meta["provider_id"]
        _acct = meta.get("account_label")
        if usage:
            actual = billable_tokens(usage)
            # 诊断：确认流式响应里的 usage 结构，以及缓存字段到底落在哪。
            log.info("stream_usage_seen", native_model=_native,
                     usage_keys=sorted(usage.keys()),
                     prompt_tokens=int(usage.get("prompt_tokens") or 0),
                     cached_in_details=int(
                         (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
                     ) if isinstance(usage.get("prompt_tokens_details"), dict) else None,
                     prompt_cache_hit_tokens=int(usage.get("prompt_cache_hit_tokens") or 0),
                     billable=actual, reserved=_reserved)
            tpm_settle(_pid, _acct, _native, _reserved, actual)
            _update_est_factor(_native, int(meta.get("est_base", 0)),
                               int(usage.get("prompt_tokens") or 0))
            _update_cache_ratio(_native, usage)
        elif status == "error":
            tpm_release(_pid, _acct, _native, _reserved)
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
    allow_quota_limited, allow_account_limited = _cooldown_exemptions(attempts, virtual)
    for tier, key in attempts:
        if account_is_rate_limited(key.provider_id, key.account_label) and not allow_account_limited:
            continue  # 账号短路：该账号已被标记 TPM 冷却
        if model_quota_is_limited(key.id, virtual) and not allow_quota_limited:
            continue  # 模型池冷却：该 key 上本模型的积分池刚报 429
        native = await resolve_native(virtual, tier.provider_id, db)
        # 令牌桶事前控速：该 key+模型无令牌时跳过，换下一把 key（多密钥池自动负载）
        if not acquire_rpm(key.id, native):
            continue
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
        _is_model_quota = is_model_pool_quota(resp.text) or "entitlement" in (resp.text or "").lower()
        if mark == KeyStatus.rate_limited:
            mark_account_rate_limited(key.provider_id, key.account_label)  # 账号短路：同账号 TPM 冷却
            await mark_key_model_status(db, key_id=key.id, provider_id=tier.provider_id, model_name=native, status=mark, message=msg)
        elif mark == KeyStatus.exhausted and _is_model_quota:
            # 模型池级额度耗尽（含 SenseNova token plan entitlement）：只标记 (key, 模型)，不把整把 key 标死
            mark_model_quota_limited(key.id, virtual)
            await mark_key_model_status(db, key_id=key.id, provider_id=tier.provider_id, model_name=native, status=mark, message=msg)
        elif mark == KeyStatus.invalid and "no access to model" in (resp.text or "").lower():
            # 模型访问权限不足：密钥本身有效，只标记模型级 invalid
            await mark_key_model_status(db, key_id=key.id, provider_id=tier.provider_id, model_name=native, status=mark, message=msg)
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


def _build_image_payload(body: dict, native_model: str) -> dict:
    """构造 images/generations 请求体。

    agent 经 chat/completions 发来的 body 只有 messages 没有 prompt，
    这里提取最后一条 user 消息的文本作为 prompt，并剔除 chat 专用字段。
    """
    payload = dict(body)
    payload["model"] = native_model
    if not payload.get("prompt"):
        prompt = ""
        for m in payload.get("messages") or []:
            if not isinstance(m, dict):
                continue
            content = m.get("content")
            if isinstance(content, str):
                prompt = content  # 取最后一条文本消息
            elif isinstance(content, list):
                parts = []
                for seg in content:
                    if isinstance(seg, dict) and isinstance(seg.get("text"), str):
                        parts.append(seg["text"])
                    elif isinstance(seg, dict) and isinstance(seg.get("type"), str) and seg.get("type") == "text":
                        parts.append(str(seg.get("text", "")))
                if parts:
                    prompt = " ".join(parts)
        if not prompt:
            raise ValueError("无法从 messages 提取生图 prompt")
        payload["prompt"] = prompt
    for key in ("messages", "stream", "max_tokens", "temperature", "top_p", "reasoning_effort", "thinking"):
        payload.pop(key, None)
    if "n" not in payload:
        payload["n"] = 1
    return payload


def _wrap_image_as_chat(data: dict, virtual_model: str) -> dict:
    """把 /v1/images/generations 的响应包装成 OpenAI chat.completion 格式。

    agent 客户端只认 chat/completions，把生图结果以 markdown 图片形式
    放进 message.content，客户端即可直接展示图片。
    """
    urls = []
    for item in data.get("data") or []:
        if item.get("url"):
            urls.append(item["url"])
        elif item.get("b64_json"):
            urls.append(f"data:image/png;base64,{item['b64_json']}")
    text = "\n".join(f"![image]({u})" for u in urls) if urls else "(图片生成失败，无返回数据)"
    return {
        "id": f"chatcmpl-img-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": virtual_model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


async def route_image_generations(
    channel: Channel, body: dict, db: AsyncSession, wrap_as_chat: bool = False
) -> JSONResponse:
    """/v1/images/generations：复用同一套 Tier fallback + 池内负载均衡 + key 状态机。

    与 chat/embeddings 同构：按 (tier, key) 尝试序列逐个发上游，
    命中可恢复错误就标状态并换下一个，成功/失败都写 CallLog。
    图片生成不支持流式，超时给得更长（120s）。
    """
    virtual = channel.virtual_model
    affinity = routing_affinity(virtual, body)

    attempts = await _build_attempts(channel, db, affinity)
    if not attempts:
        log.warning("no_available_key", channel_id=channel.id, virtual_model=virtual)
        return _no_key_response()

    last_error = "无可用上游"
    allow_quota_limited, allow_account_limited = _cooldown_exemptions(attempts, virtual)
    for tier, key in attempts:
        if account_is_rate_limited(key.provider_id, key.account_label) and not allow_account_limited:
            continue
        if model_quota_is_limited(key.id, virtual) and not allow_quota_limited:
            continue
        native = await resolve_native(virtual, tier.provider_id, db)
        # 令牌桶事前控速：该 key+模型无令牌时跳过，换下一把 key
        if not acquire_rpm(key.id, native):
            continue
        base = base_url_for(tier.provider)
        if not base:
            last_error = f"供应商 {tier.provider.slug} 缺少 base_url"
            continue
        url = _join(base, "images/generations")
        try:
            raw = decrypt_value(key.key_value)
        except Exception:
            await mark_key(key, KeyStatus.invalid, db, message="密文解密失败")
            continue

        try:
            payload = _build_image_payload(body, native)
        except ValueError as e:
            last_error = str(e)
            continue
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
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
                usage=None,
                latency_ms=latency_ms,
                status="success",
                error=None,
                stream=False,
            )
            if wrap_as_chat:
                return JSONResponse(status_code=200, content=_wrap_image_as_chat(data, virtual))
            return JSONResponse(status_code=200, content=data)
        retry, mark, msg = classify_error(resp.status_code, resp.text)
        _is_model_quota = is_model_pool_quota(resp.text) or "entitlement" in (resp.text or "").lower()
        if mark == KeyStatus.rate_limited:
            mark_account_rate_limited(key.provider_id, key.account_label)
            await mark_key_model_status(db, key_id=key.id, provider_id=tier.provider_id, model_name=native, status=mark, message=msg)
        elif mark == KeyStatus.exhausted and _is_model_quota:
            mark_model_quota_limited(key.id, virtual)
            await mark_key_model_status(db, key_id=key.id, provider_id=tier.provider_id, model_name=native, status=mark, message=msg)
        elif mark == KeyStatus.invalid and "no access to model" in (resp.text or "").lower():
            await mark_key_model_status(db, key_id=key.id, provider_id=tier.provider_id, model_name=native, status=mark, message=msg)
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
