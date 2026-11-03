"""令牌桶限流：双层事前控速。

两家维度完全不同的限速机制：

- **RPM 桶**（按 key×模型）：每分钟请求数，扣 1 个令牌/请求。
  经典令牌桶，桶空即换下一把 key。

- **TPM 预算**（按 账号×模型）：每分钟 token 数。
  不是令牌桶——因为单次 agent 请求可达数十万 token，比"每分钟额度"
  本身还大，令牌桶的 burst 容量再调也装不下一条请求，于是恒空、
  恒走放行、恒由事后补扣兜底，等于完全失效。改为**滚动 60 秒窗口记账
  + 自适应预算**：窗口内已用 token 之和逼近预算就等它滚出窗口，
  等不及则明确返回 429 + 精确 Retry-After，而不是把请求发出去撞 429。

  预算本身由上游反馈自动学习（AIMD）：
  - 一条请求成功 → 说明预算至少这么大，立即抬到 actual × 1.15
  - 慢速探测：每若干次成功轻微上调
  - 窗口消耗过半仍吃 429 → 乘性下调（有冷却，避免震荡）
  - 窗口消耗很低却吃 429 → 大概率是并发/RPM 限制，不动预算

设计原则：
1. 单进程 uvicorn，无锁（事件循环内无 await 的算术操作不存在竞态）
2. 不硬阻：预算不够先短暂等待，等不及才返回 429（带真实等待秒数）
3. 速率可按模型覆盖；`MTT_TOKEN_BUCKET_TPM=0` 一键关闭 TPM 追踪
4. 预估是粗估，成功路径用实际 usage 结算覆盖预留值，失败路径退回预留
"""
from __future__ import annotations

import math
import os
import time
from collections import deque
from pathlib import Path

import structlog

log = structlog.get_logger("relay")


