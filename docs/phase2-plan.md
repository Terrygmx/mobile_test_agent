# Mobile Test Agent — Phase 2 实施计划

> **For Hermes:** 用 subagent-driven-development 按任务派发执行；每任务两段 review（spec 合规 → 代码质量）。
>
> **Goal:** 在 P1 基线上实现 Experience Store（Candidate/Verified/Degraded/Rejected 状态机 + Runtime Guard + 人工 Promotion）与 UI State Graph 两条主线（P2 设计文档 10 目标 / 13 硬约束 / 5 里程碑 / 16 项故障注入矩阵全部落地），达到"系统运行越久、越少依赖 LLM，但正式测试行为（Element Repository）只经人工 Promotion 改变"。P2 不增加 Agent 自主性，只增加"复用已验证知识"的能力。
>
> **Architecture:** 沿用 P1 全部分层（cli/runner/executor/repository/source/agent/llm/trace/report），新增 `experience/` 与 `graph/` 两个包。核心原则：**先 Experience 主线（M1→M4），后 Graph 主线（M5）**；Experience 内部先把安全边界钉死（Guard、非幂等排除、滑动窗口），再做 Promotion 和 Cache。所有决策逻辑（Guard、状态机转换、排序、Diff）必须是脱离设备的纯函数并有单测（E13）。Experience 命中不消耗 LLM Budget；`RECOVERED ≠ PASS` 语义不变，仅细化 `detail.kind`。
>
> **Tech Stack:** Python 3.11 + Pydantic 2.x + SQLite（experience 独立库，复用 tracer 的 schema_migrations 迁移机制）+ pytest（沿用 P1 测试基建，无新依赖）。
>
> **工作分支:** `P2-1003`（从当前 HEAD `dcb375b` 切出——比 `v0.1-p1-complete`（`26b8ffe`）多一笔 P1 收尾修复；历史 `P2-0929`/`P2-0930` 等旧分支为早期试验，**不作为基线**）。**设计基线:** `docs/mobile-test-agent-phase2-design.md` v1.0-draft。**每任务完成即 commit**（沿用 P1 惯例）+ 里程碑末打 tag `checkpoint-p2-mN`。

---

## 0. P1 基线与现状差距（写计划前已核实）

| 设计要求 | 现状 | 差距 |
|---|---|---|
| ExperienceStore 接口（设计 7.1） | `agent/context.py:30` Protocol 仅有 `lookup(app_build, screen, target_id) -> list[dict]` + `EmptyExperienceStore` 恒空 | 签名不符（P2 主键是 `app_id` 非 `app_build`、返回 `Experience` 模型），且缺 create_candidate/record_run/update_status 等写接口；需迁到 `experience/store.py` 全仓唯一定义 |
| Recovery 引擎接入点（设计 3 节） | `agent/recovery.py:358` 预留位已在「reconciliation 后、LLM 前」，但只记 stage 不消费 | 接真 store：lookup → 排序 → 逐个 Guard → record_run → 执行 → 回落 LLM 全链 |
| Candidate 种子（设计 8.1 / E5） | `agent/review.py decide_review` ACCEPT 只导出 overrides 补丁 | ACCEPT 分支触发 `create_candidate`；REJECT 不经过任何代码路径入库 |
| 候选校验共享（设计 5 节） | 校验逻辑内嵌 `agent/recovery.py` 的 `_llm_stage` | 抽成 `experience/runtime_guard.py`，LLM 候选与 Experience **同一套实现**，不是平行第二套 |
| `origin: experience`（设计 9.2） | `repository/loader.py:28` `ORIGINS = ("source", "manual")`，注释已预留 experience | 扩 ORIGINS + 合并优先级 manual > source > experience（ Locator Chain 链尾追加，尝试顺序而非互斥丢弃） |
| 结果语义（设计 10 节） | `RecoveryResult.kind` 为小写短串（llm/experience/…），报告聚合 RECOVERED | `detail` 细分 `RECOVERED_LLM / RECOVERED_EXPERIENCE / RECOVERED_ASSERTION_TARGET`，聚合口径与退出码不变 |
| SQL schema（设计 11 节） | trace.db 仅迁移 `001_trace_schema_0_1.sql` | 新增 experience 库迁移 002（experience 四表）；graph 三表拆到 M5 迁移 003（见 Task 1.2 决策） |
| CLI（设计 13 节） | lint/run/repo/source/review/report 六组子命令 | 新增 `mta experience`（list/show/verify/revalidate/promote/sweep）、`mta graph`（build/diff/show） |
| 指标（设计 17 节） | report 有 LLM 调用数 / Invocation Rate | Experience Resolution Rate、四状态计数、Promotion Rate、延迟梯度、Revalidation 成功率 |
| 真实数据基线（设计 1.2） | 50 次稳定性跑轮 + 24 项矩阵产生的 recoveries/recovery_reviews 在 `out/trace.db` | 需审计 ACCEPT 数量；不足 50 条用 F5 漂移法补齐真实事件 |

