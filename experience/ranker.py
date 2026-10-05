"""ranker.py — 多候选排序与回落（设计 5.1；Task 3.2 / P2-07）。

同一 `(app_id, screen_id, target_id)` 可能存在多条历史 Experience（例如
App 经历过两次不同的改名）。`Store.lookup` 返回候选集，`rank_experiences`
给出**逐个尝试**的顺序。排序只决定「先试哪条」——每条候选各自仍走完整
Guard（E1：缓存/排序/信任档位都不绕过安全校验），试错一条的代价只是一次
Guard+执行，不会误用。

## 排序规则（档位 → 证据 → 成功率 → 新近度 → 确定性兜底）

1. **信任档位**：VERIFIED > DEGRADED > CANDIDATE > REJECTED。设计 5.1
   明文「VERIFIED 优先于 CANDIDATE」；DEGRADED 放中间是登记的决策
   （见测试 `test_degraded_ranks_between_verified_and_candidate`）；REJECTED
   排尾是防御性的——`lookup` 已按 7.1 修订排除 REJECTED，ranker 见到它
   属于旁路调用（审计/测试直喂），排序函数不做资格判定，那是 Guard 的事。
2. **有无样本**：同档内零样本排尾——「人工 ACCEPT 过但一次没用过」的
   可信度无从谈起，不靠新近度压过有真实历史的同档候选（plan step 1）。
3. **success_rate 降序**（同档、同有样本）。
4. **新近度新→旧**（同 rate）：见下方偏离说明。
5. **experience_id 兜底**：全同时按 id 定序——排序必须可复现，报告里
   「逐候选尝试顺序」是排障证据，不能依赖 Python stable sort 的输入顺序。

## 与设计伪代码的偏离（登记）

「最近一次**成功**时间」在 Experience 行上没有专列：实现以 `updated_at`
（最后一次**样本**时刻，不分成败）作新近度代理。引入 `last_success_at`
需要每次样本多写一列（或 ranker 现场读 runs——I/O，违反 E13）；对排序
而言两者只影响同分候选的先后，不影响 Guard 判定与样本记账（4.7）。
"""
from __future__ import annotations

from experience.models import Experience, ExperienceStatus

__all__ = ["rank_experiences", "RANK_TIERS"]

# 信任档位（小者优先）。与 ExperienceStatus 一一对应——新状态加入时
# 忘了登记这里会 KeyError（fail-loud），不是静默乱序。
RANK_TIERS: dict[ExperienceStatus, int] = {
    ExperienceStatus.VERIFIED: 0,
    ExperienceStatus.DEGRADED: 1,
    ExperienceStatus.CANDIDATE: 2,
    ExperienceStatus.REJECTED: 3,
}


def _rank_key(exp: Experience) -> tuple:
    return (
        RANK_TIERS[exp.status],
        0 if exp.sample_count > 0 else 1,
        -exp.success_rate,
        -exp.updated_at.timestamp(),
        exp.experience_id,
    )


def rank_experiences(candidates: list[Experience]) -> list[Experience]:
    """设计 5.1：候选集 → 逐个尝试的顺序。**纯函数**（E13）。

    返回新列表，不原地修改输入——调用方的 candidates 顺序另有留痕用途
    （`experience_lookup` 事件、逐候选 stage 的排障顺序）。
    """
    return sorted(candidates, key=_rank_key)
