-- 006_discovery_events.sql — Discovery Event 表（设计 §11 / §7.5；Task 4.5 / P3-18）。
--
-- ## 为什么是 006（偏离登记）
--
-- plan Task 4.5 写的是 `003_discovery_events.sql`——**那个编号已被 M3 的
-- `003_dry_run_detail.sql` 占用**，而 004/005 也各自有了主人（`004_review_note`
-- / `005_bug_candidates`）。迁移脚本一旦落盘就**只许向后追加**（002/004/005 的
-- 头部都明写这条，执行器按「已应用序号」跳过脚本）：重用一个已落盘的编号会让
-- 「已按旧版建过库」的人**永远拿不到这张表**。故取 **006**。偏离已登记在
-- `docs/p3_data_audit.md`。
--
-- ## 与设计 §11 的差异（全部是加固，无字段删减；沿用 001/002/005 的惯例）
--
-- | 位置 | 设计 §11 | 本文件 |
-- |---|---|---|
-- | event_id | PRIMARY KEY | 同 |
-- | kind | 可空 | `NOT NULL` + `CHECK`（四类词表，见下） |
-- | detail_json | 可空 | `NOT NULL DEFAULT '{}'` |
-- | exploration_task_id | 可空 | 同（**保持可空**：不是所有发现都来自探索任务） |
-- | review_status | `DEFAULT 'PENDING'` | 同 + `NOT NULL` + `CHECK`（F6 三态） |
-- | created_at | 可空 | `NOT NULL` |
-- | reviewer / reviewed_at / review_note | 无 | **新增三列**（F6 审核留痕） |
-- | 索引 | 无 | `idx_discovery_events_review` / `idx_discovery_events_kind` |
--
-- ⚠️ **`reviewer` / `reviewed_at` / `review_note` 是设计 §11 没有的三列**，
-- 照 `test_candidates`（002 + 004）的先例补齐：F6 要求「与 P2
-- `recovery_reviews` 相同的模式」，而 P2 那张表有 reviewer/reviewed_at/note。
-- 不补的话 `candidate review` 的 ACCEPT/REJECT 无处留痕——「谁在什么时候基于
-- 什么理由确认了这个发现」是 discovery 唯一的审计价值（发现本身只是个事件）。
-- 直接建在 006 而不是像 004 那样再开一个 ALTER：本表**首次落盘**，没有
-- 「已建过库的人拿不到列」的问题（004 是因为 002 已落盘才不得不 ALTER）。
--
-- ⚠️ **`kind` 四类词表**（设计 §11 原文）：`NEW_SCREEN` / `NEW_TRANSITION` /
-- `NEW_ELEMENT` / `ANOMALY`。前两类是**图资产**（ACCEPT → 登记进 Source
-- Graph，见 `candidates/discovery.py::graph_registration`），后两类不是——
-- `NEW_ELEMENT` 是元素级的发现（图里没有元素层），`ANOMALY` 根本不是资产，
-- 是「这次探索看到了解释不了的东西」的**信号**，只留痕、不进图。
--
-- 词表与 Python 常量是**两处字面量**（`candidates/discovery.py` 的
-- `DISCOVERY_KINDS` / `DISCOVERY_REVIEW_STATUSES`），一致性由
-- `tests/unit/test_discovery_event.py` 钉住（手法同 001 的
-- `test_state_check_constraint_matches_enum`：加值忘改这里会红）。

CREATE TABLE IF NOT EXISTS discovery_events (
    event_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL
        CHECK (kind IN ('NEW_SCREEN', 'NEW_TRANSITION', 'NEW_ELEMENT',
                        'ANOMALY')),
    detail_json TEXT NOT NULL DEFAULT '{}',
    -- 产出这次发现的探索任务（可空：不是所有发现都来自一次探索任务）
    exploration_task_id TEXT,
    review_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (review_status IN ('PENDING', 'ACCEPT', 'REJECT')),
    reviewer TEXT,
    reviewed_at TEXT,
    review_note TEXT,
    created_at TEXT NOT NULL
);

-- F6 审核队列的主查询：按状态取、新在前
CREATE INDEX IF NOT EXISTS idx_discovery_events_review
    ON discovery_events (review_status, created_at DESC);

-- 按类别看「这次探索发现了什么」（Gate 演示与人工巡检）
CREATE INDEX IF NOT EXISTS idx_discovery_events_kind
    ON discovery_events (kind, created_at DESC);