**环境事实**（沿用 P1）：模拟器 iPhone 14 UDID `AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E`（iOS 18.5）；Appium 3.7.0 需 `DEVELOPER_DIR`；LLM 网关/模型以当前分支 env 配置为准（`llm/provider.py`）；`/tmp/swift_scan` 会被清理（重扫前 `make scan`）。

**P1 行为保留原则**：P1 全部 `tests/`（含 24 项故障矩阵）与 `phase0/verify_*.py` 持续通过（回归底线）；**空 experience 库时 Recovery 行为必须与 P1 完全一致**（空库 ≙ EmptyExperienceStore 恒返回 []）；`--no-llm` 行为不变。

---

## 1. 里程碑总览与依赖

```text
M1 审计/Schema(1.5d) ─► M2 Store/Guard(4d) ─► M3 验证/降级(3d) ─► M4 Promotion(2.5d) ─► M5 Graph+演示(3.5d)
                                                │
                                                └─ M5 Graph 主线（P2-11~14）独立于 Promotion，
                                                   可与 M4 并行，但不得早于 M3 收尾（设计 15 节原则）
```

- 每个 Gate 不通过不进下一里程碑。
- 故障注入矩阵 16 项（设计 16 节）分配到各 Gate：**M2 = #1–#4/#10/#15；M3 = #5–#9/#11/#12；M4 = #13/#14；M5 = #16**（#15 只依赖 Experience 命中 + 语义细分，与 Promotion 无关，前移到 M2 早拿反馈）。
- 任务内 TDD：先写失败测试 → 最小实现 → 过测试 → commit。
- 真机只跑必须的（数据补齐、各 Gate 演示、漂移场景）；其余全部 FakeDriver / 内存库单测（E13）。
- 真实 ACCEPT 数据补齐（Task 1.1）**可与 M2 编码并行**：M2 的 Store/Guard/引擎接线单测全部基于内存库与 FakeDriver，不依赖真实数据；硬性时点为 Gate M2 真机演示前 ≥1 条（demo 需要）、Gate M5 最终演示前 ≥50 条（成功标准 1.2）。

---

## M1 — 数据审计与 Schema（设计 P2-01 / P2-02）

> **Gate M1**：审计报告落盘、补齐脚本冒烟通过（≥1 条真实事件走通 漂移→LLM→accept→Candidate 全链）；experience 库迁移幂等可重放；models 为纯数据无 I/O。50 条基线允许与 M2 并行补齐（时点约束见第 2 节注意事项第 7 条）。

### Task 1.1: P2-01 — P1 Trace 数据审计 + 真实恢复事件基线

**Objective:** 审计 P1 已积累的 Trace，建立"50+ 条真实 Recovery 事件"数据基线（成功标准 1.2 的前提）。

**Files:**
- Create: `scripts/p2_audit_recoveries.py`（读 trace.db：Locator Failure 重复出现 top-N、(screen, target) 去重对、recovery kind 分布、review ACCEPT/REJECT 计数、可做种子的 ACCEPT 清单）
- Create: `docs/p2_data_audit.md`（基线结论 + 缺口与补齐记录）
- Create: `scripts/p2_seed_recoveries.sh`（F5 漂移法批量制造真实事件：改名 identifier → 重编安装 → `mta run` → 人工 `mta review accept`；逐轮落 run_id）
- Test: `tests/unit/test_p2_audit.py`（fixture 用内存 SQLite 造数据）

**Steps:**
1. 失败测试 → 实现审计脚本（纯查询，不写库）。
2. 对真实 `out/trace.db` 跑一遍，报告落盘；若 ACCEPT < 50：漂移脚本先冒烟（≥1 条真实事件走通全链）后，其余数量**与 M2 编码并行补齐**——每个事件仍必须真 LLM 恢复 + 人工 ACCEPT，不许手工插库（E5 的"真实数据基础"不容绕过）；硬性时点：Gate M2 真机演示前 ≥1 条、Gate M5 最终演示前 ≥50 条。
3. Run → PASS → Commit: `feat(p2): trace audit + real recovery baseline (P2-01)`

### Task 1.2: P2-02 — Experience 模型 + 库迁移

**Objective:** 设计 4.2/4.3/11 节落地为代码与 schema。

