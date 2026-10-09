"""Task 1.3 / P3-03：Agent 工具层（唯一分发层；设计 §10/§10.1）。

plan Steps 的五条验收：
1. 注册表键集 == `ALLOWED_TOOLS`；
2. 禁用名八项逐一断言不存在（矩阵 #2；**静态断言在 CI 门禁文件**
   `test_tool_allowlist_gate.py`，这里测**行为**：调用它们拿不到工具）；
3. 执行类动作被 Guard 拦 → BLOCK + `agent_trace` 有记录（Audit 不静默）；
4. 同一输入下工具层 Guard 结论与 `runner.run_step` 一致（F1：**同一个** Guard）；
5. 未接线工具调用 → 明确报错不静默。

Fixture 用**真 Repository**（`fi_support.drift_repo`，含 LOW/HIGH/CRITICAL 元素）
+ 真 `SQLiteAgentStore`（审计落库）——不手搓替身 SQL。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.models import AssetTier
from agents.tools import (ALLOWED_TOOLS, BANNED_TOOLS, GUARDED_TOOLS, NOT_WIRED,
                          TOOL_METHODS, AgentToolkit, ToolDependencyError,
                          ToolNotWired, ToolResult, ToolViolation,
                          ToolkitAuditError)
from executor.guard import EnvKind, Guard
from tests.fault_injection.fi_support import FakeDS, FakeExecutor, drift_repo

BUILD = "local"


# --- fixture 与替身 -----------------------------------------------------------


@pytest.fixture()
def repo(tmp_path):
    """真 Repository：login_button(LOW) / pay_button(HIGH) /
    confirm_pay_button(CRITICAL)。"""
    return drift_repo(tmp_path)


@pytest.fixture()
def agent_store(tmp_path):
    """真 agent.db（BLOCK 的审计落点）。"""
    from agents.storage import SQLiteAgentStore
    store = SQLiteAgentStore(tmp_path / "agent.db")
    store.create_task("task_1", agent_type="explorer", goal="explore Home")
    return store


def _locator_for(repo, build: str = BUILD):
    """与 `cli/pipeline._locator_for` 同形的定位解析（生产由 pipeline 注入）。"""
    def locate(target):
        eff = repo.resolve(target, build=build)
        if hasattr(eff, "marker"):
            return [{"type": "accessibility_id", "value": eff.marker}]
        return [{"type": s.type, "value": s.value} for s in eff.strategies]
    return locate


def _toolkit(repo, *, guard=None, ex=None, ds=None, agent_db=None,
             task_id=None, **kw) -> AgentToolkit:
    return AgentToolkit(
        guard=guard if guard is not None else Guard(EnvKind.SANDBOX),
        executor=ex if ex is not None else FakeExecutor(),
        locator_for=_locator_for(repo),
        repository=repo,
        device_session=ds if ds is not None else FakeDS(),
        agent_db=agent_db, task_id=task_id, build=BUILD, **kw)


class _SpyGuard(Guard):
    """记录 `check` 收到的 GuardContext（验「Guard 的输入怎么来的」）。"""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.seen = []

    def check(self, ctx):
        self.seen.append(ctx)
        return super().check(ctx)


class _DSWithBack(FakeDS):
    """`ensure_alive()` 返回自带 `back()` 的对象——**复刻生产契约**
    （`DeviceSession.ensure_alive` 返回 driver）。"""

    def __init__(self):
        super().__init__()
        self.backs = 0

    def ensure_alive(self):
        super().ensure_alive()
        return self

    def back(self) -> None:
        self.backs += 1


class _ShotExec(FakeExecutor):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.shots: list[str] = []

    def screenshot(self, path: str) -> None:
        self.shots.append(path)


class _FindExec(FakeExecutor):
    """`tap` / `input` **先 `find`**——复刻生产 `Executor.tap = find(locator)
    .click()`（`FakeExecutor.tap` 是给 runner 的宽松桩，不消费 find 脚本，
    用它测不出「元素找不到」）。"""

    def tap(self, locator) -> None:
        self.find(locator)
        self.tap_calls += 1

    def input(self, locator, value: str) -> None:
        self.find(locator)
        self.input_calls += 1


class _FakeWait:
    def __init__(self, *, raise_timeout: bool = False):
        self.raise_timeout = raise_timeout
        self.specs: list = []

    def wait_for(self, spec) -> None:
        self.specs.append(spec)
        if self.raise_timeout:
            from executor.wait import WaitTimeout
            raise WaitTimeout("timed out after 10s")


class _FakeAssert:
    def __init__(self, mode: str = "pass"):
        self.mode = mode
        self.specs: list = []

    def check(self, spec):
        from executor.assertion import (AssertionResult, AssertionTargetDrift,
                                        AssertionValueMismatch)
        self.specs.append(spec)
        res = AssertionResult(condition=spec.condition, target="login_button",
                              expected=spec.expected, actual="other",
                              passed=self.mode == "pass")
        if self.mode == "pass":
            return res
        if self.mode == "drift":
            raise AssertionTargetDrift(res)
        raise AssertionValueMismatch(res)


# --- 1. 注册表 == ALLOWED_TOOLS ----------------------------------------------


def test_registry_covers_exactly_the_allowlist():
    """注册表键集 == `ALLOWED_TOOLS`（**派生**自 `TOOL_ASSET_TIER`，不手抄）。"""
    assert len(ALLOWED_TOOLS) == 22
    assert set(TOOL_METHODS) == set(ALLOWED_TOOLS)
    from agents.models import TOOL_ASSET_TIER
    assert ALLOWED_TOOLS == frozenset(TOOL_ASSET_TIER)


def test_registry_methods_all_exist():
    for tool, method in TOOL_METHODS.items():
        assert callable(getattr(AgentToolkit, method, None)), \
            f"{tool} 映射到不存在的 {method}"


def test_wired_and_not_wired_partition_the_allowlist():
    """已接线 ∪ 未接线 == 全集，且**不相交**（每张表都可读、可断言）。"""
    assert set(NOT_WIRED) <= ALLOWED_TOOLS
    wired = set(TOOL_METHODS) - set(NOT_WIRED)
    assert wired | set(NOT_WIRED) == set(ALLOWED_TOOLS)
    assert not (wired & set(NOT_WIRED))
    assert len(wired) == 11 and len(NOT_WIRED) == 11


def test_guarded_tools_are_a_subset_of_the_allowlist():
    assert GUARDED_TOOLS <= ALLOWED_TOOLS


# --- 2. 被禁工具（矩阵 #2 的行为面） -----------------------------------------


@pytest.mark.parametrize("tool", sorted(BANNED_TOOLS))
def test_banned_tools_are_not_callable(repo, tool):
    """设计 §10.1 末句的 8 个名字**在构造上不存在**（F2）。"""
    tk = _toolkit(repo)
    with pytest.raises(ToolViolation, match="不在 Agent 工具表里"):
        tk.call(tool)


def test_unknown_tool_is_a_violation(repo):
    with pytest.raises(ToolViolation):
        _toolkit(repo).call("definitely_not_a_tool")


def test_toolkit_has_no_escape_hatch():
    """没有 `__getattr__` / `__getitem__` 之类的绕过入口（F12）。"""
    assert not hasattr(AgentToolkit, "__getattr__")
    assert not hasattr(AgentToolkit, "__getitem__")
    assert not hasattr(AgentToolkit, "execute")     # 不存在「万能执行」入口


# --- 3. BLOCK + 审计（矩阵 #1 的工具层） -------------------------------------


def test_low_risk_tap_is_allowed_in_sandbox(repo):
    ex = FakeExecutor()
    r = _toolkit(repo, ex=ex).call("tap", target="HomeView.login_button")
    assert r.ok and r.guard == "ALLOW" and r.failure_type is None
    assert ex.tap_calls == 1
    assert r.tier is AssetTier.READ_ONLY


def test_critical_risk_tap_is_blocked_and_audited(repo, agent_store):
    """CRITICAL 元素在 sandbox 也被拦（10.1 规则 1）→ BLOCK 且**必须留痕**。"""
    ex = FakeExecutor()
    tk = _toolkit(repo, ex=ex, agent_db=agent_store, task_id="task_1")
    r = tk.call("tap", target="HomeView.confirm_pay_button")

    assert r.ok is False and r.guard == "BLOCK"
    assert r.failure_type == "SECURITY_BLOCKED"
    assert "CRITICAL" in r.blocked_reason
    assert ex.tap_calls == 0, "被拦的动作连设备都不该碰"

    trace = agent_store.get_trace("task_1")
    assert len(trace) == 1, "BLOCK 必须写 agent_trace（审计不静默）"
    assert trace[0].guard_result == "BLOCK"
    assert trace[0].result == "BLOCKED"
    assert trace[0].observation["tool"] == "tap"
    assert trace[0].observation["element_id"] == "confirm_pay_button"
    assert trace[0].decision["risk"] == "CRITICAL"


def test_blocked_targets_block_and_audit(repo, agent_store):
    """`blocked_targets`（10.1 规则 3）在工具层同样生效（同一个 Guard）。"""
    from executor.guard import BlockedTarget
    guard = Guard(EnvKind.SANDBOX, blocked_targets=(
        BlockedTarget.parse("element:login_*"),))
    ex = FakeExecutor()
    tk = _toolkit(repo, guard=guard, ex=ex, agent_db=agent_store,
                  task_id="task_1")
    r = tk.call("tap", target="HomeView.login_button")
    assert r.guard == "BLOCK" and ex.tap_calls == 0
    assert agent_store.get_trace("task_1")[0].guard_result == "BLOCK"


def test_block_cannot_be_silently_dropped(repo):
    """BLOCK 却**无法留痕** → 报错，不是「算了不记了」（F1 的审计根基）。"""
    tk = _toolkit(repo)                     # 没配 agent_db / task_id
    with pytest.raises(ToolkitAuditError, match="无法留痕"):
        tk.call("tap", target="HomeView.confirm_pay_button")


def test_block_audit_does_not_record_the_input_value(repo, agent_store):
    """审计记「想干什么」，**不记秘密**（14.4 的 SENSITIVE/SECRET 遮蔽）。"""
    from executor.guard import BlockedTarget
    guard = Guard(EnvKind.SANDBOX, blocked_targets=(
        BlockedTarget.parse("element:login_*"),))
    tk = _toolkit(repo, guard=guard, agent_db=agent_store, task_id="task_1")
    tk.call("input", target="HomeView.login_button", value="hunter2")
    row = agent_store.get_trace("task_1")[0]
    assert "hunter2" not in str(row.observation)
    assert "hunter2" not in str(row.decision)


# --- 4. Guard 输入与结论（F1） ------------------------------------------------


def test_guard_context_uses_the_pipeline_risk_formula(repo):
    """Guard 的输入必须与 `cli/pipeline._prepare` **同式**推导。

    工具层若自己造一套风险推导（只用关键词、或漏掉 metadata 的 `risk:`），
    「工具层与 run_step 结论一致」就成了巧合。
    """
    from executor.policy import effective_risk
    from testcase.schema import Risk

    guard = _SpyGuard(EnvKind.SANDBOX)
    _toolkit(repo, guard=guard).call("tap", target="HomeView.pay_button")

    [ctx] = guard.seen
    eff = repo.resolve("HomeView.pay_button", build=BUILD)
    assert ctx.action == "tap"
    assert ctx.element_id == "pay_button" and ctx.screen_id == "HomeView"
    assert ctx.risk is effective_risk(element=eff.risk, element_id=eff.id)
    assert ctx.risk is Risk.HIGH, "metadata 的 `risk: HIGH` 必须进 Guard"


def test_guard_context_for_targetless_action_is_empty_scope(repo):
    """`swipe` 没有 screen/element：如实留空（不是编一个）。"""
    guard = _SpyGuard(EnvKind.SANDBOX)
    _toolkit(repo, guard=guard).call("swipe", direction="up")
    [ctx] = guard.seen
    assert ctx.screen_id == "" and ctx.element_id == ""
    assert ctx.action == "swipe"


def _run_step(repo, guard, target, *, action="tap"):
    """按 pipeline 的形态跑一次 `StepRunner.run_step`（对照组）。"""
    from executor.policy import effective_risk
    from runner.runner import RunStepContext, StepRunner

    eff = repo.resolve(target, build=BUILD)
    ctx = RunStepContext(
        element_id=eff.id, screen_id=eff.screen,
        strategies=[{"type": s.type, "value": s.value} for s in eff.strategies],
        action=action,
        risk=effective_risk(element=eff.risk, element_id=eff.id))
    return StepRunner(FakeExecutor(), FakeDS(), guard).run_step(ctx)


@pytest.mark.parametrize("env_kind,allow_prod,target,blocked", [
    (EnvKind.PRODUCTION, True, "HomeView.pay_button", True),        # HIGH + prod
    (EnvKind.PRODUCTION, True, "HomeView.login_button", False),     # LOW + prod
    (EnvKind.SANDBOX, False, "HomeView.pay_button", False),         # HIGH + sandbox
    (EnvKind.SANDBOX, False, "HomeView.confirm_pay_button", True),  # CRITICAL
])
def test_guard_verdict_matches_run_step(repo, agent_store, env_kind,
                                        allow_prod, target, blocked):
    """F1：**同一个 Guard** 下，工具层与 `run_step` 的结论必须一致。

    双向都测（该拦的拦、该放的放）——只测「都拦」无法排除「工具层恒拦」。
    """
    guard = Guard(env_kind, allow_production=allow_prod)

    out = _run_step(repo, guard, target)
    runner_blocked = out.failure_type == "SECURITY_BLOCKED"

    tk = _toolkit(repo, guard=guard, agent_db=agent_store, task_id="task_1")
    res = tk.call("tap", target=target)
    toolkit_blocked = res.guard == "BLOCK"

    assert runner_blocked is blocked, f"run_step 结论不符：{out.failure_type}"
    assert toolkit_blocked is blocked, f"工具层结论不符：{res.blocked_reason}"
    assert runner_blocked == toolkit_blocked, "同一 Guard 下两侧结论必须一致"


# --- 5. 未接线工具（明确报错不静默） -----------------------------------------


@pytest.mark.parametrize("tool", sorted(NOT_WIRED))
def test_unwired_tool_fails_loud_with_the_task(repo, tool):
    """已注册但没实现 → `ToolNotWired`，且**消息里带接线任务号**。

    绝不静默返回空结果——那会让 Agent 把「没实现」读成「查询结果为空」。
    """
    tk = _toolkit(repo)
    with pytest.raises(ToolNotWired) as e:
        tk.call(tool)
    assert NOT_WIRED[tool].split("（")[0] in str(e.value), \
        "报错要点名接线任务，否则调用方无从下手"
    assert "不静默" in str(e.value)


def test_wired_tool_with_missing_dependency_raises(repo):
    """依赖没注入 → `ToolDependencyError`（不静默降级）。"""
    tk = AgentToolkit(guard=Guard(EnvKind.SANDBOX))     # 什么都没注入
    with pytest.raises(ToolDependencyError, match="没注入"):
        tk.call("get_ui_tree")
    with pytest.raises(ToolDependencyError, match="没注入"):
        tk.call("tap", target="HomeView.login_button")


# --- 只读工具（不经 Guard） ---------------------------------------------------


def test_read_tools_do_not_go_through_guard(repo):
    """设计 §10.1 的分组：只读工具不经 Guard（`guard` 字段为 None）。

    这不是「漏了一道闸」：Guard 的判据是**动作风险**，只读没有动作可判；
    环境侧的兜底是 F7 的启动期校验（production 下自主命令整体不存在）。
    """
    ex = FakeExecutor(page_source=(
        "<App><Node name='screen.HomeView' visible='true'/></App>"))
    tk = AgentToolkit(guard=Guard(EnvKind.PRODUCTION), executor=ex,
                      repository=repo)          # production 下也只读
    cur = tk.call("get_current_screen")
    assert cur.ok and cur.guard is None
    assert cur.value["screen"] == "HomeView"
    assert cur.value["status"] == "FOUND"
    assert cur.value["fingerprint"], "屏指纹用 F8 的同一套哈希工具"
    assert tk.call("get_ui_tree").value.startswith("<App>")


def test_input_does_not_echo_the_value(repo):
    """`input` 的值可能是密码/验证码（14.4）——返回值不得回显明文。"""
    tk = _toolkit(repo)
    r = tk.call("input", target="HomeView.login_button", value="hunter2")
    assert r.ok and r.value["value_len"] == 7
    assert "hunter2" not in repr(r.value)


# --- 执行类：wait / assert / screenshot / back / swipe -----------------------


def test_wait_timeout_is_a_result_not_an_exception(repo):
    """等待超时是**观测结果**（Agent 要能把「没等到」当成信息）。"""
    tk = _toolkit(repo, wait_engine=_FakeWait(raise_timeout=True))
    r = tk.call("wait", target="screen:HomeView", condition="active")
    assert r.ok and r.value["passed"] is False
    assert r.value["failure_type"] == "WAIT_TIMEOUT"
    assert r.step_failure_type == "WAIT_TIMEOUT", "内层结论由 step_failure_type 统一读"


def test_wait_satisfied(repo):
    eng = _FakeWait()
    r = _toolkit(repo, wait_engine=eng).call(
        "wait", target="screen:HomeView", condition="active")
    assert r.value["passed"] is True
    assert eng.specs[0].condition == "active"
    assert eng.specs[0].target.id == "HomeView"


def test_assert_text_mismatch_keeps_the_p1_classification(repo):
    """值不符 vs 目标漂移对 Recovery 含义不同（H6 / 7.3）——不能被压成一个布尔。"""
    tk = _toolkit(repo, assertion_engine=_FakeAssert("mismatch"))
    r = tk.call("assert_text", target="HomeView.login_button",
                expected="Sign in")
    assert r.ok and r.value["passed"] is False
    assert r.value["failure_type"] == "ASSERTION_VALUE_MISMATCH"
    assert r.value["expected"] == "Sign in"


def test_assert_target_drift_keeps_its_own_classification(repo):
    tk = _toolkit(repo, assertion_engine=_FakeAssert("drift"))
    r = tk.call("assert_exists", target="ProfileView.ghost_button")
    assert r.value["failure_type"] == "ASSERTION_TARGET_DRIFT"


def test_assert_text_maps_tool_vocabulary_to_6_2_conditions(repo):
    """工具名与 6.2 的条件名是两套词汇，映射只此一处。"""
    eng = _FakeAssert()
    tk = _toolkit(repo, assertion_engine=eng)
    tk.call("assert_text", target="HomeView.login_button", expected="x",
            condition="contains")
    assert eng.specs[-1].condition == "text_contains"
    with pytest.raises(ValueError, match="condition"):
        tk.call("assert_text", target="HomeView.login_button", expected="x",
                condition="bogus")


# --- 失败分类的两个落点（review_p3_task13 P3-1） ------------------------------


def test_step_failure_type_merges_both_places(repo):
    """`step_failure_type` 是**唯一读取口**：外层「动作没做成」+ 内层「判定结论」。

    当前由调用点形态保证两处不同时存在（见 `ToolResult` docstring 的表）。
    M2 的 Agent 循环把工具结论映射进 `agent_trace` 时读这个属性即可——不必让
    每个调用方都记得「两处查找」。
    """
    # 外层：元素找不到
    from executor.executor import ElementNotFound
    r = _toolkit(repo, ex=_FindExec(find_script=[ElementNotFound("x")])).call(
        "tap", target="HomeView.login_button")
    assert r.failure_type == "ELEMENT_NOT_FOUND"
    assert r.value is None
    assert r.step_failure_type == "ELEMENT_NOT_FOUND"

    # 内层：断言不过
    r2 = _toolkit(repo, assertion_engine=_FakeAssert("mismatch")).call(
        "assert_exists", target="HomeView.login_button")
    assert r2.failure_type is None
    assert r2.value["failure_type"] == "ASSERTION_VALUE_MISMATCH"
    assert r2.step_failure_type == "ASSERTION_VALUE_MISMATCH"

    # 成功路径：两处都没有
    r3 = _toolkit(repo).call("swipe", direction="up")
    assert r3.step_failure_type is None


def test_step_failure_type_prefers_the_outer_place():
    """两处**同时**有值时（当前调用点不产出这种，但若产出）**外层优先**。

    措辞是「优先 / 回退」而不是「互斥」（review_p3_task14 小观察 1）：互斥没有
    机械守卫（AST 扫 3 个 `ToolResult(...)` 构造点，无一处同写两处），而这条断言
    至少把「两处都有时谁赢」钉成**可预期**的行为。
    """
    both = ToolResult(tool="wait", tier=AssetTier.READ_ONLY, ok=False,
                      failure_type="OUTER", value={"failure_type": "INNER"})
    assert both.step_failure_type == "OUTER"
    assert ToolResult(tool="x", tier=AssetTier.READ_ONLY,
                      value={"failure_type": "INNER"}).step_failure_type == "INNER"


def test_assert_failure_type_does_not_depend_on_the_exception_message(repo):
    """分类来自**异常类**，不是消息文案（review_p3_task13 P3-2）。

    用真异常类构造一个**消息被改过**的实例：分类仍必须正确——`str(e).split(":")[0]`
    那版会在改文案时给出错分类（结论不该由另一个模块的文案格式承担）。
    """
    from executor.assertion import AssertionValueMismatch, AssertionResult

    class _Reworded(AssertionValueMismatch):
        """模拟上游改了文案（甚至去掉前缀）。"""

        def __init__(self, result):
            Exception.__init__(self, "值不符（文案改了）")
            self.result = result

    class _Engine:
        def check(self, spec):
            res = AssertionResult(condition=spec.condition, target="t",
                                  expected="a", actual="b", passed=False)
            raise _Reworded(res)

    r = _toolkit(repo, assertion_engine=_Engine()).call(
        "assert_exists", target="HomeView.login_button")
    assert r.value["failure_type"] == "ASSERTION_VALUE_MISMATCH"


def test_screenshot_writes_under_the_configured_dir(repo, tmp_path):
    ex = _ShotExec()
    tk = _toolkit(repo, ex=ex, screenshot_dir=tmp_path / "shots")
    r = tk.call("screenshot")
    assert r.ok
    assert Path(r.value["path"]).parent == tmp_path / "shots"


def test_back_uses_the_device_session_driver(repo):
    ds = _DSWithBack()
    r = _toolkit(repo, ds=ds).call("back")
    assert r.ok and ds.backs == 1


def test_swipe_dispatches_to_executor(repo):
    r = _toolkit(repo).call("swipe", direction="down")
    assert r.ok and r.value["direction"] == "down"


def test_element_not_found_is_a_result_with_p1_failure_type(repo):
    """元素找不到 → `ok=False` + 与 P1 同域的 `failure_type`。"""
    from executor.executor import ElementNotFound
    ex = _FindExec(find_script=[ElementNotFound("nope")])
    r = _toolkit(repo, ex=ex).call("tap", target="HomeView.login_button")
    assert r.ok is False and r.failure_type == "ELEMENT_NOT_FOUND"
    assert r.guard == "ALLOW", "Guard 放行了，失败在执行阶段"


def test_ambiguous_element_is_a_result(repo):
    """≥2 命中 → `AMBIGUOUS_ELEMENT`（H3：取第一个是禁止的）。"""
    from executor.executor import AmbiguousElement
    ex = _FindExec(find_script=[AmbiguousElement("2 matches")])
    r = _toolkit(repo, ex=ex).call("tap", target="HomeView.login_button")
    assert r.ok is False and r.failure_type == "AMBIGUOUS_ELEMENT"


def test_infra_error_propagates(repo):
    """设备故障不是测试结果——与 `run_step` 同款：照原样上抛。"""
    from session.device_session import InfraError
    ex = _FindExec(find_script=[InfraError("WDA died")])
    with pytest.raises(InfraError):
        _toolkit(repo, ex=ex).call("tap", target="HomeView.login_button")


def test_bad_arguments_raise_instead_of_becoming_a_result(repo):
    """**编程错误上抛**：坏参数不该被翻成「工具执行失败」的结果。

    否则 `assert_text(condition="bogus")` 会看起来像「断言没通过」——把
    调用方写错伪装成运行期结论（模块 docstring 的「返回值的分工」）。
    """
    tk = _toolkit(repo, assertion_engine=_FakeAssert())
    with pytest.raises(ValueError, match="condition"):
        tk.call("assert_text", target="HomeView.login_button", expected="x",
                condition="bogus")
    # `WaitSpec` 的 condition 也是 Literal——非法值必须**炸**，不是返回 ok=False。
    # 断言收紧成 ValueError（pydantic 的 ValidationError 是它的子类）：
    # `pytest.raises(Exception)` 会把 ToolDependencyError/TypeError 之类无关异常
    # 也算通过，与 docstring 的意图不等义（review_p3_task13 P3-3）。
    with pytest.raises(ValueError):
        _toolkit(repo, wait_engine=_FakeWait()).call(
            "wait", target="HomeView.login_button", condition="bogus")


# --- create_promotion_proposal（唯一能「建议」改 Repository 的方式） ----------


def _seeded_experience(tmp_path):
    from experience import SQLiteExperienceStore
    from experience.models import CandidateSeed
    from repository.loader import LocatorStrategy

    store = SQLiteExperienceStore(tmp_path / "experience.db")
    exp = store.create_candidate(CandidateSeed(
        review_id=1, recovery_id=1, seed_run_id="run_seed", seed_step_id=1,
        seed_recovery_review_id=1, app_id="com.matrix.app",
        screen_id="HomeView", target_id="login_button",
        strategy=LocatorStrategy(type="accessibility_id", value="login_button",
                                 origin="experience")))
    return store, exp


def test_create_promotion_proposal_delegates_to_promoter(tmp_path):
    store, exp = _seeded_experience(tmp_path)
    tk = AgentToolkit(guard=Guard(EnvKind.SANDBOX), experience_store=store)

    # CANDIDATE 非 VERIFIED → 9.5 需要显式人工路径；不给就如实回报
    r1 = tk.call("create_promotion_proposal", experience_id=exp.experience_id)
    assert r1.value["passed"] is False
    assert r1.value["failure_type"] == "PROMOTION_REJECTED"

    r2 = tk.call("create_promotion_proposal", experience_id=exp.experience_id,
                 manual_override=True, reason="人工判断", reviewer="tester")
    assert r2.value["passed"] is True and r2.value["proposal_id"]

    r3 = tk.call("create_promotion_proposal", experience_id="ghost")
    assert r3.value["failure_type"] == "EXPERIENCE_NOT_FOUND"


def test_create_promotion_proposal_does_not_go_through_guard(tmp_path):
    """候选创建不碰 App → 不经 Guard（但 approve 仍归人工，9.3 两段式）。"""
    store, exp = _seeded_experience(tmp_path)
    guard = _SpyGuard(EnvKind.PRODUCTION)
    tk = AgentToolkit(guard=guard, experience_store=store)
    tk.call("create_promotion_proposal", experience_id=exp.experience_id)
    assert guard.seen == [], "不碰 App 的工具不该走 Guard"
