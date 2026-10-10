-- 004_review_note.sql — `test_candidates` 补人工审核的拒绝理由列
-- （Task 3.5 / P3-13；AskUserQuestion 拍板：note 走迁移加列，不混进留痕表）
--
-- ## 为什么需要
--
-- F6 要求审核范式与 P2 对齐：`recovery_reviews.decide_review`（`tracer/storage.py:653`）
-- 会把 REJECT 的 `--note` 落进行里——「拒绝理由必须留痕」是 8.2 归因纪律的
-- review 侧投影。P3 的 `test_candidates` 在 002 建表时没有 note 列，审核的
-- 理由将无处落（CLI 只打印 = triage 数据断流）。
--
-- 它**不**进 `test_candidate_rejections`：那张表是**生成器**的自动拒绝
-- （SCHEMA_INVALID / SEMANTIC_REJECTED / DUPLICATE），把「人工审核决定」混进
-- 去会把两个时间线、两种责任主体搅在一起（audit 里 Task 3.4 的口径登记 3）。
--
-- ## 为什么是 004（不改 002/003）
--
-- 迁移脚本一旦落盘就**只许向后追加**：执行器按「已应用序号」跳过脚本，
-- 原地加列对已建过库的人完全不生效（review_p3_task34 round 2 的 P3-new，
-- 已抽 003 兑付过一次同一纪律）。004 照办。
--
-- `TEXT` 可空：NULL = 该候选未决或该决策无理由——与「空字符串理由」可区分，
-- 既有行回填为 NULL 是「未决」的正确表示（与 003 的 `'{}'` 同理，安全）。

ALTER TABLE test_candidates
    ADD COLUMN review_note TEXT;
