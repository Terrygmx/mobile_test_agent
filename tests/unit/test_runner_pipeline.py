"""Task 2.5 / P1-07：run_step 管线集成测（设计 7.1 + 7.5）。

用 FakeDriver 覆盖计划要求的四个脚本：
  S1 find 失败（0 个匹配）→ PRE_DISPATCH
  S2 act 超时 → POST_DISPATCH（结果未知）
  S3 WDA 死亡 → ensure_alive 失败 → InfraError（不是测试失败）
  S4 多匹配 → AMBIGUOUS_ELEMENT，**fail-closed 不取第一个**（H3）

管线（7.1）：resolve → guard → ensure_alive → find[PRE_DISPATCH] →
perform[POST_DISPATCH]。**find 与 act 必须是两次独立调用**——这是阶段判定
的前提，也是本测试的核心断言。

7.5 WDA 故障：每个 testcase 维护 non_idempotent_dispatched；WDA 中途故障时
  - false → 重启 WDA → 重跑该 testcase（attempt=2）
  - true  → **不重跑**，直接 INFRA_FAILURE + 写 infra_events
"""

from __future__ import annotations

import pytest

from executor.policy import FailurePhase, Idempotency
from executor.guard import EnvKind, Guard
from runner.runner import StepRunner, RunStepContext
from session.device_session import InfraError
from testcase.schema import Idempotency as Idem, Risk


# ---------------------------------------------------------------- FakeDriver

class FakeElement:
    def __init__(self, eid):
        self.eid = eid

    def tap(self):
        ACT_LOG.append(("tap", self.eid))

    def send_keys(self, value):
        ACT_LOG.append(("input", self.eid, value))


ACT_LOG: list = []


class FakeDriver:
    """最小 Appium 替身。`find` 返回元素列表，由脚本决定 0/1/多/抛错。"""

    def __init__(self, find_result=None, find_error=None, act_error=None):
        self.find_result = find_result if find_result is not None else [FakeElement("x")]
        self.find_error = find_error
        self.act_error = act_error
        self.find_calls = 0
        self.implicit_wait = 5
        self._alive = True

    def find_elements(self, by, value):
        self.find_calls += 1
        if self.find_error:
            raise self.find_error
        return list(self.find_result)

    def set_implicit_wait(self, seconds):
        self.implicit_wait = seconds

    # tap 走 FakeElement，不经 driver


class FakeDeviceSession:
    """DeviceSession 替身。`ensure_alive` 可脚本化为抛 InfraError。"""

    def __init__(self, driver, alive_error=None):
        self._driver = driver
        self.alive_error = alive_error
        self.ensure_calls = 0

    def ensure_alive(self):
        self.ensure_calls += 1
        if self.alive_error:
            raise self.alive_error
        return self._driver


class FakeRecorder:
    def __init__(self):
        self.rows = []

    def record_step(self, *a, **kw):
        self.rows.append((a, kw))
        return len(self.rows)


def _ctx(driver, element_id="login_button", risk="LOW", idem=None,
         action="tap", value=None, has_postcondition=False,
         postcondition_spec=None):
    return RunStepContext(
        element_id=element_id,
        screen_id="LoginView",
        strategies=(("accessibility id", element_id),),
        action=action,
        value=value,
        risk=Risk[risk] if isinstance(risk, str) else risk,
        idempotency=(Idem[idem] if isinstance(idem, str) else idem),
        has_postcondition=has_postcondition or postcondition_spec is not None,
        postcondition_spec=postcondition_spec,
    )


@pytest.fixture(autouse=True)
def _clear_act_log():
    ACT_LOG.clear()
    yield
    ACT_LOG.clear()


def _runner(driver, guard=None, device=None):
    return StepRunner(
        executor=_FakeExecutor(driver),
        device_session=device or FakeDeviceSession(driver),
        guard=guard or Guard(env_kind=EnvKind.SANDBOX),
        recorder=FakeRecorder(),
    )


