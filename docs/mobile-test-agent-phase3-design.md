# Mobile Test Agent — Phase 3 设计文档

> 版本：v1.0-draft　日期：2026-10-07　前置：Phase 0/1/2 已完成
>
> **读者**：人类工程师理解 P3 的能力边界与安全架构；编码智能体依据第 2、5–10 节的接口/schema/复用关系动手实现。
>
> **一句话目标**：让系统利用 P0 的控制能力、P1 的稳定执行、P2 的经验与知识，**自主决定测什么、生成测试、在受限范围内探索、诊断失败**——但任何会改变正式资产（TestCase、Element Repository、Verified Experience）或触及真实风险的决策，仍然只能由人工批准。
>
> **P0 是手，P1 是身体，P2 是记忆，P3 是大脑**——但大脑不能绕过身体直接碰世界。

---

## 1. 目标、非目标、成功标准

### 1.1 五个核心方向（建议开发顺序）

```text
P3-A Test Planning → P3-B Test Generation → P3-C Bounded Exploration → P3-D Failure Diagnosis → P3-E Subagent/自主回归闭环
```

| 阶段 | 回答的问题 | 产出 |
|---|---|---|
| P3-A | 这次该测什么？ | 排好优先级的 Test Plan |
| P3-B | 还有什么没测？ | Candidate Test（需人工批准） |
| P3-C | 哪里还有未知状态？ | Discovery Event + Exploration Trace |
| P3-D | 这次失败意味着什么？ | Bug Candidate（需人工确认） |
| P3-E | 把以上串成自动回归闭环 | Autonomous Regression Loop |

### 1.2 成功标准

- 给定一个新 build 的 git diff，系统能自动产出有理由的 Test Plan（排序依据可解释，不是"LLM 觉得该测这个"）。
- 生成的 Candidate Test 经 Schema/语义/去重校验后，人工接受率可统计（而不是只看生成数量）。
- Bounded Exploration 在 Sandbox 中运行，预算耗尽/检测到 Loop/触达 Screen 边界会自动停止，不会失控。
- 对一次真实故障注入（如 P2 的漂移场景 + 一次真实 App Bug），Diagnosis 能输出带证据的 Bug Candidate，而不是裸的 LLM 结论。
- 全程：Agent **不能**修改正式 TestCase / Element Repository / Verified Experience / Assertion 期望值；不能触达生产或真实支付。
- 有一个固定 Benchmark，Agent 版本升级后可以跑出"是否退化"的对比数据（第 13 节）。

### 1.3 明确不做

完全自由探索（无预算、无 Sandbox 约束）、生产环境自主操作、多模型分布式协同（模型路由列为可选项，见 9.4）、把 UNTRIAGED 失败强行归因为确定结论。

---

## 2. 硬约束（编码智能体必须遵守，延续 P0–P2 的 H/E 系列编号，此处用 F 前缀）

