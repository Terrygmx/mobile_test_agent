# P3 演示记录（设计 §1.2 六条成功标准逐条留证）

> 里程碑：M6 = `checkpoint-p3-m6`（Task 6.6 / P3-31）
> Gate 脚本：`phase0/verify_p3_final.py`　产物：`out/p3_final_gate/summary.json`
> 本次实测：**33/33 PASS，exit 0，耗时 78.2s**；全量回归 **2207 passed / 0 failed**
> 设备：iPhone 14 模拟器 `AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E`（iOS 18.5）
> App：`com.phaset0.logindemo`（自建 SwiftUI demo，非第三方）

本文**不是**功能清单，而是逐条回答「设计 §1.2 承诺过什么、这次实测到什么、
证据在哪」。凡是没有证据的，本文明写「未覆盖」，不拿设计意图充数。

---

## 闭环全貌

```text
OFF  ── G1 production 拒绝 / G2 Benchmark 闭环 / G3 矩阵#18 三 Subagent
CHAIN ─ G4 plan → G5 generate → G6 人工 accept → G7 run(--fake-driver)
DEV  ── G8 explore(真机 sandbox) → 漂移注入 → G9 run 失败 → diagnose
        → Bug Candidate → 人工 accept → CONFIRMED + Evidence Bundle
矩阵#17 ─ G10 正式资产零程序修改（主仓库 md5 + 两个 gate 工程 git status）
矩阵#18 ─ G3b/c/d（manager 级）+ G8d（真机 agent_trace 归因）
```

---

## §1.2-1　新 build 的 git diff → 有理由的 Test Plan

**证据**：G4 `PASS G4_plan_runs: plan_id=plan_9c3437d246636c56`，
紧接着 G5 `gaps=1（现场重算）`。

排序依据可解释（M3 起）：`priority_score = risk_weight × coverage_impact +
change_correlation`（`planner/risk.py` / `planner/impact.py` / `planner/prioritizer.py`），
每一项都有落库字段，不是「LLM 觉得该测这个」。G6b 落盘的候选文件名本身带
出处：`cand_HomeView_SearchView_tap_HomeView_go_search_a207e877.yaml`
——屏、动作、目标、指纹四段拼在一起，人不看库也知道它在补什么。

**边界**：`plan` 在 `--no-llm` 下产出（Gate 用的是确定性路径）；带 LLM 的排序
质量由 M3 的单测覆盖，**本 Gate 不含 LLM 排序的实测**。

## §1.2-2　Candidate 经校验后**人工接受率可统计**

**证据**：G6 `review=PENDING dry_run=PASS` → G6b 落 `suites/` → G6 accept
输出带撤销路径（`git revert b3a70106`）。

接受率是**指标**不是承诺：`report/agent_metrics.py` 的
`duplicate_rate = DUPLICATE / generated`，分母口径钉死为
「入库候选 + SEMANTIC_REJECTED + DUPLICATE」（`SCHEMA_INVALID` 不进分母——
它连候选形状都没解析出来）。`reviewed_count` / `review_latency_seconds`
统计人工复核量与耗时（§13.1 的重心是 Human Effort，不是生成数量）。

**未覆盖**：`Selection Precision` 依赖人工标注真值，**库里没有**，
`AgentMetrics` 上根本没有这个字段（`test_collect_never_produces_ground_truth_metrics`
用 `not hasattr` 钉住）。它只在 Benchmark 标注集内算。

## §1.2-3　Bounded Exploration 有界停止

**证据**：G8a `outcome=LOOP_DETECTED steps=3 screens=2`，audit 行明写停止原因：

```text
audit: step 3 STOP 指纹 '999eb008e7737495' 在最近 10 步内出现 ≥ 3 次（max_repeated_state）
```

这正是 §1.2 点名的三种停止之一（另两种：预算耗尽 / 触达 Screen 边界）。
`LOOP_DETECTED` 是**正常终态**，不是失败——探索自己撞墙了就停，没有硬扛。

F7 环境隔离：G1 `mta agent benchmark run --env-kind production` → **exit 1
且没写任何库文件**（`wrote_db=False`）；真机探索只在 sandbox 下进行。

## §1.2-4　真实故障注入 → 带证据的 Bug Candidate

**证据**：G9 全链（漂移法：改 Swift 源码 `username_field` → `user_field`、
重编译重装、**不重扫 metadata** —— 漂移的形态必须是「元数据旧、App 新」，
否则 lint 会拒掉元数据里不存在的 target）：

