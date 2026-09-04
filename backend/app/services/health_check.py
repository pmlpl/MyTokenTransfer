from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_value
from app.database import AsyncSessionLocal
from app.models import ApiKey, KeyStatus, Provider
from app.models.enums import ProbeKind
from app.relay.errors import classify_error

# 各供应商默认基址（provider.base_url 为空时兜底）
_DEFAULT_BASES = {
    "openai": "https://api.openai.com",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
}

# 限流冷却时长（秒）
_RATE_LIMIT_COOLDOWN = 60

# 非 active 状态的探测成功保护期（分钟）。
# 1-token 探测太轻量，刚被标记为 degraded 的 key 可能小请求能过但大请求仍限流，
# 保护期内探测成功不恢复 active，避免状态反复横跳。
_DEGRADED_PROBE_GRACE_MINUTES = 5

# 网络层瞬时故障重试（容器 NAT 到部分上游端点偶发 TLS 握手超时/连接重置）
_PROBE_ATTEMPTS = 3
_RETRY_BACKOFF = 1.0  # 秒

_VERSION_TAIL = re.compile(r"/v\d+$")


@dataclass
class ProbeResult:
    ok: bool
    http_status: int | None
    balance: str | None
    error: str | None
    via: str = "models"  # models=列模型接口 / chat=最小对话兜底
    body: str | None = None  # 上游响应正文片段（供 classify_error 判 quota 关键词）


def _join(base: str, path: str) -> str:
    """拼接路径。base 已含版本段（/v1、/v2、/compatible-mode/v2 等）时直接拼，否则补 /v1。"""
    b = base.rstrip("/")
    if _VERSION_TAIL.search(b) or "/compatible-mode/" in b:
        return f"{b}/{path}"
    return f"{b}/v1/{path}"


def _first_model(provider: Provider) -> str | None:
    for m in provider.models or []:
        if isinstance(m, str) and m.strip():
            return m.strip()
    return None


async def _request_with_retry(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict | None = None,
    timeout: float = 15.0,
) -> httpx.Response:
    """带重试的 HTTP 请求。仅网络层故障（连接超时/握手失败/连接重置）重试；
    HTTP 状态码一律不重试，由调用方分类。"""
    last_exc: Exception | None = None
    for attempt in range(_PROBE_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                return await client.request(method, url, headers=headers, json=json_body)
        except Exception as e:
            last_exc = e
            if attempt < _PROBE_ATTEMPTS - 1:
                await asyncio.sleep(_RETRY_BACKOFF)
    raise last_exc  # type: ignore[misc]


async def _probe_models(base: str, headers: dict[str, str]) -> ProbeResult | None:
    """探测列模型接口。返回 None 表示端点不可用，应交给对话兜底。"""
    url = _join(base, "models")
    try:
        resp = await _request_with_retry("GET", url, headers=headers)
    except Exception as e:  # 重试后仍失败：网络层故障，不改变 key 状态
        return ProbeResult(False, None, None, f"{type(e).__name__}: {e}")

    if resp.status_code == 200:
        return ProbeResult(True, 200, None, None)
    if resp.status_code in (404, 400, 405, 501):
        return None  # 供应商未开放列模型接口（如 SenseNova 兼容模式）
    snippet = resp.text[:200] if resp.text else ""
    return ProbeResult(
        False, resp.status_code, None, f"HTTP {resp.status_code}: {snippet}", body=snippet
    )


async def _probe_chat(base: str, headers: dict[str, str], model: str) -> ProbeResult:
    """用最小对话请求兜底探测（1 token 成本）。"""
    url = _join(base, "chat/completions")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "stream": False,
    }
    try:
        resp = await _request_with_retry(
            "POST",
            url,
            headers={**headers, "Content-Type": "application/json"},
            json_body=payload,
            timeout=20.0,
        )
    except Exception as e:
        return ProbeResult(False, None, None, f"{type(e).__name__}: {e}", via="chat")

    if resp.status_code == 200:
        return ProbeResult(True, 200, None, None, via="chat")
    snippet = resp.text[:200] if resp.text else ""
    return ProbeResult(
        False, resp.status_code, None, f"HTTP {resp.status_code}: {snippet}", via="chat", body=snippet
    )


