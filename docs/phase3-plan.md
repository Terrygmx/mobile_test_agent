# Mobile Test Agent — Phase 3 实施计划

> **For Hermes:** 用 subagent-driven-development 按任务派发执行；每任务两段 review（spec 合规 → 代码质量）。
>
> **与底稿的关系：** 本计划以 `docs/mobile-test-agent-phase3-plan_by_claude.md` 为底稿，于 2026-10-07 对照分支 `P3-1007`（HEAD `4cea731` = `v0.2-p2-complete` 收尾修复）真实代码逐项核实后修订。底稿的"M0 基线核实"已完成——**第 0 节即其产物**（含 path:line 证据）；底稿 M1–M5 顺延为 M2–M6，新增 M1 基础设施。底稿文件保留作参照，**以本文件为准**。
>
> **Goal:** 在 P2 基线上实现 Test Planner（P3-A）、Test Generator（P3-B）、Bounded Exploration（P3-C）、Failure Diagnosis（P3-D）、Subagent + 自主回归闭环（P3-E）五条主线（P3 设计文档 5 目标方向 / 13 硬约束 F1–F13 / 5 里程碑全部落地），达到"给定一次 Build 变化，系统能自主规划测试、补充覆盖、受限探索、诊断失败，但任何正式资产变更仍只经人工批准"。
>
> **Architecture:** 沿用 P0/P1/P2 全部分层（cli/runner/executor/repository/source/agent/llm/trace/report/experience/graph），新增 `planner/`、`generator/`、`explorer/`、`diagnosis/`、`agents/`、`candidates/`、`knowledge/` 七个包。**核心原则（设计 3.1 节"复用矩阵"）：P3 的风险判定、知识检索、LLM 预算、Schema 校验、资产审核机制全部复用 P1/P2 已有实现，不允许任何模块重新发明一套平行逻辑**——这是本计划里优先级最高的红线，高于功能进度本身。开发顺序：基础 → P3-A → P3-B → P3-C → P3-D → P3-E，前一个 Gate 不过不进下一个（Exploration 风险最高，必须在评分/校验/去重打牢之后才做）。
>
> **Tech Stack:** Python 3.11 + Pydantic 2.x + SQLite（新增 `agent.db` 独立库，复用 P2 已泛化的 `schema_migrations` 迁移机制——graph.db 已如此复用——与"按写者边界分库"原则）+ pytest（沿用 P1/P2 测试基建，无新依赖）。
>
> **工作分支:** `P3-1007`（已从 P2 完成点 `4cea731` 切出；`docs/mobile-test-agent-phase3-design.md` 已 staged 待随首个 commit 入库）。**设计基线:** `docs/mobile-test-agent-phase3-design.md` v1.0-draft。**每任务完成即 commit** + 里程碑末打 tag `checkpoint-p3-mN`。

---

## 0. P2 基线与现状差距（写计划前已核实，替代底稿 M0）

| 设计要求（P3 设计 3.1 复用矩阵） | 现状（已核实，path:line 为当前工作区） | P3 差距与动作 |
|---|---|---|
| F1 风险判定 | `executor/policy.py:108` `effective_risk(step, element, screen, env, ...)`（取 max，启发式仅在 element 未声明时 bump，H17）；Guard：`executor/guard.py:102-160`（`Guard.check(ctx)`，拦截抛 `GuardViolation:89`，`GuardContext:33` frozen）；`runner/runner.py:163-180` 每步**先于设备**调 Guard；`experience/runtime_guard.py:202` 复检；`compute_effective_risk` 别名在 `experience/runtime_guard.py:43` | **直接复用**。探索/生成/Dry Run 的动作构造同一 `GuardContext` 走同一 `check`；`planner/risk.py` 只是转发壳 |
| F3 知识检索 | `experience/knowledge.py:37-48` `KnowledgeSources` Protocol 四方法（`experience_lookup / graph_query / impact_of / trace_history`）；实现 `P2KnowledgeSources:51-111`（`__init__(*, experience_store, trace_db, cases=(), app_build=None)`）；**trace_history filters 白名单仅 `app_build`/`limit`（:98-103）** | **直接复用 + 小改**：filters 白名单扩 `screen_id / testcase_id / failure_type`（保持只读、未知键 fail-loud）；`knowledge/retrieval.py` 只做统一装配入口，不新增检索方法 |
| F4 LLM 预算 | `llm/budget.py`：`BudgetConfig:27-35`（max_calls_per_run=10 / per_testcase=3 / breaker_consecutive_failures=3）；`LLMBudget.__init__(max_calls_per_run=...) :39`、`try_acquire :71`、`record_failure :89`、`broken / calls_used`；纯内存无 IO | **直接复用**：每 Agent 任务独立实例化；`max_llm_calls` ↔ `max_calls_per_run`（任务即 run），熔断不重写 |
| F5 Candidate Schema | `testcase/schema.py`：pydantic v2、`extra="forbid"（:175）`、`SCHEMA_VERSION="0.2"（:15）`、`parse_testcase_dict :191`；lint 引擎 `testcase/lint.py` `lint(testcases, repo, secrets)` **走裸 dict**；CLI `cmd_lint :409-441`（ERROR→exit 3） | **Modify schema.py** 加可选字段（`status / generated_by / generation_evidence`，默认 None）——不能在 generator/ 平行定义模型（extra=forbid 会直接拒绝未知键，且违反 F5"同一 Schema"）；lint 对新字段不报错需回归确认 |
| F6 审核机制 | recovery_reviews：`tracer/storage.py` `REVIEW_STATUSES:75`（PENDING/ACCEPT/REJECT）、`create_review:619 / decide_review:653`；领域逻辑 `agent/review.py` `decide_review:161-191 / validate_accept:70`（reject 必须 note）；promotion_proposals：`experience/models.py:243-260`；CLI `mta review` `cli/main.py:168-207` | 沿用同范式新建 `candidates/review.py`（统一状态机 + fail-loud decide），三类型一张 CLI；**禁止**新造第三种审核形状 |
| F7 环境隔离 | `EnvKind`（sandbox/staging/production）`executor/guard.py:26-30`；`DeviceType`（simulator/real_device）`environment/capabilities.py:18-21`；启动校验在 `cli/main.py:85-96`（--env-kind）与 `cmd_run :451-459`（production 需 `--allow-production`）；**`policy.yaml` 全仓不存在**（Guard 的 blocked_targets 只是构造参数，未接任何配置文件） | **新建 `config/policy.yaml` + 加载器 + Agent 命令启动校验**（production 下一票否决自主任务，无豁免 flag；exploration 仅 sandbox+simulator）；文件缺失 → 内置默认值，P2 行为不变 |
| F8 Fingerprint | `source/screen.py:107-131` `screen_fingerprint(page_source: str) -> str | None`（可见元素 name/label 规范化去重排序 → sha256 前 16 hex；不可解析返回 None）；调用方 `agent/recovery.py:175,667`、`experience/verifier.py:194`、`experience/store.py:521` | 抽共享哈希原语（规范化 + sha256[:16]），`test_fingerprint` / bug `dedup_key` / loop window 复用；**只做精确哈希相等，禁一切相似度/模糊匹配** |
| F9 非幂等阶段判定 | `FailurePhase` `executor/policy.py:41-46`；`retry_decision :135-173`、`max_attempts_for :176`；阶段标记在 `runner/runner.py:198-247`（find 失败→PRE_DISPATCH、perform 失败→POST_DISPATCH）；postcondition 真实执行 `cli/pipeline.py make_postcondition_checker:1256`（接线 :1168-1169） | **直接复用**。Reproducer 调 `retry_decision` + postcondition checker，不重新推断"动作是否已发出" |
| Impact / UI Graph | `graph/models.py:92` 类名是 **`RuntimeGraph`**（设计文中 "ScreenGraph" 不存在）；`build_runtime_graph builder.py:157`；`graph/impact.py` `affected_testcases:83 / build_change_report:182 / cases_touching_screen:103`；element→testcase 索引原语在 `source/coverage.py`（`collect_case_refs / ref_to_cases`，P2 Task 5.4 收敛产物） | **直接复用**；`planner/impact.py` 只是"changed_files → 受影响 target → impact_of"的**映射适配层**，不建第二套索引 |
| Repository 变更落地 | `experience/promoter.py` `generate_proposal:87-133`（只落 PENDING proposal）、`approve_proposal:215-298`（写 overrides + git commit + 失败回滚，无跳过 proposal 直写路径）；`_git :301` | `create_promotion_proposal` 工具最终调**同一 promoter**；正式 TestCase 落盘沿用同模式（见 Task 3.5） |
| 设计 8.2 APP_CRASH 判定 | **不存在**：`APP_CRASH` 仅是 `tracer/storage.py:45-57` FAILURE_TYPES 允许清单里的字符串（:48），全仓无任何检测逻辑 | **需新增** `crash_detected`（session/driver 存活 + infra_events/日志特征 → failure_type=APP_CRASH），归 M5 显式任务——底稿"复用 P1 APP_CRASH 判定"不成立 |
| 设计 11 agent.db | 不存在；但迁移执行器已泛化：`experience/schema_migrations.py` `migrate(conn, migrations_dir, pkg)`（graph.db 即如此复用，`graph/storage.py:29,53-54`） | 新建 `agents/storage.py`（单写者，沿 E9）+ `agents/migrations/` 增量链：001 agent_tasks/agent_trace/test_plans；002 test_candidates / 003 discovery_events / 004 bug_candidates 随 M3/M4/M5 各自定型（P2 Task 1.2"表结构随实现定型"的教训） |
| 设计 12 CLI | argparse：`build_parser() cli/main.py:27`，现有八组 lint/run/repo/source/review/report/experience/graph；共享 helper `_resolve_app_build:356 / _load_repository:399` | 新增 plan/generate/explore/diagnose/candidate/agent 六组，沿用同注册模式 |
| Planner 输入 | **全仓无 git diff 工具**（仅 `git rev-parse --short HEAD`：`tracer/recorder.py:107` 等）；有 build 间 metadata diff：`source/build_diff.py diff_builds:161 -> SourceDiffReport`（CLI `mta source diff`） | **新增** `source/git_diff.py`（changed_files）；changed_files→targets 映射依赖 source_metadata 的文件级归属，缺失时回退 `diff_builds`（open question #2） |
| Executor 能力 | `executor/executor.py`：find :64 / find_all :79 / tap :96 / input :99 / swipe :118 / **screenshot :128 / page_source :132**；用例动作 Literal：launch_app/terminate_app/tap/input/swipe/back（`testcase/schema.py:119`）+ wait_for + assertion（exists/not_exists/text_equals/text_contains/element_count/enabled/disabled :141-149）；LLM 动作白名单 `{tap,input,swipe,back,wait}` `llm/parser.py:28` | P3 工具层把设计 10.1 的工具名映射到**既有原语**（wait→wait_for、assert_*→断言求值、get_current_screen→page_source+fingerprint），不新增执行原语 |
| LLM 封装 | `llm/provider.py` `complete(prompt, timeout=120) -> str :25-41`（temperature=0），**无 JSON mode**；结构化靠 prompt 契约（`llm/prompt.py:94`）+ 严格解析（`llm/parser.py parse_llm_output:65`、raw_decode :51、额外字段记 ignored_fields） | 沿用 prompt+parser 模式；不扩 provider（若需真 JSON mode 另立项，见执行注意事项 #7） |

