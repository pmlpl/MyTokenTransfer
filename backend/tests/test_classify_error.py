"""错误分类与 key 状态机：gateway._classify_error 的纯函数单测。

这是故障转移正确性的核心——错误码如何映射成 key 状态、是否可重试切换。
"""
from __future__ import annotations

from app.models.enums import KeyStatus
from app.relay.balancer import classify_error


def test_auth_error_marks_invalid_and_retryable():
    retry, mark, _ = classify_error(401, "unauthorized")
    assert retry is True
    assert mark == KeyStatus.invalid


def test_forbidden_marks_invalid():
    retry, mark, _ = classify_error(403, "forbidden")
    assert retry is True and mark == KeyStatus.invalid


def test_plain_429_marks_rate_limited():
    retry, mark, _ = classify_error(429, "Too Many Requests")
    assert retry is True
    assert mark == KeyStatus.rate_limited


def test_429_with_quota_marks_exhausted():
    # 429 但响应体含 quota/额度 关键词 → 真耗尽，冷却救不回来
    retry, mark, _ = classify_error(429, "insufficient_quota")
    assert retry is True
    assert mark == KeyStatus.exhausted


def test_400_insufficient_quota_marks_exhausted():
    retry, mark, _ = classify_error(400, "insufficient_quota")
    assert retry is True
    assert mark == KeyStatus.exhausted


def test_5xx_retryable_without_status_change():
    # 瞬时上游错误：换池内下一个 key，但不劣化该 key 状态
    retry, mark, _ = classify_error(500, "internal server error")
    assert retry is True
    assert mark is None

    retry, mark, _ = classify_error(503, "upstream unavailable")
    assert retry is True and mark is None


def test_400_bad_request_not_retryable():
    # 请求本身有问题（如参数错误），换 key 也没用
    retry, mark, _ = classify_error(400, "invalid model")
    assert retry is False
    assert mark is None
