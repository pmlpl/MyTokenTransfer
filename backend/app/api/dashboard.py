from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_auth
from app.database import get_db
from app.models import ApiKey, CallLog, DailyUsage, KeyModelStatus, KeyStatus, Provider
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
            func.sum(DailyUsage.calls).label("calls"),
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
            "calls": int(r.calls or 0),
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

    # 近 24 小时滚动窗口：从 call_logs 按北京时间小时聚合，用于波形图。
    # 不用"今日 0 点起"——跨过午夜后已过去的桶只剩一两个，折线缩成孤点，
    # 昨晚的波形接不上；滚动窗口（含当前小时共 24 桶）让凌晨也有昨天的
    # 尾巴可连。label 给昨天的桶带"昨"前缀，is_today 供前端把 KPI 口径
    # 收窄回今日。
    hourly = []
    hourly_by_model = []
    if days == 1:
        bj_now = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)
        bj_cur_hour = bj_now.replace(minute=0, second=0, microsecond=0)
        win_start = bj_cur_hour - timedelta(hours=23) - timedelta(hours=8)  # UTC
        win_end = bj_cur_hour + timedelta(hours=1) - timedelta(hours=8)
        model_field = func.coalesce(func.nullif(CallLog.native_model, ""), CallLog.virtual_model)
        # 分桶必须带日期（HOUR 会把昨天 21 点和今天 21 点并成一桶）
        bj_dt = func.date_add(CallLog.created_at, text("INTERVAL 8 HOUR"))
        day_expr = func.date(bj_dt)
        hour_expr = func.hour(bj_dt)

        def bucket_label(bj_bucket: datetime) -> str:
            if bj_bucket.date() == bj_now.date():
                return bj_bucket.strftime("%H:00")
            return f"昨 {bj_bucket:%H:00}"

        # 按小时聚合
        h_stmt = (
            select(
                day_expr.label("d"),
                hour_expr.label("hr"),
                func.count(CallLog.id).label("calls"),
                func.sum(CallLog.prompt_tokens).label("pt"),
                func.sum(CallLog.completion_tokens).label("ct"),
                func.sum(CallLog.total_tokens).label("tt"),
                func.sum(CallLog.cached_tokens).label("cached"),
                func.sum(CallLog.cost).label("cost"),
            )
            .where(CallLog.created_at >= win_start, CallLog.created_at < win_end)
            .group_by(day_expr, hour_expr)
            .order_by(day_expr, hour_expr)
        )
        h_rows = (await db.execute(h_stmt)).all()
        h_map = {(r.d, int(r.hr)): r for r in h_rows}
        for i in range(24):
            bj_bucket = bj_cur_hour - timedelta(hours=23 - i)
            r = h_map.get((bj_bucket.date(), bj_bucket.hour))
            hourly.append({
                "hour": bj_bucket.hour,
                "label": bucket_label(bj_bucket),
                "is_today": bj_bucket.date() == bj_now.date(),
                "calls": int(r.calls or 0) if r else 0,
                "prompt_tokens": int(r.pt or 0) if r else 0,
                "completion_tokens": int(r.ct or 0) if r else 0,
                "total_tokens": int(r.tt or 0) if r else 0,
                "cached_tokens": int(r.cached or 0) if r else 0,
                "cost": float(r.cost or 0) if r else 0.0,
            })

        # 按小时×模型聚合（同一滚动窗口）
        hbm_stmt = (
            select(
                day_expr.label("d"),
                hour_expr.label("hr"),
                model_field.label("m"),
                func.count(CallLog.id).label("calls"),
                func.sum(CallLog.total_tokens).label("tt"),
                func.sum(CallLog.cost).label("cost"),
            )
            .where(CallLog.created_at >= win_start, CallLog.created_at < win_end)
            .group_by(day_expr, hour_expr, model_field)
            .order_by(day_expr, hour_expr)
        )
        hbm_rows = (await db.execute(hbm_stmt)).all()
        hourly_by_model = [
            {
                "hour": int(r.hr),
                "label": bucket_label(
                    datetime.combine(r.d, datetime.min.time()) + timedelta(hours=int(r.hr))
                ),
                "is_today": r.d == bj_now.date(),
                "model": str(r.m or "未知"),
                "calls": int(r.calls or 0),
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


@router.get("/key-model-statuses")
async def key_model_statuses(db: AsyncSession = Depends(get_db)) -> dict:
    """密钥×模型 细粒度状态列表（含密钥名、供应商名，供 Dashboard 展示）。"""
    rows = await db.execute(
        select(
            KeyModelStatus.id,
            KeyModelStatus.key_id,
            KeyModelStatus.provider_id,
            KeyModelStatus.model_name,
            KeyModelStatus.status,
            KeyModelStatus.last_error,
            KeyModelStatus.rate_limited_until,
            KeyModelStatus.last_tested_at,
            ApiKey.name.label("key_name"),
            ApiKey.key_mask,
            Provider.name.label("provider_name"),
        )
        .join(ApiKey, ApiKey.id == KeyModelStatus.key_id)
        .join(Provider, Provider.id == KeyModelStatus.provider_id)
        .order_by(KeyModelStatus.key_id, KeyModelStatus.model_name)
    )
    items = [
        {
            "id": r.id,
            "key_id": r.key_id,
            "key_name": r.key_name or r.key_mask or f"key#{r.key_id}",
            "provider_id": r.provider_id,
            "provider_name": r.provider_name,
            "model_name": r.model_name,
            "status": r.status.value if hasattr(r.status, "value") else r.status,
            "last_error": r.last_error,
            "rate_limited_until": r.rate_limited_until.isoformat() if r.rate_limited_until else None,
            "last_tested_at": r.last_tested_at.isoformat() if r.last_tested_at else None,
        }
        for r in rows.all()
    ]
    return {"items": items, "total": len(items)}