**环境事实**（沿用 P1/P2，已确认）：模拟器 iPhone 14 UDID `AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E`（iOS 18.5）；Appium 3.7.0 需 `DEVELOPER_DIR`；LLM 网关 env：`LLM_BASE_URL / LLM_MODEL / LLM_API_KEY`（`llm/provider.py:19-23`）；演示 App `ios_demo/LoginDemo`；**正式 TestCase 即 `suites/<suite>/*.yaml`**（account/regression/search/smoke）；库文件 `out/trace.db`、`out/experience.db`、graph.db（`--graph-db`）；P3 新增 `out/agent.db`（CLI `--agent-db` 可覆盖）。

**P0/P1/P2 行为保留原则**：P0 的 8 项验收、P1 的 24 项故障矩阵、P2 的 16 项故障矩阵必须持续全绿（回归底线）；**P3 未启用时（新命令不调用、无 policy.yaml、agent.db 不存在）系统行为必须与 P2 完全一致**——这是 F1–F13 之上最基本的安全网。

### 0.1 底稿修订对照（为什么和 plan_by_claude.md 不一样）

| # | 底稿假设 | 核实结果 → 本计划修订 |
|---|---|---|
| 1 | M0 待做（0.5d 核实任务） | 核实已完成，第 0 节即产物；M0 取消 |
| 2 | agent.db 在 M5（原 Task 5.1）才建 | **提前到 M1**：test_plans（M2 要写）、agent_tasks/agent_trace（M3 起记录）都是前置；迁移链放 `agents/migrations/`，按里程碑增量（001→004） |
| 3 | `ALLOWED_TOOLS` 在 M5（原 Task 5.2）才建 | **提前到 M1**：Explorer（M4）、Dry Run（M3）在 M5 之前就要执行动作，F2/F12 的唯一通路必须先于第一个会碰 App 的模块存在 |
| 4 | F7 靠"已有多份 policy 加载逻辑" | **policy.yaml 不存在**——M1 新建（含探索预算、escalation N、evidence/planner 权重段），缺失时默认值 |
| 5 | Candidate 扩展用 `generator/models.py` 继承 TestCase | 改为 **Modify `testcase/schema.py`**（extra=forbid 下平行模型会拒绝新键，且违反 F5"同一 Schema"）；设计 6.2 示例 `schema_version: 0.1` 为笔误，现行 0.2（勘误回填设计） |
| 6 | "复用 P1 APP_CRASH 判定" | 判定**不存在**，只有 FAILURE_TYPES 词表——M5 新增 crash_detected（复用的是词表与 trace 数据，不是函数） |
| 7 | "ACCEPT 后调用 P1 Repository 的正式写入路径" | 该路径**不存在**（正式用例是人工提交的 `suites/<suite>/*.yaml`）——ACCEPT 落盘沿用 promoter 模式（人工 ACCEPT 授权 → 工具写文件 + git commit + 失败回滚，Task 3.5） |
| 8 | "test_fingerprint 断言调用了与 screen_fingerprint 同一个函数" | 两函数输入形状不同（XML page_source vs step 序列），字面同一函数不可行——修订为**共享同一哈希原语**（抽 `source/hashing.py`，screen_fingerprint 重构后行为不变），红线不变：禁相似度算法 |
| 9 | Reproducer "复用 `agent/recovery.py` 的阶段判定" | 阶段判定的本体在 **`executor/policy.py`（FailurePhase/retry_decision）+ `cli/pipeline.py` postcondition checker**；recovery.py 只是消费方——改为复用本体 |
| 10 | 13 项"验收矩阵"（约束→验证点） | 扩为 **18 项具体故障注入矩阵**（P2 惯例），F1–F13 映射保留（见第 1 节） |

---

## 1. 里程碑总览与依赖

```text
M1 基础与安全边界(2d) ─► M2 Planning(2d) ─► M3 Generation(3.5d) ─► M4 Exploration(4d) ─► M6 Subagent+闭环(4d)
                                                        │
                                                        └──► M5 Diagnosis(3.5d) ──┘
                                        （M5 与 M4 可并行：都只依赖 M1+M3；
                                          M6 收口必须在 M4/M5 全过之后）
```

- 每个 Gate 不通过不进下一里程碑。
- 任务内 TDD：先写失败测试 → 最小实现 → 过测试 → commit。
- **M4 全部与 M3 Dry Run、M5 Reproducer 的设备执行一律 `env.kind=sandbox` + simulator**；其余全部 FakeDriver / 内存库单测（F13）。沙箱集中点：Task 3.4 / 4.6 / 5.4 / 5.6 / 6.7。
- **F1–F13 → 矩阵项映射**：F1→#13；F2→#2/#17；F3→M2–M5 各任务 import 级断言（只经 KnowledgeSources，不直接 SQL）；F4→#10/#18 与各任务 budget 单测；F5→#7；F6→M3/M5 的审核一致性测试；F7→#1/#9；F8→#8/#12/#16；F9→#15；F10→#14/#16；F11→#11；F12→#18；F13→全程纯函数单测。

### P3 故障注入矩阵（18 项，分配到各 Gate）

| # | 注入场景 / 验证点 | 约束 | 里程碑 |
|---|---|---|---|
| 1 | `env.kind=production` 下一切自主命令（plan/generate/explore/diagnose/agent）**启动即拒绝**（exit≠0，无豁免 flag，非运行时拦截） | F7 | M1 |
| 2 | 工具表红线：注册表键集 == `ALLOWED_TOOLS`；禁用工具名（delete_testcase/modify_expectation/modify_verified_experience/modify_repository_directly/production_api_call/real_payment/arbitrary_shell/arbitrary_python）一个都不存在；运行时调未注册工具 → 拒绝（**此测试进 CI 门禁**） | F2 | M1 |
| 3 | agent.db 迁移幂等可重放；单写者（并发写不丢，沿 P2 #12 手法） | E9 惯例 | M1 |
| 4 | policy.yaml 缺失 → 内置默认值，现有八组 CLI 行为与 P2 逐项一致 | 回归底线 | M1 |
| 5 | LLM 输出试图重排确定性评分结果（非同分 tie-break）→ 丢弃重排、审计留痕、保留确定性顺序 | F13/设计 5.2 | M2 |
| 6 | git diff 不可用 / changed_files 为空 → fail-loud 或产出空 Plan 并明示原因（不编造依据） | 设计 5.1 | M2 |
| 7 | 候选 Schema/lint 不过 → 拒绝入库 test_candidates（fail-loud，不静默丢弃） | F5 | M3 |
| 8 | Test Fingerprint 命中已有用例/未决候选 → SKIP 且留痕（skip 原因可查） | F8 | M3 |
| 9 | Dry Run 在非 sandbox（或 real_device）请求 → 拒绝执行 | F7 | M3 |
| 10 | LLM INVALID_OUTPUT 连续 N 次 → 熔断（复用 breaker），不自由发挥 | F4 | M3 |
| 11 | 探索四类预算任一耗尽 → `*_BUDGET_EXCEEDED` 正常退出，**不自动放宽重试** | F11 | M4 |
| 12 | `max_repeated_state` 触发 → 停止（loop detection 生效） | F8/F11 | M4 |
| 13 | HIGH+ 风险动作被 Guard 拦截 → 写审计不执行；连续 N 次拦截 → ESCALATED（附上下文） | F1/9.2 | M4 |
| 14 | crash 注入 → APP_DEFECT 规则命中 + crash_detected 证据加分，**不调用 LLM** | F10/8.2 | M5 |
| 15 | 非幂等 POST_DISPATCH 失败且 postcondition 成立 → 不计复现次数、reproducible 不加分 | F9/8.4 | M5 |
| 16 | evidence_score < 归因阈值 → attribution 保持 UNTRIAGED；dedup_key 相同 → 合并 DUPLICATE | F10/F8 | M5 |
| 17 | 端到端全程**正式资产零程序修改**（overrides/Verified/期望值/正式 TestCase：git status 干净 + 库断言） | F2 总验 | M6 |
| 18 | Subagent 绕过工具层直调 Executor → 拒绝；三 Subagent 预算互不挤占 | F12/F4 | M6 |

