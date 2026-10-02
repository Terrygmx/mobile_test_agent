"""Task 4.1：Recovery 决策表纯函数（9.2 前半 / 7.4 重试规则表 / H6 / H7）。

H18：全部离设备——RecoveryEngine 的设备交互经 ctx 注入 callable，
这里用内存 stub 验证确定性半边的每个分支。
"""
from __future__ import annotations

import pytest

from agent.context import RecoveryContext, RecoveryResult
from agent.policy import (
    RecoveryAction,
    RecoveryConfig,
    admitted_actions,
)
from agent.recovery import RecoveryEngine, RunMemo
from agent.risk import candidate_risk_allowed
from executor.policy import FailurePhase, Idempotency
from testcase.schema import Risk

PRE = FailurePhase.PRE_DISPATCH
POST = FailurePhase.POST_DISPATCH


# --- 决策表：准入判定（9.2 前半） ---


def test_wait_timeout_not_admitted_by_default():
    """9.2：Wait Timeout 默认不进 Recovery（recovery.on_wait_timeout=false）。"""
    assert admitted_actions("WAIT_TIMEOUT", None, Idempotency.IDEMPOTENT,
                            False, RecoveryConfig()) is None


def test_wait_timeout_admitted_when_config_enabled():
    cfg = RecoveryConfig(on_wait_timeout=True)
    actions = admitted_actions("WAIT_TIMEOUT", PRE, Idempotency.IDEMPOTENT,
                               False, cfg)
    assert actions is not None and RecoveryAction.LLM_CANDIDATE in actions


def test_assertion_value_mismatch_never_admitted_h6():
    """H6：断言值失败不进 Recovery——期望值错了重试也是错。"""
    for cfg in (RecoveryConfig(), RecoveryConfig(on_wait_timeout=True)):
        assert admitted_actions("ASSERTION_VALUE_MISMATCH", None,
                                Idempotency.IDEMPOTENT, False, cfg) is None


def test_security_blocked_not_admitted():
    """Guard 判定确定性：恢复必然再拦（10.1 不可绕过）。"""
    assert admitted_actions("SECURITY_BLOCKED", None, Idempotency.IDEMPOTENT,
                            False, None) is None


def test_pre_dispatch_allows_settle_and_reconcile():
    """7.4：PRE_DISPATCH 任意幂等性都允许（settle / 恢复，仍受风险门控）。"""
    for idem in (Idempotency.IDEMPOTENT, Idempotency.NON_IDEMPOTENT,
                 Idempotency.UNKNOWN):
        actions = admitted_actions("ELEMENT_NOT_FOUND", PRE, idem, False)
        assert actions is not None
        assert RecoveryAction.SETTLE_RETRY in actions
        assert RecoveryAction.LOCAL_RECONCILE in actions


def test_post_dispatch_idempotent_allows_bounded_redispatch():
    """7.4：POST_DISPATCH × IDEMPOTENT → 允许有界重试。"""
    actions = admitted_actions("ACTION_OUTCOME_UNKNOWN", POST,
                               Idempotency.IDEMPOTENT, False)
    assert RecoveryAction.SETTLE_RETRY in actions
    assert RecoveryAction.POSTCONDITION_CHECK in actions


def test_post_dispatch_non_idempotent_only_postcondition_h7():
    """H7：POST_DISPATCH + 非幂等 → 只允许 postcondition 检查，绝不重发。"""
    for idem in (Idempotency.NON_IDEMPOTENT, Idempotency.UNKNOWN):
        actions = admitted_actions("ACTION_OUTCOME_UNKNOWN", POST, idem, True)
        assert actions == (RecoveryAction.POSTCONDITION_CHECK,)


def test_post_dispatch_non_idempotent_no_postcondition_not_admitted():
    """无 postcondition 可查 → 不进恢复 → FAIL(ACTION_OUTCOME_UNKNOWN)。"""
    assert admitted_actions("ACTION_OUTCOME_UNKNOWN", POST,
                            Idempotency.NON_IDEMPOTENT, False) is None


def test_post_dispatch_missing_idempotency_treated_fail_closed():
    """POST_DISPATCH 且无幂等信息 → 按 UNKNOWN（=非幂等）处理（7.4-3）：
    证明不了幂等就不许重发。"""
    actions = admitted_actions("ACTION_OUTCOME_UNKNOWN", POST, None, True)
    assert actions == (RecoveryAction.POSTCONDITION_CHECK,)


