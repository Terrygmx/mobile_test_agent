"""Task 1.1 / P3-01：Agent 数据模型与资产权限分级（设计 §4 / §11）。

红线断言（与 Task 1.3 的 CI 门禁 test_tool_allowlist_gate.py 呼应）：
  - TOOL_ASSET_TIER 不含任何 PRODUCTION / CRITICAL 工具——Agent 不持有
    直接修改正式资产的工具（F2），被禁工具在构造上不存在；
  - tier 表的键集 = 设计 10.1 ALLOWED_TOOLS 全集（单一真值源：tools.py
    的 ALLOWED_TOOLS 从这里派生，两处永不漂移）。
"""
from __future__ import annotations

import pytest

from agents.models import (
    TOOL_ASSET_TIER,
    AgentState,
    AgentTask,
    AgentTaskState,
    AgentTraceEntry,
    AssetTier,
)

# 设计 10.1 工具清单全集（F2 的落地）
DESIGN_10_1_TOOLS = {
    # 只读
    "get_current_screen", "get_ui_tree", "get_relevant_source",
    "get_relevant_experience", "get_graph_neighbors", "get_logs",
    "get_network_summary", "get_crash_info",
    # 执行（仍经 Guard）
    "tap", "input", "swipe", "back", "wait",
    "assert_exists", "assert_text", "screenshot",
    "run_testcase", "run_candidate_test",
    # 创建候选资产（Candidate Tier）
    "create_test_candidate", "create_bug_candidate",
    "create_discovery_event", "create_promotion_proposal",
}

# 被禁工具（F2 原文：永不出现在任何 Agent 的工具表里）
FORBIDDEN_TOOLS = {
    "delete_testcase", "modify_expectation", "modify_verified_experience",
    "modify_repository_directly", "production_api_call", "real_payment",
    "arbitrary_shell", "arbitrary_python",
}


def test_asset_tier_values():
    assert {t.value for t in AssetTier} == {
        "READ_ONLY", "CANDIDATE", "PRODUCTION", "CRITICAL"}


def test_tool_asset_tier_covers_full_allowlist():
    assert set(TOOL_ASSET_TIER) == DESIGN_10_1_TOOLS, \
        "tier 表键集 = 设计 10.1 全集（Task 1.3 的 ALLOWED_TOOLS 由此派生）"


def test_tool_asset_tier_has_no_production_or_critical():
    """红线：PRODUCTION / CRITICAL 工具在构造上不存在（F2）。"""
    assert all(t is not AssetTier.PRODUCTION and t is not AssetTier.CRITICAL
               for t in TOOL_ASSET_TIER.values())


@pytest.mark.parametrize("tool", sorted(FORBIDDEN_TOOLS))
def test_forbidden_tool_absent(tool):
    assert tool not in TOOL_ASSET_TIER


def test_tool_asset_tier_plan_assignments():
    """plan Task 1.1 的归属定档：执行/读取类 READ_ONLY，候选产生类
    CANDIDATE（run_candidate_test 写 dry_run_status——候选资产状态）。"""
    assert TOOL_ASSET_TIER["run_candidate_test"] is AssetTier.CANDIDATE
    assert TOOL_ASSET_TIER["run_testcase"] is AssetTier.READ_ONLY
    for tool in ("tap", "input", "swipe", "back", "wait",
                 "get_current_screen", "get_ui_tree", "assert_exists",
                 "assert_text", "screenshot"):
        assert TOOL_ASSET_TIER[tool] is AssetTier.READ_ONLY
    for tool in ("create_test_candidate", "create_bug_candidate",
                 "create_discovery_event", "create_promotion_proposal"):
        assert TOOL_ASSET_TIER[tool] is AssetTier.CANDIDATE


def test_agent_states_complete():
    """设计 9.2 的九态（M6 才强制转换表；本任务先定型词表）。"""
    assert {s.value for s in AgentState} == {
        "IDLE", "PLANNING", "EXECUTING", "OBSERVING", "DECIDING",
        "VERIFYING", "ANALYZING", "RECOVERING", "ESCALATED"}
    # AgentTaskState 是同域别名常量（plan Task 1.1 命名），防两套词表
    assert AgentTaskState.IDLE is AgentState.IDLE


def test_agent_task_defaults_and_strictness():
    task = AgentTask(task_id="task_1", agent_type="explorer", goal="g")
    assert task.state is AgentState.IDLE
    assert task.constraints == {}
    with pytest.raises(Exception):  # extra=forbid：schema 漂移 fail-loud
        AgentTask(task_id="t", agent_type="explorer", goal="g", bogus=1)


def test_agent_trace_entry_shape():
    e = AgentTraceEntry(task_id="task_1", step_index=0,
                        state=AgentState.OBSERVING,
                        observation={"screen": "HomeView"})
    assert e.decision == {} and e.guard_result is None
    assert e.novelty is None and e.llm_calls_used == 0
