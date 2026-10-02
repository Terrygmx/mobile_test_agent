"""Task 4.1：故障注入——7.5 WDA 中途故障 + 恢复引擎端到端（FakeDriver）。

设计 18 节矩阵的本任务子集（编号即矩阵行）：
  #13 wait 超时不触发恢复；#14 断言值失败无恢复；
  #16 WDA 未发出非幂等前挂 → 重启 + 重跑 attempt=2；
  #17 WDA 已发出非幂等后挂 → INFRA_FAILURE 不重跑；
  #18 POST_DISPATCH 超时 + 非幂等 + postcondition 成立 → RECOVERED(kind=
      postcondition)，未再次点击；#19 同上但 postcondition 不成立 → FAIL；
  另：settle 恢复（ELEMENT_NOT_FOUND 渲染延迟）与重启预算耗尽终止 run（7.5）。

全部 FakeDriver/内存 store，不打真机。
"""
from __future__ import annotations

import sqlite3

import yaml

from cli.pipeline import SessionPipeline
from executor.guard import EnvKind, Guard
from executor.policy import Idempotency
from runner.lifecycle import Lifecycle, WdaPolicy
from runner.runner import StepRunner
from session.device_session import InfraError
from tracer.storage import TraceStore

PAGE_XML = """\
<XCUIElementApplication>
  <XCUIElementTypeOther name="screen.HomeView" visible="true"/>
  <XCUIElementTypeButton name="go_profile"/>
</XCUIElementApplication>
"""


class FakeExecutor:
    """可编排故障的 Executor 替身。

    find_script: 每次 find 弹出一项——Exception 抛出、其他值返回；
    耗尽后恒返回最后一项。tap_fail_times: 前 N 次 tap 抛 RuntimeError
    （模拟 POST_DISPATCH 中途故障）。
    """

    def __init__(self, find_script=None, tap_fail_times=0,
                 page_source=PAGE_XML):
        self.find_script = list(find_script or [])
        self.find_calls = 0
        self.tap_calls = 0
        self.tap_fail_times = tap_fail_times
        self.page_source = page_source

    def find(self, strategies):
        idx = min(self.find_calls, len(self.find_script) - 1)
        self.find_calls += 1
        if not self.find_script:
            return _El()
        item = self.find_script[idx]
        if isinstance(item, Exception):
            raise item
        return item

    def tap(self, strategies):
        self.tap_calls += 1
        if self.tap_calls <= self.tap_fail_times:
            raise RuntimeError("dispatch died mid-tap")

    def input(self, strategies, value):
        pass

    def swipe(self, direction):
        pass


class _El:
    def is_displayed(self):
        return True

    def is_enabled(self):
        return True

    @property
    def text(self):
        return ""

    def get_attribute(self, name):
        return ""


class FakeDS:
    """ensure_alive 第 die_on_alive 次调用抛 InfraError（WDA 中途死亡）。

    `die_on_alive` 支持 int 或 set[int]——重启后 ensure 计数继续累加
    （attempt2/后续用例都是同一会话计数器），跨用例死亡场景用 set 编排。
    """

    def __init__(self, die_on_alive=None):
        self.ensure_calls = 0
        self.die_on = ({die_on_alive} if isinstance(die_on_alive, int)
                       else set(die_on_alive or ()))
        self.restarts = 0

    def ensure_alive(self):
        self.ensure_calls += 1
        if self.ensure_calls in self.die_on:
            raise InfraError("WDA session died")

    def restart_wda(self):
        self.restarts += 1


def _case(text: str):
    from testcase.schema import parse_testcase_dict
    return parse_testcase_dict(yaml.safe_load(text))


def _suite_dir(tmp_path, text: str):
    d = tmp_path / "suites"
    d.mkdir(exist_ok=True)
    (d / "fi_001.yaml").write_text(text, encoding="utf-8")
    return d


def _run(tmp_path, case_yaml, *, ds=None, ex=None, lifecycle=None,
         recovery=None, failure_policy="ABORT_SUITE", cases=None):
    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_fi")
    sdir = _suite_dir(tmp_path, case_yaml)
    pipe = SessionPipeline(suites_root=sdir, store=store, recovery=recovery)
    ds = ds or FakeDS()
    ex = ex or FakeExecutor()
    runner = StepRunner(ex, ds, Guard(EnvKind.SANDBOX))
    lifecycle = lifecycle or Lifecycle(store=store)
    # run_all → SuiteRunner → _suite_run_one 消费的是 pipeline 装配（同 cmd_run）
    pipe._step_runner = runner
    pipe._lifecycle = lifecycle
    cases = cases if cases is not None else pipe.discover()
    run = pipe.run_all(cases, run_id="run_fi",
                       failure_policy=failure_policy)
    return run, store, ds, ex


TWO_TAPS = """\
schema_version: "0.2"
id: fi_wda_001
name: wda rerun
suite: smoke
tags: [smoke]
steps:
  - action: launch_app
  - action: tap
    target: HomeView.go_profile
    idempotency: IDEMPOTENT
  - action: tap
    target: HomeView.go_search
    idempotency: IDEMPOTENT
"""