class _FakeExecutor:
    """包住 FakeDriver，暴露 7.1 管线要用的 find / perform 两个独立入口。"""

    def __init__(self, driver):
        self.driver = driver

    def find(self, locator):
        """7.1 的 find：任何失败 = PRE_DISPATCH（动作从未发出）。"""
        return self.driver.find_elements(*locator[0])

    def perform(self, action, element, value=None):
        """7.1 的 perform：失败 = POST_DISPATCH（已发出，结果未知）。"""
        if action == "tap":
            element.tap()
        elif action == "input":
            element.send_keys(value)


# ------------------------------------------------------------ S1 find 失败

def test_s1_find_zero_matches_is_pre_dispatch():
    """S1：find 返回 0 个 → PRE_DISPATCH（动作从未发出，可重试）。"""
    driver = FakeDriver(find_result=[])
    r = _runner(driver)
    out = r.run_step(_ctx(driver))
    assert out.ok is False
    assert out.phase == FailurePhase.PRE_DISPATCH
    assert out.failure_type == "ELEMENT_NOT_FOUND"
    assert out.retry_decision.allowed is True, "PRE_DISPATCH 必须可重试"
    assert ACT_LOG == [], "find 失败不该发出任何动作"


def test_s1_find_raises_is_pre_dispatch():
    """find 抛异常（策略层报错）同样归 PRE_DISPATCH。"""
    driver = FakeDriver(find_error=ValueError("bad strategy"))
    r = _runner(driver)
    out = r.run_step(_ctx(driver))
    assert out.phase == FailurePhase.PRE_DISPATCH
    assert out.retry_decision.allowed is True


# ------------------------------------------------------------ S2 act 超时

def test_s2_act_timeout_is_post_dispatch():
    """S2：act 抛错 → POST_DISPATCH（已发出，结果未知）。"""
    driver = FakeDriver()

    class _TimeoutExecutor(_FakeExecutor):
        def perform(self, action, element, value=None):
            raise TimeoutError("act timed out after dispatch")

    r = StepRunner(executor=_TimeoutExecutor(driver),
                   device_session=FakeDeviceSession(driver),
                   guard=Guard(env_kind=EnvKind.SANDBOX), recorder=FakeRecorder())
    out = r.run_step(_ctx(driver))
    assert out.phase == FailurePhase.POST_DISPATCH
    assert out.failure_type == "ACTION_OUTCOME_UNKNOWN"


def test_s2_post_dispatch_idempotent_is_retryable():
    driver = FakeDriver()

    class _TimeoutExecutor(_FakeExecutor):
        def perform(self, action, element, value=None):
            raise TimeoutError("boom")

    r = StepRunner(executor=_TimeoutExecutor(driver),
                   device_session=FakeDeviceSession(driver),
                   guard=Guard(env_kind=EnvKind.SANDBOX), recorder=FakeRecorder())
    out = r.run_step(_ctx(driver, idem="IDEMPOTENT"))
    assert out.phase == FailurePhase.POST_DISPATCH
    assert out.retry_decision.allowed is True


def test_s2_post_dispatch_non_idempotent_forbidden_h7():
    """H7：POST_DISPATCH 的非幂等步骤禁重试；有 postcondition 则改查它。"""
    driver = FakeDriver()

    class _TimeoutExecutor(_FakeExecutor):
        def perform(self, action, element, value=None):
            raise TimeoutError("boom")

    r = StepRunner(executor=_TimeoutExecutor(driver),
                   device_session=FakeDeviceSession(driver),
                   guard=Guard(env_kind=EnvKind.SANDBOX), recorder=FakeRecorder())
    out = r.run_step(_ctx(driver, idem="NON_IDEMPOTENT"))
    assert out.retry_decision.allowed is False
    assert out.retry_decision.failure_type_when_not_allowed == \
        "ACTION_OUTCOME_UNKNOWN"


def test_s2_post_dispatch_non_idempotent_with_postcondition():
    driver = FakeDriver()

    class _TimeoutExecutor(_FakeExecutor):
        def perform(self, action, element, value=None):
            raise TimeoutError("boom")

    r = StepRunner(executor=_TimeoutExecutor(driver),
                   device_session=FakeDeviceSession(driver),
                   guard=Guard(env_kind=EnvKind.SANDBOX), recorder=FakeRecorder())
    out = r.run_step(_ctx(driver, idem="NON_IDEMPOTENT",
                          has_postcondition=True))
    assert out.retry_decision.allowed is False
    assert out.retry_decision.check_postcondition is True
    assert out.retry_decision.recovered_kind_if_postcondition_holds == \
        "postcondition"