def test_settle_bound_default_is_one():
    """9.2：Settle 重试有界，默认 1 次。"""
    assert RecoveryConfig().settle_max_attempts == 1


# --- RecoveryEngine：确定性半边（ctx 注入，无设备） ---


def _ctx(**kw) -> RecoveryContext:
    defaults = dict(
        failure_type="ELEMENT_NOT_FOUND", phase=PRE,
        element_id="go_profile", screen_id="HomeView", strategies=(),
        effective_idempotency=Idempotency.IDEMPOTENT, app_build="local")
    defaults.update(kw)
    return RecoveryContext(**defaults)


def test_engine_settle_retry_recovers():
    """settle：re-find 命中 → 重发原动作 → RECOVERED(kind=settle_retry)。"""
    calls: list[str] = []
    ctx = _ctx(refind=lambda: calls.append("find") or object(),
               redispatch=lambda el: calls.append("dispatch"))
    r = RecoveryEngine(sleep=lambda s: None).recover(ctx)
    assert r.recovered and r.kind == "settle_retry"
    assert calls == ["find", "dispatch"]


def test_engine_settle_bounded_not_infinite():
    """settle 有界：re-find 恒失败 → 恰好 config 次尝试后进下一阶段。"""
    attempts: list[int] = []

    def _boom():
        attempts.append(1)
        raise LookupError("still missing")

    ctx = _ctx(refind=_boom, redispatch=lambda el: None)
    r = RecoveryEngine(sleep=lambda s: None).recover(ctx)
    assert not r.recovered
    assert len(attempts) == RecoveryConfig().settle_max_attempts


def test_engine_postcondition_satisfied_recovers_without_redispatch():
    """矩阵 #18：POST_DISPATCH 超时 + 非幂等 + postcondition 成立 →
    RECOVERED(kind=postcondition)，**未再次点击**。"""
    dispatched: list[str] = []
    ctx = _ctx(failure_type="ACTION_OUTCOME_UNKNOWN", phase=POST,
               element_id="pay_button",
               effective_idempotency=Idempotency.NON_IDEMPOTENT,
               has_postcondition=True,
               refind=lambda: dispatched.append("refind") or object(),
               redispatch=lambda el: dispatched.append("redispatch"),
               postcondition_check=lambda: True)
    r = RecoveryEngine(sleep=lambda s: None).recover(ctx)
    assert r.recovered and r.kind == "postcondition"
    assert dispatched == [], "非幂等动作绝不重发（H7）"


def test_engine_postcondition_unsatisfied_fails_action_outcome_unknown():
    """矩阵 #19：postcondition 不成立 → FAIL(ACTION_OUTCOME_UNKNOWN)。"""
    ctx = _ctx(failure_type="ACTION_OUTCOME_UNKNOWN", phase=POST,
               effective_idempotency=Idempotency.NON_IDEMPOTENT,
               has_postcondition=True,
               postcondition_check=lambda: False)
    r = RecoveryEngine(sleep=lambda s: None).recover(ctx)
    assert not r.recovered
    assert r.failure_type == "ACTION_OUTCOME_UNKNOWN"


def test_engine_not_admitted_passes_through_original_failure():
    """不进恢复的失败（如 WAIT_TIMEOUT 默认）→ 维持原症状 + 原因可见。"""
    ctx = _ctx(failure_type="WAIT_TIMEOUT", phase=None,
               effective_idempotency=Idempotency.IDEMPOTENT,
               refind=lambda: object(), redispatch=lambda el: None)
    r = RecoveryEngine(sleep=lambda s: None).recover(ctx)
    assert not r.recovered
    assert r.failure_type == "WAIT_TIMEOUT"
    assert r.detail["recovery"] == "not_admitted"


def test_engine_experience_store_consulted_after_reconcile_before_llm():
    """20 节预留位：ExperienceStore 在 reconciliation 后、LLM 前被查询，
    P1 恒 []，stage 顺序 settle → experience → llm(disabled)。"""

    class SpyStore:
        def __init__(self):
            self.calls: list[tuple] = []

        def lookup(self, app_build, screen, target_id):
            self.calls.append((app_build, screen, target_id))
            return []

    spy = SpyStore()
    ctx = _ctx(refind=lambda: (_ for _ in ()).throw(LookupError("gone")),
               redispatch=lambda el: None)
    r = RecoveryEngine(repo=None, experience_store=spy,
                       sleep=lambda s: None).recover(ctx)
    assert not r.recovered
    assert spy.calls == [("local", "HomeView", "go_profile")]
    stages = [s["stage"] for s in r.detail["stages"]]
    assert stages == ["settle", "experience", "llm"], \
        "ExperienceStore 必须在 reconciliation 后、LLM 前（20 节）"


