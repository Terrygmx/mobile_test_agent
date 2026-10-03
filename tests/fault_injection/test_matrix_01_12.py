"""矩阵 01–12（设计 §18 逐行自动化，Task 4.3 / P1-13）。

编号即矩阵行；每行断言 failure_type / 状态 + 8.4 退出码。
全部 FakeDriver/FakeLLM（无网络无真机）；#2 的真机版（改名重编译）在
phase0/verify_p1_m4.py。
"""
from __future__ import annotations

from executor.executor import ElementNotFound
from llm.budget import BudgetConfig, LLMBudget
from tests.fault_injection.fi_support import (
    El,
    FakeExecutor,
    FakeLLM,
    drift_repo,
    llm_json,
    load_case,
    run_matrix,
)

LAUNCH = """\
schema_version: "0.2"
id: fi_{case_id}
name: matrix {row}
suite: smoke
tags: [smoke]
steps:
  - action: launch_app
{step}
"""

DRIFT_TAP = """\
  - action: tap
    target: HomeView.login_button
    idempotency: IDEMPOTENT
"""


def _case(case_id: str, row: int, step: str) -> str:
    return LAUNCH.format(case_id=case_id, row=row, step=step)


# --- #1：locator 不存在（稳定 build，--no-llm）→ ELEMENT_NOT_FOUND / 1 ---


def test_fi_01_locator_missing_no_llm(tmp_path):
    """--no-llm 等价形态：引擎无 LLM，确定性半边穷尽后维持原症状。"""
    from agent.recovery import RecoveryEngine

    ex = FakeExecutor(find_script=[ElementNotFound("never")])
    recovery = RecoveryEngine(sleep=lambda s: None)
    run, store, _, _ = run_matrix(
        tmp_path, _case("01", 1, DRIFT_TAP), ex=ex,
        repo=drift_repo(tmp_path), recovery=recovery)
    r = run.results[0]
    assert r.status == "FAIL" and r.failure_type == "ELEMENT_NOT_FOUND"
    assert run.exit_code == 1
    stages = (r.detail.get("recovery_attempted") or {}).get("stages", [])
    assert {"stage": "llm", "outcome": "disabled"} in stages, \
        "无 LLM 不是静默无恢复——disabled 如实可见"


# --- #2：locator 漂移 + LLM → RECOVERED / 5（真机版在 verify_p1_m4） ---


def test_fi_02_locator_drift_llm_recovered(tmp_path):
    from agent.recovery import RecoveryEngine

    ex_find = [ElementNotFound("drifted"), ElementNotFound("drifted"), El()]
    ex = FakeExecutor(find_script=ex_find)
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              llm=FakeLLM([llm_json()]),
                              budget=LLMBudget(), sleep=lambda s: None)
    run, store, _, _ = run_matrix(
        tmp_path, _case("02", 2, DRIFT_TAP), ex=ex,
        repo=drift_repo(tmp_path), recovery=recovery)
    r = run.results[0]
    assert r.status == "RECOVERED"
    assert r.detail["recovery_kinds"] == ["llm"]
    assert run.exit_code == 5


# --- #3：locator 多匹配 → AMBIGUOUS_ELEMENT / 1 ---


def test_fi_03_locator_ambiguous(tmp_path):
    from agent.recovery import RecoveryEngine

    ex = FakeExecutor(find_script=[[El(), El()]])
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              sleep=lambda s: None)
    run, *_ = run_matrix(
        tmp_path, _case("03", 3, DRIFT_TAP), ex=ex,
        repo=drift_repo(tmp_path), recovery=recovery)
    r = run.results[0]
    assert r.status == "FAIL" and r.failure_type == "AMBIGUOUS_ELEMENT"
    assert run.exit_code == 1


# --- #4：LLM 候选多匹配 → LLM_TARGET_AMBIGUOUS，不执行 / 1 ---


