"""密钥池调度与状态机：Tier fallback + 池内负载均衡 + 错误分类。

这一层只管"选哪个 key、命中错误后怎么标状态、沿 Tier 链怎么兜底"，
不碰 HTTP。gateway 调用它得到有序的 (tier, key) 尝试序列。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiKey, ChannelKey, ChannelTier, KeyStatus, ModelMapping
from app.models.enums import KeyStrategy
from app.services.health_check import _DEFAULT_BASES, _join

log = structlog.get_logger("relay")

# 限流冷却时长（秒）：命中 429 时把 key 标 rate_limited 并冷却这段时间
_RATE_LIMIT_COOLDOWN = 60


def now_utc() -> datetime:
    """当前 UTC 时间（naive，与 DB 时间口径一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def base_url_for(provider) -> str:
    """供应商接口基址：显式 base_url 优先，否则按 slug 兜底。"""
    return provider.base_url or _DEFAULT_BASES.get(provider.slug, "")


async def resolve_native(virtual_model: str, provider_id: int, db: AsyncSession) -> str:
    """虚拟模型 → 该供应商的 native 模型名（无映射则原样透传）。"""
    mm = await db.scalar(
        select(ModelMapping).where(
            ModelMapping.virtual_model == virtual_model, ModelMapping.provider_id == provider_id
        )
    )
    return mm.native_model if mm else virtual_model


async def ordered_keys(tier: ChannelTier, strategy: KeyStrategy, db: AsyncSession) -> list[ApiKey]:
    """层内按策略排序的可用密钥（跳过非 active / 限流冷却中）。"""
    rows = await db.execute(select(ChannelKey).where(ChannelKey.channel_tier_id == tier.id))
    cks = list(rows.scalars())
    pairs: list[tuple[ChannelKey, ApiKey]] = []
    for ck in cks:
        ak = await db.get(ApiKey, ck.key_id)
        if not ak:
            continue
        if ak.status != KeyStatus.active:
            continue
        if ak.rate_limited_until and ak.rate_limited_until > now_utc():
            continue
        pairs.append((ck, ak))

    if strategy == KeyStrategy.priority:
        pairs.sort(key=lambda x: (-x[0].priority, x[0].id))
    elif strategy == KeyStrategy.weighted:
        # 按 weight 重复后打散（确定性随机）
        import random

        pool: list[tuple[ChannelKey, ApiKey]] = []
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


def classify_error(status_code: int, body_text: str) -> tuple[bool, KeyStatus | None, str]:
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


async def mark_key(
    key: ApiKey,
    status: KeyStatus | None,
    db: AsyncSession,
    *,
    message: str | None = None,
) -> None:
    """按错误分类结果标记 key 状态；状态劣化时顺带写一条告警。"""
    if status is None:
        return
    before = key.status
    key.status = status
    if status == KeyStatus.rate_limited:
        key.rate_limited_until = now_utc() + timedelta(seconds=_RATE_LIMIT_COOLDOWN)
    log.warning(
        "key_status_changed",
        key_id=key.id,
        provider_id=key.provider_id,
        before=before.value if before else None,
        after=status.value,
        message=message,
    )
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
