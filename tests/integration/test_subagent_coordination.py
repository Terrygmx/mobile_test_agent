"""Task 6.4 / P3-29：Test Manager + 三 Subagent 整合（设计 §9.1）。

判据：
1. **三 agent 各自产出经 manager 汇总**（Planning → TestPlan / Exploration →
   Exploration Trace / Diagnosis → Hypotheses）——三个都是**薄封装**（复用
   M2/M4/M5 的本体函数，不新写平行实现）；
2. **升级场景**：连续 Guard 拦截 → `should_escalate` → ESCALATED，接手包
   完整（goal / 已执行步骤 / 拦截原因）；**LLM Escalation 四类**（Security/
   Financial/Destructive/Ambiguous，§9.4 保留项）→ 直接 ESCALATED；
3. **ESCALATED 处置 = 停止并输出人工接手包，不重试不放宽**——已升级的任务
   拒绝再次运行（fail-loud，manager 不悄悄重开）。
"""
from __future__ import annotations

import pytest

from agents.base import Subagent
from agents.manager import TestManager
from agents.models import AgentState
from agents.state import EscalationReason
from agents.storage import SQLiteAgentStore
from diagnosis.analyzer import analyze  # noqa: F401 — 薄封装的本体（import 级复用证据）
from diagnosis.context import DiagnosisContext
from explorer.agent import ExplorationOutcome
from llm.budget import LLMBudget
from tests.fault_injection.fi_support import drift_repo
from planner.models import PlannerInput


# pytest 会试图收集名字以 Test 开头的类——`TestManager` 是被测的业务类，
# 不是测试用例（`agents/manager.py` 里不能加标记，测试侧豁免）。
TestManager.__test__ = False


# --- 替身 -----------------------------------------------------------------------

class _FakeGuard:
    def __init__(self, *, block_all=False):
        self.block_all = block_all
        self.checked = 0

    def check(self, ctx):
        self.checked += 1
        if self.block_all:
            from executor.guard import GuardViolation
            from executor.policy import Risk
            raise GuardViolation("测试拦截", Risk.LOW, ctx,
                                 failure_type="GUARD_BLOCKED")


class _FakeExecutor:
    def find(self, strategies):
        return None

    def tap(self, strategies):
        return {"tapped": True}

    def page_source(self):
        return "<App><Node name='screen.HomeView' visible='true'/></App>"


def _locator_for(target):
    return [{"type": "accessibility_id", "value": str(target).split(".")[-1]}]


class _TappingSubagent(Subagent):
    """测试用最小 Subagent 子类：反复发起 tap（连续拦截场景的主角）。

    目标是 `pay_button`——`drift_repo` 里登记为 HIGH，且被本测试的
    `blocked_targets` 黑名单点名：真 `Guard` 当场 BLOCK，不靠替身演「拦截」。"""

    def tap(self):
        return self.call("tap", target="HomeView.pay_button")


@pytest.fixture()
def store(tmp_path):
    return SQLiteAgentStore(tmp_path / "agent.db")


def _manager(store, tmp_path, *, guard=None) -> TestManager:
    guard = guard or _FakeGuard()
    # 真 Repository（fi_support.drift_repo：HomeView 有 screen.HomeView marker），
    # `get_current_screen` 的屏判定要真 loader——手搓替身会让探索链的观测环节
    # 变成测一个假货。
    repo = drift_repo(tmp_path)

    def factory(task_id):
        from agents.tools import AgentToolkit
        return AgentToolkit(guard=guard, executor=_FakeExecutor(),
                            locator_for=_locator_for, repository=repo,
                            agent_db=store, task_id=task_id)

    return TestManager(agent_db=store, goal="探索 Home → Profile",
                       toolkit_factory=factory)


# --- 1. 三 agent 各自产出（薄封装复用本体） ---------------------------------------


