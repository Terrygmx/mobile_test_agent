"""experience — Phase 2 Experience Store 领域模型（设计 4.2/4.3 / P2-02）。

本包只放 Experience 侧模型与 schema（Task 1.2）；graph 三表（主线 B）
拆到 M5 落地、schema 归 graph/migrations 自己的版本链（plan Task 1.2
关键决策，P1 Schema 复盘教训：表结构随实现定型，不提前冻结）。

模型纪律：
  - `strategy` 复用 P1 的 `repository.loader.LocatorStrategy`（单一真值
    源，5.2 同一条定位策略语义），序列化进 `strategy_json`；
  - E5：CandidateSeed 缺 seed 三件套任一 → 拒绝创建（ValidationError）；
  - `promoted` 与 `status` 是独立字段（9.4 两条时间线）——promotion 不随
    DEGRADED 自动撤销，撤销是人工 git revert，不是状态机行为。
"""
from pathlib import Path

from experience.cache import RecoveryCache, cache_key
from experience.models import (
    CandidateSeed,
    Experience,
    ExperienceRun,
    ExperienceStatus,
    StateEvent,
    VerificationDecision,
    VerificationPolicy,
)
from experience.ranker import rank_experiences
from experience.schema_migrations import migrate, current_version
from experience.store import (
    ExperienceStore,
    SQLiteExperienceStore,
    record_sample_runs,
)
from experience.sweeper import (
    STALE_REASON,
    StalenessPolicy,
    is_stale,
    sweep_stale_candidates,
)
from experience.verifier import (
    REVALIDATION_REQUIRED,
    VerificationOutcome,
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

# review_p2_task12 P3-5：默认库路径**单点定义**——Task 2.2 的 --exp-db
# 装配直接 import，两处字面量必漂移。
DEFAULT_EXPERIENCE_DB = Path("out/experience.db")

__all__ = [
    "Experience", "ExperienceStatus", "CandidateSeed", "ExperienceRun",
    "StateEvent", "VerificationPolicy", "VerificationDecision",
    "migrate", "current_version", "DEFAULT_EXPERIENCE_DB",
    "ExperienceStore", "SQLiteExperienceStore", "record_sample_runs",
    # Task 3.1：状态机（纯函数）+ 过期清理
    "VerificationOutcome", "evaluate", "apply_outcome", "revalidate",
    "mark_revalidation_required", "eligible_for_auto_verification",
    "sliding_window_degrade", "needs_revalidation", "distinct_run_count",
    "success_rate_of", "REVALIDATION_REQUIRED",
    "StalenessPolicy", "is_stale", "sweep_stale_candidates", "STALE_REASON",
    # Task 3.2：多候选排序（设计 5.1）
    "rank_experiences",
    # Task 3.3：进程内 Recovery Cache（设计 7.3）
    "RecoveryCache", "cache_key",
]
