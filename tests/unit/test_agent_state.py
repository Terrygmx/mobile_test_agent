"""Task 6.1 / P3-26：Agent 状态机纯函数 + ESCALATED 语义（设计 §9.2 / F13）。

判据：
1. **全转换表覆盖**——`transition(state, event)` 对每个 (态, 事件) 组合都有
   定义的行为：合法的给出后继、非法的 fail-loud（`IllegalTransition`）；
2. **ESCALATED 不是错误态**——它是「系统认为需要人介入」的正常退出路径，
   必附上下文（goal、已执行步骤、拦截原因）供人工接手；
3. **四种触发逐一命中**（设计 §9.2 原文）：连续 N 次 Guard 拦截 / LLM 判定
   CRITICAL 操作 / budget 耗尽但目标未达成 / 连续 LLM_INVALID_OUTPUT；
   外加 §9.4 保留的 LLM Escalation 四类（Security/Financial/Destructive/
   Ambiguous）→ 直接 ESCALATED；
4. **落库端到端**：`update_task_state` 强制走转换表，非法转换 fail-loud；
   既有三条调用路径（Explorer 的 IDLE→EXECUTING→IDLE、cmd_explore 错误路径的
   IDLE→ESCALATED）零改动即合法。
"""
from __future__ import annotations

import pytest

from agents.models import AgentState
from agents.state import (AGENT_TRANSITIONS, AGENT_STATES, AgentEvent,
                          EscalationReason, IllegalTransition,
                          escalation_context, successors, should_escalate,
                          transition)


# --- 1. 转换表：结构对账 ---------------------------------------------------------


def test_every_state_has_a_row_and_escalated_has_no_out_edges():
    """九态每态都有行；**ESCALATED 是终态**——无出边（再推进 fail-loud）。"""
    assert set(AGENT_TRANSITIONS) == set(AgentState)
    assert AGENT_STATES == frozenset(AgentState)
    assert successors(AgentState.ESCALATED) == frozenset(), \
        "ESCALATED 是「需要人介入」的正常退出路径——任务到此为止，不可再推进"


def test_the_main_loop_cycle_is_legal():
    """设计 §9.2 主循环：EXECUTING→OBSERVING→DECIDING→EXECUTING（环）。

    ⚠️ 口径登记：设计原文是线性草图 `IDLE→PLANNING→EXECUTING→OBSERVING→
    DECIDING→VERIFYING`，但主循环天然是环（每步 Observe→Decide→Execute）；
    VERIFYING 在**任务收尾**与**单步验证**两个位置出现，故 DECIDING 与
    VERIFYING 都可回 EXECUTING。
    """
    assert transition(AgentState.IDLE, AgentEvent.EXECUTE) is AgentState.EXECUTING
    assert transition(AgentState.EXECUTING, AgentEvent.OBSERVE) is AgentState.OBSERVING
    assert transition(AgentState.OBSERVING, AgentEvent.DECIDE) is AgentState.DECIDING
    assert transition(AgentState.DECIDING, AgentEvent.EXECUTE) is AgentState.EXECUTING


def test_planner_and_exception_branches_are_legal():
    """PLANNING 支线 + 异常支线（ANALYZING→RECOVERING→VERIFYING）。"""
    assert transition(AgentState.IDLE, AgentEvent.PLAN) is AgentState.PLANNING
    assert transition(AgentState.PLANNING, AgentEvent.EXECUTE) is AgentState.EXECUTING
    assert transition(AgentState.PLANNING, AgentEvent.COMPLETE) is AgentState.IDLE
    # 异常支线
    assert transition(AgentState.DECIDING, AgentEvent.ANALYZE) is AgentState.ANALYZING
    assert transition(AgentState.ANALYZING, AgentEvent.RECOVER) is AgentState.RECOVERING
    assert transition(AgentState.RECOVERING, AgentEvent.VERIFY) is AgentState.VERIFYING
    assert transition(AgentState.ANALYZING, AgentEvent.VERIFY) is AgentState.VERIFYING


