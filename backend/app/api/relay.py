from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_gateway
from app.database import get_db
from app.models import Channel
from app.relay.gateway import route_chat_completions

router = APIRouter(
    prefix="/v1",
    tags=["relay"],
    dependencies=[Depends(require_gateway)],
)


async def _load_channel(model: str, db: AsyncSession) -> Channel | None:
    return await db.scalar(
        select(Channel).where(Channel.virtual_model == model, Channel.is_active.is_(True))
    )


@router.post("/chat/completions")
async def chat_completions(request: Request, db: AsyncSession = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请求体不是合法 JSON")
    model = body.get("model")
    if not model:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "缺少 model 字段")
    channel = await _load_channel(model, db)
    if not channel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"未找到 model '{model}' 对应的通道")
    return await route_chat_completions(channel, body, db)


@router.get("/models")
async def list_models(db: AsyncSession = Depends(get_db)):
    """OpenAI 兼容的模型列表（来源：所有启用的通道）。"""
    rows = await db.execute(select(Channel).where(Channel.is_active.is_(True)).order_by(Channel.id))
    data = [
        {"id": c.virtual_model, "object": "model", "owned_by": "mytokentransfer"}
        for c in rows.scalars()
    ]
    return {"object": "list", "data": data}
