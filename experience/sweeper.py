"""sweeper.py — 僵尸 Candidate 的过期清理（设计 8.2 / E12 补充规则；Task 3.1 / P2-06）。

设计 §8.2 原文：「CANDIDATE 状态且 last run 早于 `max_idle_days` 前 →
`REJECTED(reason="STALE")`。之后按正常 REJECTED 的证据保留规则处理（见
11.2）。**建议作为 `mta experience sweep` 定期任务跑，不在 Runner 主流程里
做**」——本模块因此不接入 `agent/recovery.py`，只由 CLI 调用。

判定与执行分开（E13 精神）：`is_stale` 是纯函数（时间由调用方注入，
不读系统时钟），`sweep_stale_candidates` 才是 I/O。

**为什么 stale 不能写进 `evaluate`**：4.3 的状态机是「样本说了算」的纯函数，
它的输入里没有「现在几点」；把时间塞进去会让同一个 `(exp, runs, policy)`
在不同时刻给出不同决策，单测也就无法穷举。清理是**运维动作**，与「这条经验
值不值得信」是两件事。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from experience.models import (
    Experience,
    ExperienceRun,
    ExperienceStatus,
)

__all__ = ["StalenessPolicy", "is_stale", "sweep_stale_candidates",
           "STALE_REASON"]

# REJECTED 的 reason（状态事件与审计都用它，单一真值源）
STALE_REASON = "STALE"


@dataclass(frozen=True)
class StalenessPolicy:
    """设计 8.2 的 `candidate_staleness` 段（默认值即设计值）。

    `max_idle_days` 有下界（review_p2_task31 P3-2）：YAML/CLI 手误的
    `-1` 实测会**把当天刚建的候选全清掉**（update_status 无 undo）——
    构造时即 fail-loud。
    """

    max_idle_days: int = 90

    def __post_init__(self) -> None:
        if self.max_idle_days < 1:
            raise ValueError(
                f"max_idle_days must be >= 1, got {self.max_idle_days}")


def _last_activity(exp: Experience,
                   runs: list[ExperienceRun]) -> datetime:
    """最后一次「有新样本」的时刻；**没有任何样本则退回创建时刻**。

    为什么零样本也要算：8.2 的靶子是「僵尸 Candidate」——建出来 90 天没人碰
    过的候选正是最典型的僵尸，若因为「没有 last run」就豁免，它们会永远留在
    库里（E12 的补充规则就是为了解决这个）。
    """
    if runs:
        return max(r.created_at for r in runs)
    return exp.created_at


def is_stale(exp: Experience, runs: list[ExperienceRun], *,
             now: datetime, max_idle_days: int = 90) -> bool:
    """设计 8.2：**CANDIDATE** 且最后活动早于 `max_idle_days` 前 → True。

    纯函数：`now` 由调用方注入（`sweep_stale_candidates` 传系统时钟），
    所以「90 天前」的边界在单测里可精确构造（矩阵 #11）。

    只清 CANDIDATE：VERIFIED / DEGRADED 有各自的失效路径（E6 滑动窗口、
    显式 revalidate），拿「闲置」去清它们会误杀低频但有效的经验——「很久没
    用到」不等于「没用」（设计 4.3 的转换表里也没有这两态 → REJECTED 的
    闲置出口）。
    """
    if exp.status is not ExperienceStatus.CANDIDATE:
        return False
    idle = now - _last_activity(exp, runs)
    return idle > timedelta(days=max_idle_days)


def sweep_stale_candidates(store, policy: StalenessPolicy | None = None, *,
                           now: datetime | None = None) -> list[str]:
    """把过期 Candidate 转成 `REJECTED(reason=STALE)`，返回被清理的 id 列表。

    `now` 缺省取当前 UTC（真跑时）；单测注入固定时刻。**runs 不删除**——
    E12/11.2：REJECTED 后证据至少额外保留 180 天，本模块没有任何 delete
    路径（`update_status` 也只写状态与事件）。
    """
    policy = policy or StalenessPolicy()
    now = now or datetime.now(timezone.utc)
    swept: list[str] = []
    for exp in store.list(ExperienceStatus.CANDIDATE):
        runs = store.get_runs(exp.experience_id)
        if not is_stale(exp, runs, now=now,
                        max_idle_days=policy.max_idle_days):
            continue
        store.update_status(exp.experience_id, ExperienceStatus.REJECTED,
                            STALE_REASON, operator="sweeper")
        swept.append(exp.experience_id)
    return swept
