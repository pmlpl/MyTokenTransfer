"""密钥池调度与状态机：Tier fallback + 池内负载均衡 + 错误分类。

这一层只管"选哪个 key、命中错误后怎么标状态、沿 Tier 链怎么兜底"，
不碰 HTTP。gateway 调用它得到有序的 (tier, key) 尝试序列。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ApiKey, ChannelKey, ChannelTier, KeyStatus, ModelMapping
from app.models.enums import KeyStrategy
from app.services.health_check import _DEFAULT_BASES, _join

log = structlog.get_logger("relay")

# 缓存亲和路由：取消息稳定前缀的字符预算。同会话随历史增长前缀不变 →
# 哈希稳定 → 固定同一上游 key，复用其前缀缓存；共享同一 system+开头消息的
# 会话得到相同哈希 → 聚到同一 key，最大化共享前缀缓存复用。
_AFFINITY_PREFIX_BUDGET = 4096

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


def _head_prefix(messages, budget: int = _AFFINITY_PREFIX_BUDGET) -> str:
    """提取对话稳定头部（system + 第一条 user），用于路由亲和哈希。

    只取头部而非整段历史：历史随轮次增长，整段哈希会逐轮变化，导致
    同一会话漂移到不同 key、缓存全部落空；头部在会话内固定，哈希跨轮次
    稳定。共享同一头部的会话聚到同一 key，最大化头部前缀缓存复用。
    """
    parts: list[str] = []
    size = 0
    seen_user = False
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):  # 多模态 content 数组
            try:
                content = json.dumps(content, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                content = str(content)
        part = f"{role}:{content}"
        if role == "system":
            parts.append(part)
            size += len(part)
        elif not seen_user:  # 第一条非 system（通常是首条 user）
            parts.append(part)
            size += len(part)
            seen_user = True
        else:
            break  # 头部已取完，历史增长不再参与
        if size >= budget:
            break
    return "".join(parts)


def routing_affinity(virtual_model: str, body: dict, budget: int = _AFFINITY_PREFIX_BUDGET) -> int:
    """由虚拟模型 + 对话稳定头部计算确定性路由亲和哈希（跨请求稳定）。

    上游按 API key 隔离前缀缓存：同一会话固定到同一 key 才能复用其缓存。
    仅用于同池内确定性的 key 选择，不涉及敏感信息持久化。
    """
    messages = body.get("messages")
    if isinstance(messages, list):
        seed = f"{virtual_model}\x00{_head_prefix(messages, budget)}"
    else:
        # embeddings 等无 messages 的请求：退到 input 前部（精确重复→同 key）
        inp = body.get("input")
        if isinstance(inp, str):
            seed = f"{virtual_model}\x00{inp[:budget]}"
        elif isinstance(inp, list):
            seed = f"{virtual_model}\x00{json.dumps(inp, ensure_ascii=False, sort_keys=True)[:budget]}"
        else:
            seed = virtual_model
    return int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big")


async def ordered_keys(
    tier: ChannelTier,
    strategy: KeyStrategy,
    db: AsyncSession,
    affinity: int = 0,
) -> list[ApiKey]:
    """层内按策略排序的可用密钥（跳过非 active / 限流冷却中）。

    affinity=0 时行为与旧版一致（round_robin 即 id 升序、weighted 即随机洗牌），
    保证向后兼容；传入非 0 亲和哈希后，同会话确定性固定同一 key，
    降低因 key 抖动导致的上游前缀缓存未命中。
    """
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
        # 按 weight 重复后打散；用 affinity 播种 → 同会话确定性随机，亲和固定
        import random

        rng = random.Random(affinity)
        pool: list[tuple[ChannelKey, ApiKey]] = []
        for p in pairs:
            pool.extend([p] * max(1, p[0].weight))
        rng.shuffle(pool)
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
    else:  # round_robin：id 升序后按亲和哈希循环移位，同一会话固定首位 key
        pairs.sort(key=lambda x: x[0].id)
        if affinity and len(pairs) > 1:
            idx = affinity % len(pairs)
            pairs = pairs[idx:] + pairs[:idx]
    return [p[1] for p in pairs]


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