def test_s2_unknown_idempotency_treated_as_non_idempotent():
    """7.4-3：UNKNOWN 一律按 NON_IDEMPOTENT 处理。"""
    driver = FakeDriver()

    class _TimeoutExecutor(_FakeExecutor):
        def perform(self, action, element, value=None):
            raise TimeoutError("boom")

    r = StepRunner(executor=_TimeoutExecutor(driver),
                   device_session=FakeDeviceSession(driver),
                   guard=Guard(env_kind=EnvKind.SANDBOX), recorder=FakeRecorder())
    out = r.run_step(_ctx(driver, idem="UNKNOWN"))
    assert out.retry_decision.allowed is False


# ------------------------------------------------------------ S3 WDA 死亡

def test_s3_wda_dead_raises_infra_error_not_test_failure():
    """S3：ensure_alive 失败 → InfraError（**不是**测试失败）。7.5。"""
    driver = FakeDriver()
    ds = FakeDeviceSession(driver, alive_error=InfraError("WDA_DEAD"))
    r = _runner(driver, device=ds)
    with pytest.raises(InfraError):
        r.run_step(_ctx(driver))
    assert ACT_LOG == [], "WDA 死亡时不该发出动作"
    assert ds.ensure_calls == 1, "7.5：每个 action 前都要 ensure_alive"


def test_s3_wda_dead_before_find_so_nothing_dispatched():
    """ensure_alive 在 find 之前（7.1 管线顺序），故 WDA 死时 find 不该被调。"""
    driver = FakeDriver()
    ds = FakeDeviceSession(driver, alive_error=InfraError("WDA_DEAD"))
    r = _runner(driver, device=ds)
    with pytest.raises(InfraError):
        r.run_step(_ctx(driver))
    assert driver.find_calls == 0


# ------------------------------------------------------------ S4 多匹配

def test_s4_multi_match_is_ambiguous_and_fails_closed_h3():
    """S4：find 返回 ≥2 个 → AMBIGUOUS_ELEMENT，**禁止取第一个**（H3）。"""
    driver = FakeDriver(find_result=[FakeElement("a"), FakeElement("b")])
    r = _runner(driver)
    out = r.run_step(_ctx(driver))
    assert out.ok is False
    assert out.failure_type == "AMBIGUOUS_ELEMENT"
    assert ACT_LOG == [], "多匹配时禁止发出动作（H3）"


# ------------------------------------------------------------ 管线顺序

def test_pipeline_order_resolve_guard_ensure_find_perform():
    """7.1 管线顺序：guard 先于 ensure_alive 与 find——被拦的动作连设备
    都不该碰。

    注意 SECURITY_BLOCKED 是**判定结果**不是异常：设计 8.2 把它列为
    failure_type、8.1 列 BLOCKED 为 testcase 终态、8.4 退出码 4。把它当
    异常抛会让「终态 BLOCKED」这条链路断掉。
    """
    driver = FakeDriver()
    ds = FakeDeviceSession(driver)
    g = Guard(env_kind=EnvKind.SANDBOX)
    r = _runner(driver, guard=g, device=ds)
    out = r.run_step(_ctx(driver, risk="CRITICAL", element_id="pay_button"))
    assert out.ok is False
    assert out.failure_type == "SECURITY_BLOCKED"
    assert driver.find_calls == 0, "Guard 拦截不该走到 find"
    assert ds.ensure_calls == 0, "Guard 拦截不该走到 ensure_alive"
    assert ACT_LOG == [], "Guard 拦截不该发出动作"


def test_security_blocked_is_not_element_not_found():
    """被安全策略拦住 ≠ 元素找不到——两者混同会让报告指向错误方向。"""
    driver = FakeDriver()
    r = _runner(driver)
    out = r.run_step(_ctx(driver, risk="CRITICAL", element_id="pay_button"))
    assert out.failure_type == "SECURITY_BLOCKED"
    assert out.phase is None, "被拦在 find 之前，没有失败阶段"


