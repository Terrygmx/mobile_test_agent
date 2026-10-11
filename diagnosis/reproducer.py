"""reproducer.py — Reproducibility Engine（设计 §8.4 / F9；Task 5.4 / P3-23）。

设计 §8.4 原文：

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

## ⚠️ 复用对象修正（plan 底稿修订 #9）：阶段判定本体在 `executor/policy.py`

plan 原文写「复用 P1 `agent/recovery.py` 的判定函数」——`agent/recovery.py`
**只是消费方**。判「动作是否已发出」的**本体**是 `executor.policy`：

| 本体 | 位置 |
|---|---|
| `FailurePhase`（PRE_DISPATCH / POST_DISPATCH） | `executor/policy.py` |
| `retry_decision(phase, idempotency, has_postcondition)` | 同上 |
| `max_attempts_for(retryable, config_max_attempts)` | 同上 |
| `make_postcondition_checker(pipeline, runner)` | `cli/pipeline.py` |

本模块**求值**它们，不重新推断「动作是否已发出」——那正是 H7 要防的：
重推一遍就可能推出「可以重试」，然后重复点一次支付按钮。所以本模块里
**没有**任何 `if failure_type == …` 形态的阶段判断，`_plan` 的每个分支都由
`retry_decision` 的结论驱动。

## 决策树 = `retry_decision` 的表 + 一道风险闸

| 失败阶段 | 有效幂等性 | effective_risk | 本引擎的动作 |
|---|---|---|---|
| PRE_DISPATCH | 任意 | 任意 | 重跑（上界 3 次）；失败即计入复现 |
| POST_DISPATCH | IDEMPOTENT | **LOW** | 重跑（上界 3 次） |
| POST_DISPATCH | IDEMPOTENT | 非 LOW | **不重跑** → 「无法确认」 |
| POST_DISPATCH | NON_IDEMPOTENT / UNKNOWN | 任意 | **不重跑**，查 postcondition |

前三行的「重跑 / 不重跑」由 `retry_decision().allowed` 给；**第四行**
（`check_postcondition`）是 H7 的落点：动作已发出且不可重复，唯一能判定
「它到底成没成」的途径是 postcondition——**不是**再发一次。

### ⚠️ 口径登记一：风险闸是**本引擎自己**的，不在 `retry_decision` 里

设计 §8.4 第一行的条件是 **`idempotent` **且** `effective_risk == LOW`**；
而 `retry_decision` 只吃 `(phase, idempotency, has_postcondition)`——**没有
risk 形参**。所以「POST_DISPATCH + 幂等 + 非 LOW」这一格在 P1 本体里是
`allowed=True`，本引擎**再收一道**：不自动重跑，记「无法确认」。

为什么不干脆跟本体走（照重不误）？因为「幂等」说的是**重复不改变结果**，
不是**重复没有代价**（`pay` 幂等与否是声明，不是我们能验证的事实），而
`effective_risk` 正是 P1 对「这一步有多危险」的确定性推导（F1 的同一个
`effective_risk`）。自主**重复**一个 HIGH 动作，与探索侧「LOW-only 才自动
执行」是同一道闸（`explorer/policy.py::pre_judge`）。

**与 Guard 的关系（不重叠、不替代）**：重跑走的仍是同一条 Guard 通路
（F12），所以**执行**层面 HIGH 依然由 P1 Guard 判；本闸管的是「**要不要
主动再来一次**」这个选择权。缺 risk（旧 run 的列是空的）时**按非 LOW
处理**（fail-closed，与 `experience/runtime_guard.py` 的「`None` 按最高
风险处理」同款）——「不知道多危险」不构成自动重跑的理由。

### ⚠️ 口径登记一之二：缺字段一律往「不重发」的方向兜（**fail-closed**）

`steps` 表可能缺 `failure_phase` / `effective_idempotency`（旧 run，或失败发生在
find 之前）。替换规则是**两处都指向「不重发」**：

| 缺什么 | 替换成 | 依据 |
|---|---|---|
| `effective_idempotency` | `Idempotency.UNKNOWN` | 7.4-3：「UNKNOWN 一律按 NON_IDEMPOTENT 处理」——「我们不知道」正是 UNKNOWN 的语义 |
| `failure_phase` | `FailurePhase.POST_DISPATCH` | H7 是硬约束（「绝不重试」）。不知道动作发没发出时，**假定已发出**才是不重发的方向 |

⚠️ 刻意**不**用「缺 phase → 当 PRE_DISPATCH」：那个默认是 **fail-open** 的
——它假定「动作从未发出」从而放行重跑，而真正的最坏情形（非幂等动作其实已经
发出、结果未知）恰好会因此被重发一次。替换后**照常走本体**（不新增分支、
不新增 mode），且 `assumed` 字段一路带进 `audit`——审计要能区分「真判据」与
「保守替换」。

### ⚠️ 口径登记二：`reproducible` 是三态，「不重跑」≠「复现不了」

| 结论 | `reproducible` | `reproduction_count` | evidence |
|---|---|---|---|
| 至少一次重跑**同样失败** | `True` | 失败次数 | `reproducible_3_times` |
| 重跑**通过**（跑不出来） | `False` | 0 | 无 |
| 主动不重跑 / 无法判断 / 一次都没跑成 | `None` | 0 | 无 |

第三类是本模块**最容易被写错**的地方：把它记成 `False` 是**过度声称**
（我们根本没跑），而 `bug_candidates.reproducible` 是三态列（迁移 005）
——`None` 正是为「不知道」留的。同理，`postcondition` **成立**时记 `False`
（设计 §8.4 明文「不算复现失败」：原始动作其实成功了，没有失败可复现）。

## 重跑经 `AgentToolkit`（F12）；本模块不持有执行栈

重跑调 `run_testcase`（`GUARDED_TOOLS` 之一）——本模块**不持有** Executor /
StepRunner / SessionPipeline，它们藏在调用方注入的 `toolkit` 后面（构造
类型断言 + 与 `explorer` 同款的 AST 守卫纪律）。

⚠️ **现状登记**：`run_testcase` 目前是 `NOT_WIRED`（归 M6 的驱动设备工具
循环，见 `agents/tools.py::NOT_WIRED` 与 `docs/p3_data_audit.md` 的 Task 3.4
决定 1）。所以**现阶段生产装配**由 `mta diagnose`（Task 5.6）注入 `runner`
回调；缺省通路（toolkit）在 M6 接线后**自动生效**，本模块无需改动。

## 预算（F11 / F4）

每次重跑前 `llm_budget.try_acquire()`——复现是**额外的** LLM 消耗源（重跑
里可能有恢复尝试），不扣就绕过了 F11「四维预算」的精神。耗尽即**停止重跑**
并如实记 note。⚠️ 口径登记三：这一扣是「重跑的**许可费**」，与重跑内部自身
的扣减是**两笔**——本模块看不到内层计数，只保证「没有名额就不发起重跑」。
本模块**不**调 `record_success/record_failure`：熔断的连续失败计数归恢复流程
（10.5「一次 LLM 恢复尝试未以 RECOVERED 收尾」），复现引擎不知道重跑内部
每次调用的收尾，代它记分只会污染熔断判据。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agents.tools import AgentToolkit, ToolError
from diagnosis.models import EvidenceItem
from executor.policy import FailurePhase, retry_decision
from llm.budget import LLMBudget
from testcase.schema import Idempotency, Risk

__all__ = ["MAX_REPRO_ATTEMPTS", "ReproResult", "StepProbe", "reproduce"]

# 设计 §8.4：「最多重跑 3 次」。**是复现的上界，不是重试的上界**——
# `executor.policy.max_attempts_for` 管的是「同一步能不能再试一次」，本常量
# 管的是「这条用例要不要再跑一遍」，两个层级（前者是 step 级，后者是
# testcase 级）。3 这个数字取自设计，不取自 `DEFAULT_MAX_ATTEMPTS`（2）。
MAX_REPRO_ATTEMPTS = 3

# `steps.status` 词表里的「通过」（`runner/result.py::TESTCASE_STATUSES` 同域；
# 由 `tests/unit/test_reproducer.py` 断言包含关系，不在这里 import 整个 runner）。
_PASS_STATUSES = frozenset({"PASS"})


@dataclass(frozen=True)
class StepProbe:
    """失败步骤的**判定输入**（设计 §8.4 的四个条件）。

    刻意是**值对象**而不是从 `TraceStep` 现取：这些值分属两个来源——`phase` /
    `idempotency` / `risk` 在 `steps` 表的三列里（P1 `Lifecycle.record_step`
    一直在写），`postcondition` 却**不在 trace 里**（它是**用例声明**，
    `cli/pipeline.py` 从 `TestCase` 取）。把两者并成「一个 probe」让本模块
    对来源无感，也让单测不必造 trace（F13：脱离设备可单测）。

    `postcondition` 是 `testcase.schema.Postcondition | None`——原样透传给
    调用方注入的 checker（`cli/pipeline.py::make_postcondition_checker` 的
    产物），本模块**不解释**它的内容。
    """

    phase: FailurePhase | None = None
    idempotency: Idempotency | None = None
    risk: Risk | None = None
    has_postcondition: bool = False
    postcondition: Any = None


@dataclass(frozen=True)
class ReproResult:
    """一次复现判定的结论（填 `bug_candidates.reproducible` / `_count`）。"""

    # 三态：True 复现了 / False 跑不出来（或原始动作其实成功）/ None 无法确认
    reproducible: bool | None
    reproduction_count: int = 0
    attempts: int = 0
    evidence: tuple[EvidenceItem, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    audit: tuple[dict, ...] = field(default_factory=tuple)

    @property
    def confirmed(self) -> bool:
        """至少一次重跑复现了同样的失败。"""
        return self.reproducible is True


def reproduce(ctx, *, toolkit: AgentToolkit, llm_budget: LLMBudget,
              runner: Callable[[str], Any] | None = None,
              postcondition_checker: Callable[[Any], bool | None] | None = None,
              probe_for: Callable[[Any], StepProbe] | None = None,
              max_attempts: int = MAX_REPRO_ATTEMPTS) -> ReproResult:
    """设计 §8.4 的决策树（见模块 docstring 的表）。

    | 参数 | 说明 |
    |---|---|
    | `ctx` | `DiagnosisContext`——本函数只读**本 run 失败现场**的第一条失败步骤 |
    | `toolkit` | **F12 的唯一执行通路**：缺省经它调 `run_testcase` |
    | `llm_budget` | F4/F11：每次重跑前 `try_acquire()`，耗尽即停止重跑 |
    | `runner` | 重跑替身注入点（`Callable[[testcase_id], TestcaseResult]`）——单测传 Fake；Task 5.6 的 CLI 传基于 `SessionPipeline` 的真回调 |
    | `postcondition_checker` | 复用 `cli/pipeline.py::make_postcondition_checker` 的产物；缺省 → postcondition 判定为「无法判断」 |
    | `probe_for` | `step → StepProbe`；缺省 `_probe_from_step`（读 trace 三列 + 用例声明由调用方补） |

    返回 `ReproResult`——`reproducible` 是**三态**（见模块 docstring 的表）。
    """
    if not isinstance(toolkit, AgentToolkit):
        raise TypeError(
            "reproduce 只经 AgentToolkit 重跑（F12：唯一执行通路）——"
            "直接注入 StepRunner / SessionPipeline 在构造上不可达")
    if not isinstance(llm_budget, LLMBudget):
        raise TypeError(
            f"llm_budget 必须就是 P1 LLMBudget 实例（F4：不新写计数器）——"
            f"got {type(llm_budget).__name__}")

    first = _first_failure(ctx)
    if first is None:
        return ReproResult(
            reproducible=False, attempts=0,
            notes=("本 run 没有失败步骤——没有可复现的东西",))

    testcase_id = getattr(first, "testcase_id", "") or ""
    if not testcase_id:
        # 没有用例 id 就重跑不了（重跑回调要它）。这是**如实报告**而不是静默
        # 返回「跑不出来」——后者会让「不知道重跑哪条」伪装成「复现不了」。
        return ReproResult(
            reproducible=None, attempts=0,
            notes=("失败步骤没有 testcase_id，无法重跑——记为「无法确认」"
                   "（不伪装成「复现不了」）",))

    probe = (probe_for or _probe_from_step)(first)
    plan = _plan(probe)

    if plan["mode"] == "postcondition":
        verdict = _check_postcondition(probe, postcondition_checker)
        if verdict is True:
            return ReproResult(
                reproducible=False, attempts=0,
                notes=(f"postcondition 成立 → 原始动作其实成功了"
                       f"（设计 §8.4：不算复现失败）——{plan['reason']}",),
                audit=({"kind": "repro_postcondition", "satisfied": True,
                        "phase": plan["phase"],
                        "idempotency": plan["idempotency"],
                        "assumed": plan["assumed"]},))
        return ReproResult(
            reproducible=None, attempts=0,
            notes=(f"postcondition {'不成立' if verdict is False else '无法判断'}"
                   f" → 记「无法确认复现」（不重跑、不加分）——{plan['reason']}",),
            audit=({"kind": "repro_postcondition", "satisfied": verdict,
                    "phase": plan["phase"],
                    "idempotency": plan["idempotency"],
                    "assumed": plan["assumed"]},))

    if plan["mode"] != "rerun":
        # no_retry（POST_DISPATCH 非幂等且无 postcondition）/ risk_blocked
        # （POST_DISPATCH 幂等但风险非 LOW）。**不重跑 ≠ 复现不了**——我们
        # 主动放弃，如实记 None（模块 docstring 的口径登记二）。
        return ReproResult(
            reproducible=None, attempts=0,
            notes=(f"{plan['reason']} → 记「无法确认复现」（不重跑，"
                   f"不是「复现不了」）",),
            audit=({"kind": "repro_skipped", "mode": plan["mode"],
                    "phase": plan["phase"],
                    "idempotency": plan["idempotency"],
                    "risk": plan["risk"], "assumed": plan["assumed"],
                    "reason": plan["reason"]},))

    return _rerun_loop(ctx, first, testcase_id, plan=plan, toolkit=toolkit,
                       llm_budget=llm_budget, runner=runner,
                       max_attempts=max_attempts)


# --- 重跑循环 -----------------------------------------------------------------


def _rerun_loop(ctx, first, testcase_id, *, plan, toolkit, llm_budget, runner,
                max_attempts) -> ReproResult:
    """最多 `max_attempts` 次重跑（设计 §8.4 第一行）。

    **跑满上界，不提前收工**：重跑的价值在于把「确定性失败」与「flaky」分开
    ——一次通过就收工会让「fail/pass/fail」这种形态记成 `count=1` 的干净复现。
    三类计数**分开**记（`failures` / `passed` / `unstarted`）：把「重跑没跑成」
    混进「重跑跑出来没复现」会让设备抖动伪装成 `reproducible=False`——那是一次
    **过度声称**（我们并没有得到「跑不出来」的结论）。
    """
    attempts = failures = passed = unstarted = 0
    notes: list[str] = [f"重跑计划：{plan['reason']}（上界 {max_attempts} 次）"]
    # 第一条记**决定本身**（含保守替换的 assumed）——否则重跑路径的审计里
    # 只有一串 repro_attempt，看不到「凭什么重跑」（含缺字段的保守替换）。
    audit: list[dict] = [{"kind": "repro_plan", "mode": plan["mode"],
                          "phase": plan["phase"],
                          "idempotency": plan["idempotency"],
                          "risk": plan["risk"], "assumed": plan["assumed"],
                          "reason": plan["reason"]}]

    while attempts < max_attempts:
        if not llm_budget.try_acquire():
            notes.append(
                f"LLM 预算耗尽（LLMBudget.try_acquire → False）→ 停止重跑"
                f"（已重跑 {attempts} 次；F4/F11 不绕过计数）")
            break
        attempts += 1
        try:
            outcome = _run_once(toolkit, runner, testcase_id, ctx)
        except _RerunChannelUnavailable as e:
            attempts -= 1
            notes.append(str(e))
            break
        except Exception as e:  # noqa: BLE001 — 设备故障不是复现结论
            unstarted += 1
            notes.append(f"第 {attempts} 次重跑未完成（{type(e).__name__}: "
                         f"{e}）→ 本次不作数")
            audit.append({"kind": "repro_attempt", "attempt": attempts,
                          "status": "UNSTARTED",
                          "error": f"{type(e).__name__}"})
            continue
        status = _status_of(outcome)
        audit.append({"kind": "repro_attempt", "attempt": attempts,
                      "status": status})
        if status in _PASS_STATUSES:
            passed += 1
            notes.append(f"第 {attempts} 次重跑通过（status={status}）——未复现")
        else:
            failures += 1
            notes.append(f"第 {attempts} 次重跑失败（status={status}）——复现")

    if failures == attempts >= 2:
        # **每次重跑都复现**（一次都没通过）→ 领 `reproducible_3_times`。
        # 判据是「没有观察到任何一次通过」，不是「跑了满 3 次」：预算把上界
        # 掐到 2 时仍然该给这份证据（否则一个与证据无关的配置项会静默清零
        # 最强的一档证据）。`>= 2` 是因为**一次重跑只是一个数据点**，构不成
        # 「复现模式」。类别名里的 "3" 是设计 §8.4 的上界，detail 里记实际次数。
        return ReproResult(
            reproducible=True, reproduction_count=failures, attempts=attempts,
            evidence=(EvidenceItem(
                kind="reproducible_3_times",
                detail=f"{attempts} 次重跑全部复现了同样的失败（无一次通过）",
                source=f"repro:testcase={testcase_id}"),),
            notes=tuple(notes), audit=tuple(audit))
    if failures:
        # 复现了，但不是每次 → flaky，或只跑了一次。`reproducible=True`（确实
        # 复现过）+ 如实记比例；**不给** 3 次证据（该类别要求「每次都复现」）。
        why = ("flaky：有重跑通过了" if passed
               else f"只跑了 {attempts} 次（不足 2 次），不足以称为复现模式")
        return ReproResult(
            reproducible=True, reproduction_count=failures, attempts=attempts,
            notes=tuple(notes) + (
                f"⚠️ {why}——不领 reproducible_3_times 证据"
                f"（该类别要求每次重跑都复现）",),
            audit=tuple(audit))
    if passed:
        return ReproResult(
            reproducible=False, reproduction_count=0, attempts=attempts,
            notes=tuple(notes), audit=tuple(audit))
    # 一次都没跑成（预算在第一次 try_acquire 就耗尽 / 设备全程抖动）——
    # **不声称**「跑不出来」（那会是一次没跑就下的结论）。
    return ReproResult(
        reproducible=None, reproduction_count=0, attempts=attempts,
        notes=tuple(notes) + (
            f"一次都没跑成（未完成 {unstarted} 次）→ 记「无法确认」",),
        audit=tuple(audit))


class _RerunChannelUnavailable(RuntimeError):
    """重跑**通道**不可用（工具未接线 / 依赖没注入）——不是复现结论。

    与「重跑跑失败」严格区分：`run_testcase` 目前 `NOT_WIRED`（M6 才接线），
    若不区分，一次都没跑成的调用会得到 `reproducible=False`——把「没接线」
    伪装成「跑不出来」。
    """


def _run_once(toolkit, runner, testcase_id, ctx):
    """一次重跑。经 toolkit（F12）；`runner` 是替身/生产回调的注入点。

    ⚠️ 配置错误（`ToolError`：未接线 / 依赖没注入）**上抛**并转成
    `_RerunChannelUnavailable`——它们不是「这次没跑成」，是「这条路根本不通」。
    其余异常（设备抖动等）由调用方记 `UNSTARTED` 后继续下一次。
    """
    if runner is not None:
        return runner(testcase_id)
    try:
        return toolkit.call("run_testcase", target=testcase_id,
                            build=getattr(ctx, "app_build", "") or "local")
    except ToolError as e:
        raise _RerunChannelUnavailable(
            f"重跑通道不可用（{type(e).__name__}: {e}）→ 不声称任何复现结论") \
            from e


# --- 判定 ---------------------------------------------------------------------


def _plan(probe: StepProbe) -> dict:
    """设计 §8.4 → `executor.policy.retry_decision` 的一次求值（+ 风险闸）。

    ⚠️ **不重推阶段**（plan 修正 #9）：`phase` / `idempotency` 是 `StepProbe`
    的**输入**，本函数只把它们喂给本体。

    ## 缺字段时的保守替换：**两处都往「不重发」的方向兜**

    | 缺什么 | 替换成 | 依据 |
    |---|---|---|
    | `effective_idempotency` | `Idempotency.UNKNOWN` | 7.4-3：「UNKNOWN 一律按 NON_IDEMPOTENT 处理」——「我们不知道」正是 UNKNOWN 的语义 |
    | `failure_phase` | `FailurePhase.POST_DISPATCH` | H7 是硬约束（「绝不重试」）。不知道动作发没发出时，**假定已发出**才是不重发的方向 |

    ⚠️ 这里刻意**不**用「缺 phase → 当 PRE_DISPATCH」：那个默认是**fail-open**
    的——它假定「动作从未发出」从而放行重跑，而真正的最坏情形（非幂等动作
    其实已经发出、结果未知）恰好会因此被重发一次。H7 的保守方向是「不重发」，
    缺字段不是放宽它的理由。（`SECURITY_BLOCKED` 的 `phase=None` 也走这条：
    Guard 判定确定性，重试必然再拦，不重跑是对的。）

    替换后**照常走本体**——不新增分支、不新增 mode：缺字段只是把输入补成
    保守值，判定仍然只有 `retry_decision` 一个来源。
    """
    missing = [n for n, v in (("failure_phase", probe.phase),
                              ("effective_idempotency", probe.idempotency))
               if v is None]
    phase = probe.phase if probe.phase is not None \
        else FailurePhase.POST_DISPATCH
    idem = probe.idempotency if probe.idempotency is not None \
        else Idempotency.UNKNOWN

    decision = retry_decision(phase, idem,
                              has_postcondition=probe.has_postcondition)
    base = {"phase": phase.value, "idempotency": idem.value,
            "risk": probe.risk.name if probe.risk else None,
            "assumed": tuple(missing)}

    if decision.allowed:
        if phase is FailurePhase.POST_DISPATCH and probe.risk is not Risk.LOW:
            return {**base, "mode": "risk_blocked",
                    "reason": f"POST_DISPATCH 幂等但 effective_risk="
                              f"{probe.risk.name if probe.risk else 'UNKNOWN'}"
                              f"（非 LOW）→ 不自动重跑（设计 §8.4 第一行要求"
                              f" LOW；口径登记一见模块 docstring）"
                              + _assumed_note(missing)}
        return {**base, "mode": "rerun", "reason": decision.reason
                + _assumed_note(missing)}
    if decision.check_postcondition:
        return {**base, "mode": "postcondition",
                "reason": decision.reason + _assumed_note(missing)}
    return {**base, "mode": "no_retry",
            "reason": decision.reason + _assumed_note(missing)}


def _assumed_note(missing: list[str]) -> str:
    """缺字段的留痕后缀（审计要能区分「真判据」与「保守替换」）。"""
    if not missing:
        return ""
    return f"［trace 缺 {'/'.join(missing)} → 已按保守值替换］"


def _first_failure(ctx):
    """本 run 失败现场的第一条失败步骤。

    ⚠️ **只看现场（`ctx.steps`）**，不看 `history`：历史里的失败是**别的
    run** 的事——拿它去重跑「本 run 的失败」会跑错用例。`history` 是给假设
    引擎做关联用的（`diagnosis/classifier.py`），不是复现的输入。

    优先用 `DiagnosisContext.first_failure`（它自己的判定，F1 的复用精神），
    缺该属性时（单测的鸭子类型 ctx）回退到扫 `steps` 的 `failure_type`。
    """
    step = getattr(ctx, "first_failure", None)
    if step is not None:
        return step
    for s in tuple(getattr(ctx, "steps", ())):
        if getattr(s, "failure_type", None):
            return s
    return None


def _probe_from_step(step, *, postcondition=None, has_postcondition=None
                     ) -> StepProbe:
    """缺省 probe：trace 三列 + **调用方补的用例声明**。

    两个来源必须合成一个 probe，因为它们物理上不在同一处：

    | 字段 | 来源 |
    |---|---|
    | `phase` / `idempotency` / `risk` | `steps` 表的三列（Task 5.4 新增读取） |
    | `postcondition` | **用例声明**（`testcase.schema.Postcondition`）——trace 里**没有** |

    `postcondition` / `has_postcondition` 是**调用方给的**（Task 5.6 的
    `mta diagnose` 手上有 `TestCase`，按失败的 `step_index` 取该步声明）。
    缺省时退而读 trace 的 `postcondition_*` 痕迹（P1 的 postcondition 检查会
    把结论写进 `detail_json`）——那是**结论**不是**声明**，只能作兜底：
    有结论说明这一步确实声明过 postcondition，故可据此置 `has_postcondition`。
    """
    detail = getattr(step, "detail", None) or {}
    post_marks = {k: v for k, v in detail.items()
                  if isinstance(k, str) and k.startswith("postcondition")}
    declared = detail.get("postcondition")
    if has_postcondition is None:
        has_postcondition = (bool(post_marks) or declared is not None
                             or postcondition is not None)
    return StepProbe(
        phase=_as_enum(FailurePhase,
                       getattr(step, "failure_phase", None)
                       or detail.get("failure_phase")),
        idempotency=_as_enum(Idempotency,
                             getattr(step, "effective_idempotency", None)
                             or detail.get("effective_idempotency")),
        risk=_as_enum(Risk, getattr(step, "effective_risk", None)
                      or detail.get("effective_risk")),
        has_postcondition=bool(has_postcondition),
        postcondition=postcondition if postcondition is not None else declared)


def _as_enum(enum_cls, value):
    """`str | enum | None` → 枚举成员；**认不出的值 → None**（fail-closed）。

    认不出的值不当成「未知幂等性」的 `UNKNOWN` 成员——`UNKNOWN` 是一个**声明**
    （「用例说不知道」），与「我们读到了一个不认识的字符串」（列被写坏了）
    是两件事。返回 None 让 `_plan` 走「缺字段 → 按 PRE_DISPATCH 保守处理」，
    而 UNKNOWN 会走 `retry_decision` 的 NON 分支（更严）——**两条都保守**，
    但把「读坏了」伪装成「用例声明了 UNKNOWN」会污染审计。
    """
    if value is None:
        return None
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls[str(value)]
    except (KeyError, ValueError):
        return None


def _status_of(outcome) -> str:
    """重跑结论 → 状态词。接受 `TestcaseResult` / `ToolResult` / dict。"""
    status = getattr(outcome, "status", None)
    if isinstance(status, str) and status:
        return status
    value = getattr(outcome, "value", None)
    if isinstance(value, dict) and value.get("status"):
        return str(value["status"])
    inner = getattr(value, "status", None)
    if isinstance(inner, str) and inner:
        return inner
    return "PASS" if getattr(outcome, "ok", False) else "FAIL"


def _check_postcondition(probe, checker) -> bool | None:
    """查 postcondition（设计 §8.4 的 POST_DISPATCH 分支）。

    返回 `True`（成立）/ `False`（不成立）/ `None`（**无法判断**）。⚠️ 设计
    §8.4 把「不成立/**无法判断**」并列（都算「无法确认复现」），所以调用方对
    两者一视同仁；保留三态是为了让**审计**能区分「查了，不成立」与「查不了」。

    ⚠️ **不在诊断期重放设备**：checker 是 `make_postcondition_checker` 的产物
    （跑一次 `wait_for`），需要活的 sandbox 会话——Task 5.6 的 Gate 演示覆盖。
    单测注入替身（F13）。
    """
    spec = probe.postcondition
    if spec is None or checker is None:
        return None
    try:
        return bool(checker(spec))
    except Exception:  # noqa: BLE001 — 观测故障不是判定结果（8.2 反模式）
        return None
