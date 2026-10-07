# Mobile Test Agent — Phase 3 实施计划

> **For Hermes:** 用 subagent-driven-development 按任务派发执行；每任务两段 review（spec 合规 → 代码质量）。
>
> **⚠️ 本计划的局限**：撰写时没有实际代码库访问权限，第 0 节"P2 基线与现状差距"是按 **P2 设计文档已完整落地**这个假设推导的，**开工前必须对照真实代码核实**（P2 的 `experience/` `graph/` `repository/` `agent/recovery.py` `llm/budget.py` `testcase/lint.py` 等模块是否确实提供了设计文档承诺的接口）。如果核实后发现偏差，先修这部分差距，再进入 M1。
>
> **Goal:** 在 P2 基线上实现 Test Planner（P3-A）、Test Generator（P3-B）、Bounded Exploration（P3-C）、Failure Diagnosis（P3-D）、Subagent + 自主回归闭环（P3-E）五条主线（P3 设计文档 5 目标方向 / 13 硬约束 F1–F13 / 5 里程碑全部落地），达到"给定一次 Build 变化，系统能自主规划测试、补充覆盖、受限探索、诊断失败，但任何正式资产变更仍只经人工批准"。
>
> **Architecture:** 沿用 P0/P1/P2 全部分层，新增 `planner/`、`generator/`、`explorer/`、`diagnosis/`、`agents/`、`candidates/`、`knowledge/` 七个包。**核心原则（设计 3.1 节"复用矩阵"）：P3 的风险判定、知识检索、LLM 预算、Schema 校验、资产审核机制全部复用 P1/P2 已有实现，不允许任何模块重新发明一套平行逻辑**——这是本计划里优先级最高的红线，高于功能进度本身。开发顺序：P3-A → P3-B → P3-C → P3-D → P3-E，前一条主线的 Gate 不过不进入下一条（P3-C 风险最高，必须在 A/B 稳定之后才做）。
>
> **Tech Stack:** Python 3.11 + Pydantic 2.x + SQLite（`agent.db` 独立库，复用 P2 的 `schema_migrations` 迁移机制与"按写者边界分库"原则）+ pytest（沿用 P1/P2 测试基建，无新依赖）。
>
> **工作分支:** `P3-<日期>`（**从 P2 完成后的 HEAD 切出，具体 commit 由你核实后填入**；参考 P2 计划的 tag 惯例，P2 收尾应有 `v0.2-p2-complete` 或等价 tag，以此为基线）。**设计基线:** `docs/mobile-test-agent-phase3-design.md` v1.0-draft。**每任务完成即 commit** + 里程碑末打 tag `checkpoint-p3-mN`。

---

## 0. P2 基线与现状差距（**假设前提，开工前必须核实**）

| 设计要求（P3 设计 3.1 节复用矩阵） | 假设的 P2 现状 | P3 需要的接口 |
|---|---|---|
| 风险判定（F1） | P1 `agent/recovery.py` 或共享的 `runtime_guard.py` 提供 `compute_effective_risk` | P3 的 Explorer/Generator 直接 import 并调用，不重新实现 |
| 知识检索（F3） | P2 `experience/knowledge.py` 的 `KnowledgeSources` Protocol（`experience_lookup/graph_query/impact_of/trace_history`） | `knowledge/retrieval.py` 薄封装，禁止绕开直接查表 |
| LLM 预算（F4） | P1 `llm/budget.py` 的 `LLMBudget` 类 | 每个 Agent 任务实例化一个独立配额的 `LLMBudget`，不新写熔断逻辑 |
| Candidate Test Schema（F5） | P1 `testcase/schema.py`（`TestCase` Pydantic 模型）+ `testcase/lint.py` | Candidate Test 复用同一模型，只加 `status`/溯源字段；校验直接调用 `mta lint` 的底层函数 |
| 审核机制（F6） | P2 `agent/review.py`（`recovery_reviews`）+ `experience/promoter.py`（`promotion_proposals`） | P3 三类候选资产（Test/Bug/Discovery）复用同一种"PENDING→ACCEPT/REJECT"状态机范式，表结构可以独立但语义和 CLI 风格对齐 |
| 环境隔离（F7） | P1 `environment/`（`env.kind` 字段 + `policy.yaml` 的 Guard） | Explorer 启动前校验 `env.kind == sandbox`，复用同一份 policy 加载逻辑 |
| Fingerprint（F8） | P2 `experience/models.py` 或 `graph/` 中的 `screen_fingerprint` 哈希函数 | Test Fingerprint / Loop Detection / Bug 去重统一调用同一个哈希工具函数，不新写相似度算法 |
| 非幂等判定（F9） | P1 `agent/recovery.py` 的 PRE_DISPATCH/POST_DISPATCH 阶段判定 + postcondition 检查 | Diagnosis 的 Reproducibility Engine 直接复用这套阶段判定，不重新推断"动作是否已发出" |
| Repository 变更落地 | P2 `experience/promoter.py`（写 `overrides/`，Git 管理） | P3 的 `create_promotion_proposal` 工具最终调用同一个 promoter，不新建写入路径 |
| Impact Analysis / UI Graph | P2 `graph/` 全套（`builder.py`/`diff.py`/`impact.py`） | Planner/Generator 直接查询，不重建 |

