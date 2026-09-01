from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_auth
from app.database import get_db
from app.models import ModelPricing
from app.schemas.pricing import PricingCreate, PricingOut, PricingUpdate

router = APIRouter(
    prefix="/pricing",
    tags=["pricing"],
    dependencies=[Depends(require_auth)],
)


@router.get("", response_model=list[PricingOut])
async def list_pricing(db: AsyncSession = Depends(get_db)) -> list[ModelPricing]:
    result = await db.execute(select(ModelPricing).order_by(ModelPricing.model_name))
    return list(result.scalars())


@router.post("", response_model=PricingOut, status_code=201)
async def create_pricing(body: PricingCreate, db: AsyncSession = Depends(get_db)) -> ModelPricing:
    p = ModelPricing(**body.model_dump())
    db.add(p)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "model_name 已存在")
    await db.refresh(p)
    return p


@router.put("/{pricing_id}", response_model=PricingOut)
async def update_pricing(
    pricing_id: int, body: PricingUpdate, db: AsyncSession = Depends(get_db)
) -> ModelPricing:
    p = await db.get(ModelPricing, pricing_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "计价记录不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    await db.commit()
    await db.refresh(p)
    return p


@router.delete("/{pricing_id}", status_code=204)
async def delete_pricing(pricing_id: int, db: AsyncSession = Depends(get_db)) -> None:
    p = await db.get(ModelPricing, pricing_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "计价记录不存在")
    await db.delete(p)
    await db.commit()
