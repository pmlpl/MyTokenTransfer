from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_auth
from app.database import get_db
from app.models import ApiKey, CallLog, KeyStatus, Provider

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
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    base = select(CallLog).where(CallLog.created_at >= since)

    # 按日聚合
    daily_stmt = (
        select(
            func.date(CallLog.created_at).label("d"),
            func.count().label("calls"),
            func.sum(CallLog.prompt_tokens).label("pt"),
            func.sum(CallLog.completion_tokens).label("ct"),
            func.sum(CallLog.total_tokens).label("tt"),
            func.sum(CallLog.cost).label("cost"),
        )
        .where(CallLog.created_at >= since)
        .group_by(func.date(CallLog.created_at))
        .order_by(func.date(CallLog.created_at))
    )
    daily_rows = (await db.execute(daily_stmt)).all()
    daily = [
        {
            "date": str(r.d),
            "calls": int(r.calls or 0),
            "prompt_tokens": int(r.pt or 0),
            "completion_tokens": int(r.ct or 0),
            "total_tokens": int(r.tt or 0),
            "cost": float(r.cost or 0),
        }
        for r in daily_rows
    ]

    # 按模型聚合
    model_field = func.coalesce(func.nullif(CallLog.native_model, ""), CallLog.virtual_model)
    model_stmt = (
        select(
            model_field.label("m"),
            func.count().label("calls"),
            func.sum(CallLog.total_tokens).label("tt"),
            func.sum(CallLog.cost).label("cost"),
        )
        .where(CallLog.created_at >= since)
        .group_by(model_field)
        .order_by(func.sum(CallLog.cost).desc())
    )
    model_rows = (await db.execute(model_stmt)).all()
    by_model = [
        {"model": str(r.m or "未知"), "calls": int(r.calls or 0), "total_tokens": int(r.tt or 0), "cost": float(r.cost or 0)}
        for r in model_rows
    ]

    # 汇总
    totals_stmt = select(
        func.count().label("calls"),
        func.sum(CallLog.total_tokens).label("tt"),
        func.sum(CallLog.cost).label("cost"),
    ).where(CallLog.created_at >= since)
    t = (await db.execute(totals_stmt)).one()
    return {
        "days": days,
        "daily": daily,
        "by_model": by_model,
        "totals": {
            "calls": int(t.calls or 0),
            "total_tokens": int(t.tt or 0),
            "cost": float(t.cost or 0),
        },
    }
