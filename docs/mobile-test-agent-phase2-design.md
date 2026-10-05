# Mobile Test Agent — Phase 2 设计文档

> 版本：v1.0-draft　日期：2026-09-28　前置：Phase 0、Phase 1 已完成
>
> **读者**：人类工程师理解设计意图与边界；编码智能体可直接依据第 2、4–10、13–14 节的规则、接口、schema 动手实现。
>
> **一句话目标**：把 P1 中"LLM 临时解决过的问题"，转化成**经证据验证、带版本约束、每次使用仍要重新安全校验**的 Experience，使系统运行越久、越少依赖 LLM——但正式测试行为（Element Repository）只能经**人工 Promotion** 改变。
>
> P0 证明路通，P1 证明可信，**P2 证明可积累**。P2 不增加 Agent 自主性，只增加"复用已验证知识"的能力。

---

## 1. 目标、非目标、成功标准

### 1.1 目标

| # | 目标 | 验收要点 |
|---|------|---------|
| G1 | Experience Store（Candidate/Verified/Degraded/Rejected 状态机） | 状态转换规则可单测，不依赖设备 |
| G2 | Experience 命中后仍走完整 Runtime Guard | 与 P1 Recovery 候选校验同一套规则，不因 VERIFIED 而降级 |
| G3 | 非幂等 / 非 LOW 风险目标排除在自动验证之外 | 故障注入矩阵覆盖 |
| G4 | 滑动窗口降级 | 历史成功率高但近期失败也必须 DEGRADED |
| G5 | Build 集合式有效性 | 不使用区间假设 |
| G6 | 只从人工 ACCEPT 的 P1 Recovery 取种子 | REJECT 的不得进入 Candidate |
| G7 | Experience Store 单写者 | 并发场景下统计不丢更新 |
| G8 | Promotion 走 Git，不自建版本系统 | Rollback = `git revert` |
| G9 | Runtime/Source Graph + Diff + Impact Analysis | 复用 P1 已有的元素反向索引 |
| G10 | 指标：Experience Resolution Rate | 可观察到随运行次数上升 |

### 1.2 成功标准

- 完整演示：UI 漂移 → 第一次 LLM 恢复（人工 ACCEPT）→ 同 build 第二次命中 Experience（`LLM calls = 0`）→ 积累到 Verified → 人工 Promotion → 之后走确定性 Locator，不再进入 Recovery。
- Verified Experience 在人为制造"多匹配/风险变高/Screen 不符"时，必须被 Guard 拦截，而不是因为状态是 VERIFIED 就执行。
- 非幂等目标无论成功多少次，都不会被自动标记 VERIFIED。
- 50+ 条真实 Recovery 事件作为真实数据基础；Candidate→Verified→Degraded 完整状态转换至少演示一次。

### 1.3 明确不做（留给 P3）

自动生成用例、自动探索 App、自主测试规划、Multi-Agent/Subagent、强化学习、复杂 Vision Agent、Android、大规模设备调度。P2 只为 P3 准备数据（Experience / Graph / Impact Analysis），不实现规划能力本身。

---

## 2. 硬约束（编码智能体必须遵守）

| ID | 约束 |
|----|------|
| E1 | **Verified ≠ 永久可信。** 每次使用 Experience（无论 Candidate 还是 Verified）都要重新执行 P1 同一套 Runtime Guard：Screen 匹配、唯一性、类型一致、`effective_risk` 重新计算。不得因为 `status == VERIFIED` 跳过任何一项。 |
| E2 | `effective_risk` 永远由确定性信息（step/element/screen/环境）推导，不信任 LLM 自报值（沿用 P1 H4）。 |
| E3 | Experience **不得**修改断言期望值（沿用 P1 H6）。只能解决"目标在哪"，不能解决"期望是什么"。 |
| E4 | 自动 Candidate→Verified 的升级，仅适用于 `idempotency == IDEMPOTENT AND effective_risk == LOW` 的目标。非幂等或风险 ≥ MEDIUM 的目标**永远不进入自动验证循环**。 |
| E5 | Candidate 的初始种子**只能**来自 P1 `recovery_reviews` 中状态为 `ACCEPT` 的记录；`REJECT` 的不得进入 Experience Store。 |
| E6 | 降级判定用**滑动窗口**（默认最近 5 次，失败 ≥2 次触发），且**优先级高于**总体 success_rate；两者独立计算，窗口触发立即降级。 |
| E7 | Build 有效性用**集合**（`validated_builds`），不用区间；集合只在**动作真正执行成功**后才追加当前 build，仅通过静态校验不算。 |
| E8 | Screen Fingerprint 变化 → 标记 `REVALIDATION_REQUIRED`，**不**清空或拒绝该 Experience；重验证通过后更新 fingerprint 观测记录。 |
| E9 | Experience Store 是**单写者模型**：多个 Runner 并发时，统计更新必须串行化（单一写入者进程/队列，或约定实验相关套件串行跑），不得依赖数据库"恰好不冲突"。 |
| E10 | Promotion 只能由人工 Accept 触发；系统不得自动修改 Element Repository。Rollback 用 `git revert`，不另建版本管理系统。 |
| E11 | 滑动窗口 / 总体统计都**只统计"Experience 实际被尝试"的样本**：Guard 因 Screen 不匹配直接 MISS（该 Experience 根本不适用于当前场景）不计入失败，也不计入样本数。 |
| E12 | Graph 只来自已有 TestCase + 已有 Trace + Source Metadata，不做自动探索；未观察到的状态标 `NOT_OBSERVED`，不得标 `REMOVED`。 |
| E13 | 所有 Experience 相关的决策逻辑（Guard、状态机转换、排序）必须是可脱离设备的纯函数，并有单测覆盖。 |

