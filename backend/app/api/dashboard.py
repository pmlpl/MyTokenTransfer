from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_auth
from app.database import get_db
from app.models import ApiKey, CallLog, DailyUsage, KeyStatus, Provider
from app.services.usage import rollup_date

router = APIRouter(
    prefix="/stats",
    tags=["stats"],
    dependencies=[Depends(require_auth)],
)


@router.get("/overview")
async def overview(db: AsyncSession = Depends(get_db)) -> dict:
    provider_total = await db.scalar(select(func.count(Provider.id)))
    key_total = await db.scalar(select(func.count(ApiKey.id)))
    active = await db.scalar(select(func.count(ApiKey.id)).where(ApiKey.status == KeyStatus.active))

    rows = await db.execute(select(ApiKey.status))
    by_status = Counter(r[0].value if hasattr(r[0], "value") else r[0] for r in rows)
    return {
        "providers": provider_total or 0,
        "keys": key_total or 0,
        "active": active or 0,
        "by_status": dict(by_status),
    }


@router.get("/usage")
async def usage(
    days: int = Query(14, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # 先把今天（可能还在增长）重算进聚合表，保证当天数据新鲜
    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    await rollup_date(db, today)

    start = today - timedelta(days=days - 1)

    # 按日聚合（读预聚合小表，不再扫 call_logs 明细）
    daily_stmt = (
        select(
            DailyUsage.day.label("d"),
            func.sum(DailyUsage.calls).label("calls"),
            func.sum(DailyUsage.prompt_tokens).label("pt"),
            func.sum(DailyUsage.completion_tokens).label("ct"),
            func.sum(DailyUsage.total_tokens).label("tt"),
            func.sum(DailyUsage.cached_tokens).label("cached"),
            func.sum(DailyUsage.cost).label("cost"),
        )
        .where(DailyUsage.day >= start, DailyUsage.day <= today)
        .group_by(DailyUsage.day)
        .order_by(DailyUsage.day)
    )
    daily_rows = (await db.execute(daily_stmt)).all()
    daily = [
        {
            "date": str(r.d),
            "calls": int(r.calls or 0),
            "prompt_tokens": int(r.pt or 0),
            "completion_tokens": int(r.ct or 0),
            "total_tokens": int(r.tt or 0),
            "cached_tokens": int(r.cached or 0),
            "cost": float(r.cost or 0),
        }
        for r in daily_rows
    ]

    # 按日×按模型聚合：v2 真按模型堆叠面积图需要的维度
    daily_bm_stmt = (
        select(
            DailyUsage.day.label("d"),
            DailyUsage.model.label("m"),
            func.sum(DailyUsage.total_tokens).label("tt"),
            func.sum(DailyUsage.cost).label("cost"),
        )
        .where(DailyUsage.day >= start, DailyUsage.day <= today)
        .group_by(DailyUsage.day, DailyUsage.model)
        .order_by(DailyUsage.day, func.sum(DailyUsage.total_tokens).desc())
    )
    daily_bm_rows = (await db.execute(daily_bm_stmt)).all()
    daily_by_model = [
        {
            "date": str(r.d),
            "model": str(r.m or "未知"),
            "total_tokens": int(r.tt or 0),
            "cost": float(r.cost or 0),
        }
        for r in daily_bm_rows
    ]

    # 按模型聚合
    model_stmt = (
        select(
            DailyUsage.model.label("m"),
            func.sum(DailyUsage.calls).label("calls"),
            func.sum(DailyUsage.total_tokens).label("tt"),
            func.sum(DailyUsage.cost).label("cost"),
        )
        .where(DailyUsage.day >= start, DailyUsage.day <= today)
        .group_by(DailyUsage.model)
        .order_by(func.sum(DailyUsage.cost).desc())
    )
    model_rows = (await db.execute(model_stmt)).all()
    by_model = [
        {"model": str(r.m or "未知"), "calls": int(r.calls or 0), "total_tokens": int(r.tt or 0), "cost": float(r.cost or 0)}
        for r in model_rows
    ]

    # 按密钥聚合（含每 key 缓存命中率，便于定位低命中 key / 验证缓存亲和效果）
    key_stmt = (
        select(
            DailyUsage.key_id.label("kid"),
            func.sum(DailyUsage.calls).label("calls"),
            func.sum(DailyUsage.prompt_tokens).label("pt"),
            func.sum(DailyUsage.cached_tokens).label("cached"),
            func.sum(DailyUsage.cost).label("cost"),
        )
        .where(DailyUsage.day >= start, DailyUsage.day <= today)
        .group_by(DailyUsage.key_id)
        .order_by(func.sum(DailyUsage.cost).desc())
    )
    key_rows = (await db.execute(key_stmt)).all()
    # 同时取 name 和 key_mask，name 为空时用掩码兜底，key 已删除时标注
    key_info = {
        kid: (name, mask)
        for kid, name, mask in (await db.execute(select(ApiKey.id, ApiKey.name, ApiKey.key_mask))).all()
    }
    by_key = []
    for r in key_rows:
        pt = int(r.pt or 0)
        cached = int(r.cached or 0)
        info = key_info.get(r.kid)
        display_name = (
            (info[0] if info else None)
            or (info[1] if info else None)
            or (f"key#{r.kid}（已删除）" if r.kid is not None else "未知")
        )
        by_key.append(
            {
                "key_id": r.kid,
                "key_name": display_name,
                "calls": int(r.calls or 0),
                "prompt_tokens": pt,
                "cached_tokens": cached,
                "cache_hit_rate": round(cached / pt * 100, 2) if pt else 0.0,
                "cost": float(r.cost or 0),
            }
        )

    # 汇总
    totals_stmt = select(
        func.sum(DailyUsage.calls).label("calls"),
        func.sum(DailyUsage.total_tokens).label("tt"),
        func.sum(DailyUsage.cached_tokens).label("cached"),
        func.sum(DailyUsage.cost).label("cost"),
    ).where(DailyUsage.day >= start, DailyUsage.day <= today)
    t = (await db.execute(totals_stmt)).one()
    total_tokens = int(t.tt or 0)
    cached_tokens = int(t.cached or 0)
    # 缓存命中率 = cached_tokens / prompt_tokens（缓存命中发生在输入侧）
    prompt_sum = sum(d["prompt_tokens"] for d in daily)
    cache_hit_rate = round(cached_tokens / prompt_sum * 100, 2) if prompt_sum > 0 else 0.0

    # 今日视图：从 call_logs 按北京时间（UTC+8）小时聚合，用于波形图
    hourly = []
    hourly_by_model = []
    if days == 1:
        # 北京时间今日 00:00 对应 UTC 昨日 16:00
        beijing_today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
        day_start = datetime.combine(beijing_today, datetime.min.time()) - timedelta(hours=8)
        day_end = day_start + timedelta(days=1)
        model_field = func.coalesce(func.nullif(CallLog.native_model, ""), CallLog.virtual_model)
        # 北京时间小时 = HOUR(created_at + INTERVAL 8 HOUR)
        hour_expr = func.hour(func.date_add(CallLog.created_at, text("INTERVAL 8 HOUR")))

        # 按小时聚合
        h_stmt = (
            select(
                hour_expr.label("hr"),
                func.count(CallLog.id).label("calls"),
                func.sum(CallLog.prompt_tokens).label("pt"),
                func.sum(CallLog.completion_tokens).label("ct"),
                func.sum(CallLog.total_tokens).label("tt"),
                func.sum(CallLog.cached_tokens).label("cached"),
                func.sum(CallLog.cost).label("cost"),
            )
            .where(CallLog.created_at >= day_start, CallLog.created_at < day_end)
            .group_by(hour_expr)
            .order_by(hour_expr)
        )
        h_rows = (await db.execute(h_stmt)).all()
        h_map = {int(r.hr): r for r in h_rows}
        for hr in range(24):
            r = h_map.get(hr)
            hourly.append({
                "hour": hr,
                "calls": int(r.calls or 0) if r else 0,
                "prompt_tokens": int(r.pt or 0) if r else 0,
                "completion_tokens": int(r.ct or 0) if r else 0,
                "total_tokens": int(r.tt or 0) if r else 0,
                "cached_tokens": int(r.cached or 0) if r else 0,
                "cost": float(r.cost or 0) if r else 0.0,
            })

        # 按小时×模型聚合
        hbm_stmt = (
            select(
                hour_expr.label("hr"),
                model_field.label("m"),
                func.sum(CallLog.total_tokens).label("tt"),
                func.sum(CallLog.cost).label("cost"),
            )
            .where(CallLog.created_at >= day_start, CallLog.created_at < day_end)
            .group_by(hour_expr, model_field)
            .order_by(hour_expr)
        )
        hbm_rows = (await db.execute(hbm_stmt)).all()
        hourly_by_model = [
            {
                "hour": int(r.hr),
                "model": str(r.m or "未知"),
                "total_tokens": int(r.tt or 0),
                "cost": float(r.cost or 0),
            }
            for r in hbm_rows
        ]

    return {
        "days": days,
        "hourly": hourly,
        "hourly_by_model": hourly_by_model,
        "daily": daily,
        "daily_by_model": daily_by_model,
        "by_model": by_model,
        "by_key": by_key,
        "totals": {
            "calls": int(t.calls or 0),
            "total_tokens": total_tokens,
            "cached_tokens": cached_tokens,
            "cache_hit_rate": cache_hit_rate,
            "cost": float(t.cost or 0),
        },
    }