**核实步骤（M0，建议作为 M1 开工前的半天任务，而不是跳过）：**
1. 逐行核对上表"假设的 P2 现状"列，在真实代码库里找到对应模块和函数签名，记录实际签名与设计文档的差异。
2. 若某项缺失或签名不符（类似 P2 计划里发现的 `app_id` vs `app_build` 问题），先在 P3 分支里做一次小修复 commit，再开始 M1，不要让 P3 的代码绕着 P2 的缺口走。
3. 产出 `docs/p3_baseline_audit.md`，记录核实结果，作为本计划的"勘误表"。

**环境事实**：沿用 P1/P2（模拟器型号、Appium 版本、LLM 网关配置等），**具体值需你确认当前环境配置文件未变**。

**P0/P1/P2 行为保留原则**：P0 的 8 项验收、P1 的 24 项故障矩阵、P2 的 16 项故障矩阵必须持续全绿（回归底线）；**P3 未启用时（Planner/Generator/Explorer/Diagnosis 均不调用），系统行为必须与 P2 完全一致**——这是 F1–F13 之上最基本的安全网。

---

## 1. 里程碑总览与依赖

```text
M1 Planning(2d) ─► M2 Generation(3d) ─► M3 Exploration(4d) ─► M4 Diagnosis(3.5d) ─► M5 Subagent+闭环(3.5d)
```

- 严格串行，**不建议并行**：M3（Exploration）是全计划风险最高的一环（会真的在 App 上自主执行动作），必须在 M1/M2 把"确定性评分""Schema/去重复用"这些基础打牢之后再做；M4 依赖 M1-M3 产出的 Trace/Discovery 数据做真实验证；M5 是把前四个模块串起来，必须最后做。
- 每个 Gate 不通过不进下一里程碑。
- F1–F13 的验证分配到各 Gate（见下方"验收矩阵"）。
- 任务内 TDD：先写失败测试 → 最小实现 → 过测试 → commit。
- **M3/M5 的 Explorer 相关任务，真机/模拟器执行一律在 `env.kind=sandbox` 下进行**；其余全部 FakeDriver / 内存库单测（F13）。

### 验收矩阵（本计划定义，对应设计 F1–F13 与各里程碑 Gate）

| # | 约束 | 验证点 | 分配里程碑 |
|---|---|---|---|
| 1 | F1 风险判定复用 P1 Guard | Explorer/Generator 的危险动作被同一套 effective_risk 拦截 | M3 |
| 2 | F2 工具清单无危险工具 | `ALLOWED_TOOLS` 静态断言 + 运行时拒绝未注册工具调用 | M5 |
| 3 | F3 知识检索复用 KnowledgeSources | Planner/Generator/Explorer/Diagnosis 均不直接查表，只经 `knowledge/retrieval.py` | M1/M2/M3/M4 |
| 4 | F4 LLM 预算复用 LLMBudget | 各 Agent 任务的 budget 耗尽行为与 P1 一致（fail closed） | M1–M4 各自 |
| 5 | F5 Candidate Test = TestCase | Candidate 通过 `mta lint` 同一校验路径 | M2 |
| 6 | F6 审核机制统一 | 三类候选资产的 PENDING/ACCEPT/REJECT 状态机行为一致 | M2/M3/M4 |
| 7 | F7 环境隔离 | production 下 Explorer 无法启动（启动时拒绝，不是运行时才拦） | M3 |
| 8 | F8 Fingerprint 复用 | Test Fingerprint/Loop Detection/Bug 去重调用同一哈希函数 | M2/M3/M4 |
| 9 | F9 非幂等阶段判定复用 | Reproducibility Engine 的 PRE/POST_DISPATCH 判定与 P1 一致 | M4 |
| 10 | F10 Diagnosis 只产出 Hypothesis | Bug Candidate 的 `attribution` 字段默认不等于最终归因，需人工确认 | M4 |
| 11 | F11 Agent Budget | `max_steps/max_duration/max_llm_calls/max_repeated_state` 超限即停 | M3/M5 |
| 12 | F12 Subagent 无额外权限 | 任意 Subagent 发起的工具调用轨迹可追溯到同一 Executor+Guard | M5 |
| 13 | F13 纯函数可单测 | Planner 评分、去重、Evidence Score、Loop Detection 均有脱离设备的单测 | 全程 |