async def _probe_anthropic_chat(base: str, headers: dict[str, str], model: str) -> ProbeResult:
    """用 Anthropic Messages API 做最小对话兜底探测（1 token 成本）。"""
    url = _join(base, "messages")
    payload = {
        "model": model,
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }
    try:
        resp = await _request_with_retry(
            "POST",
            url,
            headers={**headers, "Content-Type": "application/json"},
            json_body=payload,
            timeout=20.0,
        )
    except Exception as e:
        return ProbeResult(False, None, None, f"{type(e).__name__}: {e}", via="chat")

    if resp.status_code == 200:
        return ProbeResult(True, 200, None, None, via="chat")
    snippet = resp.text[:200] if resp.text else ""
    return ProbeResult(
        False, resp.status_code, None, f"HTTP {resp.status_code}: {snippet}", via="chat", body=snippet
    )


async def _probe(provider: Provider, raw_key: str) -> ProbeResult:
    base = provider.base_url or _DEFAULT_BASES.get(provider.slug, "")
    if not base:
        return ProbeResult(False, None, None, "缺少 base_url 且无默认值")

    if provider.probe_kind == ProbeKind.openai_models:
        headers = {"Authorization": f"Bearer {raw_key}"}
        result = await _probe_models(base, headers)
        if result is not None:
            return result
        model = _first_model(provider)
        if not model:
            return ProbeResult(
                False, None, None, "该供应商无列模型接口，请在供应商配置中填写模型列表以启用对话兜底探测"
            )
        return await _probe_chat(base, headers, model)

    if provider.probe_kind == ProbeKind.anthropic:
        headers = {
            "x-api-key": raw_key,
            "anthropic-version": "2023-06-01",
        }
        result = await _probe_models(base, headers)
        if result is not None:
            return result
        # 列模型接口不可用（Anthropic 官方无 /models），fallback 到最小对话
        model = _first_model(provider)
        if not model:
            return ProbeResult(
                False, None, None, "Anthropic 列模型接口不可用，请在供应商配置中填写模型列表以启用对话兜底探测"
            )
        return await _probe_anthropic_chat(base, headers, model)

    if provider.probe_kind == ProbeKind.gemini_models:
        url = f"{base.rstrip('/')}/v1beta/models?key={raw_key}"
        try:
            resp = await _request_with_retry("GET", url)
        except Exception as e:
            return ProbeResult(False, None, None, f"{type(e).__name__}: {e}")
        if resp.status_code == 200:
            return ProbeResult(True, 200, None, None)
        snippet = resp.text[:200] if resp.text else ""
        return ProbeResult(
            False, resp.status_code, None, f"HTTP {resp.status_code}: {snippet}"
        )

    return ProbeResult(False, None, None, "不支持的探测方式")


