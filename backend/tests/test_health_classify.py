"""健康探测 _classify 接线层单测：ProbeResult → (状态, 错误, 冷却到期)。

这是曾经出 bug 的地方——健康探测把额度用尽(429 quota)误判成瞬时限流。
现在 _classify 复用 relay.errors.classify_error，确保与转发路径一致。
"""
from __future__ import annotations

from app.models.enums import KeyStatus
from app.services.health_check import ProbeResult, _classify


def _probe(ok, status, body="") -> ProbeResult:
    return ProbeResult(
        ok=ok,
        http_status=status,
        balance=None,
        error=f"HTTP {status}: {body}" if status else "net error",
        body=body,
    )


def test_ok_marks_active_no_cooldown():
    status, err, rlu = _classify(_probe(True, 200), KeyStatus.rate_limited)
    assert status == KeyStatus.active
    assert err is None and rlu is None


def test_429_with_quota_marks_exhausted_no_cooldown():
    # 额度用尽：不设冷却（5 小时窗口里冷却无意义），靠巡检恢复
    status, _err, rlu = _classify(
        _probe(False, 429, '{"error":{"message":"token plan entitlement exhausted"}}'),
        KeyStatus.active,
    )
    assert status == KeyStatus.exhausted
    assert rlu is None  # 关键：不是 rate_limited，无冷却


def test_plain_429_marks_rate_limited_with_cooldown():
    status, _err, rlu = _classify(_probe(False, 429, "Too Many Requests"), KeyStatus.active)
    assert status == KeyStatus.rate_limited
    assert rlu is not None  # 瞬时限流：设短冷却


def test_5xx_keeps_current_status():
    # 瞬时上游错误：不劣化 key 状态
    status, _err, rlu = _classify(_probe(False, 503, "upstream unavailable"), KeyStatus.active)
    assert status == KeyStatus.active
    assert rlu is None


def test_network_error_keeps_current_and_preserves_message():
    status, err, rlu = _classify(
        ProbeResult(False, None, None, "ConnectError: timed out"), KeyStatus.exhausted
    )
    assert status == KeyStatus.exhausted  # 保留原状态
    assert err == "ConnectError: timed out"
    assert rlu is None
