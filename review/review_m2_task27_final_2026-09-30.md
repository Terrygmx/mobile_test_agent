# M2 Task 2.7 Review（P1-08：20+ 用例 + M2 Gate）

- **日期**：2026-09-30 20:45
- **审查对象**：`5581b8e`（postcondition H7 闭环 + 20 用例集）→ `4f17a88`（H10 注入验证）→ `4d742eb`（M2 Gate 全绿 + 真机契约修正）+ `4b29144`（记账）
- **审查方式**：只读。diff 逐行核对 + trace.db 实据 + 探针/复跑实测，未修改任何项目文件

---

## 1. 计划符合性核对（plan Task 2.7 四步）

| Plan 步骤 | 交付 | 结论 |
|---|---|---|
| 1. 补写用例覆盖 6.2 全部类型 | 20 条 × 4 套件（smoke/search/account/regression 各 5），覆盖全部 action（launch/terminate/tap/input/swipe/back）、wait 全条件矩阵（active/exists/not_exists/enabled/visible/text_equals/text_contains）、断言各 type、postcondition 真声明（logout_001/login_again_001） | ✅ **实测 lint 20/20 通过（0 失败）**；lint 还实抓了两个用例 bug（chain_nav_001 误引 SearchView.cell_alpha、search_001 跨套件 id 撞车破坏 --case 筛选）——lint 体系第一次在真实产出上证明价值 |
| 2. lint 全过 + `--no-llm` 连续 3 轮全绿 | lint 全过 ✅；Gate 实测 **2 轮 40/40 PASS**（verdict true、llm_calls=0 结构性断言） | ⚠️ **轮数偏差**：plan 写 3 轮，gate `ROUNDS=2`。见 R18-2 |
| 3. 注入 cleanup 失败 → 终止 + exit 2 | `phase0/verify_p1_h10.py` 4/4 PASS；`out/h10_injection.db`：executed=2/5、remaining=3、exit 2 | ✅ 且注入过程**实锤两个真 bug**（见 §2） |
| 4. verify_p1_m2.py Gate → commit + tag | 脚本 249 行 + `out/p1_m2_gate/` 全套产物（20 份 page_source、gate_summary.json、trace.db）+ tag `checkpoint-p1-m2` | ✅ verdict true，真机 UDID 记录在案 |

**实测数据**：全套 pytest 复跑全绿（~555）；`mta lint` 20/20 通过；gate trace.db 中 4 次 `p1_m2_gate_*` 运行全部留痕（3 轮真机调试 + 1 轮终态全绿，76/160 PASS 的调试历史不抹除——诚实）。

## 2. 三个 commit 的质量亮点

**postcondition H7 闭环（`5581b8e`）——本轮最佳实现**：
- checker 复用 WaitEngine 条件矩阵，**不为 postcondition 另造判定**（避免两套真相漂移——正是 R13-1 双源分叉教训的正向应用）；
- 语义分层精确：不满足 → `ACTION_OUTCOME_UNKNOWN`（恢复候选，不是 FAIL，判定归 RecoveryEngine 7.3）；checker 自身故障 → 如实记 `postcondition_error` 不伪装判定（8.2 反模式防御）；未注入 → `postcondition_skipped` 不静默当成功；
- lint `RUNNER_UNCONSUMED` 翻正（机制保留）——R10-2 的死配置告警就此退役。

**H10 注入实锤两个真 bug（`4f17a88`）**：
- ① SuiteRunner 丢弃 run_one 返回值——真实 TestcaseResult 被开头构造的空 PASS 抹掉，**旧测试 run_one 全返回 None 故未暴露**（替身失真本 repo 第 4 次现身：R2-0 mock 旧 schema、R15-1 `_OldP0Recorder`、2.6 find 契约、本次）；
- ② `SuiteAborted` 分支 pass 掉已跑结果（伪造「没跑过」）——改从 SuiteRunner 累积列表取回。
- 两处都是「测试替身掩盖生产路径断裂」的变体，能被注入验证抓出来，说明 H10 验证步骤设计得值。

