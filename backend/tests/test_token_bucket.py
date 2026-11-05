"""TPM 滚动窗口 + 自适应预算单测。

核心不变量：
1. 窗口内消耗 + 新请求 ≤ 预算 才放行，否则给出等待秒数
2. 单条请求比整个预算还大时，窗口空就放行（否则永远发不出去）
3. 成功用实际 usage 结算，失败退回预留（失败不该白占预算）
4. AIMD：窗口打满才降预算；窗口很空时吃 429 不降（避免误伤）
"""
from __future__ import annotations

import time

import pytest

from app.relay import token_bucket as tb

PID = 7
ACCT = "acct-a"
MODEL = "glm-5.2"


@pytest.fixture(autouse=True)
def _reset():
    tb.reset_tpm_state()
    tb._buckets.clear()
    yield
    tb.reset_tpm_state()
    tb._buckets.clear()


def _state():
    return tb._get_tpm_state(PID, ACCT, MODEL)


def test_fresh_state_starts_at_seed():
    st = _state()
    assert st.budget == float(tb._TPM_SEED)
    assert st.spent() == 0


def test_reserve_within_budget_is_allowed():
    allowed, wait = tb.tpm_reserve(PID, ACCT, MODEL, 100_000)
    assert allowed is True
    assert wait == 0.0
    assert _state().spent() == 100_000


def test_reserve_over_budget_is_blocked_with_wait():
    tb.tpm_reserve(PID, ACCT, MODEL, tb._TPM_SEED - 10_000)
    allowed, wait = tb.tpm_reserve(PID, ACCT, MODEL, 100_000)
    assert allowed is False
    assert wait > 0


def test_oversized_single_request_allowed_once_on_empty_window():
    """单条请求比整个预算还大：窗口空时放行，否则永远发不出去。"""
    oversized = tb._TPM_SEED * 2
    allowed, _ = tb.tpm_reserve(PID, ACCT, MODEL, oversized)
    assert allowed is True


def test_oversized_request_blocked_when_window_not_empty():
    tb.tpm_reserve(PID, ACCT, MODEL, 1_000)
    allowed, wait = tb.tpm_reserve(PID, ACCT, MODEL, tb._TPM_SEED * 2)
    assert allowed is False
    assert wait > 0


def test_settle_replaces_reservation_with_actual():
    tb.tpm_reserve(PID, ACCT, MODEL, 100_000)
    tb.tpm_settle(PID, ACCT, MODEL, 100_000, 300_000)
    assert _state().spent() == 300_000


def test_settle_smaller_actual_releases_headroom():
    tb.tpm_reserve(PID, ACCT, MODEL, 300_000)
    tb.tpm_settle(PID, ACCT, MODEL, 300_000, 50_000)
    assert _state().spent() == 50_000


def test_release_frees_reservation():
    tb.tpm_reserve(PID, ACCT, MODEL, 200_000)
    tb.tpm_release(PID, ACCT, MODEL, 200_000)
    assert _state().spent() == 0


def test_failed_requests_do_not_accumulate_spend():
    """连续失败的请求都退回了，不应把预算抽干。"""
    for _ in range(20):
        allowed, _ = tb.tpm_reserve(PID, ACCT, MODEL, 100_000)
        assert allowed is True
        tb.tpm_release(PID, ACCT, MODEL, 100_000)
    assert _state().spent() == 0


def test_on_success_raises_budget_to_fit_actual():
    st = _state()
    before = st.budget
    actual = before * 2
    st.on_success(actual)
    assert st.budget >= actual


def test_on_rate_limit_lowers_budget_when_window_busy():
    st = _state()
    before = st.budget
    tb.tpm_reserve(PID, ACCT, MODEL, int(before * 0.8))
    tb.tpm_on_rate_limit(PID, ACCT, MODEL, int(before * 0.8))
    assert st.budget < before


def test_on_rate_limit_ignored_when_window_mostly_empty():
    """窗口很空却吃 429 → 多半是并发/RPM 限制，不该动预算。"""
    st = _state()
    before = st.budget
    tb.tpm_on_rate_limit(PID, ACCT, MODEL, 1_000)
    assert st.budget == before