**关键决策**：graph 三张表（screen_nodes/screen_transitions/graph_diffs）**拆到 M5 落地**——表结构应随 M5 实现定型，避免提前冻结（P1 Schema 复盘教训）；且 graph 是设计 14 节的独立主线 B，schema 归 `graph/migrations/` 自己的版本链与独立库文件 `out/graph.db`，**不挂在 experience 包下**（迁移执行器由 experience 侧泛化为可复用，graph 直接调用）。M1 迁移 002 只建 experience 四表。

**Files:**
- Create: `experience/__init__.py`、`experience/models.py`（`Experience`/`ExperienceStatus`/`CandidateSeed`/`ExperienceRun`/`StateEvent`/`VerificationPolicy`；`strategy` 字段复用 `repository.loader.LocatorStrategy`，序列化进 `strategy_json`）
- Create: `experience/migrations/002_experience_schema.sql`（experiences / experience_runs / experience_state_events / promotion_proposals 四表 + `idx_experiences_lookup`）、`experience/schema_migrations.py`（复用 tracer 迁移机制）
- Test: `tests/unit/test_experience_models.py`、`tests/unit/test_experience_schema.py`

**Steps:**
1. 失败测试：`CandidateSeed` 缺 `seed_run_id / seed_step_id / seed_recovery_review_id` 任一 → 拒绝创建（E5）；Experience 默认统计零值、`validated_builds=[]`；状态枚举恰四值；`promoted` 与 `status` 是独立字段（设计 9.4 两条时间线）。
2. 迁移幂等（重复 migrate 无副作用）+ 索引存在性断言。**库文件独立**：默认 `out/experience.db`（CLI `--exp-db` 可覆盖）——trace 是 append-only 流水库、experience 是可变状态库，按库划分让单写者边界（E9）最清晰。
3. Run → PASS → Commit: `feat(p2): experience models + schema migration (P2-02)` + tag `checkpoint-p2-m1`

---

## M2 — Store 与 Guard（设计 P2-03 / P2-04 / P2-05；P2 最优先级）

> **Gate M2**（= 设计 18 节 Demo 前四步）：矩阵 #1（同 build 第二次 → `RECOVERED_EXPERIENCE`、LLM calls=0）、#2/#3/#4（Guard 分支）、#10（REJECT 不建 Candidate）、#15（断言漂移恢复不改期望值）全过。

### Task 2.1: P2-03 — ExperienceStore（单写者 E9）

**Objective:** 设计 7.1/7.2 接口全套 + 单写者落地。

**Files:**
- Create: `experience/store.py`（SQLiteExperienceStore：lookup / create_candidate / record_run / get_runs / update_status / list）
- Modify: `agent/context.py`（删除 Protocol 与 Empty 定义，re-export `experience.store`——全仓唯一定义，防两套接口；顺带更正注释中旧稿"设计 20 节"编号引用）、`agent/recovery.py`（import 调整，行为不变）
- Test: `tests/unit/test_experience_store.py`

**Steps:**
1. 失败测试：create_candidate 种子三件套校验（E5）；lookup 按 `(app_id, screen_id, target_id)` 返回列表；record_run 追加且 `sample_count == success + failure` 恒成立；update_status 写 `experience_state_events`（from/to/reason/operator）；`validated_builds` 只在 `record_success_build()` 追加（E7——静态校验路径不触碰它）；runs 数据不物理删除（DEGRADED/REJECTED 后至少保留 180 天，设计 11.2）。
2. 单写者（E9）：连接 `BEGIN IMMEDIATE` + `busy_timeout`；进程内 `threading.Lock` 串行化全部写方法；CLI help / 文档标注"会产生写操作的套件 CI 串行执行"约定；Reader（lookup/get_runs/list）并发安全。
3. Run → PASS → Commit: `feat(p2): experience store sqlite single-writer (P2-03)`

### Task 2.2: P2-04 — Runtime Guard（共享实现 E1）

**Objective:** 设计 5 节 `GuardResult` 全分支 + 从 recovery.py 抽共享校验函数。

**Files:**
- Create: `experience/runtime_guard.py`（`experience_runtime_guard` + `GuardResult`，含 4.7 表的 `record_as_sample` 矩阵）
- Modify: `agent/recovery.py`（`_llm_stage` 候选校验改为调共享函数，**删除平行实现**）
- Test: `tests/unit/test_runtime_guard.py`