**真机契约三断层自纠（`4d742eb`）**：app 级动作分流（launch/terminate/back/swipe 不走 find 管线）、strategies list[dict] 形态、perform→tap/input 按 action 分派。单测全绿纯因替身失真、真机首跑 20/20 挂、逐步自纠——这条"真实链路才能暴露契约断层"的规律在本项目已四次成立，M3 起写集成测试时应直接用生产 Executor 契约做替身规格。另有 XCUITest `.text` 对清空后 TextField 回落 placeholder 的探针实锤（`text_equals ""` 恒假），用例修正有据。

## 3. 问题清单（R18）

### R18-1 (P2) 测试顺序依赖 flake 第二次复现——升级为必修
2.6 review 时记为观察项的同一对测试本轮全量跑**再次 FAILED**（`test_recorder_closed_db_still_warns_not_raises` + `test_wda_restart_applies_implicit_wait_to_new_driver`），隔离跑与复跑均绿——共享状态污染（疑 WDA restart 的 monkeypatch/类级 state 与 closed-db recorder 共享 fixture），已复现 2 次。CI 上这是随机红，M3 开工前定位修掉（`-p randomly` 或拆 fixture）。

### R18-2 (P2) Gate 轮数 2 vs plan 3——需要拍板
plan Task 2.7 Step 2 写"连续 3 轮全绿"；gate `ROUNDS=2`（注释称 flake 防线），out/ 下的手工 per-suite 轮次 db（round_1/2/3、round_acc_1/2/3、round_reg_1/2/3）是调试期产物（account 三轮均 4/5），**没有任何一次"全 20 条 × 3 连绿"记录**。两个出路：补跑一轮 20/20（gate 脚本现成，10 分钟）；或在记账文档回写口径"2 轮全绿 + marker 精确断言 ≥ 3 轮裸 PASS"并给理由。不拍板就是 plan 与 Gate 验收的悬空差异。

### R18-3 (P3) cleanup 失败的 trace 保真缺口
`h10_injection.db` 实测：case 级 `testcase_runs` 两行都是 **PASS**，`CLEANUP_FAILED` 只存在于 RunResult/套件层（run 级 INFRA_FAILURE/exit 2 一致）。原因：`run_case(manage_env=False)` 的 end_testcase 在 SuiteRunner 的 cleanup 之前落库，cleanup 失败无人回写。trace 排障时会看到"PASS 但套件中止"的矛盾。修法：SuiteRunner 捕获 CleanupError 后回写该 testcase_run 的 `cleanup_status='FAILED'`（列已存在，2.5 加的）或状态升级。不阻塞 M2 收口，M3 前记账。

### R18-4 (P3) 记账链四处欠账（上轮 R17 全部未处理 + 一项改口）
1. `--no-llm`/`--allow-metadata-mismatch` 仍零读取（R17-1/2 原样）；gate 的 `llm_calls==0` 结构性断言是合理替代，但两个 flag 仍是"参数存在=功能存在"；
2. `executed_steps=len(run.results)` 仍是**用例数冒充步骤数**（R17-3 原样，HTML LLM rate 分母失真）；
3. R16-3 曾记账"Recovery 接线→2.7"，2.7 完了 verify docstring 改口"M3/后续 task"——改口本身合理（Recovery 属 M3 语义），但账本没动；
4. `p1_schema_review.md` 止于 §11，`4b29144` 的"2.7 记账"只写进了 memory 文件；Risk YAML 可用性拍板（用例不声明 risk、由 metadata/启发式推导）也无文档记录——这个决策影响所有后续用例，应落字。

## 4. 结论

**M2 Gate 成立，Task 2.7 通过。** 20 用例真机全绿（2 轮 40/40 + marker 精确断言）、H10 闭环有注入实证、postcondition H7 闭环实现质量为本 Phase 最高、三处真机契约断层自纠留痕完整。R18-1（flake）与 R18-2（轮数拍板）建议进 M3 前处理；R18-3/4 记账即可。M2 至此收口，可进 M3（Source Intelligence 与 Build Identity）——届时优先处理：Recovery/Reconciliation 与新管线对接、`from_dirs(generated_root=overrides)` 的语义拆分（gate 脚本已自我标注）、generated+overrides 合并验证。
