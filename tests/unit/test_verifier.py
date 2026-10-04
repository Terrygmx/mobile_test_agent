"""Task 3.1 / P2-06：ExperienceVerifier 纯函数状态机（设计 4.3–4.6 / E4 / E6 / E8 / E11 / E13）。

plan step 1 的失败测试清单逐条对应：
  - E4 资格（非幂等 / 风险 ≥ MEDIUM → 样本再漂亮也 KEEP_CANDIDATE，矩阵 #8/#9）；
  - 升级门槛 4.5（min_samples=10 / success_rate ≥ 0.95 / distinct_runs ≥ 3，
    同 run 多命中只计 1 个 run）；
  - 滑动窗口 4.6（最近 5 次失败 ≥ 2 → DEGRADE，**优先于**总体 success_rate，矩阵 #5）；
  - 判定只吃「实际被尝试」样本（E11）；
  - fingerprint 变化 → REVALIDATION_REQUIRED，不删除不拒绝（E8，矩阵 #7），
    重验证通过后更新观测记录；
  - 每次转换落 `experience_state_events`。

E13：本文件全部离设备、离网络、离系统时钟（SQLite 临时库只用于「应用」区的落库断言）。
"""
from __future__ import annotations

import pytest

from experience.models import (
    CandidateSeed,
    Experience,
    ExperienceRun,
    ExperienceStatus,
    VerificationDecision,
    VerificationPolicy,
)
from experience.store import SQLiteExperienceStore
from experience.verifier import (
    REVALIDATION_REQUIRED,
    apply_outcome,
    distinct_run_count,
    eligible_for_auto_verification,
    evaluate,
    mark_revalidation_required,
    needs_revalidation,
    revalidate,
    sliding_window_degrade,
    success_rate_of,
)
from executor.policy import Idempotency
from repository.loader import LocatorStrategy
from testcase.schema import Risk

POLICY = VerificationPolicy()


def _run(i: int, result: str = "SUCCESS", run_id: str | None = None,
         fingerprint: str | None = None) -> ExperienceRun:
    return ExperienceRun(experience_id="exp_1", run_id=run_id or f"run_{i}",
                         step_id=100 + i, app_build="1026", result=result,
                         screen_fingerprint=fingerprint)


def _exp(runs: list[ExperienceRun], *,
         status: ExperienceStatus = ExperienceStatus.CANDIDATE,
         fingerprint: str | None = None, **kw) -> Experience:
    """由 runs 反推一致的统计列（模型 validator 会校验三者一致）。

    `evaluate` 要求 `len(runs) == sample_count`，故 fixture 必须与历史同源——
    手工编数字迟早造出「模型接受但 evaluate 拒绝」的假样本。
    """
    n = len(runs)
    succ = sum(1 for r in runs if r.result == "SUCCESS")
    return Experience(
        experience_id="exp_1", app_id="com.phaset0.logindemo",
        screen_id="HomeView", target_id="login_button",
        strategy=LocatorStrategy(type="accessibility_id",
                                 value="signin_button",
                                 origin="experience"),
        origin="LLM_ACCEPTED_RECOVERY", status=status,
        sample_count=n, success_count=succ, failure_count=n - succ,
        success_rate=(succ / n if n else 0.0),
        last_screen_fingerprint=fingerprint,
        seed_run_id="run_seed", seed_step_id=1, seed_recovery_review_id=1,
        **kw)


def _element(*, idempotency=Idempotency.IDEMPOTENT, risk=Risk.LOW):
    from types import SimpleNamespace
    return SimpleNamespace(idempotency=idempotency, risk=risk)


# --- 4.5 升级门槛 -----------------------------------------------------------


def test_threshold_met_promotes():
    """10 样本 / 100% / 3 个不同 run → PROMOTE_TO_VERIFIED。"""
    runs = [_run(i) for i in range(10)]
    out = evaluate(_exp(runs), runs, POLICY, auto_verify_eligible=True)

    assert out.decision is VerificationDecision.PROMOTE_TO_VERIFIED
    assert out.reason == "THRESHOLD_MET"
    assert out.changes_status is True
    assert out.detail["sample_count"] == 10
    assert out.detail["distinct_runs"] == 10


