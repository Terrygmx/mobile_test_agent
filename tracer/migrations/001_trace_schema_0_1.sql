-- Trace schema 0.1（P1-07，设计 14.2 逐字实现）
--
-- 本文件只负责「全新库直建」。旧 P0 库升级走 tracer/storage.py 的
-- _migrate_p0_to_0_1（需要数据搬运，SQL 表达不了），故不写 ALTER 脚本。
--
-- 命名说明：plan 里写 trace/，但本地包名 trace 与 Python stdlib 冲突
-- （已因此在 P0 改名 tracer/），本目录沿用 tracer/ 不改名。

CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL
);

-- suite 级运行记录。device_udid_hash 而非明文 udid（隐私，14.2）。
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  trace_schema_version TEXT NOT NULL,
  mta_version TEXT,
  suite TEXT,
  filter_json TEXT,
  app_bundle_id TEXT,
  app_version TEXT,
  app_build TEXT,
  app_git_commit TEXT,
  metadata_version TEXT,
  metadata_build TEXT,
  metadata_git_commit TEXT,
  metadata_mismatch INTEGER DEFAULT 0,
  metadata_mismatch_override INTEGER DEFAULT 0,
  device_type TEXT,
  device_name TEXT,
  ios_version TEXT,
  device_udid_hash TEXT,
  env_kind TEXT,
  llm_enabled INTEGER,
  llm_calls INTEGER DEFAULT 0,
  start_time TEXT NOT NULL,
  end_time TEXT,
  status TEXT,
  exit_code INTEGER
);

CREATE TABLE IF NOT EXISTS testcase_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  testcase_id TEXT NOT NULL,
  attempt INTEGER DEFAULT 1,
  status TEXT,
  failure_type TEXT,
  failure_attribution TEXT DEFAULT 'UNTRIAGED',
  non_idempotent_dispatched INTEGER DEFAULT 0,
  cleanup_status TEXT,
  start_time TEXT,
  end_time TEXT,
  duration_ms INTEGER,
  detail_json TEXT
);

CREATE TABLE IF NOT EXISTS steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  testcase_run_id INTEGER NOT NULL,
  step_index INTEGER,
  step_type TEXT,
  target_id TEXT,
  locator_origin TEXT,
  locator_strategy TEXT,
  effective_idempotency TEXT,
  effective_risk TEXT,
  status TEXT,
  failure_type TEXT,
  failure_phase TEXT,
  failure_attribution TEXT DEFAULT 'UNTRIAGED',
  latency_ms INTEGER,
  detail_json TEXT,
  screenshot_path TEXT,
  ui_tree_path TEXT,
  step_schema_version TEXT
);

-- 14.2：screen/app_build/source_commit/expected_target/candidate_*/result
-- 是 V2 Experience Store 的原始数据，P1 必须完整记录。
CREATE TABLE IF NOT EXISTS recoveries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  step_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  expected_target TEXT,
  candidate_target TEXT,
  candidate_origin TEXT,
  candidate_type TEXT,
  scope TEXT,
  screen TEXT,
  app_build TEXT,
  source_commit TEXT,
  confidence REAL,
  uniqueness_count INTEGER,
  type_match INTEGER,
  screen_match INTEGER,
  effective_risk TEXT,
  accepted INTEGER,
  result TEXT,
  reject_reason TEXT,
  llm_model TEXT,
  llm_tokens_in INTEGER,
  llm_tokens_out INTEGER,
  latency_ms INTEGER
);

CREATE TABLE IF NOT EXISTS recovery_reviews (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  recovery_id INTEGER NOT NULL,
  review_status TEXT DEFAULT 'PENDING',
  reviewer TEXT,
  reviewed_at TEXT,
  note TEXT
);

CREATE TABLE IF NOT EXISTS infra_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT,
  testcase_run_id INTEGER,
  event_type TEXT,
  step_index INTEGER,
  non_idempotent_dispatched INTEGER DEFAULT 0,
  action_taken TEXT,
  timestamp TEXT NOT NULL,
  detail_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_tc_runs_run ON testcase_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_steps_tc_run ON steps(testcase_run_id);
CREATE INDEX IF NOT EXISTS idx_recoveries_step ON recoveries(step_id);
CREATE INDEX IF NOT EXISTS idx_reviews_recovery ON recovery_reviews(recovery_id);
CREATE INDEX IF NOT EXISTS idx_infra_run ON infra_events(run_id);