"""池内密钥选择：gateway._ordered_keys 的单测。

验证故障转移的"自动跳过不可用 key"语义——exhausted/invalid/rate_limited 的 key
不会被选中，请求自动落到下一个可用账号。这是方案 §3.3 的核心。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models import ApiKey, Channel, ChannelKey, ChannelTier, Provider
from app.models.enums import KeyStrategy, KeyStatus
from app.relay.gateway import _now, _ordered_keys


async def _make_tier_with_keys(db, channel, provider, *specs: tuple[KeyStatus, int, int | None]):
    """建一个 tier 并挂若干 key。specs: (status, weight, priority)。

    返回 (tier, [ApiKey...])，ApiKey 顺序与 specs 一致。
    """
    tier = ChannelTier(channel_id=channel.id, order=0, provider_id=provider.id)
    db.add(tier)
    await db.flush()  # 拿到 tier.id

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
    await db.refresh(tier)
    return tier, keys


async def _channel(db) -> Channel:
    ch = Channel(name="ch", virtual_model="gpt-4", strategy=KeyStrategy.round_robin, max_retries=3)
    db.add(ch)
    await db.commit()
    return ch


async def _provider(db) -> Provider:
    p = Provider(name="OpenAI", slug="openai", base_url="https://api.openai.com")
    db.add(p)
    await db.commit()
    return p


async def test_skips_non_active_keys(db_session):
    """只有 active 的 key 被选中；exhausted/rate_limited/disabled 全部跳过。"""
    db = db_session
    ch = await _channel(db)
    p = await _provider(db)
    tier, keys = await _make_tier_with_keys(
        db, ch, p,
        (KeyStatus.active, 1, 0),
        (KeyStatus.exhausted, 1, 0),
        (KeyStatus.rate_limited, 1, 0),
        (KeyStatus.disabled, 1, 0),
    )

    chosen = await _ordered_keys(tier, KeyStrategy.round_robin, db)
    assert len(chosen) == 1
    assert chosen[0].id == keys[0].id


async def test_rate_limited_future_cooldown_skipped(db_session):
    """rate_limited 且冷却未到期 → 跳过。"""
    db = db_session
    ch = await _channel(db)
    p = await _provider(db)
    tier, keys = await _make_tier_with_keys(
        db, ch, p,
        (KeyStatus.active, 1, 0),
        (KeyStatus.active, 1, 0),
    )
    # 把第二个 active key 标成限流且冷却未到期
    keys[1].status = KeyStatus.rate_limited
    keys[1].rate_limited_until = _now() + timedelta(hours=1)
    await db.commit()

    chosen = await _ordered_keys(tier, KeyStrategy.round_robin, db)
    assert len(chosen) == 1
    assert chosen[0].id == keys[0].id


async def test_round_robin_is_id_order(db_session):
    db = db_session
    ch = await _channel(db)
    p = await _provider(db)
    tier, keys = await _make_tier_with_keys(
        db, ch, p,
        (KeyStatus.active, 1, 0),
        (KeyStatus.active, 1, 0),
        (KeyStatus.active, 1, 0),
    )

    chosen = await _ordered_keys(tier, KeyStrategy.round_robin, db)
    assert [k.id for k in chosen] == [keys[0].id, keys[1].id, keys[2].id]


async def test_priority_strategy(db_session):
    """priority 越大越优先；同 priority 回退到 id 升序。"""
    db = db_session
    ch = await _channel(db)
    p = await _provider(db)
    tier, keys = await _make_tier_with_keys(
        db, ch, p,
        (KeyStatus.active, 1, 1),   # 低优
        (KeyStatus.active, 1, 9),   # 高优
        (KeyStatus.active, 1, 5),   # 中
    )

    chosen = await _ordered_keys(tier, KeyStrategy.priority, db)
    assert [k.id for k in chosen] == [keys[1].id, keys[2].id, keys[0].id]


async def test_weighted_covers_all_active(db_session):
    """weighted 是确定性随机，无法断言顺序；断言所有 active key 都被覆盖。"""
    db = db_session
    ch = await _channel(db)
    p = await _provider(db)
    tier, keys = await _make_tier_with_keys(
        db, ch, p,
        (KeyStatus.active, 1, 0),
        (KeyStatus.active, 3, 0),   # weight 3
        (KeyStatus.active, 1, 0),
    )

    chosen = await _ordered_keys(tier, KeyStrategy.weighted, db)
    assert set(k.id for k in chosen) == {keys[0].id, keys[1].id, keys[2].id}


async def test_empty_tier_returns_empty(db_session):
    """空池返回空列表（上层据此判断无可用 key）。"""
    db = db_session
    ch = await _channel(db)
    p = await _provider(db)
    tier = ChannelTier(channel_id=ch.id, order=0, provider_id=p.id)
    db.add(tier)
    await db.commit()

    chosen = await _ordered_keys(tier, KeyStrategy.round_robin, db)
    assert chosen == []
