# Review — Phase 2 Task 2.4 收口（评审 P2-1 + P3 修订 / Gate M2 定稿）

- 日期：2026-10-04 21:50（北京时间）
- 审查对象：提交 `41030f6`（`fix(p2): review_p2_task24 修订——aux 样本积累（P2-1）+ 10.1 复检对称（P3-1）`）；父提交 `6753881`（Task 2.4 实现）；前置评审 `review/review_p2_task24_2026-10-04.md`
- 设计依据：design §4.7（什么算一次样本）/ §5（Guard）/ §6（非幂等与 postcondition）/ §10（结果语义）/ §11.1（Trace 事件）；E1 / E4 / E7 / E11；plan Task 2.4；前置评审 §5 收口处置清单
- 性质：只读 review（探针实测），未修改任何项目文件
- 结论：**通过（可收口 Task 2.4 / M2）** —— 前置评审的 2×P2 + 6×P3 处置**全部核销**（已修 6 项、显性延后 3 项，各有测试或审计落点）；遗留 **0×P1/P2、6×P3**，其中 4 条是本次修订新暴露的边角（含 1 条同源缺口）、2 条是文档/常量单点

---

## 1. 核验结果（探针实测，非仅代码阅读）

| # | 声称 | 实测 | 结果 |
|---|---|---|---|
| 1 | pytest 925 passed（919 + 6） | 实跑 **925 marks / 0 F / 0 E / exit 0**；`git log -S` 逐名核对：本提交净增 6 个测试函数（`aux_rerun_failure` 1 + `resolve_deferred_sample` 3 + 10.1 复检 1 + 屏识别回落钉子 1），另 2 个是**重命名**（净增 0）→ 919+6 成立 | ✅ |
| 2 | P2-1 aux 命中（重跑成功）落 SUCCESS 样本 | 全管线探针 15b：steps `(assert, RECOVERED)`、recoveries 1 行 `(EXPERIENCE, RECOVERED, accepted=1)`、`experience_runs` = `(SUCCESS, guard_reason=None, step_id=2)`；E7 追加 `validated_builds` | ✅ |
| 3 | P2-1 重跑失败 → FAILURE + steps 行 | 探针 15c：steps `(assert, FAILED)`、样本 `(FAILURE, AUX_RERUN_FAILED, step_id=2)`、计数 `(1,0,1)`、`validated_builds=[]`、终态 `ASSERTION_VALUE_MISMATCH` | ✅ |
| 4 | 待定样本不预置 SUCCESS（E4/E11） | 引擎侧 `result=None` + `pending_observation=True` + `guard_reason=None`，`execution="not_dispatched"` 如实入 stages | ✅ |
| 5 | 回填幂等 / 无待定样本 no-op | `resolve_deferred_sample` 首次返回 1、再回填返回 0（标记已摘）；动作步结论回填返回 0 | ✅ |
| 6 | 4.7 单点（调用方只说「成没成」） | 翻译只在 `agent.recovery.resolve_deferred_sample`；pipeline 两处调用点无 `result` 字面量，只有 `succeeded=True/False` | ✅ |
| 7 | P3-1 10.1 复检对称 | 同一候选（`signin_button`，`blocked_targets=element:signin_*`）：Experience 路径 `(BLOCK, SECURITY_BLOCKED)`、`record_as_sample=False`、`dispatched=0`；LLM 路径同 reason → **Guard 结论逐位一致**（对外 failure_type 见 P3-1） | ✅ |
| 8 | P3-1 形态（未接线 / 未登记） | `_policy_check_for` 在 `guard is None or repo is None` → None；`repo.resolve` 抛异常（未登记）→ None（与 LLM 路径同款 fail-open） | ✅ |
| 9 | P3-2 注释订正不夹带行为变更 | `cli/main.py` 该处 diff **纯注释**（+7/-3 全在注释块内），惰性构造保留 | ✅ |
| 10 | P3-3 缺 run_id 留痕 | 探针：样本不落库（`experience_runs=[]`）+ stage `{"experience_sample", "no_run_id"}` | ✅ |
| 11 | P3-6 计数订正 | 审计「919 collected（+38，父 881）」与本次「925 passed（919+6）」两处口径自洽，且与实测一致 | ✅ |
| 12 | 无库污染 / 测试隔离 | 15b/15c 全管线探针均在 tmp_path 内完成，未触碰仓库 `out/experience.db` | ✅ |

