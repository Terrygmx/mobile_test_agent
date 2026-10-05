"""Task 3.1 / P2-06：过期清理（设计 8.2 / E12；矩阵 #11）。

plan step 1 的失败测试项：**STALE 90 天**——`CANDIDATE` 且最后活动早于
`max_idle_days` → `REJECTED(reason=STALE)`，且证据不删除（11.2：REJECTED 后
至少额外保留 180 天）。

`is_stale` 是纯函数（`now` 由调用方注入），所以「90 天前」的边界可精确构造；
`sweep_stale_candidates` 才碰库。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from experience.models import (
    CandidateSeed,
    Experience,
    ExperienceRun,
    ExperienceStatus,
)
from experience.store import SQLiteExperienceStore
from experience.sweeper import (
    STALE_REASON,
    StalenessPolicy,
    is_stale,
    sweep_stale_candidates,
)
from repository.loader import LocatorStrategy

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)


def _exp(days_idle: float, *,
         status: ExperienceStatus = ExperienceStatus.CANDIDATE,
         created_days_ago: float | None = None) -> Experience:
    """构造一条 Experience（不落库）——`is_stale` 是纯函数，不需要库。"""
    created = NOW - timedelta(days=created_days_ago
                              if created_days_ago is not None else days_idle)
    return Experience(
        experience_id="exp_1", app_id="com.x", screen_id="HomeView",
        target_id="login_button",
        strategy=LocatorStrategy(type="accessibility_id", value="v2",
                                 origin="experience"),
        origin="LLM_ACCEPTED_RECOVERY", status=status,
        seed_run_id="run_seed", seed_step_id=1, seed_recovery_review_id=1,
        created_at=created)


def _run(days_ago: float) -> ExperienceRun:
    return ExperienceRun(experience_id="exp_1", run_id=f"run_{days_ago}",
                         step_id=1, app_build="1026", result="SUCCESS",
                         created_at=NOW - timedelta(days=days_ago))


# --- is_stale（纯函数边界） -------------------------------------------------


@pytest.mark.parametrize("days,expected", [
    (0, False), (89, False), (90, False), (90.001, True), (91, True),
    (365, True),
])
def test_is_stale_boundary(days, expected):
    """90 天是**严格大于**才清——恰好 90 天不算过期。

    边界取开区间是为了让「闲置 90 天」与「>90 天」可区分：`max_idle_days`
    的语义是「超过这个天数」，写成 `>=` 会让配置值比字面少一天生效。
    """
    assert is_stale(_exp(days), [], now=NOW, max_idle_days=90) is expected


def test_is_stale_uses_last_run_not_creation():
    """有样本时看**最后一次样本**，不看创建时刻。

    200 天前创建、10 天前刚用过 → 不过期。拿创建时刻判会把「老而活跃」的
    经验误清，而它们恰恰是最值得留的那批。
    """
    exp = _exp(200, created_days_ago=200)
    assert is_stale(exp, [_run(10)], now=NOW, max_idle_days=90) is False
    assert is_stale(exp, [_run(120)], now=NOW, max_idle_days=90) is True


def test_is_stale_zero_run_uses_creation_time():
    """零样本的候选按**创建时刻**算——它正是 8.2 要清的「僵尸 Candidate」。

    若因为「没有 last run」就豁免，建出来就没人碰过的候选会永远留在库里。
    """
    assert is_stale(_exp(91), [], now=NOW, max_idle_days=90) is True
    assert is_stale(_exp(10), [], now=NOW, max_idle_days=90) is False


@pytest.mark.parametrize("status", [ExperienceStatus.VERIFIED,
                                    ExperienceStatus.DEGRADED,
                                    ExperienceStatus.REJECTED])
def test_is_stale_only_applies_to_candidate(status):
    """只清 CANDIDATE：其余三态有各自的失效路径。

    「很久没用到」不等于「没用」——低频但有效的经验（一年只跑两次的回归
    用例）会被闲置清理误杀，而 4.3 的转换表里也没有这两态 → REJECTED 的
    闲置出口。
    """
    assert is_stale(_exp(365, status=status), [], now=NOW,
                    max_idle_days=90) is False


def test_staleness_policy_default_is_design_value():
    assert StalenessPolicy().max_idle_days == 90


# --- sweep_stale_candidates（落库） ----------------------------------------


def _backdate(store, experience_id: str, days: float) -> None:
    """把 Experience 与其全部 runs 的时间回拨（造 90 天前的现场）。"""
    stamp = (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with store._write_tx() as conn:
        conn.execute("UPDATE experiences SET created_at=?, updated_at=?"
                     " WHERE experience_id=?", (stamp, stamp, experience_id))
        conn.execute("UPDATE experience_runs SET created_at=?"
                     " WHERE experience_id=?", (stamp, experience_id))


def _seed(store, *, runs: int = 0) -> Experience:
    exp = store.create_candidate(CandidateSeed(
        review_id=7, recovery_id=1, seed_run_id="run_seed", seed_step_id=12,
        seed_recovery_review_id=7, app_id="com.x", screen_id="HomeView",
        target_id="login_button",
        strategy=LocatorStrategy(type="accessibility_id", value="v2",
                                 origin="experience")))
    for i in range(runs):
        store.record_run(exp.experience_id, ExperienceRun(
            experience_id=exp.experience_id, run_id=f"run_{i}", step_id=1 + i,
            app_build="1026", result="SUCCESS"))
    return exp


def _state_events(store, experience_id: str) -> list[dict]:
    conn = store._connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT from_status, to_status, reason, operator"
            " FROM experience_state_events WHERE experience_id=?"
            " ORDER BY id", (experience_id,)).fetchall()]
    finally:
        conn.close()


@pytest.fixture()
def store(tmp_path):
    return SQLiteExperienceStore(tmp_path / "exp.db")


def test_sweep_rejects_stale_candidate_and_keeps_evidence(store):
    """矩阵 #11：90 天无新样本 → REJECTED(reason=STALE)，**runs 不删除**。

    E12/11.2：REJECTED 后证据至少额外保留 180 天（审计「为什么当初被信任」
    只能靠这些行）。所以清理只写状态与事件，没有任何 delete 路径。
    """
    exp = _seed(store, runs=3)
    _backdate(store, exp.experience_id, 120)

    assert sweep_stale_candidates(store, now=NOW) == [exp.experience_id]

    after = store.list(ExperienceStatus.REJECTED)
    assert [e.experience_id for e in after] == [exp.experience_id]
    assert len(store.get_runs(exp.experience_id)) == 3, "证据不删除"
    assert [(r["to_status"], r["reason"], r["operator"])
            for r in _state_events(store, exp.experience_id)[-1:]] == [
        ("REJECTED", STALE_REASON, "sweeper")]


def test_sweep_ignores_fresh_candidate(store):
    exp = _seed(store, runs=1)
    _backdate(store, exp.experience_id, 89)
    assert sweep_stale_candidates(store, now=NOW) == []
    assert store.lookup(exp.app_id, exp.screen_id,
                        exp.target_id)[0].status \
        is ExperienceStatus.CANDIDATE


def test_sweep_ignores_idle_verified(store):
    """VERIFIED 闲置一年也不清（E6 的降级走滑动窗口，不走闲置）。"""
    exp = _seed(store)
    store.update_status(exp.experience_id, ExperienceStatus.VERIFIED, "M")
    _backdate(store, exp.experience_id, 365)
    assert sweep_stale_candidates(store, now=NOW) == []


def test_sweep_returns_only_the_stale_ones(store):
    stale = _seed(store)
    fresh = _seed(store)
    store.create_candidate(CandidateSeed(
        review_id=8, recovery_id=2, seed_run_id="run_seed", seed_step_id=13,
        seed_recovery_review_id=8, app_id="com.x", screen_id="HomeView",
        target_id="other_button",
        strategy=LocatorStrategy(type="accessibility_id", value="v3",
                                 origin="experience")))
    _backdate(store, stale.experience_id, 200)
    _backdate(store, fresh.experience_id, 1)

    swept = sweep_stale_candidates(store, now=NOW)
    assert swept == [stale.experience_id], \
        "只返回被清理的 id（调用方据此报告），且不含新鲜的那些"


def test_sweep_honours_policy_max_idle_days(store):
    exp = _seed(store)
    _backdate(store, exp.experience_id, 30)
    assert sweep_stale_candidates(
        store, StalenessPolicy(max_idle_days=29), now=NOW) == [
        exp.experience_id]
    # 30 天闲置在 90 天口径下不清——用**新种**的候选考（review_p2_task31
    # P3-6：复用已被清成 REJECTED 的那条，无论 90 天逻辑对错断言都成立，
    # 策略值根本没被读到）。
    fresh30 = _seed(store)
    _backdate(store, fresh30.experience_id, 30)
    assert sweep_stale_candidates(
        store, StalenessPolicy(max_idle_days=90), now=NOW) == []
    current = next(e for e in store.list(ExperienceStatus.CANDIDATE)
                   if e.experience_id == fresh30.experience_id)
    assert is_stale(current, store.get_runs(fresh30.experience_id),
                    now=NOW, max_idle_days=90) is False