def test_security_blocked_yields_blocked_testcase_status():
    """SECURITY_BLOCKED → testcase 终态 BLOCKED → 退出码 4（8.1/8.4）。"""
    from runner.result import RunResult, TestcaseResult, compute_exit_code
    driver = FakeDriver()
    r = _runner(driver)
    out = r.run_step(_ctx(driver, risk="CRITICAL", element_id="pay_button"))
    rr = RunResult(run_id="r1")
    rr.add(TestcaseResult(testcase_id="pay_case",
                          status="BLOCKED" if out.failure_type ==
                          "SECURITY_BLOCKED" else "FAIL",
                          failure_type=out.failure_type))
    assert rr.exit_code == 4
    assert compute_exit_code(["BLOCKED"]) == 4


def test_find_and_perform_are_two_separate_calls():
    """7.1 末段：**find 与 act 必须是两次独立调用**，这是阶段判定的前提。
    单测直接钉住这个结构约束。"""
    driver = FakeDriver()
    calls = []

    class _CountingExecutor(_FakeExecutor):
        def find(self, locator):
            calls.append("find")
            return super().find(locator)

        def perform(self, action, element, value=None):
            calls.append("perform")
            return super().perform(action, element, value)

    r = StepRunner(executor=_CountingExecutor(driver),
                   device_session=FakeDeviceSession(driver),
                   guard=Guard(env_kind=EnvKind.SANDBOX), recorder=FakeRecorder())
    r.run_step(_ctx(driver))
    assert calls == ["find", "perform"], "必须先 find 再 perform，且是两次调用"


def test_success_records_step_with_schema_0_1_fields():
    driver = FakeDriver()
    rec = FakeRecorder()
    r = StepRunner(executor=_FakeExecutor(driver),
                   device_session=FakeDeviceSession(driver),
                   guard=Guard(env_kind=EnvKind.SANDBOX), recorder=rec)
    out = r.run_step(_ctx(driver))
    assert out.ok is True
    assert out.phase is None, "成功没有失败阶段"
    assert rec.rows, "成功也要写 trace"


def test_non_idempotent_dispatched_tracked_on_outcome():
    """7.5：非幂等步骤到达 POST_DISPATCH 就置标记（后果不可回滚）。"""
    driver = FakeDriver()
    r = _runner(driver)
    out = r.run_step(_ctx(driver, idem="NON_IDEMPOTENT", element_id="pay_button"))
    assert out.non_idempotent_dispatched is True


def test_idempotent_does_not_set_dispatched_flag():
    driver = FakeDriver()
    r = _runner(driver)
    out = r.run_step(_ctx(driver, idem="IDEMPOTENT"))
    assert out.non_idempotent_dispatched is False


def test_find_failure_does_not_set_dispatched_flag():
    """PRE_DISPATCH 失败 → 动作从未发出 → 不置标记。"""
    driver = FakeDriver(find_result=[])
    r = _runner(driver)
    out = r.run_step(_ctx(driver, idem="NON_IDEMPOTENT"))
    assert out.non_idempotent_dispatched is False


def test_input_action_passes_value():
    driver = FakeDriver(find_result=[FakeElement("username_field")])
    r = _runner(driver)
    out = r.run_step(_ctx(driver, element_id="username_field",
                          action="input", value="qa_agent"))
    assert out.ok is True
    assert ACT_LOG == [("input", "username_field", "qa_agent")]


# ------------------------------------------------------------ Task 2.7
# postcondition 执行端（H7 闭环）：此前 StepRunner 只把 has_postcondition
# 用于重试决策，从不执行检查——`RECOVERED(kind=postcondition)` 无判定源。

class _PostSpec:
    """测试替身：形状对齐 testcase.schema.Postcondition。"""

    def __init__(self, target="screen:LoginView", condition="active",
                 timeout=10):
        from testcase.schema import TargetRef
        self.target = TargetRef(**({"id": target.split(":", 1)[1],
                                    "type": "screen"} if ":" in target
                                   else {"id": target, "type": "element"}))
        self.condition = condition
        self.timeout = timeout
        self.expected = None