def test_fi_04_llm_candidate_ambiguous(tmp_path):
    from agent.recovery import RecoveryEngine

    ex = FakeExecutor(find_script=[ElementNotFound("drifted"),
                                   ElementNotFound("drifted"),
                                   [El(), El()]])
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              llm=FakeLLM([llm_json()]),
                              budget=LLMBudget(), sleep=lambda s: None)
    run, store, _, ex2 = run_matrix(
        tmp_path, _case("04", 4, DRIFT_TAP), ex=ex,
        repo=drift_repo(tmp_path), recovery=recovery)
    r = run.results[0]
    assert r.status == "FAIL" and r.failure_type == "LLM_TARGET_AMBIGUOUS"
    assert run.exit_code == 1
    assert ex.tap_calls == 0, "候选未通过校验不得执行"


# --- #5：LLM 候选类型不符 → LLM_TARGET_TYPE_MISMATCH / 1 ---


def test_fi_05_llm_candidate_type_mismatch(tmp_path):
    from agent.recovery import RecoveryEngine

    ex = FakeExecutor(find_script=[ElementNotFound("d"), ElementNotFound("d"),
                                   El("XCUIElementTypeTextField")])
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              llm=FakeLLM([llm_json()]),
                              budget=LLMBudget(), sleep=lambda s: None)
    run, *_ = run_matrix(
        tmp_path, _case("05", 5, DRIFT_TAP), ex=ex,
        repo=drift_repo(tmp_path), recovery=recovery)
    r = run.results[0]
    assert r.status == "FAIL" and r.failure_type == "LLM_TARGET_TYPE_MISMATCH"
    assert run.exit_code == 1


# --- #6：LLM 候选不在当前 Screen → LLM_TARGET_SCREEN_MISMATCH / 1 ---


def test_fi_06_llm_candidate_screen_mismatch(tmp_path):
    from agent.recovery import RecoveryEngine

    ex = FakeExecutor(find_script=[ElementNotFound("d"), ElementNotFound("d"),
                                   El()])
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              llm=FakeLLM([llm_json(value="ghost_button")]),
                              budget=LLMBudget(), sleep=lambda s: None)
    run, *_ = run_matrix(
        tmp_path, _case("06", 6, DRIFT_TAP), ex=ex,
        repo=drift_repo(tmp_path), recovery=recovery)
    r = run.results[0]
    assert r.status == "FAIL" \
    and r.failure_type == "LLM_TARGET_SCREEN_MISMATCH"
    assert run.exit_code == 1


# --- #7：候选 effective_risk ≥ MEDIUM → LLM_RISK_BLOCKED / 1 ---


def test_fi_07_llm_candidate_risk_blocked(tmp_path):
    from agent.recovery import RecoveryEngine

    ex = FakeExecutor(find_script=[ElementNotFound("d"), ElementNotFound("d"),
                                   El()])
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              llm=FakeLLM([llm_json(value="pay_button")]),
                              budget=LLMBudget(), sleep=lambda s: None)
    run, *_ = run_matrix(
        tmp_path, _case("07", 7, DRIFT_TAP), ex=ex,
        repo=drift_repo(tmp_path), recovery=recovery)
    r = run.results[0]
    assert r.status == "FAIL" and r.failure_type == "LLM_RISK_BLOCKED"
    assert run.exit_code == 1


# --- #8：LLM 返回 risk_level: LOW 但元素是 HIGH → 忽略 LLM 值按 HIGH 拦 / 1 ---


def test_fi_08_llm_risk_level_ignored(tmp_path):
    from agent.recovery import RecoveryEngine

    ex = FakeExecutor(find_script=[ElementNotFound("d"), ElementNotFound("d"),
                                   El()])
    recovery = RecoveryEngine(
        repo=drift_repo(tmp_path),
        llm=FakeLLM([llm_json(value="pay_button", risk_level="LOW")]),
        budget=LLMBudget(), sleep=lambda s: None)
    run, *_ = run_matrix(
        tmp_path, _case("08", 8, DRIFT_TAP), ex=ex,
        repo=drift_repo(tmp_path), recovery=recovery)
    r = run.results[0]
    assert r.status == "FAIL" and r.failure_type == "LLM_RISK_BLOCKED", \
        "risk_level 被忽略（H4），按 metadata HIGH 拦截"
    assert run.exit_code == 1


# --- #9：LLM 返回非法 JSON → LLM_INVALID_OUTPUT / 1 ---