**Steps:**
1. 失败测试逐分支（设计 5 节伪代码 + 4.7 表逐行）：`SCREEN_UNKNOWN`/`SCREEN_MISMATCH` → MISS、不写样本；`TARGET_NOT_FOUND`/`TARGET_AMBIGUOUS`/`TYPE_MISMATCH` → BLOCK、计失败；`RISK_BLOCKED` → BLOCK、不计样本（4.7 第 4 行）；EXECUTE 计样本。`effective_risk` 走 P1 `compute_effective_risk`（E2：max(step/element/screen/env)，不信任 LLM 自报）。
2. 共享断言：同一输入下 LLM 候选校验与 Experience Guard 结论一致（FakeDriver 双向测试——防两套规则漂移，这是 P2-04 的红线）。
3. Run → PASS → Commit: `feat(p2): shared runtime guard (P2-04)`

### Task 2.3: P2-05 — review accept 触发 create_candidate（E5）

**Objective:** 设计 8.1：人工 ACCEPT 是 Candidate 唯一入口。

**Files:**
- Modify: `agent/review.py`（`decide_review` ACCEPT 分支：从 recovery 行组装 `CandidateSeed` → `create_candidate`；缺字段如实报错，不静默跳过）、`cli/main.py`（review 子命令加 `--exp-db` 装配）
- Test: `tests/unit/test_review_seed.py`

**Steps:**
1. 失败测试：ACCEPT → experience 库出现 CANDIDATE（种子三件套 + strategy 来自被接受的恢复策略）；REJECT → 库无记录（矩阵 #10）；同一 recovery 重复 ACCEPT → 幂等不重复建；trace 事件 `candidate_created`（设计 11.1）。
2. Run → PASS → Commit: `feat(p2): review accept seeds candidate (P2-05)`

### Task 2.4: Recovery 引擎接真 Store + 结果语义细化（设计 3 / 3.1 / 5.1 / 10）

> **⚠️ 接线前置（Task 2.1 评审 P3-3 显性化）**：引擎过渡债一次清掉——
> ①`agent/recovery.py` 的 `experience_store.lookup(ctx.app_build, ...)`
> 调用形态是 P1 旧签名（build ≠ bundle，键语义不同），接真 Store 必须
> **连调用点带键来源**一起改（`RecoveryContext` 现无 app_id/screen 的
> 可靠来源，需补字段）；②`EmptyExperienceStore` 与旧签名随之退役。
>
> **⚠️ 接线前置（Task 2.2 评审 P3-1/P3-3 显性化，2026-10-04）**：两处
> ②③——③生产 `Executor` **没有 `find_all`**（find 语义：0 抛
> ElementNotFound、≥2 抛 AmbiguousElement；`experience_runtime_guard`
> 走 `find_all` 返回列表）——Task 2.4 二选一：给 Executor 加 `find_all`
> 或提供异常语义→计数的适配器；④`RuntimeContext.effective_risk` 必须
> 携带 **Risk 枚举**（字符串 "LOW" 会被 runtime_guard 入口 TypeError
> 拦下，接线测试需含一条「真实 Risk 枚举流经全链 → EXECUTE」正向用例）。

**Objective:** `try_experiences` 主循环落地 + `detail.kind` 细分。此任务是 Gate M2 的集成载体。

**Files:**
- Modify: `agent/recovery.py`（预留位替换为：lookup（本任务先用 updated_at 简单序，ranker 在 M3 换入）→ 逐个 Guard → 按 4.7 record_run → EXECUTE 则 perform + postcondition → 成功返回 `kind="experience"`；全部候选用尽才回落 LLM，受 P1 Budget 控制）、`runner/`（RecoveryResult.detail 增加 `recovered_kind` 并透传到 step/testcase 明细：`RECOVERED_LLM` / `RECOVERED_EXPERIENCE` / `RECOVERED_ASSERTION_TARGET`——断言目标定位漂移映射第三类，期望值路径不动，E3）、`report/html.py` + `report/junit.py`（明细加分类；聚合逻辑与退出码不变，仍 RECOVERED ≠ PASS）
- Test: `tests/unit/test_recovery_experience.py`、`tests/fault_injection/test_p2_matrix_m2.py`

