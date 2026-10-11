"""Task 5.4（P3-23）：Reproducibility Engine（设计 §8.4 / F9）。

七块：

1. **前置与类型闸门**：`toolkit` 必是 `AgentToolkit`（F12）、`llm_budget` 必是
   P1 `LLMBudget`（F4）——两者都是**构造级**断言，不是「结果看起来一致」。
2. **决策树**：设计 §8.4 的每一格（PRE/POST × 幂等性 × 风险 × postcondition）
   逐格钉住，判据**全部来自 `executor.policy.retry_decision`**（本体复用）。
3. **postcondition 三分支**：成立 / 不成立 / 无法判断。
4. **预算与设备抖动**：两者都**不得**伪装成 `reproducible=False`。
5. **证据**：`reproducible_3_times` 进 Evidence Score（Task 5.3 的权重表）。
6. **trace 读取**：`read_trace_steps` 现在带 `failure_phase` /
   `effective_idempotency` / `effective_risk`——**不补这三列，本模块对任何真
   trace 都落进「缺字段」分支**（口径登记四）。
7. **F3**：`diagnosis/reproducer.py` 不直连数据源。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agents.models import TOOL_ASSET_TIER
from agents.tools import AgentToolkit, ToolResult
from diagnosis.context import DiagnosisContext
from diagnosis.evidence import score_evidence
from diagnosis.models import EvidenceItem
from diagnosis.reproducer import MAX_REPRO_ATTEMPTS, StepProbe, reproduce
from executor.guard import EnvKind, Guard
from executor.policy import FailurePhase
from graph import TraceStep, read_trace_steps
from llm.budget import LLMBudget
from runner.result import TestcaseResult
from testcase.schema import Idempotency, Risk
from tracer.storage import TESTCASE_STATUSES, TraceStore

APP = "com.phaset0.logindemo"
BUILD = "1026"

# 一条「非幂等、已发出、高风险」的失败步骤（最危险的那种：重推一次就是
# 重复支付）。默认用它——**默认值取最危险形态**，测试想放行就得显式放宽。
PAY = dict(testcase_run_id=1, step_index=0, step_type="tap",
           target_id="pay_button", status="FAILED",
           failure_type="ACTION_OUTCOME_UNKNOWN", testcase_id="login_001",
           failure_phase="POST_DISPATCH", effective_idempotency="NON_IDEMPOTENT",
           effective_risk="HIGH")


def _step(**over) -> TraceStep:
    kw = {**PAY, **over}
    return TraceStep(**kw)


def _ctx(step=None, *, steps=None) -> DiagnosisContext:
    seq = tuple(steps) if steps is not None else ((step or _step()),)
    return DiagnosisContext(run_id="run_1", app_id=APP, app_build=BUILD,
                            steps=seq)


def _toolkit() -> AgentToolkit:
    """**什么都没注入**的 toolkit——`run_testcase` 目前 NOT_WIRED（M6 才接线）。

    所以「缺省通路」在这里得到的必然是「通道不可用」，而不是「跑失败」：
    这正是 `_RerunChannelUnavailable` 要区分的东西（口径登记五）。
    """
    return AgentToolkit(guard=Guard(EnvKind.SANDBOX))


class _Runner:
    """可编排的重跑替身：按脚本弹结果，`raises` 走「设备抖动」分支。"""

    def __init__(self, *results, raises: int = 0):
        self.results = list(results)
        self.raises = raises
        self.calls: list[str] = []

    def __call__(self, testcase_id: str):
        self.calls.append(testcase_id)
        if self.raises:
            self.raises -= 1
            raise RuntimeError("device flake")
        return self.results.pop(0) if self.results else _fail()


def _fail(tc="login_001") -> TestcaseResult:
    return TestcaseResult(testcase_id=tc, status="FAIL",
                          failure_type="ACTION_OUTCOME_UNKNOWN")


def _pass(tc="login_001") -> TestcaseResult:
    return TestcaseResult(testcase_id=tc, status="PASS")


def _probe(**over) -> StepProbe:
    kw = dict(phase=FailurePhase.POST_DISPATCH,
              idempotency=Idempotency.NON_IDEMPOTENT, risk=Risk.HIGH)
    kw.update(over)
    return StepProbe(**kw)


def _reproduce(ctx=None, *, toolkit=None, budget=None, runner=None,
               checker=None, probe=None, **kw):
    return reproduce(
        ctx if ctx is not None else _ctx(),
        toolkit=toolkit if toolkit is not None else _toolkit(),
        llm_budget=budget if budget is not None else LLMBudget(
            max_calls_per_run=10),
        runner=runner, postcondition_checker=checker,
        probe_for=(lambda _step: probe) if probe is not None else None, **kw)


# --- ① 前置与类型闸门 ---------------------------------------------------------


def test_only_an_agent_toolkit_can_rerun():
    """F12：重跑的唯一通路是 `AgentToolkit`——构造级断言，不是文档声称。"""
    with pytest.raises(TypeError, match="AgentToolkit"):
        reproduce(_ctx(), toolkit=object(), llm_budget=LLMBudget())


def test_the_llm_budget_must_be_the_p1_instance():
    """F4：预算必须就是 P1 的 `LLMBudget`——不新写计数器。"""
    with pytest.raises(TypeError, match="LLMBudget"):
        reproduce(_ctx(), toolkit=_toolkit(), llm_budget=object())


def test_no_failure_step_means_there_is_nothing_to_reproduce():
    """没有失败步骤 → `reproducible=False`（**不是** None）。

    这是「本 run 没失败」——一个**确定**的结论（没有失败可复现），与「我们
    不知道」不同。反过来说，若把「没有失败」也记 None，`reproducible=None`
    就同时装了两种完全不同的东西，审核者无从区分。
    """
    res = _reproduce(_ctx(steps=()))
    assert res.reproducible is False and res.attempts == 0
    assert any("没有失败步骤" in n for n in res.notes)


def test_a_step_without_testcase_id_is_unconfirmable_not_unreproducible():
    """重跑不了（不知道跑哪条）→ **无法确认**，不是「复现不了」。

    把「不知道重跑哪条」记成 `False` 是一次过度声称：我们根本没有跑。
    """
    res = _reproduce(_ctx(_step(testcase_id="")))
    assert res.reproducible is None and res.attempts == 0
    assert any("无法确认" in n for n in res.notes)


# --- ② 决策树（每格都来自 retry_decision 的结论） ------------------------------


def test_pre_dispatch_reruns_even_for_a_non_idempotent_step():
    """**矩阵 #15 的钉子**：PRE_DISPATCH + NON_IDEMPOTENT → **可以重跑**。

    「动作从未发出」是**阶段**给的结论，与幂等性无关（7.4 表第一行）。用例
    刻意用最严的幂等性 + 最高风险：若有人把本引擎写成「幂等性驱动」或
    「风险驱动」（而不是让 `retry_decision` 判阶段），这条**必然红**。
    """
    runner = _Runner(_fail(), _fail(), _fail(), _fail())
    res = _reproduce(_ctx(_step(failure_phase="PRE_DISPATCH")),
                     runner=runner, budget=LLMBudget(max_calls_per_run=10))
    assert runner.calls == ["login_001"] * 3, "PRE_DISPATCH 允许重跑（跑满上界）"
    assert res.attempts == 3 and res.reproducible is True
    assert res.reproduction_count == 3
    assert [e.kind for e in res.evidence] == ["reproducible_3_times"]


def test_post_dispatch_non_idempotent_without_postcondition_never_reruns():
    """H7：已发出 + 非幂等 + 无 postcondition → **一次都不重跑**。

    `runner` 一旦被调用就说明「绝不重发」这条被破坏了——所以断言的是
    `calls == []`，而不是只看结论。
    """
    runner = _Runner(_fail())
    res = _reproduce(_ctx(), runner=runner)          # PAY 就是这一格
    assert runner.calls == [], "H7：POST_DISPATCH 非幂等绝不重跑"
    assert res.reproducible is None and res.attempts == 0
    assert res.reproduction_count == 0 and res.evidence == ()
    assert res.audit[0]["mode"] == "no_retry"


def test_post_dispatch_idempotent_low_risk_reruns_up_to_three_times():
    """设计 §8.4 第一行：幂等 + LOW → 最多重跑 3 次，失败即计入复现次数。"""
    runner = _Runner(_fail(), _fail(), _fail())
    res = _reproduce(_ctx(_step(effective_idempotency="IDEMPOTENT",
                                effective_risk="LOW")), runner=runner)
    assert res.attempts == MAX_REPRO_ATTEMPTS == 3
    assert res.reproduction_count == 3 and res.reproducible is True


def test_post_dispatch_idempotent_high_risk_is_not_rerun():
    """⚠️ **口径登记一**：设计 §8.4 第一行要求 `effective_risk == LOW`。

    `retry_decision` 没有 risk 形参，所以这一格在 P1 本体里是 `allowed=True`
    ——本引擎**再收一道**：非 LOW 不自动重跑（与探索侧「LOW-only 才自动执行」
    同一道闸）。`runner` 不被调用是判据本身。
    """
    runner = _Runner(_fail())
    res = _reproduce(_ctx(_step(effective_idempotency="IDEMPOTENT",
                                effective_risk="HIGH")), runner=runner)
    assert runner.calls == [], "非 LOW 的 POST_DISPATCH 不自动重跑"
    assert res.reproducible is None and res.audit[0]["mode"] == "risk_blocked"


def test_a_missing_risk_column_fails_closed():
    """风险**未知**按「非 LOW」处理（fail-closed）——与
    `experience/runtime_guard.py` 的「`None` 按最高风险处理」同款。

    「不知道多危险」不构成自动重跑的理由。
    """
    runner = _Runner(_fail())
    res = _reproduce(_ctx(_step(effective_idempotency="IDEMPOTENT",
                                effective_risk=None)), runner=runner)
    assert runner.calls == [] and res.reproducible is None
    assert res.audit[0]["mode"] == "risk_blocked"


def test_a_missing_phase_column_fails_closed_towards_not_redispatching():
    """⚠️ **缺字段往「不重发」兜**（H7 是硬约束，不是可放宽的默认）。

    早先的写法是「缺 phase → 当 PRE_DISPATCH」，那是 **fail-open**：它假定
    「动作从未发出」从而放行重跑，而真正的最坏情形（非幂等动作其实已经发出、
    结果未知）恰好会因此被重发一次。缺字段不是放宽 H7 的理由。

    替换规则（都写进 `_plan` 的 docstring）：缺 `failure_phase` →
    `POST_DISPATCH`（假定已发出）；缺 `effective_idempotency` → `UNKNOWN`
    （7.4-3：UNKNOWN 一律按 NON 处理——「我们不知道」正是 UNKNOWN 的语义）。
    本例两者都缺 → 落进 H7 的「绝不重跑」格。
    """
    runner = _Runner(_fail())
    res = _reproduce(_ctx(_step(failure_phase=None,
                                effective_idempotency=None)), runner=runner)
    assert runner.calls == [], "缺阶段时按「已发出」处理——绝不重发"
    assert res.reproducible is None
    assert res.audit[0]["assumed"] == ("failure_phase",
                                       "effective_idempotency")


def test_a_missing_idempotency_column_alone_fails_closed():
    """只缺幂等性 → `UNKNOWN` → 非幂等分支（H7），不是「当 IDEMPOTENT 放行」。"""
    runner = _Runner(_fail())
    res = _reproduce(_ctx(_step(effective_idempotency=None)), runner=runner)
    assert runner.calls == [] and res.reproducible is None
    assert res.audit[0]["mode"] == "no_retry"
    assert res.audit[0]["assumed"] == ("effective_idempotency",)


def test_a_missing_phase_alone_keeps_the_declared_idempotency():
    """只缺阶段 → 幂等性**声明照旧生效**（H17：显式声明优先）。

    假定的是阶段（`POST_DISPATCH`），不是幂等性——「IDEMPOTENT + 非 LOW」
    因此仍落进风险闸（不重跑）。若实现把缺字段当成「整个 probe 不可信」而
    连声明一起忽略，这一格会变成 `rerun`。
    """
    runner = _Runner(_fail())
    res = _reproduce(_ctx(_step(failure_phase=None,
                                effective_idempotency="IDEMPOTENT",
                                effective_risk="HIGH")), runner=runner)
    assert runner.calls == [] and res.audit[0]["mode"] == "risk_blocked"
    assert res.audit[0]["assumed"] == ("failure_phase",)


def test_a_guard_blocked_step_is_not_replayed_when_it_looks_dangerous():
    """`SECURITY_BLOCKED`（Guard 拦截）的实际形状：风险是 HIGH/CRITICAL。

    Guard 的 `phase=None` 是「动作从未发出」——但**缺 phase 时假定已发出**
    （fail-closed），于是它落进风险闸：不重跑。把被拦的动作重放一遍是
    H7/10.1 都不允许的。
    """
    runner = _Runner(_fail())
    res = _reproduce(_ctx(_step(failure_type="SECURITY_BLOCKED",
                                failure_phase=None,
                                effective_idempotency="IDEMPOTENT",
                                effective_risk="HIGH")), runner=runner)
    assert runner.calls == [] and res.reproducible is None
    assert res.audit[0]["mode"] == "risk_blocked"


def test_a_low_risk_guard_block_reruns_harmlessly_and_is_documented():
    """⚠️ 边界登记：`SECURITY_BLOCKED` + **LOW** 风险（`blocked_targets`
    命中一个低风险元素）→ 计划是 `rerun`。

    本引擎**刻意不读 `failure_type`**（plan 修正 #9：判定只许有一个来源，
    读它就是在重推「这一步发生了什么」）。这条重跑**无害**：Guard 判定是
    确定性的，重跑会在**动作发出之前**被再拦一次（零副作用），于是它被如实
    计为一次「确定性复现」。这是可接受的（该失败确实每次都复现），但**不是**
    「被拦的动作被放行了」——Guard 从未被绕过。这条测试把这个形态钉在明处，
    免得将来有人以为它是漏判。
    """
    runner = _Runner(_fail(), _fail(), _fail())
    res = _reproduce(_ctx(_step(failure_type="SECURITY_BLOCKED",
                                failure_phase=None,
                                effective_idempotency="IDEMPOTENT",
                                effective_risk="LOW")), runner=runner)
    assert res.audit[0]["mode"] == "rerun"
    assert len(runner.calls) == 3, "重跑确实发起了（但 Guard 会再拦）"


def test_the_plan_is_a_decision_not_a_re_derivation():
    """`_plan` 的每个分支都由 `retry_decision` 的结论驱动（plan 修正 #9）。

    直接对 `_plan` 打表：同样的 `(phase, idempotency)` 换 risk 只影响
    **风险闸**那一格，其余格的 mode 不变——若有人把阶段判断改写成
    `if failure_type == ...` 形态，这张表就对不上了。
    """
    from diagnosis.reproducer import _plan
    cases = {
        # (phase, idempotency, risk) → mode
        (FailurePhase.PRE_DISPATCH, Idempotency.NON_IDEMPOTENT, Risk.HIGH):
            "rerun",
        (FailurePhase.PRE_DISPATCH, Idempotency.UNKNOWN, Risk.HIGH): "rerun",
        (FailurePhase.POST_DISPATCH, Idempotency.IDEMPOTENT, Risk.LOW): "rerun",
        (FailurePhase.POST_DISPATCH, Idempotency.IDEMPOTENT, Risk.MEDIUM):
            "risk_blocked",
        (FailurePhase.POST_DISPATCH, Idempotency.NON_IDEMPOTENT, Risk.LOW):
            "no_retry",
        (FailurePhase.POST_DISPATCH, Idempotency.UNKNOWN, Risk.LOW):
            "no_retry",
    }
    for (phase, idem, risk), mode in cases.items():
        got = _plan(StepProbe(phase=phase, idempotency=idem, risk=risk))
        assert got["mode"] == mode, f"{phase}/{idem}/{risk} → {got}"
        assert got["assumed"] == (), "字段齐全时不该有任何保守替换"
    # 缺字段 → 保守替换后**照常走本体**（不新增分支）
    assert _plan(StepProbe(phase=None, idempotency=None))["mode"] == "no_retry"
    # 非幂等 + 有 postcondition → 改走 postcondition 分支（H7 的唯一出口）
    got = _plan(StepProbe(phase=FailurePhase.POST_DISPATCH,
                          idempotency=Idempotency.NON_IDEMPOTENT,
                          risk=Risk.HIGH, has_postcondition=True,
                          postcondition=object()))
    assert got["mode"] == "postcondition"


# --- ③ postcondition 三分支 ---------------------------------------------------


def test_postcondition_holding_means_the_original_action_succeeded():
    """设计 §8.4：「成立 → 不算复现失败」——记 `False`（**确定**跑不出来）。

    原始动作其实成功了，是别的原因让后续断言炸了——那没有「这个失败」可复现。
    """
    runner = _Runner(_fail())
    res = _reproduce(probe=_probe(
        phase=FailurePhase.POST_DISPATCH, idempotency=Idempotency.NON_IDEMPOTENT,
        has_postcondition=True, postcondition="spec"), checker=lambda _s: True)
    assert runner.calls == []
    assert res.reproducible is False and res.attempts == 0
    assert res.reproduction_count == 0 and res.evidence == ()
    assert any("不算复现失败" in n for n in res.notes)


def test_postcondition_not_satisfied_is_unconfirmable():
    """不成立 → 「无法确认复现」：`reproduction_count` 不加、证据不加分。"""
    res = _reproduce(probe=_probe(has_postcondition=True,
                                   postcondition="spec"),
                     checker=lambda _s: False)
    assert res.reproducible is None and res.reproduction_count == 0
    assert res.evidence == () and res.attempts == 0
    assert any("无法确认" in n for n in res.notes)


def test_postcondition_without_a_checker_is_unconfirmable():
    """无法判断（没注入 checker）→ 与「不成立」同样归「无法确认」。

    ⚠️ 两者**不得**都记成 `False`：`False` 是「跑不出来」的确定结论。
    """
    res = _reproduce(probe=_probe(has_postcondition=True,
                                   postcondition="spec"), checker=None)
    assert res.reproducible is None
    assert res.audit[0]["satisfied"] is None


def test_a_checker_that_blows_up_is_unconfirmable_not_failed():
    """checker 自身炸了 → 「无法判断」（观测故障不是判定结果，8.2 反模式）。"""
    def _boom(_spec):
        raise RuntimeError("wait engine died")

    res = _reproduce(probe=_probe(has_postcondition=True,
                                   postcondition="spec"), checker=_boom)
    assert res.reproducible is None


# --- ④ 预算与设备抖动（都不得伪装成「跑不出来」） ------------------------------


def test_an_exhausted_budget_stops_reruns_without_claiming_anything():
    """F4/F11：预算耗尽 → 停止重跑；**不声称** `reproducible=False`。"""
    runner = _Runner(_fail())
    res = _reproduce(_ctx(_step(effective_idempotency="IDEMPOTENT",
                                effective_risk="LOW")),
                     runner=runner, budget=LLMBudget(max_calls_per_run=0))
    assert runner.calls == [] and res.attempts == 0
    assert res.reproducible is None
    assert any("预算耗尽" in n for n in res.notes)


def test_the_budget_caps_the_number_of_reruns():
    """预算只放行 N 次 → 重跑到第 N 次为止（不绕过计数）。"""
    runner = _Runner(_fail(), _fail(), _fail())
    res = _reproduce(_ctx(_step(effective_idempotency="IDEMPOTENT",
                                effective_risk="LOW")),
                     runner=runner, budget=LLMBudget(max_calls_per_run=2))
    assert res.attempts == 2 and res.reproduction_count == 2
    assert len(runner.calls) == 2
    assert any("预算耗尽" in n for n in res.notes)
    assert [e.kind for e in res.evidence] == ["reproducible_3_times"], \
        "两次全复现仍是「每次都复现」——证据不该被预算配置清零"


def test_a_device_flake_is_not_a_reproduction_verdict():
    """重跑时设备抖动（异常）→ 该次**不作数**，继续下一次。

    抖动若被记成「重跑失败」，`reproduction_count` 会凭空多一次——复现次数
    是**结论**，不是尝试次数。
    """
    runner = _Runner(_fail(), raises=2)          # 前两次抖，第三次真失败
    res = _reproduce(_ctx(_step(effective_idempotency="IDEMPOTENT",
                                effective_risk="LOW")), runner=runner)
    assert res.attempts == 3 and res.reproduction_count == 1
    assert res.reproducible is True and res.evidence == ()
    assert sum(1 for a in res.audit if a.get("status") == "UNSTARTED") == 2


def test_all_attempts_flaking_is_unconfirmable():
    """全程抖动 → 一次都没跑成 → **无法确认**（不是「跑不出来」）。"""
    runner = _Runner(raises=5)
    res = _reproduce(_ctx(_step(effective_idempotency="IDEMPOTENT",
                                effective_risk="LOW")), runner=runner)
    assert res.attempts == 3 and res.reproducible is None
    assert any("一次都没跑成" in n for n in res.notes)


def test_an_unwired_tool_channel_is_unconfirmable_not_a_failed_reproduction():
    """**口径登记五**：`run_testcase` 现在 NOT_WIRED → 「通道不可用」。

    若把它当普通异常吞掉，一次都没跑成的调用会得到 `reproducible=False`
    ——「没接线」伪装成「跑不出来」。所以 `ToolError` 必须**上抛并中止**。
    """
    res = _reproduce(_ctx(_step(effective_idempotency="IDEMPOTENT",
                                effective_risk="LOW")))     # 无 runner
    assert res.reproducible is None and res.attempts == 0
    assert any("通道不可用" in n for n in res.notes)


def test_a_passing_rerun_means_not_reproducible():
    """幂等 + LOW，重跑通过 → `False`（跑不出来），计数 0、无证据。

    ⚠️ **不提前收工**：即便第 1 次就通过，也要跑满上界——否则
    「fail/pass/fail」这种 flaky 形态会被记成一次干净的复现。
    """
    runner = _Runner(_pass(), _pass(), _pass())
    res = _reproduce(_ctx(_step(effective_idempotency="IDEMPOTENT",
                                effective_risk="LOW")), runner=runner)
    assert res.reproducible is False and res.reproduction_count == 0
    assert res.attempts == 3 and len(runner.calls) == 3
    assert res.evidence == ()


def test_a_flaky_failure_is_not_evidence_of_reproducibility():
    """**flaky 不得领 `reproducible_3_times`**（证据类别的字面意思）。

    「3 次里失败 1 次」确实**复现过**（`reproducible=True`、`count=1`），
    但它不是「稳定复现」——领了那份 5 分证据，审计里「3 次」这个数字就是假的。
    这条同时钉住「不提前收工」：若第 1 次失败就收工，flaky 与稳定复现
    在结果上完全一样。
    """
    res = _reproduce(_ctx(_step(effective_idempotency="IDEMPOTENT",
                                effective_risk="LOW")),
                     runner=_Runner(_fail(), _pass(), _fail()))
    assert res.reproducible is True and res.reproduction_count == 2
    assert res.attempts == 3 and res.evidence == ()
    assert any("flaky" in n for n in res.notes)


def test_a_budget_capped_repro_reports_the_real_count_without_the_evidence():
    """预算把上界掐到 1 次 → 复现了，但**不领** 3 次证据（不足 2 个数据点）。"""
    res = _reproduce(_ctx(_step(effective_idempotency="IDEMPOTENT",
                                effective_risk="LOW")),
                     runner=_Runner(_fail()), budget=LLMBudget(max_calls_per_run=1))
    assert res.attempts == 1 and res.reproducible is True
    assert res.evidence == ()


def test_a_budget_capped_at_two_still_earns_the_evidence():
    """预算掐到 2 次、两次都复现 → 仍领证据（判据是「无一次通过」，不是「跑满 3」）。

    ⚠️ 若判据写成「attempts == MAX_REPRO_ATTEMPTS」，一个与证据无关的配置项
    会**静默清零最强的一档证据**——这条就是那个判据的守卫。
    """
    res = _reproduce(_ctx(_step(effective_idempotency="IDEMPOTENT",
                                effective_risk="LOW")),
                     runner=_Runner(_fail(), _fail()),
                     budget=LLMBudget(max_calls_per_run=2))
    assert res.attempts == 2 and res.reproducible is True
    assert [e.kind for e in res.evidence] == ["reproducible_3_times"]


# --- ⑤ 证据与状态读取 ---------------------------------------------------------


def test_the_reproduction_evidence_feeds_the_evidence_score():
    """证据类别必须落在 Task 5.3 的权重表里，且**真的加分**。

    `reproducible_3_times: 5` 是设计 §8.3 权重表里的第二高——复现是「这不是
    环境抖动」的最强证据之一，所以它必须能被 `score_evidence` 读到。
    """
    res = _reproduce(_ctx(_step(effective_idempotency="IDEMPOTENT",
                                effective_risk="LOW")),
                     runner=_Runner(_fail(), _fail(), _fail()))
    assert [e.kind for e in res.evidence] == ["reproducible_3_times"]
    assert score_evidence(list(res.evidence)) == 5
    assert "login_001" in res.evidence[0].source


def test_only_pass_counts_as_a_pass():
    """`_PASS_STATUSES` 只装 P1 词表里的「通过」——`RECOVERED` **不在**其中。

    `RECOVERED` 是「失败后恢复了」（8.1 步骤状态），不是「这一次跑通了」；
    把它当通过会让「恢复掩盖 flaky」的老问题在复现判定上重演（19 节）。
    """
    from diagnosis.reproducer import _PASS_STATUSES
    assert _PASS_STATUSES <= TESTCASE_STATUSES
    assert "RECOVERED" not in _PASS_STATUSES


def test_status_is_read_from_testcase_result_and_from_a_toolresult():
    """两种重跑产物都要能读出状态（`runner` 回调给前者，toolkit 给后者）。"""
    from diagnosis.reproducer import _status_of
    assert _status_of(_pass()) == "PASS"
    assert _status_of(_fail()) == "FAIL"
    tk_result = ToolResult(tool="run_testcase",
                           tier=TOOL_ASSET_TIER["run_testcase"], ok=True,
                           value={"status": "FAIL"})
    assert _status_of(tk_result) == "FAIL"
    inner = ToolResult(tool="run_testcase",
                       tier=TOOL_ASSET_TIER["run_testcase"], ok=True,
                       value=_pass())
    assert _status_of(inner) == "PASS"


# --- ⑥ trace 读取：三列（口径登记四） -----------------------------------------


def test_read_trace_steps_carries_phase_idempotency_and_risk(tmp_path):
    """`read_trace_steps` 必须带出复现判定的三列。

    ⚠️ 这三列 P1 起就在 `steps` 表里、`Lifecycle.record_step` 一直在写，
    但读取端早先没 select——**不补这一条，`reproduce` 对任何真 trace 都落进
    「缺字段 → 按 PRE_DISPATCH 保守处理」**，POST_DISPATCH 的两条分支在真实
    数据上永不可达（单测的 StepProbe 会掩盖这件事）。
    """
    db = tmp_path / "trace.db"
    store = TraceStore(db)
    store.start_run("run_1", suite="smoke", app_bundle_id=APP, app_build=BUILD)
    tc = store.start_testcase("run_1", "login_001", attempt=1)
    store.record_step(tc, 0, "tap", target_id="pay_button", status="FAILED",
                      failure_type="ACTION_OUTCOME_UNKNOWN",
                      effective_idempotency="NON_IDEMPOTENT",
                      effective_risk="HIGH", failure_phase="POST_DISPATCH")
    store.end_testcase(tc, "FAIL")
    store.end_run("run_1", status="FAIL", exit_code=1)

    steps, _, _ = read_trace_steps(db)
    assert len(steps) == 1
    got = steps[0]
    assert got.failure_phase == "POST_DISPATCH"
    assert got.effective_idempotency == "NON_IDEMPOTENT"
    assert got.effective_risk == "HIGH"
    # 端到端：真 trace 的这一步必须落进 H7 的「绝不重跑」格
    probe = _probe_from(got)
    assert probe.phase is FailurePhase.POST_DISPATCH
    assert probe.idempotency is Idempotency.NON_IDEMPOTENT
    assert probe.risk is Risk.HIGH


def _probe_from(step):
    from diagnosis.reproducer import _probe_from_step
    return _probe_from_step(step)


def test_an_unrecognised_enum_value_reads_as_missing_not_as_unknown():
    """认不出的列值 → `None`（缺字段），**不是** `Idempotency.UNKNOWN`。

    `UNKNOWN` 是一个**声明**（「用例说不知道」），与「我们读到了一个不认识
    的字符串」（列被写坏了）是两件事。两者都保守，但混同会污染审计。
    """
    from diagnosis.reproducer import _probe_from_step
    probe = _probe_from_step(_step(effective_idempotency="BOGUS",
                                   failure_phase="BOGUS"))
    assert probe.idempotency is None and probe.phase is None
    # 而真 UNKNOWN 是**声明**，必须原样带出来（走 retry_decision 的 NON 分支）
    assert _probe_from_step(
        _step(effective_idempotency="UNKNOWN")).idempotency \
        is Idempotency.UNKNOWN


# --- ⑦ F3：不直连数据源 -------------------------------------------------------


def test_the_reproducer_does_not_touch_data_sources_directly():
    """F3 的 M5 格（本文件自己扫一遍，指名 `reproducer.py`）。

    `tests/unit/test_diagnosis_models.py` 有整包的守卫，但它只断言「扫到
    ≥3 个文件」——本文件的钉子保证**这一个**文件确实在扫描面里，且它的
    import 面在加入 `graph/models.py` 的三列读取后**没有**把 `graph.storage`
    拉进来（读 trace 归 `KnowledgeSources`，归诊断侧不碰）。
    """
    path = Path(__file__).resolve().parents[2] / "diagnosis" / "reproducer.py"
    forbidden = ("sqlite3", "tracer", "graph.storage", "experience.store",
                 "source.sqlite_tx", "agents.storage")
    bad: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        mods: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
        elif isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
        bad += [m for m in mods
                if any(m == f or m.startswith(f + ".") for f in forbidden)]
    assert not bad, f"diagnosis/reproducer.py 直连数据源（F3）：{sorted(bad)}"
