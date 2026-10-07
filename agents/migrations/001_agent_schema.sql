-- 001_agent_schema.sql — agent.db 首批三表（设计 11 节；Task 1.1 / P3-01）。
--
-- 只建 test_plans / agent_tasks / agent_trace——test_candidates /
-- discovery_events / bug_candidates 随 M3/M4/M5 各自实现定型（迁移
-- 002/003/004），P2 Task 1.2「表结构随实现定型，不提前冻结」的教训。
--
-- 独立库（out/agent.db，CLI --agent-db）延续 P2「按写者边界分库」：
-- agent.db = 可变状态库（单写者 agents/storage.py）；trace.db 仍是
-- append-only 流水库，职责不混（plan 执行注意事项 #5）。

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