def test_planner_agent_produces_a_plan_through_the_manager(store, tmp_path):
    """PlannerAgent → `planner.plan` 本体：空改动 → 空 Plan（矩阵 #6 语义）。"""
    from experience import SQLiteExperienceStore
    from knowledge import build_knowledge
    from subagents.planner_agent import PlannerAgent

    ks = build_knowledge(experience_store=SQLiteExperienceStore(
        tmp_path / "experience.db"), trace_db=tmp_path / "trace.db",
        cases=(), app_build="1026")
    repo = drift_repo(tmp_path)
    m = _manager(store, tmp_path)
    agent = m.spawn(PlannerAgent, agent_type="planner")
    result = agent.plan(
        PlannerInput(app_build="1026", git_commit="c", changed_files=[]),
        knowledge=ks, cases=(), metadata={}, repository=repo,
        no_history_reason="trace 库不存在（测试）")
    assert result.plan.tasks == []
    assert any("changed_files 为空" in n for n in result.notes)
    # 状态机走完一圈：IDLE→PLANNING→IDLE
    task = store.get_task(agent.task_id)
    assert task.state is AgentState.IDLE and task.agent_type == "planner"


def test_diagnosis_agent_produces_hypotheses_through_the_manager(store, tmp_path):
    """DiagnosisAgent → `diagnosis.analyze` 本体：漂移现场 → 规则假设（LLM 零调用）。"""
    from subagents.diagnosis_agent import DiagnosisAgent
    from graph.models import TraceStep

    m = _manager(store, tmp_path)
    agent = m.spawn(DiagnosisAgent, agent_type="diagnosis")
    drift = TraceStep(testcase_run_id=1, step_index=1, step_type="input",
                      target_id="username_field", status="FAILED",
                      failure_type="ELEMENT_NOT_FOUND",
                      detail={"recovery_attempted": {
                          "recovery": "no_llm_engine",
                          "stages": [{"stage": "reconcile",
                                      "outcome": "DRIFT"}]}})
    ctx = DiagnosisContext(run_id="run_1", app_id="app", app_build="1026",
                           steps=(drift,))
    result = agent.analyze(ctx)          # 不给 llm/budget → 纯规则路径
    assert len(result.hypotheses) == 1 and result.rule_hit is True
    assert result.llm_calls_used == 0
    assert store.get_task(agent.task_id).state is AgentState.IDLE


def test_explorer_agent_delegates_to_the_explorer_loop(store, tmp_path):
    """ExplorerAgent → `explorer.Explorer` 本体：decider 无下一步 → COMPLETED。

    探索的任务状态由 Explorer 自己管（IDLE→EXECUTING→IDLE，历史行为）——
    薄封装不重复写态。
    """
    from subagents.explorer_agent import ExplorerAgent
    from graph.models import RuntimeGraph

    m = _manager(store, tmp_path)
    agent = m.spawn(ExplorerAgent, agent_type="explorer")
    result = agent.explore(graph=RuntimeGraph(app_id="app", app_build="1"),
                           decider=lambda obs, graph, memory: None,
                           repository=drift_repo(tmp_path / "explore"))
    assert result.outcome is ExplorationOutcome.COMPLETED and result.steps == 0
    assert store.get_task(agent.task_id).state is AgentState.IDLE


def test_each_spawned_agent_gets_its_own_budget(store, tmp_path):
    """F4 / 矩阵 #18 后半（manager 级）：spawn 出来的预算**实例**互不相同。"""
    from subagents.diagnosis_agent import DiagnosisAgent
    from subagents.explorer_agent import ExplorerAgent
    from subagents.planner_agent import PlannerAgent

    m = _manager(store, tmp_path)
    budgets = [m.spawn(cls, agent_type=t).llm_budget for cls, t in (
        (PlannerAgent, "planner"), (ExplorerAgent, "explorer"),
        (DiagnosisAgent, "diagnosis"))]
    assert all(b is not None for b in budgets)
    assert len({id(b) for b in budgets}) == 3, "预算必须一人一份"
    assert all(isinstance(b, LLMBudget) for b in budgets)


# --- 2. 升级路径 -----------------------------------------------------------------


