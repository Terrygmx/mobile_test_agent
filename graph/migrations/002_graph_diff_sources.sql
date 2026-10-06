-- 002_graph_diff_sources.sql — `graph_diffs` 补「比的是哪两面」（Task 5.3 评审 P3-2）
--
-- 为什么需要：`record_diff` 落的是 `(app_id, base_build, build)` + 条目列，而
-- 「源图 vs 运行时图」（两面**来源不同**、build 相同）与「build-to-build」
-- （两面**来源相同**、build 不同）在库里长得一样 —— 回读时分不清这次比较是
-- 哪种。CLI 打印时是知道的（`base=source → new=runtime`），信息在**打印时存在、
-- 落库时丢失**。
--
-- `graph_diffs` 是 Task 5.1 冻结的三表之一，当时还没有这个需求（plan 要求三表
-- 同批冻结）——这是**需求后到**，所以走 graph 自己的迁移链加列，不改 001。
--
-- 两列进**替换键**（`record_diff` 的 DELETE 用它）：同一 `(app_id, base_build,
-- build)` 下的两种比较互不覆盖。

ALTER TABLE graph_diffs ADD COLUMN base_source_of TEXT NOT NULL DEFAULT '';
ALTER TABLE graph_diffs ADD COLUMN source_of TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_diffs_scope_full
    ON graph_diffs (app_id, base_build, build, base_source_of, source_of);