```text
G9a  两个用例 FAIL (ELEMENT_NOT_FOUND)
G9b  复现 3/3 → reproducible=True
     多用例佐证 same_issue_multiple_tests：ELEMENT_NOT_FOUND 命中 2 个用例
     1 条假设，最高分 11（阈值 10）→ 归因建议 AUTOMATION_DEFECT
G9c  bug 候选 status=HUMAN_REVIEW score=11
G9d  人工 accept → CONFIRMED
G9e  Evidence Bundle bug_8a789fb0.json
```

不是裸的 LLM 结论：规则路径命中 1 条假设后**不调用 LLM**（设计 §8.2），
11 分由可数的证据堆出来（reproducible×3 计 5 + source_change_correlated 计 3
+ same_issue_multiple_tests 计 3 = 11，阈值 10）。F10 归因默认 `UNTRIAGED`，
过阈值才**建议**归因，且最终由人工确认——G9d 那一行 `reviewer=gate_final`
就是人。

## §1.2-5　全程不碰正式资产、不触达生产

**证据（矩阵 #17）**：

| 检查 | 结果 |
|---|---|
| G10a 主仓库 `overrides/` `generated/` `suites/` | 逐字节 md5 未变 |
| G10b gate 工程 A git status | `''`（人工 accept 自己 commit，撤销路径已打印） |
| G10c gate 工程 B git status | `''` |
| G10d App 源码 | 漂移改名已撤回 + 重编译重装 |

F2：Subagent **没有**任何直接改资产的工具——`Subagent.__init__` 的形参里
**不存在** `executor` / `driver` / `device_session`（不是靠约定靠签名），
唯一执行通路是构造注入的 `AgentToolkit`，而工具清单里
`modify_repository_directly` / `delete_testcase` / `modify_expectation` /
`modify_verified_experience` 压根不存在。资产变更只经
`create_*_proposal` → 人工审核。

F12 / 矩阵 #18：G3b 三份 `LLMBudget` **实例互不相同**（一个耗尽不挤占其余）；
G3c trace 按各自 `task_id` 归因、无串号；G3d 三方共走**同一个** Guard；
G8d 真机上 explorer 任务名下有 4 行 trace。F1：风险判定复用 P1
`effective_risk` + Guard，不建第二套。

## §1.2-6　固定 Benchmark 可判「是否退化」

**证据**：G2 三段——

```text
G2a  run v1 → run v2                      exit=0
G2b  compare v1 v2（同版本）              exit=0（无回归）
G2c  compare v1 v3（劣化快照）            exit=1 + AGENT_REGRESSION
      accuracy 1.000 → 0.000（阈值 0.05）
```

固定 7 类 case（设计 §13.2 逐字）：Locator Drift / Unknown Screen /
Ambiguous Element / Backend Failure / App Crash / New Transition /
Negative Scenario，每类自带标注预期，accuracy = 命中数/7。本版本 **7/7**。

判定有三条独立信号：准确率降幅 > 逐 case 由对翻错 > `llm_path` 断链。
垃圾 LLM 输出下 `analyze` 必须优雅降级（不抛）——这是 **Agent 自身的回归
探针**，与 App 回归是并列的两件事。

**未覆盖**：accuracy 1.000 是**固定场景上的确定性规则路径**，不是真实
App 上的统计；7 类样本量小，阈值 0.05 的意义只在「一类翻 = 0.143，必触发」
这一档。它防的是「改完 Agent 悄悄变差」，不是性能基准。

---

## M6 专属：升级路径（设计 §9.2）

G3e：连续 Guard 拦截 → `should_escalate` → **ESCALATED**，接手包三要素齐全
（goal / 已执行步骤 / 拦截原因），整份进 `agent_trace`。

升级是**终态**：已 ESCALATED 的任务再跑直接 `RuntimeError`（「不重试不放宽」，
`test_escalated_task_refuses_further_runs`）——人工接手是**新的任务行**，
不是把这一行重开。§9.4 保留项（Security / Financial / Destructive /
Ambiguous）不经连续计数，直接升级（`test_llm_policy_category_escalates_directly`）。

---

## 复现方式

```bash
# 前置：模拟器已 boot、Appium 4723 在跑
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
.venv/bin/python phase0/verify_p3_final.py    # exit 0 = Gate 通过
```

Gate 在 `out/p3_final_gate/` 下自建两个 git 工程（**不碰主仓库**），跑完
自动清理；退出前一定把漂移注入的源码改回去并重编译重装（G10d 钉住）。