def test_consecutive_guard_blocks_escalate_with_full_context(store, tmp_path):
    """连续 N 次 Guard 拦截 → ESCALATED，接手包三要素齐全（不重试不放宽）。"""
    from executor.guard import BlockedTarget, EnvKind, Guard
    # 真 Guard 的**规则 3**（blocked_targets 显式黑名单优先于风险等级）：
    # SANDBOX 下 HIGH 并不拦（只有 CRITICAL 拦），要造出确定的连续 BLOCK，
    # 用黑名单条目而不是拿 HIGH 元素赌一个 sandbox 例外。
    guard = Guard(EnvKind.SANDBOX,
                  blocked_targets=[BlockedTarget.parse("element:pay_button")])
    m = _manager(store, tmp_path, guard=guard)
    agent = m.spawn(_TappingSubagent, agent_type="explorer")

    limit = m.policy.escalation.guard_blocks_before_escalate
    for _ in range(limit - 1):
        agent.tap()
        assert m.assess(agent) is None, "N-1 次不升级"
    agent.tap()
    package = m.assess(agent)
    assert package is not None
    assert package["reason"] == EscalationReason.GUARD_BLOCK_STREAK.value
    assert package["goal"] == "探索 Home → Profile"
    assert package["executed_steps"], "已执行步骤要在接手包里"
    task = store.get_task(agent.task_id)
    assert task.state is AgentState.ESCALATED
    assert task.outcome == EscalationReason.GUARD_BLOCK_STREAK.value
    # 接手包整份进 agent_trace（append-only 审计）
    trace = store.get_trace(agent.task_id)
    assert any("goal" in e.decision and "reason" in e.decision
               for e in trace)


def test_llm_policy_category_escalates_directly(store, tmp_path):
    """§9.4 保留项：Security/Financial/Destructive/Ambiguous → **直接**升级
    （安全边界，不等连续计数）。"""
    from subagents.diagnosis_agent import DiagnosisAgent

    m = _manager(store, tmp_path)
    for cat in ("SECURITY", "FINANCIAL", "DESTRUCTIVE", "AMBIGUOUS"):
        fresh = m.spawn(DiagnosisAgent, agent_type="diagnosis")
        package = m.assess(fresh, llm_policy_category=cat)
        assert package is not None and \
            package["reason"] == EscalationReason.LLM_POLICY.value, cat


def test_escalated_task_refuses_further_runs(store, tmp_path):
    """**不重试不放宽**（设计 §9.1 升级处置）：已 ESCALATED 的任务再跑 →
    fail-loud（manager 不悄悄重开——人工接手是新的任务行）。"""
    from subagents.diagnosis_agent import DiagnosisAgent
    from graph.models import TraceStep

    m = _manager(store, tmp_path)
    agent = m.spawn(DiagnosisAgent, agent_type="diagnosis")
    m.escalate(agent, EscalationReason.CRITICAL_ACTION, detail="LLM 判 CRITICAL")
    drift = TraceStep(testcase_run_id=1, step_index=1, step_type="tap",
                      target_id="x", status="FAILED",
                      failure_type="ELEMENT_NOT_FOUND")
    ctx = DiagnosisContext(run_id="r", app_id="app", app_build="1",
                           steps=(drift,))
    with pytest.raises(RuntimeError, match="ESCALATED"):
        agent.analyze(ctx)


def test_escalate_is_idempotent_loud(store, tmp_path):
    """对已升级的任务再次 escalate → fail-loud（重复升级是编程错误）。"""
    from subagents.diagnosis_agent import DiagnosisAgent

    m = _manager(store, tmp_path)
    agent = m.spawn(DiagnosisAgent, agent_type="diagnosis")
    m.escalate(agent, EscalationReason.BUDGET_EXHAUSTED, detail="d")
    with pytest.raises(RuntimeError, match="ESCALATED"):
        m.escalate(agent, EscalationReason.BUDGET_EXHAUSTED, detail="d")
