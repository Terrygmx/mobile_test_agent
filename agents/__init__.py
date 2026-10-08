"""agents — Phase 3 Agent 主线（Test Planner / Generator / Explorer /
Diagnosis / Subagent；设计 4/9/10/11 节）。

包级纪律（P3 设计硬约束 F2/F12 的落点，本包是唯一通路）：
  - 资产权限分级在 `models.TOOL_ASSET_TIER`——PRODUCTION / CRITICAL 工具
    在构造上不存在（F2），被禁名单由 CI 门禁测试钉死；
  - agent.db 独立库（`DEFAULT_AGENT_DB` = `out/agent.db`），单写者
    `SQLiteAgentStore`（E9：进程锁 + `BEGIN IMMEDIATE`，实现单点在
    `source/sqlite_tx.write_tx`）。

⚠️ **尚未落地的两条**（review_p3_task11 P3-1：docstring 不得描述「P3 完成
态」）——都归 **Task 1.3**，在那之前「本包是唯一通路」是**目标**而非既成事实：
  - `agents/tools.py` 的 `AgentToolkit`（→ Executor+Guard，F1/F12）尚不存在，
    所以「任何会碰 App 的动作都经它、不存在绕过路径」还没有执行体；
  - 禁用名单的 CI 门禁测试 `tests/unit/test_tool_allowlist_gate.py` 尚不存在，
    当前由 `tests/unit/test_agent_models.py` 的断言临时兜住（同一批断言）。
"""
from agents.models import (
    TOOL_ASSET_TIER,
    AgentState,
    AgentTask,
    AgentTaskState,
    AgentTraceEntry,
    AssetTier,
)
from agents.policy_config import (
    DEFAULT_POLICY_PATH,
    PolicyConfig,
    PolicyConfigError,
    load_policy,
)
from agents.storage import (
    AGENT_MIGRATIONS_DIR,
    AGENT_SCHEMA_VERSION,
    DEFAULT_AGENT_DB,
    SQLiteAgentStore,
)

__all__ = [
    "AssetTier", "TOOL_ASSET_TIER",
    "AgentState", "AgentTaskState", "AgentTask", "AgentTraceEntry",
    "SQLiteAgentStore", "AGENT_MIGRATIONS_DIR", "AGENT_SCHEMA_VERSION",
    "DEFAULT_AGENT_DB",
    # policy（Task 1.2 / P3-02）
    "DEFAULT_POLICY_PATH", "PolicyConfig", "PolicyConfigError", "load_policy",
]
