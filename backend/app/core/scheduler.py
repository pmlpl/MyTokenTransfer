from __future__ import annotations

import structlog

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import ApiKey, KeyStatus
from app.services.health_check import test_key
from app.services.usage import rollup_date, rollup_recent

log = structlog.get_logger()
scheduler = AsyncIOScheduler()

# 巡检间隔（分钟）
SWEEP_INTERVAL_MINUTES = 10


async def sweep_degraded_keys() -> None:
    """周期巡检：重测所有非 active 的密钥，恢复成功的转回 active。"""
    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(ApiKey).where(ApiKey.status != KeyStatus.active))
        keys = list(rows.scalars())
        if not keys:
            return
        recovered = 0
        for key in keys:
            before = key.status
            try:
                await test_key(db, key)
            except Exception as e:  # 单 key 失败不影响其他
                log.warning("sweep_key_failed", key_id=key.id, error=str(e))
                continue
            if before != KeyStatus.active and key.status == KeyStatus.active:
                recovered += 1
        log.info("sweep_done", total=len(keys), recovered=recovered)


async def rollup_yesterday() -> None:
    """每日固化前一天的用量聚合（凌晨跑，把昨天定为最终值）。"""
    from datetime import datetime, timedelta, timezone

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    async with AsyncSessionLocal() as db:
        await rollup_date(db, yesterday)
    log.info("rollup_yesterday_done", day=str(yesterday))


async def backfill_usage() -> None:
    """启动回填：把最近 60 天的 CallLog 聚合进 DailyUsage。"""
    async with AsyncSessionLocal() as db:
        await rollup_recent(db, days=60)


def start_scheduler() -> None:
    scheduler.add_job(
        sweep_degraded_keys,
        "interval",
        minutes=SWEEP_INTERVAL_MINUTES,
        id="sweep_degraded_keys",
        replace_existing=True,
    )
    # 启动后 30 秒跑一次首轮巡检
    scheduler.add_job(
        sweep_degraded_keys,
        "date",
        id="sweep_first",
        replace_existing=True,
    )
    # 每日凌晨 02:30 固化前一天的用量聚合
    scheduler.add_job(
        rollup_yesterday,
        "cron",
        hour=2,
        minute=30,
        id="rollup_yesterday",
        replace_existing=True,
    )
    # 启动后 40 秒回填最近 60 天聚合
    scheduler.add_job(
        backfill_usage,
        "date",
        id="rollup_backfill",
        replace_existing=True,
    )
    scheduler.start()
    log.info("scheduler_started", interval_min=SWEEP_INTERVAL_MINUTES)


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