## 2. 设计对照

| 设计/评审条款 | 实现情况 |
|---|---|
| §4.7 表：Guard 过 + 执行成功 → SUCCESS 并追加 validated_builds（E7） | ✅ aux 重跑观测成功后同样追加（探针 15b） |
| §4.7 表：Guard 过但执行失败 → FAILURE（计样本） | ✅ aux 重跑失败回填 FAILURE 并落 steps（探针 15c）——此前这一类**根本没进表** |
| E11：MISS / 风险拦截不计样本 | ✅ 待定样本只在 `gres.outcome == "EXECUTE"` 之后排队；BLOCK/MISS 路径不受影响 |
| E4：非幂等 / 风险 ≥ MEDIUM 不进自动验证 | ✅ 不猜 SUCCESS 正是这条的延伸（猜错会推高 success_rate 把 Candidate 推向 VERIFIED） |
| §5：LLM 候选与 Experience 候选过**同一**校验链（E1） | ✅ 10.1 环由 `policy_check` 参数贯通，两路径 callable 形态与取数（登记 risk/screen/id）逐字一致 |
| §10 结果语义：不观测就不声称恢复 | ✅ `execution == "UNKNOWN"`（postcondition 观测不到）仍不写样本、不返回 recovered；aux 则改成「待定 + 回填」，语义更精确而非放宽 |
| 评审 §5 处置清单（6 已修 / 3 延后） | ✅ 已修 6 项全部落点可查（探针 + diff）；延后 3 项（P2-2 / P3-4 / P3-5）在审计有显性理由与钉子测试 |
| plan Task 2.4 的 Gate M2 载体职责 | ✅ tag `checkpoint-p2-m2` 落在本提交；真机 12 项判据证据在审计（本次未复跑，见 §4） |

## 3. 发现

### P3-1 10.1 复检的「Guard 结论」对称了，但「终态 failure_type」仍不对称
- 探针（同一候选、同一 guard、同一 repo）：Experience 路径被拦后**对外 `failure_type` 仍是原症状 `ELEMENT_NOT_FOUND`**；LLM 路径同候选被拦给出 `SECURITY_BLOCKED`（经 `GUARD_REASON_TO_LLM_FAILURE`）。
- 机制：`_try_experiences` 对 BLOCK 只 `return None`（换下一候选 / 回落 LLM），终态 failure_type 由最后一次 `miss()` 用 `ctx.failure_type` 决定；而 LLM 路径显式映射 Guard reason。即**映射表只有 LLM 侧一个消费点**。
- 触发条件有限：只要有 LLM 回落且 LLM 也给同一候选，终态会收敛到 `SECURITY_BLOCKED`；不对称只在**无回落**时显形（`--no-llm` / 无 llm / 预算耗尽 / 熔断）。且当前生产没把 guard 接进引擎（本提交记录的延后项）→ 现在不可达。
- 建议：与「把 guard 接进引擎」同批处理——Experience 路径在「全部候选因安全/风险被拦且无回落」时用同一映射表产出终态，或在 stage 里显式标注「终态原因 = 安全拦截」。不要在本次收口里改（改它动的是 P1 的 miss 语义）。

