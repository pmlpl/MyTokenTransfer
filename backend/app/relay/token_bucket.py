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
# RPM = 每分钟允许的请求条数，与内容大小无关；TPM 才是 token 量。
#
# 为什么不能靠手工配：上游从不公开每模型的 RPM，各模型还不一样（实测
# glm-5.2 约 4–5 次/分、glm-5.3 约 6 次/分），而这里是**按模型**配置的
# 静态常量。每接一个新模型，没人配就落回默认 20（实测真实上限的 3–5 倍）
# ——放行 20 次、上游只收 4–6 次，其余必然 429。这就是「每换个模型就撞一次
# 墙」的根因：限速靠人填常量，而不是靠反馈学出来。
#
# 因此改为 AIMD 自学习（与 TPM 预算同一套思路）：
# - 种子保守（_RPM_SEED），宁可一开始少发，也不制造 429；
# - 成功持续则缓慢上调（_RPM_PROBE_FACTOR），把真实余量找回来；
# - 撞 429 则乘性下调（_RPM_DECREASE_FACTOR），并把"当前速率"记为上限证据；
# - 上限证据有保质期（额度随积分池余额浮动，同 TPM 的理由）。
#
# 显式的 MTT_TOKEN_BUCKET_MODEL_RPM 仍生效：作为该模型的**初始种子**，
# 之后照样自适应（不是硬上限），这样既保留手工兜底，又不用每个新模型都配。
_RPM_SEED = _env_int("MTT_TOKEN_BUCKET_RPM", 5)

# 突发容量：桶满时允许连续发出的请求数（不用等补充）。
#
# 不能取 1：容量小于"每分钟额度"时，空闲攒下的额度会被**丢弃**。
# 取 1 时用完那一个就要干等 60/rpm 秒，而上游真实口径是"每分钟 N 次"
# ——空闲 50 秒本可攒够 5 次，却只放行 1 次，等于把容量砍掉大半
# （实测 15 并发仅 1 个 200）。
#
# 也不宜过大：容量远大于每分钟额度时，桶满可瞬间连发远超均值的请求，
# 而上游 429 按瞬时窗口判，会直接打爆。取"一分钟额度即可全部攒下"的量级
# 最贴合上游口径——_Bucket 会按学习到的 rpm 把容量钳到 min(burst, rpm)。
_DEFAULT_BURST = _env_int("MTT_TOKEN_BUCKET_BURST", 10)

# 自适应上下限：下限防止 429 把速率压到 1 次/分（正常 agent 会话会被拖死）；
# 上限防止某次误判把速率永久抬高。
_RPM_MIN = _env_float("MTT_TOKEN_BUCKET_RPM_MIN", 1.0)
_RPM_MAX = _env_float("MTT_TOKEN_BUCKET_RPM_MAX", 60.0)

# 成功持续多久才允许上调：太急会在边界反复试探，撞 429 又降回来。
_RPM_PROBE_EVERY = _env_int("MTT_TOKEN_BUCKET_RPM_PROBE_EVERY", 8)
_RPM_PROBE_FACTOR = 1.05
_RPM_DECREASE_FACTOR = 0.6

# 两次下调之间的最短间隔（秒）：防连发 429 把速率瞬间砸到地板。
_RPM_DECREASE_COOLDOWN = 30.0

# 上限证据的保质期（秒）：与 TPM 同理，上游额度随积分池余额浮动。
_RPM_EVIDENCE_TTL = float(_env_int("MTT_TOKEN_BUCKET_RPM_EVIDENCE_TTL", 900))


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
    """该模型的 RPM **初始种子**（之后由 AIMD 自适应，不是硬上限）。"""
    return _MODEL_RPM_OVERRIDES.get(model_name, _RPM_SEED)


