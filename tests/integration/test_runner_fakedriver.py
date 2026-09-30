"""FakeDriver 集成测（Task 2.5 Step 3）：find 失败 / act 超时 / WDA 死亡 /
多匹配，四个脚本走完整 7.1 管线。

为什么需要集成层：policy/retry 的纯函数测试证明的是**规则**正确，管线测试
证明的是**阶段判定**正确——即「失败到底该记 PRE_DISPATCH 还是 POST_DISPATCH、
该允许重试还是禁止」。这两件事都会改变最终用例结论（H7），而纯函数测试
看不到调用顺序。

不依赖真实设备：FakeDriver / FakeExecutor 实现与 Appium 同形的失败语义。
"""

from __future__ import annotations

import pytest

from executor.guard import Guard, GuardContext, EnvKind
from executor.policy import FailurePhase, Idempotency
from runner.runner import RunStepContext, StepRunner
from session.device_session import InfraError
from testcase.schema import Risk


# --- Fake 组件 ---


class FakeElement:
    def __init__(self, tag="e"):
        self.tag = tag

    def __repr__(self):
        return f"<FakeElement {self.tag}>"


class FakeAppiumError(Exception):
    """模拟 Appium 命令超时（区别于我们自己的 InfraError）。"""


class FakeExecutor:
    """只实现管线用到的两个方法；行为按脚本配置。"""

    def __init__(self, found=None, find_error=None, perform_error=None):
        self.found = found if found is not None else [FakeElement()]
        self.find_error = find_error
        self.perform_error = perform_error
        self.calls: list[tuple] = []

    def find(self, strategies):
        self.calls.append(("find", tuple(strategies)))
        if self.find_error:
            raise self.find_error
        return list(self.found)

    def perform(self, action, element, value=None):
        self.calls.append(("perform", action, value))
        if self.perform_error:
            raise self.perform_error


class _TrackedExecutor(FakeExecutor):
    """perform 时把「已派发非幂等动作」记进用例级 tracker（7.5 的状态跟踪
    在 lifecycle.TestcaseLifecycle 内，这里用注入验证接线时序）。"""

    def __init__(self, lifecycle, **kw):
        super().__init__(**kw)
        self.lifecycle = lifecycle

    def perform(self, action, element, value=None):
        super().perform(action, element, value)
        self.lifecycle.note_dispatch(0, True)


def _tracked_executor(lifecycle, **kw) -> _TrackedExecutor:
    return _TrackedExecutor(lifecycle, **kw)


class FakeDeviceSession:
    def __init__(self, alive=True, ensure_error=None):
        self._alive = alive
        self.ensure_error = ensure_error
        self.ensure_calls = 0

    def ensure_alive(self):
        self.ensure_calls += 1
        if self.ensure_error:
            raise self.ensure_error
        if not self._alive:
            raise InfraError("WDA dead")


def _runner(ex, ds=None, guard=None, rec=None, **kw) -> StepRunner:
    return StepRunner(ex, ds or FakeDeviceSession(),
                      guard or Guard(EnvKind.SANDBOX), recorder=rec, **kw)


def _ctx(**kw) -> RunStepContext:
    base = dict(
        element_id="login_button",
        screen_id="LoginView",
        strategies=(("accessibility id", "login_button"),),
        action="tap",
        risk=Risk.LOW,
        idempotency=Idempotency.IDEMPOTENT,
        step_index=0,
    )
    base.update(kw)
    return RunStepContext(**base)


# --- 脚本 1：find 失败（0 个匹配） ---

def test_find_zero_matches_is_pre_dispatch():
    """0 个 → ELEMENT_NOT_FOUND，PRE_DISPATCH（动作从未发出）。"""
    ex = FakeExecutor(found=[])
    out = _runner(ex).run_step(_ctx())
    assert not out.ok
    assert out.failure_type == "ELEMENT_NOT_FOUND"
    assert out.phase is FailurePhase.PRE_DISPATCH
    # 关键：perform 根本没被调用
    assert [c[0] for c in ex.calls] == ["find"]
    assert out.non_idempotent_dispatched is False


