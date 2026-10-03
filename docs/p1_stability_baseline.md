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

## 基线结论

（正式 50 轮跑完后回填：比例、flaky 清单、WDA 重启、平均耗时、卫生判据、
异常轮分析。）
