from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_auth
from app.database import get_db
from app.models import AlertEvent
from app.schemas.alert import AlertListResponse, AlertOut

router = APIRouter(
    prefix="/alerts",
    tags=["alerts"],
    dependencies=[Depends(require_auth)],
)


@router.get("")
async def list_alerts(
    unresolved: bool | None = Query(None, description="仅未解决"),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> AlertListResponse:
    stmt = select(AlertEvent)
    if unresolved is True:
        stmt = stmt.where(AlertEvent.resolved.is_(False))
    stmt = stmt.order_by(AlertEvent.resolved.asc(), AlertEvent.created_at.desc()).limit(limit)

    rows = (await db.execute(stmt)).scalars().all()

    total_q = select(func.count(AlertEvent.id))
    unresolved_q = select(func.count(AlertEvent.id)).where(AlertEvent.resolved.is_(False))
    total = (await db.execute(total_q)).scalar_one()
    unresolved_total = (await db.execute(unresolved_q)).scalar_one()

    return AlertListResponse(
        items=[AlertOut.model_validate(r) for r in rows],
        total=total,
        unresolved=unresolved_total,
    )


@router.post("/{alert_id}/resolve", response_model=AlertOut)
async def resolve_alert(alert_id: int, db: AsyncSession = Depends(get_db)) -> AlertEvent:
    a = await db.get(AlertEvent, alert_id)
    if not a:
        from fastapi import HTTPException, status

        raise HTTPException(status.HTTP_404_NOT_FOUND, "告警事件不存在")
    if not a.resolved:
        a.resolved = True
        a.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()
        await db.refresh(a)
    return a
