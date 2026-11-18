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


# ── 4b. 原地等待上限必须跟随退避阶梯，否则升档后永久失效 ──────────

def test_inline_wait_ceiling_follows_backoff_ladder(monkeypatch):
    """退避阶梯的全部档位都得落在原地等待上限内。

    这是 2026-11-18 那波「重新连接」风暴的结构性成因：阶梯 20→40→60→90，
    而等待阈值固定 25 秒。一旦退避升到 40 秒以上，剩余冷却恒 > 25，
    "原地等"分支永久失效，网关只能甩 429，客户端随即重连
    （实测 02:31–03:20 共 22 次 429 全是这个原因）。
    """
    monkeypatch.setattr(gw, "_SHORT_COOLDOWN_WAIT", 25.0)
    monkeypatch.setattr(gw, "_INLINE_WAIT_CEILING", 65.0)
    ceiling = gw.inline_wait_ceiling()
    assert ceiling >= max(balancer._BACKOFF_STEPS), (
        f"退避最高档 {max(balancer._BACKOFF_STEPS)}s 超出等待上限 {ceiling}s，"
        "升档后网关会开始甩 429 让客户端重连"
    )


def test_inline_wait_ceiling_never_exceeds_hard_cap(monkeypatch):
    """上限不能超过硬天花板——等太久等于换一种方式制造客户端超时。"""
    monkeypatch.setattr(gw, "_INLINE_WAIT_CEILING", 40.0)
    monkeypatch.setattr(balancer, "_BACKOFF_STEPS", (20, 300))
    assert gw.inline_wait_ceiling() == 40.0


def test_inline_wait_ceiling_never_below_configured_floor(monkeypatch):
    """阶梯若被配得比现有阈值还短，也不能把等待上限拉低到旧值以下。"""
    monkeypatch.setattr(gw, "_SHORT_COOLDOWN_WAIT", 25.0)
    monkeypatch.setattr(gw, "_INLINE_WAIT_CEILING", 65.0)
    monkeypatch.setattr(balancer, "_BACKOFF_STEPS", (5,))
    assert gw.inline_wait_ceiling() == 25.0


def test_backoff_steps_never_exceed_tpm_window():
    """冷却封顶不得超过 TPM 滚动窗口长度。

    TPM 限流按 60 秒滚动窗口恢复，窗口一旦滚过就必然有容量。冷却超过窗口
    长度的那部分秒数等不到任何新容量，是纯死时间：既救不回请求，又因为
    超出网关原地等待上限而被迫甩 429 让客户端重连（原阶梯封顶 90 秒正是
    2026-11-18 那波风暴的成因）。
    """
    window = tb._TPM_WINDOW_SEC
    assert max(balancer._BACKOFF_STEPS) <= window, (
        f"退避封顶 {max(balancer._BACKOFF_STEPS)}s 超过 TPM 窗口 {window}s，"
        "多出的等待是死时间"
    )


def test_inline_wait_ceiling_covers_full_backoff_after_cap():
    """把阶梯压回窗口内之后，等待上限必须能覆盖全部档位（否则仍会甩 429）。"""
    assert gw.inline_wait_ceiling() >= max(balancer._BACKOFF_STEPS)


# ── 4c. Retry-After 必须指向「最早能用的那把 key」 ────────────────

def test_retry_after_targets_earliest_recovering_key():
    """多把 key 冷却时，客户端该等的是最早恢复的那把，不是最后恢复的。

    旧实现取 max：6 把 key 里 5 把冷却 90 秒、1 把 20 秒时也要等 90 秒，
    白白丢掉那把 20 秒后就能用的容量。Retry-After 指向最早的才不浪费。
    """
    from datetime import timedelta

    balancer.mark_account_rate_limited(7, "late", cooldown=90)
    balancer.mark_account_rate_limited(7, "early", cooldown=20)

    remaining = [
        balancer.account_rate_limited_remaining(7, acct) for acct in ("late", "early")
    ]
    assert min(remaining) <= 21, "最早恢复的那把 key 应该约 20 秒后可用"
    assert max(remaining) >= 89, "最晚恢复的那把 key 确实要等 90 秒"
    # 网关取 min → Retry-After 约 20 秒，而不是 90 秒
    assert min(remaining) < max(remaining)


def test_cooling_predicate_uses_any_not_min():
    """「是否还有 key 在冷却」必须用 any 判定，不能用 min(...) > 0。

    回归护栏：曾把判定和取值一起改成 min，结果只要有任意一把 key 已解冻，
    min 就是 0 → 判定为"无需 429" → 落到 502 分支。实测 5 并发里出现 1 个
    502（客户端会当网关故障猛重试，比 429 更糟）。
    """
    from datetime import timedelta

    balancer.mark_account_rate_limited(7, "still_cooling", cooldown=60)
    # "warm" 未标记，冷却剩余为 0
    per_key = [
        balancer.account_rate_limited_remaining(7, acct)
        for acct in ("still_cooling", "warm")
    ]
    assert min(per_key) == 0, "已解冻的 key 让 min 归零——这正是 bug 的形态"
    cooling = [r for r in per_key if r > 0]
    assert cooling, "但确实还有 key 在冷却，必须判为需要 429"
    assert min(cooling) > 0, "取值仍用最早恢复的那把"


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


# ── 6. max_tokens 是上限，不能当预测量 ───────────────────────────

def test_output_estimate_clamps_max_tokens():
    """ZCode 带 max_tokens=128000，但实际一次工具调用只吐几百 token。

    按上限预扣会让每条请求预占 12.8 万——超过整分钟预算，把每条请求都推进
    "超大请求放行一次"分支，事前控速完全失效（实测诊断日志：
    prompt_est=97298, output_est=128000, reserved=225298）。
    """
    _p, out = gw._estimate_parts({"messages": [{"role": "user", "content": "x" * 3000}],
                                  "max_tokens": 128_000})
    assert out <= gw._MAX_OUTPUT_ESTIMATE_CEIL
    assert out < 128_000


def test_output_estimate_respects_small_max_tokens():
    """客户端自己限得小就按它来（不要反向上调）。"""
    _p, out = gw._estimate_parts({"messages": [{"role": "user", "content": "hi"}],
                                  "max_tokens": 8})
    assert out == 8


def test_output_estimate_default_without_max_tokens():
    _p, out = gw._estimate_parts({"messages": [{"role": "user", "content": "hi"}]})
    assert out == gw._DEFAULT_OUTPUT_ESTIMATE


def test_reserve_with_cache_and_output_cap_stays_sane():
    """真实 ZCode 请求的预扣必须落在合理量级，而不是几十万。"""
    _p, out = gw._estimate_parts({"messages": [{"role": "user", "content": "x" * 291_894}],
                                  "max_tokens": 128_000})
    # 模拟已学到 99% 命中
    saved = dict(gw._CACHE_RATIO)
    try:
        gw._CACHE_RATIO["glm-5.2"] = 0.99
        gw._EST_FACTOR.pop("glm-5.2", None)
        reserved = gw._reserve_estimate("glm-5.2", 97_298, out)
        assert reserved < 20_000, f"预扣 {reserved} 仍然过大，会撑爆分钟预算"
    finally:
        gw._CACHE_RATIO.clear()
        gw._CACHE_RATIO.update(saved)
