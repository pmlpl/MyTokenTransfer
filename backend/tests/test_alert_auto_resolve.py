"""告警自动 resolve：key 恢复 active 时历史未解决告警自动消除。

修复前：exhausted/invalid 告警一旦写入永远 resolved=False，铃铛只增不减。
修复后：test_key / mark_key 把 key 从非 active 恢复为 active 时，自动 resolve
该 key 的所有未解决告警。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.models import AlertEvent, ApiKey, KeyStatus, Provider
from app.models.enums import ProbeKind
from app.relay.balancer import mark_key
from app.services.alerts import resolve_key_alerts
from app.services.health_check import ProbeResult
import app.services.health_check as health_check


async def _make_provider(db) -> Provider:
    p = Provider(name="OpenAI", slug="openai", base_url="https://api.openai.com",
                 probe_kind=ProbeKind.openai_models, models=["gpt-4o-mini"])
    db.add(p)
    await db.commit()
    return p


async def _make_key(db, provider, status=KeyStatus.exhausted) -> ApiKey:
    k = ApiKey(
        provider_id=provider.id,
        name="test-key",
        key_value="encrypted-dummy",
        key_mask="sk-…xxxx",
        status=status,
        weight=1,
    )
    db.add(k)
    await db.commit()
    await db.refresh(k)
    return k


async def _make_alerts(db, key_id, count=3) -> list[AlertEvent]:
    alerts = []
    for i in range(count):
        a = AlertEvent(
            type="key_exhausted",
            severity="critical",
            key_id=key_id,
            provider_id=1,
            message=f"历史告警 #{i}",
            resolved=False,
        )
        db.add(a)
        alerts.append(a)
    await db.commit()
    return alerts


async def _unresolved_count(db, key_id) -> int:
    rows = await db.execute(
        select(AlertEvent).where(AlertEvent.key_id == key_id, AlertEvent.resolved.is_(False))
    )
    return len(list(rows.scalars()))


# ---- 1. resolve_key_alerts 直接测试 ----

async def test_resolve_key_alerts_marks_all_unresolved(db_session):
    db = db_session
    p = await _make_provider(db)
    k = await _make_key(db, p)
    await _make_alerts(db, k.id, 3)

    assert await _unresolved_count(db, k.id) == 3

    resolved = await resolve_key_alerts(db, k.id)
    await db.commit()

    assert resolved == 3
    assert await _unresolved_count(db, k.id) == 0


async def test_resolve_key_alerts_only_affects_this_key(db_session):
    db = db_session
    p = await _make_provider(db)
    k1 = await _make_key(db, p, KeyStatus.exhausted)
    k2 = await _make_key(db, p, KeyStatus.exhausted)
    await _make_alerts(db, k1.id, 2)
    await _make_alerts(db, k2.id, 2)

    await resolve_key_alerts(db, k1.id)
    await db.commit()

    assert await _unresolved_count(db, k1.id) == 0
    assert await _unresolved_count(db, k2.id) == 2  # 其他 key 的告警不受影响


async def test_resolve_key_alerts_no_unresolved_returns_zero(db_session):
    db = db_session
    p = await _make_provider(db)
    k = await _make_key(db, p)
    # 没有任何告警
    resolved = await resolve_key_alerts(db, k.id)
    assert resolved == 0


# ---- 2. mark_key 恢复 active 时自动 resolve ----

async def test_mark_key_recovery_resolves_alerts(db_session):
    """mark_key 把 key 从 exhausted 标为 active → 历史告警自动 resolve。"""
    db = db_session
    p = await _make_provider(db)
    k = await _make_key(db, p, KeyStatus.exhausted)
    await _make_alerts(db, k.id, 3)

    await mark_key(k, KeyStatus.active, db, message="恢复")

    assert k.status == KeyStatus.active
    assert await _unresolved_count(db, k.id) == 0


async def test_mark_key_recovery_from_invalid_resolves_alerts(db_session):
    """mark_key 把 key 从 invalid 标为 active → 历史告警自动 resolve。"""
    db = db_session
    p = await _make_provider(db)
    k = await _make_key(db, p, KeyStatus.invalid)
    await _make_alerts(db, k.id, 2)

    await mark_key(k, KeyStatus.active, db)

    assert await _unresolved_count(db, k.id) == 0


async def test_mark_key_degradation_does_not_resolve(db_session):
    """mark_key 把 key 从 active 标为 exhausted → 不 resolve 历史告警。"""
    db = db_session
    p = await _make_provider(db)
    k = await _make_key(db, p, KeyStatus.active)
    # 先制造一条已存在的历史告警
    existing = await _make_alerts(db, k.id, 1)
    existing_id = existing[0].id

    await mark_key(k, KeyStatus.exhausted, db, message="额度用尽")

    assert k.status == KeyStatus.exhausted
    # 劣化方向不 resolve，原历史告警保持未解决
    rows = await db.execute(select(AlertEvent).where(AlertEvent.id == existing_id))
    assert rows.scalar().resolved is False


async def test_mark_key_active_to_active_no_resolve_call(db_session):
    """mark_key active→active 不触发 resolve（状态未变化）。"""
    db = db_session
    p = await _make_provider(db)
    k = await _make_key(db, p, KeyStatus.active)
    await _make_alerts(db, k.id, 1)

    await mark_key(k, KeyStatus.active, db)

    # before == active，不满足 before != active，不 resolve
    assert await _unresolved_count(db, k.id) == 1


# ---- 3. test_key 恢复 active 时自动 resolve ----

async def test_test_key_recovery_resolves_alerts(db_session):
    """test_key 探测成功使 key 从 exhausted 恢复 active → 历史告警自动 resolve。"""
    db = db_session
    p = await _make_provider(db)
    k = await _make_key(db, p, KeyStatus.exhausted)
    await _make_alerts(db, k.id, 3)

    ok_result = ProbeResult(True, 200, None, None, via="chat")
    with patch("app.services.health_check.decrypt_value", return_value="sk-plain"):
        with patch("app.services.health_check._probe", new=AsyncMock(return_value=ok_result)):
            result = await health_check.test_key(db, k)

    assert result["ok"] is True
    assert result["status"] == "active"
    assert result["state_changed"] is True
    assert await _unresolved_count(db, k.id) == 0


async def test_test_key_stays_exhausted_does_not_resolve(db_session):
    """test_key 探测仍失败（429 quota）→ key 保持 exhausted，告警不 resolve。"""
    db = db_session
    p = await _make_provider(db)
    k = await _make_key(db, p, KeyStatus.exhausted)
    await _make_alerts(db, k.id, 2)

    fail_result = ProbeResult(False, 429, None, "quota exceeded", body="quota exceeded")
    with patch("app.services.health_check.decrypt_value", return_value="sk-plain"):
        with patch("app.services.health_check._probe", new=AsyncMock(return_value=fail_result)):
            result = await health_check.test_key(db, k)

    assert result["ok"] is False
    assert result["status"] == "exhausted"
    # 未恢复，告警保持未解决
    assert await _unresolved_count(db, k.id) == 2


async def test_test_key_active_no_change_no_resolve(db_session):
    """test_key 探测成功且 key 原本就是 active → 不触发 resolve。"""
    db = db_session
    p = await _make_provider(db)
    k = await _make_key(db, p, KeyStatus.active)
    await _make_alerts(db, k.id, 1)

    ok_result = ProbeResult(True, 200, None, None, via="models")
    with patch("app.services.health_check.decrypt_value", return_value="sk-plain"):
        with patch("app.services.health_check._probe", new=AsyncMock(return_value=ok_result)):
            result = await health_check.test_key(db, k)

    assert result["state_changed"] is False
    # before == active，不满足恢复条件，不 resolve
    assert await _unresolved_count(db, k.id) == 1
