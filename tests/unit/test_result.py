"""Task 2.5 / P1-07：结果模型 + 退出码（设计 8 章）。

口径写死在本文件 docstring。全部纯函数（H18）。

8.1 testcase 终态优先级：ENV > INFRA > BLOCKED > FAIL > RECOVERED > PASS
8.3 通过率：PASS Rate = PASS/TOTAL，Recovery Rate = RECOVERED/TOTAL，
    **不得**用 (PASS+RECOVERED)/TOTAL
8.4 退出码：0 全 PASS / 1 有 FAIL / 2 有 INFRA|ENV / 3 前置错误 / 4 BLOCKED
    / 5 无 FAIL 但有 RECOVERED；并存时 `3 > 2 > 4 > 1 > 5`
8.5 JUnit 映射：PASS→pass；FAIL→failure；RECOVERED→failure[type=RECOVERED_NEEDS_REVIEW]；
    INFRA/ENV/BLOCKED→error
H5：RECOVERED ≠ PASS，不计入通过率，退出码非 0，必须人工确认
"""

from __future__ import annotations

import pytest

from runner.result import (
    ExitCode,
    RunResult,
    TestcaseResult,
    compute_exit_code,
    junit_status_for,
    summarize_statuses,
)


# --- 8.4 退出码优先级 ---

def test_exit_code_all_pass_is_zero():
    assert compute_exit_code(["PASS", "PASS"]) == 0


def test_exit_code_fail_is_one():
    assert compute_exit_code(["PASS", "FAIL"]) == 1


def test_exit_code_infra_or_env_is_two():
    assert compute_exit_code(["PASS", "INFRA_FAILURE"]) == 2
    assert compute_exit_code(["PASS", "ENVIRONMENT_FAILURE"]) == 2


def test_exit_code_blocked_is_four():
    assert compute_exit_code(["PASS", "BLOCKED"]) == 4


def test_exit_code_recovered_only_is_five():
    """8.4 码 5：无 FAIL 但存在 RECOVERED（需人工确认）。"""
    assert compute_exit_code(["PASS", "RECOVERED"]) == 5


def test_exit_code_preflight_error_is_three():
    """码 3：配置/lint/metadata 不匹配等前置错误（无 testcase 结果）。"""
    assert compute_exit_code([], preflight_error=True) == 3


@pytest.mark.parametrize("statuses,expected", [
    # 3 > 2 > 4 > 1 > 5
    (["FAIL", "INFRA_FAILURE", "BLOCKED", "RECOVERED"], 2),
    (["FAIL", "BLOCKED", "RECOVERED"], 4),          # 4 > 1 > 5
    (["FAIL", "RECOVERED"], 1),                      # 1 > 5
    (["PASS", "RECOVERED"], 5),
    (["BLOCKED", "FAIL"], 4),
])
def test_exit_code_priority_order(statuses, expected):
    assert compute_exit_code(statuses) == expected


def test_exit_code_priority_3_beats_everything():
    """码 3（前置错误）最高——配置错了，跑出来的结果没意义。"""
    assert compute_exit_code(["FAIL", "INFRA_FAILURE", "BLOCKED"],
                             preflight_error=True) == 3


def test_empty_run_is_zero_not_error():
    assert compute_exit_code([]) == 0


def test_exit_code_enum_values_match_design_8_4():
    """8.4 表格里的码值是外部契约（CI 依赖），逐个钉住。"""
    assert ExitCode.OK == 0
    assert ExitCode.FAIL == 1
    assert ExitCode.INFRA == 2
    assert ExitCode.PREFLIGHT == 3
    assert ExitCode.BLOCKED == 4
    assert ExitCode.RECOVERED == 5


# --- 8.3 通过率口径 ---

def test_pass_rate_excludes_recovered_h5():
    """H5：RECOVERED ≠ PASS，不计入通过率。"""
    s = summarize_statuses(["PASS", "PASS", "PASS", "RECOVERED"])
    assert s.pass_rate == 0.75
    assert s.recovery_rate == 0.25


def test_pass_rate_denominator_is_total_including_skipped():
    s = summarize_statuses(["PASS", "PASS", "SKIPPED"])
    assert s.total == 3
    assert s.pass_rate == 2 / 3


def test_pass_rate_regression_assertion_not_combined():
    """回归断言：PASS Rate ≠ (PASS+RECOVERED)/TOTAL（8.3 明令禁止）。"""
    statuses = ["PASS", "RECOVERED", "RECOVERED", "RECOVERED"]
    s = summarize_statuses(statuses)
    combined = (s.counts.get("PASS", 0) + s.counts.get("RECOVERED", 0)) / s.total
    assert s.pass_rate != combined, "PASS Rate 不得含 RECOVERED"
    assert s.pass_rate == 0.25 and combined == 1.0