| ID | 约束 |
|----|------|
| F1 | **风险判定复用 P1 `effective_risk` + Guard**，不新建风险枚举或判定逻辑。Exploration/Generation/Diagnosis 中任何会执行到 App 上的动作，都走同一个 Guard。 |
| F2 | **Agent 不持有任何直接修改正式资产的工具**。`modify_repository_directly` / `delete_testcase` / `modify_expectation` / `modify_verified_experience` 这类工具**不存在**于 Tool 清单中（第 6 节）。资产变更只能通过 `create_*_proposal` 系列工具，走人工审核。 |
| F3 | **知识检索复用 P2 `KnowledgeSources` Protocol**（`experience_lookup / graph_query / impact_of / trace_history`），不新建平行的检索接口。 |
| F4 | **LLM 预算复用 P1 `LLMBudget`**：每个 Agent 任务持有一个独立配额的 `LLMBudget` 实例，熔断逻辑不重写。 |
| F5 | **Candidate Test 就是 P1 TestCase**，只是多 `status: CANDIDATE` 与溯源字段；Schema 校验直接复用 `mta lint`，不新建校验器。 |
| F6 | **Review 机制统一**：Candidate Test / Bug Candidate / Discovery Event 的"AI 提议 → 人工 Accept/Reject → 进入正式资产"遵循与 P2 `recovery_reviews`/`promotion_proposals` 相同的模式（状态机、retention、CLI 范式）。 |
| F7 | **环境隔离复用 P1 `env.kind`**：Exploration 默认只允许 `env.kind == sandbox`；真机只能运行"已审核的 Test Plan"；`production` 下 Autonomous Agent 整体禁用（不是"风险高就拦"，是"环境就不允许启动"）。 |
| F8 | **Fingerprint 复用 P2 `screen_fingerprint` 机制**。Test Fingerprint = screen 序列 + action 序列 + assertion 的哈希，用同一套哈希工具，不新造一套相似度算法。 |
| F9 | **非幂等动作的 Recovery/Reproduction 复用 P1 7.4/7.5 的阶段判定**（PRE_DISPATCH / POST_DISPATCH + postcondition），不重新定义"怎么判断动作有没有发出去"。 |
| F10 | Diagnosis 产出的是 **Hypothesis（带 evidence 和置信度），不是 Fact**；`failure_attribution` 默认 `UNTRIAGED`，只有达到证据阈值才建议归因，最终仍由人工确认。 |
| F11 | Agent 的每一步行动都必须有 `max_steps / max_duration / max_llm_calls / max_repeated_state` 预算，超限即 `*_BUDGET_EXCEEDED` 并停止，不自动放宽重试。 |
| F12 | Subagent **没有额外权限**。所有工具调用无论来自哪个 Subagent，最终都经过同一个 Executor + Policy，不存在"Subagent 可以绕过主 Agent 的安全检查"的路径。 |
| F13 | 所有决策类逻辑（Planner 评分、去重判定、Evidence Score、Loop Detection）必须是可脱离设备单测的纯函数（沿用 P1 E18/E13 的习惯）。 |

---

## 3. 总体架构

```text
Git Diff / Requirement
        │
        ▼
   Test Planner ──uses──► KnowledgeSources(P2) + Impact Analysis(P2) + Trace History(P1/P2)
        │
        ▼
   Test Plan（既有用例排序 + 需要补的 Coverage Gap）
        │
        ├──► Test Generator ──► Candidate Test ──(Schema=P1 lint, 语义校验, 去重, Dry Run@Sandbox)──► 人工审核 ──► 正式 TestCase（P1 Repository 流程）
        │
        ▼
   Autonomous Agent（Planner/Explorer/Diagnosis 三个 Subagent，统一走 Executor+Guard）
        │
        ▼
   iOS App（Sandbox / UITest Build）
        │
   ┌────┴────┐
 PASS       FAIL
   │          │
   │          ▼
   │     Diagnosis Engine ──► Bug Candidate（Evidence Score, 去重, 复现）──► 人工确认 ──► 正式 Bug / Knowledge
   │
   ▼
Trace（P1）+ Agent Trace（P3 新增）──► Agent Evaluation Benchmark
```

### 3.1 与 P1/P2 的复用矩阵（写代码前必须对照）

| P3 概念 | 复用的 P1/P2 能力 | 不允许新建 |
|---|---|---|
| Exploration/Generation 风险判定 | P1 `effective_risk` + Guard | 新的风险枚举/判定函数 |
| 知识检索 | P2 `KnowledgeSources` | 新的检索接口 |
| LLM 预算 | P1 `LLMBudget` | 新的熔断器 |
| Candidate Test Schema | P1 `TestCase` + `mta lint` | 新 Schema |
| 资产变更审核 | P2 `recovery_reviews`/`promotion_proposals` 模式 | 新审核状态机 |
| 环境隔离 | P1 `env.kind` / `policy.yaml` | 新的沙箱判定 |
| Fingerprint | P2 `screen_fingerprint` 哈希逻辑 | 新相似度算法 |
| 非幂等判定/复现 | P1 7.4/7.5 阶段判定 | 新"是否已发生"推断逻辑 |
| Element Repository 变更落地 | P2 `promoter.py`（写 `overrides/`，Git 管理） | 新的配置写入路径 |

---

## 4. 资产权限分级

