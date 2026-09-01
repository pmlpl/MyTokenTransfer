"""上游错误 → key 状态的统一分类。

转发路径(gateway)和健康探测(health_check)都调这一个函数，
避免两处分类逻辑漂移（曾经健康探测把额度用尽误判成瞬时限流）。

依据：商汤 SenseNova Token Plan 为"每 5 小时 N 次调用"额度，
用尽时返回 429 + quota_exceeded_error / entitlement exhausted，
属于额度用尽(exhausted)而非瞬时限流(rate_limited)。
"""
from __future__ import annotations

from app.models import KeyStatus

# 额度/余额用尽的关键词（出现即判 exhausted，冷却救不回来，靠定时巡检恢复）
_QUOTA_KEYWORDS = ("quota", "entitlement", "余额", "exhausted", "insufficient", "exceeded")


def classify_error(status_code: int, body_text: str) -> tuple[bool, KeyStatus | None, str]:
    """返回 (是否可重试/切换, 标记的状态, 错误描述)。

    status==None 的网络层错误不应走这里（由调用方自行保留原状态）。
    """
    text = (body_text or "").lower()
    snippet = (body_text or "")[:200]
    if status_code in (401, 403):
        return True, KeyStatus.invalid, f"HTTP {status_code}: {snippet}"
    if status_code == 429:
        if any(k in text for k in _QUOTA_KEYWORDS):
            return True, KeyStatus.exhausted, f"HTTP 429 quota: {snippet}"
        return True, KeyStatus.rate_limited, f"HTTP 429: {snippet}"
    if status_code == 400 and any(k in text for k in _QUOTA_KEYWORDS):
        return True, KeyStatus.exhausted, f"HTTP 400 quota: {snippet}"
    if status_code >= 500:
        return True, None, f"HTTP {status_code}: {snippet}"
    # 400 等客户端错误：不可重试（请求本身有问题，换 key 也没用）
    return False, None, f"HTTP {status_code}: {snippet}"
