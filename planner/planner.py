"""planner.py — Planner 编排（设计 §5.3；Task 2.3 / P3-07）。

```text
PlannerInput(changed_files)
    → planner/impact.py      changed_files → targets        （适配层）
    → KnowledgeSources       impact_of / trace_history      （P2 的索引与历史，不重写）
    → planner/risk.py        case_risk                      （转发 P1 的 effective_risk）
    → planner/prioritizer.py priority_score + order_key     （确定性打分 + 全序）
    → LLM                    reasons（解释）+ 同分 tie-break（**不许改分数**）
    → TestPlan
```

## 口径 1：候选集 = **受影响用例**（不是全部用例）

设计 §5.3 的流程从「受影响 Screen/Element/TestCase」出发，再把历史失败率与业务风险
**加进排序**——所以候选集是**改动影响面内的用例**。§5.3 那句「CRITICAL/HIGH 强制高
优先级，**不管改动大小**」说的是**排序**（哪怕这次 diff 只碰了它一个字符，风险 floor
照样生效），不是「把没受影响的 CRITICAL 用例也拉进 Plan」。

这条口径同时让矩阵 #6 自洽：`changed_files` 为空 → 影响面为空 → **空 Plan 并明示**
（若候选集是全部用例，空改动也会产出一份「排好序的全体用例」，那就不是空 Plan 了）。

**未受影响的用例不进 Plan**，这一点由 `notes` 明示（不是静默丢弃）。

## 口径 2：`reasons` = **规则摘要（永远在）+ LLM 解释（有则追加）**

F13 要求「可解释，**不是『LLM 觉得』**」。所以数值依据（impact / history / risk / score）
由代码生成、**永远存在**；LLM 的文字以 `llm: ` 前缀追加，来源一眼可辨。LLM 挂掉 /
预算耗尽 / 输出不合契约 → 只是少了 `llm: ` 那几行，**Plan 照常产出**（plan Steps 的
降级要求）。

## 口径 3：LLM 只许在**同分**内重排（矩阵 #5）

判据不是「结果看起来差不多」，而是可机械判定的：按确定性顺序给每条一个**分数段位**，
LLM 给的排列里段位序列必须**非递减**。把低分用例排到高分之前 → 段位下降 → **判违规，
整条顺序丢弃、保留确定性顺序，并留审计**（`PlanResult.audit`）。

## ⚠️ 偏离登记：`PlanResult` 是 plan 之外的**过程产物**（且**不落库**）

设计 §5.1 的 `TestPlan` 只有 `schema_version / plan_id / app_build / git_commit / tasks`
——**没有**承载「为什么是这个 Plan」的字段。但本任务的 Steps 明确要求两件事必须可见：
「空 Plan 且**明示**原因」（矩阵 #6）与「LLM 重排被拒 → **审计留痕**」（矩阵 #5）。
所以编排层返回 `PlanResult(plan, notes, unrankable, audit)`：`plan` 是**持久化产物**
（`save_plan` 只收它），其余三项是**过程结论**（CLI 渲染；M6 起写 `agent_trace`）。
不给 `TestPlan` 加字段——那要动 `test_plans` 的 schema（设计 §11 的表没有这些列）。

⚠️ **由此推出一条硬约束**：`notes` / `unrankable` / `audit` **不进 `test_plans`**
（`save_plan(plan_id, *, app_build, git_commit, tasks)` 只写 `tasks_json`）。所以任何
「**必须随 Plan 一起被复核**」的结论都不能只放在它们里——要么进 `reasons`
（唯一落库的可解释性位置），要么在 M6 写进 `agent_trace`。
**第一条落地的就是 drift 回退的精度损失**（`rule_basis(drift=True)`）：
它是**对 `reasons` 里那句「impact: 命中改动影响面」本身的限定**，只记在 `notes` 里
等于「落库的 Plan 里，精确影响面与宽影响面长得一模一样」。

## ⚠️ 偏离登记：`failure_rate` 按**运行**统计

`KnowledgeSources.trace_history` 返回的是 **steps**（不是 runs），但「历史失败率」的
自然口径是「这个用例历史上跑 N 次失败 M 次」。两者可以调和：`TraceStep.testcase_run_id`
就在返回的步骤上，所以按 `testcase_run_id` 分组即可——**不需要**新接口，也不用手搓 SQL。
详见 `failure_rate` 的 docstring（分母/分子/空历史都写清了）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

from planner.impact import (ImpactMappingError, changed_targets, drift_targets,
                            unmatched_changed_files)
from planner.models import (PlannerInput, TestPlan, TestPlanTask, plan_id_for,
                            require_paths)
from planner.prioritizer import (RISK_FLOOR, TestCaseMeta, order_key,
                                 priority_score)
from planner.risk import case_risk
from repository.resolver import AmbiguousReferenceError, UnknownReferenceError
from source.coverage import collect_refs

if TYPE_CHECKING:            # 只为类型标注；`from __future__ import annotations`
    from experience.knowledge import KnowledgeSources   # 已把标注变成字符串，
    from llm.budget import LLMBudget                    # 运行期不付这笔导入代价
    from llm.provider import LLMProvider
    from repository.resolver import RepositoryProtocol
    from testcase.schema import TestCase

__all__ = [
    "PlanResult",
    "Unrankable",
    "failure_rate",
    "llm_order_violations",
    "plan",
    "rule_basis",
]

# 步骤状态里「失败」的那一个（`tracer/storage.py::STEP_STATUSES` 的词表）。
# 只认它：`RECOVERED` 是**恢复成功**，不是失败证据（E7 的口径——恢复成功要计入
# SUCCESS 侧）；`SKIPPED` / `PENDING` / `RUNNING` 都不是结论。
_FAILED_STEP = "FAILED"

# 审计里违规对的上限（超出置 `truncated`）：审计要**看得见**，但不该随 Plan 规模
# 无界增长——把 `test_plans` 行与 CLI 输出撑坏不是「留痕」。
_MAX_VIOLATIONS = 20

# 本任务只产出 `existing`（既有用例）。`generated_gap`（生成补齐）归 M3 的
# Test Generator——那时才有「这条任务是生成的」这个事实。设计 §5.1 的 Literal 两值
# 都已支持，这里只是**没有生产者**，不是漏了。
_SOURCE_EXISTING = "existing"


@dataclass(frozen=True)
class Unrankable:
    """**进了影响面却无法打分**的用例 + 原因。

    当前只有一种成因：用例引用了 Repository 解析不到的元素（`UnknownReferenceError`）。
    P1 的处置是同一句话——`runner/testcase_runner.py` 把它当**用例缺陷**（lint 应已
    拦截）→ 该用例直接 FAIL，不做 recovery。所以这里也**不猜**它的风险（猜 LOW 会让
    它在 Plan 里沉底，而它恰恰是最可能失败的那个），而是**列出来**让人看见。
    """

    testcase_id: str
    reason: str


@dataclass(frozen=True)
class PlanResult:
    """一次规划的**完整**结论：产物 + 过程（见模块 docstring 的偏离登记）。"""

    plan: TestPlan
    # 人读的「明示」：空 Plan 的成因、drift 回退、LLM 降级……（矩阵 #6 的落点）
    notes: tuple[str, ...] = ()
    # 进了影响面但打不了分的用例（坏引用）
    unrankable: tuple[Unrankable, ...] = ()
    # 结构化留痕（矩阵 #5 的落点）：LLM 重排被拒的违规对
    audit: tuple[dict, ...] = ()

    @property
    def tasks(self) -> tuple[TestPlanTask, ...]:
        return tuple(self.plan.tasks)


# --- 历史失败率 ---------------------------------------------------------------


def failure_rate(steps: Iterable[Any]) -> float:
    """历史失败率 ∈ [0, 1]，**按运行统计**（`testcase_run_id`）。

    ```text
    分母 = 该用例历史上出现过的 distinct testcase_run_id 数
    分子 = 其中有 ≥1 个 status == "FAILED" 步骤的运行数
    无历史（steps 为空）→ 0.0
    ```

    三条口径说明（都会影响排序，所以写在这里而不是散在代码里）：

    1. **按运行而不是按步骤**：`trace_history` 给的是步骤流，但「失败率」的自然含义是
       「跑 N 次失败 M 次」。`TraceStep.testcase_run_id` 就在返回的行上，分组即可——
       **不新增接口、不手搓 SQL**。按步骤算会让「步骤多的用例」失败率被稀释；
    2. **`RECOVERED` 不算失败**：它是恢复**成功**（E7：Guard 过 + 执行成功 → SUCCESS）。
       把它算成失败会让「恢复能力强」表现为「历史很差」；
    3. **空历史 → 0.0**（不是 1.0）：没有历史 = 没有失败**证据**。给 1.0 会让所有新用例
       凭空拿到满分历史项，反而挤掉真正有失败史的老用例。

    ⚠️ **入参直接取属性，不用 `getattr` 兜底**（review_p3_task23 P3-1——**同一个形状在本包
    第三次出现**：`planner/risk.py` 的 `tc_meta.element_ids`、`planner/impact.py::_paths`
    的单字符串闸门，都是这条）。`getattr(step, "testcase_run_id", None)` 会让**传错形态**
    静默塌成 `0.0` 或 `1.0`（探针实测：两个缺 `testcase_run_id` 的 FAILED 步骤 → `1.0`；
    缺 `status` → `0.0`），而失败率占 **30 分**权重——塌成 0 让有失败史的用例**沉底**、
    塌成 1 让没失败史的用例白拿 30 分，两侧都是「看不见」那一侧。传错就 `AttributeError`。
    """
    runs: dict[Any, bool] = {}
    for step in steps:
        run_id = step.testcase_run_id
        failed = step.status == _FAILED_STEP
        runs[run_id] = runs.get(run_id, False) or failed
    if not runs:
        return 0.0
    return sum(1 for failed in runs.values() if failed) / len(runs)


def _history_by_case(knowledge: KnowledgeSources) -> dict[str, tuple]:
    """`testcase_id` → 该用例的全部历史步骤。**一次读全量**再分组。

    `trace_history(filters)` 每次调用都会 `read_trace_steps` 读**全库**再在 Python 里
    过滤（见 `experience/knowledge.py`），所以「在候选循环里逐用例调用」是
    O(候选数 × 全库步骤数)。分组键 `testcase_id` 正是 Task 2.2 追加到 `TraceStep` 上的
    字段，这里直接用它（review_p3_task23 小观察 2；M6 的自主闭环规模会疼）。
    """
    grouped: dict[str, list] = {}
    for step in knowledge.trace_history({}):
        grouped.setdefault(step.testcase_id, []).append(step)
    return {k: tuple(v) for k, v in grouped.items()}


# --- 规则摘要（reasons 的确定性部分） -----------------------------------------


def rule_basis(*, hit: bool, rate: float, risk, score: int,
               drift: bool = False, no_history: bool = False) -> list[str]:
    """确定性打分的**依据**（F13：可解释性由代码保证，不依赖 LLM 在场）。

    每条都**扣住一个真实输入**：impact 是否命中、历史失败率、风险等级（带 floor）、
    最终分数。顺序固定 → 同一输入下 `reasons` 的前缀稳定（可 diff、可对账）。

    两个**限定标记**（都附着在它们各自限定的那一行上，而不是每条 reasons 追加一整句）：

    - `drift=True` → impact 行加「drift 回退：影响面比本次 commit 宽」；
    - `no_history=True` → history 行加「无历史数据」。

    为什么标记必须进 `reasons`（而不是只进 `PlanResult.notes`）：`reasons` 是**唯一随
    `test_plans` 落库**的位置（`save_plan` 只收 `tasks_json`），而这两个标记恰恰是对
    「命中改动影响面」「历史失败率 0.00」这两句话本身的限定 —— 只记在 `notes` 里，
    落库的 Plan 上「影响面精确」与「影响面宽」、「真的没失败过」与「根本没有历史」
    就长得一模一样（review_p3_task23 P2-1 的同一课）。
    标记**附着在限定的那一行**（而不是每条追加一整句 plan 级说明），避免 N 份重复。

    ⚠️ **编排路径上 `hit` 恒为 `True`**：候选集本身就是影响面（口径 1），所以
    「不在影响面内」那一支在 `plan()` 里不可达、`IMPACT_WEIGHT` 的 +40 在候选集内
    **区分度为零**（review_p3_task23 小观察 1）。这是口径 1 的正确推论，不是 bug；
    参数保留是因为本函数是**纯函数**，它的契约由「命中/未命中」两值定义，不由某一个
    调用方的用法定义（`hit=False` 那一支有直接调用它的专测）。
    """
    floor = RISK_FLOOR.get(risk)
    risk_line = f"risk: {risk.name}"
    if floor is not None:
        risk_line += f"（floor {floor}）"
    impact_line = f"impact: {'命中改动影响面' if hit else '不在影响面内'}"
    if drift:
        impact_line += "（drift 回退：影响面比本次 commit 宽）"
    history_line = f"history: 历史失败率 {rate:.2f}"
    if no_history:
        history_line += "（无历史数据：trace 库尚不存在）"
    return [
        impact_line,
        history_line,
        risk_line,
        f"score: {score}",
    ]


# --- LLM 顺序合法性（矩阵 #5） ------------------------------------------------


def llm_order_violations(ordered: Sequence[tuple[str, int]],
                         proposed: Sequence[str]) -> list[dict]:
    """`proposed` 是否违反「只许同分重排」。空列表 = 合法。

    `ordered` 是**确定性顺序**（`order_key` 排过）的 `(testcase_id, score)` 列表。
    判据可机械核对：

    1. `proposed` 必须是 `ordered` 的一个**排列**（漏一个/多一个/重复都算违规——
       半个排列会让「谁被漏了」变成要猜的问题）；
    2. 给每条一个**分数段位**（同分共享一段，分数降序段号递增），`proposed` 的段位
       序列必须**非递减**：把低分用例提到高分之前 → 段位下降 → 违规。

    ⚠️ **段位必须按分数算，不能按名次算**：同分两条互换是**合法**的 tie-break
    （设计 §5.2 允许），按名次判会把合法操作全判成违规——那样「矩阵 #5 的守卫」会
    变成「LLM 永远被拒」，判据与设计不等义。

    返回的是**违规对**（而不是 bool）：审计要能回答「它想怎么改」，不只是「它违规了」。
    相邻段位下降处即违规点（序列非递减 ⇔ 无相邻下降）；每条给出 `moved_up`（被上移的
    低分用例）与 `displaced`（被挤后的高分用例）及各自分数——字段名按**角色**给，审计
    读者不会把分数读反。最多报 `_MAX_VIOLATIONS` 条（超出置 `truncated`），避免审计
    条目随 Plan 规模无界增长。
    """
    ids = [cid for cid, _ in ordered]
    if len(proposed) != len(ids) or set(proposed) != set(ids):
        return [{"kind": "not_a_permutation",
                 "missing": sorted(set(ids) - set(proposed)),
                 "unknown": sorted(set(proposed) - set(ids))}]
    score = dict(ordered)
    group: dict[str, int] = {}
    prev, g = None, -1
    for cid, sc in ordered:
        if sc != prev:
            g += 1
            prev = sc
        group[cid] = g

    out: list[dict] = []
    for i in range(len(proposed) - 1):
        early, late = proposed[i], proposed[i + 1]
        if group[early] > group[late]:
            # `early` 段位更低（分数更小）却被排到了前面 → 它上移、`late` 被挤后。
            # 字段名按**角色**给（不是按位置），否则审计读者会把「分数」读反。
            out.append({"kind": "cross_group_reorder",
                        "moved_up": early, "moved_up_score": score[early],
                        "displaced": late, "displaced_score": score[late]})
    if len(out) > _MAX_VIOLATIONS:
        out = out[:_MAX_VIOLATIONS] + [{"kind": "truncated"}]
    return out


# --- 编排 ---------------------------------------------------------------------


def _resolve_targets(planner_input: PlannerInput, metadata: Mapping,
                     base_metadata: Mapping | None, cases: Sequence[Any],
                     notes: list[str]) -> tuple[tuple[str, ...], bool]:
    """改动文件 → `(targets, 是否走了 drift 回退)`。

    回退面要 `cases`：`diff_builds` 的范围由**用例到达的屏**决定（`planner/impact.py`
    的 `drift_targets` 会显式拒绝空 cases）。

    第二个返回值是给 `rule_basis(drift=…)` 的——回退一旦发生，**Plan 的所有分数都建立
    在那个宽影响面上**，这件事必须随 Plan 落库（见 `rule_basis` 的说明）。
    """
    changed = list(planner_input.changed_files)
    try:
        return changed_targets(changed, metadata), False
    except ImpactMappingError as e:
        if base_metadata is None:
            raise
        notes.append(
            f"metadata 缺文件级归属（{e}）→ 回退 build 间 drift 面"
            f"（base_metadata）；⚠️ 精度损失：drift 是 build 之间的元素变化，"
            f"比「本次 commit 改了哪些文件」宽（这条已随 reasons 落库）")
        return drift_targets(base_metadata, metadata, cases), True


def _case_metas(cases: Sequence[TestCase]) -> dict[str, TestCaseMeta]:
    """用例 id → `TestCaseMeta`（`element_ids` 走 P1 的引用收集单点）。

    `collect_refs` 已经滤掉 `screen:` 引用（屏引用不是元素引用，`resolve` 也解析不了），
    所以这里不需要第二套过滤。
    """
    refs: dict[str, list[str]] = {}
    for case_id, ref_id in collect_refs(cases):
        bucket = refs.setdefault(case_id, [])
        if ref_id not in bucket:
            bucket.append(ref_id)
    return {cid: TestCaseMeta(testcase_id=cid, element_ids=tuple(ids))
            for cid, ids in refs.items()}


def plan(planner_input: PlannerInput, *, knowledge: KnowledgeSources,
         cases: Iterable[TestCase], metadata: Mapping,
         repository: RepositoryProtocol, llm: LLMProvider | None = None,
         budget: LLMBudget | None = None,
         base_metadata: Mapping | None = None,
         no_history_reason: str | None = None) -> PlanResult:
    """设计 §5.3 的编排（**纯逻辑 + 一次可选的 LLM 调用**）。

    | 参数 | 说明 |
    |---|---|
    | `knowledge` | `KnowledgeSources`——**只能由 `knowledge.build_knowledge` 装配**（F3 单一入口，有守卫钉住） |
    | `cases` | 已解析的用例（`pipeline.discover()` 的产物） |
    | `metadata` | 12.3 source_metadata（`source/build_identity.read_metadata` 读） |
    | `repository` | `case_risk` 解析元素风险用 |
    | `llm` / `budget` | **两个都给**才调用 LLM；缺任一个即走规则摘要（见下） |
    | `base_metadata` | 可选；metadata 缺文件级归属时的 drift 回退面 |
    | `no_history_reason` | 可选；**调用方已知没有历史数据**时给出原因（非空即不读 trace） |

    ⚠️ **`no_history_reason` 的边界**：「trace 库**不存在**」（还没有任何历史）与
    「库存在但**读不了**」是两回事 —— 前者是合法的空，后者必须 fail-loud。**分不清
    就该由调用方先分清**（`cli.main.cmd_plan` 用文件存在性判），别用这个开关把
    「读不出来」也兜住：那会让「历史一直很差」与「历史读不到」在落库的 Plan 上
    长得一模一样。

    **LLM 的门槛是「两个都给」**：只给 `llm` 而不给 `budget` 时不调用——静默造一个
    预算对象会让「这次调用有没有被记账」有两个来源。预算扣减走 `try_acquire()` 的
    **无参形态**（只受 per-run 限）：这是**一次 run 级批量调用**（整个 Plan 一次），
    不是 per-testcase 的调用，per-testcase 配额在这里没有语义。

    失败/降级一律**只影响 `reasons` 与同分次序**，Plan 照常产出（plan Steps 的降级要求）。
    """
    notes: list[str] = []
    case_list = list(cases)
    targets, drift_fallback = _resolve_targets(planner_input, metadata,
                                              base_metadata, case_list, notes)

    if not planner_input.changed_files:
        # 矩阵 #6：空改动 → 空 Plan 并明示（不编造依据）。
        notes.append("changed_files 为空：本次没有改动 → 空 Plan（矩阵 #6）")
    elif not targets:
        # 改动非空但一个 target 都没映射到——把**未匹配的文件**列出来，否则
        # 「这次没碰 UI」与「metadata 的路径写法与 git 不一致」分不开。
        unmatched = unmatched_changed_files(planner_input.changed_files, metadata)
        notes.append(
            f"改动 {len(planner_input.changed_files)} 个文件，但没有一个映射到 "
            f"metadata 里的元素（未匹配: {', '.join(unmatched) or '(无)'}）→ 空 Plan")

    impact: frozenset[str] = frozenset(
        case_id for target in targets
        for case_id in knowledge.impact_of(target))

    if targets and not impact:
        notes.append(
            f"改动命中 {len(targets)} 个元素（{', '.join(targets[:5])}"
            f"{'…' if len(targets) > 5 else ''}），但没有用例引用它们 → 空 Plan")

    if impact:
        # 口径 1 的「明示」：候选集是影响面内的用例，其余不进 Plan——**说出来**，
        # 而不是让人从「tasks 里没有它」去推断（那正是「静默丢弃」的形态）。
        notes.append(
            f"候选集 = 影响面内的 {len(impact)} 个用例；其余 "
            f"{max(0, len(case_list) - len(impact))} 个用例不在本次改动的影响面内，"
            f"不进 Plan（设计 §5.3 的候选集口径）")

    metas = _case_metas(case_list)
    # 候选集 = 影响面内的用例（口径 1）。按用例**原始顺序**遍历，保证同一输入下
    # 未排序阶段的处理顺序也确定（最终顺序由 order_key 全序决定）。
    # `case.id` 直接取属性（不用 `getattr` 兜底）：传错对象时 `"?"` 会让**所有用例塌成
    # 同一个 id**，Plan 里出现一条 id 为 "?" 的任务而全程无报错——与 P3-1 同形。
    candidates = [c for c in case_list if c.id in impact]
    # 历史**一次读全量**再按用例分组（不在循环里逐用例调 `trace_history`）。
    # `no_history_reason` 非空 = 调用方**已知**没有历史数据（如 trace 库尚不存在）：
    # 此时不读库、历史项按 0.0 计，并把原因写进 notes + 每条 reasons 的 history 行。
    history = {} if no_history_reason else (
        _history_by_case(knowledge) if candidates else {})
    if no_history_reason and candidates:
        notes.append(no_history_reason)

    unrankable: list[Unrankable] = []
    scored: list[dict] = []
    for case in candidates:
        cid = case.id
        meta = metas.get(cid) or TestCaseMeta(testcase_id=cid)
        try:
            risk = case_risk(meta, repository, build=planner_input.app_build)
        except (UnknownReferenceError, AmbiguousReferenceError) as e:
            # P1 的同一句话（`runner/testcase_runner.py`）：引用错误是**用例缺陷**
            # （lint 应已拦截），运行期直接 FAIL、不做 recovery。这里同理：不猜风险
            # （猜 LOW 会让它沉底，而它恰恰最可能失败）——列出来让人看见。
            unrankable.append(Unrankable(
                testcase_id=cid, reason=f"{type(e).__name__}: {e}"))
            continue
        rate = failure_rate(history.get(cid, ()))
        score = priority_score(meta, impact, rate, risk)
        scored.append({"case": case, "meta": meta, "risk": risk, "rate": rate,
                       "score": score,
                       "basis": rule_basis(hit=cid in impact, rate=rate,
                                           risk=risk, score=score,
                                           drift=drift_fallback,
                                           no_history=bool(no_history_reason))})

    scored.sort(key=lambda e: order_key(e["score"], e["meta"].testcase_id))
    deterministic = [e["meta"].testcase_id for e in scored]

    if unrankable:
        notes.append(
            f"{len(unrankable)} 个用例进了影响面但无法打分（坏引用，lint 应已拦截）："
            f"{', '.join(u.testcase_id for u in unrankable)}")

    audit: list[dict] = []
    llm_reasons: dict[str, str] = {}
    if scored and llm is not None and budget is not None:
        if not budget.try_acquire():
            notes.append("LLM 预算耗尽/熔断 → reasons 降级为规则摘要（Plan 照常产出）")
        else:
            llm_reasons, order = _llm_pass(
                planner_input, scored, llm, budget, notes, audit)
            if order is not None:
                scored = _apply_order(scored, order)

    tasks = [
        TestPlanTask(
            testcase_id=e["meta"].testcase_id,
            priority=e["score"],
            reasons=e["basis"] + (
                [f"llm: {llm_reasons[e['meta'].testcase_id]}"]
                if e["meta"].testcase_id in llm_reasons else []),
            source=_SOURCE_EXISTING)
        for e in scored
    ]
    return PlanResult(
        plan=TestPlan(
            plan_id=plan_id_for(planner_input.app_build, planner_input.git_commit,
                                planner_input.changed_files),
            app_build=planner_input.app_build,
            git_commit=planner_input.git_commit,
            tasks=tasks),
        notes=tuple(notes),
        unrankable=tuple(unrankable),
        audit=tuple(audit))


def _llm_pass(planner_input: PlannerInput, scored: list[dict], llm, budget,
              notes: list[str], audit: list[dict]
              ) -> tuple[dict[str, str], tuple[str, ...] | None]:
    """一次批量 LLM 调用：拿 `reasons` + 同分 tie-break。失败只降级，不抛。"""
    from llm.parser import parse_plan_reasons
    from llm.prompt import build_plan_rationale_prompt

    deterministic = [e["meta"].testcase_id for e in scored]
    # 段位要按**分数**算（同分共享一段），所以带上 score 一起交给判据。
    ordered_pairs = [(e["meta"].testcase_id, e["score"]) for e in scored]
    prompt = build_plan_rationale_prompt(
        app_build=planner_input.app_build,
        git_commit=planner_input.git_commit,
        changed_files=list(planner_input.changed_files),
        entries=[{"testcase_id": e["meta"].testcase_id, "priority": e["score"],
                  "basis": e["basis"]} for e in scored])
    timeout = budget.config.timeout_seconds
    try:
        raw = llm.complete(prompt, timeout=timeout)
    except Exception as e:  # noqa: BLE001 — provider 故障如实降级（不吞掉 Plan）
        budget.record_failure()
        notes.append(f"LLM 调用失败（{type(e).__name__}）→ reasons 降级为规则摘要")
        return {}, None

    parsed = parse_plan_reasons(raw)
    # 只认**本 Plan 里真有**的 id：多出来的（幻觉 / 别的 Plan 的 id）丢弃并留痕。
    unknown = sorted(set(parsed.reasons) - set(deterministic))
    if unknown:
        audit.append({"kind": "llm_unknown_testcase_ids", "ids": unknown})
    reasons = {cid: text for cid, text in parsed.reasons.items()
               if cid in set(deterministic)}
    if parsed.ignored_fields:
        audit.append({"kind": "llm_ignored_fields",
                      "fields": sorted(parsed.ignored_fields)})

    order: tuple[str, ...] | None = None
    if parsed.order is not None:
        violations = llm_order_violations(ordered_pairs, parsed.order)
        if violations:
            # 矩阵 #5：丢弃重排、保留确定性顺序、审计留痕。
            audit.append({"kind": "llm_order_rejected",
                          "violations": violations,
                          "llm_order": list(parsed.order)})
            notes.append(
                f"LLM 试图跨分重排（{len(violations)} 处违规）→ 已丢弃重排、"
                f"保留确定性顺序（矩阵 #5）")
        else:
            order = parsed.order

    if reasons or order is not None:
        budget.record_success()
    else:
        budget.record_failure()
        notes.append("LLM 输出没有可用内容 → reasons 降级为规则摘要")
    return reasons, order


def _apply_order(scored: list[dict], order: Sequence[str]) -> list[dict]:
    """按 LLM 的同分次序重排（`order` 已通过 `llm_order_violations` 判定为合法排列）。

    合法性意味着「跨分顺序不变」，所以这里**只可能**改变同分内部的次序——正是设计
    §5.2 允许 LLM 做的全部。
    """
    by_id = {e["meta"].testcase_id: e for e in scored}
    return [by_id[cid] for cid in order]
