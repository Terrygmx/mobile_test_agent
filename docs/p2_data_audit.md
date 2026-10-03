# P2 Trace 数据审计报告

## 汇总

- runs：101（2026-09-24 ~ 2026-10-03）
- testcase_runs：89
- steps：244（含失败 0）
- runs.llm_enabled 为 NULL：101

## Locator Failure 重复出现（top）

| target_id | failure_type | 次数 | 跨 run 数 |
|---|---|---|---|
|（无）||||

## Recovery 分布

| kind | result | accepted | 条数 |
|---|---|---|---|
| LLM |  | 1 | 30 |
| LLM |  | 0 | 12 |
| None |  | 1 | 7 |

## Review 分布

| review_status | 条数 |
|---|---|

## 追溯链质量

- recoveries 总数：49
- step_id 悬空 dangling（关联不到 steps）：42
- 有 review 记录的 recovery：0

## 可做 Experience 种子的 ACCEPT 清单（E5）

| review_id | seed_run_id | seed_step_id | screen | target_id | candidate | seed_ready |
|---|---|---|---|---|---|---|
|（无）|||||||

**seed_ready=True 的种子：0 条**（成功标准要求 50+ 条真实 Recovery 事件基线）

---

## 审计结论（2026-10-03，P2-01 Task 1.1）

### 核心结论：可做 Experience 种子的 ACCEPT = 0 条，50 条基线需从零补齐

真实 `out/trace.db`（101 runs，2026-09-24 ~ 2026-10-03）的存量恢复数据**全部不可直接转化为种子**，三个叠加缺口：

1. **review 记录为 0**。`recovery_reviews` 表无任何行——存量 runs 走的是 P1 管线早期路径与 `phase0/verify_*` 脚本（经 `tracer/recorder.py` 的 P0 兼容 `record_recovery` 写入，`agent/recovery.py:60`），这些路径不创建 review；只有经 `cli/pipeline.py` 新管线（`_write_recovery_row`）的 LLM 恢复才会建 PENDING review。而设计 E5/8.1 要求 Candidate 只能从 `recovery_reviews.review_status == 'ACCEPT'` 产生——**没有 review 记录，就没有合法入口**。
2. **追溯链悬空 42/49**。`recoveries.step_id` 关联不到 `steps`（P0 兼容路径写入 `step_id or 0`）。这些行没有 run_id / screen 上下文，即使补上 review 也不满足种子三件套（seed_run_id / seed_step_id / seed_recovery_review_id）。
3. **result 列全空 + `runs.llm_enabled` 全 NULL**。存量行不满足设计 4.7 表"实际被尝试"的样本语义，也无法判断当时 LLM 是否启用。

另外 `steps.failure_type` 全库 0 条非空——101 次 run 全部 PASS（P1 稳定性基线的 `--no-llm` 跑轮），没有 Locator Failure 可复用。

### 对计划的影响

- **Task 1.1 的"补齐"工作量 = 全额 50 条**（不是"补缺口"）：需按 `scripts/p2_seed_recoveries.sh` 走 漂移 → `mta run`（新管线，产生 review）→ 人工 `mta review accept` 的完整链，每条事件都有完整三件套。
- 补齐与 M2 编码并行的约定不变（`docs/phase2-plan.md` 注意事项第 7 条）：Gate M2 真机演示前 ≥1 条、Gate M5 最终演示前 ≥50 条。
- 顺带发现的数据质量改进点（不改 P1 已冻结行为，记录备查）：P0 兼容 `recover()` 路径的 `step_id or 0` 写法、`runs.llm_enabled` 未回填——两者只影响旧数据可读性，新管线（`cli/pipeline.py`）写入路径无此问题。

### 种子产生方式（约束重申）

按设计 E5 与计划 Task 1.1：**每个事件必须真 LLM 恢复 + 人工 `mta review accept`，不许手工插库**。ACCEPT 动作在 M2 Task 2.3 接线后自动产生 Candidate 种子。

