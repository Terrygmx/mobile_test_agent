-- 007_benchmark_runs.sql — Agent Benchmark 的持久化行（Task 6.5 / P3-30）。
--
-- ## 为什么是 007（偏离登记，沿用 006 的先例）
--
-- plan Task 6.5 写的是 `005_benchmark_runs.sql`（「设计 11 节之外的补充表」）
-- ——**那个编号已被 M5 的 `005_bug_candidates.sql` 占用**，006 也已有了主人
-- （M4 的 `006_discovery_events.sql`）。迁移脚本一旦落盘就**只许向后追加**
-- （002/004/005/006 的头部都明写这条，执行器按「已应用序号」跳过脚本）。
-- 故取 **007**。偏离已登记在 `docs/p3_data_audit.md`。
--
-- ## 为什么是补充表（不在设计 §11 的表清单里）
--
-- 设计 §11 的五张表都是**资产/过程**表；benchmark_runs 是**评估历史**——
-- 「每次改完 Agent（Prompt/模型/规则）跑一遍的快照」。它不参与任何审核
-- 状态机（F6 不适用），唯一的消费方是 `mta agent benchmark compare`
-- （两版对比 → AGENT_REGRESSION 判定）。
--
-- `metrics_json` 是**整份快照**（含逐 case 的对错与标注），不做列展开：
-- 指标词表还在演进（设计 §13.1 明说初版），列展开会让每次加指标都动 schema。

CREATE TABLE IF NOT EXISTS benchmark_runs (
    run_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,                   -- 版本标签（compare v1 v2 用它寻行）
    metrics_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_benchmark_runs_label
    ON benchmark_runs (label, created_at DESC);