```text
Read-only Knowledge   Agent 可读：Source Metadata / UI Graph / Trace / Experience / Impact Analysis
Candidate Assets      Agent 可创建：Candidate Test / Bug Candidate / Discovery Event
Production Assets     需人工批准：正式 TestCase / Element Repository / Verified Experience
Critical Actions      默认禁止：真实支付 / 删除真实数据 / 生产环境操作
```

```python
class AssetTier(str, Enum):
    READ_ONLY = "READ_ONLY"
    CANDIDATE = "CANDIDATE"
    PRODUCTION = "PRODUCTION"
    CRITICAL = "CRITICAL"

TOOL_ASSET_TIER: dict[str, AssetTier] = {
    "get_current_screen": AssetTier.READ_ONLY, "get_relevant_experience": AssetTier.READ_ONLY,
    "create_test_candidate": AssetTier.CANDIDATE, "create_bug_candidate": AssetTier.CANDIDATE,
    "create_discovery_event": AssetTier.CANDIDATE,
    # PRODUCTION / CRITICAL 不出现在任何 Agent 可调用的工具表里（F2）
}
```

---

## 5. Test Planner（P3-A）

### 5.1 输入输出

```python
class PlannerInput(BaseModel):
    app_build: str
    git_commit: str
    changed_files: list[str]            # git diff 结果

class TestPlanTask(BaseModel):
    testcase_id: str
    priority: int                        # 0-100，确定性评分
    reasons: list[str]                   # 可解释，不是"LLM 觉得"
    source: Literal["existing", "generated_gap"]

class TestPlan(BaseModel):
    schema_version: str = "0.1"
    plan_id: str
    app_build: str; git_commit: str
    tasks: list[TestPlanTask]
```

### 5.2 评分：确定性打分 + LLM 只做解释/微调

```python
def priority_score(tc: TestCaseMeta, impact: ImpactResult, history: FailureHistory, risk: Risk) -> int:
    """
    纯函数（F13），输入全部来自 P1/P2 已有数据源：
      - impact: KnowledgeSources.impact_of(changed_element) 得到的"受影响用例"
      - history: KnowledgeSources.trace_history(filters) 统计出的历史失败率
      - risk: 用例所涉及元素的 effective_risk（P1）
    评分公式和权重是初版，需在真实数据上校准（沿用 P2 验证阈值的态度，不假装精确）。
    """
```

LLM 的角色：对评分结果给出**自然语言解释**（写入 `reasons`），以及在确定性评分打平手时做**次要排序**，**不允许**替代打分本身独立决定顺序（F13 的落地，呼应 review 第 9 条）。

### 5.3 Change-aware Regression（P3-D/E 阶段扩展）

```text
Git Diff → Impact Analysis(P2) → 受影响 Screen/Element/TestCase
                                        │
                        + 历史高失败率（KnowledgeSources.trace_history）
                        + 业务风险（effective_risk，CRITICAL/HIGH 强制高优先级，不管改动大小）
                                        ▼
                                  TestPlan
```

---

## 6. Test Generator（P3-B）

### 6.1 流程（F5/F6 的落地）

```text
Coverage Gap（UI Graph 未覆盖的 Transition/State）+ Bug History + Requirement
        ↓
LLM 生成候选步骤序列
        ↓
Schema Validation：直接复用 mta lint（target 是否存在、action 是否合法……）
        ↓
Semantic Validation：步骤序列是否与 UI Graph 的已知转移矛盾
        ↓
Duplicate Detection：Test Fingerprint（F8）命中已有用例 → SKIP
        ↓
Dry Run：仅在 env.kind == sandbox 执行一次（F7）
        ↓
Candidate Test（status: CANDIDATE，复用 P1 TestCase 格式）
        ↓
人工审核（复用 P2 review 模式，F6）→ 通过则落为正式 TestCase（走 P1 Repository/CI 流程，不是新路径）
```

### 6.2 Candidate Test（澄清 review 第 68 节的歧义）