def test_budget_never_below_floor():
    st = _state()
    # 反复触发降档（绕过 30 秒冷却，直接改时间戳）
    for _ in range(50):
        st.window.clear()
        st.window.append((time.monotonic(), st.budget))
        st.last_decrease_at = 0.0
        st.on_rate_limit(st.budget)
    assert st.budget >= st.floor
    assert st.floor >= tb._TPM_FLOOR


def test_budget_will_not_drop_below_largest_successful_request():
    """实测回归：预算被降到 51840，而真实请求 365235 →
    每条请求都"超预算"，每窗口只能发一条，退化成 60 秒一发。"""
    st = _state()
    big = 365235
    st.on_success(big)                      # 这条请求证明上游至少容得下它
    st.budget = tb._TPM_FLOOR               # 模拟被 429 一路压到地板
    st.window.clear()
    st.window.append((time.monotonic(), st.budget))
    st.last_decrease_at = 0.0
    st.on_rate_limit(50_000)                # 再吃一个 429
    assert st.budget >= big * 1.05          # 不得低于已成功请求规模


def test_budget_hold_logged_when_already_at_evidence_floor(caplog=None):
    st = _state()
    st.on_success(400_000)
    st.budget = st.max_success * 1.05
    st.window.clear()
    st.window.append((time.monotonic(), st.budget))
    st.last_decrease_at = 0.0
    before = st.budget
    st.on_rate_limit(100_000)
    assert st.budget == before              # 压到底就不再压


def test_oversized_request_is_never_permanently_blocked():
    """单条请求超过整个分钟预算时，窗口空着就必须放行一次。

    否则那条请求永远发不出去（等多久都不够），长会话 agent 会直接卡死。
    注意预算状态是 per-account 的，别指望新账号能继承别的账号学到的规模。
    """
    big = 365_000
    ok, wait = tb.tpm_reserve(PID, ACCT, MODEL, big)
    assert ok is True, f"单条超大请求被拒绝（需等 {wait}s），长会话会永久卡住"


def test_account_learns_its_own_request_size():
    """同一个大请求重复出现时，该账号的预算应被抬到容得下它。"""
    big = 365_000
    ok, _ = tb.tpm_reserve(PID, ACCT, MODEL, big)
    assert ok is True
    tb.tpm_settle(PID, ACCT, MODEL, big, big)
    st = _state()
    assert st.budget >= big, f"预算 {st.budget} 仍小于已成功的请求 {big}"


def test_budget_never_above_ceil():
    st = _state()
    st.on_success(tb._TPM_CEIL * 10)
    assert st.budget <= tb._TPM_CEIL


def test_disabled_tpm_always_allows(monkeypatch):
    monkeypatch.setattr(tb, "_MODEL_TPM_OVERRIDES", {MODEL: 0})
    tb.reset_tpm_state()
    allowed, wait = tb.tpm_reserve(PID, ACCT, MODEL, 10**9)
    assert allowed is True and wait == 0.0
    assert tb.tpm_remaining(PID, ACCT, MODEL) == float("inf")


def test_wait_seconds_read_does_not_reserve():
    tb.tpm_reserve(PID, ACCT, MODEL, tb._TPM_SEED - 5_000)
    before = _state().spent()
    wait = tb.tpm_wait(PID, ACCT, MODEL, 100_000)
    assert wait > 0
    assert _state().spent() == before  # 只读，不记账


def test_different_accounts_have_independent_budgets():
    tb.tpm_reserve(PID, "acct-a", MODEL, tb._TPM_SEED)
    allowed, _ = tb.tpm_reserve(PID, "acct-b", MODEL, tb._TPM_SEED)
    assert allowed is True


def test_rpm_bucket_still_works_independently():
    assert tb.acquire_rpm(1, MODEL) is True
    tb.release_rpm(1, MODEL)
    assert tb.available_tokens(1, MODEL) >= 1


def test_ceil_div_seconds():
    assert tb.ceil_div_seconds(0.2) == 1
    assert tb.ceil_div_seconds(1.0) == 1
    assert tb.ceil_div_seconds(1.1) == 2
