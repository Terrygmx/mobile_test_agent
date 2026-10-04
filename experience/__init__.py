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

from experience.models import (
    CandidateSeed,
    Experience,
    ExperienceRun,
    ExperienceStatus,
    StateEvent,
    VerificationDecision,
    VerificationPolicy,
)
from experience.schema_migrations import migrate, current_version
from experience.store import (
    EmptyExperienceStore,
    ExperienceStore,
    SQLiteExperienceStore,
)

# review_p2_task12 P3-5：默认库路径**单点定义**——Task 2.2 的 --exp-db
# 装配直接 import，两处字面量必漂移。
DEFAULT_EXPERIENCE_DB = Path("out/experience.db")

__all__ = [
    "Experience", "ExperienceStatus", "CandidateSeed", "ExperienceRun",
    "StateEvent", "VerificationPolicy", "VerificationDecision",
    "migrate", "current_version", "DEFAULT_EXPERIENCE_DB",
    "ExperienceStore", "SQLiteExperienceStore", "EmptyExperienceStore",
]
