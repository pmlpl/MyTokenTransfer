"""DailyUsage 预聚合：rollup_date 的正确性单测。

验证：把一天的多条 CallLog 明细正确归并成按 (key, model) 的聚合行，
且不影响其他日期。
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from sqlalchemy import select

from app.models import CallLog, DailyUsage
from app.services.usage import rollup_date


def _log(day, *, key_id, native, pt, ct, total, cost, status="success"):
    return CallLog(
        created_at=datetime.combine(day, time(12, 0)),
        channel_id=None,
        tier_order=0,
        provider_id=None,
        key_id=key_id,
        virtual_model="gpt-4",
        native_model=native,
        prompt_tokens=pt,
        completion_tokens=ct,
        total_tokens=total,
        cost=cost,
        latency_ms=100,
        status=status,
        error=None,
        stream=False,
    )


async def test_rollup_aggregates_by_key_and_model(db_session):
    db = db_session
    day = datetime(2026, 8, 15).date()

    db.add_all([
        _log(day, key_id=1, native="gpt-4",       pt=100, ct=50,  total=150, cost=0.01),
        _log(day, key_id=1, native="gpt-4",       pt=200, ct=80,  total=280, cost=0.02),  # 同 key 同模型累加
        _log(day, key_id=1, native="gpt-4o",      pt=10,  ct=5,   total=15,  cost=0.001), # 同 key 不同模型
        _log(day, key_id=2, native="gpt-4",       pt=50,  ct=20,  total=70,  cost=0.005),  # 不同 key
    ])
    await db.commit()

    n = await rollup_date(db, day)
    assert n == 3  # (key1,gpt-4) (key1,gpt-4o) (key2,gpt-4)

    rows = (await db.execute(select(DailyUsage).where(DailyUsage.day == day))).scalars().all()
    by_pair = {(r.key_id, r.model): r for r in rows}
    assert set(by_pair) == {(1, "gpt-4"), (1, "gpt-4o"), (2, "gpt-4")}

    k1gpt4 = by_pair[(1, "gpt-4")]
    assert k1gpt4.calls == 2
    assert k1gpt4.prompt_tokens == 300
    assert k1gpt4.completion_tokens == 130
    assert k1gpt4.total_tokens == 430
    assert round(float(k1gpt4.cost), 4) == 0.03


async def test_rollup_isolated_per_day(db_session):
    """rollup 某天不影响其他天的行。"""
    db = db_session
    d1 = datetime(2026, 8, 14).date()
    d2 = datetime(2026, 8, 15).date()

    db.add_all([
        _log(d1, key_id=1, native="gpt-4", pt=10, ct=5, total=15, cost=0.01),
        _log(d2, key_id=1, native="gpt-4", pt=20, ct=10, total=30, cost=0.02),
    ])
    await db.commit()

    await rollup_date(db, d1)
    d1_rows = (await db.execute(select(DailyUsage).where(DailyUsage.day == d1))).scalars().all()
    d2_rows = (await db.execute(select(DailyUsage).where(DailyUsage.day == d2))).scalars().all()
    assert len(d1_rows) == 1 and d1_rows[0].total_tokens == 15
    assert d2_rows == []


async def test_rollup_idempotent(db_session):
    """重复 rollup 同一天：先 insert 再 rollup 应替换而非累积。"""
    db = db_session
    day = datetime(2026, 8, 16).date()

    db.add(_log(day, key_id=1, native="gpt-4", pt=100, ct=50, total=150, cost=0.01))
    await db.commit()

    await rollup_date(db, day)
    await rollup_date(db, day)  # 再来一次

    rows = (await db.execute(select(DailyUsage).where(DailyUsage.day == day))).scalars().all()
    assert len(rows) == 1
    assert rows[0].calls == 1
    assert rows[0].total_tokens == 150
