"""调度候选队列 `_build_attempts` 的四级兜底回归测试。

两次线上 503（no_available_key）事故的修复护栏：
- L1 正常路径：active 密钥按策略排序；
- L2 全池耗尽兜底：追加最久未测的 exhausted 密钥（f73ec51）；
- L3 模型池冷却兜底：全部 (key,模型) 组合进入 300 秒冷却时，追加冷却中的
  组合（c398298，deepseek 小池子事故）；
- L4 最终兜底：所有层处于 tier TPM 冷却时，无视冷却取层内密钥
  （fcaf33a，CrawAgent 高并发打满共享 TPM 事故）。
另覆盖守卫豁免判定 `_cooldown_exemptions`（两个请求循环共用的跳过开关）。
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from app.models import ApiKey, Channel, ChannelKey, ChannelTier, Provider
from app.models.enums import KeyStatus, KeyStrategy
from app.relay.balancer import (
    mark_account_rate_limited,
    mark_model_quota_limited,
    now_utc,
)
from app.relay.gateway import _build_attempts, _cooldown_exemptions

VIRTUAL = "deepseek-v4-flash"


async def _provider(db) -> Provider:
    p = Provider(name="P", slug="testprov", base_url="https://upstream.test")
    db.add(p)
    await db.commit()
    return p


async def _channel(db, max_retries: int = 3) -> Channel:
    ch = Channel(
        name="ch", virtual_model=VIRTUAL,
        strategy=KeyStrategy.round_robin, max_retries=max_retries,
    )
    db.add(ch)
    await db.commit()
    await db.refresh(ch)  # selectin 关系此刻才加载，之后访问 ch.tiers 不会再触发同步懒加载
    return ch


async def _tier_with_keys(db, channel: Channel, provider: Provider, order: int,
                          *specs: tuple[KeyStatus, int, int]):
    """建一层并挂 key，specs: (status, weight, priority)。返回 (tier, [ApiKey...])。"""
    tier = ChannelTier(channel_id=channel.id, order=order, provider_id=provider.id)
    db.add(tier)
    await db.flush()

    keys: list[ApiKey] = []
    for status, weight, priority in specs:
        ak = ApiKey(
            provider_id=provider.id,
            name=f"k{len(keys)}",
            key_value="dummy-cipher",
            key_mask="sk-…xxxx",
            status=status,
            weight=weight,
            tags=None,
        )
        db.add(ak)
        await db.flush()
        db.add(ChannelKey(channel_tier_id=tier.id, key_id=ak.id, weight=weight, priority=priority))
        keys.append(ak)
    await db.commit()
    await db.refresh(channel)  # selectin 关系重新拉取，channel.tiers 才含新层
    return tier, keys


async def test_normal_active_then_exhausted_tail(db_session):
    """L1+L2：active 在前，exhausted 尾部兜底始终追加（试错代价仅快速 429）。"""
    db = db_session
    p = await _provider(db)
    ch = await _channel(db)
    tier, keys = await _tier_with_keys(
        db, ch, p, 0,
        (KeyStatus.active, 1, 0),
        (KeyStatus.active, 1, 0),
        (KeyStatus.exhausted, 1, 0),
        (KeyStatus.disabled, 1, 0),   # 不参与任何一级
    )

    attempts = await _build_attempts(ch, db, 0)
    assert [(t.id, k.id) for t, k in attempts] == [
        (tier.id, keys[0].id),
        (tier.id, keys[1].id),
        (tier.id, keys[2].id),   # exhausted 尾巴，正常尝试失败后才轮到
    ]


async def test_max_retries_caps_each_level(db_session):
    """max_retries 对每级分别截断：L1 取 1 把 active，L2 兜底仍给 1 把 exhausted。"""
    db = db_session
    p = await _provider(db)
    ch = await _channel(db, max_retries=1)
    tier, keys = await _tier_with_keys(
        db, ch, p, 0,
        (KeyStatus.active, 1, 0),
        (KeyStatus.active, 1, 0),
        (KeyStatus.exhausted, 1, 0),
        (KeyStatus.exhausted, 1, 0),
    )
    keys[2].last_tested_at = now_utc() - timedelta(minutes=5)
    keys[3].last_tested_at = now_utc() - timedelta(hours=1)  # 更久未测，优先兜底
    await db.commit()

    attempts = await _build_attempts(ch, db, 0)
    assert [(t.id, k.id) for t, k in attempts] == [
        (tier.id, keys[0].id),
        (tier.id, keys[3].id),
    ]


async def test_all_exhausted_last_resort(db_session):
    """L2：全池 exhausted 时队列只剩兜底尾巴，最久未测优先。"""
    db = db_session
    p = await _provider(db)
    ch = await _channel(db)
    tier, keys = await _tier_with_keys(
        db, ch, p, 0,
        (KeyStatus.exhausted, 1, 0),
        (KeyStatus.exhausted, 1, 0),
        (KeyStatus.invalid, 1, 0),   # invalid 永不参与
    )
    keys[1].last_tested_at = now_utc() - timedelta(hours=2)
    keys[0].last_tested_at = now_utc() - timedelta(minutes=5)
    await db.commit()

    attempts = await _build_attempts(ch, db, 0)
    assert [(t.id, k.id) for t, k in attempts] == [
        (tier.id, keys[1].id),   # 更久未测，先赌它的窗口已重置
        (tier.id, keys[0].id),
    ]


async def test_model_pool_cooldown_skipped_when_clean_exists(db_session):
    """L1 守卫：有干净组合时跳过模型池冷却中的 key；冷却别的模型不牵连本模型。"""
    db = db_session
    p = await _provider(db)
    ch = await _channel(db)
    tier, keys = await _tier_with_keys(
        db, ch, p, 0,
        (KeyStatus.active, 1, 0),
        (KeyStatus.active, 1, 0),
        (KeyStatus.active, 1, 0),
    )
    mark_model_quota_limited(keys[2].id, VIRTUAL)       # 本模型积分池刚 429
    mark_model_quota_limited(keys[1].id, "glm-5.2")     # 别的模型，不影响

    attempts = await _build_attempts(ch, db, 0)
    assert [(t.id, k.id) for t, k in attempts] == [
        (tier.id, keys[0].id),
        (tier.id, keys[1].id),   # 只在 glm-5.2 上冷却，deepseek 可用
    ]


async def test_all_model_pool_cooling_triggers_level3(db_session):
    """L3：全部组合处于模型池冷却时，追加冷却中的组合做最后尝试（c398298）。"""
    db = db_session
    p = await _provider(db)
    ch = await _channel(db)
    tier, keys = await _tier_with_keys(
        db, ch, p, 0,
        (KeyStatus.active, 1, 0),
        (KeyStatus.active, 1, 0),
    )
    mark_model_quota_limited(keys[0].id, VIRTUAL)
    mark_model_quota_limited(keys[1].id, VIRTUAL)

    attempts = await _build_attempts(ch, db, 0)
    assert [(t.id, k.id) for t, k in attempts] == [
        (tier.id, keys[0].id),
        (tier.id, keys[1].id),
    ]


async def test_exhausted_tail_beats_model_pool_fallback(db_session):
    """L2 优先于 L3：exhausted 兜底使队列非空后，模型池冷却组合不再追加。

    兜底按代价排序——exhausted 只是额度用尽，冷却组合连正常层都进不去，
    先试更可能成功的。
    """
    db = db_session
    p = await _provider(db)
    ch = await _channel(db)
    tier, keys = await _tier_with_keys(
        db, ch, p, 0,
        (KeyStatus.active, 1, 0),
        (KeyStatus.active, 1, 0),
        (KeyStatus.exhausted, 1, 0),
    )
    mark_model_quota_limited(keys[0].id, VIRTUAL)
    mark_model_quota_limited(keys[1].id, VIRTUAL)

    attempts = await _build_attempts(ch, db, 0)
    assert [(t.id, k.id) for t, k in attempts] == [(tier.id, keys[2].id)]


async def test_tier_cooldown_triggers_final_resort(db_session):
    """L4：整层 TPM 冷却时 L1–L3 全部被守卫跳过，final_resort 无视冷却收尾（fcaf33a）。"""
    db = db_session
    p = await _provider(db)
    ch = await _channel(db)
    tier, keys = await _tier_with_keys(
        db, ch, p, 0,
        (KeyStatus.active, 1, 0),
        (KeyStatus.exhausted, 1, 0),   # 从未测过 → 排最前
        (KeyStatus.disabled, 1, 0),
        (KeyStatus.invalid, 1, 0),     # 仍排除
    )
    keys[0].last_tested_at = now_utc() - timedelta(hours=1)
    keys[2].last_tested_at = now_utc() - timedelta(hours=2)
    await db.commit()
    mark_account_rate_limited(p.id, None)

    attempts = await _build_attempts(ch, db, 0)
    assert [(t.id, k.id) for t, k in attempts] == [
        (tier.id, keys[1].id),
        (tier.id, keys[2].id),
        (tier.id, keys[0].id),
    ]


async def test_tier_order_precedes_key_id(db_session):
    """跨层顺序按 tier.order：主层 key 的 id 更大也要排前面。"""
    db = db_session
    p = await _provider(db)
    ch = await _channel(db)
    _, keys_low = await _tier_with_keys(db, ch, p, 1, (KeyStatus.active, 1, 0))   # 先建，id 更小
    tier_high, keys_high = await _tier_with_keys(db, ch, p, 0, (KeyStatus.active, 1, 0))

    attempts = await _build_attempts(ch, db, 0)
    assert [(t.id, k.id) for t, k in attempts] == [
        (tier_high.id, keys_high[0].id),
        (ch.tiers[1].id, keys_low[0].id),
    ]


async def test_empty_pool_returns_empty(db_session):
    """空通道/空层 → 空队列（上层据此回 503）。"""
    db = db_session
    p = await _provider(db)
    ch = await _channel(db)
    assert await _build_attempts(ch, db, 0) == []

    tier, _keys = await _tier_with_keys(db, ch, p, 0)   # 层存在但没有 key
    assert await _build_attempts(ch, db, 0) == []


def test_cooldown_exemptions_all_cooling():
    """兜底场景：attempts 全部处于冷却 → 两个守卫都豁免，组合才有机会执行。"""
    mark_model_quota_limited(1, VIRTUAL)
    mark_model_quota_limited(2, VIRTUAL)
    mark_account_rate_limited(9, "acct")
    t = SimpleNamespace(id=9)
    attempts = [
        (t, SimpleNamespace(id=1, provider_id=9, account_label="acct")),
        (t, SimpleNamespace(id=2, provider_id=9, account_label="acct")),
    ]

    allow_quota, allow_account = _cooldown_exemptions(attempts, VIRTUAL)
    assert allow_quota
    assert allow_account


def test_cooldown_exemptions_partial_not_exempt():
    """存在任一干净组合即不豁免：冷却中的照旧跳过，别浪费重试次数。"""
    mark_model_quota_limited(1, VIRTUAL)
    mark_account_rate_limited(9, "acct")
    attempts = [
        (SimpleNamespace(id=9), SimpleNamespace(id=1, provider_id=9, account_label="acct")),    # 双冷却
        (SimpleNamespace(id=8), SimpleNamespace(id=2, provider_id=8, account_label="other")),  # 干净组合
    ]

    allow_quota, allow_account = _cooldown_exemptions(attempts, VIRTUAL)
    assert not allow_quota
    assert not allow_account


def test_cooldown_exemptions_empty_attempts():
    """空队列不豁免（调用方在拿到空 attempts 时已提前 503，这里是防御）。"""
    assert _cooldown_exemptions([], VIRTUAL) == (False, False)
