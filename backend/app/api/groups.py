from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_auth
from app.database import get_db
from app.models import KeyGroup
from app.schemas.key_group import KeyGroupCreate, KeyGroupOut, KeyGroupUpdate

router = APIRouter(
    prefix="/groups",
    tags=["groups"],
    dependencies=[Depends(require_auth)],
)


@router.get("", response_model=list[KeyGroupOut])
async def list_groups(db: AsyncSession = Depends(get_db)) -> list[KeyGroup]:
    result = await db.execute(select(KeyGroup).order_by(KeyGroup.id))
    return list(result.scalars())


@router.post("", response_model=KeyGroupOut, status_code=201)
async def create_group(
    body: KeyGroupCreate, db: AsyncSession = Depends(get_db)
) -> KeyGroup:
    group = KeyGroup(**body.model_dump())
    db.add(group)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "分组名已存在")
    await db.refresh(group)
    return group


@router.put("/{group_id}", response_model=KeyGroupOut)
async def update_group(
    group_id: int,
    body: KeyGroupUpdate,
    db: AsyncSession = Depends(get_db),
) -> KeyGroup:
    group = await db.get(KeyGroup, group_id)
    if not group:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "分组不存在")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(group, k, v)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "分组名已存在")
    await db.refresh(group)
    return group


@router.delete("/{group_id}", status_code=204)
async def delete_group(group_id: int, db: AsyncSession = Depends(get_db)) -> None:
    group = await db.get(KeyGroup, group_id)
    if not group:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "分组不存在")
    await db.delete(group)
    await db.commit()
