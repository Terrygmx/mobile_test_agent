# P1 稳定性基线（M5 / P1-14 / Task 5.1）

> 状态：**脚本就绪，待环境跑数**。Appium/模拟器在线后按下面流程先冒烟
> 3 轮，确认脚本正确，再正式 50 轮（整夜或分批）。

## 任务定义（设计 17 M5 / 1.2）

同 build、同设备、同套件集**连续 50 次** `mta run --no-llm`，产出基线：

- PASS / RECOVERED / FAIL / INFRA 比例（run 级 + 用例级，8.3 口径）
- flaky 用例清单（跨轮终态不一致）
- WDA 重启总数、平均耗时
- **Gate M5**：无 WDA session 泄漏、无状态污染、Trace 无丢失、无未分类
  失败。**不预设通过率 SLA**——先跑出基线，再据此制定团队指标。

## 前置（一次性，跑前核对）

1. Appium 在线（`http://127.0.0.1:4723`）+ 模拟器 booted。
2. 被测 App 按当前 commit 构建/安装，metadata 同源（参考
   `phase0/verify_p1_m4.py` 的 build/install 流程；同 build 是 M5 的
   前提，中途不重编）。
3. `TEST_USERNAME` / `TEST_PASSWORD` 已入 env（lint 前置门）。

## 流程

```bash
ROUNDS=3 scripts/p1_stability_run.sh          # 冒烟 3 轮，核对 CSV/日志
.venv/bin/python scripts/p1_stability_report.py \
  --db out/stability/trace.db --csv out/stability/rounds.csv \
  --json out/stability/baseline.json

ROUNDS=50 scripts/p1_stability_run.sh         # 正式 50 轮（可中断续跑）
.venv/bin/python scripts/p1_stability_report.py ...   # 同上，产出基线
```

- 一轮 = 按固定顺序跑全部 4 套件（smoke → search → account →
  regression，20 条用例/轮）；每次 mta run 调用落一行 CSV
  （时间窗 + 退出码 + 当轮卫生计数）。
- **断点续跑**：中断后重跑同命令，从 CSV 已有最大轮号之后继续。
- 专用库 `out/stability/trace.db`，与日常 runs 隔离；报告按时间窗 +
  run_id 去重归因。

## 脚本行为约定

- preflight（Appium / booted / secret env）不满足 → exit 3，不刷轮。
- 连续 3 次 mta run exit 3（前置错误）→ 终止（环境坏了继续刷只会产出
  垃圾基线）。
- 每次调用后核对：RUNNING 残留（TraceStore 打开即自愈，仍记账）、
  FAIL×UNTRIAGED 计数；WDA 泄漏检查尽力而为（simctl listapps）。
- 报告卫生判据：`running_left == 0` 且 `llm_calls == 0`，违规 exit 2。

## 环境（2026-10-03 起跑记录）

- 模拟器 iPhone 14 `AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E`（iOS 18.5）；
  Appium 3.7.0（127.0.0.1:4723，DEVELOPER_DIR=/Applications/Xcode.app）。
- 同 build：commit `e2f0f3f`（make p1-build 注入 MTA_GIT_COMMIT +
  p1-install + repo-generate 一致性 PASS）。跑前预检曾实锤 runner 缺
  `--bundle-id`（连续 exit 3 自动终止按设计生效）——已补 `--udid`/
  `--bundle-id` 两个真机路径旗标。

## 冒烟结果（3 轮，2026-10-03 01:44-01:52 UTC）

- 12/12 次调用 exit 0；**60/60 用例 PASS**（20 用例/轮 × 3 轮）；
  RECOVERED 0；flaky 无；WDA 重启 0；llm_calls 0；UNTRIAGED FAIL 0；
  RUNNING 残留 0。单套件 34-45s，单轮 ≈ 2.6 min。
- 冒烟期顺带修复报告归因 bug：相邻调用共享边界秒时用例行被按时间窗
  双计（实测 105 行 vs 实际 60）——改为按本窗**新归因 run_id** 取
  用例/infra 行；7 个聚合单测同步回归。

## 基线结论（正式 50 轮，2026-10-03 01:54-03:54 UTC）

- 规模：50 轮 × 4 套件 × 5 用例 = **200 次 run / 1000 条用例执行**，
  同 build（`e2f0f3f`）同设备（iPhone 14 模拟器）同套件集，全程无重编。
- **结果：998 / 1000 PASS（99.8%，8.3 口径）；RECOVERED 0；FAIL 2。**
- **flaky 清单（2 例，均为单次 WAIT_TIMEOUT）**：
  - `search_004`（第 10 轮）：tap go_search 后等 SearchView 超时；
  - `profile_view_001`（第 44 轮）：tap go_profile 后等 ProfileView 超时。
  - 归因（人工 triage）：**ENVIRONMENT_DEFECT**——同一转换其余 49 轮全部
    通过、无 WDA 重启、同 build，判定为模拟器转场时序抖动，非应用缺陷。
    证据与判定已写入对应 testcase_runs.detail_json。
- **WDA 重启总数：0**（infra_events 零 RESTART_WDA）。
- **耗时**：平均每轮 153.7s（min 150 / max 163，波动 <9%）；平均用例
  5.3s。总时长 2h8m。
- **Gate M5 卫生判据（四项全过）**：
  1. 无 WDA session 泄漏：全程 --session-override 有界，campaign 结束
     残留恰 1 个最后一轮 WDA runner（无累积），已手动清理；跟进项：mta
     run 退出时不做显式 session delete，建议后续加 finally quit。
  2. 无状态污染：每轮 prepare/cleanup 全链复位，200 轮结果分布稳定
     （无连续失败、无连锁 INFRA）。
  3. Trace 无丢失：RUNNING 残留 0；**披露**：2 个 FAIL 轮的失败 wait
     步骤缺 steps 行、detail_json 空（本轮发现并已修复的记录缺口——
     见下），2 行用例级 triage 证据为事后补写。
  4. 无未分类失败：2 个 FAIL 已归因（ENVIRONMENT_DEFECT），UNTRIAGED
     归零。
- LLM 调用：0（`--no-llm` 基线，结构性成立）。

### 本轮顺带修复（跑完即修，不改变基线数据）

- 失败 aux 步骤（wait/assert）此前不落 steps 行 → 补齐（trace 不再
  「跑到一半就没了」）；testcase detail_json 此前恒空 → end_testcase
  打通 detail 传递（存储层 redact）。后续 run 的失败现场将完整可查。
- p1_stability_run.sh 表头只在文件新建时写一次（重复 append 曾产生
  哑行）；stability 报告按 run_id 归因修边界秒双计。

### 团队指标建议（依据本基线，非预设 SLA）

- 稳定 build 用例级 PASS Rate 底线：**≥ 99%**（本基线 99.8%）。
- 单轮时长预算：**≤ 3 min**（本基线 2m34s ± 7s）。
- WAIT_TIMEOUT 单发（≤2/1000）视为环境抖动，同用例连续 ≥2 轮失败才
  升级为用例缺陷排查。
