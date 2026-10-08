-- 001_agent_schema.sql — agent.db 首批三表（设计 11 节；Task 1.1 / P3-01）。
--
-- 只建 test_plans / agent_tasks / agent_trace——test_candidates /
-- discovery_events / bug_candidates 随 M3/M4/M5 各自实现定型（迁移
-- 002/003/004），P2 Task 1.2「表结构随实现定型，不提前冻结」的教训。
--
-- 独立库（out/agent.db，CLI --agent-db）延续 P2「按写者边界分库」：
-- agent.db = 可变状态库（单写者 agents/storage.py）；trace.db 仍是
-- append-only 流水库，职责不混（plan 执行注意事项 #5）。
--
-- ## 与设计 §11 的差异（全部是加固，无字段删减；review_p3_task11 P3-5）
--
-- | 位置 | 设计 §11 | 本文件 |
-- |---|---|---|
-- | agent_tasks.agent_type | 可空 | `NOT NULL` |
-- | agent_tasks.goal | 可空 | `NOT NULL` |
-- | agent_tasks.constraints_json | 可空 | `NOT NULL DEFAULT '{}'` |
-- | agent_tasks.state | 可空 | `NOT NULL DEFAULT 'IDLE'` + `CHECK` 词表 |
-- | agent_trace.llm_calls_used | 可空 | `NOT NULL DEFAULT 0` |
-- | agent_trace.created_at | 可空 | `NOT NULL` |
-- | test_plans.created_at | 可空 | `NOT NULL` |
-- | test_plans.tasks_json | 可空 | `NOT NULL DEFAULT '[]'` |
-- | 索引 | 无 | `idx_agent_trace_task` / `idx_test_plans_created` |
--
-- 其余字段与设计逐字一致。`state` 的 `CHECK` 词表与 `AgentState` 枚举是同一
-- 词表的**两处字面量**，一致性由 `tests/unit/test_agent_store.py::
-- test_state_check_constraint_matches_enum` 钉住（加第 10 态时忘改这里会红）。
-- `start_time` / `end_time` / `outcome` 保持可空——「未开始 / 未结束 / 未定论」
-- 都是合法状态，用空串表示会让「没有」与「空值」混为一谈。

CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id TEXT PRIMARY KEY,
    agent_type TEXT NOT NULL,              -- planner/explorer/diagnosis
    goal TEXT NOT NULL,
    constraints_json TEXT NOT NULL DEFAULT '{}',
    state TEXT NOT NULL DEFAULT 'IDLE'
        CHECK (state IN ('IDLE', 'PLANNING', 'EXECUTING', 'OBSERVING',
                         'DECIDING', 'VERIFYING', 'ANALYZING',
                         'RECOVERING', 'ESCALATED')),
    start_time TEXT,
    end_time TEXT,
    outcome TEXT
);

CREATE TABLE IF NOT EXISTS agent_trace (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    step_index INTEGER,
    state TEXT,
    goal TEXT,
    observation_json TEXT,
    decision_json TEXT,
    guard_result TEXT,                     -- ALLOW/BLOCK（F1 审计，不静默）
    result TEXT,
    novelty REAL,
    llm_calls_used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_trace_task
    ON agent_trace (task_id, id);

CREATE TABLE IF NOT EXISTS test_plans (
    plan_id TEXT PRIMARY KEY,
    app_build TEXT,
    git_commit TEXT,
    created_at TEXT NOT NULL,
    tasks_json TEXT NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_test_plans_created
    ON test_plans (created_at DESC);