**Steps:**
1. 失败测试（FakeDriver + 内存 store）：命中且执行成功 → `RECOVERED_EXPERIENCE` 且 LLM 未被调用（budget 计数 0）；Guard BLOCK → 换下一候选；全失败 → 回落 LLM（`RECOVERED_LLM`）；MISS（Screen 不符）**不写** experience_runs（E11，查库断言）；非幂等命中后超时 → 不重试、查 postcondition，确认成功才记 SUCCESS（设计 6 节）；fingerprint 观测差异记录 `screen_fingerprint_match=false`（E8 的观测面，REVALIDATION 标记在 M3）。
2. 矩阵 #1/#2/#3/#4/#15 自动化（#2 双匹配、#3 高风险屏用 FakeDriver 构造；#15 断言目标漂移 → `RECOVERED_ASSERTION_TARGET` 且期望值未被修改，E3——它只依赖本任务实现的命中 + 语义细分，与 Promotion 无关，故在此验证）；真机演示脚本 `phase0/verify_p2_m2.py`（F5 漂移：第一次 LLM → accept → 第二次 Experience 命中、LLM calls=0，trace 断言）。
3. Run → PASS → Commit: `feat(p2): recovery engine on real store + result semantics (P2-03/04/05 integration)` + tag `checkpoint-p2-m2`

---

## M3 — 验证与降级（设计 P2-06 / P2-07 / P2-08）

> **Gate M3**：Candidate→Verified→Degraded→revalidate→Verified 完整状态转换可演示；非幂等 10/10 成功仍 CANDIDATE；滑动窗口压倒总体 98%；矩阵 #5–#9/#11/#12 全过。

### Task 3.1: P2-06 — ExperienceVerifier 纯函数状态机

**Objective:** 设计 4.3–4.6 / 8.2：全部决策纯函数（E13），不依赖设备。

**Files:**
- Create: `experience/verifier.py`（`evaluate(exp, runs, policy) -> VerificationDecision`、`eligible_for_auto_verification`、`sliding_window_degrade`）、`experience/sweeper.py`（`sweep_stale_candidates`：90 天无新样本 → REJECTED(reason=STALE)）
- Test: `tests/unit/test_verifier.py`、`tests/unit/test_sweeper.py`

**Steps:**
1. 失败测试全覆盖：E4 资格（非幂等或风险 ≥ MEDIUM → 样本再漂亮也 `KEEP_CANDIDATE`，矩阵 #8/#9）；升级门槛 4.5（min_samples=10 / success_rate ≥ 0.95 / distinct_runs ≥ 3，同 run 多命中只计 1 个 run）；滑动窗口 4.6（最近 5 次失败 ≥ 2 → DEGRADE，**优先于**总体 success_rate，矩阵 #5）；判定只吃"实际被尝试"样本（E11）；STALE 90 天（矩阵 #11）；revalidate 成功 → VERIFIED(reason=REVALIDATED)；fingerprint 变化 → 事件 reason=REVALIDATION_REQUIRED，不删除不拒绝（E8，矩阵 #7），重验证通过后更新观测记录；每次转换落 `experience_state_events`。
2. Run → PASS → Commit: `feat(p2): experience verifier state machine (P2-06)`

### Task 3.2: P2-07 — ranker 多候选排序

**Objective:** 设计 5.1 `rank_experiences`。

**Files:**
- Create: `experience/ranker.py`；Modify: `agent/recovery.py`（简单序换 ranker）
- Test: `tests/unit/test_ranker.py`

**Steps:**
1. 失败测试（纯函数）：VERIFIED > CANDIDATE；同状态按 success_rate 降序；再按最近一次成功时间新→旧；无样本/REJECTED 排尾。
2. Run → PASS → Commit: `feat(p2): experience ranker (P2-07)`

### Task 3.3: P2-08 — Recovery Cache

**Objective:** 设计 7.3：进程内 LRU，命中仍强制走 Guard（E1 延伸：缓存不允许绕过安全校验）。

**Files:**
- Create: `experience/cache.py`
- Test: `tests/unit/test_experience_cache.py`

**Steps:**
1. 失败测试：cache_key = sha256(`app_id|screen_id|target_id|failure_type|screen_fingerprint`)；命中返回候选列表但 Guard 仍执行（断言 guard 被调用）；`app_build` 变化不整体清空（无失效风暴，交给 Guard/validated_builds 自然裁决）；LRU 容量边界。
2. Run → PASS → Commit: `feat(p2): recovery cache lru (P2-08)`

### Task 3.4: `mta experience` CLI（设计 13 节）

> **⚠️ 接线前置（review_p2_task31 P2-1/P3-3/P3-7 显性化，2026-10-05）**：
> 1. **E4 资格单点**（P3-7）：`verify` 子命令必须从 Repository 解析出目标元素并经 `eligible_for_auto_verification` 得到布尔，再传给 `evaluate` 的 `auto_verify_eligible`——它是 E4 的**唯一**实现且目前零生产调用面；忘调/传常量 `True` 会令整条 E4 约束为空。验收含一条矩阵测试：非幂等元素即使 100% 成功也不升级。
> 2. **revalidate 护栏已就位**（P2-1）：`revalidate` 对非 DEGRADED 现在 fail-loud（`ValueError`）；CLI 侧捕获后应给出可操作的错误信息（CANDIDATE → 走 verify；REJECTED → 走重新 ACCEPT），不得自行绕过。
> 3. **DEGRADED 的自动 REJECTED 出口暂无**（P3-3 定档）：§4.3「持续失败 → REJECTED」不在 `evaluate` 内实现（维持状态机纯函数口径）；若 CLI 要提供人工 `reject` 出口，在本任务加 `apply_outcome`（决策来自人工，不来自 evaluate），并在模块 docstring 偏离清单同步更新。

