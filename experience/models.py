"""models.py — Experience 领域模型（设计 4.2 / P2-02，Task 1.2）。

逐字段对齐设计 4.2 代码骨架；三处 P1 复用/纪律：

  1. `strategy: LocatorStrategy` 复用 `repository.loader` 的定义——5.2
     定位策略单一真值源，P2 不再造第二种（设计明文「复用 P1 定义」）；
  2. `CandidateSeed` 是 E5 的模型侧闸门：seed 三件套（run/step/review id）
     缺一 → ValidationError——Trace 审计（Task 1.1）已实锤 P0 遗留数据
     42/49 悬空，模型层必须让它们「建不出来」，不是靠 Store 写入时人肉
     记得校验；
  3. `promoted` 与 `status` 是两个独立字段（9.4）：Promotion 后 DEGRADED
     不自动撤销已进 Git 的策略——撤销是人工 git revert，不是状态机行为；
     模型用两个平级字段表达，不提供联动 setter。

E7：`validated_builds` 是集合语义（list 但去重校验），不是区间。
E11 只影响 experience_runs 的写入侧（4.7 表），模型不承载该判定。
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from repository.loader import LocatorStrategy

__all__ = [
    "ExperienceStatus", "Experience", "CandidateSeed", "ExperienceRun",
    "StateEvent", "VerificationPolicy", "VerificationDecision",
    "EXPERIENCE_ORIGINS",
]

# 设计 4.2：P2 唯一来源；未来可扩展（Literal 换 enum 的预留注释在骨架里）
EXPERIENCE_ORIGINS = Literal["LLM_ACCEPTED_RECOVERY"]


class ExperienceStatus(str, Enum):
    """4.3 生命周期四态。转换归 ExperienceVerifier（Task 3.1），模型不判。"""

    CANDIDATE = "CANDIDATE"
    VERIFIED = "VERIFIED"
    DEGRADED = "DEGRADED"
    REJECTED = "REJECTED"


class _Strict(BaseModel):
    """E 系纪律的载体：多余字段拒绝（schema 漂移要 fail-loud，不是吞掉）。"""

    model_config = ConfigDict(extra="forbid")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Experience(_Strict):
    """4.2 骨架落地。统计只数「实际被尝试」的样本（E11 表：Guard MISS/
    风险拦截不写 experience_runs，因此不进这些计数）。"""

    experience_id: str
    app_id: str
    screen_id: str
    target_id: str
    strategy: LocatorStrategy
    origin: EXPERIENCE_ORIGINS
    status: ExperienceStatus = ExperienceStatus.CANDIDATE

    # 总体统计（默认零值——新建 Experience 没有任何「被尝试」样本）
    sample_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    success_rate: float = 0.0

    validated_builds: list[str] = Field(default_factory=list)
    last_screen_fingerprint: str | None = None

    # 可追溯性（E5 三件套——与 CandidateSeed 同源，Store 层再校验一致性）
    seed_run_id: str
    seed_step_id: int
    seed_recovery_review_id: int

    # Promotion 状态（9.4 独立时间线；见模块 docstring 第 3 点）
    promoted: bool = False
    promoted_commit: str | None = None

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)

    @field_validator("validated_builds")
    @classmethod
    def _builds_are_set(cls, v: list[str]) -> list[str]:
        # E7：集合语义——重复 build 是调用方 bug，模型层拒绝而不是去重
        # （去重会掩盖重复写路径）。
        if len(v) != len(set(v)):
            raise ValueError(f"validated_builds has duplicates: {v}")
        return v

    @model_validator(mode="after")
    def _counters_consistent(self) -> "Experience":
        if self.success_count + self.failure_count != self.sample_count:
            raise ValueError(
                f"sample_count ({self.sample_count}) != success"
                f" ({self.success_count}) + failure ({self.failure_count})")
        if self.promoted and not self.promoted_commit:
            # 9.4/9.5：Promotion 必须带 git sha（9.4 节「记录 promoted_commit」）
            raise ValueError("promoted=True requires promoted_commit")
        return self

    def record_sample(self, result: Literal["SUCCESS", "FAILURE"],
                      app_build: str) -> None:
        """记一次「实际被尝试」（E11 口径由调用方保证——Guard MISS/风险
        拦截不得调本方法）。统计与 updated_at 同步推进；validated_builds
        集合语义去重由 validator 兜底（重复即调方 bug）。"""
        if result not in ("SUCCESS", "FAILURE"):
            raise ValueError(f"invalid sample result: {result!r}")
        self.sample_count += 1
        if result == "SUCCESS":
            self.success_count += 1
        else:
            self.failure_count += 1
        self.success_rate = (self.success_count / self.sample_count
                             if self.sample_count else 0.0)
        if app_build not in self.validated_builds:
            self.validated_builds.append(app_build)
        self.updated_at = _utcnow()


class CandidateSeed(_Strict):
    """E5 的模型侧闸门：来自 recovery_reviews.ACCEPT 的种子。

    三件套缺一 → ValidationError（plan step 1 的失败测试首项）。P1 的
    seedable_accepts（Task 1.1 审计）是本模型的合法供给方；42 条悬空
    行在这里建不出来。
    """

    review_id: int
    recovery_id: int
    seed_run_id: str
    seed_step_id: int
    seed_recovery_review_id: int
    app_id: str
    screen_id: str
    target_id: str
    strategy: LocatorStrategy
    app_build: str | None = None
    reviewer: str | None = None

    @model_validator(mode="after")
    def _seed_triplet_required(self) -> "CandidateSeed":
        # E5 原文：Candidate 的初始种子只能来自 ACCEPT 记录——三件套是
        # 「这条 ACCEPT 真的发生过」的可追溯证据，缺一 = 不可追溯 = 拒绝。
        missing = [n for n in ("seed_run_id", "seed_step_id",
                               "seed_recovery_review_id")
                   if not getattr(self, n)]
        if missing:
            raise ValueError(
                f"CandidateSeed missing required seed fields (E5): {missing}")
        if self.seed_step_id <= 0:
            # P0 遗留写入 step_id=0 的教训（审计 dangling=42 的直接成因）
            raise ValueError(
                f"seed_step_id must be a real steps.id, got {self.seed_step_id}")
        return self


class ExperienceRun(_Strict):
    """一次「实际被尝试」的样本行（4.7 表口径：只记 record_as_sample 的）。

    `result` 只有两值；guard_reason 只在 FAILURE 时有意义（SUCCESS 时
    置 None 由 Store 层保证，模型不拦——避免把 Store 的写路径纪律复制
    进每个构造点）。"""

    experience_id: str
    run_id: str
    step_id: int
    app_build: str
    screen_fingerprint: str | None = None
    result: Literal["SUCCESS", "FAILURE"]
    guard_reason: str | None = None
    effective_risk: str | None = None
    uniqueness_count: int | None = None
    element_type_match: bool | None = None
    latency_ms: int | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class StateEvent(_Strict):
    """状态时间线事件（4.3 生命周期的每一步跳变都留痕——状态可复算，
    不靠当前值反推）。reason 枚举在 Task 3.1 ExperienceVerifier 落地时
    收紧为 Literal；P2-02 先自由文本（设计 11 节注释同口径）。"""

    experience_id: str
    from_status: ExperienceStatus | None
    to_status: ExperienceStatus
    reason: str
    run_id: str | None = None
    app_build: str | None = None
    operator: str = "system"
    created_at: datetime = Field(default_factory=_utcnow)


class VerificationPolicy(_Strict):
    """4.5 升级门槛（默认值即设计值——改动即设计变更，测试拦住）。"""

    min_samples: int = 10
    min_success_rate: float = 0.95
    min_distinct_runs: int = 3
    # 4.6 滑动窗口降级参数
    degrade_window: int = 5
    degrade_max_failures: int = 2


class VerificationDecision(str, Enum):
    """4.3 状态机决策词汇（Task 3.1 ExperienceVerifier 的输出）。"""

    PROMOTE_TO_VERIFIED = "PROMOTE_TO_VERIFIED"
    KEEP_CANDIDATE = "KEEP_CANDIDATE"
    DEGRADE = "DEGRADE"
    REJECT = "REJECT"
    NO_CHANGE = "NO_CHANGE"