---

## M1 — 基础与安全边界（设计 §4/§10/§11）

> **Gate M1**：agent.db 迁移幂等、单写者生效；policy.yaml 加载 + production 一票否决（矩阵 #1/#4）；工具注册表静态断言过且进 CI（矩阵 #2）；`source/git_diff.py` 对真实仓库产出正确 changed_files。P0/P1/P2 全量回归绿（矩阵 #3/#4）。

### Task 1.1: P3-01 — agent.db + Agent 数据模型 + 资产权限分级

**Objective:** 设计 11 节（首批表）+ 4 节 `AssetTier/TOOL_ASSET_TIER` 落地为代码与 schema。

**Files:**
- Create: `agents/__init__.py`、`agents/models.py`（`AssetTier` 枚举 + 设计 4 节 `TOOL_ASSET_TIER` 映射（执行类工具归属：`run_candidate_test`→CANDIDATE（写 dry_run_status）、`run_testcase`/`get_*`/`assert_*`/`screenshot`→READ_ONLY）；`AgentTaskState` 枚举常量（IDLE/PLANNING/EXECUTING/OBSERVING/DECIDING/VERIFYING/ANALYZING/RECOVERING/ESCALATED，M6 才强制转换表）；`AgentTask`/`AgentTraceEntry` 模型）
- Create: `agents/storage.py`（SQLiteAgentStore：单写者 `BEGIN IMMEDIATE` + 进程内锁（沿 E9）；`create_task / update_task_state / append_trace / save_plan / list_plans`；Reader 并发安全；复制 P2 泛化迁移执行器用法）
- Create: `agents/migrations/001_agent_schema.sql`（`agent_tasks / agent_trace / test_plans` 三表，字段照设计 11 节）
- Test: `tests/unit/test_agent_models.py`、`tests/unit/test_agent_store.py`

**Steps:**
1. 失败测试：TOOL_ASSET_TIER 不含任何 PRODUCTION/CRITICAL 工具（红线断言，与 Task 1.3 的 CI 门禁呼应）；agent_tasks.state 仅接受枚举值；agent_trace 追加式（不 UPDATE）；test_plans.tasks_json 往返序列化；迁移幂等 + 并发写不丢（矩阵 #3）。
2. **test_candidates / discovery_events / bug_candidates 三表不在 001 建**——随 M3/M4/M5 各自实现定型（迁移 002/003/004），P2 Task 1.2 教训。
3. Run → PASS → Commit: `feat(p3): agent db + asset tiers + task models (P3-01)`

### Task 1.2: P3-02 — policy.yaml + 启动校验（F7）

**Objective:** 设计 7.2/7.4/10 节：环境与预算约束集中一处，启动时校验而非运行时拦截。

**Files:**
- Create: `config/policy.yaml`（首次入库；段结构：`autonomous.enabled`、`exploration.{max_steps:100, max_duration_seconds:600, max_llm_calls:20, max_repeated_state:3}`（设计 7.2 初版值）、`escalation.guard_blocks_before_escalate:3`、`planner_weights`（M2 初版）、`diagnosis_evidence_weights` + `evidence.attribution_threshold:10`（M5 初版，设计 8.3 权重原文））
- Create: `agents/policy_config.py`（`load_policy(path) -> PolicyConfig`：文件缺失 → 全默认值；未知键 fail-loud）
- Modify: `cli/main.py`（自主命令组公共前置 `_check_autonomous_env(args)`：解析 env_kind（复用 `--env-kind` 既有语义 `cli/main.py:85-96`）→ `production` 一票否决 **无豁免 flag**（区别于 `cmd_run:451-459` 的 `--allow-production`）；DeviceType 校验留给 `mta explore`（M4））
- Test: `tests/unit/test_policy_config.py`、`tests/fault_injection/test_p3_matrix_m1.py`（矩阵 #1/#4）

**Steps:**
1. 失败测试：production + 自主命令 → exit≠0 且消息含 F7 指引；policy.yaml 缺失 → 默认值生效且 P2 既有命令行为不变（矩阵 #4）；`autonomous.enabled=false` → 同样拒绝。
2. Run → PASS → Commit: `feat(p3): policy.yaml + autonomous env gate (P3-02)`

### Task 1.3: P3-03 — Agent 工具层（F2/F12 的唯一通路）

**Objective:** 设计 10/10.1 节：`ALLOWED_TOOLS → TOOL_ASSET_TIER → Executor+Guard` 唯一分发层；被禁工具**在构造上不存在**。

**Files:**
- Create: `agents/tools.py`（`ALLOWED_TOOLS` frozenset（设计 10.1 全集）；`AgentToolkit`：持有 executor/guard/knowledge/agent_db/budget；`call(tool, **kwargs)`：查表（未注册 → `ToolViolation`）→ tier 标注 → 执行类构造 `GuardContext`（`effective_risk`）→ `guard.check` → 执行 → 观测返回；BLOCK → 写 agent_trace（guard_result=BLOCK，**审计不静默**）；工具名 → 既有原语映射：`get_current_screen`=page_source+screen_fingerprint+node 匹配、`get_ui_tree`=page_source、`wait`=wait_for、`assert_exists/assert_text`=find+断言条件求值、`screenshot`=executor.screenshot:128、`run_testcase/run_candidate_test`=M3 Dry Run 入口（M3 前调用 fail-loud "not wired yet"）、`create_promotion_proposal`=转调 `experience/promoter.py`）
- Test: `tests/unit/test_agent_tools.py`、（CI 门禁）`tests/unit/test_tool_allowlist_gate.py`

**Steps:**
1. 失败测试：注册表键集 == ALLOWED_TOOLS；禁用名八项逐一断言不存在（矩阵 #2，**进 CI 门禁**）；执行类动作 risk>LOW → BLOCK + agent_trace 有记录（Audit）；同一输入下工具层 Guard 结论与 `runner.run_step` 一致（FakeDriver 双向，沿 P2 Task 2.2 手法）；未接线工具调用 → 明确报错不静默。
2. Run → PASS → Commit: `feat(p3): agent toolkit single dispatch layer (P3-03)`

### Task 1.4: P3-04 — git diff / changed_files 工具

**Objective:** 设计 5.1 `PlannerInput.changed_files` 的数据源（全仓现状：无任何 git diff 工具，见第 0 节）。

**Files:**
- Create: `source/git_diff.py`（`changed_files(repo_root, base, head=None) -> GitChangeSet`：`git diff --name-status base..head`，新增/修改/删除分类；非 git 目录/坏 ref → `GitDiffError` fail-loud（矩阵 #6 的 M2 侧消费））
- Modify: `experience/promoter.py` 的 `_git`（:301）抽为共享 `source/vcs.py run_git()`，promoter 改调它（行为不变，P2 回归保证）
- Test: `tests/unit/test_git_diff.py`（tmp git 仓库 fixture）

**Steps:**
1. 失败测试：新文件/修改/删除/重命名分类正确；base 不存在 → fail-loud；head 缺省 = HEAD。
2. Run → PASS → Commit: `feat(p3): git diff changed files util (P3-04)` + tag `checkpoint-p3-m1`

---

## M2 — Test Planning（设计 5 节 / P3-A）

> **Gate M2**：给定一次 Build 改动，`mta plan` 输出可解释的优先级排序（`reasons` 非空、依据可追溯），评分纯函数可单测；LLM 重排被拒（矩阵 #5/#6）。

### Task 2.1: P3-05 — planner 数据模型 + Plan 持久化

**Objective:** 设计 5.1 节模型落地并存 agent.db。

**Files:**
- Create: `planner/__init__.py`、`planner/models.py`（`PlannerInput / TestPlanTask / TestPlan`，`schema_version="0.1"`——这是 **TestPlan 自己的版本**，与 TestCase 的 0.2 无关；`reasons` 非空校验）
- Modify: `agents/storage.py`（`save_plan / get_plan` 落 `test_plans` 表）
- Test: `tests/unit/test_planner_models.py`

