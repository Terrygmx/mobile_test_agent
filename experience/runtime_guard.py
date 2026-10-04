"""runtime_guard.py — Experience Runtime Guard（设计 5 节 / P2-04，Task 2.2）。

E1 红线：LLM 候选校验（agent/recovery._llm_stage）与 Experience 运行时
Guard 是**同一套实现**——两者都调 `guard_candidate` 共享链，不是平行维护
的第二套规则。红线测试：同一输入下两条路径的 (outcome, reason,
record_as_sample) 逐位一致（见 tests/unit/test_runtime_guard.py）。

4.7 表「什么算一次样本」落成 `record_as_sample` 矩阵（设计 5 节伪代码）：

  | 判定                          | outcome | record_as_sample |
  |-------------------------------|---------|------------------|
  | SCREEN_UNKNOWN（当前屏未知）  | MISS    | False            |
  | SCREEN_MISMATCH / 未登记      | MISS    | False            |
  | TARGET_NOT_FOUND（0 匹配）    | BLOCK   | True             |
  | TARGET_AMBIGUOUS（≥2 匹配）   | BLOCK   | True             |
  | TYPE_MISMATCH                 | BLOCK   | True             |
  | RISK_BLOCKED（risk != LOW）   | BLOCK   | False            |
  | SECURITY_BLOCKED（10.1 Guard）| BLOCK   | False            |
  | 全部通过                      | EXECUTE | True             |

理由（4.7 原文语义）：Screen 不匹配/风险拦截 =「此 Experience 此刻本不
适用/不被允许」，不是「用了但错了」——不计入统计；唯一性/类型/执行失败
= 「用了但错了」——计入失败样本。

E2：effective_risk 由调用方按 P1 规则算好传入（`compute_effective_risk`
即 `executor.policy.effective_risk` 的 re-export，max(step/element/screen/
env)，不信任 LLM 自报）；链内 fail-closed——None（无可证明的风险）按
最高处理，与 9.3-4 定档一致。

顺序（设计 5 节 + 9.3 定档合并）：SCREEN_UNKNOWN → confidence（LLM 专属，
零设备成本）→ SCREEN_MISMATCH（纯 Repository 比对，零设备成本）→ 数量
（设备）→ 类型（设备）→ risk → 10.1 Guard → EXECUTE。单故障注入下每项
各触发对应 reason；多故障同时命中时 fail-fast 序为实现细节（P2-02 评审
同款定档）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Literal

from pydantic import BaseModel, ConfigDict

from executor.policy import effective_risk as compute_effective_risk
from executor.policy import Risk

__all__ = [
    "GuardResult", "RuntimeContext", "guard_candidate",
    "experience_runtime_guard", "experience_locator",
    "compute_effective_risk",
    "GUARD_REASON_TO_LLM_FAILURE",
]


class GuardResult(BaseModel):
    """设计 5 节的判定结果（frozen——判定是值，不是可变票据）。"""

    model_config = ConfigDict(frozen=True)

    outcome: Literal["EXECUTE", "MISS", "BLOCK"]
    reason: str | None = None
    record_as_sample: bool


@dataclass(frozen=True)
class RuntimeContext:
    """设计 5 节 ctx。effective_risk 由调用方按 E2 规则算好传入。"""

    current_screen: str | None
    expected_type: str | None = None
    effective_risk: Risk | None = None
    action: str = "tap"
    element_id: str | None = None
    screen_fingerprint: str | None = None


# LLM 侧 failure_type 映射（recovery._llm_stage 消费；单一真值源）
GUARD_REASON_TO_LLM_FAILURE: dict[str, str] = {
    "SCREEN_UNKNOWN": "LLM_TARGET_SCREEN_MISMATCH",
    "SCREEN_MISMATCH": "LLM_TARGET_SCREEN_MISMATCH",
    "TARGET_UNREGISTERED": "LLM_TARGET_SCREEN_MISMATCH",
    "TARGET_NOT_FOUND": "LLM_TARGET_NOT_FOUND",
    "TARGET_AMBIGUOUS": "LLM_TARGET_AMBIGUOUS",
    "TYPE_MISMATCH": "LLM_TARGET_TYPE_MISMATCH",
    "RISK_BLOCKED": "LLM_RISK_BLOCKED",
    "SECURITY_BLOCKED": "SECURITY_BLOCKED",
    "LLM_LOW_CONFIDENCE": "LLM_LOW_CONFIDENCE",
}


def _norm_type(t: str | None) -> str:
    """XCUIElementTypeButton ↔ button（9.3-2 定档：剥前缀 + 小写）。"""
    return str(t or "").replace("XCUIElementType", "").strip().lower()


def guard_candidate(
    *,
    current_screen: str | None,
    candidate_screen: str | None,
    find: Callable[[], object],
    expected_type: str | None = None,
    effective_risk: Risk | None = None,
    policy_check: Callable[[], None] | None = None,
    confidence: float | None = None,
    min_confidence: float | None = None,
) -> GuardResult:
    """共享校验链（E1）。LLM 候选与 Experience 运行时 Guard 的唯一规则体。

    参数：
      current_screen   当前屏（None = 未知，Screen 识别失败）；
      candidate_screen 候选登记屏（None = 未登记/无法证明 → fail-closed）；
      find             执行候选定位的 callable（返回元素或元素列表；
                       异常 = 0 匹配）；
      expected_type    期望元素类型（None/空 = 跳过类型校验）；
      effective_risk   E2 计算后的门控风险（None 按最高处理）；
                       必须是 Risk 枚举——字符串 "LOW" 会被 fail-closed
                       拦成 RISK_BLOCKED（review P3-1 类型地雷，入口断言）；
      policy_check     10.1 Guard 复检 callable（GuardViolation → 拦；
                       None = 跳过——执行路径 dispatch 处自会再过 Guard）；
      confidence/min_confidence  LLM 专属（Experience 路径不传）。
    """
    # 1. 当前屏未知 → MISS（4.7：Experience 此刻不适用，不计样本）
    if current_screen is None:
        return GuardResult(outcome="MISS", reason="SCREEN_UNKNOWN",
                           record_as_sample=False)

    # 2. confidence（LLM 专属；零设备成本，先于任何设备操作——9.3-5 定档）
    if confidence is not None and min_confidence is not None \
            and confidence < min_confidence:
        return GuardResult(outcome="BLOCK", reason="LLM_LOW_CONFIDENCE",
                           record_as_sample=False)

    # 3. Screen：候选必须登记在当前屏（fail-closed：未登记 = 无法证明）
    if candidate_screen != current_screen:
        reason = "SCREEN_MISMATCH" if candidate_screen else "TARGET_UNREGISTERED"
        return GuardResult(outcome="MISS", reason=reason,
                           record_as_sample=False)

    # 4. 数量：恰一（0 → NOT_FOUND；≥2 → AMBIGUOUS）——异常即 0 匹配
    try:
        found = find()
    except Exception:  # noqa: BLE001 — ElementNotFound/KeyError 等 = 没找到
        return GuardResult(outcome="BLOCK", reason="TARGET_NOT_FOUND",
                           record_as_sample=True)
    elements = found if isinstance(found, (list, tuple)) else [found]
    if len(elements) == 0:
        return GuardResult(outcome="BLOCK", reason="TARGET_NOT_FOUND",
                           record_as_sample=True)
    if len(elements) > 1:
        return GuardResult(outcome="BLOCK", reason="TARGET_AMBIGUOUS",
                           record_as_sample=True)
    element = elements[0]

    # 5. 类型：候选类型 == 期望类型（期望为空 = 跳过，既有行为）
    if expected_type:
        get_attr = getattr(element, "get_attribute", None)
        runtime_type = (get_attr("type") or "") if callable(get_attr) else ""
        if not runtime_type or _norm_type(runtime_type) != _norm_type(expected_type):
            return GuardResult(outcome="BLOCK", reason="TYPE_MISMATCH",
                               record_as_sample=True)

    # 6. 风险：effective_risk == LOW 才放行（None 按最高处理，fail-closed；
    #    4.7：风险拦截不是样本——「不被允许使用」≠「用了但错了」）。
    #    review P3-1 类型地雷：字符串 "LOW" 会静默拦成 RISK_BLOCKED（不炸、
    #    不报错、只掉成功率）——入口显式断言 Risk 枚举，接线错误当场显形。
    if effective_risk is not None and not isinstance(effective_risk, Risk):
        raise TypeError(
            f"effective_risk must be Risk | None, got "
            f"{type(effective_risk).__name__}:{effective_risk!r} "
            f"(string risk 会被静默 fail-closed——用 E2 的 compute_effective_risk)")
    if effective_risk is not Risk.LOW:
        return GuardResult(outcome="BLOCK", reason="RISK_BLOCKED",
                           record_as_sample=False)

    # 7. 10.1 Guard 复检（blocked_targets 等；不受 LLM/任何输出影响）
    if policy_check is not None:
        try:
            policy_check()
        except Exception as e:  # noqa: BLE001 — GuardViolation → 拦
            if type(e).__name__ == "GuardViolation":
                return GuardResult(outcome="BLOCK",
                                   reason="SECURITY_BLOCKED",
                                   record_as_sample=False)
            raise

    return GuardResult(outcome="EXECUTE", record_as_sample=True)


def experience_locator(exp: "Experience") -> list[dict]:
    """Experience 的候选策略 → Executor 的 `Locator`（`list[dict]`）。

    模型侧 `strategy` 是 `repository.loader.LocatorStrategy`（5.2 单一真值
    源），Executor 侧的 `Locator` 是 `[{"type","value"}]`（5.2 的执行形态）
    ——转换只此一处，两个消费方（Guard 的 find_all 与执行端的重发）共用，
    免得各自 `{"type": ..., "value": ...}` 一遍后漂移。
    """
    return [{"type": exp.strategy.type, "value": exp.strategy.value}]


def experience_runtime_guard(exp: "Experience", ctx: RuntimeContext,
                             executor,
                             policy_check: Callable[[], None] | None = None
                             ) -> GuardResult:
    """设计 5 节入口：Experience 的运行时守卫（Task 2.4 的
    try_experiences 逐候选消费本函数）。

    `policy_check`（10.1 Guard 复检）由调用方在**候选策略解析到登记元素**
    后传入，与 LLM 路径同一形态（`agent/recovery._llm_stage` 的
    `_policy_check`）。**不要以为执行端会兜**：`StepRunner.run_step` 的
    `guard.check` 只对**原步骤的原元素**跑一次，恢复重发走的
    `_dispatch_action` / `dispatch` 里没有任何 Guard（Task 2.4 评审 P3-1
    实锤）。所以不传 = `blocked_targets` 命中的候选可以经 Experience 路径
    执行、而同样的候选经 LLM 路径会被拦——同一候选因来路不同而结论不同。
    候选解析不到登记元素时传 None（与 LLM 路径同款 fail-open：10.1 的三条
    规则都依赖登记的 risk/screen/id，无登记就无从判定）。

    `executor` 只需提供 `find_all(Locator) -> list`（数量观测端；生产
    `Executor.find_all` / 引擎的 ctx 注入适配器都满足该形态）。
    """
    return guard_candidate(
        current_screen=ctx.current_screen,
        candidate_screen=exp.screen_id,
        find=lambda: executor.find_all(experience_locator(exp)),
        expected_type=ctx.expected_type,
        effective_risk=ctx.effective_risk,
        policy_check=policy_check,
    )


if TYPE_CHECKING:
    from experience.models import Experience
