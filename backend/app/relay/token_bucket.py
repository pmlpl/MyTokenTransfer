"""令牌桶限流：按 (key_id, native_model) 维度事前控速。

双层令牌桶：
- RPM 桶（按 key×模型）：每分钟请求数，扣 1 个令牌/请求
- TPM 桶（按 账号×模型）：每分钟 token 数，按预估 token 数扣减

TPM 桶解决 DeepSeek-Flash 等快生成模型的问题：单次请求可烧上万
token，RPM 桶只扣 1 无法感知，导致 TPM 配额在上游被打满后才开始
事后冷却。TPM 桶在请求发出前就按预估消耗量扣减，桶空则换有余量的
账号，从源头减少注定 429 的请求。

与现有"事后冷却"（mark_model_quota_limited / key_model_statuses）互补：
- 事后冷却：已经被上游 429 后才标记，冷却期内跳过
- 令牌桶：请求发出前检查速率，无令牌则换下一把 key，从源头减少 429

设计原则：
1. 单进程 uvicorn，无锁（事件循环内无 await 的算术操作不存在竞态）
2. 令牌桶永不导致硬失败——全部 key 无令牌时由 gateway 顾问放行（不硬阻）
3. 速率可按模型覆盖，默认值保守（免费层远低于官方 5 RPS 上限）
4. TPM 预估是粗估（prompt 字符数/3 + max_tokens），响应返回后按
   实际 usage 修正，避免长期偏差
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


# ── TPM（每分钟 token 数）配置 ──────────────────────────────────────
# 上游按账号×模型限 TPM（tokens per minute）。DeepSeek 等快生成模型
# 单次请求可烧上万 token，RPM 桶无法感知 → 需要按 token 消耗控速。
# 0 = 关闭 TPM 追踪（仅用 RPM，向后兼容）。
# 商汤 SenseNova 的 TPM 上限未公开（登录后才可见），取 30000 留余量：
# GLM-5.2 正常 agent 用量远低于此值不受影响；DeepSeek-Flash 可按需调低。
_DEFAULT_TPM = int(os.environ.get("MTT_TOKEN_BUCKET_TPM", "30000"))

# TPM 突发容量比例：桶满时允许一次性消耗的 token 数占 TPM 的比例。
# 0.5 = 15000（对 30000 TPM），允许 2-3 次大请求后开始节流。
_DEFAULT_TPM_BURST_RATIO = float(os.environ.get("MTT_TOKEN_BUCKET_TPM_BURST_RATIO", "0.5"))

# 按原生模型覆盖 TPM，格式："deepseek-flash:10000,glm-5.2:60000"
_MODEL_TPM_ENV = os.environ.get("MTT_TOKEN_BUCKET_MODEL_TPM", "")
_MODEL_TPM_OVERRIDES: dict[str, int] = {}
for pair in _MODEL_TPM_ENV.split(","):
    pair = pair.strip()
    if ":" in pair:
        name, val = pair.split(":", 1)
        try:
            _MODEL_TPM_OVERRIDES[name.strip()] = int(val.strip())
        except ValueError:
            pass


def _tpm_for(model_name: str) -> int:
    return _MODEL_TPM_OVERRIDES.get(model_name, _DEFAULT_TPM)


# ── 令牌桶 ────────────────────────────────────────────────────────────
class _Bucket:
    __slots__ = ("capacity", "last_refill", "rate", "tokens")

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

    def try_consume(self, n: float) -> bool:
        """尝试消耗 n 个令牌（用于 TPM：按 token 数扣减）。"""
        self._refill()
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    def deduct(self, n: float) -> None:
        """无条件扣减（事后修正：实际消耗 > 预估时补扣）。可为负。"""
        self._refill()
        self.tokens -= n

    def refund(self, n: float = 1.0) -> None:
        """退还令牌（事后修正：实际消耗 < 预估时回退，不超容量）。"""
        self._refill()
        self.tokens = min(self.capacity, self.tokens + n)

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


def acquire_token(
    key_id: int,
    model_name: str,
    est_tokens: int = 0,
    provider_id: int = 0,
    account_label: str | None = None,
) -> bool:
    """尝试为 (key_id, model_name) 获取令牌（RPM + 可选 TPM）。

    est_tokens>0 时额外检查账号级 TPM 桶：按预估 token 数扣减，
    桶空则退还已扣的 RPM 令牌并返回 False（跳过此 key，换有余量的账号）。
    est_tokens=0 时仅检查 RPM（向后兼容 embeddings/images）。

    返回 True 表示已消耗令牌、可以发请求；
    返回 False 表示桶空、应跳过此 key 换下一把。
    """
    rpm_bucket = _get_bucket(key_id, model_name)
    if not rpm_bucket.acquire():
        return False
    if est_tokens > 0:
        tpm_bucket = _get_tpm_bucket(provider_id, account_label, model_name)
        if tpm_bucket is not None and not tpm_bucket.try_consume(float(est_tokens)):
            rpm_bucket.refund(1.0)  # TPM 不够，退还已扣的 RPM 令牌
            return False
    return True


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


# ── TPM 令牌桶（按 账号×模型）──────────────────────────────────────
# 同账号密钥共享上游 TPM 池（多数厂商按账号/组织限流），与 balancer 的
# account_is_rate_limited 同口径。按 (provider_id, account_label, model) 标识。
_tpm_buckets: dict[tuple[int, str | None, str], _Bucket] = {}


def _get_tpm_bucket(
    provider_id: int, account_label: str | None, model_name: str
) -> _Bucket | None:
    """获取或创建 TPM 桶。TPM=0（未配置）时返回 None，表示不追踪。"""
    tpm = _tpm_for(model_name)
    if tpm <= 0:
        return None
    bkey = (provider_id, account_label, model_name)
    bucket = _tpm_buckets.get(bkey)
    if bucket is None:
        burst = max(1, int(tpm * _DEFAULT_TPM_BURST_RATIO))
        bucket = _Bucket(rpm=tpm, capacity=burst)  # rate=tpm/60 tokens/sec
        _tpm_buckets[bkey] = bucket
        log.info("tpm_bucket_created", provider_id=provider_id,
                 account_label=account_label, model=model_name,
                 tpm=tpm, burst=burst)
    return bucket


def tpm_available(
    provider_id: int, account_label: str | None, model_name: str
) -> float:
    """当前可用 TPM 令牌数（不消耗）。gateway 用此判断是否有余量。"""
    bucket = _get_tpm_bucket(provider_id, account_label, model_name)
    if bucket is None:
        return float("inf")  # TPM 未启用，视为无限余量
    return bucket.available()


def tpm_correct(
    provider_id: int,
    account_label: str | None,
    model_name: str,
    estimated: int,
    actual: int,
) -> None:
    """响应返回后按实际 usage 修正 TPM 桶。

    estimated=0 表示请求时未预扣（桶空顾问放行），按实际消耗补扣。
    estimated>0 时按差额修正：实际 > 预估则补扣，实际 < 预估则退还。
    """
    bucket = _get_tpm_bucket(provider_id, account_label, model_name)
    if bucket is None:
        return
    diff = actual - estimated
    if diff > 0:
        bucket.deduct(float(diff))
    elif diff < 0:
        bucket.refund(float(-diff))