**Steps:**
1. 失败测试：序列化/反序列化往返；`reasons` 为空列表 → 拒绝创建（可解释性是 F13 的落地，不是裸分数）；`source` 仅 existing/generated_gap 两值。
2. Run → PASS → Commit: `feat(p3): planner models + plan persistence (P3-05)`

### Task 2.2: P3-06 — KnowledgeSources 装配 + filters 扩展 + priority_score 纯函数

**Objective:** 设计 5.2 节确定性打分；F3 装配入口定型。

**Files:**
- Create: `knowledge/__init__.py`、`knowledge/retrieval.py`（`build_knowledge(...) -> KnowledgeSources`：组合 experience_store/trace_db/graph store/cases/app_build 为一个 `P2KnowledgeSources` 实例——**只装配，不新增检索方法**）
- Modify: `experience/knowledge.py`（`trace_history` filters 白名单 :98-103 扩 `screen_id / testcase_id / failure_type`；仍只读、未知键 fail-loud；既有调用不受影响）
- Create: `planner/prioritizer.py`（`priority_score(tc_meta, impact, history, risk) -> int` 纯函数；初版权重：impact 命中 +40、历史失败率 ×30、risk floor（CRITICAL≥90 / HIGH≥70，设计 5.3"不管改动大小"）——**标注初版，待真实数据校准**；同分次序 = testcase_id 字典序，LLM tie-break 仅在同分组内允许）
- Create: `planner/risk.py`（转发 `executor.policy.effective_risk`，取用例所涉元素 max；**不重新实现**）
- Test: `tests/unit/test_prioritizer.py`、`tests/unit/test_knowledge_retrieval.py`

**Steps:**
1. 失败测试：评分公式纯函数化（F13）；CRITICAL 用例无论 impact/history 多低都进顶部区间；`planner/risk.py` 与 P1 对同一输入同结果（import 级断言 + 行为断言）；filters 扩展后旧白名单行为不变、未知键仍 fail-loud。
2. Run → PASS → Commit: `feat(p3): knowledge assembly + deterministic priority score (P3-06)`

### Task 2.3: P3-07 — changed_files→targets 映射 + Planner 编排 + LLM 解释层

> **⚠️ 接线前置（Task 2.1 留的，review_p3_task21 小观察 4）**：**`plan_id` 的来源要在
> 本任务拍板**——`test_plans` 的主键是 `plan_id`，`save_plan` 是 upsert（同 id 覆盖），
> 所以「重存算不算新计划」**完全取决于 plan_id 是否稳定**：
> ① 每次随机生成（uuid）→ upsert **永不发生**，库随调用次数线性增长；
> ② 由 `app_build + git_commit`（+ 可选的 base_build）派生 → 同一 build 重复 plan 会**覆盖**，
> 但「同一 build 跑了两次想留两份」就不可能。
> **✅ 已拍板（Task 2.3 实现时）**：**内容寻址**——唯一生成点
> `planner/models.py::plan_id_for(app_build, git_commit, changed_files)`
> = `"plan_" + sha256(三者) [16 hex]`。理由：`save_plan` 是 upsert，所以
> ① 随机 uuid → upsert 永不发生、库线性增长，且同一输入两次跑出两份「内容可能不同」
> 的 Plan（LLM 解释层不稳定），无法回答「这个 build 的 plan 是哪一份」；
> ② 只由 `app_build + git_commit` 派生 → 同一 build 换 base（改动集不同）会**静默
> 覆盖**前一份。内容寻址两头都躲开：同输入幂等、不同输入不互相覆盖。
> `changed_files` 先排序再入哈希（集合相同 → id 相同）。

> **⚠️ 接线前置（Task 2.2 留的，review_p3_task22 P3-3 / 小观察 1、5）**——三条，都影响
> `planner/planner.py` 的写法：
> 1. **`KnowledgeSources` 只能经 `knowledge.build_knowledge` 取**：不许在编排层直接
>    `P2KnowledgeSources(...)`（那正是 F3「单一入口」要防的绕过；守卫
>    `tests/unit/test_knowledge_retrieval.py::test_p2_knowledge_sources_is_constructed_in_one_place`
>    扫全仓的真实构造调用，命中集只许 `knowledge/retrieval.py`）。本任务也是
>    `build_knowledge` 的**第一个生产调用者**——在那之前那条「单一入口」只是约定。
> 2. **`priority_score` 的入参是硬契约**：`risk` 只收 `Risk`（传字符串/裸 int/None →
>    `ValueError`，因为 `RISK_FLOOR.get(非 Risk)` 返回 `None` 会让 CRITICAL 的 floor
>    **静默失效**）；`history` 只收 `[0,1]` 的数值（`Risk` 是 `int` 枚举，**不能**当失败率传）。
>    编排层从 LLM / policy / CLI 拿到的值必须先过类型，别直接喂。
> 3. **`impact` 传 `set`/`frozenset`**：`priority_score` 对已去重的集合不重建，而编排层是
>    **按用例循环调用**（N 个用例 × 同一个 impact 集合）——传 list 会变成 O(N×|impact|)。
>    在循环外 `frozenset(...)` 一次即可。

**Objective:** 设计 5.3 节 Git Diff → Impact → TestPlan；LLM 只解释/打平，不决定顺序。

**Files:**
- Create: `planner/impact.py`（`changed_targets(changes, metadata) -> tuple[str,...]`：source_metadata 文件级归属映射 → target ids → `knowledge.impact_of()`；**metadata 缺文件级归属时回退 `source/build_diff.py diff_builds` 的 drift 面**（实现时登记，open question #2）；索引本体是 `source/coverage.py` + `graph/impact.py`，此处只是适配层）
- Create: `planner/planner.py`（编排：PlannerInput → impact/history/risk → score → sort → LLM 解释 → TestPlan）
- Modify: `llm/prompt.py`（新增"为排序结果写 reason"模板，复用 `complete()` + `parse_llm_output` 的 JSON 契约模式）
- Test: `tests/unit/test_planner_e2e.py`（纯内存 fixture，不依赖真实 git）

**Steps:**
1. 失败测试：LLM 输出修改非同分组排序 → 判违规：丢弃 LLM 结果、保留确定性顺序、审计留痕（矩阵 #5）；LLM budget 耗尽 → reasons 降级为规则摘要（impact/history/risk 数值模板拼接），Plan 仍产出；changed_files 为空 → 空 Plan 且明示（矩阵 #6）。
2. Run → PASS → Commit: `feat(p3): diff-driven planner + llm rationale (P3-07)`

### Task 2.4: P3-08 — `mta plan` CLI + Gate 演示

> **⚠️ 接线前置（Task 1.2 拍板，2026-10-08；review_p3_task12 P3-3）**：`mta plan` 是
> **第一个自主命令**，`_check_autonomous_env` 的 CLI 端到端接线从它开始。三件事：
> 1. **`env_kind` 的来源**——给自主命令加 `--env-kind`（`choices` 与 `run` 同款、
>    `default="sandbox"`），并在 `cmd_*` 第一行用**唯一解析点**
>    `env_kind=_resolve_env_kind(args)`（`cli/main.py`，`run` 也在用）。**不许**
>    硬编码 `env_kind="sandbox"`——那会让 F7 退化成**永真**（判据还在、测试还绿、
>    但永远不拒）。守卫 `tests/fault_injection/test_p3_matrix_m1.py::
>    test_every_wired_autonomous_command_passes_the_gate` 会在 `cmd_plan` 出现的
>    那天检查这条。
> 2. **`policy` 的来源**——**✅ 已拍板（Task 2.4 实现时）**：**加了 `--policy`**，
>    语义按 P3-2：不给 → `load_policy()`（读相对 CWD 的 `config/policy.yaml`，读不到
>    → 内置默认值，矩阵 #4）；给了 → 该路径，**不存在即报错**（`PolicyConfigError`）。
>    实现是 `load_policy(args.policy) if args.policy is not None else load_policy()`
>    —— `is not None` 而非 `if args.policy`：`--policy ""` 必须**报错**而不是静默
>    退回默认解析（原「二选一」的另一半是直接用 `load_policy()`，会丢掉操作员
>    指定策略的能力）。以下是原措辞（保留供对照）：`--policy` 这个 flag **此前不存在**（Task 1.2 的
>    docstring 已改成将来时）。接线时二选一：加 `--policy`（显式路径，按 P3-2 的
>    语义**不存在即报错**），或直接用 `load_policy()` 的默认解析（相对 CWD 的
>    `config/policy.yaml`，读不到 → 内置默认值）。**拍板后回填本节**。
> 3. **调用顺序**：`cmd_plan` 的第一行过前置（`load_policy` → `_check_autonomous_env`），
>    过了才做别的事——F7 是「启动时校验」，不是「跑一半才拦」。
> 4. ~~**`AgentToolkit` 的装配点**（Task 1.3 交付，尚未接线）~~ → **已证伪（Task 2.4
>    实现时）**：`mta plan` **不碰 App**（离线规划：git diff + metadata + 用例集 + 历史），
>    装配 `AgentToolkit` 会是一个**死对象**（还要为它建真机会话）——所以**没有**在
>    `cmd_plan` 里装配。真正的装配点是**第一个会驱动设备的命令**（M6 的自主闭环，或
>    M3 的 Dry Run）。届时按需注入：`guard` 必填；`executor` / `locator_for`（**用
>    `SessionPipeline._locator_for` 那一份**，别在装配处再写一遍 `{"type","value"}`
>    转换）/ `repository` / `device_session`（`back` 走它）/ `wait_engine` /
>    `assertion_engine` / `experience_store`；**`agent_db` + `task_id` 是 BLOCK 的审计
>    落点**（不配就在第一次 BLOCK 时抛 `ToolkitAuditError`，不静默丢弃）。
>    ⚠️ 同时更正 Task 1.3 的两处 docstring 声称（「装配点归 Task 2.4」是**推测**）。