def test_find_zero_matches_allows_retry_even_for_non_idempotent():
    """PRE_DISPATCH 任意幂等性都允许重试（7.4 表第 1 行）。"""
    out = _runner(FakeExecutor(found=[])).run_step(
        _ctx(element_id="pay_button", idempotency=Idempotency.NON_IDEMPOTENT))
    assert out.retry_decision.allowed is True
    assert out.non_idempotent_dispatched is False


def test_find_raises_value_error_classified_as_element_not_found():
    ex = FakeExecutor(find_error=ValueError("no such element"))
    out = _runner(ex).run_step(_ctx())
    assert out.failure_type == "ELEMENT_NOT_FOUND"
    assert out.phase is FailurePhase.PRE_DISPATCH


def test_unknown_find_error_is_not_silently_element_not_found():
    """fail-loud：认不出的异常不得伪装成 ELEMENT_NOT_FOUND（那会把编程错误
    变成测试失败）。"""
    ex = FakeExecutor(find_error=RuntimeError("boom"))
    out = _runner(ex).run_step(_ctx())
    assert out.failure_type.startswith("FIND_ERROR:")
    assert out.failure_type != "ELEMENT_NOT_FOUND"


# --- 脚本 2：act 超时（动作已发出，结果未知） ---

def test_act_timeout_is_post_dispatch():
    """perform 抛错 → POST_DISPATCH + ACTION_OUTCOME_UNKNOWN。"""
    ex = FakeExecutor(perform_error=FakeAppiumError("timeout after 60s"))
    out = _runner(ex).run_step(_ctx())
    assert not out.ok
    assert out.phase is FailurePhase.POST_DISPATCH
    assert out.failure_type == "ACTION_OUTCOME_UNKNOWN"
    assert out.dispatch_reached is True


def test_act_timeout_idempotent_allows_bounded_retry():
    out = _runner(FakeExecutor(perform_error=FakeAppiumError("t"))).run_step(_ctx())
    assert out.retry_decision.allowed is True
    assert out.retry_decision.bounded is True


def test_act_timeout_non_idempotent_forbids_retry_h7():
    """H7：非幂等动作 POST_DISPATCH 失败**绝不重试**——重发可能二次下单。"""
    ex = FakeExecutor(perform_error=FakeAppiumError("t"))
    out = _runner(ex).run_step(
        _ctx(element_id="submit_button",
             idempotency=Idempotency.NON_IDEMPOTENT))
    assert out.retry_decision.allowed is False
    assert out.retry_decision.allowed is False
    assert out.non_idempotent_dispatched is True, "派发标记必须置位"


def test_act_timeout_unknown_idempotency_also_forbids():
    out = _runner(FakeExecutor(perform_error=FakeAppiumError("t"))).run_step(
        _ctx(idempotency=Idempotency.UNKNOWN))
    assert out.retry_decision.allowed is False
    assert out.non_idempotent_dispatched is True


def test_act_timeout_non_idempotent_with_postcondition_checks_it():
    out = _runner(FakeExecutor(perform_error=FakeAppiumError("t"))).run_step(
        _ctx(element_id="submit_button",
             idempotency=Idempotency.NON_IDEMPOTENT, has_postcondition=True))
    assert out.retry_decision.check_postcondition is True


# --- 脚本 3：WDA 死亡 ---

def test_wda_dead_raises_infra_error_not_test_failure():
    """ensure_alive 失败是**基础设施故障**，不是测试失败：抛 InfraError 让
    用例层按 7.5 决定重跑/终止，不能记成 FAIL。"""
    ds = FakeDeviceSession(ensure_error=InfraError("WDA_DEAD"))
    ex = FakeExecutor()
    with pytest.raises(InfraError):
        _runner(ex, ds=ds).run_step(_ctx())
    # 设备都死了，不该去 find
    assert ex.calls == []


def test_wda_dead_does_not_mark_dispatch():
    """WDA 在 find 前就死 → 动作没发出，不能置 non_idempotent_dispatched。"""
    from runner.lifecycle import Lifecycle
    ds = FakeDeviceSession(ensure_error=InfraError("WDA_DEAD"))
    lifecycle = Lifecycle()
    ex = _tracked_executor(lifecycle)
    with pytest.raises(InfraError):
        _runner(ex, ds=ds).run_step(
            _ctx(element_id="pay_button",
                 idempotency=Idempotency.NON_IDEMPOTENT))
    assert lifecycle.non_idempotent_dispatched is False
    assert ex.calls == [], "WDA 死时连 find 都不该调"