---

## 3. 架构与运行时流程

```text
Test Runner → Deterministic Executor（含 Promoted Experience 的 Locator Chain）→ iOS App
                        │
                       FAIL
                        │
                        ▼
                 Recovery Engine（P1）
                        │
          ┌─────────────┼──────────────┐
          │             │              │
     WDA/Wait/     Local Recon.    Experience Store
     Settle Retry                       │
                                 ┌───────┴───────┐
                                 │               │
                               HIT             MISS
                                 │               │
                        （排序后逐个尝试）        ▼
                                 │              LLM
                                 ▼               │
                          Runtime Guard          ▼
                                 │           Candidate
                          ┌──────┴──────┐        │
                        PASS          FAIL  （需人工 ACCEPT 后才成为种子）
                          │          （换下一候选       │
                          ▼           或回落 LLM）   ExperienceStore
                       Execute

所有路径 → Trace（含 experience_* 事件）
                        │
          ┌─────────────┴─────────────┐
          ▼                           ▼
  Experience Analysis            UI Graph Engine
  （状态机 / 排序 / 降级）      （Runtime/Source Graph → Diff → Impact）
          │
          ▼
  Verified → Promotion Proposal → Human Review → Git Commit → Element Repository
          （之后进入 P1 正常 Locator Chain，很多情况不再进入 Recovery）
```

### 3.1 与 P1 的集成点（不新起机制）

| 能力 | 复用 P1 | P2 新增 |
|---|---|---|
| 候选校验（唯一/类型/Screen/风险） | `agent/recovery.py` 的候选校验逻辑 | 包成 `experience/runtime_guard.py`，Recovery 和 Experience 共用 |
| 脱敏 / 密钥 | `trace/redactor.py` / `SecretProvider` | 直接复用，不另建 |
| 元素 ↔ 用例反向索引 | `mta lint` 建立的索引 | Impact Analysis 直接查询，不重建 |
| Element Repository 合并规则 | `generated/overrides`，`origin` 字段 | 增加 `origin: experience`，走同一套合并与"不可静默覆盖"规则 |
| LLM Budget / Circuit Breaker | `llm/budget.py` | 不变；Experience 命中时根本不消耗 Budget |
| RECOVERED 语义、退出码 5 | 不变 | 细分为 `RECOVERED_LLM` / `RECOVERED_EXPERIENCE` / `RECOVERED_ASSERTION_TARGET`，仍然都 ≠ PASS |

---

## 4. Experience 数据模型

### 4.1 主键与范围

```text
主 Key: (app_id, screen_id, target_id)
```

`screen_fingerprint` **不是主键的一部分**，只是辅助校验"当前页面结构是否仍与历史观测相似"；fingerprint 变化触发 `REVALIDATION_REQUIRED`，不触发删除或拒绝（E8）。

### 4.2 字段

```python
class ExperienceStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    VERIFIED = "VERIFIED"
    DEGRADED = "DEGRADED"
    REJECTED = "REJECTED"

class Experience(BaseModel):
    experience_id: str
    app_id: str
    screen_id: str
    target_id: str
    strategy: LocatorStrategy              # 复用 P1 的 LocatorStrategy 定义
    origin: Literal["LLM_ACCEPTED_RECOVERY"]  # P2 唯一来源；未来可扩展
    status: ExperienceStatus

    # 总体统计（仅统计"实际被尝试"的样本，见 E11）
    sample_count: int = 0
    success_count: int = 0
    failure_count: int = 0

    validated_builds: list[str] = []       # 集合，不是区间（E7）
    last_screen_fingerprint: str | None = None

    # 可追溯性（E5 的落地）
    seed_run_id: str
    seed_step_id: int
    seed_recovery_review_id: int

    # Promotion 状态（与 Experience 自身状态是两条独立时间线，见 9.4）
    promoted: bool = False
    promoted_commit: str | None = None

    created_at: datetime
    updated_at: datetime
```

### 4.3 生命周期

```text
CANDIDATE ──(样本达标且满足 E4 资格)──► VERIFIED
CANDIDATE ──(90 天无新样本 / 显式拒绝)──► REJECTED        # 过期清理，见 9.6
VERIFIED  ──(滑动窗口触发，E6)──► DEGRADED
DEGRADED  ──(显式 revalidate 成功)──► VERIFIED
DEGRADED  ──(持续失败 / 人工判定)──► REJECTED
```

状态转换是**纯函数**，由 `ExperienceVerifier` 实现，不依赖设备：

```python
class VerificationDecision(str, Enum):
    PROMOTE_TO_VERIFIED = "PROMOTE_TO_VERIFIED"
    KEEP_CANDIDATE = "KEEP_CANDIDATE"
    DEGRADE = "DEGRADE"
    REJECT = "REJECT"
    NO_CHANGE = "NO_CHANGE"

class ExperienceVerifier:
    def evaluate(self, exp: Experience, runs: list[ExperienceRun],
                 policy: VerificationPolicy) -> VerificationDecision:
        """
        纯函数，输入 Experience 当前状态 + 完整 run 历史 + 策略配置，输出决策。
        不做任何 I/O，方便单测覆盖所有分支（见第 16 节故障注入）。
        """
```

### 4.4 自动升级资格（E4 的判定）

```python
def eligible_for_auto_verification(exp: Experience, element: EffectiveElement) -> bool:
    return (
        element.idempotency == Idempotency.IDEMPOTENT
        and element.effective_risk == Risk.LOW
    )
```

