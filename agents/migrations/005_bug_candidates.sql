-- 005_bug_candidates.sql — Bug Candidate 表（设计 §11；Task 5.1 / P3-20）。
--
-- ## 为什么是 005（偏离登记）
--
-- plan Task 5.1 写的是 `004_bug_candidates.sql`——**那个编号已被 M3 的
-- `004_review_note.sql` 占用**（Task 3.5 / P3-13 先落）。迁移脚本一旦落盘就
-- **只许向后追加**（002/003 的头部都明写这条，执行器按「已应用序号」跳过脚本）：
-- 重用一个已落盘的编号会让「已按旧版建过库」的人**永远拿不到这张表**，而
-- `_bug_from_row` 会去取它 → `no such table`。故顺延取 **005**（M4 的
-- discovery_events 顺延 006）。偏离已登记在 `docs/p3_data_audit.md`。
--
-- ## 与设计 §11 的差异（全部是加固，无字段删减；沿用 001/002 的惯例）
--
-- | 位置 | 设计 §11 | 本文件 |
-- |---|---|---|
-- | candidate_id | PRIMARY KEY | 同（**就是 bug 的 id**，不另起 `bug_id`） |
-- | title / severity / failure_type | 可空 | `NOT NULL`（审核队列里三列缺一不可读） |
-- | severity | 无词表 | `CHECK` 四档（与 `diagnosis.models.BUG_SEVERITIES` 同表） |
-- | status | `DEFAULT 'DISCOVERED'` | 同 + `NOT NULL` + `CHECK`（§8.5 全词表） |
-- | evidence_json | 可空 | `NOT NULL DEFAULT '[]'` |
-- | evidence_score | 可空 | `NOT NULL DEFAULT 0` |
-- | affected_tests_json | 可空 | `NOT NULL DEFAULT '[]'` |
-- | reproduction_count | 可空 | `NOT NULL DEFAULT 0` |
-- | created_at | 可空 | `NOT NULL` |
-- | 索引 | 无 | `idx_bug_candidates_status` / `idx_bug_candidates_dedup` |
--
-- ⚠️ **`reproducible` 保持可空**（设计也是可空的）：三态而不是两态——
-- `NULL` = 「还没试过复现」、`0` = 「试过，复现不了」、`1` = 「复现了」。
-- 用 `NOT NULL DEFAULT 0` 会把「没试过」与「试过但失败」压成同一个值，
-- 而这两件事对人工审核的意义完全不同（Task 5.4 的 `reproduce` 才会写它）。
--
-- ⚠️ **`attribution_hypothesis` 与 `review_decision` 是两列**（F10）：
-- 前者是**系统给的假设**（默认 `UNTRIAGED`），后者是**人工的决定**。合成一列
-- 会让「系统猜的」与「人判的」在库里无法区分——那正是 8.2 明文要防的
-- （「不得仅凭 ELEMENT_NOT_FOUND 判 APP_DEFECT」）。
--
-- ⚠️ **`dedup_key` 可空且带索引**（F8；Task 5.5 消费）：入库时由
-- `diagnosis/deduplicator.py` 算好，同一 key 的行合并 `affected_tests`
-- （保留首条，后到者 `status='DUPLICATE'`）。索引服务于那条合并查询。
--
-- 词表与 Python 常量是**两处字面量**（`diagnosis/models.py` 的
-- `BUG_STATUSES` / `BUG_SEVERITIES` / `ATTRIBUTIONS`），一致性由
-- `tests/unit/test_bug_store.py` 钉住（手法同 001 的
-- `test_state_check_constraint_matches_enum`：加值忘改这里会红）。

CREATE TABLE IF NOT EXISTS bug_candidates (
    candidate_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'MEDIUM'
        CHECK (severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')),
    failure_type TEXT NOT NULL,
    attribution_hypothesis TEXT NOT NULL DEFAULT 'UNTRIAGED'
        CHECK (attribution_hypothesis IN (
            'UNTRIAGED', 'APP_DEFECT', 'AUTOMATION_DEFECT',
            'ENVIRONMENT_DEFECT', 'TEST_DATA_DEFECT',
            'INFRASTRUCTURE_DEFECT')),
    evidence_json TEXT NOT NULL DEFAULT '[]',
    evidence_score INTEGER NOT NULL DEFAULT 0,
    reproducible INTEGER,                  -- 三态：NULL 未试过 / 0 复现不了 / 1 复现了
    reproduction_count INTEGER NOT NULL DEFAULT 0,
    dedup_key TEXT,
    status TEXT NOT NULL DEFAULT 'DISCOVERED'
        CHECK (status IN ('DISCOVERED', 'ANALYZING', 'CANDIDATE',
                          'HUMAN_REVIEW', 'CONFIRMED', 'REJECTED',
                          'DUPLICATE')),
    affected_tests_json TEXT NOT NULL DEFAULT '[]',
    reviewer TEXT,
    review_decision TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bug_candidates_status
    ON bug_candidates (status);

CREATE INDEX IF NOT EXISTS idx_bug_candidates_dedup
    ON bug_candidates (dedup_key);