def test_complete_goes_idle_from_every_working_state():
    """`COMPLETE→IDLE` = 「任务结束、outcome 落库」。

    `Explorer.run()` 正常返回时写的就是 IDLE（历史行为），这条转换让它是
    **合法**的——Explorer 零改动。
    """
    for state in (AgentState.PLANNING, AgentState.EXECUTING,
                  AgentState.VERIFYING, AgentState.ANALYZING,
                  AgentState.RECOVERING):
        assert transition(state, AgentEvent.COMPLETE) is AgentState.IDLE, state


def test_escalate_is_reachable_from_every_non_terminal_state():
    """`ESCALATE→ESCALATED` 从**任何非终态**——严重异常不挑时机。

    `cmd_explore` 的两条错误路径（设备起不来 / App 起不来）在任务还是 IDLE 时
    就写 ESCALATED——这条转换让它们**零改动**即合法。
    """
    for state in AgentState:
        if state is AgentState.ESCALATED:
            continue
        assert transition(state, AgentEvent.ESCALATE) is AgentState.ESCALATED, state


def test_cross_state_jumps_are_illegal():
    """反钉子：跨支线跳变 fail-loud——转换表的价值就在拦住这些。"""
    for state, event in ((AgentState.ANALYZING, AgentEvent.PLAN),
                         (AgentState.RECOVERING, AgentEvent.PLAN),
                         (AgentState.IDLE, AgentEvent.OBSERVE),
                         (AgentState.IDLE, AgentEvent.DECIDE),
                         (AgentState.ESCALATED, AgentEvent.EXECUTE),
                         (AgentState.ESCALATED, AgentEvent.COMPLETE)):
        with pytest.raises(IllegalTransition):
            transition(state, event)


def test_transition_error_message_names_state_event_and_legal_events():
    """报错要能指导修正：当前态、事件、合法事件集都在消息里。"""
    with pytest.raises(IllegalTransition) as e:
        transition(AgentState.IDLE, AgentEvent.OBSERVE)
    msg = str(e.value)
    assert "IDLE" in msg and "OBSERVE" in msg
    assert "PLAN" in msg and "ESCALATE" in msg      # 合法事件集


# --- 2. should_escalate：四触发 + §9.4 四类，逐一命中 ----------------------------


def _esc(**kw):
    """全 False 的基线（不触发），逐参数覆盖。"""
    base = dict(guard_block_streak=0, llm_invalid_streak=0,
                budget_exceeded=False, goal_achieved=False,
                guard_limit=3, llm_invalid_limit=2)
    base.update(kw)
    return should_escalate(**base)


def test_no_trigger_means_none():
    assert _esc() is None
    assert _esc(budget_exceeded=True, goal_achieved=True) is None, \
        "预算耗尽但目标**已**达成 = 正常收工，不升级"


def test_guard_block_streak_trigger():
    assert _esc(guard_block_streak=3) is EscalationReason.GUARD_BLOCK_STREAK
    assert _esc(guard_block_streak=4) is EscalationReason.GUARD_BLOCK_STREAK
    assert _esc(guard_block_streak=2) is None            # N 来自 policy（3）
    # streak 在两次**非拦截**之间清零——连续性由调用方维护，这里只判阈值


def test_critical_action_trigger():
    """LLM 判定 CRITICAL 操作 → 立即升级（不用等连续计数）。"""
    assert _esc(proposed_risk="CRITICAL") is EscalationReason.CRITICAL_ACTION
    assert _esc(proposed_risk="HIGH") is None, \
        "HIGH 在探索侧由 LOW-only 闸拦（F1），不构成升级"


def test_llm_invalid_output_streak_trigger():
    assert _esc(llm_invalid_streak=2) is EscalationReason.LLM_INVALID_STREAK
    assert _esc(llm_invalid_streak=1) is None


def test_budget_exhausted_with_goal_pending_trigger():
    assert _esc(budget_exceeded=True) is EscalationReason.BUDGET_EXHAUSTED


def test_llm_policy_categories_escalate_directly():
    """设计 §9.4 保留项：Security/Financial/Destructive/Ambiguous → 直接升级。

    这是**安全边界不是性能优化**（设计原文）——四类逐一命中，别的不算。
    """
    for cat in ("SECURITY", "FINANCIAL", "DESTRUCTIVE", "AMBIGUOUS"):
        assert _esc(llm_policy_category=cat) is EscalationReason.LLM_POLICY, cat
    assert _esc(llm_policy_category="PERFORMANCE") is None


