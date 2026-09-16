from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.cache import cache_scan_delete
from app.core.deps import require_auth
from app.database import get_db
from app.models import ApiKey, Channel, ChannelKey, ChannelTier, ModelMapping, Provider
from app.services.health_check import test_channel
from app.schemas.channel import (
    ChannelCreate,
    ChannelOut,
    ChannelTierOut,
    ChannelUpdate,
    ModelMappingOut,
)

router = APIRouter(
    prefix="/channels",
    tags=["channels"],
    dependencies=[Depends(require_auth)],
)


def _serialize(channel: Channel) -> ChannelOut:
    tiers = []
    for t in channel.tiers:
        keys = [
            {
                "id": ck.id,
                "key_id": ck.key_id,
                "key_name": ck.api_key.name if ck.api_key else None,
                "key_mask": ck.api_key.key_mask if ck.api_key else None,
                "status": ck.api_key.status.value if ck.api_key and ck.api_key.status else None,
                "weight": ck.weight,
                "priority": ck.priority,
            }
            for ck in t.keys
        ]
        tiers.append(
            ChannelTierOut(
                id=t.id,
                order=t.order,
                provider_id=t.provider_id,
                provider_name=t.provider.name if t.provider else None,
                keys=keys,
            )
        )
    # 映射：从该通道所有 tier 涉及的 provider 聚合
    mappings = []
    # 直接查该通道相关的 ModelMapping（按 virtual_model）
    return ChannelOut(
        id=channel.id,
        name=channel.name,
        virtual_model=channel.virtual_model,
        strategy=channel.strategy.value,
        max_retries=channel.max_retries,
        is_active=channel.is_active,
        note=channel.note,
        tiers=tiers,
        mappings=mappings,
        created_at=channel.created_at,
        updated_at=channel.updated_at,
    )


async def _load_mappings(channel: Channel, db: AsyncSession) -> list[ModelMappingOut]:
    rows = await db.execute(
        select(ModelMapping, Provider).join(Provider, ModelMapping.provider_id == Provider.id).where(
            ModelMapping.virtual_model == channel.virtual_model
        )
    )
    out = []
    for mm, prov in rows:
        out.append(
            ModelMappingOut(
                id=mm.id,
                provider_id=mm.provider_id,
                provider_name=prov.name,
                native_model=mm.native_model,
            )
        )
    return out


async def _serialize_full(channel: Channel, db: AsyncSession) -> ChannelOut:
    out = _serialize(channel)
    out.mappings = await _load_mappings(channel, db)
    return out


def _full_channel_stmt():
    """统一用 selectinload 预取嵌套关系，避免异步下隐式懒加载。"""
    return select(Channel).options(
        selectinload(Channel.tiers).selectinload(ChannelTier.keys).selectinload(ChannelKey.api_key),
        selectinload(Channel.tiers).selectinload(ChannelTier.provider),
    )


async def _load_full(channel_id: int, db: AsyncSession) -> Channel | None:
    return await db.scalar(_full_channel_stmt().where(Channel.id == channel_id))


@router.get("", response_model=list[ChannelOut])
async def list_channels(db: AsyncSession = Depends(get_db)) -> list[ChannelOut]:
    result = await db.execute(_full_channel_stmt().order_by(Channel.id))
    channels = list(result.scalars())
    return [await _serialize_full(c, db) for c in channels]


@router.post("", response_model=ChannelOut, status_code=201)
async def create_channel(body: ChannelCreate, db: AsyncSession = Depends(get_db)) -> ChannelOut:
    if await db.scalar(select(Channel).where(Channel.virtual_model == body.virtual_model)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "virtual_model 已存在")
    channel = Channel(
        name=body.name,
        virtual_model=body.virtual_model,
        strategy=body.strategy,
        max_retries=body.max_retries,
        is_active=body.is_active,
        note=body.note,
    )
    db.add(channel)
    await db.flush()

    for idx, t_in in enumerate(body.tiers):
        order = t_in.order if t_in.order is not None else idx
        if not await db.get(Provider, t_in.provider_id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"供应商 {t_in.provider_id} 不存在")
        tier = ChannelTier(channel_id=channel.id, order=order, provider_id=t_in.provider_id)
        db.add(tier)
        await db.flush()
        for k_in in t_in.keys:
            if not await db.get(ApiKey, k_in.key_id):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"密钥 {k_in.key_id} 不存在")
            db.add(ChannelKey(channel_tier_id=tier.id, key_id=k_in.key_id, weight=k_in.weight, priority=k_in.priority))

    for m_in in body.mappings:
        if not await db.get(Provider, m_in.provider_id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"供应商 {m_in.provider_id} 不存在")
        db.add(ModelMapping(virtual_model=channel.virtual_model, provider_id=m_in.provider_id, native_model=m_in.native_model))

    await db.commit()
    await cache_scan_delete(f"mm:{channel.virtual_model}:*")
    channel = await _load_full(channel.id, db)
    return await _serialize_full(channel, db)


@router.get("/{channel_id}", response_model=ChannelOut)
async def get_channel(channel_id: int, db: AsyncSession = Depends(get_db)) -> ChannelOut:
    channel = await _load_full(channel_id, db)
    if not channel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "通道不存在")
    return await _serialize_full(channel, db)


@router.put("/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: int, body: ChannelUpdate, db: AsyncSession = Depends(get_db)
) -> ChannelOut:
    channel = await _load_full(channel_id, db)
    if not channel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "通道不存在")

    if body.name is not None:
        channel.name = body.name
    if body.strategy is not None:
        channel.strategy = body.strategy
    if body.max_retries is not None:
        channel.max_retries = body.max_retries
    if body.is_active is not None:
        channel.is_active = body.is_active
    if body.note is not None:
        channel.note = body.note

    if body.tiers is not None:
        # ORM 方式整体替换 tiers：触发 delete-orphan 级联并同步会话状态
        for tier in list(channel.tiers):
            await db.delete(tier)
        await db.flush()
        for idx, t_in in enumerate(body.tiers):
            order = t_in.order if t_in.order is not None else idx
            tier = ChannelTier(channel_id=channel.id, order=order, provider_id=t_in.provider_id)
            db.add(tier)
            await db.flush()
            for k_in in t_in.keys:
                db.add(ChannelKey(channel_tier_id=tier.id, key_id=k_in.key_id, weight=k_in.weight, priority=k_in.priority))

    if body.mappings is not None:
        await db.execute(delete(ModelMapping).where(ModelMapping.virtual_model == channel.virtual_model))
        for m_in in body.mappings:
            db.add(ModelMapping(virtual_model=channel.virtual_model, provider_id=m_in.provider_id, native_model=m_in.native_model))

    await db.commit()
    await cache_scan_delete(f"mm:{channel.virtual_model}:*")
    channel = await _load_full(channel.id, db)
    return await _serialize_full(channel, db)


@router.post("/{channel_id}/test")
async def test_channel_endpoint(channel_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """按渠道探测：用该渠道实际对应的原生模型名测试其下所有密钥，结果写入 key_model_statuses。"""
    channel = await _load_full(channel_id, db)
    if not channel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "通道不存在")
    return await test_channel(db, channel)


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(channel_id: int, db: AsyncSession = Depends(get_db)) -> None:
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "通道不存在")
    virtual_model = channel.virtual_model
    await db.delete(channel)
    await db.commit()
    await cache_scan_delete(f"mm:{virtual_model}:*")