# ── TPM（每分钟 token 数）预算配置 ──────────────────────────────────
# 种子值：仅作自适应起点，不是硬上限。
#
# 2026-11-05 实测上游真实 TPM 边界（单 key，48k 冷请求每 10 秒一发）：
#   t+0/10 429 → t+21/32/42 200 200 200 → t+50/60/70 429 → t+82 200 ...
# 即每分钟约放过 15.6 万 token（未命中缓存的输入 + 输出）。旧的 40 万种子
# 是真实上限的 2.5 倍——网关按 40 万放行，上游按 15.6 万拒收，必然 429。
# 取 15 万作为自学习起点，交给 AIMD 上下微调。
_TPM_SEED = _env_int("MTT_TOKEN_BUCKET_TPM", 150_000)

# 预算上下限。下限防止 429 把预算压到发不出正常请求；
# 上限防止某次异常大头把预算永久抬高（留 4 倍余量给有更大配额的账号）。
_TPM_FLOOR = _env_int("MTT_TOKEN_BUCKET_TPM_MIN", 50_000)
_TPM_CEIL = _env_int("MTT_TOKEN_BUCKET_TPM_MAX", 600_000)

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

# 「成功过的请求规模」这条下界证据的保质期（秒）。
# SenseNova 的每分钟额度随 5 小时积分池余额浮动：池子满时单请求能过 156k，
# 快见底时只放行 8k。证据过期后下限回落到配置 floor，允许 AIMD 继续下探；
# 15 分钟足以覆盖一次正常会话的连续请求，又不会把半小时前的旧证据当现状。
_TPM_EVIDENCE_TTL = float(_env_int("MTT_TOKEN_BUCKET_EVIDENCE_TTL", 900))

# 按原生模型覆盖种子，格式："deepseek-flash:100000,glm-5.2:800000"
_MODEL_TPM_OVERRIDES = _parse_model_overrides(
    _env_str("MTT_TOKEN_BUCKET_MODEL_TPM", "")
)


def _tpm_seed_for(model_name: str) -> int:
    return _MODEL_TPM_OVERRIDES.get(model_name, _TPM_SEED)


