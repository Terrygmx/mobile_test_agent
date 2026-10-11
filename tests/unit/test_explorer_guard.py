"""Task 4.1 / P3-14：Explorer Loop 骨架 + Guard 接入（F1）。

plan Steps 的判据：
1. HIGH/CRITICAL 目标 → Guard BLOCK、不执行、`agent_trace` 有
   `guard_result=BLOCK` 记录（**非静默跳过**——步骤留痕、循环继续）；
2. Guard 与 P1 **同一函数对象**（import 级断言：`effective_risk` 就是
   `executor.policy` 的那个本体，F1 不许第二套风险推导）；
3. explorer 包 grep 级断言：无直接设备访问（矩阵 #13 前半）——唯一放行的
   `executor.*` import 是纯函数 `executor.policy.effective_risk`（plan 4.1
   ⚠️ 注明本体就在那里）；设备动作一律经 `AgentToolkit`（F12）。

Fixture 沿用 `fi_support` 的真 Repository（LOW/HIGH/CRITICAL 元素）+
`FakeExecutor` + 真 `SQLiteAgentStore`（trace 落点）——不手搓替身 SQL。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agents.models import AgentState
from agents.storage import SQLiteAgentStore
from agents.tools import AgentToolkit
from executor.guard import EnvKind, Guard
from graph.models import (EVIDENCE_SOURCE_DECLARED, SOURCE, RuntimeGraph,
                          ScreenNode)
from tests.fault_injection.fi_support import FakeExecutor, drift_repo

BUILD = "local"
HOME_PAGE = ("<App><Node name='screen.HomeView' visible='true'/></App>")
PROFILE_PAGE = ("<App><Node name='screen.ProfileView' visible='true'/></App>")


# --- fixture 与替身 -----------------------------------------------------------


@pytest.fixture()
def repo(tmp_path):
    return drift_repo(tmp_path)


@pytest.fixture()
def agent_store(tmp_path):
    store = SQLiteAgentStore(tmp_path / "agent.db")
    store.create_task("task_1", agent_type="explorer", goal="explore Home")
    return store


def _toolkit(repo, *, ex=None, agent_db=None, task_id=None) -> AgentToolkit:
    from tests.unit.test_agent_tools import _locator_for
    return AgentToolkit(
        guard=Guard(EnvKind.SANDBOX), executor=ex or FakeExecutor(),
        locator_for=_locator_for(repo), repository=repo,
        agent_db=agent_db, task_id=task_id, build=BUILD)


class _PageScriptExec(FakeExecutor):
    """`tap` 后页面切换——verify 步「新 State？」要能观测到变化。"""

    def __init__(self, pages, **kw):
        super().__init__(**kw)
        self._pages = list(pages)
        self._i = 0

    def tap(self, strategies):
        super().tap(strategies)
        self._i = min(self._i + 1, len(self._pages) - 1)

    def page_source(self):
        return self._pages[self._i]


def _scripted(*actions):
    """剧本 decider：按序弹出动作，耗尽后返回 None（→ COMPLETED）。"""
    it = iter(actions)

    def decide(observation, graph, visited):
        return next(it, None)

    return decide


def _graph(*screen_ids):
    return RuntimeGraph(
        app_id="demo", app_build=BUILD, source_of=SOURCE,
        nodes=tuple(ScreenNode(s, source_of=SOURCE,
                               evidence=(EVIDENCE_SOURCE_DECLARED,))
                    for s in screen_ids))


def _explorer(repo, agent_store, toolkit, decider):
    from explorer.agent import Explorer
    return Explorer(toolkit=toolkit, graph=_graph("HomeView", "ProfileView"),
                    agent_db=agent_store, task_id="task_1", decider=decider,
                    repository=repo)


# --- 矩阵 #13 前半：HIGH+ 被 Guard 拦截 → 写审计不执行 -------------------------


def test_high_risk_action_is_blocked_and_audited_not_executed(repo, agent_store):
    ex = FakeExecutor()
    toolkit = _toolkit(repo, ex=ex, agent_db=agent_store, task_id="task_1")
    explorer = _explorer(repo, agent_store, toolkit, _scripted(
        _action("tap", target="pay_button"),        # HIGH → 必被拦
        _action("tap", target="login_button"),      # LOW → 执行
        None))
    result = explorer.run()

    # BLOCK 的动作**没执行**（toolkit 在实现之前就拦下了）
    assert ex.tap_calls == 1
    # 循环没有死在被拦的步骤上——继续走完了剧本
    assert result.outcome.value == "COMPLETED"
    # 审计有 BLOCK 留痕（toolkit 的 audit + explorer 的步骤记录，≥1 条）
    trace = agent_store.get_trace("task_1")
    blocked = [e for e in trace if e.guard_result == "BLOCK"]
    assert blocked, "被拦动作必须留痕，静默跳过 = 销毁证据"
    assert any(e.decision.get("risk") == "HIGH" for e in blocked)


def test_critical_risk_is_also_blocked(repo, agent_store):
    ex = FakeExecutor()
    toolkit = _toolkit(repo, ex=ex, agent_db=agent_store, task_id="task_1")
    explorer = _explorer(repo, agent_store, toolkit,
                         _scripted(_action("tap", target="confirm_pay_button"),
                                   None))
    result = explorer.run()
    assert ex.tap_calls == 0
    assert result.outcome.value == "COMPLETED"


# --- 风险预判与完成语义 --------------------------------------------------------


def test_risk_prejudgment_only_low_is_auto_execute(repo):
    from explorer.policy import pre_judge, action_kwargs, ProposedAction

    low = pre_judge(ProposedAction(tool="tap", target="login_button"),
                    repository=repo, build=BUILD)
    high = pre_judge(ProposedAction(tool="tap", target="pay_button"),
                     repository=repo, build=BUILD)
    assert low.auto_execute is True
    assert low.risk == "LOW"
    assert high.auto_execute is False
    assert high.risk == "HIGH"
    # 无 target 的动作（swipe/back）走 effective_risk 的兜底 LOW
    swipe = pre_judge(ProposedAction(tool="swipe", direction="up"),
                      repository=repo, build=BUILD)
    assert swipe.auto_execute is True
    # kwargs 构造：input 带 value，back 为空
    assert action_kwargs(ProposedAction(tool="input", target="login_button",
                                        value="secret")) == {
        "target": "login_button", "value": "secret"}
    assert action_kwargs(ProposedAction(tool="back")) == {}


def test_decider_done_completes_normally_and_records_outcome(repo, agent_store):
    toolkit = _toolkit(repo, agent_db=agent_store, task_id="task_1")
    explorer = _explorer(repo, agent_store, toolkit, _scripted(None))
    result = explorer.run()
    assert result.outcome.value == "COMPLETED"
    assert result.steps == 0
    task = agent_store.get_task("task_1")
    assert task.outcome == "COMPLETED"


def test_every_step_is_audited_with_decision_and_guard_verdict(
        repo, agent_store):
    ex = FakeExecutor()
    toolkit = _toolkit(repo, ex=ex, agent_db=agent_store, task_id="task_1")
    explorer = _explorer(repo, agent_store, toolkit, _scripted(
        _action("tap", target="login_button"), None))
    result = explorer.run()

    assert result.steps == 1
    trace = agent_store.get_trace("task_1")
    steps = [e for e in trace if e.step_index == 0 and e.decision]
    assert steps, "每个循环步都要在 agent_trace 留一条"
    entry = steps[0]
    assert entry.decision["tool"] == "tap"
    assert entry.decision["target"] == "login_button"
    assert entry.decision["auto_execute"] is True
    assert entry.guard_result == "ALLOW"
    assert entry.observation["screen"] == "HomeView"
    assert entry.state in (AgentState.EXECUTING, AgentState.VERIFYING)


def test_new_state_is_verified_and_novelty_recorded(repo, agent_store):
    ex = _PageScriptExec([HOME_PAGE, PROFILE_PAGE])
    toolkit = _toolkit(repo, ex=ex, agent_db=agent_store, task_id="task_1")
    explorer = _explorer(repo, agent_store, toolkit, _scripted(
        _action("tap", target="login_button"), None))
    result = explorer.run()

    assert result.steps == 1
    trace = agent_store.get_trace("task_1")
    entry = [e for e in trace if e.step_index == 0 and e.decision][0]
    # tap 后页面从 HomeView → ProfileView：verify 观测到了新 State
    assert entry.observation["post_screen"] == "ProfileView"
    assert entry.novelty == 1.0
    # visited 收的是指纹：起点屏 + 新屏，两条不同指纹
    assert len(result.visited_fingerprints) == 2
    assert result.visited_fingerprints[0] != result.visited_fingerprints[1]


# --- F1/F12 的结构性断言（import 级 + grep 级） --------------------------------


def test_explorer_shares_the_p1_risk_function():
    """import 级断言：explorer 的风险预判就是 `executor.policy` 的本体
    （F1：不新建判定逻辑；plan 4.1 ⚠️ 注明本体在 executor/policy.py）。"""
    import executor.policy as p1_policy
    import explorer.policy as explorer_policy
    assert explorer_policy.effective_risk is p1_policy.effective_risk


def test_toolkit_is_the_only_execution_channel(repo, agent_store):
    """Explorer 构造上只收 `AgentToolkit`（F12：无第二执行通路）——
    别的对象给不进来。"""
    from explorer.agent import Explorer
    with pytest.raises(TypeError):
        Explorer(toolkit=object(), graph=_graph("HomeView"),
                 agent_db=agent_store, task_id="task_1",
                 decider=_scripted(None), repository=repo)


def test_action_outside_execution_tools_is_rejected_fail_loud(
        repo, agent_store):
    """decider 提议的必须是执行类动作——把候选类工具当动作提议是编程错误，
    fail-loud 而不是静默调用。"""
    toolkit = _toolkit(repo, agent_db=agent_store, task_id="task_1")
    explorer = _explorer(repo, agent_store, toolkit,
                         _scripted(_action("create_discovery_event"), None))
    with pytest.raises(ValueError):
        explorer.run()


def test_explorer_package_has_no_direct_device_access():
    """矩阵 #13 前半（grep/AST 级）：explorer 包内**无直接设备访问**。

    判据（AST，不吃注释/docstring 的字面亏）：
    - import 只许 `executor.policy`（`effective_risk` 纯函数本体，plan 4.1
      ⚠️ 点名的复用起点）；其余任何 `executor.*` / `session.*` / appium /
      webdriver import → 红；
    - 不得构造 `Executor(...)`（设备执行器只能由 toolkit 持有）。
    """
    allowed_executor_imports = {"executor.policy"}
    root = Path(__file__).resolve().parents[2] / "explorer"
    files = sorted(root.glob("*.py"))
    assert files, "explorer 包不存在？"
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
                if mod.startswith(("appium", "webdriver", "session.")):
                    pytest.fail(f"{path.name}: 禁止 import {mod}（设备访问"
                                f"必须经 AgentToolkit，F12/矩阵 #13）")
                if mod.startswith("executor.") and mod != "executor.policy" \
                        and mod not in allowed_executor_imports:
                    pytest.fail(
                        f"{path.name}: import {mod} 越界——explorer 只许"
                        f"复用 executor.policy 的纯函数（effective_risk）")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(("executor", "session.", "appium",
                                              "webdriver")):
                        pytest.fail(f"{path.name}: import {alias.name} 越界")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "Executor":
                pytest.fail(f"{path.name}: 直接构造 Executor（设备执行器"
                            f"只能由 AgentToolkit 持有）")


def _action(tool, **kw):
    from explorer.policy import ProposedAction
    return ProposedAction(tool=tool, **kw)
