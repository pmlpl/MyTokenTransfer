from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_auth
from app.database import get_db
from app.models import Provider
from app.schemas.provider import ProviderCreate, ProviderOut, ProviderUpdate

router = APIRouter(
    prefix="/providers",
    tags=["providers"],
    dependencies=[Depends(require_auth)],
)


@router.get("", response_model=list[ProviderOut])
async def list_providers(db: AsyncSession = Depends(get_db)) -> list[Provider]:
    result = await db.execute(select(Provider).order_by(Provider.id))
    return list(result.scalars())


@router.post("", response_model=ProviderOut, status_code=201)
async def create_provider(
    body: ProviderCreate, db: AsyncSession = Depends(get_db)
) -> Provider:
    provider = Provider(**body.model_dump())
    db.add(provider)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name/slug 已存在")
    await db.refresh(provider)
    return provider


@router.get("/{provider_id}", response_model=ProviderOut)
async def get_provider(provider_id: int, db: AsyncSession = Depends(get_db)) -> Provider:
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "供应商不存在")
    return provider


@router.put("/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: int,
    body: ProviderUpdate,
    db: AsyncSession = Depends(get_db),
) -> Provider:
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "供应商不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(provider, k, v)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "name/slug 已存在")
    await db.refresh(provider)
    return provider


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(provider_id: int, db: AsyncSession = Depends(get_db)) -> None:
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "供应商不存在")
    await db.delete(provider)
    await db.commit()
