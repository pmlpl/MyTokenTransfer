from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_auth
from app.core.security import InvalidToken, decrypt_value, encrypt_value, mask_key
from app.database import get_db
from app.models import ApiKey, KeyStatus, Provider
from app.schemas.api_key import (
    ApiKeyCreate,
    ApiKeyOut,
    ApiKeyReveal,
    ApiKeyUpdate,
    ExportRequest,
    ImportRequest,
    ImportResult,
)
from app.services.health_check import test_key, test_keys
from app.services.key import build_export_bundle, import_bundle

router = APIRouter(
    prefix="/keys",
    tags=["keys"],
    dependencies=[Depends(require_auth)],
)


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(
    provider_id: int | None = None,
    group_id: int | None = None,
    status_: KeyStatus | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKey]:
    stmt = select(ApiKey)
    if provider_id is not None:
        stmt = stmt.where(ApiKey.provider_id == provider_id)
    if group_id is not None:
        stmt = stmt.where(ApiKey.group_id == group_id)
    if status_ is not None:
        stmt = stmt.where(ApiKey.status == status_)
    result = await db.execute(stmt.order_by(ApiKey.id))
    return list(result.scalars())


@router.post("", response_model=ApiKeyOut, status_code=201)
async def create_key(
    body: ApiKeyCreate, db: AsyncSession = Depends(get_db)
) -> ApiKey:
    if not await db.get(Provider, body.provider_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "供应商不存在")
    key = ApiKey(
        provider_id=body.provider_id,
        group_id=body.group_id,
        account_label=body.account_label,
        name=body.name,
        key_value=encrypt_value(body.key_value),
        key_mask=mask_key(body.key_value),
        weight=body.weight,
        tags=body.tags,
        status=KeyStatus.active,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return key


@router.get("/{key_id}", response_model=ApiKeyOut)
async def get_key(key_id: int, db: AsyncSession = Depends(get_db)) -> ApiKey:
    key = await db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "密钥不存在")
    return key


@router.put("/{key_id}", response_model=ApiKeyOut)
async def update_key(
    key_id: int, body: ApiKeyUpdate, db: AsyncSession = Depends(get_db)
) -> ApiKey:
    key = await db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "密钥不存在")
    data = body.model_dump(exclude_unset=True)
    if "provider_id" in data and data["provider_id"] is not None:
        if not await db.get(Provider, data["provider_id"]):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "供应商不存在")
    if "key_value" in data and data["key_value"]:
        plaintext = data["key_value"]
        key.key_value = encrypt_value(plaintext)
        key.key_mask = mask_key(plaintext)
        data.pop("key_value", None)
    for k, v in data.items():
        setattr(key, k, v)
    await db.commit()
    await db.refresh(key)
    return key


@router.delete("/{key_id}", status_code=204)
async def delete_key(key_id: int, db: AsyncSession = Depends(get_db)) -> None:
    key = await db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "密钥不存在")
    await db.delete(key)
    await db.commit()


@router.post("/{key_id}/reveal", response_model=ApiKeyReveal)
async def reveal_key(key_id: int, db: AsyncSession = Depends(get_db)) -> ApiKeyReveal:
    """返回明文密钥（列表页揭示用，需登录态）。"""
    key = await db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "密钥不存在")
    return ApiKeyReveal(id=key.id, key_value=decrypt_value(key.key_value))


@router.post("/{key_id}/test")
async def test_single(key_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    key = await db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "密钥不存在")
    return await test_key(db, key)


@router.post("/test")
async def test_batch(
    provider_id: int | None = None,
    group_id: int | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    return await test_keys(db, provider_id=provider_id, group_id=group_id)


@router.post("/export")
async def export_keys(
    body: ExportRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """导出全部供应商/分组/密钥为加密包（口令加密，明文密钥不出现在响应里）。"""
    return await build_export_bundle(db, body.password)


@router.post("/import", response_model=ImportResult)
async def import_keys(
    body: ImportRequest, db: AsyncSession = Depends(get_db)
) -> ImportResult:
    """导入加密包：按 slug/name 调谐，重名跳过，明文重新本地加密落库。"""
    try:
        stats = await import_bundle(db, body.bundle, body.password)
    except InvalidToken:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "口令错误或导出包已损坏")
    return ImportResult(**stats)
