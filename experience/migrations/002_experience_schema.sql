-- 002_experience_schema.sql — Experience 四表（设计 11 节 / P2-02，Task 1.2）。
--
-- 独立库文件 out/experience.db（plan Task 1.2 关键决策）：trace 是
-- append-only 流水库、experience 是可变状态库——按库划分让单写者边界
-- （E9）最清晰。graph 三表（screen_nodes/screen_transitions/graph_diffs）
-- 拆到 M5 落地、进 graph/migrations 自己的版本链（plan 关键决策第 2 条）。
--
-- 与设计 11 节 SQL 的差异（有意为之，实施定档）：
--   * experiences.strategy_json 存 LocatorStrategy 单对象 JSON（4.2 的
--     strategy 是单条策略不是列表——Candidate 种子来自一次恢复的一条候选）；
--   * experience_runs.screen_fingerprint（设计 screen_fingerprint 同名）；
--   * experience_runs 增加 result CHECK 约束（只有 SUCCESS/FAILURE 两值，
--     4.7 表口径由写入侧执行，DB 层兜底）。

CREATE TABLE IF NOT EXISTS experiences (
    experience_id TEXT PRIMARY KEY,
    app_id TEXT NOT NULL,
    screen_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    strategy_json TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('LLM_ACCEPTED_RECOVERY')),
    status TEXT NOT NULL CHECK (status IN
        ('CANDIDATE', 'VERIFIED', 'DEGRADED', 'REJECTED')),

    sample_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    success_rate REAL DEFAULT 0,

    validated_builds_json TEXT DEFAULT '[]',
    last_screen_fingerprint TEXT,

    seed_run_id TEXT NOT NULL,
    seed_step_id INTEGER NOT NULL CHECK (seed_step_id > 0),
    seed_recovery_review_id INTEGER NOT NULL CHECK (seed_recovery_review_id > 0),

    promoted INTEGER DEFAULT 0 CHECK (promoted IN (0, 1)),
    promoted_commit TEXT,

    created_at TEXT, updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_experiences_lookup
    ON experiences(app_id, screen_id, target_id, status);

CREATE TABLE IF NOT EXISTS experience_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experience_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    step_id INTEGER NOT NULL,
    app_build TEXT NOT NULL,
    screen_fingerprint TEXT,
    result TEXT NOT NULL CHECK (result IN ('SUCCESS', 'FAILURE')),
    guard_reason TEXT,
    effective_risk TEXT,
    uniqueness_count INTEGER,
    element_type_match INTEGER,
    latency_ms INTEGER,
    created_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_experience_runs_exp
    ON experience_runs(experience_id, created_at);

CREATE TABLE IF NOT EXISTS experience_state_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experience_id TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    reason TEXT,
    run_id TEXT,
    app_build TEXT,
    operator TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS promotion_proposals (
    proposal_id TEXT PRIMARY KEY,
    experience_id TEXT NOT NULL,
    diff_text TEXT NOT NULL,
    evidence_summary_json TEXT,
    manual_override_of_auto_policy INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN
        ('PENDING', 'APPROVED', 'REJECTED')),
    reviewer TEXT, decision_note TEXT,
    git_commit TEXT,
    created_at TEXT, decided_at TEXT
);