# ── RPM 令牌桶 ────────────────────────────────────────────────────────
class _Bucket:
    """按 (key, 模型) 的 RPM 令牌桶，速率自学习（AIMD）。

    与 TPM 预算同一套思路，但学的维度不同：TPM 学"每分钟能过多少 token"，
    这里学"每分钟能过多少条请求"。两者独立——一条 30 万 token 的请求只花
    1 次 RPM，却是整分钟 TPM 的两倍。

    速率不是固定常量：成功持续则上调、撞 429 则下调，从反馈里收敛到上游
    真实上限。这样新模型接进来不需要人先去测、去配。
    """

    __slots__ = (
        "capacity", "last_refill", "rate", "tokens",
        "successes", "last_decrease_at", "rate_ceiling", "rate_ceiling_at",
        "seed", "key_id", "model",
    )

    def __init__(self, rpm: int, capacity: int, key_id: int = 0, model: str = ""):
        seed = float(max(_RPM_MIN, min(_RPM_MAX, rpm)))
        self.seed = seed
        self.rate = seed / 60.0         # tokens / second
        # 容量钳到"一分钟额度"：既让空闲攒下的额度不被丢弃（capacity 太小
        # 会把每分钟 N 次砍成 1 次），又不会让 burst 远超均值去打爆瞬时窗口。
        self.capacity = float(max(1, min(capacity, int(round(seed)))))
        self.tokens = float(self.capacity)   # 初始满桶
        self.last_refill = time.monotonic()
        self.successes = 0
        self.last_decrease_at = 0.0
        # 撞 429 时的速率快照 = "至少这么快就会被拒" 的实证。
        # 上调不能显著越过它，否则会反复试探边界、反复 429。
        self.rate_ceiling = float(_RPM_MAX)
        self.rate_ceiling_at = 0.0
        # 身份标识：每条调速日志都要能定位到具体 (key, 模型)
        self.key_id = key_id
        self.model = model

    @property
    def rpm(self) -> float:
        """当前学习到的每分钟速率。"""
        return self.rate * 60.0

    def _log_ctx(self) -> dict:
        return {"key_id": self.key_id, "model": self.model}

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        if elapsed > 0:
            # 速率会变，token 上限跟着容量走（token 是"次数"的池子，与 rpm 无关）
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_refill = now

    def _set_rpm(self, rpm: float) -> None:
        clamped = max(_RPM_MIN, min(_RPM_MAX, rpm))
        self.rate = clamped / 60.0
        # 容量跟随速率：学到更高额度时容量要跟着放大，否则"每分钟 N 次"
        # 仍会被旧的小容量卡住；下调时同步收紧，避免残留的令牌形成超额突发。
        self.capacity = float(max(1, min(_DEFAULT_BURST, int(round(clamped)))))
        if self.tokens > self.capacity:
            self.tokens = self.capacity

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

    def on_success(self) -> None:
        """一次请求成功：慢速探测上调，把被 429 压低后的余量找回来。

        只在**连续成功**若干次后上调（_RPM_PROBE_EVERY），且不越过
        `rate_ceiling`——那是上次撞 429 时的速率快照，越过它必然再撞。
        上限证据过期后（额度随积分池浮动）才允许突破。
        """
        self.successes += 1
        if self.successes % _RPM_PROBE_EVERY:
            return
        now = time.monotonic()
        evidence_fresh = (now - self.rate_ceiling_at) < _RPM_EVIDENCE_TTL
        target = self.rpm * _RPM_PROBE_FACTOR
        if evidence_fresh:
            target = min(target, self.rate_ceiling)
        if target > self.rpm + 1e-9:
            self._set_rpm(target)
            log.info("rpm_probe_raised", **self._log_ctx(),
                     rpm=round(self.rpm, 2), ceiling=round(self.rate_ceiling, 2),
                     evidence_fresh=evidence_fresh)

    def on_rate_limit(self) -> None:
        """上游返回 429：乘性下调，并把当前速率记为上限证据。

        这条路径是自学习的核心：不必知道上游真实 RPM 是多少，撞一次就
        学到"这个速率过高"。递减步长按 AIMD 取 0.6，两三次 429 就能收敛
        到可用区间；`_RPM_DECREASE_COOLDOWN` 防止并发突发把速率瞬间砸到底。
        """
        now = time.monotonic()
        # 无论是否真的下调，都要刷新上限证据：这次 429 证明当前速率过高
        self.rate_ceiling = self.rpm
        self.rate_ceiling_at = now
        if now - self.last_decrease_at < _RPM_DECREASE_COOLDOWN:
            return
        self.last_decrease_at = now
        before = self.rpm
        self._set_rpm(before * _RPM_DECREASE_FACTOR)
        log.warning("rpm_lowered", **self._log_ctx(),
                    rpm=round(self.rpm, 2), before=round(before, 2))

    def on_success_streak_reset(self) -> None:
        """重置连续成功计数（429 后重新计）。"""
        self.successes = 0


# 全局桶注册表：(key_id, native_model) -> _Bucket
_buckets: dict[tuple[int, str], _Bucket] = {}


def _get_bucket(key_id: int, model_name: str) -> _Bucket:
    bkey = (key_id, model_name)
    bucket = _buckets.get(bkey)
    if bucket is None:
        rpm = _rpm_for(model_name)
        bucket = _Bucket(rpm=rpm, capacity=_DEFAULT_BURST,
                         key_id=key_id, model=model_name)
        _buckets[bkey] = bucket
        log.info("token_bucket_created", key_id=key_id, model=model_name,
                 rpm=rpm, burst=_DEFAULT_BURST,
                 source="env_override" if model_name in _MODEL_RPM_OVERRIDES else "seed")
    return bucket


def acquire_rpm(key_id: int, model_name: str) -> bool:
    """只扣 RPM 令牌（embeddings / images 等无 token 语义的接口用）。"""
    return _get_bucket(key_id, model_name).acquire()


def release_rpm(key_id: int, model_name: str) -> None:
    """退还一个 RPM 令牌（请求失败时调用）。"""
    _get_bucket(key_id, model_name).refund(1.0)