def test_non_idempotent_dispatch_is_tracked_after_perform():
    """对照：正常 perform 后 lifecycle 状态应置位（7.5）。"""
    from runner.lifecycle import Lifecycle
    lifecycle = Lifecycle()
    ex = _tracked_executor(lifecycle)
    _runner(ex).run_step(
        _ctx(element_id="delete_item", idempotency=Idempotency.NON_IDEMPOTENT))
    assert lifecycle.non_idempotent_dispatched is True


# --- 7.5 用例级 WDA 故障处理（lifecycle 契约，走真实 Lifecycle） ---


def test_wda_failure_reruns_when_nothing_dispatched():
    """7.5：非幂等动作没派发过 → 重启 WDA 后**可重跑**该 testcase。"""
    from runner.lifecycle import Lifecycle
    lifecycle = Lifecycle()
    restarted = []

    class _DS:
        def restart_wda(self):
            restarted.append(1)

    # 不抛异常 = 允许重跑
    lifecycle.handle_wda_failure("tc1", attempt=1, device_session=_DS())
    assert restarted == [1]


def test_wda_failure_after_non_idempotent_dispatch_refuses_rerun():
    """7.5 + H7：非幂等动作已派发 → **不重跑**，直接 INFRA_FAILURE。

    这是本任务最关键的语义：重跑可能二次下单/二次付款。"""
    from runner.lifecycle import Lifecycle, NonIdempotentDispatched
    lifecycle = Lifecycle()
    lifecycle.note_dispatch(3, non_idempotent=True)
    restarted = []

    class _DS:
        def restart_wda(self):
            restarted.append(1)

    with pytest.raises(NonIdempotentDispatched):
        lifecycle.handle_wda_failure("tc1", attempt=1, device_session=_DS())
    assert restarted == [], "已派发非幂等动作时不得重启后重跑"
    assert lifecycle.dispatched_step_index == 3


def test_wda_rerun_limited_per_testcase():
    """7.5：同一 testcase 最多重跑 1 次（attempt=2）→ 再失败给 InfraError。"""
    from runner.lifecycle import Lifecycle
    lifecycle = Lifecycle()
    with pytest.raises(InfraError, match="重跑"):
        lifecycle.handle_wda_failure("tc1", attempt=2)


def test_wda_restart_budget_is_run_level_not_per_testcase():
    """7.5：`max_restart_per_run` 是整个 run 的上限。跨用例累计，不得每条
    用例重置——否则「超过上限终止 run」形同虚设。"""
    from runner.lifecycle import Lifecycle, WdaPolicy
    lifecycle = Lifecycle(policy=WdaPolicy(max_restart_per_run=2,
                                           max_testcase_rerun=1))
    # 第一条用例用掉第 1 次预算
    lifecycle.handle_wda_failure("tc1", attempt=1)
    lifecycle.reset_testcase_state()   # 新用例：清用例态
    # 第二条用例用掉第 2 次
    lifecycle.handle_wda_failure("tc2", attempt=1)
    lifecycle.reset_testcase_state()
    # 第三条：预算耗尽 → 终止 run
    with pytest.raises(InfraError, match="max_restart_per_run"):
        lifecycle.handle_wda_failure("tc3", attempt=1)


def test_infra_event_recorded_with_dispatch_context(tmp_path):
    """7.5：WDA 故障必须写 infra_events，带 non_idempotent_dispatched 上下文
    ——事后判断「为什么没重跑」的唯一依据（R14-2 接线）。"""
    from runner.lifecycle import Lifecycle
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run_w")
    tc_id = store.start_testcase("run_w", "tc1")
    lifecycle = Lifecycle(store=store)
    lifecycle.begin_testcase("run_w", "tc1")
    lifecycle.note_dispatch(2, non_idempotent=True)
    with pytest.raises(Exception):
        lifecycle.handle_wda_failure("tc1", attempt=1)
    row = store.conn.execute(
        "SELECT event_type, step_index, non_idempotent_dispatched,"
        " action_taken FROM infra_events ORDER BY id DESC LIMIT 1").fetchone()
    assert row[0] == "WDA_DEAD"
    assert row[1] == 2 and row[2] == 1
    assert row[3] == "SKIP_RERUN"


