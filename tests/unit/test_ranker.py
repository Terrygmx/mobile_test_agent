"""Task 3.2 / P2-07：rank_experiences 多候选排序（设计 5.1）。

plan step 1 的失败测试清单逐条对应：
  - VERIFIED > CANDIDATE（信任档位优先，样本再漂亮的 Candidate 也不越级）；
  - 同状态按 success_rate 降序；
  - 再按最近一次成功时间新→旧；
  - 无样本 / REJECTED 排尾。

E13：rank_experiences 是可脱离设备的纯函数——不读库、不看钟、不碰设备；
输入列表不被原地修改（排序返回新列表，调用方的 candidates 顺序另有留痕
用途——experience_lookup 事件的 candidates 计数与逐候选 stage 顺序）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from experience.models import Experience, ExperienceStatus
from experience.ranker import rank_experiences
from repository.loader import LocatorStrategy

NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)


def _exp(status: ExperienceStatus = ExperienceStatus.CANDIDATE, *,
         exp_id: str = "exp_1", rate: float = 0.0, samples: int = 0,
         success: int = 0, updated: datetime | None = None) -> Experience:
    """构造一条排序输入（统计列由助手保证一致，绕过模型一致性校验的坑）。"""
    return Experience(
        experience_id=exp_id, app_id="com.x", screen_id="HomeView",
        target_id="login_button",
        strategy=LocatorStrategy(type="accessibility_id", value="v2",
                                 origin="experience"),
        origin="LLM_ACCEPTED_RECOVERY", status=status,
        sample_count=samples, success_count=success,
        failure_count=samples - success, success_rate=rate,
        seed_run_id="run_seed", seed_step_id=1, seed_recovery_review_id=1,
        created_at=NOW - timedelta(days=30),
        updated_at=updated or NOW - timedelta(days=1))


# --- 档位：VERIFIED > DEGRADED > CANDIDATE > REJECTED（排尾） ----------------


def test_verified_beats_higher_rate_candidate():
    """VERIFIED > CANDIDATE：Candidate 的样本再漂亮也不越级。

    VERIFIED 是「过了 E4+4.5 门槛」的信任档位；Candidate 是「人工 ACCEPT
    过一次、尚未验证」——排序反映可信度，不是单点成功率。
    """
    verified = _exp(ExperienceStatus.VERIFIED, exp_id="v", rate=0.9,
                    samples=10, success=9)
    candidate = _exp(ExperienceStatus.CANDIDATE, exp_id="c", rate=1.0,
                     samples=8, success=8)
    assert rank_experiences([candidate, verified]) == [verified, candidate]


def test_degraded_ranks_between_verified_and_candidate():
    """DEGRADED 档位在 VERIFIED 与 CANDIDATE 之间（**登记的决策**）。

    设计 5.1 只写了 VERIFIED/CANDIDATE 两档，DEGRADED 未提。放中间的
    理由：DEGRADED 有真实历史（曾是 VERIFIED，E6 滑动窗口刚打过脸），
    排序只决定「先试哪条」——每条各自仍走完整 Guard（E1），试错一次的
    代价只是一次 Guard+执行，不会误用。放 CANDIDATE 之后反而让「刚被
    打脸但底蕴还在」的排在「零证据」的后面。
    """
    verified = _exp(ExperienceStatus.VERIFIED, exp_id="v", rate=0.9,
                    samples=10, success=9)
    degraded = _exp(ExperienceStatus.DEGRADED, exp_id="d", rate=0.8,
                    samples=20, success=16)
    candidate = _exp(ExperienceStatus.CANDIDATE, exp_id="c", rate=0.95,
                     samples=20, success=19)
    assert rank_experiences([candidate, degraded, verified]) == [
        verified, degraded, candidate]


def test_rejected_goes_last_even_with_perfect_rate():
    """REJECTED 排尾（plan step 1）：人工判过「不可用」的策略排最后。

    `Store.lookup` 已按 7.1 修订排除 REJECTED，ranker 见到它属于旁路
    调用（审计/测试直喂）——防御性排尾而非拒绝：排序函数不做资格判定，
    那是 Guard 的事。
    """
    rejected = _exp(ExperienceStatus.REJECTED, exp_id="r", rate=1.0,
                    samples=50, success=50)
    candidate = _exp(ExperienceStatus.CANDIDATE, exp_id="c", rate=0.1,
                     samples=10, success=1)
    assert rank_experiences([rejected, candidate]) == [candidate, rejected]


# --- 同档位：success_rate 降序 → 最近成功新→旧 -------------------------------


def test_same_status_sorted_by_success_rate_desc():
    verified_a = _exp(ExperienceStatus.VERIFIED, exp_id="a", rate=0.8,
                      samples=10, success=8)
    verified_b = _exp(ExperienceStatus.VERIFIED, exp_id="b", rate=0.95,
                      samples=20, success=19)
    assert rank_experiences([verified_a, verified_b]) == [verified_b,
                                                          verified_a]


def test_same_rate_sorted_by_recency_newest_first():
    """rate 同分 → 最近写时刻新→旧。

    「最近一次成功时间」在 Experience 行上没有专列（登记的偏离）：以
    `updated_at` 作新近度代理——**真实语义是「最后一次写时刻」**（样本/
    状态跳变/指纹任一落库都推进，review_p2_task32 P3-1 实锤过「状态跳变
    计新」），比「最后一次样本」更粗。影响仅限同分候选先后（Guard 兜底）；
    引入 last_success_at 列的 Schema 变更点已在 ranker 模块 docstring
    登记，届时撤偏离。
    """
    old = _exp(ExperienceStatus.CANDIDATE, exp_id="old", rate=0.9,
               samples=10, success=9, updated=NOW - timedelta(days=7))
    new = _exp(ExperienceStatus.CANDIDATE, exp_id="new", rate=0.9,
               samples=10, success=9, updated=NOW - timedelta(hours=1))
    assert rank_experiences([old, new]) == [new, old]


def test_zero_sample_goes_after_sampled_within_tier():
    """无样本排尾（plan step 1）：零证据的排在同档有样本的后面——
    「人工 ACCEPT 过但一次都没被用过」的可信度无从谈起，不该靠新近度
    压过有真实历史的同档候选。"""
    zero = _exp(ExperienceStatus.CANDIDATE, exp_id="zero", rate=0.0,
                samples=0, updated=NOW)          # 刚建，最新
    sampled = _exp(ExperienceStatus.CANDIDATE, exp_id="s", rate=0.5,
                   samples=10, success=5,
                   updated=NOW - timedelta(days=3))
    assert rank_experiences([zero, sampled]) == [sampled, zero]


def test_full_tie_is_deterministic_by_experience_id():
    """档位/rate/新近度全同 → 按 experience_id 定序。

    排序必须可复现：报告里「逐候选尝试顺序」是排障证据，Python 的
    stable sort 只保证「输入顺序优先」——把输入顺序变成隐藏依赖会让
    同一份数据在两次查询里给出两种尝试顺序，审计对不上。
    """
    a = _exp(ExperienceStatus.CANDIDATE, exp_id="exp_a", rate=0.9,
             samples=10, success=9)
    b = _exp(ExperienceStatus.CANDIDATE, exp_id="exp_b", rate=0.9,
             samples=10, success=9)
    assert rank_experiences([b, a]) == [a, b]
    assert rank_experiences([a, b]) == [a, b]


# --- E13：纯函数纪律 ---------------------------------------------------------


def test_rank_does_not_mutate_input():
    candidates = [
        _exp(ExperienceStatus.CANDIDATE, exp_id="c", rate=0.5, samples=10,
             success=5),
        _exp(ExperienceStatus.VERIFIED, exp_id="v", rate=0.9, samples=10,
             success=9),
    ]
    original = list(candidates)
    rank_experiences(candidates)
    assert candidates == original, "输入列表不被原地修改"


def test_rank_empty_list():
    assert rank_experiences([]) == []


def test_rank_returns_new_list():
    candidates = [_exp(ExperienceStatus.CANDIDATE, exp_id="c")]
    ranked = rank_experiences(candidates)
    assert ranked is not candidates