---

## M1 — Test Planning（设计 5 节 / P3-A）

> **Gate M1**：给定一次 Build 的 git diff，`mta plan` 输出可解释的优先级排序（`reasons` 非空且可读），评分函数纯函数可单测；F3/F4 在本里程碑的验证点通过。

### Task 1.1: planner 数据模型 + Schema

**Objective:** 设计 5.1 节 `TestPlanTask`/`TestPlan` 落地。

**Files:**
- Create: `planner/__init__.py`、`planner/models.py`（`TestPlanTask`/`TestPlan`，`schema_version="0.1"`）
- Test: `tests/unit/test_planner_models.py`

**Steps:**
1. 失败测试：`TestPlan` 序列化/反序列化；`TestPlanTask.reasons` 不允许为空列表（F13 要求可解释性，不是裸分数）。
2. Run → PASS → Commit: `feat(p3): planner models (P3-A step1)`

### Task 1.2: priority_score 纯函数（复用 Impact/History/Risk，F1/F3）

**Objective:** 设计 5.2 节确定性打分。

**Files:**
- Create: `planner/prioritizer.py`（`priority_score` 纯函数，输入全部来自 `knowledge/retrieval.py` 的查询结果，不直接碰数据库）
- Modify: `knowledge/__init__.py`、`knowledge/retrieval.py`（薄封装 P2 `KnowledgeSources`，**只能转发调用，不能重新实现查询逻辑**，这是 F3 的红线）
- Test: `tests/unit/test_prioritizer.py`（fixture 构造 impact/history/risk，不依赖真实 P2 数据）

**Steps:**
1. 失败测试：评分公式纯函数化（F13）；`effective_risk` 由 `planner/risk.py` 转发 P1 既有函数，**不重新实现**（review 中反复强调的复用红线，这里是第一次真正落地，务必严格）；CRITICAL/HIGH 风险用例强制高优先级不受 change size 影响（呼应设计 5.3 的 Risk-based Regression）。
2. 共享断言：`planner/risk.py` 与 P1 Guard 对同一输入给出同一结果（和 P2 Task 2.2 的"共享断言"手法一致）。
3. Run → PASS → Commit: `feat(p3): deterministic priority scoring (P3-A step2)`

### Task 1.3: Change-aware Impact 查询 + LLM 解释层

**Objective:** 设计 5.3 节 Git Diff → Impact → TestPlan；LLM 只做解释/打平排序，不独立决定顺序。

**Files:**
- Create: `planner/impact.py`（Git diff 文件名 → 受影响 Screen/Element → `knowledge.impact_of()`）、`planner/planner.py`（组装 TestPlan 主流程）
- Modify: `llm/prompt.py`（新增"给排序结果写 reason 说明"的 prompt 模板，复用 `llm/provider.py` 和 `LLMBudget`，F4）
- Test: `tests/unit/test_planner_e2e.py`（纯内存 fixture，不依赖真实 Git）

**Steps:**
1. 失败测试：确定性评分打分已定的情况下，LLM 输出修改排序 → 测试应判定为**违规**（F13 红线：LLM 不能独立决定顺序，只能解释/打平）；LLM budget 耗尽 → 排序仍然输出（无 reason 文本降级为规则摘要），不阻塞 Plan 生成本身。
2. Run → PASS → Commit: `feat(p3): git-diff-driven test plan + llm rationale (P3-A step3)`

### Task 1.4: `mta plan` CLI

**Objective:** 设计 12 节 CLI。

**Files:**
- Modify: `cli/main.py`（`plan --build <id> --base-build <id>`）
- Create: `phase0/verify_p3_m1.py`（真实或 fixture build diff → Plan 输出，断言 reasons 非空、CRITICAL 用例优先级最高）
- Test: `tests/unit/test_cli_plan.py`

**Steps:**
1. 失败测试：CLI 参数校验；输出格式（人类可读 + 可被 M2/M5 复用的结构化 JSON）。
2. Run → PASS → Commit: `feat(p3): mta plan cli (P3-A)` + tag `checkpoint-p3-m1`

---

## M2 — Test Generation（设计 6 节 / P3-B）

> **Gate M2**：对已知 Coverage Gap 生成 Candidate Test，Schema/语义/去重/Dry Run 全过，人工可通过统一审核流程 Accept；矩阵 #5/#6/#8 全过。

### Task 2.1: Candidate Test = TestCase（F5 的落地，设计 6.2）

**Objective:** 确认 Candidate Test **不是新 Schema**。

