"""上游限流口径相关的回归测试。

三个数字都是 2026-11-05 在容器里直连上游实测出来的，改动时必须能被
测试挡住——它们直接决定 ZCode 是否频繁「重新连接」：

1. TPM 只算未命中缓存的输入 + 输出（命中部分几乎不占配额）
2. 429 后 20–32 秒就恢复（冷却阶梯不能比这长太多）
3. 全池短冷却时应原地等待，而不是甩 429 让客户端重连
"""
from __future__ import annotations

from itertools import pairwise

from app.relay import balancer
from app.relay import gateway as gw
from app.relay import token_bucket as tb

# ── 1. 计费口径：缓存命中不计入 TPM ──────────────────────────────

def test_billable_tokens_excludes_cached_prompt():
    """真实 ZCode agent 请求：70k prompt 里 69k 命中缓存。

    按全额记账要吃掉整个分钟预算（70k），按未命中记账只有 ~1k。
    上游就是按后者算配额的，网关必须同口径。
    """
    usage = {
        "prompt_tokens": 70_000,
        "completion_tokens": 300,
        "prompt_tokens_details": {"cached_tokens": 69_000},
    }
    assert gw.billable_tokens(usage) == 1_300


def test_billable_tokens_without_cache_detail_counts_full_prompt():
    """上游没给缓存明细时按全额算——保守，宁高不低。"""
    usage = {"prompt_tokens": 5_000, "completion_tokens": 200}
    assert gw.billable_tokens(usage) == 5_200


def test_billable_tokens_all_cached_counts_only_completion():
    usage = {
        "prompt_tokens": 48_000,
        "completion_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 48_000},
    }
    assert gw.billable_tokens(usage) == 120


def test_billable_tokens_handles_none_and_empty():
    assert gw.billable_tokens(None) == 0
    assert gw.billable_tokens({}) == 0


def test_billable_tokens_never_negative():
    """cached > prompt 的脏数据不能算成负数（会让预算被凭空释放）。"""
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 0,
        "prompt_tokens_details": {"cached_tokens": 500},
    }
    assert gw.billable_tokens(usage) >= 0


# ── 1b. 事前预扣也要按缓存率折算，否则会自锁 ────────────────────

def test_reserve_estimate_discounts_prompt_by_cache_ratio(monkeypatch):
    """不折算会把自己锁死：真实请求 70k prompt 只占 1.3k 配额，

    事前按 70k 预扣 → 两条就吃满整个分钟预算 → 后续请求全被自己的
    预算挡住（实测压测第 7 条起连续 429「本分钟 token 预算不足」）。
    """
    monkeypatch.setattr(gw, "_CACHE_RATIO", {"glm-5.2": 0.9})
    monkeypatch.setattr(gw, "_EST_FACTOR", {})
    # 1000 prompt × (1-0.9) + 100 output；int 截断容 1 的误差
    assert abs(gw._reserve_estimate("glm-5.2", 1000, 100) - 200) <= 1


def test_reserve_estimate_counts_output_in_full(monkeypatch):
    """输出 token 不可能命中缓存，不能被缓存率折算掉。"""
    monkeypatch.setattr(gw, "_CACHE_RATIO", {"glm-5.2": 0.99})
    monkeypatch.setattr(gw, "_EST_FACTOR", {})
    assert abs(gw._reserve_estimate("glm-5.2", 1000, 500) - 510) <= 1


def test_reserve_estimate_cold_start_uses_full_prompt(monkeypatch):
    """冷启动（还没学到命中率）必须保守按全额预扣，宁可少发不可超发。"""
    monkeypatch.setattr(gw, "_CACHE_RATIO", {})
    monkeypatch.setattr(gw, "_EST_FACTOR", {})
    assert gw._reserve_estimate("glm-5.2", 1000, 100) == 1100


