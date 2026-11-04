"""域名级网络熔断单测。

背景：上游按 SNI 间歇性阻断（ConnectTimeout / ConnectError / TLS 中断）时，
同池所有 key 指向同一域名，换 key 重试毫无意义。原来 6 把 key × 3 次重试
× 10 秒 connect timeout = 最坏 180 秒才返回 502，客户端表现为「卡住不动」。
"""
from __future__ import annotations

import time

import pytest

from app.relay import gateway as gw

URL = "https://token.sensenova.cn/v1/chat/completions"


@pytest.fixture(autouse=True)
def _clear_breaker():
    gw._domain_blocked_until.clear()
    yield
    gw._domain_blocked_until.clear()


def test_domain_of_extracts_host():
    assert gw._domain_of(URL) == "token.sensenova.cn"
    assert gw._domain_of("https://api.deepseek.com/v1/x") == "api.deepseek.com"


def test_fresh_domain_is_not_cooling():
    assert gw._domain_is_cooling(URL) is False


def test_trip_marks_domain_cooling():
    gw._trip_domain_breaker(URL)
    assert gw._domain_is_cooling(URL) is True


def test_breaker_only_affects_same_domain():
    gw._trip_domain_breaker(URL)
    other = "https://api.deepseek.com/v1/chat/completions"
    assert gw._domain_is_cooling(other) is False


def test_breaker_expires_after_cooldown(monkeypatch):
    gw._trip_domain_breaker(URL)
    assert gw._domain_is_cooling(URL) is True
    # 把熔断截止时间拨到过去，模拟冷却结束
    gw._domain_blocked_until[gw._domain_of(URL)] = time.monotonic() - 1
    assert gw._domain_is_cooling(URL) is False


def test_connect_timeout_is_short_enough():
    """connect timeout 必须远小于整体 read timeout。

    实测 10 秒 × 3 次重试 × 6 把 key 会让网络故障的请求挂 180 秒。
    """
    assert gw._NETWORK_CONNECT_TIMEOUT <= 6.0
    assert gw._NETWORK_CONNECT_TIMEOUT < gw._NETWORK_BREAKER_SEC


def test_breaker_cooldown_is_reasonable():
    """熔断时长要够长以覆盖抖动，又不能长到把瞬时故障放大成长期不可用。"""
    assert 10.0 <= gw._NETWORK_BREAKER_SEC <= 60.0