不满足 → 永远停留在 `CANDIDATE`（或由人工直接 Promotion，见 9.5），不管样本和成功率多高。

### 4.5 升级门槛（满足 4.4 之后才检查）

```yaml
verification_policy:
  min_samples: 10
  min_success_rate: 0.95
  min_distinct_runs: 3
```

`distinct_runs` = `experience_runs` 中不同 `run_id` 的计数（同一个 run 内多次命中只算一次 run，避免"同一个 testcase 反复点同一个按钮"虚增样本多样性）。

### 4.6 滑动窗口降级（E6 的判定）

```python
def sliding_window_degrade(runs: list[ExperienceRun], window: int = 5, max_failures: int = 2) -> bool:
    """
    只取"实际被尝试"的最近 N 条（见 E11：Guard 因 Screen 不匹配直接 MISS 的记录
    不应出现在这个 runs 列表里——它们根本不该被写入 experience_runs，见 4.7）。
    """
    recent = runs[-window:]
    failures = sum(1 for r in recent if r.result == "FAILURE")
    return failures >= max_failures
```

降级判定**优先于**总体 success_rate：即使总体 98%，只要滑动窗口触发，立即 `DEGRADED`。

### 4.7 什么算"一次样本"（E11 的落地，澄清上一版的模糊点）

| Guard 判定结果 | 是否写入 `experience_runs` | 算不算失败 |
|---|---|---|
| `CURRENT_SCREEN_UNKNOWN` 或 Screen 不匹配 | **不写**（该 Experience 本来就不适用于此刻） | 不计入 |
| 唯一性校验失败（0 个或 ≥2 个匹配） | 写，`result=FAILURE` | 计入失败 |
| 类型不匹配 | 写，`result=FAILURE` | 计入失败 |
| 风险校验未过（`effective_risk != LOW`） | **不写**（这是"此 Experience 当前不被允许使用"，不是"用了但错了"） | 不计入 |
| 通过 Guard，执行动作失败 | 写，`result=FAILURE` | 计入失败 |
| 通过 Guard，执行成功（含 postcondition 满足） | 写，`result=SUCCESS` | 计入成功，追加当前 build 到 `validated_builds` |

---

## 5. Experience Runtime Guard

```python
class GuardResult(BaseModel):
    outcome: Literal["EXECUTE", "MISS", "BLOCK"]
    reason: str | None = None     # TARGET_NOT_FOUND / TARGET_AMBIGUOUS / TYPE_MISMATCH /
                                   # SCREEN_MISMATCH / RISK_BLOCKED / SCREEN_UNKNOWN
    record_as_sample: bool        # 对应 4.7 的表

def experience_runtime_guard(exp: Experience, ctx: RuntimeContext, executor) -> GuardResult:
    if ctx.current_screen is None:
        return GuardResult(outcome="MISS", reason="SCREEN_UNKNOWN", record_as_sample=False)
    if ctx.current_screen != exp.screen_id:
        return GuardResult(outcome="MISS", reason="SCREEN_MISMATCH", record_as_sample=False)

    matches = executor.find_all(exp.strategy)
    if len(matches) == 0:
        return GuardResult(outcome="BLOCK", reason="TARGET_NOT_FOUND", record_as_sample=True)
    if len(matches) > 1:
        return GuardResult(outcome="BLOCK", reason="TARGET_AMBIGUOUS", record_as_sample=True)

    el = matches[0]
    expected_type = ctx.effective_element.type
    if el.type != expected_type:
        return GuardResult(outcome="BLOCK", reason="TYPE_MISMATCH", record_as_sample=True)

    effective_risk = compute_effective_risk(ctx)   # P1 规则：max(step, element, screen, env)，不信任 LLM
    if effective_risk != Risk.LOW:
        return GuardResult(outcome="BLOCK", reason="RISK_BLOCKED", record_as_sample=False)

    return GuardResult(outcome="EXECUTE", record_as_sample=True)
```

这套 Guard 与 P1 Recovery 中 LLM 候选的校验**是同一套实现**（`agent/recovery.py` 抽出共享函数），不是平行维护的第二套规则。

### 5.1 多候选排序与回落

同一 `(app_id, screen_id, target_id)` 可能存在多条历史 Experience（例如 App 经历过两次不同的改名）。Lookup 返回**排序后的列表**，逐个尝试，而不是只取一条：

```python
def rank_experiences(candidates: list[Experience]) -> list[Experience]:
    # 1. VERIFIED 优先于 CANDIDATE
    # 2. success_rate 高的优先
    # 3. 最近一次成功时间新的优先
    ...

def try_experiences(candidates: list[Experience], ctx, executor) -> RecoveryOutcome:
    for exp in rank_experiences(candidates):
        result = experience_runtime_guard(exp, ctx, executor)
        record_run_if_needed(exp, result, ctx)          # 按 4.7 决定是否写入
        if result.outcome == "EXECUTE":
            exec_result = executor.perform_and_check_postcondition(exp, ctx)
            record_execution_result(exp, exec_result)    # 成功才追加 validated_builds
            if exec_result.success:
                return RecoveryOutcome.recovered_experience(exp)
        # BLOCK 或执行失败 → 尝试下一个候选
    return RecoveryOutcome.fall_through_to_llm()
```

全部候选用尽才回落到 LLM（受 P1 Budget 控制）。