```yaml
schema_version: "0.2"          # 与 P1 TestCase 同一 Schema（原写 "0.1" 系笔误，Task 3.1 勘误回填）
id: search_candidate_empty_result
name: 搜索空结果                 # P1 必填字段（原示例漏写，Task 3.1 勘误回填）
status: CANDIDATE               # 唯一新增字段之一
generated_by: generator_agent
generation_evidence:
  coverage_gap: ["Search -> EmptyResultState"]
  bug_history: []
  source_refs: ["SearchView.swift"]
suite: smoke
steps: [...]                    # 与正式 TestCase 完全相同的 step 语法
```

### 6.3 测试分类与覆盖率

```text
Happy Path / Boundary / Negative / Exception / State Transition / Concurrency-Timing / Recovery
```

Coverage 指标直接建立在 P2 UI Graph 之上：

```python
def coverage_gap(graph: ScreenGraph, existing_tests: list[TestCase]) -> list[UncoveredTransition]:
    """哪些 Source Graph 中存在、但现有用例从未触发过的 Transition。"""
```

---

## 7. Bounded Exploration（P3-C）

### 7.1 Agent Loop

```text
Observe（current_screen, ui_tree）
   ↓
Frontier 选择（P2 Graph 中 unexplored 的 Transition 优先，而不是随机点击）
   ↓
Guard（F1：同一个 effective_risk 判定，LOW 才允许自动执行）
   ↓
Execute → Verify（新 State？新 Transition？）
   ↓
新 → 记 Discovery Event（Candidate Asset）；旧 → 按 Loop Detection 处理
```

### 7.2 Budget（F11，默认值需在真实运行后校准）

```yaml
exploration:
  max_steps: 100
  max_duration_seconds: 600
  max_llm_calls: 20
  max_repeated_state: 3        # 复用 P2 screen_fingerprint 判定"重复状态"（F8）
```

### 7.3 Loop Detection

```python
def is_loop(visited_fingerprints: list[str], current_fingerprint: str, window: int, max_repeat: int) -> bool:
    """fingerprint 来自 P2 的同一套哈希工具（F8），不是新的相似度判断。"""
```

### 7.4 环境约束（F7）

```text
Exploration 默认只能: Simulator + UITest Build + env.kind == sandbox
真机: 只运行已审核的 Test Plan，不运行自由探索
production: Autonomous Agent 整体不可用（policy.yaml 启动时校验，不是运行时才拦）
```

### 7.5 Discovery Event → 正式资产

```text
Discovery Event（新 Screen/Transition/Element）
      ↓
Candidate（与 P2 Graph 的 ADDED 分类一致，不是新概念）
      ↓
人工审核（F6）
      ↓
纳入 Source/Runtime Graph 的"已知"集合
```

---

## 8. Failure Diagnosis（P3-D）

### 8.1 输入（全部来自已有系统，不新建数据源）

```text
Test Trace（P1） + Screenshot/UI Tree（P1） + App/Crash/Network Logs
+ Source Diff（P2 Graph Diff） + Experience（P2） + Historical Failures（KnowledgeSources.trace_history）
```

### 8.2 规则优先，LLM 补充（F10）

```python
def rule_based_hypotheses(ctx: DiagnosisContext) -> list[Hypothesis]:
    """
    先过确定性规则，例如：
      - app crash 检测到 → APP_DEFECT candidate（复用 P1 8.2 的 APP_CRASH 判定）
      - API 5xx + UI 正常 → ENVIRONMENT_DEFECT candidate
      - Locator 漂移 + Source/Runtime Diff 命中 → UI_DRIFT candidate（复用 P2 Local Reconciliation 结果）
      - 断言值不符 → TEST_ASSERTION_FAILURE（不走 Diagnosis，直接是 P1 既有结论）
    规则给不出结论的，才补充 LLM 的组合推理，仍然只产出带 confidence 的 Hypothesis。
    """
```

### 8.3 Evidence Score（替代单纯 LLM confidence，权重为初版）

```yaml
evidence_score_weights:        # 初版，需用真实 Bug 数据校准，不是精确值
  crash_detected: 5
  reproducible_3_times: 5
  source_change_correlated: 3
  same_issue_multiple_tests: 3
  backend_evidence: 2
  llm_hypothesis_only: 1
```