def test_exactly_at_threshold_promotes():
    """边界取闭区间：20 样本 / 恰好 0.95 / 恰好 3 个 run → 升级。

    门槛是「≥」，把边界写成「>」会让一条刚好达标的经验永远卡在 CANDIDATE，
    而且没人能从报告里看出为什么。
    """
    results = ["FAILURE"] + ["SUCCESS"] * 19            # 19/20 = 0.95
    run_ids = ["run_a"] * 3 + ["run_b"] * 8 + ["run_c"] * 9
    runs = [_run(i, result=results[i], run_id=run_ids[i])
            for i in range(20)]
    exp = _exp(runs)
    assert distinct_run_count(runs) == 3
    assert success_rate_of(runs) == pytest.approx(0.95)

    out = evaluate(exp, runs, POLICY, auto_verify_eligible=True)
    assert out.decision is VerificationDecision.PROMOTE_TO_VERIFIED


def test_too_few_samples_keeps_candidate():
    runs = [_run(i) for i in range(9)]
    out = evaluate(_exp(runs), runs, POLICY, auto_verify_eligible=True)

    assert out.decision is VerificationDecision.KEEP_CANDIDATE
    assert out.reason == "THRESHOLD_NOT_MET"
    assert out.changes_status is False


def test_success_rate_below_threshold_keeps_candidate():
    runs = [_run(i, result=("FAILURE" if i < 2 else "SUCCESS"))
            for i in range(10)]     # 0.8
    out = evaluate(_exp(runs), runs, POLICY, auto_verify_eligible=True)
    assert out.decision is VerificationDecision.KEEP_CANDIDATE


def test_same_run_multiple_hits_counts_as_one_run():
    """4.5：同一个 run 内多次命中只算一个 run——否则多样性被虚增。

    10 个样本全在一个 run 里 → distinct_runs=1 < 3 → 不升级（哪怕 100% 成功）。
    这挡的正是「一个 testcase 反复点同一个按钮」把门槛刷过去。
    """
    runs = [_run(i, run_id="run_single") for i in range(10)]
    out = evaluate(_exp(runs), runs, POLICY, auto_verify_eligible=True)

    assert out.detail["distinct_runs"] == 1
    assert out.decision is VerificationDecision.KEEP_CANDIDATE
    assert out.reason == "THRESHOLD_NOT_MET"


def test_truncated_history_fails_loud():
    """传了截断的 runs → ValueError，不静默按 5 个样本判。

    静默失真的方向是「样本看起来更少 → 不升级」，而报告上完全看不出原因。
    """
    runs = [_run(i) for i in range(10)]
    with pytest.raises(ValueError, match="截断历史"):
        evaluate(_exp(runs), runs[:5], POLICY, auto_verify_eligible=True)


# --- 4.4 / E4 资格（矩阵 #8 / #9） -----------------------------------------


@pytest.mark.parametrize("element,expected", [
    (_element(), True),
    (_element(idempotency=Idempotency.NON_IDEMPOTENT), False),
    (_element(idempotency=Idempotency.UNKNOWN), False),
    (_element(idempotency=None), False),
    (_element(risk=Risk.MEDIUM), False),
    (_element(risk=Risk.HIGH), False),
    (_element(risk=Risk.CRITICAL), False),
    (_element(risk=None), False),
])
def test_eligible_for_auto_verification_matrix(element, expected):
    """4.4：只有 `IDEMPOTENT AND risk == LOW` 才有自动升级资格。

    `None`（未登记 / metadata 未声明）一律不合格——E4 是安全约束，「不知道」
    必须与「不满足」同侧（fail-closed）。
    """
    assert eligible_for_auto_verification(element) is expected