def test_infra_error_from_find_propagates_not_classified():
    """find 里的设备层错误（InfraError）要上抛，不能当 ELEMENT_NOT_FOUND。"""
    ex = FakeExecutor(find_error=InfraError("session dead"))
    with pytest.raises(InfraError):
        _runner(ex).run_step(_ctx())


def test_infra_error_from_perform_propagates():
    """perform 里的 InfraError 上抛——动作可能已发出，用例层要重跑整个用例
    而不是记一步失败。"""
    ex = FakeExecutor(perform_error=InfraError("WDA died mid-tap"))
    with pytest.raises(InfraError):
        _runner(ex).run_step(_ctx())


# --- 脚本 4：多匹配（H3） ---

def test_multiple_matches_is_ambiguous_and_never_takes_first():
    """H3：≥2 个 → AMBIGUOUS_ELEMENT，禁止取第一个。"""
    ex = FakeExecutor(found=[FakeElement("a"), FakeElement("b")])
    out = _runner(ex).run_step(_ctx())
    assert not out.ok
    assert out.failure_type == "AMBIGUOUS_ELEMENT"
    assert out.phase is FailurePhase.PRE_DISPATCH
    assert [c[0] for c in ex.calls] == ["find"], "多匹配时不得 perform"


def test_multiple_matches_message_says_count():
    ex = FakeExecutor(found=[FakeElement("a"), FakeElement("b"),
                             FakeElement("c")])
    out = _runner(ex).run_step(_ctx())
    assert "3 elements" in out.error


# --- 管线顺序 ---

def test_guard_runs_before_device_touch():
    """被 Guard 拦的动作连设备都不该碰（10.1：不可被 testcase/LLM 绕过）。"""
    from executor.guard import GuardViolation
    ex = FakeExecutor()
    ds = FakeDeviceSession()
    guard = Guard(EnvKind.PRODUCTION, allow_production=False)
    out = _runner(ex, ds=ds, guard=guard).run_step(
        _ctx(element_id="pay_button", risk=Risk.HIGH))
    assert out.failure_type == "SECURITY_BLOCKED"
    assert ds.ensure_calls == 0, "Guard 拦下后不该 ensure_alive"
    assert ex.calls == [], "Guard 拦下后不该 find"


def test_guard_blocked_is_pre_dispatch_semantics():
    """SECURITY_BLOCKED 的 phase 是 None（不是失败阶段），但**重试决策不允许**
    ——R15 P3-5：guard 判定是确定性的，重试必然再拦，「允许重试」语义不通。"""
    from executor.guard import GuardViolation
    guard = Guard(EnvKind.PRODUCTION, allow_production=False)
    out = _runner(FakeExecutor(), guard=guard).run_step(
        _ctx(risk=Risk.CRITICAL))
    assert out.failure_type == "SECURITY_BLOCKED"
    assert out.phase is None
    assert out.retry_decision.allowed is False, \
        "被安全闸拦的步骤不得标成「可重试」"
    assert "SECURITY_BLOCKED" in out.retry_decision.reason


def test_pipeline_order_is_guard_ensure_find_perform():
    """顺序固定（7.1）：guard → ensure_alive → find → perform。"""
    ex = FakeExecutor()
    ds = FakeDeviceSession()
    _runner(ex, ds=ds).run_step(_ctx())
    assert [c[0] for c in ex.calls] == ["find", "perform"]
    assert ds.ensure_calls == 1


def test_find_and_perform_are_two_separate_calls():
    """7.1 硬要求：find 与 act 必须是两次独立调用——阶段判定完全建立在
    这个分界上；合并成 find_and_tap 就无法区分「没找到」与「点了但超时」。"""
    ex = FakeExecutor()
    _runner(ex).run_step(_ctx())
    assert len(ex.calls) == 2
    assert ex.calls[0][0] == "find"
    assert ex.calls[1][0] == "perform"