**Files:**
- Create: `generator/__init__.py`、`generator/models.py`（在 P1 `TestCase` 基础上扩展 `status: Literal["CANDIDATE"]`、`generated_by`、`generation_evidence`，**不重新定义 steps 的语法**）
- Test: `tests/unit/test_generator_models.py`

**Steps:**
1. 失败测试：Candidate Test 通过 P1 `testcase/schema.py` 的 `TestCase` 模型校验（继承而非平行定义）；额外字段不影响原有 lint 逻辑。
2. Run → PASS → Commit: `feat(p3): candidate test extends p1 testcase schema (P3-B step1)`

### Task 2.2: Coverage Gap 计算

**Objective:** 设计 6.3 节，基于 P2 UI Graph 找出未覆盖 Transition。

**Files:**
- Create: `generator/coverage.py`（`coverage_gap(graph, existing_tests) -> list[UncoveredTransition]`，纯函数读 `knowledge/retrieval.py` 的 `graph_query`）
- Test: `tests/unit/test_coverage_gap.py`

**Steps:**
1. 失败测试：fixture Source Graph 有而 Runtime 未覆盖的 Transition → 正确识别；已覆盖的不重复列出。
2. Run → PASS → Commit: `feat(p3): coverage gap detection (P3-B step2)`

### Task 2.3: Test Fingerprint 去重（F8 复用）

**Objective:** 设计 6.1 节 Duplicate Detection，必须调用 P2 已有哈希工具。

**Files:**
- Create: `generator/deduplicator.py`（`test_fingerprint(steps) -> str`：**显式 import P2 的 fingerprint 哈希函数**，只是拼接的输入不同——screen 序列+action 序列+assertion，不是重新设计算法）
- Test: `tests/unit/test_deduplicator.py`

**Steps:**
1. 失败测试：相同语义的生成测试（不同命名）→ 同一 fingerprint → SKIP；fingerprint 计算函数与 P2 `screen_fingerprint` 复用同一个底层哈希工具（断言调用了同一个函数，不是另写了一份相似逻辑——这是本任务的红线）。
2. Run → PASS → Commit: `feat(p3): test fingerprint dedup reusing p2 hash util (P3-B step3)`

### Task 2.4: Schema/语义校验 + Dry Run（F5/F7）

**Objective:** 设计 6.1 节完整流程：Schema（复用 lint）→ 语义 → Dry Run（仅 sandbox）。

**Files:**
- Create: `generator/validator.py`（`validate_schema` 直接调用 `testcase/lint.py` 的底层函数；`validate_semantic` 检查步骤序列与已知 UI Graph 转移是否矛盾）、`generator/generator.py`（主流程：LLM 生成 → 校验 → 去重 → Dry Run）
- Test: `tests/unit/test_generator_pipeline.py`、`tests/fault_injection/test_p3_matrix_m2.py`

**Steps:**
1. 失败测试：`env.kind != sandbox` 时 Dry Run 被拒绝（F7，矩阵验证点）；Schema 不合法的生成结果直接拒绝，不进入人工审核队列（减少人工噪音）；语义矛盾（如断言一个从未在该 Screen 出现过的元素）标记但不阻断——留给人工判断，只拒绝**结构性**错误。
2. Run → PASS → Commit: `feat(p3): generation pipeline with validation + sandbox dry-run (P3-B step4)`

### Task 2.5: 统一审核接入（F6）

**Objective:** Candidate Test 走和 P2 `recovery_reviews`/`promotion_proposals` 同样的审核范式。

**Files:**
- Create: `candidates/__init__.py`、`candidates/test_case.py`（`test_candidates` 表读写：PENDING→ACCEPT/REJECT；ACCEPT 后调用 P1 Repository 的正式写入路径，**不新建第二条落地路径**）
- Modify: `cli/main.py`（`generate` + `candidate review list/accept/reject`）
- Test: `tests/unit/test_candidate_review.py`

**Steps:**
1. 失败测试：ACCEPT → Candidate 变成正式 TestCase（走 P1 既有流程）；REJECT → 不落地，记录原因；审核状态机与 P2 的范式一致（字段命名、CLI 动词对齐，方便人工记忆）。
2. Run → PASS → Commit: `feat(p3): mta generate + unified candidate review (P3-B)` + tag `checkpoint-p3-m2`

---

## M3 — Bounded Exploration（设计 7 节 / P3-C，风险最高的一环）

> **Gate M3**：Sandbox 内完成一次探索任务，Budget/Loop Detection 生效，Discovery Event 正确产出且不自动改动任何正式资产；矩阵 #1/#7/#8/#11 全过。本里程碑**任何任务都不允许在非 sandbox 环境下跑通过**，这是硬性前提。

