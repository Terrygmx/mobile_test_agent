-- 002_test_candidates.sql — Candidate Test 表（设计 §11；Task 3.4 / P3-12）。
--
-- 设计 §11 只给了 `test_candidates` 一张表——那是「**入库的候选**」这一种表达。
-- 但矩阵 #7（Schema/lint 不过 → 拒绝入库，fail-loud）与 #8（Test Fingerprint
-- 命中 → SKIP 且留痕）都要求「**被拒的**东西也留下可查的痕迹」：不入库 + 无声
-- 消失 = 人工看不见「生成器到底试过什么、为什么没给我」。故本迁移建**两张表**：
--
--   1. `test_candidates`        —— 入库的候选（F6 人工审核队列的输入）
--   2. `test_candidate_rejections` —— 生成期被拒/SKIP 的留痕（审计，不是审核队列）
--
-- 分开而不是给 `test_candidates` 加 `review_status='SKIPPED'`：后者会把「生成器
-- 自己的判据」混进 F6 的**人工**审核状态机（PENDING → ACCEPT/REJECT），Task 3.5
-- 的 `candidate review list` 就得额外过滤，而设计 §11 的状态语义被撑开。
-- 表名是 `rejections` 而非 `skips`：它同时装 SKIP（DUPLICATE）与 REJECT
-- （SCHEMA_INVALID / SEMANTIC_REJECTED）——叫 skips 是名实不符。
--
-- ## 与设计 §11 的差异（全部是加固，无字段删减；沿用 001 的惯例）
--
-- | 位置 | 设计 §11 | 本文件 |
-- |---|---|---|
-- | test_candidates.generation_evidence_json | 可空 | `NOT NULL DEFAULT '{}'` |
-- | test_candidates.dry_run_status | 可空 | `NOT NULL DEFAULT 'PENDING'` + `CHECK` 词表 |
-- | test_candidates.review_status | `DEFAULT 'PENDING'` | 同上 + `NOT NULL` + `CHECK` 词表 |
-- | test_candidates.created_at | 可空 | `NOT NULL` |
-- | 索引 | 无 | `idx_test_candidates_review` / `idx_test_candidates_fp` |
--
-- 词表与 Python 常量是**两处字面量**（`candidates/test_case.py` 的
-- `DRY_RUN_STATUSES` / `REVIEW_STATUSES` / `REJECTION_REASONS`），一致性由
-- `tests/unit/test_candidate_store.py` 钉住（手法同 001 的
-- `test_state_check_constraint_matches_enum`：加值忘改这里会红）。
--
-- ⚠️ `fingerprint` **可空**：设计 §11 未标 NOT NULL，且留痕表里 SCHEMA_INVALID
-- 的候选压根算不出指纹（步骤都解析不了）。入库的候选则**必有**指纹——那条由
-- `save_candidate` 的 Python 侧闸门保证，不写进 DDL（DDL 里 `NOT NULL` 会让
-- 「留痕」与「入库」两张表用同一份列定义变得不可能，而它们的语义本就不同）。
--
-- `duplicate_of` 是矩阵 #8 的硬要求（`docs/handover.md` §9 开工前置 #1）：
-- SKIP 必须留下「**与哪条既有用例/候选撞了指纹**」，否则人工看不见被跳过的是
-- 哪一条——而 `precondition` 不进指纹（Task 3.3 拍板）意味着真语料里**已有**
-- 一对语义不同的同指纹用例会被跳过，这个字段就是那笔代价的兑付方式。

CREATE TABLE IF NOT EXISTS test_candidates (
    candidate_id TEXT PRIMARY KEY,
    testcase_yaml TEXT NOT NULL,
    generated_by TEXT,
    generation_evidence_json TEXT NOT NULL DEFAULT '{}',
    fingerprint TEXT,                      -- F8：Test Fingerprint（Task 3.3）
    dry_run_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (dry_run_status IN ('PENDING', 'PASS', 'FAIL')),
    review_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (review_status IN ('PENDING', 'ACCEPT', 'REJECT')),
    reviewer TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL
);

-- F6 审核队列的主查询：按状态取、新在前
CREATE INDEX IF NOT EXISTS idx_test_candidates_review
    ON test_candidates (review_status, created_at DESC);

-- F8 去重按指纹查（生成器每次都要拿「已见过的指纹」全集）
CREATE INDEX IF NOT EXISTS idx_test_candidates_fp
    ON test_candidates (fingerprint);

CREATE TABLE IF NOT EXISTS test_candidate_rejections (
    rejection_id TEXT PRIMARY KEY,
    candidate_id TEXT,                     -- 可空：SCHEMA_INVALID 时可能没有合法 id
    reason TEXT NOT NULL
        CHECK (reason IN ('SCHEMA_INVALID', 'SEMANTIC_REJECTED', 'DUPLICATE')),
    fingerprint TEXT,                      -- 可空：SCHEMA_INVALID 时算不出
    duplicate_of TEXT,                     -- 矩阵 #8：撞上的那条既有用例/候选的 id
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

-- 生成期审计的主查询：按原因看「这次生成被挡了多少、挡在哪」
CREATE INDEX IF NOT EXISTS idx_test_candidate_rejections_reason
    ON test_candidate_rejections (reason, created_at DESC);