> **修订记录（Task 3.2 实现后回填，2026-10-05，review_p2_task32 P3-2）**：
> 伪代码的排序规则只写了两档；实现为四级信任档位 **VERIFIED > DEGRADED >
> CANDIDATE > REJECTED（排尾）**。DEGRADED 排在 CANDIDATE 之前意味着
> 「刚被 E6 滑动窗口判连续失败的候选」仍先于「零失败但未验证的候选」被
> 尝试——理由：DEGRADED 有真实历史（曾是 VERIFIED），排序只决定「先试
> 哪条」，每条各自仍走完整 Guard（E1），试错代价只是一次 Guard+执行，
> 不会误用；「有真实历史」优先于「零证据」。「无样本排尾」是**档位内**
> 限定（零样本 VERIFIED——9.5 人工 Promotion 跳过自动验证的路径——仍以
> 档位 0 排最前，「人工判断 > 统计」语义自洽）。同档同率按 `updated_at`
> （最后一次写时刻）作新近度代理，与 5.1 原义「最近一次成功时间」的
> 偏离已在 `experience/ranker.py` 模块 docstring 登记（含未来补
> `last_success_at` 列的 Schema 变更点）。REJECTED 排尾是防御性的——
> `lookup` 已按 7.1 修订排除 REJECTED，ranker 不做资格判定（那是 Guard
> 的事）。

---

## 6. 非幂等 / 高风险目标的处理

- 这类目标的 Experience **只能停留在 CANDIDATE**，不参与自动 10 次验证循环（E4）。
- 每次命中仍然可以使用（因为 Candidate 本身就是"曾经被人工 ACCEPT 过一次"），但**每次都视为需要重新走 Guard + Postcondition 校验**，不因为连续用了几次就提高自动信任度。
- 若业务需要把这类 Experience 转正：只能走**人工直接 Promotion**（9.5 的"跳过自动验证的人工路径"），不能靠统计样本量自动转正。
- 非幂等动作命中 Experience 后超时 → 不重试，查 postcondition（沿用 P1 7.4/7.5 的阶段判定规则），postcondition 确认成功才记一次 `SUCCESS` 样本。

---

## 7. Experience Store

### 7.1 接口

```python
class ExperienceStore(Protocol):
    def lookup(self, app_id: str, screen_id: str, target_id: str) -> list[Experience]: ...
    def create_candidate(self, seed: CandidateSeed) -> Experience: ...
    def record_run(self, experience_id: str, run: ExperienceRunRecord) -> None: ...
    def get_runs(self, experience_id: str, limit: int | None = None) -> list[ExperienceRun]: ...
    def update_status(self, experience_id: str, new_status: ExperienceStatus, reason: str) -> None: ...
    def list(self, status: ExperienceStatus | None = None) -> list[Experience]: ...
```

`CandidateSeed` 必须携带 `seed_run_id / seed_step_id / seed_recovery_review_id`（E5），缺任何一项拒绝创建。

> **修订记录（Task 2.1 实现后回填，2026-10-04）**：`lookup` 对 **REJECTED
> 状态的 Experience 由 Store 层排除**（人工判定「不可用」的策略不再被
> 消费路径看见；行仍在库，`list()` 审计视角全量可见）。§5.1 的
> `rank_experiences` 因此只对**可用候选**排序——「逐个尝试全部候选」的
> 设计语义自本条起收窄为「全部可用候选」。REJECTED 仍留在库里供审计。

### 7.2 单写者（E9）

```text
开发/单机：Runner → ExperienceStore → SQLite（天然单写者）
CI 多 Runner：Runner(N) → Experience Event Queue → 单一 Writer 进程 → SQLite
```

P2 不需要立即做成独立服务；约定：**凡是会产生 Experience 写操作（record_run / update_status / create_candidate）的套件，CI 中串行执行**，或者统一经过一个写入队列。Reader（lookup）可以并发。

### 7.3 Recovery Cache（运行时加速层，不是权威数据）

```python
cache_key = sha256(f"{app_id}|{screen_id}|{target_id}|{failure_type}|{screen_fingerprint}")
```

- 缓存命中依然必须经过 5 节的 Runtime Guard（E1 的延伸，不允许缓存绕过安全校验）。
- P2 用进程内 LRU 即可，不引入 Redis；缓存失效策略：`app_build` 变化时不整体清空，而是让 Guard 的 `validated_builds` 检查自然决定是否需要重新执行验证。

---

## 8. Candidate 的产生与过期

### 8.1 来源（E5）

```text
P1 RECOVERED (LLM)
      ↓
recovery_reviews.review_status == 'ACCEPT'
      ↓
ExperienceStore.create_candidate(seed)
```

`REJECT` 的记录**不经过任何代码路径**进入 Experience Store——建议在 `mta review accept <id>` 命令里直接触发 `create_candidate`，而不是靠后台扫描判断，避免遗漏或误判。

### 8.2 过期清理（E12 的补充规则，解决"僵尸 Candidate"）

```yaml
candidate_staleness:
  max_idle_days: 90   # 超过这个天数没有新样本（无论成功失败）
```

```python
def sweep_stale_candidates(store: ExperienceStore, policy) -> list[str]:
    """
    CANDIDATE 状态且 last run 早于 max_idle_days 前 → REJECTED(reason="STALE")。
    之后按正常 REJECTED 的证据保留规则处理（见 11.3）。
    建议作为 `mta experience sweep` 定期任务跑，不在 Runner 主流程里做。
    """
```

---

## 9. Promotion

### 9.1 流程

```text
VERIFIED
   ↓
Promotion Proposal（系统生成 diff）
   ↓
mta experience promote <id>  → 人工 Review
   ↓
ACCEPT → 写入 repository/overrides/elements/<Screen>.yaml（origin: experience）
   ↓
Git commit（人工或工具辅助生成 commit，但 commit 本身走正常 PR 流程）
   ↓
CI → Element Repository 生效
```

### 9.2 写入目标与合并规则

