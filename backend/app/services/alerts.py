from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AlertEvent
from app.models.enums import KeyStatus

# 触发告警的状态（劣化到这些即记录一条事件）
_ALERT_STATES = {KeyStatus.exhausted, KeyStatus.invalid}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _should_alert(before: KeyStatus, after: KeyStatus | None) -> bool:
    """仅当状态*转入* exhausted/invalid 时告警（避免重复刷屏）。"""
    return after in _ALERT_STATES and before != after


async def emit_key_alert(
    db: AsyncSession,
    *,
    key_id: int | None,
    provider_id: int | None,
    key_name: str | None,
    before: KeyStatus,
    after: KeyStatus | None,
    message: str,
) -> None:
    """密钥状态劣化时写一条告警事件。由调用方负责 commit。"""
    if not _should_alert(before, after):
        return
    severity = "critical" if after == KeyStatus.exhausted else "warning"
    label = key_name or f"key#{key_id}"
    db.add(
        AlertEvent(
            type=f"key_{after.value}",
            severity=severity,
            key_id=key_id,
            provider_id=provider_id,
            message=f"密钥「{label}」状态 {before.value} → {after.value}:{message}",
        )
    )


async def resolve_key_alerts(db: AsyncSession, key_id: int) -> int:
    """密钥恢复 active 时，自动 resolve 其所有未解决告警。

    返回被 resolve 的告警条数。由调用方负责 commit。
    """
    result = await db.execute(
        update(AlertEvent)
        .where(
            AlertEvent.key_id == key_id,
            AlertEvent.resolved.is_(False),
        )
        .values(resolved=True, resolved_at=_now_utc())
    )
    return result.rowcount or 0
