from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_value
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


async def _probe_models(base: str, headers: dict[str, str]) -> ProbeResult | None:
    """探测列模型接口。返回 None 表示端点不可用，应交给对话兜底。"""
    url = _join(base, "models")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
    except Exception as e:  # 网络层故障：瞬时，不改变 key 状态
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
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                url, headers={**headers, "Content-Type": "application/json"}, json=payload
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
        return ProbeResult(False, None, None, "Anthropic 探测失败")

    if provider.probe_kind == ProbeKind.gemini_models:
        url = f"{base.rstrip('/')}/v1beta/models?key={raw_key}"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url)
        except Exception as e:
            return ProbeResult(False, None, None, f"{type(e).__name__}: {e}")
    if resp.status_code == 200:
        return ProbeResult(True, 200, None, None)
    snippet = resp.text[:200] if resp.text else ""
    return ProbeResult(
        False, resp.status_code, None, f"HTTP {resp.status_code}: {snippet}", body=snippet
    )

    return ProbeResult(False, None, None, "不支持的探测方式")


def _classify(
    result: ProbeResult, current: KeyStatus
) -> tuple[KeyStatus, str | None, datetime | None]:
    """根据探针结果决定新状态/错误/冷却到期。

    复用 relay.errors.classify_error，与健康路径保持一致：
    429 带 quota 关键词 → exhausted（额度用尽，靠巡检恢复，不设冷却）；
    纯 429 → rate_limited + 短冷却；401/403 → invalid。
    """
    now = _now()
    if result.ok:
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
    from app.services.alerts import emit_key_alert

    before = key.status
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
    new_status, err, rlu = _classify(result, key.status)
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
    await db.commit()
    return {
        "id": key.id,
        "status": key.status.value,
        "ok": result.ok,
        "error": err,
        "http_status": result.http_status,
        "via": result.via,
    }


async def test_keys(
    db: AsyncSession,
    provider_id: int | None = None,
    group_id: int | None = None,
) -> list[dict]:
    """批量探测密钥。"""
    stmt = select(ApiKey)
    if provider_id is not None:
        stmt = stmt.where(ApiKey.provider_id == provider_id)
    if group_id is not None:
        stmt = stmt.where(ApiKey.group_id == group_id)
    result = await db.execute(stmt.order_by(ApiKey.id))
    keys = list(result.scalars())

    results: list[dict] = []
    for key in keys:
        results.append(await test_key(db, key))
    return results
