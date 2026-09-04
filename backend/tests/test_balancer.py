"""池内密钥选择：gateway.ordered_keys 的单测。

验证故障转移的"自动跳过不可用 key"语义——exhausted/invalid/rate_limited 的 key
不会被选中，请求自动落到下一个可用账号。这是方案 §3.3 的核心。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.models import ApiKey, Channel, ChannelKey, ChannelTier, Provider
from app.models.enums import KeyStrategy, KeyStatus
from app.relay.balancer import now_utc, ordered_keys, routing_affinity


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

    chosen = await ordered_keys(tier, KeyStrategy.round_robin, db)
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
    keys[1].rate_limited_until = now_utc() + timedelta(hours=1)
    await db.commit()

    chosen = await ordered_keys(tier, KeyStrategy.round_robin, db)
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

    chosen = await ordered_keys(tier, KeyStrategy.round_robin, db)
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

    chosen = await ordered_keys(tier, KeyStrategy.priority, db)
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

    chosen = await ordered_keys(tier, KeyStrategy.weighted, db)
    assert set(k.id for k in chosen) == {keys[0].id, keys[1].id, keys[2].id}


async def test_empty_tier_returns_empty(db_session):
    """空池返回空列表（上层据此判断无可用 key）。"""
    db = db_session
    ch = await _channel(db)
    p = await _provider(db)
    tier = ChannelTier(channel_id=ch.id, order=0, provider_id=p.id)
    db.add(tier)
    await db.commit()

    chosen = await ordered_keys(tier, KeyStrategy.round_robin, db)
    assert chosen == []


async def _tier_3_keys(db, ch, p):
    return await _make_tier_with_keys(
        db, ch, p,
        (KeyStatus.active, 1, 0),
        (KeyStatus.active, 1, 0),
        (KeyStatus.active, 1, 0),
    )


async def test_round_robin_affinity_pins_same_seed(db_session):
    """同一亲和哈希 → 首位 key 固定；affinity=0 保持原 id 升序。"""
    db = db_session
    ch = await _channel(db)
    p = await _provider(db)
    tier, keys = await _tier_3_keys(db, ch, p)

    a = 12345
    first = (await ordered_keys(tier, KeyStrategy.round_robin, db, a))[0].id
    for _ in range(5):
        assert (await ordered_keys(tier, KeyStrategy.round_robin, db, a))[0].id == first

    # affinity=0 与旧行为一致：id 升序
    zero = await ordered_keys(tier, KeyStrategy.round_robin, db, 0)
    assert [k.id for k in zero] == [keys[0].id, keys[1].id, keys[2].id]


async def test_round_robin_affinity_spreads(db_session):
    """不同亲和哈希在池内摊开（至少两个不同首位），且覆盖全部 key。"""
    db = db_session
    ch = await _channel(db)
    p = await _provider(db)
    tier, keys = await _tier_3_keys(db, ch, p)

    firsts = { (await ordered_keys(tier, KeyStrategy.round_robin, db, a))[0].id for a in range(24) }
    assert len(firsts) >= 2  # 哈希分布散开
    assert firsts <= {keys[0].id, keys[1].id, keys[2].id}


async def test_weighted_affinity_deterministic(db_session):
    """weighted 用 affinity 播种 → 同一会话顺序确定；不同亲和覆盖全部 key。"""
    db = db_session
    ch = await _channel(db)
    p = await _provider(db)
    tier, keys = await _make_tier_with_keys(
        db, ch, p,
        (KeyStatus.active, 1, 0),
        (KeyStatus.active, 3, 0),
        (KeyStatus.active, 1, 0),
    )

    a = 999
    order_a = [k.id for k in await ordered_keys(tier, KeyStrategy.weighted, db, a)]
    for _ in range(5):
        assert [k.id for k in await ordered_keys(tier, KeyStrategy.weighted, db, a)] == order_a
    assert set(order_a) == {keys[0].id, keys[1].id, keys[2].id}

    # 不同亲和也应覆盖全部 active key
    for a in range(8):
        got = await ordered_keys(tier, KeyStrategy.weighted, db, a)
        assert {k.id for k in got} == {keys[0].id, keys[1].id, keys[2].id}


async def test_routing_affinity_stable_across_history_growth():
    """同会话历史增长（尾部追加消息）→ 头部哈希不变，会话固定同一 key。"""
    base = {"messages": [{"role": "system", "content": "你是助手"}, {"role": "user", "content": "你好"}]}
    grown = {"messages": base["messages"] + [{"role": "assistant", "content": "你好！"}, {"role": "user", "content": "继续"}]}
    assert routing_affinity("glm-5.2", base) == routing_affinity("glm-5.2", grown)


async def test_routing_affinity_changes_on_prefix_change():
    """system prompt 变化 → 哈希变化（改前缀/不同会话落到不同 key）。"""
    a = {"messages": [{"role": "system", "content": "你是助手A"}, {"role": "user", "content": "你好"}]}
    b = {"messages": [{"role": "system", "content": "你是助手B"}, {"role": "user", "content": "你好"}]}
    c = {"messages": [{"role": "system", "content": "你是助手A"}, {"role": "user", "content": "今天讲什么"}]}
    assert routing_affinity("glm-5.2", a) != routing_affinity("glm-5.2", b)
    assert routing_affinity("glm-5.2", a) != routing_affinity("glm-5.2", c)  # 首条 user 不同也区分


async def test_routing_affinity_embeddings_input():
    """embeddings 无 messages → 按 input 派生且确定性（精确重复→同 key）。"""
    body1 = {"input": "今天天气不错"}
    assert routing_affinity("embed-1", body1) == routing_affinity("embed-1", body1)
    assert routing_affinity("embed-1", body1) != routing_affinity("embed-2", body1)  # 模型不同
    # 无 messages 也无 input → 仅模型派生，仍确定
    assert routing_affinity("embed-1", {}) == routing_affinity("embed-1", {})
    # 数组 input 也确定性
    arr = {"input": ["a", "b"]}
    assert routing_affinity("embed-1", arr) == routing_affinity("embed-1", arr)