### Task 3.1: Explorer Agent Loop 骨架 + Guard 接入（F1）

**Objective:** 设计 7.1 节 Observe→Frontier→Guard→Execute→Verify。

**Files:**
- Create: `explorer/__init__.py`、`explorer/agent.py`（Loop 骨架）、`explorer/policy.py`（**直接 import `planner/risk.py`/P1 Guard，不新写风险判定**）
- Test: `tests/unit/test_explorer_guard.py`（FakeDriver）

**Steps:**
1. 失败测试：高风险目标（CRITICAL/HIGH）被 Guard 拦截，Loop 不执行该动作，记录 `BLOCK` 而非静默跳过；Guard 调用的函数与 P1 Recovery 路径调用的是**同一个函数对象**（import 级别断言，防止悄悄复制一份）。
2. Run → PASS → Commit: `feat(p3): explorer loop + shared guard (P3-C step1)`

### Task 3.2: Frontier 选择（基于 P2 UI Graph）

**Objective:** 设计 7.1/7.3 节，优先探索未知 Transition，不是随机点击。

**Files:**
- Create: `explorer/frontier.py`（查 `knowledge.graph_query()` 得到 explored/unexplored 集合，排序未探索优先）
- Test: `tests/unit/test_frontier.py`

**Steps:**
1. 失败测试：已 explored 的 Transition 排在 unexplored 之后；Frontier 为空时（全部已知）Loop 正常结束而不是报错。
2. Run → PASS → Commit: `feat(p3): frontier selection from ui graph (P3-C step2)`

### Task 3.3: Budget 与 LLM 预算（F4/F11）

**Objective:** 设计 7.2 节，Budget 超限即停。

**Files:**
- Create: `explorer/state.py`（`ExplorationBudget`：`max_steps/max_duration_seconds/max_llm_calls/max_repeated_state`）
- Modify: `explorer/agent.py`（接入 `LLMBudget`，**不新写熔断器**）
- Test: `tests/unit/test_exploration_budget.py`

**Steps:**
1. 失败测试：四类预算任一超限 → `*_BUDGET_EXCEEDED`，Loop 立即停止；`max_llm_calls` 实际底层是 P1 `LLMBudget` 实例（断言类型，不是自己写的计数器）。
2. Run → PASS → Commit: `feat(p3): exploration budget + shared llm budget (P3-C step3)`

### Task 3.4: Loop Detection（F8 复用）

**Objective:** 设计 7.3 节，基于 P2 `screen_fingerprint`。

**Files:**
- Create: `explorer/loop_detector.py`（`is_loop`，调用 P2 既有 fingerprint 函数）
- Test: `tests/unit/test_loop_detector.py`

**Steps:**
1. 失败测试：构造 `Home→Search→Home→Search→Home` fixture → 识别为 `STATE_LOOP`；`max_repeated_state` 生效；fingerprint 函数复用断言（同 Task 2.3 的手法）。
2. Run → PASS → Commit: `feat(p3): loop detection reusing p2 fingerprint (P3-C step4)`

### Task 3.5: Discovery Event + 环境隔离（F6/F7）

**Objective:** 设计 7.4/7.5 节，sandbox-only + 候选资产走统一审核。

**Files:**
- Create: `candidates/discovery.py`（`discovery_events` 表读写，PENDING→ACCEPT/REJECT）
- Modify: `explorer/agent.py`（启动前校验 `env.kind == sandbox`，**在启动时拒绝，不是运行到一半才拦**）
- Test: `tests/unit/test_discovery_event.py`、`tests/fault_injection/test_p3_matrix_m3.py`

**Steps:**
1. 失败测试：`env.kind == production` → Explorer 直接拒绝启动（F7，矩阵验证点）；发现新 Screen/Transition → 写 `discovery_events`，状态 PENDING，**不自动**写入 Source/Runtime Graph 的"已知"集合；ACCEPT 后才纳入已知集合。
2. Run → PASS → Commit: `feat(p3): discovery events + sandbox-only enforcement (P3-C step5)`

### Task 3.6: `mta explore` CLI + 真机/模拟器 Gate 演示

**Objective:** 设计 12 节 CLI；端到端验证本里程碑全部约束。

**Files:**
- Modify: `cli/main.py`（`explore --goal "..." --budget <file>`）
- Create: `phase0/verify_p3_m3.py`（真实 Sandbox 环境跑一次探索任务，断言 Budget 生效、Discovery Event 产出、无资产被自动修改）
- Test: `tests/unit/test_cli_explore.py`

**Steps:**
1. 失败测试 + CLI 集成。
2. Sandbox 真机/模拟器演示：给定目标"探索某页面未覆盖状态"，跑完整链路。
3. Run → PASS → Commit: `feat(p3): mta explore cli + m3 demo (P3-C)` + tag `checkpoint-p3-m3`