**Objective:** list/show/verify/revalidate/sweep 五个子命令（promote 在 M4 加入）。

**Files:**
- Modify: `cli/main.py`（experience 子命令组 + `--exp-db` 默认 `out/experience.db`）
- Test: `tests/unit/test_cli_experience.py`

**Steps:**
1. 失败测试：`list --status` 过滤；`verify` 对全部 Candidate 跑 Verifier 并产出决策报告（不自动执行高风险/非幂等升级）；`revalidate` 对 DEGRADED 走一次 Guard+执行并按结果转状态；`sweep` 输出清理清单（只改状态不删证据）。
2. `phase0/verify_p2_m3.py`：完整状态转换演示 + 矩阵 #5–#12 断言（#12 并发写：双线程各 record_run 100 次，计数和 = 200 无丢失——单写者验证；#6 新 build 需完整 Guard+执行成功后才追加）。
3. Run → PASS → Commit: `feat(p2): mta experience cli (P2-06)` + tag `checkpoint-p2-m3`

---

## M4 — Promotion（设计 P2-09 / P2-10）

> **Gate M4**：Verified Experience 人工 Promotion 后，后续运行在正常 `find()` 阶段命中、不再进 Recovery；DEGRADED 不自动撤销 Git 中的 Promotion；`git revert` 回滚且 Experience Store 历史不受影响；矩阵 #13/#14 全过。

### Task 4.1: P2-09 — promoter

> **⚠️ 接线前置（review_p2_task23 P3-4 显性化，2026-10-04）**：Task 2.3 起
> Candidate 的 `strategy.origin = "experience"`，而
> `repository/loader.py:28` 的 `ORIGINS = ("source", "manual")` **尚不含该值**
> （loader 第 179 行按白名单校验 → 命中即 raise）。Task 2.3 无路径把
> Candidate.strategy 喂给 loader，故不构成拦截；但本任务的 promote 要把它
> 写进 overrides 并被 loader 消费——**`ORIGINS` 扩 `"experience"` 必须与
> 写入同批落地**，否则一上线就 fail-loud。
>
> **另（review_p2_task23 P3-7）**：Experience 不携带 element type
> （recovery 行的 `candidate_type`，只用于 H15 补丁与 Guard 类型校验）。
> 若 Promotion 需要写「完整 override」（type 字段），本任务需回查 recovery
> 行或另行存储——设计 9.2 未提，落地前先定。

**Objective:** 设计 9.1–9.3 / 9.5：Proposal 生成 → 人工 Accept → 写 overrides（origin: experience）→ Git commit。

**Files:**
- Create: `experience/promoter.py`（Proposal 生成：YAML diff + evidence_summary（sample_count/success_rate/distinct_runs/validated_builds）；approve 写 `repository/overrides/elements/<Screen>.yaml` 并记 `promoted=True/promoted_commit`；9.5 人工跳过路径：非 VERIFIED 需显式 `manual_override_of_auto_policy=true` + 理由）
- Modify: `repository/loader.py`（`ORIGINS` 加 `"experience"`）、`repository/resolver.py`（合并按 origin 排序：manual > source > experience——**尝试顺序**，experience 策略排链尾而非丢弃，沿用"不可静默覆盖"规则）、`cli/main.py`（`experience promote` 子命令）
- Test: `tests/unit/test_promoter.py`、`tests/unit/test_repository_experience_origin.py`

**Steps:**
1. 失败测试：Proposal 只对 VERIFIED 生成（或走 9.5 显式人工路径）；diff 是可 review 的 YAML 补丁文本；approve 后 overrides 文件含 `origin: experience`；resolver：同元素 manual/source/experience 三源并存时尝试顺序正确且冲突记 warning；approve 后 `git log` 出现 commit（工具辅助生成、走正常 PR 流程，E10）。
2. Run → PASS → Commit: `feat(p2): promoter + origin experience merge (P2-09)`

### Task 4.2: P2-10 — Promotion 与状态解耦 + 回滚验证