@pytest.mark.parametrize("element", [
    _element(idempotency=Idempotency.NON_IDEMPOTENT),
    _element(risk=Risk.MEDIUM),
])
def test_ineligible_never_promotes_however_good_the_samples(element):
    """矩阵 #8/#9：非幂等 10/10、风险 MEDIUM 100% → 仍 KEEP_CANDIDATE。"""
    runs = [_run(i) for i in range(10)]
    out = evaluate(_exp(runs), runs, POLICY,
                   auto_verify_eligible=eligible_for_auto_verification(
                       element))

    assert out.decision is VerificationDecision.KEEP_CANDIDATE
    assert out.reason == "NOT_ELIGIBLE", \
        "reason 要与「门槛没到」区分——前者永远等不到，后者只差样本"
    assert out.detail["eligible"] is False
    assert out.changes_status is False


def test_auto_verify_eligible_is_required_keyword():
    """资格是**必填**的：忘了传是 TypeError，不是「默默不升级」。

    设计 §4.3 的伪代码是 `evaluate(exp, runs, policy)`，但 E4 的资格是**目标
    元素**的属性，三者里都没有它（`ExperienceRun.effective_risk` 恒为 LOW：
    4.7 表规定风险未过的尝试不写样本）。做成可选默认 False 会让「忘了传」与
    「确实不合格」不可区分。
    """
    runs = [_run(i) for i in range(10)]
    with pytest.raises(TypeError):
        evaluate(_exp(runs), runs, POLICY)      # type: ignore[call-arg]


# --- 4.6 / E6 滑动窗口降级（矩阵 #5） --------------------------------------


def test_sliding_window_beats_overall_success_rate():
    """矩阵 #5：总体 97% 但最近 5 次里 3 次失败 → 立即 DEGRADED。

    设计原文举「100 次 98 成功、最近 5 次 3 失败」——3 次失败意味着总体最多
    97%，原文的 98% 是示意。这里用自洽的 97%（仍 ≥ 门槛 0.95），所以「窗口
    优先于总体成功率」是真的被考到了：只看总体它会一直 VERIFIED。
    """
    runs = ([_run(i) for i in range(97)]
            + [_run(97 + i, result="FAILURE") for i in range(3)])
    exp = _exp(runs, status=ExperienceStatus.VERIFIED)
    assert success_rate_of(runs) == pytest.approx(0.97)

    out = evaluate(exp, runs, POLICY, auto_verify_eligible=True)

    assert out.decision is VerificationDecision.DEGRADE
    assert out.reason == "SLIDING_WINDOW"
    assert out.detail["window_failures"] == 3
    assert out.detail["success_rate"] == pytest.approx(0.97), \
        "判据明细里要同时看得到总体率与窗口失败数——否则没法解释这次降级"


def test_verified_healthy_is_no_change():
    runs = [_run(i) for i in range(20)]
    out = evaluate(_exp(runs, status=ExperienceStatus.VERIFIED), runs, POLICY,
                   auto_verify_eligible=True)
    assert out.decision is VerificationDecision.NO_CHANGE
    assert out.reason == "HEALTHY"


def test_sliding_window_boundary_and_window_size():
    """窗口边界：恰好 2 次失败触发；1 次不触发；只数尾部 window 条。"""
    one_fail = [_run(i) for i in range(4)] + [_run(4, result="FAILURE")]
    assert sliding_window_degrade(one_fail, window=5, max_failures=2) is False

    two_fail = [_run(i) for i in range(3)] + [
        _run(3, result="FAILURE"), _run(4, result="FAILURE")]
    assert sliding_window_degrade(two_fail, window=5, max_failures=2) is True

    # 早期失败已滑出窗口 → 不算
    slid_out = [_run(0, result="FAILURE"), _run(1, result="FAILURE")] + [
        _run(i) for i in range(2, 8)]
    assert sliding_window_degrade(slid_out, window=5, max_failures=2) is False


def test_sliding_window_rejects_nonpositive_window():
    with pytest.raises(ValueError, match="window must be positive"):
        sliding_window_degrade([_run(0)], window=0)