NON_IDEM_THEN_TAP = """\
schema_version: "0.2"
id: fi_wda_002
name: wda after non-idempotent
suite: smoke
tags: [smoke]
steps:
  - action: launch_app
  - action: tap
    target: HomeView.pay_button
    idempotency: NON_IDEMPOTENT
  - action: tap
    target: HomeView.go_search
    idempotency: IDEMPOTENT
"""


# --- #16：WDA 在未发出非幂等动作前挂 → 重启 + 重跑 attempt=2 ---


def test_fi_16_wda_rerun_attempt2(tmp_path):
    from agent.recovery import RecoveryEngine

    ds = FakeDS(die_on_alive=2)   # launch 不 ensure（app 级）；tap2 的 ensure 死
    run, store, ds, ex = _run(
        tmp_path, TWO_TAPS, ds=ds, recovery=RecoveryEngine(sleep=lambda s: None))
    assert run.passed, f"重启后重跑应全绿: {run.results[0].detail}"
    assert ds.restarts == 1
    conn = sqlite3.connect(tmp_path / "trace.db")
    rows = conn.execute(
        "SELECT attempt, status FROM testcase_runs ORDER BY id").fetchall()
    # attempt=1 的 WDA 死亡行 + attempt=2 的终态行（7.5「attempt=2」落库）
    assert rows == [(1, "INFRA_FAILURE"), (2, "PASS")], rows
    events = conn.execute(
        "SELECT action_taken FROM infra_events").fetchall()
    assert ("RESTART_WDA",) in events


# --- #17：WDA 在已发出非幂等动作后挂 → INFRA_FAILURE 不重跑 ---


def test_fi_17_wda_after_non_idempotent_no_rerun(tmp_path):
    from agent.recovery import RecoveryEngine

    ds = FakeDS(die_on_alive=2)   # tap1（pay_button 非幂等）已发出后 tap2 前死
    run, store, ds, ex = _run(
        tmp_path, NON_IDEM_THEN_TAP, ds=ds,
        recovery=RecoveryEngine(sleep=lambda s: None))
    r = run.results[0]
    assert r.status == "INFRA_FAILURE"
    assert r.failure_type == "WDA_FAILURE"
    assert "rerun_skipped" in r.detail, "非幂等已发出必须明确记录不重跑"
    assert ds.restarts == 0, "不得重跑 = 不得重启（7.5）"
    conn = sqlite3.connect(tmp_path / "trace.db")
    rows = conn.execute("SELECT attempt, status FROM testcase_runs").fetchall()
    assert len(rows) == 1 and rows[0] == (1, "INFRA_FAILURE")
    events = conn.execute("SELECT action_taken FROM infra_events").fetchall()
    assert ("SKIP_RERUN",) in events


# --- 7.5：wda.max_restart_per_run 超限 → 终止 run ---


def test_fi_wda_restart_budget_exhausted_terminates_run(tmp_path):
    from agent.recovery import RecoveryEngine

    case_a = TWO_TAPS.replace("fi_wda_001", "fi_wda_a")
    case_b = TWO_TAPS.replace("fi_wda_001", "fi_wda_b")
    case_c = TWO_TAPS.replace("fi_wda_001", "fi_wda_c")
    cases = [_case(case_a), _case(case_b), _case(case_c)]
    # max_restart_per_run=1：case_a 重启 1 次后重跑绿；case_b 再死 → 预算耗尽。
    # 计数全程累加：case_a attempt1 死于 #2（tap2 ensure）；attempt2 用 #3/#4；
    # case_b 死于 #5（tap1 ensure）→ 预算 2 > 1 → TERMINATE_RUN
    ds = FakeDS(die_on_alive={2, 5})
    lifecycle = Lifecycle(store=TraceStore(tmp_path / "trace.db"),
                          policy=WdaPolicy(max_restart_per_run=1))
    run, store, ds, ex = _run(
        tmp_path, case_a, ds=ds, lifecycle=lifecycle,
        recovery=RecoveryEngine(sleep=lambda s: None), cases=cases)
    statuses = [r.status for r in run.results]
    assert run.results[0].status == "PASS"      # case_a 重跑绿
    assert run.results[1].status == "INFRA_FAILURE"
    assert run.results[1].detail.get("terminate_run") is True
    assert len(run.results) == 2, "终止 run：case_c 不得执行"
    assert run.exit_code == 2


# --- 恢复引擎端到端：settle 重试（ELEMENT_NOT_FOUND 渲染延迟） ---