**Objective:** 设计 9.4 / 9.6：两条独立时间线 + Rollback = `git revert`。

**Files:**
- Create: `tests/fault_injection/test_p2_matrix_m4.py`、`phase0/verify_p2_m4.py`

**Steps:**
1. 失败测试（矩阵 #13）：promote 后注入连续失败 → Experience DEGRADED，但 overrides YAML **未被程序改动**（工作区 diff 干净——撤销是人工决策）；#14：`git revert` promotion commit → resolver 恢复原策略、experience 库状态事件链完整可查。（#15 已前移至 Task 2.4——M4 聚焦 Promotion/回滚本身。）
2. 真机 Gate 演示：设计 18 节步骤 9–10。
3. Run → PASS → Commit: `test(p2): promotion decoupling + rollback (P2-10)`

### Task 4.3: 指标 — Experience Resolution Rate（设计 17 节）

**Objective:** 核心指标可观测（G10：随运行次数上升）。

**Files:**
- Modify: `report/html.py`（Experience Resolution Rate = Experience 解决的 Recovery / 全部 Recovery Attempts；四状态计数；Promotion Rate；lookup/cache/LLM 延迟梯度；Revalidation 成功率）、`report/junit.py`（明细带 `recovered_kind`）
- Test: `tests/unit/test_report_experience_metrics.py`

**Steps:**
1. 失败测试（聚合纯函数）：Resolution Rate 分母 = 全部 Recovery Attempts（含 Experience MISS 后回落 LLM 的）；数据源为 trace `experience_*` 事件 + experience 库。
2. Run → PASS → Commit: `feat(p2): experience metrics (design 17)` + tag `checkpoint-p2-m4`

---

## M5 — UI State Graph 与端到端演示（设计 P2-11~14 + 18/19 节）

> 独立于 Experience 主线（可与 M4 并行，不得早于 M3 收尾）。**Gate M5**：矩阵 #16；Build-to-Build Diff 报告产出；18 节 10 步全链路演示；KnowledgeSources 四接口可查。

### Task 5.1: P2-11 — Runtime Graph

**Objective:** 设计 12.1：从 P1 Trace 构建实际到达的 Screen/Transition。

**Files:**
- Create: `graph/__init__.py`、`graph/models.py`、`graph/builder.py`、`graph/storage.py`、`graph/migrations/001_graph_schema.sql`（screen_nodes / screen_transitions / graph_diffs——独立版本链、独立库文件 `out/graph.db`（CLI `--graph-db`），此时才冻结表结构，见 Task 1.2 决策）
- Test: `tests/unit/test_graph_builder.py`

**Steps:**
1. 失败测试：从 fixture trace（含 screen 转移的 steps）聚合出 nodes/transitions；visit_count / first_seen / last_seen；同一转移合并计数；**输入只有已有 Trace，不做自动探索**（E12）。
2. Run → PASS → Commit: `feat(p2): runtime graph from trace (P2-11)`

### Task 5.2: P2-12 — Source Graph

**Objective:** 设计 12.1：从 source_metadata 构建声明式导航。

**Files:**
- Modify: `graph/builder.py`
- Test: `tests/unit/test_graph_source.py`

**Steps:**
1. 失败测试：metadata 的 screens/导航信息 → `screen_nodes(source_of='source')`；metadata 无导航声明时如实为空，不推测。
2. Run → PASS → Commit: `feat(p2): source graph from metadata (P2-12)`

### Task 5.3: P2-13 — Graph Diff

**Objective:** 设计 12.2 / 12.3：五类分类 + build-to-build + `mta graph` CLI。

**Files:**
- Create: `graph/diff.py`；Modify: `cli/main.py`（`graph build [--from-trace] [--from-source]` / `graph diff --build B --base-build A` / `graph show`）
- Test: `tests/unit/test_graph_diff.py`

**Steps:**
1. 失败测试（纯函数）：ADDED / REMOVED / CHANGED / NOT_OBSERVED / UNKNOWN 五类判定；**REMOVED 仅当 Source 明确标注且 Runtime 多次确认不存在，默认 NOT_OBSERVED**（E12，矩阵 #16）；build-to-build diff 按 app_build 关联。
2. Run → PASS → Commit: `feat(p2): graph diff + mta graph cli (P2-13)`

### Task 5.4: P2-14 — Impact Analysis + UI Change Report

**Objective:** 设计 11.3 / 12.3：复用 P1 反向索引，不建第二套。

**Files:**
- Create: `graph/impact.py`（`affected_testcases(target_id)`：把 `testcase/lint.py` 的 element→testcase 索引构建抽成可复用函数后查询；UI Change Report = diff × 影响用例）
- Test: `tests/unit/test_graph_impact.py`

