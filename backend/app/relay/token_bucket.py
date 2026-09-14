"""令牌桶限流：按 (key_id, native_model) 维度事前控速。

与现有"事后冷却"（mark_model_quota_limited / key_model_statuses）互补：
- 事后冷却：已经被上游 429 后才标记，冷却期内跳过
- 令牌桶：请求发出前检查速率，无令牌则换下一把 key，从源头减少 429

设计原则：
1. 单进程 uvicorn，无锁（事件循环内无 await 的算术操作不存在竞态）
2. 令牌桶永不导致硬失败——全部 key 无令牌时由 gateway 短暂等待后放行
3. 速率可按模型覆盖，默认值保守（免费层远低于官方 5 RPS 上限）
"""
from __future__ import annotations

import os
import time

import structlog

log = structlog.get_logger("relay")

# ── 速率配置 ──────────────────────────────────────────────────────────
# 默认每把 key 每分钟请求数（RPM）。保守值：免费层避免触发 "429 rpm exhausted"。
# 官方接口上限 5 RPS = 300 RPM，免费层实际 RPM 限额未公布，取 20 留足余量。
_DEFAULT_RPM = int(os.environ.get("MTT_TOKEN_BUCKET_RPM", "20"))

# 突发容量：桶满时允许连续发出的请求数（不用等补充）。
_DEFAULT_BURST = int(os.environ.get("MTT_TOKEN_BUCKET_BURST", "10"))

# 按原生模型名覆盖 RPM，格式："glm-5.2:10,deepseek-v4-flash:15"
_MODEL_RPM_ENV = os.environ.get("MTT_TOKEN_BUCKET_MODEL_RPM", "")
_MODEL_RPM_OVERRIDES: dict[str, int] = {}
for pair in _MODEL_RPM_ENV.split(","):
    pair = pair.strip()
    if ":" in pair:
        name, val = pair.split(":", 1)
        try:
            _MODEL_RPM_OVERRIDES[name.strip()] = int(val.strip())
        except ValueError:
            pass


def _rpm_for(model_name: str) -> int:
    return _MODEL_RPM_OVERRIDES.get(model_name, _DEFAULT_RPM)


# ── 令牌桶 ────────────────────────────────────────────────────────────
class _Bucket:
    __slots__ = ("rate", "capacity", "tokens", "last_refill")

    def __init__(self, rpm: int, capacity: int):
        self.rate = rpm / 60.0          # tokens / second
        self.capacity = float(capacity)
        self.tokens = float(capacity)   # 初始满桶
        self.last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_refill = now

    def acquire(self) -> bool:
        """尝试消耗 1 个令牌。返回 False 表示桶空（应跳过此 key）。"""
        self._refill()
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def available(self) -> float:
        """当前可用令牌数（含应补充未补充的）。"""
        self._refill()
        return self.tokens

    def seconds_until(self, tokens: float = 1.0) -> float:
        """还需多少秒才能攒够指定令牌数（用于等待决策）。"""
        self._refill()
        if self.tokens >= tokens:
            return 0.0
        return (tokens - self.tokens) / self.rate if self.rate > 0 else float("inf")


# 全局桶注册表：(key_id, native_model) -> _Bucket
_buckets: dict[tuple[int, str], _Bucket] = {}


def _get_bucket(key_id: int, model_name: str) -> _Bucket:
    bkey = (key_id, model_name)
    bucket = _buckets.get(bkey)
    if bucket is None:
        rpm = _rpm_for(model_name)
        bucket = _Bucket(rpm=rpm, capacity=_DEFAULT_BURST)
        _buckets[bkey] = bucket
        log.info("token_bucket_created", key_id=key_id, model=model_name,
                 rpm=rpm, burst=_DEFAULT_BURST)
    return bucket


def acquire_token(key_id: int, model_name: str) -> bool:
    """尝试为 (key_id, model_name) 获取一个令牌。

    返回 True 表示已消耗令牌、可以发请求；
    返回 False 表示桶空、应跳过此 key 换下一把。
    """
    return _get_bucket(key_id, model_name).acquire()


def available_tokens(key_id: int, model_name: str) -> float:
    """查询当前可用令牌数（不消耗）。用于日志/调试。"""
    return _get_bucket(key_id, model_name).available()


def wait_seconds_for_any(key_ids: list[int], model_name: str) -> float:
    """给定一组 key，返回任意一把攒够 1 个令牌所需的最短秒数。

    用于 gateway 在全部 key 桶空时决定等待多久。
    """
    if not key_ids:
        return 0.0
    return min(_get_bucket(kid, model_name).seconds_until(1.0) for kid in key_ids)