---

## M4 — Failure Diagnosis（设计 8 节 / P3-D）

> **Gate M4**：对一次真实或注入故障产出带 Evidence Score 的 Bug Candidate；矩阵 #9/#10/#13 全过。

### Task 4.1: diagnosis 数据模型

**Objective:** 设计 8.1/8.5/11 节 `Hypothesis`/`BugCandidate` + `bug_candidates` 表。

**Files:**
- Create: `diagnosis/__init__.py`、`diagnosis/models.py`（`Hypothesis`/`BugCandidateDraft`，`attribution` 字段默认 `UNTRIAGED`，F10）、`diagnosis/migrations/004_bug_candidates.sql`（沿用 P2 迁移机制）
- Test: `tests/unit/test_diagnosis_models.py`

**Steps:**
1. 失败测试：`Hypothesis` 必须带 `confidence` 和 `evidence` 列表（不允许空证据的裸结论，这是 F10 的第一道防线）；`BugCandidateDraft.attribution` 默认不等于任何具体归因值。
2. Run → PASS → Commit: `feat(p3): diagnosis models (P3-D step1)`

### Task 4.2: 规则引擎优先，LLM 补充（F10）

**Objective:** 设计 8.2 节，规则给不出结论才用 LLM。

**Files:**
- Create: `diagnosis/classifier.py`（`rule_based_hypotheses`：复用 P1 `APP_CRASH` 判定、P2 Local Reconciliation 结果作为 `UI_DRIFT` 的直接证据源）、`diagnosis/analyzer.py`（规则优先，规则空结果才调 LLM，LLM 输出同样是带 confidence 的 Hypothesis，不是结论）
- Test: `tests/unit/test_classifier.py`

**Steps:**
1. 失败测试：App Crash 场景 → 规则直接给出 `APP_DEFECT` candidate，**不调用 LLM**（省成本也更可信）；Locator 漂移 + Reconciliation 命中 → `UI_DRIFT` candidate；规则无法判定的场景 → 走 LLM，但输出仍标注 `origin: llm_hypothesis`，权重最低（呼应设计 8.3 的 `llm_hypothesis_only: 1`）。
2. Run → PASS → Commit: `feat(p3): rule-first diagnosis + llm fallback (P3-D step2)`

### Task 4.3: Evidence Score

**Objective:** 设计 8.3 节，权重为初版，需真实数据校准。

**Files:**
- Create: `diagnosis/evidence.py`（`evidence_score` 纯函数，权重从 `policy.yaml` 读取，不写死在代码里，方便后续校准）
- Test: `tests/unit/test_evidence_score.py`

**Steps:**
1. 失败测试：各证据项按配置权重累加；Evidence Score 高于 LLM confidence 在排序中的实际优先级（断言 Score 决定 severity 排序，而不是 confidence）。
2. Run → PASS → Commit: `feat(p3): evidence score (P3-D step3)`

### Task 4.4: Reproducibility Engine（F9 复用）

**Objective:** 设计 8.4 节，复用 P1 阶段判定，不重新推断。

**Files:**
- Create: `diagnosis/reproducer.py`（`reproduce`：**直接复用 P1 `agent/recovery.py` 的 PRE_DISPATCH/POST_DISPATCH 判定函数**）
- Test: `tests/unit/test_reproducer.py`、`tests/fault_injection/test_p3_matrix_m4.py`

**Steps:**
1. 失败测试：幂等+LOW风险 → 最多重跑 3 次；非幂等 POST_DISPATCH 失败 → 查 postcondition，成立则不计入失败复现（矩阵验证点，F9）；非幂等且无法判断 postcondition → 记"无法确认复现"，不计入 `reproduction_count`，Evidence Score 不加分。
2. Run → PASS → Commit: `feat(p3): reproducibility engine reusing p1 dispatch phases (P3-D step4)`

### Task 4.5: Bug 去重（F8 复用）

**Objective:** 设计 8.5 节 `dedup_key`。

**Files:**
- Create: `diagnosis/deduplicator.py`（复用同一哈希工具，输入为 screen+runtime diff+source change）
- Test: `tests/unit/test_bug_dedup.py`

**Steps:**
1. 失败测试：三个不同 testcase 失败但同一根因 → 合并为一个 Bug Candidate，`affected_tests` 累加而不是生成三条记录。
2. Run → PASS → Commit: `feat(p3): bug deduplication (P3-D step5)`

### Task 4.6: Bug Candidate 统一审核 + `mta diagnose` CLI

**Objective:** 设计 8.5 节状态机 + F6 统一审核。

