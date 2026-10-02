"""矩阵 13–24（设计 §18 逐行自动化，Task 4.3 / P1-13）。

编号即矩阵行；每行断言 failure_type / 状态 + 8.4 退出码。
#16/#17 为 FakeDriver 版——真机 WDA 版在 phase0/verify_p1_m4.py；
#21 的真机拦截链在 phase0/verify_p1_task33.py。
"""
from __future__ import annotations

import sqlite3

import pytest

from executor.executor import ElementNotFound
from executor.guard import EnvKind, Guard
from llm.budget import LLMBudget
from tests.fault_injection.fi_support import (
    El,
    FakeDS,
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

NON_IDEM_TAP = """\
  - action: tap
    target: HomeView.login_button
    idempotency: NON_IDEMPOTENT
"""


def _case(case_id: str, row: int, step: str) -> str:
    return LAUNCH.format(case_id=case_id, row=row, step=step)


# --- #13：wait_for 超时 → WAIT_TIMEOUT，不触发 LLM / 1 ---


def test_fi_13_wait_timeout_no_recovery(tmp_path):
    """目标屏已登记、别的屏在当前——「还没等到」的真 WAIT_TIMEOUT
    （页面无 marker 是 #11，多 marker 是 #12，见 test_matrix_01_12）。"""
    from agent.recovery import RecoveryEngine

    step = """\
  - wait_for:
      target: screen:ProfileView
      condition: active
      timeout: 0.2
"""
    # 页面只有 HomeView marker（目标屏没来）→ 分类为 FOUND(别的屏) → #13
    ex = FakeExecutor(find_script=[ElementNotFound("not yet")])
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              llm=FakeLLM([llm_json()]),
                              budget=LLMBudget(), sleep=lambda s: None)
    run, store, _, _ = run_matrix(
        tmp_path, _case("13", 13, step), ex=ex,
        repo=drift_repo(tmp_path), recovery=recovery)
    r = run.results[0]
    assert r.status == "FAIL" and r.failure_type == "WAIT_TIMEOUT"
    assert run.exit_code == 1
    # 未触发 LLM：llm.calls 只在恢复时才有——wait 未进恢复管线
    conn = sqlite3.connect(tmp_path / "trace.db")
    reviews = conn.execute("SELECT COUNT(*) FROM recovery_reviews").fetchone()
    assert reviews[0] == 0


# --- #14：断言期望值不符 → ASSERTION_VALUE_MISMATCH，无 Recovery / 1 ---


def test_fi_14_assertion_value_mismatch_no_recovery(tmp_path):
    from agent.recovery import RecoveryEngine

    step = """\
  - assertion:
      target: HomeView.login_button
      condition: text_equals
      expected: "unexpected"
"""
    ex = FakeExecutor()
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              llm=FakeLLM([llm_json()]),
                              budget=LLMBudget(), sleep=lambda s: None)
    run, *_ = run_matrix(
        tmp_path, _case("14", 14, step), ex=ex,
        repo=drift_repo(tmp_path), recovery=recovery)
    r = run.results[0]
    assert r.status == "FAIL" \
        and r.failure_type == "ASSERTION_VALUE_MISMATCH"
    assert run.exit_code == 1
    assert "recovery_attempted" not in r.detail, "H6：值断言失败不进恢复"


# --- #15：断言目标 ID 漂移 → RECOVERED（context=assertion_target）/ 5 ---


def test_fi_15_assertion_target_drift_recovered(tmp_path):
    from agent.recovery import RecoveryEngine

    step = """\
  - assertion:
      target: HomeView.login_button
      condition: exists
      timeout: 0.2
"""
    ex = FakeExecutor(find_script=[ElementNotFound("drifted"),
                                   ElementNotFound("drifted"), El(), El()])
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              llm=FakeLLM([llm_json()]),
                              budget=LLMBudget(), sleep=lambda s: None)
    run, store, _, _ = run_matrix(
        tmp_path, _case("15", 15, step), ex=ex,
        repo=drift_repo(tmp_path), recovery=recovery)
    r = run.results[0]
    assert r.status == "RECOVERED" and run.exit_code == 5
    conn = sqlite3.connect(tmp_path / "trace.db")
    row = conn.execute(
        "SELECT detail_json FROM steps WHERE status='RECOVERED'").fetchone()
    assert row and "assertion_target" in (row[0] or "")


# --- #16：WDA 未发出非幂等前挂 → 重启 + 重跑 attempt=2（真机版 verify 脚本）---


TWO_TAPS = """\
schema_version: "0.2"
id: fi_{case_id}
name: wda
suite: smoke
tags: [smoke]
steps:
  - action: launch_app
  - action: tap
    target: HomeView.login_button
    idempotency: IDEMPOTENT
  - action: tap
    target: HomeView.signin_button
    idempotency: IDEMPOTENT
"""


