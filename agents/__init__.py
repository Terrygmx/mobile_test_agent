"""agents — Phase 3 Agent 主线（Test Planner / Generator / Explorer /
Diagnosis / Subagent；设计 4/9/10/11 节）。

包级纪律（P3 设计硬约束 F2/F12 的落点，本包是唯一通路）：
  - 资产权限分级在 models.TOOL_ASSET_TIER——PRODUCTION / CRITICAL 工具
    在构造上不存在（F2），被禁名单有 CI 门禁测试钉死；
  - 任何会碰 App 的动作最终都经 agents/tools.py 的 AgentToolkit →
    Executor+Guard（F1/F12），不存在绕过路径；
  - agent.db 独立库（out/agent.db），单写者 SQLiteAgentStore（E9 惯例）。
"""
from agents.models import (
    TOOL_ASSET_TIER,
    AgentState,
    AgentTask,
    AgentTaskState,
    AgentTraceEntry,
    AssetTier,
)
from agents.storage import (
    AGENT_MIGRATIONS_DIR,
    AGENT_SCHEMA_VERSION,
    SQLiteAgentStore,
)

__all__ = [
    "AssetTier", "TOOL_ASSET_TIER",
    "AgentState", "AgentTaskState", "AgentTask", "AgentTraceEntry",
    "SQLiteAgentStore", "AGENT_MIGRATIONS_DIR", "AGENT_SCHEMA_VERSION",
]
