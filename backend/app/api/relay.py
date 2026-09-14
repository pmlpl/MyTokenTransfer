from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_gateway
from app.database import get_db
from app.models import Channel, ModelPricing
from app.relay.gateway import route_chat_completions, route_embeddings, route_image_generations

router = APIRouter(
    prefix="/v1",
    tags=["relay"],
    dependencies=[Depends(require_gateway)],
)


async def _load_channel(model: str, db: AsyncSession) -> Channel | None:
    return await db.scalar(
        select(Channel).where(Channel.virtual_model == model, Channel.is_active.is_(True))
    )


_IMAGE_MODEL_HINTS = ("image", "img", "t2i", "dall-e", "sora", "flux", "midjourney")


def _is_image_channel(channel: Channel) -> bool:
    """判断该渠道是否为生图渠道（模型名/渠道名含图片特征词）。

    生图模型（如 agnes-image-2.5-flash）走 /v1/images/generations；
    agent 客户端只调 /v1/chat/completions 时，由这里识别并自动分发。
    """
    name = f"{channel.virtual_model} {channel.name}".lower()
    return any(h in name for h in _IMAGE_MODEL_HINTS)


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
    # 生图渠道自动分发：agent 只调 chat/completions 时，转 images/generations
    # 并把图片 URL 包装成聊天响应返回（多模态内容），客户端无需改任何配置
    if _is_image_channel(channel):
        return await route_image_generations(channel, body, db, wrap_as_chat=True)
    return await route_chat_completions(channel, body, db)


@router.post("/embeddings")
async def embeddings(request: Request, db: AsyncSession = Depends(get_db)):
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
    return await route_embeddings(channel, body, db)


@router.post("/images/generations")
async def image_generations(request: Request, db: AsyncSession = Depends(get_db)):
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
    return await route_image_generations(channel, body, db)


@router.get("/models")
async def list_models(db: AsyncSession = Depends(get_db)):
    """OpenAI 兼容的模型列表（来源：所有启用的通道），并附上价格表里的上下文长度。"""
    rows = await db.execute(select(Channel).where(Channel.is_active.is_(True)).order_by(Channel.id))
    channels = rows.scalars().all()
    prices = await db.execute(select(ModelPricing))
    ctx = {p.model_name: p.context_length for p in prices.scalars()}
    data = [
        {
            "id": c.virtual_model,
            "object": "model",
            "owned_by": "mytokentransfer",
            "context_length": ctx.get(c.virtual_model, 0),
        }
        for c in channels
    ]
    return {"object": "list", "data": data}