沿用 P1 第 5 节的 Repository 规则：**写入 `overrides/`，记录 `origin: experience`**，不新建第三个目录。合并优先级（**尝试顺序**，不是互斥覆盖）：

```text
manual override  >  source generated  >  promoted experience
```

即：如果 `manual`/`source` 的策略在运行时失败，Locator Chain 会继续尝试 `promoted experience` 策略——它是 Locator Chain 里排序最后的一条，而不是被直接丢弃。

### 9.3 Promotion Proposal

```python
class PromotionProposal(BaseModel):
    proposal_id: str
    experience_id: str
    diff: str                 # 生成的 YAML diff，供人工 review
    evidence_summary: str     # sample_count / success_rate / distinct_runs / validated_builds
    status: Literal["PENDING", "APPROVED", "REJECTED"]
```

Approve 后记录 `experience.promoted = True`、`experience.promoted_commit = <git sha>`。

### 9.4 Promotion 与 Experience 状态是两条独立时间线

**必须明确**：Promotion 之后，若该 Experience 后续因滑动窗口触发 `DEGRADED`，**不会自动撤销**已经进入 Git 的 Promotion。原因：
- 一旦 Promoted，这条策略已经是 P1 `find()` 的一条普通 Locator，不再经过 Experience Store 的统计路径（正常定位成功根本不会触发 Recovery）。
- 如果 Promoted 策略后来确实开始大面积失效（App 又变了），P1 的正常流程会是：`find()` 全部策略失败 → 触发新一轮 Recovery → 可能产生新的 LLM Candidate，指向这个 `target_id` 的新策略。
- 撤销旧 Promotion（`git revert`）是**人工判断**，不由 Experience 状态自动触发。这样可以避免"系统自己决定回滚正式配置"。

### 9.5 跳过自动验证的人工路径（非幂等/高风险目标）

对于第 6 节中无法自动转正的 Experience，允许人工直接发起 Promotion（跳过 `eligible_for_auto_verification`），但：
- 必须在 Proposal 中显式标注 `manual_override_of_auto_policy: true` 和理由；
- 建议要求至少一次在 Sandbox/Staging 环境下的人工验证记录作为前提（具体验证手段由业务团队决定，P2 只记录这个事实，不强制自动化）。

### 9.6 Rollback（E10）

```bash
git revert <promotion_commit>
```

**不实现** `mta repository rollback` 或任何独立版本管理。Experience Store 的历史记录（状态、证据）**不受 Git revert 影响**，两者是独立的事实来源：Git 记录"现在生效的配置是什么"，Experience Store 记录"系统曾经学到并验证过什么"。

---

## 10. 结果语义细化

延续 P1：`RECOVERED ≠ PASS`，不计入通过率，退出码非 0。P2 细分 `detail.kind`：

```text
RECOVERED_LLM            # 本次恢复由 LLM 解决（Experience MISS 或全部候选失败后回落）
RECOVERED_EXPERIENCE     # 本次恢复由 Experience Store 命中解决，LLM 未被调用
RECOVERED_ASSERTION_TARGET  # 断言目标定位漂移被恢复（沿用 P1 7.3 的区分：只允许定位漂移，不允许修改期望值）
```

报告和 JUnit 的聚合逻辑不变（P1 第 8 节），只是在明细里多一层分类，便于回答"这次是 LLM 救的还是经验救的"。

---

## 11. 数据模型（SQL）

```sql
CREATE TABLE experiences (
    experience_id TEXT PRIMARY KEY,
    app_id TEXT NOT NULL,
    screen_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    strategy_json TEXT NOT NULL,
    origin TEXT NOT NULL,
    status TEXT NOT NULL,

    sample_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    success_rate REAL DEFAULT 0,

    validated_builds_json TEXT DEFAULT '[]',
    last_screen_fingerprint TEXT,

    seed_run_id TEXT NOT NULL,
    seed_step_id INTEGER NOT NULL,
    seed_recovery_review_id INTEGER NOT NULL,

    promoted INTEGER DEFAULT 0,
    promoted_commit TEXT,

    created_at TEXT, updated_at TEXT
);
CREATE INDEX idx_experiences_lookup ON experiences(app_id, screen_id, target_id, status);

CREATE TABLE experience_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experience_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    step_id INTEGER NOT NULL,
    app_build TEXT NOT NULL,
    screen_fingerprint TEXT,
    result TEXT NOT NULL,                 -- SUCCESS / FAILURE（只有 record_as_sample=true 的才写，见 4.7）
    guard_reason TEXT,                    -- TARGET_NOT_FOUND / TARGET_AMBIGUOUS / TYPE_MISMATCH / ...
    effective_risk TEXT,
    uniqueness_count INTEGER,
    element_type_match INTEGER,
    latency_ms INTEGER,
    created_at TEXT
);
CREATE INDEX idx_experience_runs_exp ON experience_runs(experience_id, created_at);

CREATE TABLE experience_state_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    experience_id TEXT NOT NULL,
    from_status TEXT, to_status TEXT,
    reason TEXT,                          -- SLIDING_WINDOW_DEGRADE / AUTO_VERIFIED / STALE / REVALIDATED / MANUAL / ...
    run_id TEXT, app_build TEXT,
    operator TEXT,                        -- 'system' 或人工用户名
    created_at TEXT
);

CREATE TABLE promotion_proposals (
    proposal_id TEXT PRIMARY KEY,
    experience_id TEXT NOT NULL,
    diff_text TEXT NOT NULL,
    evidence_summary_json TEXT,
    manual_override_of_auto_policy INTEGER DEFAULT 0,
    status TEXT NOT NULL,                 -- PENDING / APPROVED / REJECTED
    reviewer TEXT, decision_note TEXT,
    git_commit TEXT,
    created_at TEXT, decided_at TEXT
);

-- Graph（主线 B，数据来源仅限已有 TestCase/Trace/Source Metadata，见 E12）
CREATE TABLE screen_nodes (
    screen_id TEXT, app_id TEXT, source_of TEXT,     -- 'runtime' | 'source'
    fingerprint TEXT, visit_count INTEGER DEFAULT 0,
    first_seen TEXT, last_seen TEXT,
    PRIMARY KEY (screen_id, app_id, source_of)
);

CREATE TABLE screen_transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT, source_of TEXT,
    from_screen TEXT, to_screen TEXT,
    trigger_action TEXT, trigger_target TEXT,
    sample_count INTEGER DEFAULT 0, success_count INTEGER DEFAULT 0,
    first_seen TEXT, last_seen TEXT
);

CREATE TABLE graph_diffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT, base_build TEXT, target_build TEXT,
    diff_type TEXT,                       -- ADDED / REMOVED / CHANGED / NOT_OBSERVED / UNKNOWN
    scope TEXT,                           -- screen / transition / element
    subject TEXT, detail_json TEXT,
    created_at TEXT
);
```