def test_fi_09_llm_invalid_json(tmp_path):
    from agent.recovery import RecoveryEngine

    ex = FakeExecutor(find_script=[ElementNotFound("d")])
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              llm=FakeLLM(["trust me it is signin_button"]),
                              budget=LLMBudget(), sleep=lambda s: None)
    run, *_ = run_matrix(
        tmp_path, _case("09", 9, DRIFT_TAP), ex=ex,
        repo=drift_repo(tmp_path), recovery=recovery)
    r = run.results[0]
    assert r.status == "FAIL" and r.failure_type == "LLM_INVALID_OUTPUT"
    assert run.exit_code == 1


# --- #10：budget 用尽 → LLM_BUDGET_EXCEEDED 且未发起新调用 / 1 ---


def test_fi_10_budget_exhausted_no_new_api_call(tmp_path):
    from agent.recovery import RecoveryEngine

    # review_m4_task43 P3-2：原两段 no-op .replace() 已删——_case 直接
    # 产出不同 id（fi_10a/fi_10b），无需「看起来在做事」的字符串操作。
    cases = [load_case(_case("10a", 10, DRIFT_TAP)),
             load_case(_case("10b", 10, DRIFT_TAP))]
    ex = FakeExecutor(find_script=[ElementNotFound("d")])
    llm = FakeLLM([llm_json()])
    recovery = RecoveryEngine(
        repo=drift_repo(tmp_path), llm=llm,
        budget=LLMBudget(config=BudgetConfig(max_calls_per_run=1,
                                             breaker_consecutive_failures=99)),
        sleep=lambda s: None)
    run, *_ = run_matrix(tmp_path, _case("10a", 10, DRIFT_TAP), ex=ex,
                         repo=drift_repo(tmp_path), recovery=recovery,
                         cases=cases)
    assert run.results[0].failure_type == "LLM_TARGET_NOT_FOUND"
    assert run.results[1].failure_type == "LLM_BUDGET_EXCEEDED"
    assert len(llm.calls) == 1, "预算耗尽后不得再发调用"
    assert run.exit_code == 1


# --- #11：Screen marker 缺失 → CURRENT_SCREEN_UNKNOWN / 1 ---


def test_fi_11_screen_marker_missing(tmp_path):
    """目标屏已登记但页面无任何 marker（app 侧丢了 mtaScreen）——不是
    「还没等到」的 #13，是页面不可识别（13.2）。"""
    from agent.recovery import RecoveryEngine

    step = """\
  - wait_for:
      target: screen:HomeView
      condition: active
      timeout: 0.2
"""
    # marker find 必须失败（app 侧丢了 mtaScreen）——空 find_script 会
    # 恒返回元素让 wait 假成功
    ex = FakeExecutor(find_script=[ElementNotFound("marker gone")],
                      page_source="<App><Node name='other_thing'/></App>")
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              sleep=lambda s: None)
    run, *_ = run_matrix(
        tmp_path, _case("11", 11, step), ex=ex,
        repo=drift_repo(tmp_path), recovery=recovery)
    r = run.results[0]
    assert r.status == "FAIL" and r.failure_type == "CURRENT_SCREEN_UNKNOWN"
    assert run.exit_code == 1


# --- #12：多个 marker 且无 modal → SCREEN_AMBIGUOUS / 1 ---


def test_fi_12_markers_ambiguous_no_modal(tmp_path):
    """两个已登记 marker 同时可见、无 modal 压顶，等待第三个已登记屏——
    当前屏互斥不明（13.2）。"""
    from agent.recovery import RecoveryEngine

    step = """\
  - wait_for:
      target: screen:DetailView
      condition: active
      timeout: 0.2
"""
    two_markers = ("<App>"
                   "<Node name='screen.HomeView' visible='true'/>"
                   "<Node name='screen.ProfileView' visible='true'/>"
                   "</App>")
    # DetailView 的 marker find 必须失败（页面上没有它）——空 find_script
    # 会恒返回元素让 wait 假成功
    ex = FakeExecutor(find_script=[ElementNotFound("detail marker gone")],
                      page_source=two_markers)
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              sleep=lambda s: None)
    run, *_ = run_matrix(
        tmp_path, _case("12", 12, step), ex=ex,
        repo=drift_repo(tmp_path), recovery=recovery)
    r = run.results[0]
    assert r.status == "FAIL" and r.failure_type == "SCREEN_AMBIGUOUS"
    assert run.exit_code == 1