### P3-2 待定样本的契约无防护：忘了回填就炸在 pydantic
- 探针：把未回填的待定样本（`result=None`）直接交给 `record_sample_runs` → `ValidationError: 1 validation error for ExperienceRun`（`result` 是 `Literal["SUCCESS","FAILURE"]`）。
- 现状安全（pipeline 两条 aux 分支都在写库前回填），但契约是隐式的：任何未来调用方漏掉 `resolve_deferred_sample` 都会在落库点炸，且报错指向 `ExperienceRun` 而不是「你忘了回填待定样本」。
- 建议：`record_sample_runs` 加一行前置检查，对 `result is None` 给出指名道姓的报错（`"deferred sample 未回填——先调 resolve_deferred_sample"`）。5 行内，符合本项目 fail-loud 的一贯做法。

### P3-3 aux 重跑失败：steps.detail 丢了 recovery 段（成功路径有）
- 探针对照同一用例的成功/失败两支：
  - 15b（成功）`steps.detail` 顶层键 `['recovered_kind', 'recovery', 'recovery_context', 'recovery_kind']`，`recovery.stages` 完整（含 `experience` hit、`experience_candidate` EXECUTE）。
  - 15c（失败）`steps.detail` **只有 `{"error": ...}`**，无 `recovery` 段。
- 后果：失败时 trace 上看得出「这一步失败」和「有一条 FAILURE 样本」，但看不出**试过哪条经验、候选值是什么、Guard 判了什么**——正是本次修订想消掉的那类排障盲区（steps 行补上了，证据链还缺一环）。
- 建议：`_aux_rerun_failed` 里把 `rec.detail` 的 stages（及 experience_id/候选）并进 step detail，与成功路径同形。

### P3-4 `recoveries` 表表达不了「失败的经验尝试」
- 实测：15b 有 recoveries 行，15c 一行都没有。原因是 `_write_recovery_row` 把 `result="RECOVERED", accepted=True` 写死，只有成功路径调用它。
- 若这是**有意**（recoveries = 恢复动作记录 + review 种子来源，只记成功），建议在函数 docstring 明写，否则每次评审都会把它当缺口重报；若希望「尝试过但失败」也留痕，则需要给该函数加 `result` 参数（不能靠硬编码撒谎）。
- 影响面：审计的 recovery 分布会少统计一类尝试；不影响 E5 主线（失败尝试不产种子）。

### P3-5 共享 `guard_candidate` 的 docstring 仍留着被本次证伪的那句话
- 本提交在 `experience_runtime_guard` 与 `_policy_check_for` 两处都改正了「执行路径 dispatch 处自会再过 Guard」这个错误论断，但**共享规则体本身**的 docstring 仍写着（`inspect.getdoc` 实测）：
  `policy_check 10.1 Guard 复检 callable（GuardViolation → 拦；None = 跳过——执行路径 dispatch 处自会再过 Guard）`
- 这正是前置评审 P3-1 要消灭的那句话，现在只剩这一处；三处表述不一致比一处错更容易误导。顺手改一句即可。

### P3-6 Gate 探活的阈值与规模两处硬编码
- `_llm_latency_probe`：① 判据 `took < 20.0` 与 budget 的 `timeout_seconds` 是**两个真值源**（budget 默认值一变，探活口径就漂）；② 探活用 `max_tokens: 8` 的最小 completion，而 G2 跑的是完整恢复 prompt —— 探活天然乐观，「探活绿、G2 红」仍可能发生（本提交靠 G2 重试 2→4 兜）。
- 建议：阈值引用 `LLMBudgetConfig.timeout_seconds`（项目对 `DEFAULT_EXPERIENCE_DB` 已经这么做了）；探活 prompt 规模或超时余量在注释里写明「这是下界，不是保证」。

## 4. 未验证 / 存疑

