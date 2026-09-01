from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_auth
from app.database import get_db
from app.models import CallLog

router = APIRouter(
    prefix="/logs",
    tags=["logs"],
    dependencies=[Depends(require_auth)],
)


@router.get("")
async def list_logs(
    limit: int = Query(100, le=500),
    offset: int = 0,
    channel_id: int | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict:
    stmt = select(CallLog).order_by(desc(CallLog.id))
    if channel_id is not None:
        stmt = stmt.where(CallLog.channel_id == channel_id)
    if status is not None:
        stmt = stmt.where(CallLog.status == status)
    stmt = stmt.limit(limit).offset(offset)
    rows = await db.execute(stmt)
    items = []
    for r in rows.scalars():
        items.append(
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "channel_id": r.channel_id,
                "tier_order": r.tier_order,
                "provider_id": r.provider_id,
                "key_id": r.key_id,
                "virtual_model": r.virtual_model,
                "native_model": r.native_model,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "total_tokens": r.total_tokens,
                "cost": float(r.cost),
                "latency_ms": r.latency_ms,
                "status": r.status,
                "error": r.error,
                "stream": r.stream,
            }
        )
    return {"items": items, "limit": limit, "offset": offset}
