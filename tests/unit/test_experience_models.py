"""Task 1.2 / P2-02：Experience 领域模型测试（设计 4.2 / E5 / E7 / 9.4）。

plan step 1 失败测试清单：
  - CandidateSeed 缺 seed 三件套任一 → 拒绝创建（E5）；
  - Experience 默认统计零值、validated_builds=[]；
  - 状态枚举恰四值；
  - promoted 与 status 独立（9.4 两条时间线）。

H18：全部离设备、离网络。
"""
from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from experience.models import (
    CandidateSeed,
    Experience,
    ExperienceRun,
    ExperienceStatus,
    StateEvent,
    VerificationPolicy,
    VerificationDecision,
)
from repository.loader import LocatorStrategy


def _strategy(**kw) -> LocatorStrategy:
    d = {"type": "accessibility_id", "value": "username_field_v2",
         "origin": "manual"}
    d.update(kw)
    return LocatorStrategy(**d)


def _seed(**kw) -> CandidateSeed:
    d = dict(
        review_id=7, recovery_id=1,
        seed_run_id="run_a", seed_step_id=12, seed_recovery_review_id=7,
        app_id="com.phaset0.logindemo", screen_id="LoginView",
        target_id="username_field", strategy=_strategy(),
    )
    d.update(kw)
    return CandidateSeed(**d)


def _exp(**kw) -> Experience:
    d = dict(
        experience_id="exp_001", app_id="com.phaset0.logindemo",
        screen_id="LoginView", target_id="username_field",
        strategy=_strategy(), origin="LLM_ACCEPTED_RECOVERY",
        seed_run_id="run_a", seed_step_id=12, seed_recovery_review_id=7,
    )
    d.update(kw)
    return Experience(**d)


# --- E5：CandidateSeed 三件套闸门 ---------------------------------------------


def test_seed_full_triplet_ok():
    s = _seed()
    assert s.seed_run_id == "run_a" and s.seed_step_id == 12
    assert s.seed_recovery_review_id == 7


@pytest.mark.parametrize("drop", ["seed_run_id", "seed_step_id",
                                  "seed_recovery_review_id"])
def test_seed_missing_triplet_field_rejected(drop):
    """E5：三件套缺一拒绝创建——42 条悬空 P0 数据在这里建不出来。"""
    kw = {drop: 0 if drop != "seed_run_id" else ""}
    with pytest.raises(ValidationError, match="missing required seed"):
        _seed(**kw)


def test_seed_zero_step_id_rejected():
    """P0 遗留写 step_id=0 的教训（dangling=42 的直接成因）——模型层拒绝。"""
    with pytest.raises(ValidationError, match="seed_step_id"):
        _seed(seed_step_id=0)


def test_seed_review_id_mismatch_rejected():
    """P3-2：review_id 与 seed_recovery_review_id 恒同指——不一致即构造
    错误（M4 冗余列「对不上」的坑在模型层堵死）。"""
    with pytest.raises(ValidationError, match="review_id"):
        _seed(review_id=99)   # fixture 默认 seed_recovery_review_id=7


def test_experience_success_rate_bypass_rejected():
    """P3-1：旁路构造的 success_rate 会被 Store 落库，与计数矛盾——
    构造期锁死一致性。"""
    with pytest.raises(ValidationError, match="success_rate"):
        _exp(success_rate=0.9)   # 全零计数下 rate 必为 0.0


def test_experience_success_rate_consistent_ok():
    e = _exp(sample_count=4, success_count=3, failure_count=1,
             success_rate=0.75)
    assert e.success_rate == 0.75


def test_seed_extra_field_rejected():
    with pytest.raises(ValidationError):
        _seed(risk_level="LOW")   # 设计 4.2 无此字段；H4 教训：不留后门


# --- Experience 默认值与约束 ---------------------------------------------------


def test_experience_defaults_are_zero_and_empty():
    e = _exp()
    assert e.status is ExperienceStatus.CANDIDATE
    assert e.sample_count == 0 and e.success_count == 0
    assert e.failure_count == 0 and e.success_rate == 0.0
    assert e.validated_builds == []
    assert e.promoted is False and e.promoted_commit is None
    assert e.last_screen_fingerprint is None