### 8.4 Reproducibility（F9 的落地，解决 review 第 5 条的空白）

```text
idempotent + effective_risk == LOW:
    最多重跑 3 次，PRE_DISPATCH 失败直接计入复现次数

non-idempotent:
    不盲目重跑；按 P1 7.4/7.5：
      - PRE_DISPATCH 阶段的失败可以安全重试判定复现
      - POST_DISPATCH 阶段失败，查 postcondition：
          成立 → 不算复现失败（原始动作其实成功了，是别的原因导致后续断言异常）
          不成立/无法判断 → 计一次"无法确认复现"，不计入 reproduction_count，
                              Evidence Score 不给 reproducible 加分
```

### 8.5 Bug Candidate 状态机与去重（F6）

```text
DISCOVERED → ANALYZING → CANDIDATE → HUMAN_REVIEW → {CONFIRMED | REJECTED | DUPLICATE}
```

```python
def dedup_key(bug: BugCandidateDraft) -> str:
    """同 screen + 同 runtime diff + 同 source change → 合并，用 F8 的同一套哈希工具。"""
```

---

## 9. Subagent 与 Agent 架构（P3-E）

### 9.1 分工（F12：无额外权限）

```text
Test Manager（协调）
   ├── Planning Agent   —— 只产出 TestPlan，不执行
   ├── Exploration Agent —— 只在 Sandbox 执行，Budget 约束
   └── Diagnosis Agent  —— 只读 Trace/Logs，产出 Hypothesis
```

所有三个 Subagent 的工具调用都落到**同一个** `Executor + Guard`（第 10 节），没有任何 Subagent 专属的"绕过"路径。

### 9.2 Agent 状态机

```text
IDLE → PLANNING → EXECUTING → OBSERVING → DECIDING → VERIFYING
异常：ANALYZING → RECOVERING → VERIFYING
严重异常：ESCALATED（触发条件：连续 N 次决策被 Guard 拦截 / LLM 判定为 CRITICAL 操作 /
          budget 耗尽但目标未达成 / 连续 LLM_INVALID_OUTPUT）
```

`ESCALATED` 状态**不是**错误状态，是"系统认为需要人介入"的正常退出路径，必须附带足够上下文（当前 goal、已执行步骤、拦截原因）供人工快速接手。

### 9.3 Context 管理（F3 的落地，取代 review 第 54–59 节的"分层 Memory"措辞）

```python
def build_agent_context(task: AgentTask, knowledge: KnowledgeSources) -> AgentContext:
    """
    只检索当前任务相关的子集，全部经由 P2 的 KnowledgeSources：
      current_screen       -> knowledge.graph_query(scope=current_screen)
      relevant_experience  -> knowledge.experience_lookup(app_id, screen_id, target_id)
      relevant_history     -> knowledge.trace_history(filters={screen: current_screen})
      relevant_source      -> source_metadata 的当前 Screen 子集（P1/P2 已有）
    不做"把全部 Source + 全部 Trace 塞进去"的全文 RAG。
    """
```

### 9.4 模型路由（可选，不在 P3-A~E 核心范围）

本地小模型 / 强模型 / Vision Model 的分级路由（原文第 90–91 节）作为**后续优化项**单独立项，不纳入 P3-A~E 的里程碑验收；P3 初版统一用一个模型即可跑通全部能力。LLM Escalation 到人工的触发条件（Security/Financial/Destructive/Ambiguous）仍然保留，这是安全边界不是性能优化。

---

## 10. 安全架构（贯穿全部模块）

```text
Agent (Planner/Explorer/Diagnosis/Subagent)
        │
        ▼
   Proposed Action
        │
        ▼
Deterministic Policy（= P1 Guard，F1）
        │
   ┌────┴────┐
 ALLOW      BLOCK
   │          │
   ▼          ▼
Executor    Audit（写 Trace，不是静默丢弃）
   │
   ▼
  iOS App（env.kind 已在启动时校验，F7）
```

任何 Agent、任意 LLM 输出、任意 Subagent，都不能绕过 `Policy → Executor → Environment → Trace` 这四层（与原文第 104 节一致，这里强调：没有例外路径）。