def test_degraded_has_no_automatic_exit():
    """4.3：DEGRADED 只能走**显式** revalidate——窗口恢复不自动转回。

    自动转回会让降级变成抖动（E6 的滑动窗口是安全阀，不是健康探针）。
    """
    runs = [_run(i) for i in range(20)]
    out = evaluate(_exp(runs, status=ExperienceStatus.DEGRADED), runs, POLICY,
                   auto_verify_eligible=True)
    assert out.decision is VerificationDecision.NO_CHANGE
    assert out.reason == "AWAITING_REVALIDATION"


def test_rejected_is_terminal():
    """4.3：REJECTED 是终态，状态机不得复活它（要复活只能人工重新 ACCEPT）。"""
    runs = [_run(i) for i in range(20)]
    out = evaluate(_exp(runs, status=ExperienceStatus.REJECTED), runs, POLICY,
                   auto_verify_eligible=True)
    assert out.decision is VerificationDecision.NO_CHANGE
    assert out.reason == "TERMINAL"


# --- E11：判定只吃「实际被尝试」的样本 --------------------------------------


def test_decision_is_computed_from_runs_not_denormalized_columns():
    """判定以传入历史为唯一依据；空历史 → 0.0（不是 1.0）。

    E11：`experience_runs` 里只会有「实际被尝试」的样本（4.7 表：Screen 不
    匹配 / 风险拦截**不写**），所以从 runs 现算就等于只统计被尝试的样本。
    """
    assert success_rate_of([]) == 0.0
    assert distinct_run_count([]) == 0
    out = evaluate(_exp([]), [], POLICY, auto_verify_eligible=True)
    assert out.decision is VerificationDecision.KEEP_CANDIDATE
    assert out.detail["success_rate"] == 0.0


# --- E8：fingerprint 变化 ---------------------------------------------------


def test_needs_revalidation_semantics():
    exp = _exp([], fingerprint="aaa")
    assert needs_revalidation(exp, "bbb") is True
    assert needs_revalidation(exp, "aaa") is False
    assert needs_revalidation(exp, None) is False, "页面不可解析 ≠ 变了"
    assert needs_revalidation(_exp([]), "bbb") is False, "首次命中无从比较"


def test_mark_revalidation_required_does_not_touch_status(store):
    """E8（矩阵 #7）：标记 `REVALIDATION_REQUIRED`，**不删除、不拒绝**。

    标记落状态时间线，但 from==to——它不是跳变，只是「此刻仍是这个状态，
    且被标记了」。
    """
    exp = _seed_store(store)
    assert mark_revalidation_required(store, exp, run_id="run_x") is True

    rows = _state_events(store, exp.experience_id)
    # 建种子时已有一条 SEEDED 跳变（None→CANDIDATE），故取最后一条断言
    assert [(r["from_status"], r["to_status"], r["reason"])
            for r in rows[-1:]] == [
        ("CANDIDATE", "CANDIDATE", REVALIDATION_REQUIRED)]
    assert store.lookup(exp.app_id, exp.screen_id, exp.target_id)[0].status \
        is ExperienceStatus.CANDIDATE, "状态不变（不拒绝）"
    assert store.get_runs(exp.experience_id) is not None, "证据不删除"


def test_mark_revalidation_required_is_idempotent_and_remarkable(store):
    """同一状态内重复标记只写一次；状态跳变之后可以重新标记。"""
    exp = _seed_store(store)
    assert mark_revalidation_required(store, exp) is True
    assert mark_revalidation_required(store, exp) is False, "重复标记不再写行"

    store.update_status(exp.experience_id, ExperienceStatus.VERIFIED,
                        "THRESHOLD_MET")
    assert mark_revalidation_required(store, exp) is True, \
        "跳变后旧标记作废——新指纹变化必须能重新标记"