def test_summary_counts_by_status():
    s = summarize_statuses(["PASS", "FAIL", "FAIL", "RECOVERED"])
    assert s.counts["PASS"] == 1
    assert s.counts["FAIL"] == 2
    assert s.counts["RECOVERED"] == 1
    assert s.total == 4


def test_summary_empty_run_is_zero_rates():
    s = summarize_statuses([])
    assert s.total == 0 and s.pass_rate == 0.0 and s.recovery_rate == 0.0


def test_summary_aggregates_attempt_counts():
    s = summarize_statuses(["PASS", "FAIL"], attempts=2)
    assert s.llm_calls == 2, "llm_calls 走 attempts 汇总（P1 由 suite 累加）"


# --- 8.5 JUnit 映射 ---

@pytest.mark.parametrize("status,junit_status", [
    ("PASS", "pass"),
    ("FAIL", "failure"),
    ("RECOVERED", "failure"),
    ("INFRA_FAILURE", "error"),
    ("ENVIRONMENT_FAILURE", "error"),
    ("BLOCKED", "error"),
    ("SKIPPED", "skipped"),
    ("ABORTED", "error"),
])
def test_junit_status_mapping(status, junit_status):
    assert junit_status_for(status).status == junit_status


def test_recovered_junit_has_special_type():
    """8.5：RECOVERED→failure type="RECOVERED_NEEDS_REVIEW"（保证 CI 能看见）。"""
    j = junit_status_for("RECOVERED")
    assert j.status == "failure"
    assert j.type == "RECOVERED_NEEDS_REVIEW"
    assert j.message, "RECOVERED 需人工确认，必须有 message"


def test_fail_junit_carries_failure_type_as_message():
    j = junit_status_for("FAIL", failure_type="ELEMENT_NOT_FOUND")
    assert "ELEMENT_NOT_FOUND" in j.message


# --- RunResult / TestcaseResult ---

def test_run_result_carries_exit_code_and_counts():
    r = RunResult(run_id="run_1")
    r.add(TestcaseResult(testcase_id="login_001", status="PASS"))
    r.add(TestcaseResult(testcase_id="logout_001", status="RECOVERED"))
    assert r.exit_code == 5
    assert r.summary.counts == {"PASS": 1, "RECOVERED": 1}
    assert r.summary.total == 2


def test_run_result_exit_code_reflects_worst_status():
    r = RunResult(run_id="run_1")
    r.add(TestcaseResult(testcase_id="a", status="PASS"))
    r.add(TestcaseResult(testcase_id="b", status="FAIL"))
    r.add(TestcaseResult(testcase_id="c", status="PASS"))
    assert r.exit_code == 1


def test_run_result_preflight_error_forces_code_3():
    r = RunResult(run_id="run_1", preflight_error="lint: 2 errors")
    r.add(TestcaseResult(testcase_id="a", status="PASS"))
    assert r.exit_code == 3
    assert "lint" in r.preflight_error


def test_testcase_result_duration_defaults_none_not_zero():
    """0 会被当「瞬间完成」的假数据（与 _duration_ms 同理）。"""
    t = TestcaseResult(testcase_id="a", status="PASS")
    assert t.duration_ms is None


def test_testcase_result_attribution_defaults_untriaged():
    t = TestcaseResult(testcase_id="a", status="FAIL")
    assert t.failure_attribution == "UNTRIAGED"


def test_add_rejects_unknown_status():
    """fail-loud：未知状态不静默（与 tracer.storage 同纪律）。"""
    r = RunResult(run_id="run_1")
    with pytest.raises(ValueError):
        r.add(TestcaseResult(testcase_id="a", status="WHATEVER"))


def test_summary_recovered_never_counts_as_pass_h5():
    """H5 显式回归：全部 RECOVERED 时 pass_rate 必须是 0。"""
    r = RunResult(run_id="run_1")
    r.add(TestcaseResult(testcase_id="a", status="RECOVERED"))
    assert r.summary.pass_rate == 0.0
    assert r.exit_code == 5, "RECOVERED 的退出码非 0（H5）"


# --- 终态取自 tracer.storage，不重复实现（8.1 唯一实现） ---

def test_summarize_uses_tracer_aggregate_for_worst_status():
    from tracer.storage import aggregate_status
    assert summarize_statuses(
        ["RECOVERED", "ENVIRONMENT_FAILURE"]).worst_status == \
        aggregate_status(["RECOVERED", "ENVIRONMENT_FAILURE"])


def test_worst_status_ignores_attempt_states():
    s = summarize_statuses(["PASS", "RUNNING"])
    assert s.worst_status == "PASS"
