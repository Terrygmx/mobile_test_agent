"""Task 2.5 / P1-07：Lifecycle（7.5 WDA + TraceStore 接线）。

**本文件核销 R14-2**（TraceStore 零消费）与 **R14-3**（R12-3/5 端到端）。
断言的结构化结果必须真的走 detail_json 落到 steps 表——这正是 R14-3 指出的
「接口就绪 ≠ 链路打通」。

7.5 的核心不变量：非幂等动作一旦发出，WDA 故障后**不得重跑**（后果不可
回滚：重复下单/重复支付）。
"""

from __future__ import annotations

import json

import pytest

from executor.policy import FailurePhase, Idempotency
from runner.lifecycle import (
    Lifecycle,
    NonIdempotentDispatched,
    WdaPolicy,
)
from runner.runner import StepOutcome
from session.device_session import InfraError
from testcase.schema import Risk
from tracer.storage import TraceStore


@pytest.fixture
def store(tmp_path):
    s = TraceStore(tmp_path / "trace.db")
    s.start_run("run_lc", suite="smoke", device_type="simulator")
    return s


def _outcome(**kw):
    kw.setdefault("ok", True)
    kw.setdefault("step_index", 0)
    kw.setdefault("element_id", "login_button")
    kw.setdefault("action", "tap")
    kw.setdefault("effective_idempotency", Idempotency.IDEMPOTENT)
    kw.setdefault("effective_risk", Risk.LOW)
    return StepOutcome(**kw)


# ------------------------------------------------------------ 7.5 状态跟踪

def test_dispatch_state_starts_false():
    lc = Lifecycle(store=None)
    assert lc.non_idempotent_dispatched is False


def test_idempotent_dispatch_does_not_set_flag():
    lc = Lifecycle(store=None)
    lc.note_dispatch(0, non_idempotent=False)
    assert lc.non_idempotent_dispatched is False


def test_non_idempotent_dispatch_sets_flag_7_5():
    lc = Lifecycle(store=None)
    lc.note_dispatch(2, non_idempotent=True)
    assert lc.non_idempotent_dispatched is True
    assert lc.dispatched_step_index == 2


def test_flag_never_resets_within_testcase():
    """7.5：置位后不可回落——后果不可回滚。"""
    lc = Lifecycle(store=None)
    lc.note_dispatch(0, non_idempotent=True)
    lc.note_dispatch(1, non_idempotent=False)
    assert lc.non_idempotent_dispatched is True, "标记一旦置位不得清除"


def test_begin_testcase_resets_state():
    lc = Lifecycle(store=None)
    lc.note_dispatch(0, non_idempotent=True)
    lc.begin_testcase("run", "tc2")
    assert lc.non_idempotent_dispatched is False, "新用例是新战场"


# ------------------------------------------------------------ 7.5 WDA 处理

def test_wda_failure_after_idempotent_allows_rerun():
    """非幂等未发出 → 可重启 WDA 并重跑（attempt=2）。"""
    lc = Lifecycle(store=None)
    lc.note_dispatch(0, non_idempotent=False)
    lc.handle_wda_failure("tc1", attempt=1)   # 不抛 = 可重跑


def test_wda_failure_after_non_idempotent_forbids_rerun_7_5():
    """7.5 硬约束：已发出非幂等动作 → 不重跑。"""
    lc = Lifecycle(store=None)
    lc.note_dispatch(3, non_idempotent=True)
    with pytest.raises(NonIdempotentDispatched) as ei:
        lc.handle_wda_failure("tc1", attempt=1)
    assert ei.value.step_index == 3


def test_rerun_budget_exhausted_raises_infra():
    """同一 testcase 最多重跑 1 次；再故障 → InfraError。"""
    lc = Lifecycle(store=None)
    with pytest.raises(InfraError):
        lc.handle_wda_failure("tc1", attempt=2)


def test_run_restart_budget_exhausted_terminates_run_7_5():
    """wda.max_restart_per_run=2：前两次重启放行，第 3 次触发终止 run。"""
    lc = Lifecycle(store=None, policy=WdaPolicy(max_restart_per_run=2))
    lc.handle_wda_failure("tc1", attempt=1)   # restart 1/2 → 放行
    lc.handle_wda_failure("tc2", attempt=1)   # restart 2/2 → 放行
    with pytest.raises(InfraError, match="max_restart_per_run"):
        lc.handle_wda_failure("tc3", attempt=1)   # 第 3 次 → 终止 run


def test_restart_budget_is_run_level_not_per_testcase_7_5():
    """`max_restart_per_run` 是**整个 run** 的上限。每条用例开头不能重置它
    ——否则「重启预算耗尽终止 run」形同虚设（无限续杯）。"""
    lc = Lifecycle(store=None, policy=WdaPolicy(max_restart_per_run=2))
    lc.handle_wda_failure("tc1", attempt=1)
    lc.begin_testcase("run", "tc2")            # 新用例 → 不该重置 run 预算
    lc.handle_wda_failure("tc2", attempt=1)
    with pytest.raises(InfraError, match="max_restart_per_run"):
        lc.handle_wda_failure("tc3", attempt=1)


# ------------------------------------------------------------ R14-2 TraceStore

def test_lifecycle_writes_steps_to_trace_store(store):
    lc = Lifecycle(store=store)
    lc.begin_testcase("run_lc", "login_001")
    lc.record_step(_outcome(step_index=0, action="tap",
                            element_id="login_button"), step_index=0)
    rows = store.conn.execute(
        "SELECT step_index, step_type, target_id, status, locator_strategy"
        " FROM steps").fetchall()
    assert len(rows) == 1
    assert tuple(rows[0])[:4] == (0, "tap", "login_button", "SUCCESS")
    assert json.loads(rows[0][4])["strategy"]