def test_fi_16_wda_death_before_non_idempotent_rerun(tmp_path):
    from agent.recovery import RecoveryEngine

    ds = FakeDS(die_on=2)   # launch 不 ensure（app 级）；tap2 的 ensure 死
    ex = FakeExecutor()
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              sleep=lambda s: None)
    run, store, ds2, _ = run_matrix(
        tmp_path, TWO_TAPS.format(case_id="16"), ex=ex, ds=ds,
        repo=drift_repo(tmp_path), recovery=recovery)
    assert run.results[0].status == "PASS" and run.exit_code == 0
    assert ds.restarts == 1
    rows = sqlite3.connect(tmp_path / "trace.db").execute(
        "SELECT attempt, status FROM testcase_runs ORDER BY id").fetchall()
    assert rows == [(1, "INFRA_FAILURE"), (2, "PASS")]


# --- #17：WDA 已发出非幂等后挂 → INFRA_FAILURE 不重跑 / 2 ---


def test_fi_17_wda_death_after_non_idempotent_no_rerun(tmp_path):
    from agent.recovery import RecoveryEngine

    case = LAUNCH.format(case_id="17", row=17, step=NON_IDEM_TAP) + \
        """\
  - action: tap
    target: HomeView.signin_button
    idempotency: IDEMPOTENT
"""
    ds = FakeDS(die_on=2)   # tap1（非幂等）已发出后 tap2 前死
    ex = FakeExecutor()
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              sleep=lambda s: None)
    run, store, ds2, _ = run_matrix(
        tmp_path, case, ex=ex, ds=ds,
        repo=drift_repo(tmp_path), recovery=recovery)
    r = run.results[0]
    assert r.status == "INFRA_FAILURE" and r.failure_type == "WDA_FAILURE"
    assert run.exit_code == 2
    assert "rerun_skipped" in r.detail and ds.restarts == 0
    rows = sqlite3.connect(tmp_path / "trace.db").execute(
        "SELECT attempt FROM testcase_runs").fetchall()
    assert len(rows) == 1, "不得重跑（7.5）"


# --- #18：POST_DISPATCH 超时 + 非幂等 + postcondition 成立 → RECOVERED / 5 ---


def test_fi_18_post_dispatch_postcondition_recovered(tmp_path):
    from agent.recovery import RecoveryEngine

    step = """\
  - action: tap
    target: HomeView.login_button
    idempotency: NON_IDEMPOTENT
    postcondition:
      target: screen:HomeView
      condition: active
      timeout: 1
"""
    ex = FakeExecutor(tap_fail_times=1)
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              sleep=lambda s: None)
    run, *_ = run_matrix(
        tmp_path, _case("18", 18, step), ex=ex,
        repo=drift_repo(tmp_path), recovery=recovery)
    r = run.results[0]
    assert r.status == "RECOVERED" and run.exit_code == 5
    assert ex.tap_calls == 1, "RECOVERED(kind=postcondition) 不再次点击（H7）"


# --- #19：同上但无 postcondition → ACTION_OUTCOME_UNKNOWN / 1 ---


def test_fi_19_post_dispatch_no_postcondition_fails(tmp_path):
    from agent.recovery import RecoveryEngine

    ex = FakeExecutor(tap_fail_times=1)
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              sleep=lambda s: None)
    run, *_ = run_matrix(
        tmp_path, _case("19", 19, NON_IDEM_TAP), ex=ex,
        repo=drift_repo(tmp_path), recovery=recovery)
    r = run.results[0]
    assert r.status == "FAIL" \
        and r.failure_type == "ACTION_OUTCOME_UNKNOWN"
    assert run.exit_code == 1


# --- #20：cleanup 失败 → ENVIRONMENT_FAILURE，套件终止 / 2 ---


def test_fi_20_cleanup_failure_suite_aborts(tmp_path):
    from environment.manager import CleanupError

    class BoomEnv:
        def prepare(self, tc):
            pass

        def cleanup(self, tc=None):
            raise CleanupError("cleanup boom")

    second = LAUNCH.format(case_id="20b", row=20, step=DRIFT_TAP)
    cases = [load_case(_case("20a", 20, DRIFT_TAP)),
             load_case(second)]
    run, *_ = run_matrix(
        tmp_path, _case("20a", 20, DRIFT_TAP), ex=FakeExecutor(),
        repo=drift_repo(tmp_path), env=BoomEnv(), cases=cases)
    r = run.results[0]
    assert r.status == "ENVIRONMENT_FAILURE"
    assert r.detail.get("aborted_suite") is True
    assert len(run.results) == 1, "套件中止：第二条不执行"
    assert run.exit_code == 2


# --- #21：App build 与 metadata 不一致 → BUILD_METADATA_MISMATCH / 3 ---


