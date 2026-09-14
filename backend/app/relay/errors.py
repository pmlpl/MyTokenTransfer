"""上游错误 → key 状态的统一分类。

转发路径(gateway)和健康探测(health_check)都调这一个函数，
避免两处分类逻辑漂移（曾经健康探测把额度用尽误判成瞬时限流）。

依据：商汤 SenseNova Token Plan 为"每 5 小时 N 次调用"额度，
用尽时返回 429 + quota_exceeded_error / entitlement exhausted，
属于额度用尽(exhausted)而非瞬时限流(rate_limited)。

注意：TPM/RPM 速率限制（如 "inference tpm exhausted"）是瞬时限流，
应判 rate_limited（60秒冷却自动恢复），不能误判为 exhausted。
因此速率限制关键词的优先级高于额度关键词。
"""
from __future__ import annotations

from app.models import KeyStatus

# 瞬时限流关键词（命中即判 rate_limited，60秒冷却后自动恢复）
# 必须优先于额度关键词判断，避免 "tpm exhausted" 被误判为额度耗尽
_RATE_LIMIT_KEYWORDS = (
    "tpm",          # tokens per minute
    "rpm",          # requests per minute
    "rpd",          # requests per day
    "rate limit",
    "rate-limit",
    "ratelimit",
    "too many requests",
    "throttl",      # throttle / throttling
    "concurrency",  # 并发数超限
)

# 额度/余额用尽的关键词（出现即判 exhausted，冷却救不回来，靠定时巡检恢复）
# 注意：不包含单独的 "exhausted"，因为 "tpm exhausted" 是速率限制而非额度耗尽
_QUOTA_KEYWORDS = ("quota", "entitlement", "余额", "insufficient", "exceeded")

# 模型池级额度耗尽特征：转售商按 key×模型 划分积分池（实测同一把 key 上
# deepseek 429 而 glm-5.2 同秒成功），某模型的池子用尽返回该文案。
# 此时密钥本身（鉴权、其他模型的池）仍健康，不应标 exhausted 污染全局状态。
_MODEL_POOL_QUOTA_MARKERS = ("allocated quota exceeded",)


def is_model_pool_quota(body_text: str) -> bool:
    """判断 429 正文是否为「模型池级」额度耗尽（而非密钥级余额用尽）。"""
    text = (body_text or "").lower()
    return any(m in text for m in _MODEL_POOL_QUOTA_MARKERS)


def classify_error(status_code: int, body_text: str) -> tuple[bool, KeyStatus | None, str]:
    """返回 (是否可重试/切换, 标记的状态, 错误描述)。

    status==None 的网络层错误不应走这里（由调用方自行保留原状态）。
    """
    text = (body_text or "").lower()
    snippet = (body_text or "")[:200]
    if status_code in (401, 403):
        # 模型访问权限不足（如 "no access to model"）：密钥本身有效，
        # 只是该模型不可用/名称不对，不应标 invalid 污染全局密钥状态。
        if "no access to model" in text or "model access" in text:
            return False, None, f"HTTP {status_code}: {snippet}"
        return True, KeyStatus.invalid, f"HTTP {status_code}: {snippet}"
    if status_code == 429:
        # 优先判瞬时限流：TPM/RPM 等速率限制是短时的，60秒冷却后恢复
        if any(k in text for k in _RATE_LIMIT_KEYWORDS):
            return True, KeyStatus.rate_limited, f"HTTP 429 rate limit: {snippet}"
        # 再判额度耗尽：quota/entitlement 等是周期额度，靠巡检恢复
        if any(k in text for k in _QUOTA_KEYWORDS):
            return True, KeyStatus.exhausted, f"HTTP 429 quota: {snippet}"
        return True, KeyStatus.rate_limited, f"HTTP 429: {snippet}"
    if status_code == 400 and any(k in text for k in _QUOTA_KEYWORDS):
        return True, KeyStatus.exhausted, f"HTTP 400 quota: {snippet}"
    if status_code >= 500:
        return True, None, f"HTTP {status_code}: {snippet}"
    # 400 等客户端错误：不可重试（请求本身有问题，换 key 也没用）
    return False, None, f"HTTP {status_code}: {snippet}"