**Objective:** 设计 12 节 CLI。

**Files:**
- Modify: `cli/main.py`（`plan --build <id> --base-build <id>`：装配 PlannerInput（`source/git_diff.py` + `_resolve_app_build`）→ planner → 存 test_plans → 人类可读 + `--json` 结构化输出；**加 `--env-kind`**，第一行过 `_check_autonomous_env`）
- Create: `phase0/verify_p3_m2.py`（对 ios_demo 构造一次真实改动（改一个 Swift 文件 + commit）→ Plan 输出：reasons 非空、CRITICAL 用例居顶、plan_id 可查库）
- Test: `tests/unit/test_cli_plan.py`

**Steps:**
1. 失败测试：参数校验；输出双格式；plan 入库可回查。
2. Gate 演示跑通留档。
3. Run → PASS → Commit: `feat(p3): mta plan cli (P3-08)` + tag `checkpoint-p3-m2`

---

## M3 — Test Generation（设计 6 节 / P3-B）

> **Gate M3**：对已知 Coverage Gap 生成 Candidate，Schema/语义/去重/Dry Run 全过，统一审核可 Accept 并落为正式用例；矩阵 #7–#10 全过。

### Task 3.1: P3-09 — TestCase Schema 扩展（F5）

> **⚠️ 勘误（对照设计 6.2）**：设计示例 `schema_version: "0.1"` 与 P1 现行 `"0.2"`（`testcase/schema.py:15`）不符——**以 0.2 为准**，勘误回填设计文档。扩展方式是 **Modify 既有模型**，不是 `generator/models.py` 平行继承（extra=forbid 下平行模型拒绝新键，且违反 F5"同一 Schema"）。

**Objective:** Candidate Test = P1 TestCase + 溯源字段。

**Files:**
- Modify: `testcase/schema.py`（新增可选字段：`status: Literal["CANDIDATE"] | None = None`、`generated_by: str | None = None`、`generation_evidence: GenerationEvidence | None = None`（嵌套模型：coverage_gap/bug_history/source_refs 列表）；`extra="forbid"` 不动；SCHEMA_VERSION 不变）
- Test: `tests/unit/test_testcase_candidate_fields.py`、`tests/unit/test_lint_candidate_compat.py`

**Steps:**
1. 失败测试：带新字段的 YAML 经 `parse_testcase_dict` 通过；不带字段的既有用例解析结果逐字节不变；`mta lint` 底层 `lint()`（裸 dict 路径）对新字段零告警；正式 suites/ 现有用例全量 lint 结果不变。
2. Run → PASS → Commit: `feat(p3): candidate fields on p1 testcase schema (P3-09)`

### Task 3.2: P3-10 — Coverage Gap 计算

**Objective:** 设计 6.3 节：Source Graph 有、现有用例从未触发的 Transition。

**Files:**
- Create: `generator/__init__.py`、`generator/coverage.py`（`coverage_gap(graph: RuntimeGraph, existing_tests) -> list[UncoveredTransition]` 纯函数；graph 经 `knowledge/retrieval.py` 的 `graph_query`）
- Test: `tests/unit/test_coverage_gap.py`

**Steps:**
1. 失败测试：fixture Source 有而 Runtime/用例未覆盖的 Transition → 正确识别；已覆盖不重复列出；空图/空用例集 → 如实为空。分类标签（Happy/Negative/…，设计 6.3 七类）作**附注字段初版启发**，不是独立分类器。
2. Run → PASS → Commit: `feat(p3): coverage gap detection (P3-10)`

### Task 3.3: P3-11 — Test Fingerprint 去重（F8）

> **⚠️ 复用口径（底稿修订 #8）**：`screen_fingerprint` 输入是 XML page_source，`test_fingerprint` 输入是 step 序列——字面"调用同一个函数"不可行。红线落地为**共享同一哈希原语**：抽 `source/hashing.py`（规范化拼接 → sha256 前 16 hex），`screen_fingerprint` 重构后改调它（行为不变，P2 的 fingerprint 相关测试全绿）。**只做精确哈希相等，禁相似度/模糊匹配。**

**Files:**
- Create: `source/hashing.py`（`stable_hash(parts) -> str`）、`candidates/__init__.py`、`candidates/fingerprint.py`（`test_fingerprint(tc) -> str`：screen 序列 + action 序列 + assertion 规范化）
- Modify: `source/screen.py`（screen_fingerprint 改调 stable_hash，行为不变）
- Test: `tests/unit/test_test_fingerprint.py`、`tests/unit/test_screen_fingerprint_regress.py`

**Steps:**
1. 失败测试：同语义不同命名的候选 → 同 fingerprint；fingerprint 变化的因素恰为 screen/action/assertion 三类输入；screen_fingerprint 重构前后对同一批 fixture 输出完全一致。
2. Run → PASS → Commit: `feat(p3): shared hash primitive + test fingerprint (P3-11)`

### Task 3.4: P3-12 — 生成管线（Schema/语义校验 + Dry Run + test_candidates）

**Objective:** 设计 6.1 节主流程 + 设计 11 节 test_candidates 表（迁移 002）。

**Files:**
- Create: `agents/migrations/002_test_candidates.sql`（字段照设计 11 节：fingerprint/dry_run_status/review_status 等）、`candidates/test_case.py`（表读写）
- Create: `generator/validator.py`（`validate_schema`：直接调 `testcase/lint.py lint()`，ERROR → 拒绝；`validate_semantic`：已知 Screen 上的步骤与 RuntimeGraph/SourceGraph 已知转移**结构性矛盾** → 拒绝；未知 Screen/新元素 → 标记 unknown 放行交给 Dry Run 验证——登记该口径）
- Create: `generator/dry_run.py`（`run_candidate(tc, *, env) -> DryRunResult`：**启动断言 env_kind==sandbox 且 DeviceType==simulator**（矩阵 #9）；执行装配复用 `cli/pipeline.py` 既有函数（Executor/StepRunner/trace）；若装配与 `cmd_run` 纠缠过深，抽最小装配 helper——**不改 CLI 行为，P1/P2 全绿后合入**；结果写 dry_run_status）
- Create: `generator/generator.py`（主流程：gaps + bug history（trace_history）+ requirement → LLM 生成候选步骤（prompt 复用 `llm/prompt.py` 模式、解析复用 `llm/parser.py` 白名单 + ignored_fields）→ 任务级 `LLMBudget(max_calls_per_run=...)`（F4，INVALID_OUTPUT 连续 N 次 → breaker，矩阵 #10）→ 校验 → 去重（fingerprint 对比正式 suites/ 全量 + test_candidates 未决集；命中 → SKIP 留痕，矩阵 #8）→ Dry Run → 落 test_candidates（status PENDING））
- Test: `tests/unit/test_generator_pipeline.py`、`tests/unit/test_dry_run.py`、`tests/fault_injection/test_p3_matrix_m3.py`（矩阵 #7–#10）

**Steps:**
1. 失败测试：lint 不过 → 不入审核队列（fail-loud，减少人工噪音）；结构性语义矛盾 → 拒绝；fingerprint 命中 → SKIP 且可查原因；非 sandbox Dry Run → 拒绝；Dry Run PASS 的候选 → 库中 dry_run_status=PASS、review_status=PENDING。
2. Run → PASS → Commit: `feat(p3): generation pipeline + test candidates store (P3-12)`

### Task 3.5: P3-13 — 统一审核（F6 首个消费者）+ `mta generate` CLI

> **⚠️ 落盘路径（底稿修订 #7）**："P1 Repository 的正式写入路径"不存在——正式用例是 `suites/<suite>/*.yaml`（人工提交）。ACCEPT 落盘沿用 **promoter 模式**（`experience/promoter.py approve_proposal:215-298`）：人工 ACCEPT 即授权 → 工具写 `suites/<suite>/<id>.yaml`（suite 取候选声明字段）→ git commit → 失败回滚；**id 与现有用例冲突 → fail-loud**。此后该用例即入 P1 lint/run 正常流程（设计 6.1"不是新路径"）。提供 `--export-patch` 仅导出补丁文本备选（完全人工落盘的团队习惯），默认自动写。

