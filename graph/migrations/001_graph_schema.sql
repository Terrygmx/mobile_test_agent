-- 001_graph_schema.sql — UI State Graph 三表（设计 12 / P2-11~13，Task 5.1）
--
-- 独立库（默认 out/graph.db）+ 独立版本链（graph/migrations/）。plan Task 1.2
-- 的关键决策：表结构**随 M5 实现定型**，不提前冻结（P1 Schema 复盘教训）。
--
-- 三表的共同口径：
--   · `app_id` / `app_build` 是**范围**：同一屏在不同构建上可能不同，diff 要
--     按 build 关联（设计 12.3）。app_build 允许为空串（旧 trace 没有 build）。
--   · `source_of` 区分**声明的**与**观察到的**：`runtime`（P1 Trace 实际到达）
--     与 `source`（Source Metadata 的声明，Task 5.2）。二者同表不同行，diff 时
--     直接对比——不建两张镜像表（同一概念只许一处结构）。
--   · 每张表都带 `first_seen` / `last_seen`：图是**累积**的（多次 run 聚合），
--     没有时间范围就无法回答「这个转移是什么时候出现的」。

CREATE TABLE IF NOT EXISTS screen_nodes (
    id            INTEGER PRIMARY KEY,
    app_id        TEXT NOT NULL DEFAULT '',
    app_build     TEXT NOT NULL DEFAULT '',
    screen_id     TEXT NOT NULL,
    source_of     TEXT NOT NULL,           -- runtime | source
    visit_count   INTEGER NOT NULL DEFAULT 0,
    -- 观测证据类别（逗号分隔的集合语义，排序后写入）：wait_screen /
    -- recovery_observed / source_declared。E12：图只来自已有 Trace 与
    -- Source Metadata，**不做自动探索**——证据列就是这条纪律的留痕。
    evidence      TEXT NOT NULL DEFAULT '',
    first_seen    TEXT,
    last_seen     TEXT,
    UNIQUE (app_id, app_build, screen_id, source_of)
);

CREATE TABLE IF NOT EXISTS screen_transitions (
    id            INTEGER PRIMARY KEY,
    app_id        TEXT NOT NULL DEFAULT '',
    app_build     TEXT NOT NULL DEFAULT '',
    from_screen   TEXT NOT NULL,
    to_screen     TEXT NOT NULL,
    -- 触发条件：紧邻「目标屏被观测到」之前的那个步骤（`<step_type>:<target_id>`）。
    -- 设计 12.2 的 CHANGED（「同一触发条件，目标 Screen 变化」）要靠它做连接键，
    -- 所以它进唯一键——同一对屏由不同动作触发是**两条**不同的转移。
    trigger       TEXT NOT NULL DEFAULT '',
    source_of     TEXT NOT NULL,           -- runtime | source
    count         INTEGER NOT NULL DEFAULT 0,
    first_seen    TEXT,
    last_seen     TEXT,
    UNIQUE (app_id, app_build, from_screen, to_screen, trigger, source_of)
);

-- Task 5.3 用；Task 5.1 只建表（plan 要求三表同批冻结）。
CREATE TABLE IF NOT EXISTS graph_diffs (
    id            INTEGER PRIMARY KEY,
    app_id        TEXT NOT NULL DEFAULT '',
    base_build    TEXT NOT NULL DEFAULT '',
    build         TEXT NOT NULL DEFAULT '',
    kind          TEXT NOT NULL,           -- ADDED/REMOVED/CHANGED/NOT_OBSERVED/UNKNOWN
    screen_id     TEXT,                    -- 节点类 diff
    from_screen   TEXT,                    -- 转移类 diff
    to_screen     TEXT,
    trigger       TEXT,
    detail_json   TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_nodes_scope
    ON screen_nodes (app_id, app_build, source_of);
CREATE INDEX IF NOT EXISTS idx_transitions_scope
    ON screen_transitions (app_id, app_build, source_of);
CREATE INDEX IF NOT EXISTS idx_diffs_scope
    ON graph_diffs (app_id, base_build, build);
