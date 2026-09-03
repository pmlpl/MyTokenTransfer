"""用量预聚合：把 CallLog 明细按日归并到 DailyUsage 小表。

仪表盘查 DailyUsage（每天每 key 每模型一行），避免随 call_logs 膨胀扫描明细。
单天重算是 delete + insert，幂等、可读路径上安全调用。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CallLog, DailyUsage

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _day_bounds(d: date) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time.min)
    return start, start + timedelta(days=1)


async def rollup_date(db: AsyncSession, d: date) -> int:
    """重算某天的聚合行并落库。返回写入的行数。

    先删该天旧行再从 call_logs 重新聚合插入——所以重复调用幂等，
    新增/补录的明细会被算进来。读路径上也可安全调用（单用户场景）。
    """
    await db.execute(delete(DailyUsage).where(DailyUsage.day == d))

    start, end = _day_bounds(d)
    # 与仪表盘一致：native 为空时回退到 virtual
    model_field = func.coalesce(func.nullif(CallLog.native_model, ""), CallLog.virtual_model)
    stmt = (
        select(
            CallLog.key_id,
            model_field.label("model"),
            func.count().label("calls"),
            func.sum(CallLog.prompt_tokens).label("pt"),
            func.sum(CallLog.completion_tokens).label("ct"),
            func.sum(CallLog.total_tokens).label("tt"),
            func.sum(CallLog.cached_tokens).label("cached"),
            func.sum(CallLog.cost).label("cost"),
        )
        .where(CallLog.created_at >= start, CallLog.created_at < end)
        .group_by(CallLog.key_id, model_field)
    )
    rows = (await db.execute(stmt)).all()
    for r in rows:
        db.add(
            DailyUsage(
                day=d,
                key_id=r.key_id,
                model=r.model,
                calls=int(r.calls or 0),
                prompt_tokens=int(r.pt or 0),
                completion_tokens=int(r.ct or 0),
                total_tokens=int(r.tt or 0),
                cached_tokens=int(r.cached or 0),
                cost=float(r.cost or 0),
            )
        )
    await db.commit()
    return len(rows)


async def rollup_recent(db: AsyncSession, days: int = 60) -> int:
    """回填最近 N 天（启动时用，补上历史明细对应的聚合行）。"""
    today = _utcnow().date()
    total = 0
    for i in range(days):
        d = today - timedelta(days=i)
        total += await rollup_date(db, d)
    log.info("rollup_recent done days=%d rows=%d", days, total)
    return total