### 11.1 Trace 事件（追加到 P1 的 Trace，不新建独立存储体系）

```text
experience_lookup, experience_hit, experience_miss, experience_guard_block,
experience_execution, candidate_created, candidate_verified,
experience_degraded, experience_rejected, experience_revalidated,
promotion_proposed, promotion_approved
```

示例（对应一次命中）：

```json
{
  "experience_id": "exp_001",
  "status_before": "VERIFIED",
  "screen_match": true,
  "screen_fingerprint_match": false,
  "build_in_validated_set": false,
  "uniqueness_count": 1,
  "type_match": true,
  "effective_risk": "LOW",
  "execution": "SUCCESS",
  "result": "RECOVERED_EXPERIENCE"
}
```

### 11.2 Evidence Retention

| Experience 状态 | `experience_runs` 保留策略 |
|---|---|
| CANDIDATE / VERIFIED | 持续保留，不过期 |
| DEGRADED / REJECTED | 状态变化后**至少额外保留 180 天**，用于审计"为什么当初被信任、后来为什么失效" |

### 11.3 Impact Analysis（复用 P1，不重建）

```python
def affected_testcases(target_id: str, repo_index) -> list[str]:
    """直接查询 P1 `mta lint` 已建立的 element → testcase 反向索引，不新建第二套。"""
```

---

## 12. UI State Graph（主线 B）

### 12.1 来源与约束

```text
Runtime Graph  ← P1 Trace 中已执行用例实际到达的 Screen 转移
Source Graph   ← Source Metadata 的导航信息
Diff           ← 两者对比
```

**不做自动探索（E12）**。未观察到的 Source 转移标 `NOT_OBSERVED`，不是 `REMOVED`。

### 12.2 Diff 分类

```text
ADDED            # Runtime 出现了 Source 未声明的转移（如 Login → LoginError）
REMOVED          # 需谨慎：仅当 Source 明确标注过、且 Runtime 多次尝试确认不存在时才标此类；默认优先标 NOT_OBSERVED
CHANGED          # 同一触发条件，目标 Screen 变化
NOT_OBSERVED     # Source 有、Runtime 未覆盖到（用例没走到，不代表被删除）
UNKNOWN          # 无法判定
```

### 12.3 Build-to-Build Diff

```bash
mta graph diff --build 1025 --base-build 1024
```

输出变化的 Screen/Transition，并关联 Impact Analysis，生成 `UI Change Report`。

---

## 13. CLI

```bash
mta experience list [--status CANDIDATE|VERIFIED|DEGRADED|REJECTED]
mta experience show <experience_id>
mta experience verify          # 对所有 Candidate 跑一遍 ExperienceVerifier，产出决策（不自动执行高风险/非幂等升级）
mta experience revalidate <experience_id>
mta experience promote <experience_id>
mta experience sweep           # 清理过期 Candidate（8.2）

mta graph build [--from-trace runs/<run_id>] [--from-source metadata.json]
mta graph diff --build <B> --base-build <A>
mta graph show
```

---

## 14. 目录结构（在 P1 基础上新增）

```text
mobile-test-agent/
├── experience/
│   ├── models.py            # Experience / ExperienceRun / StateEvent
│   ├── store.py              # ExperienceStore 实现（SQLite，单写者）
│   ├── runtime_guard.py      # 与 agent/recovery.py 共享候选校验逻辑
│   ├── verifier.py           # 纯函数状态机决策
│   ├── ranker.py              # 多候选排序
│   ├── cache.py               # 进程内 LRU Recovery Cache
│   ├── promoter.py            # Proposal 生成 / Approve / 写 overrides
│   └── sweeper.py             # 过期清理
├── graph/
│   ├── models.py
│   ├── builder.py              # Runtime/Source Graph 构建
│   ├── diff.py
│   └── storage.py
└── agent/
    └── recovery.py             # 改造：接入 experience.lookup，复用 runtime_guard
```

---

## 15. 里程碑与任务

> 原则：**先 Experience 主线，后 Graph 主线**；Experience 内部先把安全边界（Guard、非幂等排除、滑动窗口）钉死，再做 Promotion 和 Cache。

### M1 — 数据审计与 Schema

| 任务 | 内容 |
|---|---|
| P2-01 | 审计 P1 已积累的 Trace：统计哪些 Locator Failure 重复出现、哪些 Recovery 被人工 ACCEPT，建立真实数据基线 |
| P2-02 | `experience/models.py` + 第 11 节 SQL schema + `schema_migrations` |

