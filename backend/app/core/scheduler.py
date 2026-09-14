from __future__ import annotations

import structlog
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import ApiKey, KeyModelStatus, KeyStatus
from app.services.health_check import test_key, test_key_model_status
from app.services.usage import rollup_date, rollup_recent

log = structlog.get_logger()
scheduler = AsyncIOScheduler()

# 巡检间隔（分钟）
SWEEP_INTERVAL_MINUTES = 10
# exhausted 状态密钥的最小重测间隔（分钟）。
# 转售商额度窗口（按 key 配额）滚动恢复常在几十分钟内，60 分钟锁空窗期太长；
# 1-token 探针成本极低，10 分钟复测让恢复的密钥尽快回池参与调度。
EXHAUSTED_RETEST_MINUTES = 10


async def sweep_degraded_keys() -> None:
    """周期巡检：重测所有非 active 的密钥，恢复成功的转回 active。

    exhausted 状态的密钥距上次测试不足 EXHAUSTED_RETEST_MINUTES 则跳过，
    避免频繁命中上游 429 quota。
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with AsyncSessionLocal() as db:
        rows = await db.execute(select(ApiKey).where(ApiKey.status.not_in([KeyStatus.active, KeyStatus.disabled])))
        keys = list(rows.scalars())
        if not keys:
            return
        recovered = 0
        skipped_exhausted = 0
        for key in keys:
            # exhausted 密钥延长巡检间隔
            if key.status == KeyStatus.exhausted and key.last_tested_at is not None:
                if (now - key.last_tested_at) < timedelta(minutes=EXHAUSTED_RETEST_MINUTES):
                    skipped_exhausted += 1
                    continue
            before = key.status
            try:
                await test_key(db, key)
            except Exception as e:  # 单 key 失败不影响其他
                log.warning("sweep_key_failed", key_id=key.id, error=str(e))
                continue
            if before != KeyStatus.active and key.status == KeyStatus.active:
                recovered += 1
        log.info("sweep_done", total=len(keys), tested=len(keys) - skipped_exhausted,
                 skipped_exhausted=skipped_exhausted, recovered=recovered)


async def sweep_degraded_model_statuses() -> None:
    """周期巡检：重测所有非 active 的密钥×模型组合，恢复成功的转回 active。

    与密钥级巡检互补：密钥级测的是 provider.models[0]，模型级测的是
    用户实际使用的原生模型名（如 glm-5.2、deepseek-v4-flash）。
    exhausted 状态距上次测试不足 EXHAUSTED_RETEST_MINUTES 则跳过。
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            select(KeyModelStatus).where(KeyModelStatus.status.not_in([KeyStatus.active, KeyStatus.disabled]))
        )
        kms_list = list(rows.scalars())
        if not kms_list:
            return
        recovered = 0
        skipped_exhausted = 0
        for kms in kms_list:
            # exhausted 延长巡检间隔
            if kms.status == KeyStatus.exhausted and kms.last_tested_at is not None:
                if (now - kms.last_tested_at) < timedelta(minutes=EXHAUSTED_RETEST_MINUTES):
                    skipped_exhausted += 1
                    continue
            try:
                result = await test_key_model_status(db, kms)
                if result.get("status") == "active":
                    recovered += 1
            except Exception as e:
                log.warning("sweep_model_status_failed", id=kms.id, key_id=kms.key_id,
                            model_name=kms.model_name, error=str(e))
                continue
        log.info("sweep_model_status_done", total=len(kms_list),
                 tested=len(kms_list) - skipped_exhausted,
                 skipped_exhausted=skipped_exhausted, recovered=recovered)


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
    # 密钥×模型细粒度状态巡检（测用户实际使用的模型名，而非 provider.models[0]）
    scheduler.add_job(
        sweep_degraded_model_statuses,
        "interval",
        minutes=SWEEP_INTERVAL_MINUTES,
        id="sweep_degraded_model_statuses",
        replace_existing=True,
    )
    # 启动后 35 秒跑一次首轮模型级巡检
    scheduler.add_job(
        sweep_degraded_model_statuses,
        "date",
        id="sweep_model_status_first",
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