**Files:**
- Create: `candidates/review.py`（`decide_candidate(store, kind, candidate_id, decision, reviewer, note=None)`：PENDING→ACCEPT/REJECT；REJECT 必须 note；**字段命名/CLI 动词与 `agent/review.py decide_review:161-191` 对齐**；kind 为可扩展判别（test/discovery/bug），本任务只接 test）
- Modify: `cli/main.py`（`generate --gap-from <plan_id>`（读 plan 的 generated_gap 任务并现场重算 coverage）；`candidate review list|accept|reject <id> --kind test`）
- Test: `tests/unit/test_candidate_review.py`、`tests/unit/test_cli_generate.py`

**Steps:**
1. 失败测试：ACCEPT → suites/ 出现新 YAML + git commit + 库状态 ACCEPTED；REJECT（无 note）→ 拒绝执行；重复 decide 同一候选 → 幂等拒绝；accept 后 `mta lint` 对新文件通过、`mta run` 可发现该用例（P1 流程无改造）。
2. Run → PASS → Commit: `feat(p3): unified candidate review + mta generate (P3-13)` + tag `checkpoint-p3-m3`

---

## M4 — Bounded Exploration（设计 7 节 / P3-C，风险最高的一环）

> **Gate M4**：Sandbox 内完成一次探索任务，Budget/Loop Detection 生效，Discovery Event 正确产出且不自动改动任何正式资产；矩阵 #11–#13 全过。**本里程碑任何任务不允许在非 sandbox 环境跑通**，硬性前提。

### Task 4.1: P3-14 — Explorer Loop 骨架 + Guard 接入（F1）

> **⚠️ 复用来源修正（底稿修订）**：Guard 的本体是 `executor/guard.py`（`Guard.check`）+ `executor/policy.py:108`（`effective_risk`）——**不是 `planner/risk.py`**（那是 M2 的 planner 侧转发壳）。Explorer 一律经 `agents/tools.py AgentToolkit`（M1），import 级断言 explorer 包内无直接 executor/driver 访问。

**Objective:** 设计 7.1 节 Observe→Frontier→Guard→Execute→Verify。

**Files:**
- Create: `explorer/__init__.py`、`explorer/agent.py`（Loop 骨架：observe（page_source → `screen_fingerprint` → 图上 node 匹配）→ decide → `toolkit.call(...)` → verify（新 State？新 Transition？）→ agent_trace 每步落库）、`explorer/policy.py`（候选动作构造 + risk 预判：`effective_risk`==LOW 才自动执行，否则交 Guard 拦截路径）
- Test: `tests/unit/test_explorer_guard.py`（FakeDriver）

**Steps:**
1. 失败测试：HIGH/CRITICAL 目标 → Guard BLOCK、不执行、agent_trace 有 guard_result=BLOCK 记录（非静默跳过）；Guard 与 P1 同一函数对象（import 级断言）；explorer 包 grep 级断言：无 `executor.` 直接调用（矩阵 #13 前半）。
2. Run → PASS → Commit: `feat(p3): explorer loop via shared guard (P3-14)`

### Task 4.2: P3-15 — Frontier 选择（基于 P2 UI Graph）

**Objective:** 设计 7.1 节：优先未探索 Transition，不随机点击。

**Files:**
- Create: `explorer/frontier.py`（`select_frontier(graph: RuntimeGraph, visited, current_screen) -> list[FrontierItem]` 纯函数：unexplored（source 有/runtime 无、runtime visit_count 低）优先，novelty 打分初版）
- Test: `tests/unit/test_frontier.py`

**Steps:**
1. 失败测试：explored 排在 unexplored 之后；frontier 为空（全部已知）→ Loop 正常结束（OUTCOME=FRONTIER_EMPTY）而非报错。
2. Run → PASS → Commit: `feat(p3): frontier selection (P3-15)`

### Task 4.3: P3-16 — 探索 Budget（F11/F4）

**Objective:** 设计 7.2 节四维预算，超限即停。

**Files:**
- Create: `explorer/state.py`（`ExplorationBudget`：max_steps/max_duration_seconds/max_llm_calls/max_repeated_state；默认值来自 `agents/policy_config.py`，`--budget <file>` 同结构覆盖）
- Modify: `explorer/agent.py`（接入 `LLMBudget(max_calls_per_run=budget.max_llm_calls)`——**断言注入对象类型就是 P1 LLMBudget**（F4，不新写计数器）；四维检查 → `ExplorationOutcome(*_BUDGET_EXCEEDED)`，不自动放宽重试）
- Test: `tests/unit/test_exploration_budget.py`

**Steps:**
1. 失败测试：四类预算逐一超限 → 对应 `*_BUDGET_EXCEEDED` 且 agent_trace 终态正确；LLM 预算底层是 LLMBudget 实例（类型断言）。
2. Run → PASS → Commit: `feat(p3): exploration budget on llm budget (P3-16)`

### Task 4.4: P3-17 — Loop Detection（F8）

**Objective:** 设计 7.3 节 `is_loop` 纯函数。

**Files:**
- Create: `explorer/loop_detector.py`（`is_loop(visited_fingerprints, current_fingerprint, window, max_repeat) -> bool`；fingerprint 来自 P2 同一哈希工具）
- Test: `tests/unit/test_loop_detector.py`

**Steps:**
1. 失败测试：`Home→Search→Home→Search→Home` fixture → 识别 loop；`max_repeated_state` 生效；**`current_fingerprint=None`（页面不可解析）→ 不计入重复窗口 + 审计留痕**（登记决策，open question #3）。
2. Run → PASS → Commit: `feat(p3): loop detection (P3-17)`

### Task 4.5: P3-18 — Discovery Event + 启动环境校验（F6/F7）

**Objective:** 设计 7.4/7.5 节：sandbox-only + 候选资产统一审核。

**Files:**
- Create: `agents/migrations/003_discovery_events.sql`（设计 11 节字段）、`candidates/discovery.py`（表读写 + `decide_candidate(kind="discovery")` 接线）
- Modify: `explorer/agent.py`（发现新 Screen/Transition/Element → `create_discovery_event`（经 toolkit，CANDIDATE tier）；**不自动**写图）；`cli/main.py`（`explore` 启动前置：env_kind==sandbox **且** DeviceType==simulator，否则 exit≠0——启动时拒绝，不是运行一半才拦；production 已被 M1 一票否决覆盖）
- Test: `tests/unit/test_discovery_event.py`

**Steps:**
1. 失败测试：非 sandbox / real_device → 拒绝启动（矩阵 #9 同源断言）；新发现 → discovery_events PENDING、图未变；ACCEPT → 经 `graph/storage.py GraphStore` 幂等登记（origin 标记 discovery），图可查；REJECT → 图不变。
2. Run → PASS → Commit: `feat(p3): discovery events + sandbox-only startup (P3-18)`

### Task 4.6: P3-19 — `mta explore` CLI + Sandbox Gate 演示

> **⚠️ 口径必守（review_p3_task12 §4 留的）**：`mta explore` 必须在 **staging 下也拒**
> ——plan 执行注意事项 #3 与 design §7.4 对 Exploration 要求的是 **sandbox-only 且不能
> 有例外**。而 Task 1.2 的 `_check_autonomous_env` 只做「production 一票否决」，它的
> 拒绝消息目前对用户说「请在 **sandbox 或 staging** 下运行」——**本任务落地时必须让
> staging 也被 `mta explore` 拒掉**，否则文案与行为打架（那条消息对 `plan`/`generate`
> 是准确的，对 `explore` 不是）。

**Objective:** 设计 12 节 CLI；本里程碑端到端验证。

**Files:**
- Modify: `cli/main.py`（`explore --goal "..." [--budget <file>]`）
- Create: `phase0/verify_p3_m4.py`（LoginDemo sandbox 演示：给定"探索某页面未覆盖状态"，完整跑 observe→frontier→guard→execute→verify；断言预算生效、discovery 产出、guard 拦截有审计、**结束后 git status 干净 + experience/graph/正式用例零修改**）
- Test: `tests/unit/test_cli_explore.py`

**Steps:**
1. 失败测试 + CLI 集成。
2. Sandbox 模拟器演示跑通留档（矩阵 #11/#12/#13 现场复核）。
3. Run → PASS → Commit: `feat(p3): mta explore cli + m4 demo (P3-19)` + tag `checkpoint-p3-m4`

---

## M5 — Failure Diagnosis（设计 8 节 / P3-D）

> **Gate M5**：对一次真实/注入故障产出带 Evidence Score 的 Bug Candidate，而非裸 LLM 结论；矩阵 #14–#16 全过。

### Task 5.1: P3-20 — Diagnosis 数据模型 + Context 装配 + bug_candidates 表

**Objective:** 设计 8.1/8.5/11 节。

**Files:**
- Create: `diagnosis/__init__.py`、`diagnosis/models.py`（`Hypothesis{statement, confidence, evidence: list, origin}`——**空 evidence 拒绝创建**（F10 第一道防线）；`BugCandidateDraft`，`attribution_hypothesis` 默认 `UNTRIAGED`）、`diagnosis/context.py`（`DiagnosisContext` 装配：P1 trace run/steps/截图/UI 树 + infra_events/日志 + `graph/diff.py` diff（--base-build 可选）+ Experience + `trace_history`——**全部已有数据源**，经 `knowledge/retrieval.py`）
- Create: `agents/migrations/004_bug_candidates.sql`（设计 11 节字段）、`candidates/bug.py`（表读写）
- Test: `tests/unit/test_diagnosis_models.py`