def _env_int(name: str, default: int) -> int:
    """读整型环境变量，缺失时回退读 backend/.env。

    pydantic-settings 只把 .env 读进 Settings 对象，不会写 os.environ，
    所以令牌桶这类直接用 os.environ 的模块在本地直跑时读不到 .env。
    这里补一层回退，让「把 MTT_* 写进 backend/.env」也能生效。
    """
    raw = os.environ.get(name)
    if raw is None:
        raw = _read_dotenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (ValueError, AttributeError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        raw = _read_dotenv(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except (ValueError, AttributeError):
        return default


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None:
        raw = _read_dotenv(name)
    return default if raw is None else raw.strip()


def _read_dotenv(name: str) -> str | None:
    """从 backend/.env 读一个键（仅本模块使用的兜底通道）。"""
    path = Path(__file__).resolve().parents[2] / ".env"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key.strip() == name:
            return val.strip().strip('"').strip("'")
    return None


# ── RPM（每分钟请求数）──────────────────────────────────────────────
# 默认每把 key 每分钟请求数（RPM）。保守值：免费层避免触发 "429 rpm exhausted"。
# 官方接口上限 5 RPS = 300 RPM，免费层实际 RPM 限额未公布，取 20 留足余量。
_DEFAULT_RPM = _env_int("MTT_TOKEN_BUCKET_RPM", 20)

# 突发容量：桶满时允许连续发出的请求数（不用等补充）。
_DEFAULT_BURST = _env_int("MTT_TOKEN_BUCKET_BURST", 10)


def _parse_model_overrides(raw: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" in pair:
            name, val = pair.split(":", 1)
            try:
                out[name.strip()] = int(val.strip())
            except ValueError:
                pass
    return out


# 按原生模型名覆盖 RPM，格式："glm-5.2:10,deepseek-v4-flash:15"
_MODEL_RPM_OVERRIDES = _parse_model_overrides(
    _env_str("MTT_TOKEN_BUCKET_MODEL_RPM", "")
)


def _rpm_for(model_name: str) -> int:
    return _MODEL_RPM_OVERRIDES.get(model_name, _DEFAULT_RPM)


# ── TPM（每分钟 token 数）预算配置 ──────────────────────────────────
# 种子值：仅作自适应起点，不是硬上限。实测单次 agent 请求可达 33 万 token
# （长会话整段历史每次全带上），旧的 30000 令牌桶容量 15000 装不下任何一条
# 真实请求，导致 TPM 层从未生效。
_TPM_SEED = _env_int("MTT_TOKEN_BUCKET_TPM", 400_000)

# 预算上下限。下限防止 429 把预算压到发不出正常请求；
# 上限防止某次异常大头把预算永久抬高。
_TPM_FLOOR = _env_int("MTT_TOKEN_BUCKET_TPM_MIN", 50_000)
_TPM_CEIL = _env_int("MTT_TOKEN_BUCKET_TPM_MAX", 4_000_000)

# 预算不足时最多等待多少秒再重发；超过则直接 429。
_TPM_MAX_WAIT = _env_float("MTT_TOKEN_BUCKET_TPM_MAX_WAIT", 8.0)

# 窗口长度：与上游 TPM 口径一致（每分钟）。
_TPM_WINDOW_SEC = 60.0

# 乘性下调系数 / 上调系数
_TPM_DECREASE_FACTOR = 0.6
_TPM_INCREASE_FACTOR = 1.15
_TPM_PROBE_FACTOR = 1.02
_TPM_PROBE_EVERY = 10  # 每 N 次成功做一次慢速上调

# 两次下调之间的最短间隔（秒）：防止连发 429 把预算瞬间砸到地板。
_TPM_DECREASE_COOLDOWN = 30.0

# 只有窗口消耗达到预算该比例时才认为"是 TPM 打满"，才调预算。
_TPM_DECREASE_THRESHOLD = 0.5

# 按原生模型覆盖种子，格式："deepseek-flash:100000,glm-5.2:800000"
_MODEL_TPM_OVERRIDES = _parse_model_overrides(
    _env_str("MTT_TOKEN_BUCKET_MODEL_TPM", "")
)


def _tpm_seed_for(model_name: str) -> int:
    return _MODEL_TPM_OVERRIDES.get(model_name, _TPM_SEED)


# ── RPM 令牌桶 ────────────────────────────────────────────────────────
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

    def refund(self, n: float = 1.0) -> None:
        """退还令牌（失败请求回收），不超容量。"""
        self._refill()
        self.tokens = min(self.capacity, self.tokens + n)

    def available(self) -> float:
        """当前可用令牌数（含应补充未补充的）。"""
        self._refill()
        return self.tokens


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


def acquire_rpm(key_id: int, model_name: str) -> bool:
    """只扣 RPM 令牌（embeddings / images 等无 token 语义的接口用）。"""
    return _get_bucket(key_id, model_name).acquire()


def release_rpm(key_id: int, model_name: str) -> None:
    """退还一个 RPM 令牌（请求失败时调用）。"""
    _get_bucket(key_id, model_name).refund(1.0)


def available_tokens(key_id: int, model_name: str) -> float:
    """查询当前可用 RPM 令牌数（不消耗）。用于日志/调试。"""
    return _get_bucket(key_id, model_name).available()


# ── TPM 滚动窗口 + 自适应预算 ────────────────────────────────────────
class _TpmState:
    """一个 (账号, 模型) 的 TPM 记账状态。

    window 存 (monotonic_ts, tokens)，只保留最近 60 秒内的条目。
    每次操作前先 prune，窗口和即为"本分钟已用"。
    """

    __slots__ = ("budget", "window", "last_decrease_at", "successes", "seed",
                 "floor", "provider_id", "account_label", "model", "max_success")

    def __init__(self, seed: int, provider_id: int = 0,
                 account_label: str | None = None, model: str = ""):
        self.seed = float(seed)
        self.floor = float(max(_TPM_FLOOR, int(seed * 0.1)))
        self.budget = float(max(self.floor, min(_TPM_CEIL, seed)))
        self.window: deque[tuple[float, float]] = deque()
        self.last_decrease_at = 0.0
        self.successes = 0
        # 历史上成功过的最大单条请求。预算绝不能被降到它之下：
        # 一条请求能成功就证明上游限额至少这么大，盲目调低只会让后续
        # 每条请求都"超过预算"而每个窗口只能发一条（实测降到 51840，
        # 而真实请求 365235，导致每条都等满 60 秒）。
        self.max_success = 0.0
        # 身份标识：每条预算调整日志都要能定位到具体账号，否则多账号并发时
        # 日志里只有孤零零的数字，无法判断是哪个账号在降/在升。
        self.provider_id = provider_id
        self.account_label = account_label
        self.model = model

    def _log_ctx(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "account_label": self.account_label,
            "model": self.model,
        }

    def _prune(self, now: float) -> None:
        while self.window and now - self.window[0][0] >= _TPM_WINDOW_SEC:
            self.window.popleft()

    def spent(self) -> float:
        now = time.monotonic()
        self._prune(now)
        return sum(t for _, t in self.window)

    def _would_fit(self, est: float) -> bool:
        return self.spent() + est <= self.budget

    def wait_seconds(self, est: float) -> float:
        """窗口放开 est 需要等多久（秒）；0 表示可以立即放行。

        单条请求比整个预算还大时，只有窗口完全空才能放行——否则这条
        请求永远发不出去。此时等到窗口清空即可。
        """
        now = time.monotonic()
        self._prune(now)
        if not self.window:
            return 0.0
        if self._would_fit(est):
            return 0.0
        # 需要让最老的条目滚出窗口，腾出足够空间
        need_to_free = self.spent() + est - self.budget
        freed = 0.0
        for ts, toks in self.window:
            freed += toks
            if freed >= need_to_free:
                return max(0.0, _TPM_WINDOW_SEC - (now - ts))
        # 极端情况：整窗全滚空也不够（est > budget），等窗口清空
        return max(0.0, _TPM_WINDOW_SEC - (now - self.window[0][0]))

    def reserve(self, est: float) -> tuple[bool, float]:
        wait = self.wait_seconds(est)
        if wait > 0.0:
            return False, wait
        self.window.append((time.monotonic(), float(est)))
        return True, 0.0

    def settle(self, reserved: float, actual: float) -> None:
        """把最近一条预留额从 est 改记为实际消耗。"""
        now = time.monotonic()
        self._prune(now)
        for i in range(len(self.window) - 1, -1, -1):
            ts, toks = self.window[i]
            if toks == float(reserved):
                self.window[i] = (ts, float(actual))
                break
        else:
            # 找不到对应预留（被 prune 掉或已释放）：直接补记差额
            delta = float(actual) - float(reserved)
            if delta:
                self.window.append((now, delta))

    def release(self, reserved: float) -> None:
        """退回一条预留（请求根本没发出去或失败）。"""
        now = time.monotonic()
        self._prune(now)
        for i in range(len(self.window) - 1, -1, -1):
            ts, toks = self.window[i]
            if toks == float(reserved):
                del self.window[i]
                return

    def on_success(self, actual: float) -> None:
        """成功调用：抬预算到至少容得下这条请求，并做慢速探测上调。"""
        if actual > self.max_success:
            self.max_success = float(actual)
        target = actual * _TPM_INCREASE_FACTOR
        if target > self.budget:
            self.budget = min(float(_TPM_CEIL), target)
            log.info("tpm_budget_raised", **self._log_ctx(),
                     budget=int(self.budget), actual=int(actual))
        self.successes += 1
        if self.successes % _TPM_PROBE_EVERY == 0:
            self.budget = min(float(_TPM_CEIL), self.budget * _TPM_PROBE_FACTOR)

    def on_rate_limit(self, est: float) -> None:
        """收到 429：仅当窗口确实被打满时才乘性下调预算。

        下限取 max(floor, max_success × 1.05)：已成功过的请求规模是上游真实
        限额的**下界证据**，预算再低也不能低于它，否则每条请求都"超预算"，
        每个滚动窗口只能发一条，退化成 60 秒一发。
        """
        now = time.monotonic()
        self._prune(now)
        spent = self.spent()
        if spent < self.budget * _TPM_DECREASE_THRESHOLD:
            # 窗口很空却吃 429 → 极可能是并发/RPM 限制，不是 TPM；不动预算
            return
        if now - self.last_decrease_at < _TPM_DECREASE_COOLDOWN:
            return
        self.last_decrease_at = now
        floor = max(self.floor, self.max_success * 1.05)
        reduced = min(self.budget * _TPM_DECREASE_FACTOR, float(est) * 0.9)
        new_budget = max(floor, reduced)
        if new_budget >= self.budget:
            # 已到证据下界：不再下调，并确保预算不低于下界本身
            # （可能因早先的下调或外部赋值而低于它）。
            if self.budget < floor:
                self.budget = floor
            log.info("tpm_budget_hold_at_evidence_floor", **self._log_ctx(),
                     budget=int(self.budget), floor=int(floor), spent=int(spent))
            return
        self.budget = new_budget
        log.warning("tpm_budget_lowered", **self._log_ctx(),
                    budget=int(self.budget), spent=int(spent), est=int(est))


_tpm_states: dict[tuple[int, str | None, str], _TpmState] = {}


def _get_tpm_state(
    provider_id: int, account_label: str | None, model_name: str
) -> _TpmState | None:
    """获取或创建 TPM 状态。种子 <= 0 时返回 None，表示不追踪。"""
    seed = _tpm_seed_for(model_name)
    if seed <= 0:
        return None
    bkey = (provider_id, account_label, model_name)
    state = _tpm_states.get(bkey)
    if state is None:
        state = _TpmState(seed, provider_id=provider_id,
                          account_label=account_label, model=model_name)
        _tpm_states[bkey] = state
        log.info("tpm_state_created", provider_id=provider_id,
                 account_label=account_label, model=model_name,
                 budget=int(state.budget), floor=int(state.floor))
    return state


def tpm_reserve(
    provider_id: int, account_label: str | None, model_name: str, est: int
) -> tuple[bool, float]:
    """尝试为本请求预留 est 个 token。

    返回 (是否放行, 需要等待的秒数)。放行时已记入窗口。
    TPM 未启用时恒为 (True, 0.0)。
    """
    state = _get_tpm_state(provider_id, account_label, model_name)
    if state is None:
        return True, 0.0
    allowed, wait = state.reserve(float(est))
    if allowed:
        log.debug("tpm_reserved", provider_id=provider_id,
                  account_label=account_label, model=model_name,
                  est=int(est), spent=int(state.spent()), budget=int(state.budget))
    return allowed, wait


def tpm_settle(
    provider_id: int,
    account_label: str | None,
    model_name: str,
    reserved: int,
    actual: int,
) -> None:
    """成功响应后按实际 usage 结算：窗口里的预估值改为实际值。"""
    state = _get_tpm_state(provider_id, account_label, model_name)
    if state is None:
        return
    state.settle(float(reserved), float(actual))
    state.on_success(float(actual))


def tpm_release(
    provider_id: int, account_label: str | None, model_name: str, reserved: int
) -> None:
    """请求失败（网络异常/非 200/参数错误）：退回预留额度。"""
    state = _get_tpm_state(provider_id, account_label, model_name)
    if state is None:
        return
    state.release(float(reserved))


def tpm_on_rate_limit(
    provider_id: int, account_label: str | None, model_name: str, est: int
) -> None:
    """上游返回 429：让预算按 AIMD 下调。"""
    state = _get_tpm_state(provider_id, account_label, model_name)
    if state is None:
        return
    state.on_rate_limit(float(est))


def tpm_remaining(
    provider_id: int, account_label: str | None, model_name: str
) -> float:
    """当前预算余量（不消耗）。TPM 未启用时返回 inf。"""
    state = _get_tpm_state(provider_id, account_label, model_name)
    if state is None:
        return float("inf")
    return max(0.0, state.budget - state.spent())


def tpm_wait(
    provider_id: int, account_label: str | None, model_name: str, est: int
) -> float:
    """放行 est 还需等待多少秒（0 = 现在就能放行）。只读，不记账。"""
    state = _get_tpm_state(provider_id, account_label, model_name)
    if state is None:
        return 0.0
    return state.wait_seconds(float(est))


def tpm_snapshot() -> list[dict]:
    """导出全部 TPM 状态，供运维日志/排查使用。"""
    out: list[dict] = []
    for (pid, acct, model), st in _tpm_states.items():
        out.append({
            "provider_id": pid,
            "account_label": acct,
            "model": model,
            "budget": int(st.budget),
            "spent": int(st.spent()),
            "remaining": int(max(0.0, st.budget - st.spent())),
            "floor": int(st.floor),
            "successes": st.successes,
        })
    return out


def reset_tpm_state() -> None:
    """清空全部 TPM 状态（测试与配置热更用）。"""
    _tpm_states.clear()


def max_wait_seconds() -> float:
    """预算不足时允许等待的上限（秒）。"""
    return max(0.0, _TPM_MAX_WAIT)


def ceil_div_seconds(wait: float) -> int:
    """把等待秒数转成 Retry-After 用的整数秒（至少 1）。"""
    return max(1, int(math.ceil(wait)))