### 10.1 工具清单（F2 的落地）

```python
ALLOWED_TOOLS = {
    # 只读
    "get_current_screen", "get_ui_tree", "get_relevant_source",
    "get_relevant_experience", "get_graph_neighbors", "get_logs",
    "get_network_summary", "get_crash_info",
    # 执行（仍经 Guard）
    "tap", "input", "swipe", "back", "wait",
    "assert_exists", "assert_text", "screenshot",
    "run_testcase", "run_candidate_test",
    # 创建候选资产（Candidate Tier）
    "create_test_candidate", "create_bug_candidate", "create_discovery_event",
    "create_promotion_proposal",          # 唯一能"建议"修改 Repository 的方式（F2）
}
# 以下工具永不出现在任何 Agent 的工具表里：
# delete_testcase / modify_expectation / modify_verified_experience /
# modify_repository_directly / production_api_call / real_payment /
# arbitrary_shell / arbitrary_python
```

---

## 11. 数据模型（SQL，新增到独立的 `agent.db`，延续 P2"按写者边界分库"的原则）

```sql
CREATE TABLE test_plans (
    plan_id TEXT PRIMARY KEY, app_build TEXT, git_commit TEXT,
    created_at TEXT, tasks_json TEXT
);

CREATE TABLE test_candidates (
    candidate_id TEXT PRIMARY KEY, testcase_yaml TEXT NOT NULL,
    generated_by TEXT, generation_evidence_json TEXT,
    fingerprint TEXT,                      -- F8
    dry_run_status TEXT,                   -- PENDING/PASS/FAIL
    review_status TEXT DEFAULT 'PENDING',  -- 同 P2 review 模式，F6
    reviewer TEXT, reviewed_at TEXT, created_at TEXT
);

CREATE TABLE discovery_events (
    event_id TEXT PRIMARY KEY, kind TEXT,   -- NEW_SCREEN/NEW_TRANSITION/NEW_ELEMENT/ANOMALY
    detail_json TEXT, exploration_task_id TEXT,
    review_status TEXT DEFAULT 'PENDING', created_at TEXT
);

CREATE TABLE bug_candidates (
    candidate_id TEXT PRIMARY KEY, title TEXT, severity TEXT,
    failure_type TEXT, attribution_hypothesis TEXT,   -- F10：只是 hypothesis
    evidence_json TEXT, evidence_score INTEGER,
    reproducible INTEGER, reproduction_count INTEGER,
    dedup_key TEXT, status TEXT DEFAULT 'DISCOVERED',  -- F6 状态机
    affected_tests_json TEXT,
    reviewer TEXT, review_decision TEXT, reviewed_at TEXT,
    created_at TEXT
);

CREATE TABLE agent_tasks (
    task_id TEXT PRIMARY KEY, agent_type TEXT,          -- planner/explorer/diagnosis
    goal TEXT, constraints_json TEXT,                     -- max_steps/max_llm_calls/...
    state TEXT,                                            -- Agent 状态机（9.2）
    start_time TEXT, end_time TEXT, outcome TEXT
);

CREATE TABLE agent_trace (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
    step_index INTEGER, state TEXT, goal TEXT,
    observation_json TEXT, decision_json TEXT,
    guard_result TEXT, result TEXT, novelty REAL,
    llm_calls_used INTEGER, created_at TEXT
);
```

---

## 12. CLI

```bash
mta plan   --build <id> --base-build <id>            # Test Planner
mta generate --gap-from <plan_id>                    # Test Generator，输出 Candidate
mta explore --goal "..." --budget exploration.yaml   # Bounded Exploration（仅 sandbox）
mta diagnose --run-id <id> --step <n>                # Failure Diagnosis
mta candidate review list|accept|reject <id>         # 统一的候选资产审核（F6）
mta agent benchmark run|compare <v1> <v2>             # Agent Evaluation（第 13 节）
```

---

## 13. 指标与 Benchmark

### 13.1 五组指标