**Steps:**
1. 失败测试：fixture 用例集 + target → 受影响 testcase 全集；Report 含 diff 行与影响面。
2. Run → PASS → Commit: `feat(p2): impact analysis + ui change report (P2-14)`

### Task 5.5: 端到端演示 + P3 接口预留（设计 18 / 19 节）

**Objective:** 10 步演示固化为集成场景；KnowledgeSources 四查询接口定型。

**Files:**
- Create: `phase0/verify_p2_final.py`（18 节 10 步：漂移 → LLM → accept → Candidate → 二次命中 LLM=0 → Verified → AMBIGUOUS 拦截 → RISK 拦截 → 滑动窗口降级 → revalidate → promote → find() 直接命中 → `git revert`；每步 trace / 库断言）、`experience/knowledge.py`（KnowledgeSources Protocol：experience_lookup / graph_query / impact_of / trace_history——组合现有 store/graph/impact/tracer，无新存储）
- Test: `tests/integration/test_knowledge_sources.py`
- Create: `docs/p2_demo_record.md`（对照 1.2 成功标准逐条记录）

**Steps:**
1. 全链路真机演示跑通并留档。
2. 16/16 矩阵全量回归：`python -m pytest tests -q`（P1 24 项 + P2 16 项全绿）。
3. Commit: `feat(p2): e2e demo + knowledge sources (design 18/19)` + tag `checkpoint-p2-m5` + `v0.2-p2-complete`

---

## 2. 执行注意事项（给实现者/子智能体）

1. **硬约束检查表**：每任务自查 E1–E13，重点：E1（VERIFIED 仍走全量 Guard——任何 `status == VERIFIED` 就跳过校验的代码都是 bug）、E4（非幂等/风险 ≥ MEDIUM 永不自动 VERIFIED）、E5（种子只收 ACCEPT）、E6（滑动窗口压总体成功率）、E9（单写者）、E10（系统不自动改 Element Repository）、E11（MISS / RISK_BLOCKED 不计样本）、E12（NOT_OBSERVED 不猜 REMOVED）。
2. **回归底线**：每任务完成后 `python -m pytest tests -q` 全绿（P1 24 项矩阵含在内）；空 experience 库时 Recovery 行为与 P1 逐项一致。
3. **两套校验合一**（P2-04 红线）：`agent/recovery.py` 的 LLM 候选校验与 `experience/runtime_guard.py` 必须是同一函数；review 发现平行维护即打回。
4. **库文件边界**：experience.db（可变状态库，单写者）与 trace.db（append-only 流水库）职责不混；Experience 相关 trace 事件追加写 P1 trace（设计 11.1），不另建存储体系。
5. **真机集中点**：数据补齐（1.1）、Gate 演示（2.4 / 3.4 / 4.2 / 5.5）；其余全部 FakeDriver / 内存库单测（E13）。
6. **open questions**（实现中遇到即停下确认，不猜）：
   - `LocatorStrategy` 的 JSON 序列化格式与 overrides YAML 表示力的兼容性——若 YAML 放不下某类策略（如 predicate 参数），回到设计层确认扩展，不私自加字段；
   - 50+ 真实事件补齐若遇 LLM 网关不稳，允许分批并行（硬性时点见下条），不阻塞 M2 编码；
   - `distinct_runs` 的 run_id 与 P1 语义对齐（一次 `mta run` = 一个 run_id）。
7. **数据补齐与编码并行的关键路径约定**：M2 全部单测基于内存库/FakeDriver，不需要真实 ACCEPT 数据；真实事件补齐与 M2 开发并行推进，硬性时点：Gate M2 真机演示前 ≥1 条（demo 依赖真实候选）、Gate M5 最终演示前 ≥50 条（成功标准 1.2）。

## 3. 工作量与顺序摘要

| 里程碑 | 任务 | 预估 | Gate（含矩阵项） |
|---|---|---|---|
| M1 | 1.1~1.2 | 1.5d | 审计报告 + 补齐冒烟（50 条与 M2 并行） + 迁移幂等 |
| M2 | 2.1~2.4 | 4d | #1–#4/#10/#15；二次命中 LLM=0 真机演示 |
| M3 | 3.1~3.4 | 3d | #5–#9/#11/#12；完整状态转换演示 |
| M4 | 4.1~4.3 | 2.5d | #13/#14；promote 后 find() 直接命中 + revert |
| M5 | 5.1~5.5 | 3.5d | #16；10 步全链路；KnowledgeSources |

总计约 14.5 人日（不含真机/LLM 网关等待）。