**Steps:**
1. 失败测试：Hypothesis 无 evidence → 拒绝；attribution 默认 UNTRIAGED；context 各字段来源可追溯（import 级断言：不直接 SQL，只经 KnowledgeSources/既有 API）。
2. Run → PASS → Commit: `feat(p3): diagnosis models + context assembly (P3-20)`

### Task 5.2: P3-21 — 规则优先的假设引擎（F10）

> **⚠️ 事实修正（底稿修订 #6）**：**P1 不存在 APP_CRASH 判定**——`APP_CRASH` 只是 FAILURE_TYPES 词表中的一个字符串（`tracer/storage.py:48`）。本任务**新增** `crash_detected` 检测（session/driver 存活探测 + infra_events/日志特征 → failure_type=APP_CRASH，落既有词表）；复用的是词表与 trace 数据，不是不存在的函数。

**Objective:** 设计 8.2 节：规则给不出结论才用 LLM。

**Files:**
- Create: `diagnosis/classifier.py`（`crash_detected(ctx) -> bool` + `rule_based_hypotheses(ctx) -> list[Hypothesis]`：app crash → APP_DEFECT；API 5xx + UI 正常 → ENVIRONMENT_DEFECT（network summary）；Locator 漂移 + Source/Runtime Diff 命中 → UI_DRIFT（复用 `graph/diff.py` 与 P2 recovery 的 reconciliation 记录，不重算漂移）；断言值不符 → TEST_ASSERTION_FAILURE（P1 既有结论，**不进诊断流程**））、`diagnosis/analyzer.py`（规则优先；规则空结果才调 LLM，输出仍为带 confidence 的 Hypothesis，`origin=llm_hypothesis`）
- Test: `tests/unit/test_classifier.py`

**Steps:**
1. 失败测试：crash 场景 → 规则直接出 APP_DEFECT、**LLM 零调用**（矩阵 #14）；漂移 + diff 命中 → UI_DRIFT；规则无法判定 → LLM 补充且 origin 标注（权重最低，设计 8.3 `llm_hypothesis_only: 1`）。
2. Run → PASS → Commit: `feat(p3): rule-first diagnosis + crash detection (P3-21)`

### Task 5.3: P3-22 — Evidence Score

**Objective:** 设计 8.3 节；权重可配置便于校准。

**Files:**
- Create: `diagnosis/evidence.py`（`score_evidence(evidence) -> int` 纯函数；权重从 `agents/policy_config.py` 读（M1 已有段，初版 = 设计 8.3 原值）；`suggested_attribution` 仅当 score ≥ `evidence.attribution_threshold`（初版 10）才给出，否则保持 UNTRIAGED）
- Test: `tests/unit/test_evidence_score.py`

**Steps:**
1. 失败测试：各证据项按配置权重累加；**排序由 Evidence Score 决定，不由 LLM confidence 决定**（同证据集下 confidence 高低不改变次序）；低于阈值 → suggested_attribution 为空（矩阵 #16 前半）。
2. Run → PASS → Commit: `feat(p3): evidence score (P3-22)`

### Task 5.4: P3-23 — Reproducibility Engine（F9）

> **⚠️ 复用对象修正（底稿修订 #9）**：阶段判定本体在 `executor/policy.py`（`FailurePhase:41-46 / retry_decision:135-173 / max_attempts_for:176`）+ `cli/pipeline.py make_postcondition_checker:1256`；`agent/recovery.py` 只是消费方。本任务复用本体，不重新推断"动作是否已发出"。

**Objective:** 设计 8.4 节复现控制。

**Files:**
- Create: `diagnosis/reproducer.py`（`reproduce(ctx) -> ReproResult`：idempotent + effective_risk==LOW → 最多重跑 3 次（PRE_DISPATCH 失败直接计入）；non-idempotent → 不盲目重跑，按 `retry_decision`：POST_DISPATCH 失败查 postcondition——成立 → 不算复现失败；不成立/无法判断 → 记"无法确认复现"，`reproduction_count` 不加、evidence 不给 reproducible 加分）
- Test: `tests/unit/test_reproducer.py`、`tests/fault_injection/test_p3_matrix_m5.py`（矩阵 #15）

**Steps:**
1. 失败测试：三分支全覆盖（矩阵 #15）；重跑经 toolkit 执行（F12 不断）；重跑预算受任务 LLMBudget 约束。
2. 设备集中点：postcondition 真实判定需 sandbox 会话（LoginDemo 演示时覆盖）。
3. Run → PASS → Commit: `feat(p3): reproducer on p1 dispatch phases (P3-23)`

### Task 5.5: P3-24 — Bug 去重（F8）

**Objective:** 设计 8.5 节 `dedup_key`。

**Files:**
- Create: `diagnosis/deduplicator.py`（`dedup_key(bug) -> str`：screen + runtime diff + source change 规范化 → `source/hashing.stable_hash`；同 key → 合并 `affected_tests` 累加、后到者状态置 DUPLICATE（保留首条））
- Test: `tests/unit/test_bug_dedup.py`

**Steps:**
1. 失败测试：三个不同 testcase 失败但同根因 → 一条 Bug Candidate + affected_tests=3（矩阵 #16 后半）；key 组成任一维变化 → 不同 key。
2. Run → PASS → Commit: `feat(p3): bug deduplication (P3-24)`

### Task 5.6: P3-25 — Bug 状态机 + `mta diagnose` CLI + Gate 演示

**Objective:** 设计 8.5 状态机 + F6 统一审核。

**Files:**
- Modify: `candidates/bug.py`（`DISCOVERED→ANALYZING→CANDIDATE→HUMAN_REVIEW→{CONFIRMED|REJECTED|DUPLICATE}` 全路径，转换表纯函数）、`candidates/review.py`（接 kind=bug：HUMAN_REVIEW 上 accept→CONFIRMED / reject→REJECTED）
- Modify: `cli/main.py`（`diagnose --run-id <id> [--step <n>] [--base-build <id>]`；`candidate review` 扩 bug 类型）
- Create: `phase0/verify_p3_m5.py`（Gate 演示：① 复用 P2 F5 漂移法制造失败；② LoginDemo 注入一个真实逻辑 bug（改坏一处判断）→ `mta run` 失败 → `mta diagnose` → Bug Candidate 带 Evidence Score 与证据清单 → 人工 confirm）
- Test: `tests/unit/test_bug_review.py`

**Steps:**
1. 失败测试：状态机非法转换拒绝；CONFIRMED → 产出结构化 Evidence Bundle（JSON 落 `out/`）；**不规定外部系统对接**（open question #1）。
2. Gate 演示跑通留档。
3. Run → PASS → Commit: `feat(p3): mta diagnose + bug candidate lifecycle (P3-25)` + tag `checkpoint-p3-m5`

---

## M6 — Subagent + 自主回归闭环 + Benchmark（设计 9/10/13 节 / P3-E）

> **Gate M6**：新 Build 端到端跑通 Diff→Plan→补测→执行→探索→诊断→Bug Candidate→人工确认，全程无资产被自动修改；矩阵 #17/#18 全过；Benchmark 可运行并产出对比数据。

### Task 6.1: P3-26 — Agent 状态机纯函数（ESCALATED 语义）

**Objective:** 设计 9.2 节：合法转换表 + 升级触发，全部脱离设备可测（F13）。

**Files:**
- Create: `agents/state.py`（`transition(state, event) -> AgentState` 合法转换表；`should_escalate(...) -> EscalationReason | None`：连续 N 次 Guard 拦截 / LLM 判定 CRITICAL 操作 / budget 耗尽但目标未达成 / 连续 LLM_INVALID_OUTPUT；N 来自 policy.yaml）
- Modify: `agents/storage.py`（`update_task_state` 强制走转换表，非法转换 fail-loud）
- Test: `tests/unit/test_agent_state.py`

**Steps:**
1. 失败测试：全转换表覆盖；**ESCALATED 不是错误态**——必附上下文（goal、已执行步骤、拦截原因）供人工接手；四种触发逐一命中。
2. Run → PASS → Commit: `feat(p3): agent state machine + escalation (P3-26)`

### Task 6.2: P3-27 — Subagent 绑定统一工具层（F12）

**Objective:** 设计 9.1/10 节：任何 Subagent 的工具调用都落同一 `AgentToolkit → Executor+Guard`，无绕过路径。

**Files:**
- Create: `agents/base.py`（`Subagent` 基类：持有**唯一** toolkit 实例引用 + 任务级 `LLMBudget`；无其他执行通道）
- Test: `tests/unit/test_subagent_binding.py`

**Steps:**
1. 失败测试：Subagent 尝试直接构造 Executor/driver → 类型系统/构造函数层面不可达（依赖只经注入的 toolkit）；任意 subagent 的工具调用在 agent_trace 可追溯到同一 toolkit（F12，矩阵 #18 前半）；三 Subagent 预算独立互不挤占（矩阵 #18 后半）。
2. Run → PASS → Commit: `feat(p3): subagent base on unified toolkit (P3-27)`

### Task 6.3: P3-28 — Context 检索（设计 9.3，F3）