**Files:**
- Create: `candidates/bug.py`（`DISCOVERED→ANALYZING→CANDIDATE→HUMAN_REVIEW→{CONFIRMED|REJECTED|DUPLICATE}`）
- Modify: `cli/main.py`（`diagnose --run-id <id> --step <n>`，`candidate review` 扩展支持 bug 类型）
- Test: `tests/unit/test_bug_review.py`

**Steps:**
1. 失败测试：状态机全路径；CONFIRMED 后进入"正式 Bug/Knowledge"（具体落地到 issue tracker 或内部 Knowledge Base，按团队实际工具对接，此处只要求**产出结构化 Evidence Bundle**，不规定对接哪个外部系统）。
2. Gate 演示：对一次真实（或可控注入的）故障跑通规则判定→Evidence Score→复现→去重→人工确认全链路。
3. Run → PASS → Commit: `feat(p3): mta diagnose + bug candidate review (P3-D)` + tag `checkpoint-p3-m4`

---

## M5 — Subagent + 自主回归闭环（设计 9/10 节 / P3-E）

> **Gate M5**：新 Build 端到端跑通 Diff→Plan→补测→执行→探索→诊断→Bug Candidate→人工确认，全程无资产被自动修改；矩阵 #2/#12 全过；Agent Benchmark 可运行并产出对比数据。

### Task 5.1: Agent 数据模型 + 状态机

**Objective:** 设计 9.2/11 节 `agent_tasks`/`agent_trace` + 状态机（含 `ESCALATED`）。

**Files:**
- Create: `agents/__init__.py`、`agents/models.py`（`AgentTask`/`AgentTrace`/`AgentState` 枚举）、`agents/migrations/005_agent_schema.sql`
- Test: `tests/unit/test_agent_models.py`

**Steps:**
1. 失败测试：状态机合法转换表；`ESCALATED` 触发条件（连续 N 次 Guard 拦截 / LLM_INVALID_OUTPUT 连续多次 / budget 耗尽但目标未达成）作为纯函数可独立单测（F13）。
2. Run → PASS → Commit: `feat(p3): agent state machine + trace model (P3-E step1)`

### Task 5.2: 工具清单与 Executor 统一接入（F2/F12，本里程碑安全红线）

**Objective:** 设计 10/10.1 节，任何 Subagent 的工具调用都经同一 Executor+Guard。

**Files:**
- Create: `agents/manager.py`（`ALLOWED_TOOLS` 常量 + 工具分发器，分发时强制查表，未注册工具直接拒绝）
- Test: `tests/unit/test_tool_allowlist.py`

**Steps:**
1. 失败测试：`ALLOWED_TOOLS` 静态断言不包含 `delete_testcase`/`modify_expectation`/`modify_verified_experience`/`modify_repository_directly`/`production_api_call`/`real_payment`/`arbitrary_shell`/`arbitrary_python` 中任何一个（F2 的硬性检查，**这个测试应该作为 CI 的强制门禁，而不只是一次性验证**）；任意工具调用无论来自 Planner/Explorer/Diagnosis 哪个 Subagent，最终落到同一个 Executor 实例（F12，import 级别断言）。
2. Run → PASS → Commit: `feat(p3): tool allowlist + unified executor dispatch (P3-E step2)`

### Task 5.3: Context 检索薄封装（F3，呼应设计 9.3）

**Objective:** 确认"分层 Memory"概念落地为对 P2 `KnowledgeSources` 的直接调用。

**Files:**
- Create: `agents/context.py`（`build_agent_context`：组合 `knowledge/retrieval.py` 的四个查询，按当前任务的 Screen/Goal 过滤，**不做全文拼接**）
- Test: `tests/unit/test_agent_context.py`

**Steps:**
1. 失败测试：context 体积随任务收窄而不是随 Trace 总量增长；底层调用的确实是 `KnowledgeSources` 的方法（而非直接 SQL），import 级别断言。
2. Run → PASS → Commit: `feat(p3): scoped context retrieval (P3-E step3)`

### Task 5.4: Planner/Explorer/Diagnosis Agent 整合

**Objective:** 设计 9.1 节 Test Manager 协调三个 Subagent。

**Files:**
- Create: `agents/planner_agent.py`、`agents/explorer_agent.py`、`agents/diagnosis_agent.py`（分别薄封装 M1/M3/M4 已实现的模块，接入 `agents/manager.py` 的工具分发）
- Test: `tests/integration/test_subagent_coordination.py`

**Steps:**
1. 失败测试：三个 Subagent 各自预算独立不互相挤占（Planner 的 `max_llm_calls` 耗尽不影响 Explorer）；`ESCALATED` 状态能正确携带足够上下文（当前 goal、已执行步骤、拦截原因）供人工接手，而不是裸异常。
2. Run → PASS → Commit: `feat(p3): subagent coordination (P3-E step4)`