```text
Planning:   Test Selection Precision / Risk Coverage / Change Coverage
Generation: Generated / Accepted / Duplicate Rate / Coverage Gain
Exploration: New Screens/Transitions/Elements / Bugs Found / Information Gain per Action
Diagnosis:  Diagnosis Accuracy / False Positive Rate / Reproduction Rate / Duplicate Bug Rate
Overall:    Regression Time Saved / Tests Avoided（Coverage 不下降的前提下）/ Bugs Found / LLM Cost / Human Review Cost
```

**最重要的不是 LLM 调用量，而是 Coverage / Cost / Human Effort 三者的关系**：测试数量下降、耗时下降，但 Coverage 不能下降，人工复核量应该下降而不是上升（否则 Agent 只是把工作量转移给了人）。

### 13.2 Agent Evaluation Benchmark（F13 的延伸）

```text
固定 Benchmark Cases：已知 Locator Drift / Unknown Screen / Ambiguous Element /
                      Backend Failure / App Crash / New Transition / Negative Scenario
每次修改 Agent（Prompt、模型、规则）→ 跑一遍 Benchmark → 与上一版本对比
→ 若关键指标（如 Recovery/Diagnosis 准确率）下降，标记 AGENT_REGRESSION
```

Agent 本身的质量回归检测，和 App 回归测试是并列的两件事，都需要常态化监控。

---

## 14. 里程碑

| 里程碑 | 产出 | Gate |
|---|---|---|
| P3-A Test Planning | `planner/` | 给定 Build 改动，输出可解释的排序 Plan（不依赖自主探索） |
| P3-B Test Generation | `generator/` | 对已知 Coverage Gap 生成 Candidate，Schema/去重/Dry Run 全过，人工可审核 |
| P3-C Bounded Exploration | `explorer/` | Sandbox 内完成一次探索任务，预算/Loop Detection 生效，产出 Discovery Event |
| P3-D Failure Diagnosis | `diagnosis/` | 对一次真实/注入故障产出带 Evidence Score 的 Bug Candidate，而非裸 LLM 结论 |
| P3-E Subagent + 自主回归闭环 | `agents/` | 新 Build 端到端跑通：Diff→Plan→补测→执行→探索→诊断→Bug Candidate→人工确认，全程无资产被自动修改 |

### 14.1 目录结构

```text
mobile-test-agent/
├── planner/      planner.py prioritizer.py impact.py risk.py
├── generator/    generator.py validator.py deduplicator.py coverage.py
├── explorer/     agent.py policy.py frontier.py state.py loop_detector.py
├── diagnosis/    analyzer.py classifier.py evidence.py reproducer.py deduplicator.py
├── agents/       manager.py planner_agent.py explorer_agent.py diagnosis_agent.py
├── candidates/   test_case.py bug.py promotion.py
└── knowledge/    retrieval.py        # 薄封装，内部直接调用 P2 KnowledgeSources（F3）
```

---

## 附录：关键设计决策记录

| 决策 | 理由 |
|---|---|
| 风险判定、知识检索、LLM 预算、Schema 校验、审核流程全部复用 P1/P2 现有实现 | P3 最大的工程风险不是"Agent 不够聪明"，而是"平行维护多套本质相同的机制导致行为不一致"；第 3.1 节的复用矩阵是整份文档里最该先确认的部分 |
| Candidate Test = P1 TestCase + 状态字段，不是新 Schema | 避免生成的测试和正式测试走两套校验/执行逻辑，批准后也不需要格式转换 |
| Diagnosis 产出 Hypothesis + Evidence Score，不产出 Fact | 自动诊断系统最大的风险是"看起来很智能地给错结论"；证据分优先于 LLM confidence |
| Evidence Score 权重、Planner 评分权重均标注为初版 | 延续 P2 对阈值的态度：先有真实数据，再谈精确校准 |
| 模型路由移出核心里程碑 | 属于性能优化，不是安全或正确性问题，不应该拖慢 P3-A~E 的交付 |
| Subagent 无额外权限，统一走 Executor+Guard | 拆分 Subagent 是为了降低单个 Agent 的 context 复杂度，不是为了获得新的执行通道 |
| Agent 本身需要 Benchmark 监控 | Agent 能力会随 Prompt/模型变化而变化，这种变化本身需要像代码回归一样被检测 |