1. **Gate M2 真机 12 项未复跑**：本次环境无 booted 模拟器 / Appium / LLM 网关，只核对产物 `out/p2_m2_gate/summary.json` 与审计/commit 描述一致（`verdict=PASS`、`device_half=PASS`、12 项全 PASS、`elapsed 54.1s`、G1 `13 passed`）。建议把该产物路径连同「判据逐条可机械复核」的表一起留在审计里（已有）。
2. **`_llm_latency_probe` 的模型默认值**：脚本里 `LLM_MODEL` 缺省 `glm-5-3-flash`；本机 `.env` 是否同值未核。探活若打到不存在的模型，会以 4xx 计成「探活失败」而非「环境未配」——两者对排障的含义不同。
3. **P3-1 的影响面**：我按「无回落时才显形」判定影响有限；若将来 Experience 命中被设计成**不回落到 LLM**（例如高风险屏直接终态），这条就会从边角升格为常态，届时需一并处理。

## 5. 值得肯定

- **P2-1 的修法不是「补一句 SUCCESS」，而是把不可观测性显性化**：待定样本 + 回填，既不猜（E4/E11 级错误）也不丢（4.7 的知识积累目标对 aux 整体失效）。而且翻译留在引擎（4.7 单点），调用方只说「成没成」——这正是本项目「同一概念只许一处实现」的一贯纪律。
- **修订中自查出评审未及的一层**：生产 `cli/main.py` 装配 `RecoveryEngine` 没传 `guard` → 10.1 复检自 P1 起从未生效。作者没有顺手改掉，而是判断「接它会改 P1 的 LLM 路径行为 → 与 P1 行为保留冲突 → 单独立项」，只把形态铺好。这种「发现了但不越界」的克制比修完更值钱。
- **P3-3 的 `stage: no_run_id`**：把「静默丢样本」变成可判读信号，与「做不了就留痕」的既有纪律同源；`missing_run_id_writes_no_sample` 的语义因此从「看不见」变成「看得见地没写」。
- **数字订正用 `git worktree` 独立计数**，而不是改文字了事——919/+38/父 881 现在可复核。
- **测试的诚实度**：`test_p2_15_aux_hit_records_sample_from_rerun_observation` 同时断言「引擎侧仍如实标 `not_dispatched`」，不为了让新机制好看而抹掉事实；`test_screen_recognition_failure_falls_back_to_registered_screen` 把延后项钉成钉子测试，改它必先撞上「P1 行为保留」。
- **前置评审的 6 项修订全部实质核销**：不是改注释交差（P3-2 之外每一项都有测试或探针可复现）。

## 6. 建议动作（优先级序）

1. **随手修（P3-3 / P3-5）**：失败路径的 step detail 带上 `recovery` 段；`guard_candidate` docstring 那一句改掉。两处都是「证据链一致性」，成本一行级。
2. **立项时一并处理（P3-1 / P3-2）**：与「把 guard 接进引擎」同批（那时才显形）；P3-2 的 fail-loud 前置检查现在就能加。
3. **常量单点（P3-6）**：探活阈值引用 `LLMBudgetConfig.timeout_seconds`，并注明探活是下界。
4. **里程碑**：M2 收口（tag `checkpoint-p2-m2` 已在本提交）。M3 起步前建议先清 plan 里的延后项清单——`SCREEN_UNKNOWN` 收紧（与 P1 行为保留的边界）/ `validated_builds` 贯通真实 build id / `EXPERIENCE_EVENT_TYPES` 全接，这三条都已被显性记录且各有钉子，别再让它们随任务滚动。

—— review by 小巴（探针：6 组直呼/全管线探针——E1 双路径 10.1 对称（含 failure_type 对照）/ 待定样本未回填落库 / 15b vs 15c 三表（steps·recoveries·experience_runs）对照 / resolve 回填幂等与 no-op / 无 run_id 留痕 / steps.detail 两路径对照；全量 925 marks·0 F·0 E·exit 0（关 FS 钩子）；`git log -S` 逐名核对 6 项新增测试的归属与净增量；`inspect.getdoc(guard_candidate)` 取证文档残留；`GUARD_REASON_TO_LLM_FAILURE` 与两处 `policy_check` 构造逐行对照）