def test_experience_status_enum_exactly_four():
    assert {s.value for s in ExperienceStatus} == {
        "CANDIDATE", "VERIFIED", "DEGRADED", "REJECTED"}


def test_experience_counters_must_be_consistent():
    with pytest.raises(ValidationError, match="sample_count"):
        _exp(sample_count=5, success_count=3, failure_count=1)


def test_experience_strategy_uses_p1_locator_strategy():
    """P1 LocatorStrategy 单一真值源：strategy 是单条策略（4.2），序列化
    由 Store 层做进 strategy_json。"""
    e = _exp(strategy=_strategy(value="signin_button", origin="manual"))
    assert e.strategy.type == "accessibility_id"
    assert e.strategy.model_dump()["origin"] == "manual"


def test_promoted_requires_commit():
    with pytest.raises(ValidationError, match="promoted_commit"):
        _exp(promoted=True)


def test_promoted_independent_of_status_9_4():
    """9.4 两条时间线：DEGRADED 不联动撤销 promoted——字段平级、无联动。"""
    e = _exp(promoted=True, promoted_commit="abc1234",
             status=ExperienceStatus.VERIFIED)
    e.status = ExperienceStatus.DEGRADED
    assert e.promoted is True and e.promoted_commit == "abc1234"


def test_validated_builds_reject_duplicates_e7():
    """E7：集合语义——重复 build 是调用方 bug，拒绝而不是去重。"""
    with pytest.raises(ValidationError, match="duplicates"):
        _exp(validated_builds=["1025", "1025"])


def test_record_sample_updates_counters_and_rate():
    e = _exp()
    e.record_sample("SUCCESS", "1025")
    e.record_sample("SUCCESS", "1025")
    e.record_sample("FAILURE", "1026")
    assert (e.sample_count, e.success_count, e.failure_count) == (3, 2, 1)
    assert e.success_rate == pytest.approx(2 / 3)
    # E7：validated_builds 集合去重（同一 build 两次尝试只记一个）
    assert e.validated_builds == ["1025", "1026"]
    e.updated_at > e.created_at


def test_record_sample_rejects_invalid_result():
    e = _exp()
    with pytest.raises(ValueError, match="invalid sample result"):
        e.record_sample("GUARD_MISS", "1025")   # E11：MISS 不是样本


# --- ExperienceRun / StateEvent / Policy --------------------------------------


def test_experience_run_shape():
    r = ExperienceRun(experience_id="exp_001", run_id="run_b", step_id=33,
                      app_build="1026", result="FAILURE",
                      guard_reason="TARGET_AMBIGUOUS")
    assert r.result == "FAILURE" and r.guard_reason == "TARGET_AMBIGUOUS"
    with pytest.raises(ValidationError):
        ExperienceRun(experience_id="e", run_id="r", step_id=1,
                      app_build="1", result="GUARD_MISS")   # 4.7：两值外拒绝


def test_state_event_records_transition():
    ev = StateEvent(experience_id="exp_001", from_status=None,
                    to_status=ExperienceStatus.CANDIDATE,
                    reason="SEEDED", run_id="run_a", app_build="1025")
    assert ev.operator == "system"
    assert ev.from_status is None and ev.to_status is ExperienceStatus.CANDIDATE


def test_verification_policy_defaults_are_design_values():
    """4.5/4.6 默认值即设计值——改动即设计变更，测试拦住。"""
    p = VerificationPolicy()
    assert (p.min_samples, p.min_success_rate, p.min_distinct_runs) == \
        (10, 0.95, 3)
    assert (p.degrade_window, p.degrade_max_failures) == (5, 2)


def test_verification_decision_vocabulary():
    assert {d.value for d in VerificationDecision} == {
        "PROMOTE_TO_VERIFIED", "KEEP_CANDIDATE", "DEGRADE", "REJECT",
        "NO_CHANGE"}