def test_trigger_priority_is_pinned():
    """多触发同时命中时按**安全优先**取最重的：LLM_POLICY > CRITICAL_ACTION >
    GUARD_BLOCK_STREAK > LLM_INVALID_STREAK > BUDGET_EXHAUSTED。

    优先级不钉死的话，调用方拿到哪个都是「碰运气」——而升级理由决定人工
    接手时先看什么。
    """
    all_on = dict(guard_block_streak=9, llm_invalid_streak=9,
                  budget_exceeded=True, goal_achieved=False,
                  proposed_risk="CRITICAL",
                  llm_policy_category="SECURITY",
                  guard_limit=3, llm_invalid_limit=2)
    assert should_escalate(**all_on) is EscalationReason.LLM_POLICY
    del all_on["llm_policy_category"]
    assert should_escalate(**all_on) is EscalationReason.CRITICAL_ACTION
    all_on["proposed_risk"] = "LOW"
    assert should_escalate(**all_on) is EscalationReason.GUARD_BLOCK_STREAK
    all_on["guard_block_streak"] = 0
    assert should_escalate(**all_on) is EscalationReason.LLM_INVALID_STREAK
    all_on["llm_invalid_streak"] = 0
    assert should_escalate(**all_on) is EscalationReason.BUDGET_EXHAUSTED


# --- 3. ESCALATED 不是错误态：接手包 -------------------------------------------


def test_escalation_context_carries_the_handover_package():
    """接手包三要素（设计 §9.2）：当前 goal、已执行步骤、拦截原因。"""
    ctx = escalation_context(
        goal="explore Home → Profile",
        executed_steps=({"tool": "tap", "target": "HomeView.go_profile"},),
        reason=EscalationReason.GUARD_BLOCK_STREAK,
        detail="连续 3 次被 Guard 拦截")
    assert ctx["goal"] == "explore Home → Profile"
    assert len(ctx["executed_steps"]) == 1
    assert ctx["reason"] == "GUARD_BLOCK_STREAK"
    assert "Guard" in ctx["detail"]


def test_escalation_context_rejects_an_empty_reason():
    """没有升级理由的 ESCALATED 是**错误态**——构造上拒绝（接手包的意义就在
    让人快速接手，没理由的升级让人无从下手）。"""
    with pytest.raises(ValueError):
        escalation_context(goal="g", executed_steps=(), reason=None)


# --- 4. 落库端到端：update_task_state 走转换表 ---------------------------------


def test_update_task_state_enforces_the_transition_table(tmp_path):
    from agents.storage import SQLiteAgentStore

    store = SQLiteAgentStore(tmp_path / "agent.db")
    store.create_task("t1", agent_type="explorer", goal="g")
    # Explorer 的真实路径：IDLE→EXECUTING→IDLE —— 零改动合法
    store.update_task_state("t1", AgentState.EXECUTING)
    store.update_task_state("t1", AgentState.IDLE, outcome="COMPLETED")
    # cmd_explore 错误路径：IDLE→ESCALATED —— 零改动合法
    store.create_task("t2", agent_type="explorer", goal="g")
    store.update_task_state("t2", "ESCALATED", outcome="DEVICE_UNAVAILABLE")
    # 非法转换 fail-loud
    store.create_task("t3", agent_type="planner", goal="g")
    store.update_task_state("t3", AgentState.PLANNING)
    with pytest.raises(ValueError, match="IDLE"):
        store.update_task_state("t3", AgentState.ANALYZING)
    # 终态不可再推进
    store.create_task("t4", agent_type="explorer", goal="g")
    store.update_task_state("t4", AgentState.ESCALATED)
    with pytest.raises(ValueError, match="ESCALATED"):
        store.update_task_state("t4", AgentState.EXECUTING)
    assert store.get_task("t4").state is AgentState.ESCALATED


def test_policy_carries_the_llm_invalid_threshold():
    """LLM invalid 阈值入 policy（与 guard_blocks 同段）——校准只改一处。"""
    from agents.policy_config import load_policy

    policy = load_policy()
    assert policy.escalation.guard_blocks_before_escalate == 3
    assert policy.escalation.llm_invalid_before_escalate == 2