def _classify(
    result: ProbeResult,
    current: KeyStatus,
    rate_limited_until: datetime | None = None,
    last_tested_at: datetime | None = None,
) -> tuple[KeyStatus, str | None, datetime | None]:
    """根据探针结果决定新状态/错误/冷却到期。

    复用 relay.errors.classify_error，与健康路径保持一致：
    429 带 quota 关键词 → exhausted（额度用尽，靠巡检恢复，不设冷却）；
    纯 429 → rate_limited + 短冷却；401/403 → invalid。

    探测成功保护：1-token 探测太轻量，刚被标记为 degraded 的 key 可能小请求
    能过但大请求仍限流。rate_limited 冷却未到期、或 exhausted 在保护期内时，
    探测成功不恢复 active，避免状态反复横跳。
    """
    # 手动停用的 key 不被探测自动恢复
    if current == KeyStatus.disabled:
        return current, result.error, None
    now = _now()
    if result.ok:
        # rate_limited 冷却未到期：1-token 成功不代表限流解除，保留冷却
        if current == KeyStatus.rate_limited and rate_limited_until and rate_limited_until > now:
            return current, None, rate_limited_until
        # exhausted 保护期内：刚被标记为额度用尽，1-token 成功可能是请求太小
        if current == KeyStatus.exhausted and last_tested_at:
            if (now - last_tested_at) < timedelta(minutes=_DEGRADED_PROBE_GRACE_MINUTES):
                return current, None, None
        return KeyStatus.active, None, None
    if result.http_status is None:
        # 网络层/配置错误：瞬时，保留原状态，记录原始错误
        return current, result.error, None

    _retry, status, msg = classify_error(result.http_status, result.body or "")
    if status == KeyStatus.rate_limited:
        return status, msg, now + timedelta(seconds=_RATE_LIMIT_COOLDOWN)
    if status is not None:
        return status, msg, None
    # 5xx/未知：保留原状态，仅记录错误
    return current, msg, None


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def test_key(db: AsyncSession, key: ApiKey) -> dict:
    """探测单条密钥并更新状态。"""
    from app.services.alerts import emit_key_alert, resolve_key_alerts

    before = key.status
    if key.status == KeyStatus.disabled:
        return {"id": key.id, "status": key.status.value, "ok": False, "error": "密钥已手动停用，跳过探测"}
    try:
        raw = decrypt_value(key.key_value)
    except Exception:
        key.last_tested_at = _now()
        key.status = KeyStatus.invalid
        key.last_error = "密文解密失败"
        await emit_key_alert(
            db,
            key_id=key.id,
            provider_id=key.provider_id,
            key_name=key.name,
            before=before,
            after=KeyStatus.invalid,
            message="密文解密失败",
        )
        await db.commit()
        return {"id": key.id, "status": key.status.value, "ok": False, "error": key.last_error}

    result = await _probe(key.provider, raw)
    new_status, err, rlu = _classify(result, key.status, key.rate_limited_until, key.last_tested_at)
    key.status = new_status
    key.last_error = err
    key.rate_limited_until = rlu
    key.last_tested_at = _now()
    await emit_key_alert(
        db,
        key_id=key.id,
        provider_id=key.provider_id,
        key_name=key.name,
        before=before,
        after=new_status,
        message=err or "健康探测失败",
    )
    # key 从非 active 恢复为 active → 自动 resolve 其历史未解决告警
    if new_status == KeyStatus.active and before != KeyStatus.active:
        await resolve_key_alerts(db, key.id)
    await db.commit()
    return {
        "id": key.id,
        "status": key.status.value,
        "ok": result.ok,
        "error": err,
        "http_status": result.http_status,
        "via": result.via,
        "state_changed": before != key.status,
    }


# 批量探测并发度（避免对上游同时打太多请求）
_BATCH_CONCURRENCY = 5


async def test_keys(
    db: AsyncSession,
    provider_id: int | None = None,
    group_id: int | None = None,
) -> list[dict]:
    """批量探测密钥（并发，跳过已手动停用的 key）。"""
    stmt = select(ApiKey).where(ApiKey.status != KeyStatus.disabled)
    if provider_id is not None:
        stmt = stmt.where(ApiKey.provider_id == provider_id)
    if group_id is not None:
        stmt = stmt.where(ApiKey.group_id == group_id)
    result = await db.execute(stmt.order_by(ApiKey.id))
    key_ids = [k.id for k in result.scalars()]

    sem = asyncio.Semaphore(_BATCH_CONCURRENCY)

    async def _test_one(kid: int) -> dict:
        async with sem:
            async with AsyncSessionLocal() as sdb:
                k = await sdb.get(ApiKey, kid)
                if not k:
                    return {"id": kid, "status": "unknown", "ok": False, "error": "密钥不存在"}
                return await test_key(sdb, k)

    results = await asyncio.gather(*[_test_one(kid) for kid in key_ids])
    return list(results)