### Task 5.5: Agent Evaluation Benchmark

**Objective:** 设计 13.2 节，固定 Benchmark + 版本对比。

**Files:**
- Create: `agents/benchmark.py`（固定 case 集：已知 Locator Drift / Unknown Screen / Ambiguous Element / Backend Failure / App Crash / New Transition / Negative Scenario）
- Modify: `cli/main.py`（`agent benchmark run|compare <v1> <v2>`）
- Test: `tests/unit/test_benchmark.py`

**Steps:**
1. 失败测试：Benchmark 跑出的关键指标（Recovery/Diagnosis 准确率等）可对比两次结果，差异超过阈值标记 `AGENT_REGRESSION`。
2. Run → PASS → Commit: `feat(p3): agent evaluation benchmark (P3-E step5)`

### Task 5.6: 端到端自主回归闭环演示

**Objective:** 设计 14 节 Gate；对照 1.2 成功标准逐条记录。

**Files:**
- Create: `phase0/verify_p3_final.py`（新 Build → Diff → Plan → 补测 Candidate → Sandbox 执行 → 未覆盖区域 Bounded Exploration → 诊断失败 → Bug Candidate → 人工确认；每步 trace/库断言）
- Create: `docs/p3_demo_record.md`

**Steps:**
1. 全链路演示跑通并留档；明确记录"全程无 TestCase/Repository/Verified Experience/Assertion 期望值被自动修改"这一断言的证据。
2. 回归全量：`python -m pytest tests -q`（P0+P1+P2+P3 全部矩阵绿）。
3. Commit: `feat(p3): e2e autonomous regression loop demo` + tag `checkpoint-p3-m5` + `v0.3-p3-complete`

---

## 2. 执行注意事项（给实现者/子智能体）

1. **复用优先于实现**：本计划里反复出现"直接 import，不新写""断言调用了同一个函数对象"这类要求，不是风格偏好，是防止 P3 退化成"看起来接了 P1/P2，实际各写一份"。每个任务涉及复用的地方，测试里都应该有一条**显式的"是同一个函数/同一套逻辑"断言**（import 级别或行为等价性断言），而不仅仅是"结果看起来一致"。
2. **F2/F12 是整个计划的安全红线**：`ALLOWED_TOOLS` 的静态检查建议直接接入 CI 门禁（而不只是 M5 跑一次），防止后续任何一次 PR 不小心给 Agent 加上危险工具。
3. **M3（Exploration）的 sandbox-only 约束不能有例外**：任何因为"演示方便"而临时在真机/staging 之外的环境跑 Explorer 的冲动，都应该在 PR review 时被拒绝。
4. **回归底线**：每任务完成后 `python -m pytest tests -q` 全绿（P0/P1/P2 全部矩阵含在内）；P3 未启用时行为与 P2 完全一致。
5. **真机/Sandbox 集中点**：M1（无需）、M2 Dry Run（Task 2.4）、M3 全部（风险最高，集中在此）、M4 Gate 演示（Task 4.6）、M5 端到端演示（Task 5.6）；其余全部 FakeDriver / 内存库单测（F13）。
6. **open questions（实现中遇到即停下确认，不猜）**：
   - Bug Candidate CONFIRMED 后具体对接哪个外部系统（Jira/内部 Issue Tracker/纯文件归档）—— 设计文档只要求产出结构化 Evidence Bundle，不规定下游；
   - Evidence Score / Priority Score 的具体权重需要多少真实数据才能开始校准，建议和 M4/M1 的 Gate 演示一起积累；
   - `ESCALATED` 状态的具体通知渠道（CLI 输出 / Slack / 邮件）由团队现有基础设施决定，不在本计划范围内。

## 3. 工作量与顺序摘要

| 里程碑 | 任务 | 预估 | Gate（含矩阵项） |
|---|---|---|---|
| M0 | 基线核实 | 0.5d | `docs/p3_baseline_audit.md` 产出 |
| M1 | 1.1~1.4 | 2d | 可解释 Plan；#3/#4 |
| M2 | 2.1~2.5 | 3d | Candidate 全流程；#5/#6/#8 |
| M3 | 3.1~3.6 | 4d | Sandbox 探索闭环；#1/#7/#8/#11 |
| M4 | 4.1~4.6 | 3.5d | Bug Candidate 全流程；#9/#10/#13 |
| M5 | 5.1~5.6 | 3.5d | 自主闭环演示；#2/#12 |

总计约 16.5 人日（不含真机/Sandbox/LLM 网关等待，不含 M0 核实后可能产生的 P2 缺口修复工作量）。
