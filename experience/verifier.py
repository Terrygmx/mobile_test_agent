"""verifier.py — ExperienceVerifier 纯函数状态机（设计 4.3–4.6 / 8.2；Task 3.1 / P2-06）。

**E13 的载体**：`evaluate` 与它依赖的全部谓词都是**可脱离设备的纯函数**——
输入 `(Experience, runs, policy, 资格事实)`，输出决策；不碰设备、不碰库、不看
时间。所有分支都能在单测里覆盖（矩阵 #5–#9/#11 逐条对应，见各函数 docstring）。
本模块唯一的 I/O 在文件末尾的「应用」区（`apply_outcome` / `revalidate`），
它们只做「决策 → 落库」，不含任何判定逻辑。

## 决策表（4.3 生命周期 + 4.4/4.5/4.6 判据）

| 当前状态 | 条件 | 决策 | reason |
|---|---|---|---|
| REJECTED | —（终态） | `NO_CHANGE` | `TERMINAL` |
| CANDIDATE | 不满足 E4 资格 | `KEEP_CANDIDATE` | `NOT_ELIGIBLE` |
| CANDIDATE | 满足 4.5 门槛 | `PROMOTE_TO_VERIFIED` | `THRESHOLD_MET` |
| CANDIDATE | 其他 | `KEEP_CANDIDATE` | `THRESHOLD_NOT_MET` |
| VERIFIED | 滑动窗口触发（4.6/E6） | `DEGRADE` | `SLIDING_WINDOW` |
| VERIFIED | 其他 | `NO_CHANGE` | `HEALTHY` |
| DEGRADED | —（只能走**显式** `revalidate`） | `NO_CHANGE` | `AWAITING_REVALIDATION` |

DEGRADED 没有自动出口：4.3 明文「DEGRADED ──(显式 revalidate 成功)──►
VERIFIED」。**不提供「窗口恢复就自动转回」**——那会让降级变成抖动（E6 的
滑动窗口是安全阀，不是健康探针）。**同理也没有自动的 REJECTED 出口**：
§4.3 的「DEGRADED ──(持续失败 / 人工判定)──► REJECTED」目前**无实现**——
`evaluate` 对反复失败的 DEGRADED 只会一直返回 `AWAITING_REVALIDATION`，
唯一的自动出口是 sweeper（它只清 CANDIDATE）。人工判定侧归 9.5 流程；
「持续失败 → REJECTED」是否在 Task 3.4 的 CLI 里给 `mta experience reject`
出口，接线时定档（review_p2_task31 P3-3，勿当回归重查）。

## 与设计伪代码的四处有意偏离（都写在对应函数的 docstring 里）

1. `evaluate` 多一个**必填关键字** `auto_verify_eligible`：E4 的资格是**目标
   元素**的属性（idempotency / risk），而 `(exp, runs, policy)` 三者里都没有
   它——`ExperienceRun.effective_risk` 恒为 LOW（4.7 表：风险未过的尝试**不写
   样本**，见 §4.7 第 4 行），拿它判 E4 必然恒真。故资格由调用方经
   `eligible_for_auto_verification(element)` 算好传入，**必填无默认值**：
   忘了传是 TypeError（fail-loud），不是「默默不升级」。
2. `evaluate` 返回 `VerificationOutcome`（决策 + reason + 判据明细）而不是裸
   `VerificationDecision`：状态事件要写 reason、报告要展示判据，在调用方再推
   一遍等于把同一套规则实现两次。
3. `success_rate` 与 `distinct_runs` 一律**从传入的 runs 现算**，不读
   `Experience` 上的冗余列——冗余列是给查询用的，判定必须以历史为唯一依据
   （E11：判定只吃「实际被尝试」的样本）。
4. `eligible_for_auto_verification` **去掉了伪代码里的 `exp` 参数**（签名级
   偏离，review_p2_task31 P3-5）：设计伪代码 `eligible(exp, element)` 的
   `exp` 在函数体内未用到，留着只会诱导调用方以为状态参与资格判定（资格是
   **元素**的属性，与经验当前的样本/状态无关）。顺带两笔口径：伪代码用
   `element.effective_risk`，实现用 `element.risk`（`EffectiveElement` 的
   真实字段名是后者）；`executor.policy.Risk` 与 `testcase.schema.Risk` 是
   **同一枚举对象**（`is` 为 True），两处 import 名字不同源却同体——改
   import 源不会换枚举，勿为此加转换层。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from executor.policy import Idempotency, Risk
from experience.models import (
    Experience,
    ExperienceRun,
    ExperienceStatus,
    VerificationDecision,
    VerificationPolicy,
)

__all__ = [
    "VerificationOutcome",
    "REVALIDATION_REQUIRED",
    "is_state_transition",
    "distinct_run_count",
    "success_rate_of",
    "recent_failure_count",
    "sliding_window_degrade",
    "eligible_for_auto_verification",
    "needs_revalidation",
    "evaluate",
    "apply_outcome",
    "revalidate",
    "mark_revalidation_required",
]

# E8 的标记 reason（fingerprint 变化 → 标记但**不**改状态、不删除、不拒绝）
REVALIDATION_REQUIRED = "REVALIDATION_REQUIRED"


@dataclass(frozen=True)
class VerificationOutcome:
    """`evaluate` 的结论：决策 + 状态事件 reason + 可观测的判据明细。

    `detail` 是**给人看与给报告用**的判据快照（样本数/成功率/distinct_runs/
    窗口内失败数/门槛），不进数据库结构；有了它，排障不必回头重算一遍。
    """

    decision: VerificationDecision
    reason: str
    detail: dict = field(default_factory=dict)

    @property
    def changes_status(self) -> bool:
        """是否是一次真正的状态跳变（NO_CHANGE / KEEP_CANDIDATE 都不是）。"""
        return self.decision in (VerificationDecision.PROMOTE_TO_VERIFIED,
                                 VerificationDecision.DEGRADE,
                                 VerificationDecision.REJECT)


# --- 纯谓词（4.5 / 4.6 / 4.4 / E8） ---------------------------------------


def is_state_transition(from_status, to_status) -> bool:
    """**「状态跳变」的唯一定义**（时间线行的判据）。

    `from_status is None`（首条事件，无前态）或 `from != to` 才是跳变；
    `from == to` 是**非跳变标记**（E8 的 `REVALIDATION_REQUIRED` 刻意写同值行，
    见 `store.record_state_event`）。

    ⚠️ 这个规则在 `SQLiteExperienceStore.has_state_event_since_transition` 里
    有一份 SQL 版（`from_status IS NULL OR from_status != to_status`）——两处
    必须同义。任何按「时间线行」计数的消费方（如指标的 revalidation 分母）都
    要用本函数，不要各自写 `to_status == "DEGRADED"` 之类的判据：那会把非跳变
    标记也算进去（review_p2_task43 P3-1 实测：真降级 1 次报成 2 次、成功率
    100% 掉成 50%）。
    """
    return from_status is None or from_status != to_status


def distinct_run_count(runs: list[ExperienceRun]) -> int:
    """4.5：`distinct_runs` = 不同 `run_id` 的计数。

    同一个 run 内多次命中只算一次——否则「一个 testcase 反复点同一个按钮」
    会虚增样本多样性，把 3 次真实构建的验证门槛稀释成 1 次（设计 4.5 明文）。
    """
    return len({r.run_id for r in runs})


def success_rate_of(runs: list[ExperienceRun]) -> float:
    """总体成功率（从历史现算）。空历史 → 0.0（不是 1.0：没验证过 ≠ 全对）。"""
    if not runs:
        return 0.0
    return sum(1 for r in runs if r.result == "SUCCESS") / len(runs)


def recent_failure_count(runs: list[ExperienceRun], *,
                         window: int) -> int:
    """4.6 判据的共用计数谓词：最近 `window` 次里的失败数（**唯一实现**）。

    `sliding_window_degrade` 与 `evaluate` 的 `detail["window_failures"]`
    都从这里取数——同一概念只许一处实现，否则两套切片必然漂移
    （review_p2_task31 P3-1：`runs[-0:]` 是**整个列表**，裸切片会把
    「最近 0 条的失败数」算成「全部历史的失败数」，detail 静默失真）。
    """
    if window <= 0:
        raise ValueError(f"window must be positive, got {window}")
    return sum(1 for r in runs[-window:] if r.result == "FAILURE")


def sliding_window_degrade(runs: list[ExperienceRun], *,
                           window: int = 5, max_failures: int = 2) -> bool:
    """4.6 / E6：最近 `window` 次里失败 ≥ `max_failures` → 触发降级。

    调用方必须传**按时间升序的完整历史**（`ExperienceStore.get_runs` 的语义），
    本函数只取尾部窗口。列表里只会有「实际被尝试」的样本——4.7 表保证
    Screen 不匹配 / 风险拦截**不写** `experience_runs`（E11）。

    E6：窗口触发**优先于**总体 success_rate，两者独立计算（矩阵 #5：历史 100
    次 98 成功、最近 5 次里 3 次失败 → 立即 DEGRADED，不受 98% 影响）。
    """
    return (recent_failure_count(runs, window=window) >= max_failures)


def eligible_for_auto_verification(element) -> bool:
    """4.4 / E4：自动 Candidate→Verified 的**资格**判定。

    `element` 是 Repository 解析出的 `EffectiveElement`（只用到
    `idempotency` / `risk` 两个字段，故此处按鸭子类型接受）。

    不满足 → 永远停留在 CANDIDATE（或人工 Promotion，9.5），**不管样本和
    成功率多高**（矩阵 #8 非幂等 10/10、#9 风险 MEDIUM/HIGH 100%）。

    `None`（未登记 / metadata 未声明）一律**不合格**：E4 是安全约束，
    「不知道」必须与「不满足」同侧（fail-closed）——这与 `guard_candidate` 的
    `effective_risk=None` 按最高风险处理是同一条纪律。
    """
    return (getattr(element, "idempotency", None) is Idempotency.IDEMPOTENT
            and getattr(element, "risk", None) is Risk.LOW)


def needs_revalidation(exp: Experience, current_fingerprint: str | None) -> bool:
    """E8：观测到的 fingerprint 与记录不符 → 需要重新验证。

    **纯判定，不做任何标记动作**（标记见 `mark_revalidation_required`）。
    任一侧为 `None` → `False`：没有记录（首次命中）或页面不可解析时无从比较，
    此时「不确定」不等于「变了」——谎报会让每条新 Experience 都被要求重验证。
    """
    if exp.last_screen_fingerprint is None or current_fingerprint is None:
        return False
    return exp.last_screen_fingerprint != current_fingerprint


# --- 状态机（4.3） ---------------------------------------------------------


def evaluate(exp: Experience, runs: list[ExperienceRun],
             policy: VerificationPolicy, *,
             auto_verify_eligible: bool | None) -> VerificationOutcome:
    """4.3 状态机：`(当前状态, 完整历史, 策略, 资格) → 决策`。**纯函数**。

    `runs` 必须是**完整历史**（升序）。长度与 `exp.sample_count` 不符即
    `ValueError`：传了截断的列表会把「10 个样本」算成「5 个」，门槛判定静默
    失真——这是 fail-loud 而非容忍的场景（调用方本该传 `get_runs(id)` 的全量）。

    `auto_verify_eligible` **必填**：见模块 docstring 偏离 1。三态
    （review_p2_task34 P3-2）：`True`/`False` = E4 资格判定结果（CANDIDATE
    分支消费，None 落在 CANDIDATE 上按 False fail-closed）；`None` =
    **不适用**（非 CANDIDATE 分支不消费资格）——与 False 分开传，将来
    若其他分支消费资格（如 revalidate 资格）二者可区分。
    """
    if len(runs) != exp.sample_count:
        raise ValueError(
            f"runs 是截断历史：len(runs)={len(runs)} != "
            f"sample_count={exp.sample_count}；evaluate 需要**完整**历史"
            f"（用 ExperienceStore.get_runs(experience_id) 取全量）")

    sample_count = len(runs)
    rate = success_rate_of(runs)
    distinct = distinct_run_count(runs)
    window_failures = recent_failure_count(runs,
                                           window=policy.degrade_window)
    base = {"sample_count": sample_count, "success_rate": round(rate, 6),
            "distinct_runs": distinct,
            "window_failures": window_failures,
            "policy": {"min_samples": policy.min_samples,
                       "min_success_rate": policy.min_success_rate,
                       "min_distinct_runs": policy.min_distinct_runs,
                       "degrade_window": policy.degrade_window,
                       "degrade_max_failures": policy.degrade_max_failures}}

    if exp.status is ExperienceStatus.REJECTED:
        # 4.3：REJECTED 是终态（人工判定过不可用）。状态机不得复活它——
        # 要复活只能人工重新 ACCEPT 一条新 Candidate（E5 的重新学习路径）。
        return VerificationOutcome(VerificationDecision.NO_CHANGE,
                                   "TERMINAL", base)

    if exp.status is ExperienceStatus.CANDIDATE:
        if not auto_verify_eligible:
            # E4：样本再漂亮也不升级（矩阵 #8/#9）。这里**不**返回 NO_CHANGE：
            # CANDIDATE 是它应有的状态，语义是「保持候选」。
            return VerificationOutcome(VerificationDecision.KEEP_CANDIDATE,
                                       "NOT_ELIGIBLE",
                                       {**base, "eligible": False})
        met = (sample_count >= policy.min_samples
               and rate >= policy.min_success_rate
               and distinct >= policy.min_distinct_runs)
        if met:
            return VerificationOutcome(
                VerificationDecision.PROMOTE_TO_VERIFIED, "THRESHOLD_MET",
                {**base, "eligible": True})
        return VerificationOutcome(VerificationDecision.KEEP_CANDIDATE,
                                   "THRESHOLD_NOT_MET",
                                   {**base, "eligible": True})

    if exp.status is ExperienceStatus.VERIFIED:
        if sliding_window_degrade(runs, window=policy.degrade_window,
                                  max_failures=policy.degrade_max_failures):
            # E6：窗口优先于总体成功率——这里**不看** rate（矩阵 #5）
            return VerificationOutcome(VerificationDecision.DEGRADE,
                                       "SLIDING_WINDOW", base)
        return VerificationOutcome(VerificationDecision.NO_CHANGE,
                                   "HEALTHY", base)

    # DEGRADED：没有自动出口（4.3 只允许显式 revalidate）
    return VerificationOutcome(VerificationDecision.NO_CHANGE,
                               "AWAITING_REVALIDATION", base)


# --- 应用（本模块唯一的 I/O 入口：只做「决策 → 落库」，不含判定） ----------

_DECISION_STATUS = {
    VerificationDecision.PROMOTE_TO_VERIFIED: ExperienceStatus.VERIFIED,
    VerificationDecision.DEGRADE: ExperienceStatus.DEGRADED,
    VerificationDecision.REJECT: ExperienceStatus.REJECTED,
}


def apply_outcome(store, exp: Experience, outcome: VerificationOutcome, *,
                  run_id: str | None = None,
                  app_build: str | None = None,
                  operator: str = "system") -> bool:
    """把决策落到 Store（状态跳变 + `experience_state_events` 留痕）。

    返回是否真的发生了跳变。`KEEP_CANDIDATE` / `NO_CHANGE` 不写任何东西——
    同状态写事件会把时间线塞满噪声（`update_status` 本身对同状态 no-op，
    这里只是不去调用它）。

    判定与落库**分开**是为了 E13：`evaluate` 可脱离设备单测，本函数才是
    I/O。两者之间没有共享的规则，故不存在「两套实现漂移」。

    ⚠️ 本函数**不校验** `exp.status` 与决策是否匹配——它是「决策 → 落库」的
    裸搬运工（review_p2_task31 P3-4）。调用方必须保证 `outcome` 来自**同一条
    exp** 的 `evaluate`；Task 3.4 CLI 接线时若需校验，由调用方传前自行断言。
    """
    if not outcome.changes_status:
        return False
    new_status = _DECISION_STATUS[outcome.decision]
    store.update_status(exp.experience_id, new_status, outcome.reason,
                        operator=operator, run_id=run_id, app_build=app_build)
    return True


def revalidate(store, exp: Experience, *, fingerprint: str | None = None,
               run_id: str | None = None, app_build: str | None = None,
               operator: str = "system") -> None:
    """9.5 / 4.3：**显式**重验证通过 → `VERIFIED(reason=REVALIDATED)`。

    「显式」是设计明文：DEGRADED 只能由人（或 `mta experience revalidate`）
    推回 VERIFIED，状态机不自动恢复。本函数只做两件事——记状态跳变、把
    **观测到的 fingerprint** 更新为新的记录（E8「重验证通过后更新 fingerprint
    观测记录」）。它**不**自己跑验证：验证证据由调用方（跑了一次真机验证的
    人/命令）提供，`fingerprint` 就是那次观测。

    **只接受 DEGRADED**（fail-loud，review_p2_task31 P2-1）：CANDIDATE 走
    这条路等于绕过 E4 资格 + 4.5 门槛零样本直推 VERIFIED；REJECTED 走这条
    路等于复活终态——4.3 明文复活只能人工重新 ACCEPT 一条新 Candidate
    （E5 的重新学习路径）。两条都是状态机红线，不提供静默 no-op 出口。
    """
    if exp.status is not ExperienceStatus.DEGRADED:
        raise ValueError(
            f"revalidate 只用于 DEGRADED（显式重验证），当前状态是 "
            f"{exp.status.value}：CANDIDATE 需先经 evaluate 过 E4+4.5 门槛，"
            f"REJECTED 是终态、只能重新 ACCEPT 新 Candidate（E5）")
    store.update_status(exp.experience_id, ExperienceStatus.VERIFIED,
                        "REVALIDATED", operator=operator, run_id=run_id,
                        app_build=app_build)
    if fingerprint is not None:
        store.record_screen_fingerprint(exp.experience_id, fingerprint)


def mark_revalidation_required(store, exp: Experience, *,
                               run_id: str | None = None,
                               app_build: str | None = None,
                               operator: str = "system") -> bool:
    """E8：标记 `REVALIDATION_REQUIRED` —— **不改状态、不删除、不拒绝**。

    为什么落 `experience_state_events` 而不是新表：设计 §11.1「追加到 P1 的
    Trace，不新建独立存储体系」，而状态时间线就是该 Experience 的追加流水。
    为什么 from==to：schema 的 `to_status` 是 NOT NULL（「跳变必须有终态」），
    而非跳变标记没有终态——同值表达「此刻仍是这个状态，只是被标记了」。

    幂等：同一状态下的重复标记只写一次（否则每次命中都塞一行）。
    """
    if store.has_state_event_since_transition(exp.experience_id,
                                              REVALIDATION_REQUIRED):
        return False
    store.record_state_event(exp.experience_id, REVALIDATION_REQUIRED,
                             run_id=run_id, app_build=app_build,
                             operator=operator)
    return True