### M2 — Store 与 Guard（P2 最优先级）

| 任务 | 内容 |
|---|---|
| P2-03 | `ExperienceStore`：create/lookup/record_run/update_status，单写者约束落地（E9） |
| P2-04 | `runtime_guard.py`：与 `agent/recovery.py` 共享实现；覆盖第 5 节全部分支 |
| P2-05 | 接入 P1 `recovery_reviews`：只有 `ACCEPT` 触发 `create_candidate`（E5） |

**Gate M2**：第 18 节 Demo 的"第一次 LLM → 第二次 Experience 命中，LLM calls=0"跑通。

### M3 — 验证与降级

| 任务 | 内容 |
|---|---|
| P2-06 | `ExperienceVerifier`：E4 资格判定 + 升级门槛（4.5）+ 滑动窗口降级（4.6）+ 过期清理（8.2） |
| P2-07 | `ranker.py`：多候选排序与逐个尝试（5.1） |
| P2-08 | `cache.py`：进程内 Recovery Cache，命中仍强制走 Guard |

**Gate M3**：Candidate→Verified→Degraded 完整状态转换可演示；非幂等目标无论跑多少次都不会自动 VERIFIED；滑动窗口优先级高于总体成功率的用例验证通过。

### M4 — Promotion

| 任务 | 内容 |
|---|---|
| P2-09 | `promoter.py`：Proposal 生成、写入 `repository/overrides/`（`origin: experience`）、Approve/Reject |
| P2-10 | Promotion 与 Experience 状态解耦验证（9.4）；Rollback = `git revert` 验证 |

**Gate M4**：Verified Experience 经人工 Promotion 后，之后的运行在正常 `find()` 阶段就命中，不再进入 Recovery；`git revert` 后 Repository 恢复，Experience Store 历史不受影响。

### M5 — Graph（独立于 Experience，可并行但不要提前）

| 任务 | 内容 |
|---|---|
| P2-11 | Runtime Graph：从 P1 Trace 构建 Screen/Transition |
| P2-12 | Source Graph：从 Source Metadata 构建 |
| P2-13 | Graph Diff（Build-to-Build + Source/Runtime）+ `NOT_OBSERVED` 规则 |
| P2-14 | Impact Analysis：复用 P1 反向索引，产出 `UI Change Report` |

---

## 16. 故障注入矩阵（M2–M4 验收）

| # | 场景 | 预期 |
|---|---|---|
| 1 | 同一 build 第二次遇到相同 Locator Failure | `RECOVERED_EXPERIENCE`，LLM calls = 0 |
| 2 | Verified Experience 候选在当前 UI 出现 2 个匹配 | `TARGET_AMBIGUOUS`，Guard BLOCK，不执行 |
| 3 | Verified Experience 的目标 Screen 变成 PaymentView（risk 升高） | `RISK_BLOCKED`，Guard BLOCK |
| 4 | Verified Experience 当前 Screen 与记录不符 | MISS，不计入失败样本（E11），回落下一候选或 LLM |
| 5 | 历史 100 次 98 成功，最近 5 次中 3 次失败 | 立即 `DEGRADED`，不受总体 98% 影响 |
| 6 | Experience 存在 `validated_builds=[1024,1025]`，当前 build=1026 | 不直接信任，需走一次完整 Guard+执行验证，成功后追加 1026 |
| 7 | Screen fingerprint 变化（新增验证码字段） | `REVALIDATION_REQUIRED`，不删除、不拒绝 |
| 8 | 非幂等目标（提交订单）成功 10/10 | 不自动 VERIFIED，保持 CANDIDATE |
| 9 | 风险 MEDIUM/HIGH 目标成功率 100% | 不自动 VERIFIED |
| 10 | P1 Recovery 被人工 REJECT | 不创建 Candidate |
| 11 | Candidate 90 天无新样本 | 自动转 `REJECTED(reason=STALE)` |
| 12 | 两个 Runner 并发写同一 Experience 的统计 | 无丢更新（验证单写者约束） |
| 13 | Promotion 后 Experience 又被降级为 DEGRADED | Git 中的 Promotion **不自动撤销** |
| 14 | `git revert` 已 Promote 的 commit | Repository 恢复旧状态；Experience Store 历史记录不受影响 |
| 15 | 断言目标 ID 漂移，Experience 命中 | `RECOVERED_ASSERTION_TARGET`，期望值本身未被修改 |
| 16 | Source 中存在但用例从未覆盖的 Screen | Graph Diff 标 `NOT_OBSERVED`，不是 `REMOVED` |

---

## 17. 指标

| 指标 | 定义 | 说明 |
|---|---|---|
| Experience Resolution Rate | `由 Experience 解决的 Recovery / 全部 Recovery Attempts` | 核心指标，替代原先重复的 "LLM Avoidance Rate" / "Experience Hit Rate" 两个说法 |
| Candidate / Verified / Degraded / Rejected 计数 | — | 观察知识积累健康度 |
| Promotion Rate | `Verified 中被 Promote 的比例` | — |
| Experience lookup / cache / LLM 延迟 | — | 验证"确定性 < Promoted < Cache < Store < LLM"的速度梯度 |
| Revalidation 成功率 | — | 观察 DEGRADED 之后能恢复的比例 |

稳定 build：`LLM Invocation ≈ 0`（P1 既有目标）；发生过 drift 的 build：`Experience Resolution Rate` 应随运行次数上升，不预设绝对数值，先建基线。

---