def test_engine_experience_position_after_reconciliation_with_repo():
    """review P3-4：repo=None 的用例只证明了 experience 在 settle 后、llm
    前——「reconciliation 之后」这个位置约束要用带 fake repo 的用例闭环：
    stages 顺序必须含 screen → reconcile，且 experience 在 reconcile 之后。"""
    from types import SimpleNamespace

    repo = SimpleNamespace(
        generated_screens={
            "HomeView": SimpleNamespace(marker="screen.HomeView")},
        override_screens={},
        screen_kind_hint=lambda s: "page")
    # go_profile 在源码 metadata 里（literal）但运行时不在 → DRIFT
    page = ("<App><Node name='screen.HomeView'/>"
            "<Node name='signin_button'/></App>")
    source_metadata = {
        "elements": [{"accessibilityId": "go_profile",
                      "resolution_type": "literal", "screen": "HomeView"}],
        "screens": ["HomeView"]}
    ctx = _ctx(
        refind=lambda: (_ for _ in ()).throw(LookupError("drifted")),
        page_source=lambda: page,
        source_metadata=source_metadata)
    r = RecoveryEngine(repo=repo, sleep=lambda s: None).recover(ctx)
    stages = [st["stage"] for st in r.detail["stages"]]
    assert stages == ["settle", "screen", "reconcile", "experience", "llm"], \
        f"ExperienceStore 必须在 reconciliation 之后（20 节）: {stages}"
    recon = next(st for st in r.detail["stages"] if st["stage"] == "reconcile")
    assert recon["outcome"] == "DRIFT"


def test_engine_run_memo_consumes_saved_strategy():
    """9.4：同 run 内 (screen, target, build) 复用已校验恢复 →
    RECOVERED(kind=run_memo)，且**按 memo 保存的恢复策略重找**（不是原始
    strategies——漂移下按原策略找必然再失败，review P3-1）；新引擎（新
    memo）不可见——不跨 run。"""
    memo = RunMemo()
    memo_strategy = {"type": "accessibility_id", "value": "go_profile_v2"}
    memo.save("HomeView", "go_profile", "local", memo_strategy)
    engine = RecoveryEngine(sleep=lambda s: None, run_memo=memo)
    finds: list = []

    def find_with(strategies):
        finds.append(list(strategies))
        return object()

    ctx = _ctx(
        failure_type="ELEMENT_NOT_FOUND", phase=PRE,
        refind=lambda: (_ for _ in ()).throw(LookupError("drifted")),
        find_with=find_with, redispatch=lambda el: None)
    r = engine.recover(ctx)
    assert r.recovered and r.kind == "run_memo"
    # refind（settle）按原始策略失败；memo 消费走 find_with 且收到 memo 策略
    assert finds == [[memo_strategy]], \
        f"memo 恢复策略必须被消费: {finds}"
    assert r.detail["memo_strategy"] == memo_strategy

    fresh = RecoveryEngine(sleep=lambda s: None)
    r2 = fresh.recover(_ctx(
        refind=lambda: (_ for _ in ()).throw(LookupError("drifted")),
        find_with=find_with, redispatch=lambda el: None))
    assert not r2.recovered or r2.kind != "run_memo"


# --- 风险门控（9.3 第 4 行确定性部分） ---


def test_candidate_risk_only_low_allowed():
    assert candidate_risk_allowed(Risk.LOW) is True
    assert candidate_risk_allowed(Risk.MEDIUM) is False
    assert candidate_risk_allowed(Risk.HIGH) is False
    assert candidate_risk_allowed(Risk.CRITICAL) is False
    # 风险未知（候选无 metadata）不放行——fail-closed
    assert candidate_risk_allowed(None) is False


# --- 结果模型 ---


def test_recovery_result_defaults():
    r = RecoveryResult(recovered=False, failure_type="ELEMENT_NOT_FOUND")
    assert r.kind is None and r.detail == {}