def test_revalidate_promotes_and_records_new_fingerprint(store):
    """E8：重验证通过 → VERIFIED(reason=REVALIDATED) + 更新 fingerprint 观测。"""
    exp = _seed_store(store)
    store.update_status(exp.experience_id, ExperienceStatus.VERIFIED, "M")
    store.update_status(exp.experience_id, ExperienceStatus.DEGRADED, "W")

    revalidate(store, exp, fingerprint="new_fp", run_id="run_rv",
               operator="alice")

    after = store.lookup(exp.app_id, exp.screen_id, exp.target_id)[0]
    assert after.status is ExperienceStatus.VERIFIED
    assert after.last_screen_fingerprint == "new_fp"
    rows = _state_events(store, exp.experience_id)
    assert (rows[-1]["from_status"], rows[-1]["to_status"],
            rows[-1]["reason"], rows[-1]["operator"]) == (
        "DEGRADED", "VERIFIED", "REVALIDATED", "alice")


# --- 应用区：决策 → 落库 ----------------------------------------------------


def test_apply_outcome_writes_transition_event(store):
    exp = _seed_store(store)
    runs = [_run(i) for i in range(10)]
    out = evaluate(_with_runs(exp, runs), runs, POLICY,
                   auto_verify_eligible=True)
    assert out.changes_status is True

    assert apply_outcome(store, exp, out, run_id="run_10",
                         app_build="1026", operator="verifier") is True
    after = store.lookup(exp.app_id, exp.screen_id, exp.target_id)[0]
    assert after.status is ExperienceStatus.VERIFIED
    rows = _state_events(store, exp.experience_id)
    assert [(r["from_status"], r["to_status"], r["reason"], r["run_id"]) for r
            in rows[-1:]] == [
        ("CANDIDATE", "VERIFIED", "THRESHOLD_MET", "run_10")]


def test_apply_outcome_skips_non_transitions(store):
    """KEEP_CANDIDATE / NO_CHANGE 不写任何东西（同状态事件只是噪声）。"""
    exp = _seed_store(store)
    runs = [_run(i) for i in range(3)]
    out = evaluate(_with_runs(exp, runs), runs, POLICY,
                   auto_verify_eligible=True)
    assert out.decision is VerificationDecision.KEEP_CANDIDATE

    before = _state_events(store, exp.experience_id)
    assert apply_outcome(store, exp, out) is False
    assert _state_events(store, exp.experience_id) == before


def test_apply_outcome_reject_uses_outcome_reason(store):
    from experience.verifier import VerificationOutcome
    exp = _seed_store(store)
    out = VerificationOutcome(VerificationDecision.REJECT, "STALE", {})
    assert apply_outcome(store, exp, out) is True
    rows = _state_events(store, exp.experience_id)
    assert (rows[-1]["to_status"], rows[-1]["reason"]) == ("REJECTED", "STALE")


# --- fixture ----------------------------------------------------------------


def _with_runs(exp: Experience, runs: list[ExperienceRun]) -> Experience:
    """把已落库的 Experience 的统计列对齐到给定历史（`evaluate` 要求两者同源）。

    `apply_outcome` 按 `exp.experience_id` 落库，所以这里的 exp 必须指向真实
    行；`model_copy(update=)` 不重跑 validator，但下面算出的值本身就是自洽的。
    """
    n = len(runs)
    succ = sum(1 for r in runs if r.result == "SUCCESS")
    return exp.model_copy(update={
        "sample_count": n, "success_count": succ, "failure_count": n - succ,
        "success_rate": (succ / n if n else 0.0)})


def _seed_store(store) -> Experience:
    return store.create_candidate(CandidateSeed(
        review_id=7, recovery_id=1, seed_run_id="run_seed", seed_step_id=12,
        seed_recovery_review_id=7, app_id="com.phaset0.logindemo",
        screen_id="HomeView", target_id="login_button",
        strategy=LocatorStrategy(type="accessibility_id",
                                 value="signin_button",
                                 origin="experience")))


def _state_events(store, experience_id: str) -> list[dict]:
    conn = store._connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT from_status, to_status, reason, run_id, app_build,"
            " operator FROM experience_state_events WHERE experience_id=?"
            " ORDER BY id", (experience_id,)).fetchall()]
    finally:
        conn.close()


@pytest.fixture()
def store(tmp_path):
    return SQLiteExperienceStore(tmp_path / "exp.db")
