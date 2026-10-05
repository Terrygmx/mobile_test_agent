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
   可信度无从谈起，不靠新近度压过有真实历史的同档候选（plan step 1 的
   「无样本排尾」是**档位内**限定）。注意零样本 VERIFIED（9.5 人工
   Promotion 跳过自动验证的路径）会以档位 0 排最前且零证据——这是
   有意的：「人工判断 > 统计」与 9.5 的语义自洽。
3. **success_rate 降序**（同档、同有样本）。
4. **新近度新→旧**（同 rate）：见下方偏离说明。
5. **experience_id 兜底**：全同时按 id 定序——排序必须可复现，报告里
   「逐候选尝试顺序」是排障证据，不能依赖 Python stable sort 的输入顺序。

## 与设计伪代码的偏离（登记）

「最近一次**成功**时间」在 Experience 行上没有专列：实现以 `updated_at`
作新近度代理。**代理的真实语义是「最后一次写时刻」**（review_p2_task32
P3-1）：`record_run` / `update_status` / `record_screen_fingerprint` /
`record_success_build` 任一落库都会推进它，不只是样本，也不分成败——
比「最后一次样本」更粗。影响面仅限**同 rate 同档**候选的先后（Guard +
执行兜底，试错一条的代价只是一次尝试），故不值得为此动 schema；若未来
5.1 原义成为 load-bearing（如缓存/报告需要真实「最近一次成功」），Schema
变更点是给 `experiences` 补 `last_success_at` 列（`record_run` 成功分支
推进），届时同步撤掉本条偏离。

另记（review_p2_task32 P3-3）：`-updated_at.timestamp()` 对 naive
datetime 会 TypeError——fail-loud 可接受（Store 写入恒带 tz），不兜底。
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