def test_cache_ratio_learns_from_usage(monkeypatch):
    monkeypatch.setattr(gw, "_CACHE_RATIO", {})
    gw._update_cache_ratio(
        "glm-5.2",
        {"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 900}},
    )
    assert gw._CACHE_RATIO["glm-5.2"] == 0.9


def test_cache_ratio_cold_request_pulls_estimate_down(monkeypatch):
    """冷请求（cached=0）必须把命中率估计往下拉，否则预扣永远偏小、必然超发。"""
    monkeypatch.setattr(gw, "_CACHE_RATIO", {"glm-5.2": 0.9})
    gw._update_cache_ratio(
        "glm-5.2",
        {"prompt_tokens": 1000, "prompt_tokens_details": {"cached_tokens": 0}},
    )
    assert gw._CACHE_RATIO["glm-5.2"] < 0.9


def test_cache_ratio_ignores_zero_prompt():
    """无 prompt 的响应不能参与学习（除以 0 / 污染估计）。"""
    gw._CACHE_RATIO.clear()
    gw._update_cache_ratio("glm-5.2", {"prompt_tokens": 0})
    assert "glm-5.2" not in gw._CACHE_RATIO


# ── 2. 冷却阶梯要贴合上游真实恢复时间 ────────────────────────────

def test_backoff_starts_near_measured_recovery_time():
    """实测 429 后 20–32 秒恢复，首档必须落在这个量级。

    旧阶梯首档 60 秒，把 20 秒的可恢复限流锁成 1 分钟死时间。
    """
    first = balancer._BACKOFF_STEPS[0]
    assert 15 <= first <= 30, f"首档 {first}s 偏离实测恢复时间 20–32s"


def test_backoff_ceiling_is_not_much_longer_than_recovery():
    """实测周期里最长一次连续 429 约 30 秒，封顶不该到分钟级。

    旧封顶 300 秒 = 实测恢复时间的约 10 倍，是 ZCode 长时间重连的直接原因。
    """
    ceiling = balancer._BACKOFF_STEPS[-1]
    assert ceiling <= 90, f"封顶 {ceiling}s 远超实测恢复时间，会放大成死时间"


def test_backoff_steps_are_monotonic():
    steps = balancer._BACKOFF_STEPS
    assert all(b > a for a, b in pairwise(steps)), steps


# ── 3. TPM 预算种子贴近实测上游上限 ──────────────────────────────

def test_tpm_seed_matches_measured_upstream_limit():
    """实测 ~15.6 万 token/分钟/账号。

    种子高于真实上限 = 网关超发（旧值 40 万是上限的 2.5 倍，必然 429）；
    远低于上限 = 网关自我限死（历史值 1.5 万装不下任何一条真实请求）。
    """
    assert 100_000 <= tb._TPM_SEED <= 250_000, f"种子 {tb._TPM_SEED} 偏离实测 ~156k"


def test_tpm_ceiling_leaves_room_but_is_bounded():
    """上限给大配额账号留余量，但不应回到 400 万那种失效区间。"""
    assert tb._TPM_CEIL <= 1_000_000


def test_tpm_floor_is_at_least_one_large_request():
    """单条真实 agent 请求可达 33 万 token；下限至少要让 5 万以下的能过。"""
    assert tb._TPM_FLOOR >= 30_000


# ── 4. 短冷却原地等待，避免客户端重连风暴 ────────────────────────

def test_short_cooldown_wait_covers_backoff_first_step():
    """首档退避 20 秒必须落在"原地等"的阈值内，否则又会甩 429 让客户端重连。"""
    first = balancer._BACKOFF_STEPS[0]
    assert gw._SHORT_COOLDOWN_WAIT >= first


def test_short_cooldown_wait_is_short_enough_for_http():
    """等待塞在请求里，不能长到让客户端超时（拆连接 = 又是重连）。"""
    assert gw._SHORT_COOLDOWN_WAIT <= 40


# ── 5. 证据下界会过期，否则预算永久锁死 ──────────────────────────

def _tpm_state_with_success(model="glm-5.2", success=66_024):
    st = tb._get_tpm_state(7, "acct-evidence", model)
    st.budget = success * 1.15
    st.on_success(success)
    st.window.clear()
    st.window.append((__import__("time").monotonic(), st.budget))
    st.last_decrease_at = 0.0
    return st


def test_evidence_floor_blocks_decrease_while_fresh(monkeypatch):
    """刚成功过的大请求，短时间内不许把预算降到它之下（防止退化成 60 秒一发）。"""
    monkeypatch.setattr(tb, "_TPM_EVIDENCE_TTL", 900.0)
    tb.reset_tpm_state()
    st = _tpm_state_with_success()
    st.on_rate_limit(1_000)
    floor = st.max_success * 1.05
    assert st.budget >= floor, (
        f"证据仍然新鲜，预算 {st.budget} 不该被压到已成功请求规模 {floor} 之下"
    )


def test_stale_evidence_allows_budget_to_decrease(monkeypatch):
    """证据过期后必须能继续下探。

    这是「一直 429 但预算纹丝不动」的根因：上游额度随 5 小时积分池余额
    浮动（池子满时 156k、快见底时 8k），永久钉住历史最大成功会让网关
    按 69k 放行而上游只收 8k，永远 429。
    """
    monkeypatch.setattr(tb, "_TPM_EVIDENCE_TTL", 900.0)
    tb.reset_tpm_state()
    st = _tpm_state_with_success()
    before = st.budget
    # 把证据时间拨到很久以前
    st.max_success_at = 0.0
    st.window.clear()
    st.window.append((__import__("time").monotonic(), st.budget))
    st.last_decrease_at = 0.0
    st.on_rate_limit(1_000)
    assert st.budget < before, "证据已过期，预算必须能下调（否则永久 429）"


def test_evidence_ttl_is_minutes_not_hours():
    """保质期要短到能跟住额度变化，又不能短到让每次成功白学。"""
    assert 300 <= tb._TPM_EVIDENCE_TTL <= 1800