**Objective:** 只检索当前任务相关子集，不做全文拼接。

**Files:**
- Create: `agents/context.py`（`build_agent_context(task, knowledge) -> AgentContext`：current_screen→graph_query 的 Screen 子集、relevant_experience→experience_lookup、relevant_history→trace_history(filters={screen})、relevant_source→source_metadata 的当前 Screen 子集）
- Test: `tests/unit/test_agent_context.py`

**Steps:**
1. 失败测试：context 体积随任务收窄而非随 Trace 总量增长；底层确实是 KnowledgeSources 四方法（import 级断言，非直接 SQL）。
2. Run → PASS → Commit: `feat(p3): scoped agent context (P3-28)`

### Task 6.4: P3-29 — Test Manager + 三 Subagent 整合

**Objective:** 设计 9.1 节协调与升级路径。

**Files:**
- Create: `agents/manager.py`（Test Manager：编排 planner/explorer/diagnosis agent；ESCALATED 处置 = 停止并输出人工接手包，不重试不放宽）、`agents/planner_agent.py`、`agents/explorer_agent.py`、`agents/diagnosis_agent.py`（分别薄封装 M2/M4/M5 模块，接入 base.py）
- Test: `tests/integration/test_subagent_coordination.py`

**Steps:**
1. 失败测试：三 agent 各自产出（TestPlan/Discovery Events/Hypotheses）经 manager 汇总；升级场景（连续 Guard 拦截）→ ESCALATED 上下文完整；LLM Escalation 触发条件（Security/Financial/Destructive/Ambiguous，设计 9.4 保留项）→ 直接 ESCALATED。
2. Run → PASS → Commit: `feat(p3): test manager + subagents (P3-29)`

### Task 6.5: P3-30 — 指标（设计 13.1）+ Benchmark（设计 13.2）

**Objective:** 五组指标可观测 + Agent 自身回归可比。

**Files:**
- Create: `report/agent_metrics.py`（沿 `report/experience_metrics.py` 模式：采集纯函数 + `collect_agent_metrics(agent_db, trace_db)`；可自动计算项：Generation Generated/Accepted/Duplicate Rate、Exploration 新 Screen/Transition/Element 与 Bugs Found、Diagnosis 复现率/Duplicate 率、LLM Cost（budget 用量）、Human Review Cost（review 数量与耗时）；**Selection Precision / Diagnosis Accuracy 依赖标注真值，只在 Benchmark 标注集内计算**（登记口径））
- Modify: `report/html.py`（agent 指标注入段，渲染层不读库）
- Create: `agents/migrations/005_benchmark_runs.sql`（**设计 11 节之外的补充表**，登记）、`agents/benchmark.py`（固定 7 类 case：Locator Drift / Unknown Screen / Ambiguous Element / Backend Failure / App Crash / New Transition / Negative Scenario——**复用 `tests/fault_injection/fi_support.py` 的 FakeExecutor/FakeDS/FakeLLM 与 test_matrix_01_12/13_24 的场景构造**，无真机）；`mta agent benchmark run|compare <v1> <v2>`（compare：关键指标下降超阈值 → 标记 `AGENT_REGRESSION`，exit≠0 供 CI）
- Test: `tests/unit/test_agent_metrics.py`、`tests/unit/test_benchmark.py`

**Steps:**
1. 失败测试：指标分母口径（如 Duplicate Rate = dup/(generated)）；benchmark 两版本对比 + AGENT_REGRESSION 判定；LLM Cost 来自各任务 budget 用量聚合。
2. Run → PASS → Commit: `feat(p3): agent metrics + benchmark (P3-30)`

### Task 6.6: P3-31 — 端到端自主回归闭环演示

**Objective:** 设计 14 节 P3-E Gate；对照 1.2 成功标准逐条记录。

**Files:**
- Create: `phase0/verify_p3_final.py`（闭环：模拟新 build（改动 + commit）→ `mta plan` → `mta generate`（补 gap）→ 人工 accept（落 suites/）→ `mta run`（含新用例）→ `mta explore`（sandbox）→ 注入真实 bug → `mta run` 失败 → `mta diagnose` → Bug Candidate → 人工 confirm；**每步 trace/库断言；全程断言"正式资产零程序修改"：git status 干净 + overrides/Verified/期望值/既有用例文件逐一比对**（矩阵 #17））
- Create: `docs/p3_demo_record.md`（对照设计 1.2 成功标准逐条留证）

**Steps:**
1. 全链路演示跑通并留档。
2. 回归全量：`python -m pytest tests -q`（P0 8 项验收 + P1 24 项 + P2 16 项 + P3 18 项矩阵全绿）。
3. Commit: `feat(p3): e2e autonomous regression loop demo (P3-31)` + tag `checkpoint-p3-m6` + `v0.3-p3-complete`

---

## 2. 执行注意事项（给实现者/子智能体）

1. **复用优先于实现**：本计划反复出现"直接 import，不新写""同一函数对象断言"，不是风格偏好，是防止 P3 退化成"看起来接了 P1/P2，实际各写一份"。每个涉及复用的任务，测试里都要有一条**显式的"是同一个函数/同一套逻辑"断言**（import 级或行为等价性），而不是"结果看起来一致"。第 0 节的 path:line 是复用起点，实现时若签名有出入，**回到该处核实而不是凭记忆写**。
2. **F2/F12 是整个计划的安全红线**：工具表静态检查自 M1 起进 CI 门禁（`tests/unit/test_tool_allowlist_gate.py`），防止后续任何 PR 给 Agent 加上危险工具；M6 的 Subagent 绑定测试复验同一断言。
3. **M4（Exploration）的 sandbox-only 约束不能有例外**：任何"演示方便"而在非 sandbox 环境跑 Explorer 的冲动，PR review 一律拒绝。
4. **回归底线**：每任务完成后 `python -m pytest tests -q` 全绿（P0/P1/P2 全部矩阵含在内）；无 policy.yaml / agent.db 不存在 / P3 命令未调用时，系统行为与 P2 完全一致。
5. **库文件边界**：agent.db（可变状态库，单写者 = `agents/storage.py`）、trace.db（append-only 流水库）、experience.db、graph.db 职责不混；P3 的 agent 关键事件（task 状态转换、guard 拦截、escalation）写 agent_trace；与 P1 用例执行相关的记录仍归 P1 trace，不重复落两处。
6. **真机/Sandbox 集中点**：Task 3.4（Dry Run）、4.6（探索演示）、5.4（postcondition 复现）、5.6（诊断演示）、6.6（端到端）；其余全部 FakeDriver / 内存库单测（F13）。
7. **LLM 结构化输出**：`llm/provider.py` 无 JSON mode——沿用 prompt 契约 + `llm/parser.py` 严格解析（raw_decode、动作白名单、ignored_fields）。若实现中发现三处以上（planner 解释 / generator 生成 / diagnosis 补充）都需要真 JSON mode，作为独立小提案回到设计层，不在任务内私自扩 provider。
8. **open questions（实现中遇到即停下确认，不猜）**：
   - Bug Candidate CONFIRMED 后的下游（Jira / 内部 tracker / 纯文件归档）——设计只要求结构化 Evidence Bundle，M5 先落 `out/`；
   - `changed_files → targets` 依赖 source_metadata 的文件级归属字段——若缺失，回退 `source/build_diff.py diff_builds` 的 build 间 drift 面（Task 2.3 已登记），映射精度损失要写进 plan 的 reasons；
   - `screen_fingerprint` 返回 None（页面不可解析）时 loop 窗口与重复计数的语义——Task 4.4 默认"不计入 + 审计留痕"，若实测噪声大回设计层确认；
   - Evidence Score / priority_score 权重校准需要多少真实数据——随 M2/M5 Gate 演示积累，**初版权重不假装精确**（P2 对阈值的态度）；
   - ESCALATED 的通知渠道（CLI 输出之外的 Slack/邮件）不在本计划范围。

## 3. 工作量与顺序摘要

| 里程碑 | 任务 | 预估 | Gate（含矩阵项） |
|---|---|---|---|
| M1 基础与安全边界 | 1.1~1.4 | 2d | #1–#4；agent.db/policy.yaml/工具层/git diff 就绪 |
| M2 Planning (P3-A) | 2.1~2.4 | 2d | 可解释 Plan；#5/#6 |
| M3 Generation (P3-B) | 3.1~3.5 | 3.5d | Candidate 全流程 + 统一审核；#7–#10 |
| M4 Exploration (P3-C) | 4.1~4.6 | 4d | Sandbox 探索闭环；#11–#13 |
| M5 Diagnosis (P3-D) | 5.1~5.6 | 3.5d | Bug Candidate 全流程；#14–#16（可与 M4 并行） |
| M6 Subagent+闭环 (P3-E) | 6.1~6.6 | 4d | 自主闭环演示 + Benchmark；#17/#18 |

总计约 **19 人日**（不含真机/Sandbox/LLM 网关等待）。较底稿（16.5d + 0.5d M0）增加的部分即底稿缺失的基础设施（agent.db 提前、policy.yaml 新建、工具层前置、git diff 工具），这些是 F2/F7/F12 能在第一个会碰 App 的模块之前生效的前提。