def test_fi_21_app_build_metadata_mismatch_exit3(tmp_path, monkeypatch):
    """真机拦截链端到端在 phase0/verify_p1_task33.py（注入构建+读回）；
    这里用注入读取验 CLI 拦截语义（矩阵 #21 的未启动用例 + exit 3）。"""
    import json

    from cli.main import main
    from source.build_identity import AppIdentity

    monkeypatch.setattr("source.build_identity.read_app_identity",
                        lambda u, b: AppIdentity("deadbeef", "local"))
    suites = tmp_path / "suites"
    suites.mkdir()
    (suites / "a_001.yaml").write_text(
        'schema_version: "0.2"\nid: a_001\nname: x\nsuite: smoke\n'
        "tags: [smoke]\nsteps:\n  - action: launch_app\n", encoding="utf-8")
    meta = tmp_path / "source_metadata.json"
    meta.write_text(json.dumps({"build": "local", "git_commit": "a76d449"}),
                    encoding="utf-8")
    code = main(["run", "--suite", "smoke", "--suites-root", str(suites),
                 "--db", str(tmp_path / "trace.db"), "--udid", "UDID",
                 "--bundle-id", "com.phaset0.logindemo",
                 "--metadata", str(meta)])
    assert code == 3
    rows = sqlite3.connect(tmp_path / "trace.db").execute(
        "SELECT status FROM testcase_runs").fetchall()
    assert rows == [], "拦截必须发生在用例启动前"


# --- #22：CRITICAL 元素 + production 环境 → SECURITY_BLOCKED / 4 ---


def test_fi_22_critical_element_production_blocked(tmp_path):
    from agent.recovery import RecoveryEngine

    step = """\
  - action: tap
    target: HomeView.confirm_pay_button
    idempotency: IDEMPOTENT
"""
    # production + allow_production：HIGH/CRITICAL 仍拦（10.1 规则 1 的
    # sandbox 例外不覆盖 production）
    guard = Guard(EnvKind.PRODUCTION, allow_production=True)
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              sleep=lambda s: None)
    run, *_ = run_matrix(
        tmp_path, _case("22", 22, step), ex=FakeExecutor(),
        repo=drift_repo(tmp_path), recovery=recovery, guard=guard)
    r = run.results[0]
    assert r.status == "BLOCKED", "8.1：Guard 拦截是 BLOCKED 不是 FAIL"
    assert r.failure_type == "SECURITY_BLOCKED"
    assert run.exit_code == 4, "8.4：安全策略拦截 exit 4"


# --- #23：用例含 sleep / 未知 target → mta lint 报错 / 3 ---


def test_fi_23_sleep_and_unknown_target_lint_exit3(tmp_path):
    from cli.main import main

    repo = drift_repo(tmp_path)
    gen = tmp_path / "generated" / "local"

    sleep_case = tmp_path / "sleep_case.yaml"
    sleep_case.write_text(
        'schema_version: "0.2"\nid: fi_23a\nname: sleep\nsuite: smoke\n'
        "tags: [smoke]\nsteps:\n  - action: launch_app\n"
        "  - action: sleep\n", encoding="utf-8")
    code = main(["lint", str(sleep_case),
                 "--generated", str(gen)])
    assert code == 3, "H11：sleep 是 lint ERROR"

    unknown_case = tmp_path / "unknown_case.yaml"
    unknown_case.write_text(
        'schema_version: "0.2"\nid: fi_23b\nname: unknown\nsuite: smoke\n'
        "tags: [smoke]\nsteps:\n  - action: launch_app\n"
        "  - action: tap\n    target: HomeView.not_registered_anywhere\n",
        encoding="utf-8")
    code = main(["lint", str(unknown_case),
                 "--generated", str(gen)])
    assert code == 3, "4.1：未知 target 是 lint ERROR"


# --- #24：Recovery 成功但 cleanup 失败 → 最终 ENVIRONMENT_FAILURE / 2 ---


def test_fi_24_recovery_then_cleanup_fail(tmp_path):
    """8.1 优先级：ENVIRONMENT_FAILURE > RECOVERED——恢复成功不掩盖
    cleanup 失败（RECOVERED 不得变成变相 PASS）。"""
    from agent.recovery import RecoveryEngine
    from environment.manager import CleanupError

    class BoomCleanup:
        def prepare(self, tc):
            pass

        def cleanup(self, tc=None):
            raise CleanupError("cleanup after recovery")

    ex = FakeExecutor(find_script=[ElementNotFound("render delay"), El()])
    recovery = RecoveryEngine(repo=drift_repo(tmp_path),
                              sleep=lambda s: None)
    run, *_ = run_matrix(
        tmp_path, _case("24", 24, DRIFT_TAP), ex=ex,
        repo=drift_repo(tmp_path), recovery=recovery, env=BoomCleanup())
    r = run.results[0]
    assert r.status == "ENVIRONMENT_FAILURE", \
        f"恢复成功也不得掩盖 cleanup 失败: {r.status}"
    assert run.exit_code == 2
