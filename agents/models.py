"""models.py — Agent 数据模型与资产权限分级（设计 §4 / §9.2 / §11；Task 1.1 / P3-01）。

**资产权限分级（设计 §4，F2 的落点）**：PRODUCTION / CRITICAL 工具在
构造上不存在——本模块的 `TOOL_ASSET_TIER` 是工具全集的**单一真值源**，
Task 1.3 的 `ALLOWED_TOOLS` 从这里派生（`frozenset(TOOL_ASSET_TIER)`），
两处永不漂移。

禁用名单（`delete_testcase` 等 8 项）当前由 `tests/unit/test_agent_models.py`
的断言钉住；**CI 门禁测试 `tests/unit/test_tool_allowlist_gate.py` 归 Task 1.3**
（review_p3_task11 P3-1：此处早先写成「已由 CI 门禁钉死」，而那个文件当时还
不存在——docstring 不得描述「P3 完成态」）。

AgentState 九态（设计 §9.2）：本任务只定型词表与存储；合法转换表
M6（Task 6.1）落地——在那之前 store 不做转换校验，只做枚举校验。
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AssetTier", "TOOL_ASSET_TIER",
    "AgentState", "AgentTaskState", "AgentTask", "AgentTraceEntry",
]


class AssetTier(str, Enum):
    """设计 §4 四级资产权限。PRODUCTION / CRITICAL 仅作为枚举值存在
    （语义文档），不出现在任何 Agent 可调用工具的归属里（F2）。"""

    READ_ONLY = "READ_ONLY"
    CANDIDATE = "CANDIDATE"
    PRODUCTION = "PRODUCTION"
    CRITICAL = "CRITICAL"


class _Strict(BaseModel):
    """E 系纪律的载体：多余字段拒绝（schema 漂移要 fail-loud，不是吞掉）。"""

    model_config = ConfigDict(extra="forbid")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentState(str, Enum):
    """设计 §9.2 的 Agent 状态机九态。ESCALATED 不是错误态——必附上下文
    供人工接手（M6 Task 6.1 的语义，词表先定型）。"""

    IDLE = "IDLE"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    DECIDING = "DECIDING"
    VERIFYING = "VERIFYING"
    ANALYZING = "ANALYZING"
    RECOVERING = "RECOVERING"
    ESCALATED = "ESCALATED"


# plan Task 1.1 的命名别名——**真别名**（同一个 Enum 对象），防两套状态词表
# 漂移。早先写成「普通类 + 9 个别名属性」，名字像 Enum 却不是 Enum：`issubclass(
# AgentTaskState, Enum)` 为 False、不可迭代、`AgentTaskState("IDLE")` 抛
# TypeError——于是很容易被写成类型标注 `state: AgentTaskState`（review_p3_task11
# P3-4）。别名语义与 `AgentState` 完全相同，一行即可。
AgentTaskState = AgentState


# 工具 → 资产归属（设计 §4 + §10.1 全集；plan Task 1.1 的归属定档）。
# 键集即 Task 1.3 的 ALLOWED_TOOLS（单一真值源）；值域只有 READ_ONLY /
# CANDIDATE——PRODUCTION / CRITICAL 工具构造上不存在（F2）。
#
# 归属原则：资产的读写视角，不是 App 副作用的视角——tap/input 等执行
# 类工具不创建/修改任何资产（App 侧风险由 F1 Guard 拦），故 READ_ONLY；
# run_candidate_test 写候选资产的 dry_run_status，故 CANDIDATE。
TOOL_ASSET_TIER: dict[str, AssetTier] = {
    # 只读知识
    "get_current_screen": AssetTier.READ_ONLY,
    "get_ui_tree": AssetTier.READ_ONLY,
    "get_relevant_source": AssetTier.READ_ONLY,
    "get_relevant_experience": AssetTier.READ_ONLY,
    "get_graph_neighbors": AssetTier.READ_ONLY,
    "get_logs": AssetTier.READ_ONLY,
    "get_network_summary": AssetTier.READ_ONLY,
    "get_crash_info": AssetTier.READ_ONLY,
    # 执行（App 侧风险仍经 Guard——F1）
    "tap": AssetTier.READ_ONLY,
    "input": AssetTier.READ_ONLY,
    "swipe": AssetTier.READ_ONLY,
    "back": AssetTier.READ_ONLY,
    "wait": AssetTier.READ_ONLY,
    "assert_exists": AssetTier.READ_ONLY,
    "assert_text": AssetTier.READ_ONLY,
    "screenshot": AssetTier.READ_ONLY,
    "run_testcase": AssetTier.READ_ONLY,
    # 候选资产产生/推进
    "run_candidate_test": AssetTier.CANDIDATE,   # 写 dry_run_status
    "create_test_candidate": AssetTier.CANDIDATE,
    "create_bug_candidate": AssetTier.CANDIDATE,
    "create_discovery_event": AssetTier.CANDIDATE,
    "create_promotion_proposal": AssetTier.CANDIDATE,
}


class AgentTask(_Strict):
    """设计 §11 agent_tasks 行的模型投影。"""

    task_id: str
    agent_type: str                       # planner/explorer/diagnosis
    goal: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    state: AgentState = AgentState.IDLE
    start_time: datetime | None = None
    end_time: datetime | None = None
    outcome: str | None = None


class AgentTraceEntry(_Strict):
    """设计 §11 agent_trace 行的模型投影（**追加式**：只有 insert，无
    update——每步留痕是审计的根基，改历史 = 销毁证据）。"""

    task_id: str
    step_index: int | None = None
    state: AgentState | None = None
    goal: str | None = None
    observation: dict[str, Any] = Field(default_factory=dict)
    decision: dict[str, Any] = Field(default_factory=dict)
    guard_result: str | None = None       # ALLOW/BLOCK
    result: str | None = None
    novelty: float | None = None
    llm_calls_used: int = 0
    created_at: datetime = Field(default_factory=_utcnow)