def test_fi_settle_retry_recovers_recovered_exit5(tmp_path):
    from agent.recovery import RecoveryEngine
    from executor.executor import ElementNotFound

    case = """\
schema_version: "0.2"
id: fi_settle_001
name: settle
suite: smoke
tags: [smoke]
steps:
  - action: launch_app
  - action: tap
    target: HomeView.go_profile
    idempotency: IDEMPOTENT
"""
    # find 第 1 次（初执行）失败，settle 的 re-find（第 2 次）命中
    ex = FakeExecutor(find_script=[ElementNotFound("render delay"), _El()])
    run, store, ds, ex = _run(
        tmp_path, case, ex=ex,
        recovery=RecoveryEngine(sleep=lambda s: None))
    r = run.results[0]
    assert r.status == "RECOVERED", r.detail
    assert r.detail["recovery_kinds"] == ["settle_retry"]
    assert run.exit_code == 5, "8.4：无 FAIL 但存在 RECOVERED → exit 5"
    assert ex.tap_calls == 1
    conn = sqlite3.connect(tmp_path / "trace.db")
    step_status = conn.execute(
        "SELECT status FROM steps ORDER BY id DESC LIMIT 1").fetchone()
    assert step_status == ("RECOVERED",), "步骤终态 RECOVERED，不是 SUCCESS"


# --- #13：wait 超时不触发恢复 ---


def test_fi_13_wait_timeout_no_recovery(tmp_path):
    from agent.recovery import RecoveryEngine
    from executor.executor import ElementNotFound

    case = """\
schema_version: "0.2"
id: fi_wait_001
name: wait timeout
suite: smoke
tags: [smoke]
steps:
  - action: launch_app
  - wait_for:
      target: screen:GhostView
      condition: active
      timeout: 0.1
      polling_interval: 0.05
"""
    ex = FakeExecutor(find_script=[ElementNotFound("never")],
                      page_source="<App/>")
    run, *_ = _run(tmp_path, case, ex=ex,
                   recovery=RecoveryEngine(sleep=lambda s: None))
    r = run.results[0]
    assert r.status == "FAIL" and r.failure_type == "WAIT_TIMEOUT"
    assert "recovery_attempted" not in r.detail, \
        "决策表未准入就不该进引擎（9.2：Wait Timeout 默认不恢复）"


# --- #14：断言期望值不符无恢复 ---


def test_fi_14_assertion_mismatch_no_recovery(tmp_path):
    from agent.recovery import RecoveryEngine

    case = """\
schema_version: "0.2"
id: fi_assert_001
name: assertion
suite: smoke
tags: [smoke]
steps:
  - action: launch_app
  - assertion:
      target: HomeView.go_profile
      condition: text_equals
      expected: "unexpected"
"""
    run, store, ds, ex = _run(
        tmp_path, case,
        recovery=RecoveryEngine(sleep=lambda s: None))
    r = run.results[0]
    assert r.status == "FAIL" and r.failure_type == "ASSERTION_VALUE_MISMATCH"
    assert "recovery_attempted" not in r.detail, "H6：断言值失败不进恢复"


# --- #18：POST_DISPATCH 超时 + 非幂等 + postcondition 成立 → RECOVERED ---


def test_fi_18_post_dispatch_postcondition_recovered(tmp_path):
    from agent.recovery import RecoveryEngine

    case = """\
schema_version: "0.2"
id: fi_post_001
name: postcondition recovery
suite: smoke
tags: [smoke]
steps:
  - action: launch_app
  - action: tap
    target: HomeView.pay_button
    idempotency: NON_IDEMPOTENT
    postcondition:
      target: screen:HomeView
      condition: active
      timeout: 1
"""
    # tap 抛（动作结果未知）；postcondition 检查走 page_source marker → 成立
    ex = FakeExecutor(tap_fail_times=1)
    run, store, ds, ex = _run(
        tmp_path, case, ex=ex,
        recovery=RecoveryEngine(sleep=lambda s: None))
    r = run.results[0]
    assert r.status == "RECOVERED", r.detail
    assert r.detail["recovery_kinds"] == ["postcondition"]
    assert ex.tap_calls == 1, "RECOVERED(kind=postcondition) 不得再次点击（H7）"


# --- #19：POST_DISPATCH 失败但 postcondition 不成立 → FAIL ---


def test_fi_19_post_dispatch_postcondition_unsatisfied_fails(tmp_path):
    from agent.recovery import RecoveryEngine

    case = """\
schema_version: "0.2"
id: fi_post_002
name: postcondition fail
suite: smoke
tags: [smoke]
steps:
  - action: launch_app
  - action: tap
    target: HomeView.pay_button
    idempotency: NON_IDEMPOTENT
    postcondition:
      target: screen:GhostView
      condition: active
      timeout: 0.1
"""
    from executor.executor import ElementNotFound
    # 主步骤 find#1 放行（tap 中途炸）；postcondition 检查（screen:GhostView
    # active）经 WaitEngine → find 恒失败 → WaitTimeout → 检查为 False
    ex = FakeExecutor(find_script=[_El(), ElementNotFound("ghost")],
                      tap_fail_times=1,
                      page_source="<App><Node name='screen.HomeView'/></App>")
    run, store, ds, ex = _run(
        tmp_path, case, ex=ex,
        recovery=RecoveryEngine(sleep=lambda s: None))
    r = run.results[0]
    assert r.status == "FAIL"
    assert r.failure_type == "ACTION_OUTCOME_UNKNOWN"