def _ctx_with_post(driver, *, checker_result=True, idem="NON_IDEMPOTENT"):
    class _CheckerProbe:
        def __init__(self, result):
            self.result = result
            self.calls = []

        def check(self, spec):
            self.calls.append(spec)
            return self.result

    probe = _CheckerProbe(checker_result)
    r = StepRunner(
        executor=_FakeExecutor(driver).__class__(driver)
        if False else _FakeExecutor(driver),
        device_session=FakeDeviceSession(driver),
        guard=Guard(env_kind=EnvKind.SANDBOX), recorder=FakeRecorder(),
        postcondition_checker=probe.check)
    return r, probe


def test_postcondition_satisfied_marks_success():
    """满足 → 动作成功，detail 如实记录（可观测）。"""
    driver = FakeDriver()
    r, probe = _ctx_with_post(driver, checker_result=True)
    out = r.run_step(_ctx(driver, idem="NON_IDEMPOTENT",
                          postcondition_spec=_PostSpec()))
    assert out.ok is True
    assert out.detail.get("postcondition_satisfied") is True
    assert len(probe.calls) == 1


def test_postcondition_not_satisfied_is_outcome_unknown():
    """不满足 → ACTION_OUTCOME_UNKNOWN（不是 PASS——那会把「没做到」当成功）；
    也不是直接 FAIL——恢复判定归 RecoveryEngine，这里只如实报告。"""
    driver = FakeDriver()
    r, probe = _ctx_with_post(driver, checker_result=False)
    out = r.run_step(_ctx(driver, idem="NON_IDEMPOTENT",
                          postcondition_spec=_PostSpec()))
    assert out.ok is False
    assert out.failure_type == "ACTION_OUTCOME_UNKNOWN"
    assert out.phase is FailurePhase.POST_DISPATCH
    assert out.detail.get("postcondition_satisfied") is False
    # 非幂等 + 不满足：重试仍禁止（H7），postcondition 已查过
    assert out.retry_decision.allowed is False
    assert out.retry_decision.check_postcondition is True


def test_postcondition_checker_error_is_not_success_nor_fail():
    """checker 自身故障 → UNKNOWN：记 postcondition_error，不当成功也不当
    失败（把观测故障伪装成判定结果是 8.2 反模式）。"""
    driver = FakeDriver()

    def _boom(spec):
        raise RuntimeError("checker exploded")

    r = StepRunner(executor=_FakeExecutor(driver),
                   device_session=FakeDeviceSession(driver),
                   guard=Guard(env_kind=EnvKind.SANDBOX), recorder=FakeRecorder(),
                   postcondition_checker=_boom)
    out = r.run_step(_ctx(driver, idem="NON_IDEMPOTENT",
                          postcondition_spec=_PostSpec()))
    # 动作本身成功发出 → ok 保持 True，但 detail 如实暴露故障
    assert out.ok is True
    assert "postcondition_error" in out.detail
    assert "RuntimeError" in out.detail["postcondition_error"]
    assert "postcondition_satisfied" not in out.detail


def test_postcondition_spec_without_checker_is_skipped_loudly():
    """有 spec 但未注入 checker → detail 记 postcondition_skipped，
    不静默当成功——「参数给了但没执行」必须可见。"""
    driver = FakeDriver()
    r = _runner(driver)
    out = r.run_step(_ctx(driver, idem="NON_IDEMPOTENT",
                          postcondition_spec=_PostSpec()))
    assert out.ok is True
    assert out.detail.get("postcondition_skipped") == "no checker injected"


def test_no_postcondition_no_checker_calls():
    """未声明 postcondition → 绝不调 checker（无谓轮询 + 拖慢用例）。"""
    driver = FakeDriver()
    calls = []

    class _Probe:
        def check(self, spec):
            calls.append(spec)
            return True

    r = StepRunner(executor=_FakeExecutor(driver),
                   device_session=FakeDeviceSession(driver),
                   guard=Guard(env_kind=EnvKind.SANDBOX), recorder=FakeRecorder(),
                   postcondition_checker=_Probe().check)
    out = r.run_step(_ctx(driver, idem="IDEMPOTENT"))
    assert out.ok is True
    assert calls == []
    assert out.detail == {}