## 18. 演示脚本（对照第 1.2 成功标准，用于 Demo / 集成测试固定场景）

1. Build 1024 用 `login_button`；Build 1025 改为 `signin_button`，不更新 source_metadata（制造漂移）。
2. 第一次运行：`ELEMENT_NOT_FOUND` → P1 Local Reconciliation → LLM → Guard 通过 → 执行成功 → `RECOVERED_LLM` → 人工 `mta review accept` → 自动创建 Candidate。
3. 第二次运行（同 build）：Experience HIT → Guard 通过 → `RECOVERED_EXPERIENCE`，`LLM calls = 0`。
4. 重复运行满足 4.4 + 4.5 条件 → `VERIFIED`。
5. 人为让 `signin_button` 出现两个匹配 → 即使 VERIFIED 也必须 `TARGET_AMBIGUOUS`，不执行。
6. 人为把目标所在 Screen 改成高风险（如移到 PaymentView）→ `RISK_BLOCKED`。
7. 连续注入失败触发滑动窗口 → `DEGRADED`。
8. `mta experience revalidate` → 成功 → 回到 `VERIFIED`。
9. `mta experience promote` → 人工 Accept → 写入 `repository/overrides/` → Git commit → 之后运行走正常 Locator，不再触发 Recovery。
10. `git revert` 该 commit → Repository 回退；确认 Experience Store 历史不受影响。

---

## 19. P2 → P3 预留接口

P2 不实现规划能力，但为 P3 准备四类可查询的知识：

```python
class KnowledgeSources(Protocol):
    def experience_lookup(self, app_id, screen_id, target_id) -> list[Experience]: ...
    def graph_query(self, app_id) -> ScreenGraph: ...
    def impact_of(self, target_id) -> list[TestCaseId]: ...
    def trace_history(self, filters) -> list[TraceEvent]: ...
```

P3（自动探索、测试规划、Subagent）建立在这四个查询接口之上，不需要重新设计数据层。

---

## 附录：关键设计决策记录（延续 P0/P1 的记录习惯）

| 决策 | 理由 |
|---|---|
| Guard 因 Screen 不匹配产生的 MISS 不计入失败样本 | 否则正常业务流程路过其他页面会冤枉拖垮健康的 Experience |
| `validated_builds` 只在执行成功后追加 | 静态校验通过不等于这个 build 上该策略真的有效 |
| 非幂等/高风险目标永不自动转 Verified | 自动验证本身意味着重复执行副作用动作，风险不可接受 |
| Candidate 种子只收人工 ACCEPT 的 P1 Recovery | 防止一次误判的 LLM 恢复污染后续自动学习的起点 |
| 降级判定（滑动窗口）优先于总体成功率 | 历史好不代表现在好，近期失败必须立刻降低信任 |
| Promotion 与 Experience 状态是两条独立时间线 | 避免"系统自己决定撤销已生效配置"，回滚必须是人工决策 |
| Rollback = `git revert`，不自建版本系统 | Element Repository 已纳入 Git 管理，重复造轮子没有必要 |
| Graph 不做自动探索，未观察到标 `NOT_OBSERVED` | P2 的知识边界就是"测试系统观察到的世界"，不能假装知道更多 |
| Experience Store 单写者 | 避免并发更新丢失统计数据，且不需要立即引入分布式写入服务 |
| 屏识别**跑过但没结论**时 fail-closed（`SCREEN_UNKNOWN → MISS`），但识别**没跑**时沿用登记屏先验 | §5 的 `current_screen is None → MISS` 只有在「识别跑过」时才有意义。此前的无条件回落让 §5 那条规则成死代码，更让 Experience 路径的屏校验**自比自**（`current_screen` 与 `exp.screen_id` 都等于 `ctx.screen_id` → 恒等），于是「页面无任何已登记 marker」时只要存在同名唯一元素，候选就会在未确认屏下执行。反之，没有 Repository 就没有 marker 表——「识别」这件事无从发生，任何屏校验都必然空转，此时收紧只掉能力不增安全，故保留 P1 的登记屏先验（Task 2.4 终审 P2-2 收口） |
| 10.1 Guard 复检的终态 `failure_type` 两条路径共用一张映射表 | LLM 路径自 P1 起就把 Guard reason 映射成终态（`SECURITY_BLOCKED` 等）；Experience 路径「候选全被拦且无回落」时若报原症状（`ELEMENT_NOT_FOUND`），CI 会把「被策略拦下」读成「元素漂移」——排障方向完全错，且丢掉 BLOCKED/exit 4 的语义。表值里的 `LLM_` 前缀是既有报告契约值，保留不动（Task 2.4 终审 P3-1 收口） |
| `recoveries` 表只记**成功**的恢复动作 | 它的语义是「恢复动作记录 + 9.5 review 的种子来源」，而 E5 规定失败尝试不产种子——记进来只会让 review 队列多出永远不该 ACCEPT 的行。失败尝试的痕迹在 `experience_runs`（4.7 样本 + guard_reason）与 `steps`（终态 + `recovery` 段）（Task 2.4 终审 P3-4 显性化） |
| aux（wait/assert）命中的样本由**调用方观测后回填**，引擎产出「待定样本」 | aux 无 dispatch 语义，执行结果引擎侧不可观测；但调用方在覆盖定位后重跑了一次断言/等待，**它看得见**。写死 SUCCESS 会抬高 success_rate 把 Candidate 推向 VERIFIED（E4/E11 级）；直接丢弃则 aux-only Candidate 的 `sample_count` 恒为 0、永不能 VERIFIED，P2 的「知识积累」对 aux 目标整体失效（Task 2.4 评审 P2-1） |
