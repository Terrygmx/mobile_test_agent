"""agents — Phase 3 Agent 主线（Test Planner / Generator / Explorer /
Diagnosis / Subagent；设计 4/9/10/11 节）。

包级纪律（P3 设计硬约束 F2/F12 的落点，本包是唯一通路）：
  - 资产权限分级在 `models.TOOL_ASSET_TIER`——PRODUCTION / CRITICAL 工具
    在构造上不存在（F2），被禁名单由 CI 门禁测试钉死；
  - **任何会碰 App 的动作都经 `tools.AgentToolkit` → Executor+Guard**
    （F1/F12，唯一分发层）：执行类工具先过 `Guard.check`，BLOCK 写
    `agent_trace`（`guard_result=BLOCK`）且**无法留痕时报错而非静默丢弃**；
  - agent.db 独立库（`DEFAULT_AGENT_DB` = `out/agent.db`），单写者
    `SQLiteAgentStore`（E9：进程锁 + `BEGIN IMMEDIATE`，实现单点在
    `source/sqlite_tx.write_tx`）；
  - 环境与预算约束在 `policy_config`（F7 的启动期校验由
    `cli.main._check_autonomous_env` 消费）。

⚠️ **唯一通路是「Agent 侧」的保证**：`tools.AgentToolkit` 之后，任何会执行到
App 上的动作都经它。而 P1 的 `runner/runner.py` 是**用例执行**通路（不经本包）
——两条通路共用同一个 `Guard` 与 `Executor`，不是两套安全实现。
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
from agents.tools import (
    ALLOWED_TOOLS,
    BANNED_TOOLS,
    GUARDED_TOOLS,
    NOT_WIRED,
    TOOL_METHODS,
    AgentToolkit,
    ToolDependencyError,
    ToolError,
    ToolNotWired,
    ToolResult,
    ToolViolation,
    ToolkitAuditError,
)

__all__ = [
    "AssetTier", "TOOL_ASSET_TIER",
    "AgentState", "AgentTaskState", "AgentTask", "AgentTraceEntry",
    "SQLiteAgentStore", "AGENT_MIGRATIONS_DIR", "AGENT_SCHEMA_VERSION",
    "DEFAULT_AGENT_DB",
    # policy（Task 1.2 / P3-02）
    "DEFAULT_POLICY_PATH", "PolicyConfig", "PolicyConfigError", "load_policy",
    # tools（Task 1.3 / P3-03）
    "ALLOWED_TOOLS", "BANNED_TOOLS", "GUARDED_TOOLS", "NOT_WIRED",
    "TOOL_METHODS", "AgentToolkit", "ToolResult",
    "ToolError", "ToolViolation", "ToolNotWired", "ToolDependencyError",
    "ToolkitAuditError",
]