def rpm_on_success(key_id: int, model_name: str) -> None:
    """上游成功响应：让 RPM 速率慢速探测上调。

    这是自学习闭环的上半部分——没有它，被 429 压低后的速率永远回不来，
    网关会一直按最低速率发，明明有容量却用不满。
    """
    _get_bucket(key_id, model_name).on_success()


def rpm_on_rate_limit(key_id: int, model_name: str) -> None:
    """上游 429：RPM 速率乘性下调并记录上限证据。

    闭环的下半部分。只在确实撞了限流时调用，且必须排除"429 是 TPM 打满
    导致的"——那种情况该由 tpm_on_rate_limit 处理，压 RPM 会误伤
    （一条大 prompt 请求打满 TPM，不代表请求条数超了）。
    """
    bucket = _get_bucket(key_id, model_name)
    bucket.on_rate_limit()
    bucket.on_success_streak_reset()


def available_tokens(key_id: int, model_name: str) -> float:
    """查询当前可用 RPM 令牌数（不消耗）。用于日志/调试。"""
    return _get_bucket(key_id, model_name).available()


def rpm_snapshot() -> list[dict]:
    """导出全部 RPM 桶状态，供运维排查「某模型实际学到的上限是多少」。"""
    return [
        {
            "key_id": b.key_id,
            "model": b.model,
            "rpm": round(b.rpm, 2),
            "seed": round(b.seed, 2),
            "ceiling": round(b.rate_ceiling, 2),
            "available": round(b.available(), 2),
            "successes": b.successes,
        }
        for b in _buckets.values()
    ]


# ── TPM 滚动窗口 + 自适应预算 ────────────────────────────────────────
class _TpmState:
    """一个 (账号, 模型) 的 TPM 记账状态。

    window 存 (monotonic_ts, tokens)，只保留最近 60 秒内的条目。
    每次操作前先 prune，窗口和即为"本分钟已用"。
    """

    __slots__ = ("budget", "window", "last_decrease_at", "successes", "seed",
                 "floor", "provider_id", "account_label", "model", "max_success",
                 "max_success_at")

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
        #
        # 但这条证据**会过期**（见 max_success_at / _TPM_EVIDENCE_TTL）：
        # SenseNova 的每分钟额度随 5 小时积分池余额浮动，池子满时能过 156k，
        # 快耗尽时只放行 8k。永久钉住历史最大成功会导致预算锁在 69k、网关按
        # 69k 放行而上游按 8k 拒收——实测正是「一直 429 且预算不降」的根因。
        self.max_success = 0.0
        self.max_success_at = 0.0
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
            self.max_success_at = time.monotonic()
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

        但证据有保质期（_TPM_EVIDENCE_TTL）：SenseNova 的每分钟额度随 5 小时
        积分池余额浮动——池子满时单请求能过 156k，快见底时只放行 8k。若永久
        钉住历史上那条 66k 的成功记录，预算会卡在 69k 再也降不下来，网关按
        69k 放行而上游只收 8k，表现为「持续 429 且预算纹丝不动」。
        证据过期后下限回落到配置的 floor，让 AIMD 能继续下探。
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
        evidence_fresh = (now - self.max_success_at) < _TPM_EVIDENCE_TTL
        evidence_floor = self.max_success * 1.05 if evidence_fresh else 0.0
        floor = max(self.floor, evidence_floor)
        reduced = min(self.budget * _TPM_DECREASE_FACTOR, float(est) * 0.9)
        new_budget = max(floor, reduced)
        if new_budget >= self.budget:
            # 已到证据下界：不再下调，并确保预算不低于下界本身
            # （可能因早先的下调或外部赋值而低于它）。
            if self.budget < floor:
                self.budget = floor
            log.info("tpm_budget_hold_at_evidence_floor", **self._log_ctx(),
                     budget=int(self.budget), floor=int(floor), spent=int(spent),
                     evidence_fresh=evidence_fresh)
            return
        self.budget = new_budget
        log.warning("tpm_budget_lowered", **self._log_ctx(),
                    budget=int(self.budget), spent=int(spent), est=int(est),
                    evidence_fresh=evidence_fresh)


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