def test_success_path_records_locator_strategy():
    out = _runner(FakeExecutor()).run_step(_ctx())
    assert out.ok
    assert out.locator_strategy == "accessibility id"
    assert out.dispatch_reached is True


def test_success_marks_non_idempotent_dispatched():
    """成功发出也算「已派发」——7.5 的判据是「到达过 POST_DISPATCH」，
    不是「失败了」。"""
    out = _runner(FakeExecutor()).run_step(
        _ctx(element_id="delete_item", idempotency=Idempotency.NON_IDEMPOTENT))
    assert out.ok
    assert out.non_idempotent_dispatched is True


def test_success_of_idempotent_does_not_mark_dispatch():
    out = _runner(FakeExecutor()).run_step(_ctx())
    assert out.non_idempotent_dispatched is False


def test_latency_recorded():
    out = _runner(FakeExecutor()).run_step(_ctx())
    assert out.latency_ms is not None and out.latency_ms >= 0


# --- trace 接线（R14-2 记账项之一） ---

class _RecordingRecorder:
    def __init__(self):
        self.calls: list[dict] = []

    def record_step(self, **kw):
        self.calls.append(kw)


def test_outcome_recorded_with_new_schema_fields():
    """7.1 产出的 effective_risk / idempotency / failure_phase 必须落 trace
    ——Report（2.6）按这些字段渲染失败原因。"""
    rec = _RecordingRecorder()
    ex = FakeExecutor(perform_error=FakeAppiumError("t"))
    _runner(ex, rec=rec).run_step(
        _ctx(idempotency=Idempotency.NON_IDEMPOTENT))
    kw = rec.calls[0]
    assert kw["failure_phase"] == "POST_DISPATCH"
    assert kw["failure_type"] == "ACTION_OUTCOME_UNKNOWN"
    assert kw["effective_idempotency"] == "NON_IDEMPOTENT"
    assert kw["effective_risk"] == "LOW"


class _OldP0Recorder:
    """旧 Recorder 不接新字段（record_step 签名窄）→ 应回退而非崩。"""

    def __init__(self):
        self.calls: list[dict] = []

    def record_step(self, step_index, action_type, status="SUCCESS",
                    error=None, latency_ms=0):
        self.calls.append({"step_index": step_index,
                           "action_type": action_type, "status": status})


def test_old_p0_recorder_still_works():
    """P0 脚本兼容到 M2 Gate 后才退役——新参数不得打断旧 Recorder。"""
    rec = _OldP0Recorder()
    out = _runner(FakeExecutor(), rec=rec).run_step(_ctx())
    assert out.ok
    assert rec.calls[0]["status"] == "SUCCESS"


def test_record_failure_does_not_break_step_result():
    """trace 写失败不能反过来把步骤变成崩溃。

    这条测试写出来就抓到 `_record` 的真 bug：只 catch 了 TypeError，
    磁盘满之类的 RuntimeError 会一路冒泡，把一个 SUCCESS 的步骤变成崩溃。
    修法：捕获后 `warnings.warn`——**不静默**（warning 会进日志，
    Report 侧 2.6 可对 warning 计数暴露 trace 丢失），但不让它打断执行。
    """
    class _BrokenRecorder:
        def record_step(self, **kw):
            raise RuntimeError("disk full")

    with pytest.warns(RuntimeWarning, match="trace write failed"):
        out = _runner(FakeExecutor(), rec=_BrokenRecorder()).run_step(_ctx())
    assert out.ok, "trace 写盘异常不应影响步骤结论"


def test_broken_p0_recorder_also_only_warns():
    """窄签名（P0 形态）recorder 自身抛错时同样不得打断执行。

    R15-1 修掉「回退路径」后，两条路径合并成一条 warn，措辞统一为
    `trace write failed`。这里保留本测试是为了钉住「窄签名 recorder
    也走同一条保护」——不再依赖回退路径存在。"""
    class _BrokenOldRecorder:
        def record_step(self, run_id, step_index, action_type, status="SUCCESS",
                        error=None, latency_ms=0):
            raise RuntimeError("db locked")

    with pytest.warns(RuntimeWarning, match="trace write failed"):
        out = _runner(FakeExecutor(), rec=_BrokenOldRecorder(),
                      run_id="r1").run_step(_ctx())
    assert out.ok