def test_trace_store_records_effective_idempotency_and_risk(store):
    """7.4 推导结果落库——事后能查「当时判成什么」，不靠回忆。"""
    lc = Lifecycle(store=store)
    lc.begin_testcase("run_lc", "tc")
    lc.record_step(_outcome(effective_idempotency=Idempotency.NON_IDEMPOTENT,
                            effective_risk=Risk.HIGH), step_index=0)
    row = store.conn.execute(
        "SELECT effective_idempotency, effective_risk FROM steps").fetchone()
    assert tuple(row) == ("NON_IDEMPOTENT", "HIGH")


def test_failure_phase_and_type_recorded(store):
    lc = Lifecycle(store=store)
    lc.begin_testcase("run_lc", "tc")
    lc.record_step(_outcome(ok=False,
                            phase=FailurePhase.POST_DISPATCH,
                            failure_type="ACTION_OUTCOME_UNKNOWN"), step_index=2)
    row = store.conn.execute(
        "SELECT step_index, status, failure_phase, failure_type"
        " FROM steps").fetchone()
    assert tuple(row) == (2, "FAILED", "POST_DISPATCH",
                          "ACTION_OUTCOME_UNKNOWN")


def test_end_testcase_records_non_idempotent_dispatched(store):
    lc = Lifecycle(store=store)
    tc_id = lc.begin_testcase("run_lc", "pay_case")
    lc.record_step(_outcome(effective_idempotency=Idempotency.NON_IDEMPOTENT),
                   step_index=0)
    lc.end_testcase(status="PASS")
    row = store.conn.execute(
        "SELECT status, non_idempotent_dispatched FROM testcase_runs WHERE id=?",
        (tc_id,)).fetchone()
    assert tuple(row) == ("PASS", 1), "PASS 也可能已发出非幂等动作（要留痕）"


def test_infra_event_written_on_wda_failure(store):
    """7.5：WDA 故障要写 infra_events（不重跑时尤其重要——事后要能查）。"""
    lc = Lifecycle(store=store)
    lc.begin_testcase("run_lc", "pay_case")
    lc.note_dispatch(1, non_idempotent=True)
    with pytest.raises(NonIdempotentDispatched):
        lc.handle_wda_failure("pay_case", attempt=1)
    row = store.conn.execute(
        "SELECT event_type, action_taken, non_idempotent_dispatched,"
        " step_index FROM infra_events").fetchone()
    assert tuple(row) == ("WDA_DEAD", "SKIP_RERUN", 1, 1)


def test_lifecycle_without_store_is_noop():
    """没注入 store 时不应崩（纯设备链路仍可跑，只是不落 trace）。"""
    lc = Lifecycle(store=None)
    lc.begin_testcase("run", "tc")
    lc.record_step(_outcome(), step_index=0)
    lc.end_testcase(status="PASS")


# ------------------------------------------------------------ R14-3 端到端

def test_assertion_structured_result_lands_in_detail_json(store):
    """R14-3 核销点：断言结构化结果真的经 lifecycle → TraceStore →
    steps.detail_json 落库（旧链路只留格式化异常字符串）。"""
    lc = Lifecycle(store=store)
    lc.begin_testcase("run_lc", "search_001")
    lc.record_step(_outcome(
        ok=False, action="assertion", element_id="search_field",
        phase=FailurePhase.POST_DISPATCH,
        failure_type="ASSERTION_VALUE_MISMATCH",
        detail={"kind": "assertion_target",
                "password": "hunter2",
                "assertion": {"condition": "element_count",
                              "expected": 3, "actual": 0,
                              "target": "search_field", "timeout": 10.0}}),
        step_index=4)
    raw = store.conn.execute("SELECT detail_json FROM steps").fetchone()[0]
    parsed = json.loads(raw)
    assert parsed["kind"] == "assertion_target"
    assert parsed["assertion"]["expected"] == 3
    assert parsed["assertion"]["actual"] == 0
    assert parsed["password"] == "***REDACTED***", "H8：脱敏仍前置"


def test_runner_pipeline_detail_flows_into_trace(store):
    """端到端：StepRunner 产出的 detail 经 Lifecycle 进 trace（不是各写各的）。"""
    from runner.runner import StepRunner, RunStepContext
    from executor.guard import Guard, EnvKind

    class _El:
        def tap(self):
            pass

    class _Ex:
        def find(self, locator):
            return [_El()]

        def perform(self, action, element, value=None):
            raise RuntimeError("act failed")

    class _DS:
        def ensure_alive(self):
            return None

    sr = StepRunner(executor=_Ex(), device_session=_DS(),
                    guard=Guard(env_kind=EnvKind.SANDBOX), recorder=None)
    out = sr.run_step(RunStepContext(
        element_id="pay_button", screen_id="PaymentView",
        strategies=(("accessibility id", "pay_button"),), action="tap",
        step_index=1))
    assert out.ok is False and out.phase == FailurePhase.POST_DISPATCH

    lc = Lifecycle(store=store)
    lc.begin_testcase("run_lc", "pay_case")
    lc.record_step(out, step_index=1)
    lc.end_testcase(status="FAIL",
                    failure_type=out.failure_type)
    row = store.conn.execute(
        "SELECT step_index, failure_phase, failure_type FROM steps").fetchone()
    assert tuple(row) == (1, "POST_DISPATCH", "ACTION_OUTCOME_UNKNOWN")
    tc = store.conn.execute(
        "SELECT status, failure_type, non_idempotent_dispatched"
        " FROM testcase_runs").fetchone()
    assert tuple(tc) == ("FAIL", "ACTION_OUTCOME_UNKNOWN", 1)
