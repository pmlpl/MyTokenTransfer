from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import settings
from app.core.deps import require_auth

router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(require_auth)],
)


@router.get("")
async def get_settings() -> dict:
    return {"gateway_token": settings.GATEWAY_TOKEN}
