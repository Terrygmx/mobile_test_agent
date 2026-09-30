"""policy — 7.4 幂等性/风险推导 + 重试规则表（纯函数，H18）。

**全部纯函数，可脱离设备单测**（H18）。这是设计明确要求的：风险与幂等性
只由确定性信息推导（4），LLM 绝不参与（H4）。

三条纪律：
  1. UNKNOWN **一律按 NON_IDEMPOTENT 处理**（7.4-3）——返回值仍是 UNKNOWN
     （保留声明原貌供报告展示），但下游 retry 决策必须当 NON。
  2. 显式声明优先于启发式（H17）。哪怕显式声明 IDEMPOTENT / LOW，只要命中
     关键词也不 bump——lint 会提示补声明，但执行以声明为准。
  3. 重试规则表的 `max_attempts` **来自配置不来自用例**（7.4）。用例自己要求
     重试次数，等于让用例给自己放宽安全边界。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from testcase.schema import Idempotency, Risk

# 7.4-2 关键词表（可配置）。作用于 element id / label / accessibility_id。
# 注意这是**明文英文枚举**，不做语义扩展——中文/同义表述不在表内就不命中，
# 那些元素必须显式声明 idempotency（lint 提示）。扩表属于改 spec。
RISK_KEYWORDS: tuple[str, ...] = (
    "submit", "pay", "payment", "order", "checkout", "purchase",
    "delete", "remove", "refund", "transfer", "withdraw", "confirm_pay",
)

# 7.4-1 严格度序：NON > UNKNOWN > IDEMPOTENT
_IDEMPOTENCY_STRICTNESS = {
    "IDEMPOTENT": 0,
    "UNKNOWN": 1,
    "NON_IDEMPOTENT": 2,
}

# 默认有界重试次数（7.4：来自配置；此处为配置缺省）
DEFAULT_MAX_ATTEMPTS = 2


class FailurePhase(str, enum.Enum):
    """7.1 阶段判定。find 与 act 是两次独立调用，这是前提。"""

    PRE_DISPATCH = "PRE_DISPATCH"     # 动作从未发出
    POST_DISPATCH = "POST_DISPATCH"   # 已发出，结果未知


@dataclass(frozen=True)
class RetryDecision:
    """重试规则表一行的求值结果。"""

    allowed: bool
    bounded: bool
    check_postcondition: bool
    failure_type_when_not_allowed: str | None = None
    recovered_kind_if_postcondition_holds: str | None = None
    reason: str = ""


def keyword_heuristic_hit(*texts: str | None) -> bool:
    """任一文本（小写后）命中 RISK_KEYWORDS 子串即 True。"""
    for text in texts:
        if not text:
            continue
        low = str(text).lower()
        if any(kw in low for kw in RISK_KEYWORDS):
            return True
    return False


def _as_idem(value) -> Idempotency | None:
    if value is None:
        return None
    if isinstance(value, Idempotency):
        return value
    return Idempotency[str(value)]


def effective_idempotency(step_decl, element_decl,
                          element_id: str | None = None,
                          label: str | None = None,
                          accessibility_id: str | None = None,
                          keywords=RISK_KEYWORDS) -> Idempotency:
    """7.4-1/2/3 幂等性推导。

    1. 有声明 → 取更严格者；
    2. 无声明 → 关键词启发式命中则 NON，否则 IDEMPOTENT；
    3. UNKNOWN 不在此改写（保留原值），由 retry_decision 当 NON 处理。
    """
    step = _as_idem(step_decl)
    element = _as_idem(element_decl)
    if step is not None or element is not None:
        return max((d for d in (step, element) if d is not None),
                   key=lambda d: _IDEMPOTENCY_STRICTNESS[d.value])
    # 2. 无任何声明 → 启发式
    table = RISK_KEYWORDS if keywords is None else tuple(keywords)
    if keyword_heuristic_hit(element_id, label, accessibility_id):
        return Idempotency.NON_IDEMPOTENT
    del table  # keyword_heuristic_hit 用模块级表；显式传参留给未来可配置化
    return Idempotency.IDEMPOTENT


def effective_risk(step=None, element=None, screen=None, env=None,
                   element_id: str | None = None,
                   label: str | None = None,
                   accessibility_id: str | None = None,
                   keywords=RISK_KEYWORDS) -> Risk:
    """7.4：`max(step, element, screen, env, heuristic_bump)`。

    `heuristic_bump`：仅当 **element.risk 未显式声明**且命中关键词 → HIGH。
    显式声明（哪怕 LOW）则不 bump（H17）。启发**只往上加不往下压**。
    """
    def _r(v) -> Risk | None:
        if v is None:
            return None
        return v if isinstance(v, Risk) else Risk[str(v)]

    bump = None
    if element is None and keyword_heuristic_hit(
            element_id, label, accessibility_id):
        bump = Risk.HIGH

    candidates = [c for c in (_r(step), _r(element), _r(screen), _r(env), bump)
                  if c is not None]
    if not candidates:
        return Risk.LOW
    return max(candidates, key=lambda r: r.value)


def retry_decision(phase: FailurePhase, idempotency: Idempotency,
                   has_postcondition: bool = False) -> RetryDecision:
    """7.4 重试规则表（纯函数求值）。

    | 失败阶段 | 有效幂等性 | 允许的自动重试 |
    |---|---|---|
    | PRE_DISPATCH | 任意 | 允许 |
    | POST_DISPATCH | IDEMPOTENT | 允许，有界 |
    | POST_DISPATCH | NON_IDEMPOTENT / UNKNOWN | **禁止**；查 postcondition |

    H7 是硬约束：POST_DISPATCH 的非幂等步骤绝不重试。注意「有 postcondition」
    **不等于**「可以重发动作」——它只提供一条确认结果的途径。
    """
    phase = phase if isinstance(phase, FailurePhase) else FailurePhase(phase)
    # 7.4-3：UNKNOWN 按 NON_IDEMPOTENT 处理
    effective_non = idempotency in (Idempotency.NON_IDEMPOTENT,
                                    Idempotency.UNKNOWN)

    if phase is FailurePhase.PRE_DISPATCH:
        # 动作从未发出 → 任何幂等性都可重试（仍受 Guard 风险门控）
        return RetryDecision(
            allowed=True, bounded=True, check_postcondition=False,
            reason="PRE_DISPATCH：动作从未发出，允许重试")

    if effective_non:
        if has_postcondition:
            return RetryDecision(
                allowed=False, bounded=False, check_postcondition=True,
                failure_type_when_not_allowed="ACTION_OUTCOME_UNKNOWN",
                recovered_kind_if_postcondition_holds="postcondition",
                reason="H7：POST_DISPATCH 非幂等禁止重试；改查 postcondition")
        return RetryDecision(
            allowed=False, bounded=False, check_postcondition=False,
            failure_type_when_not_allowed="ACTION_OUTCOME_UNKNOWN",
            reason="H7：POST_DISPATCH 非幂等且无 postcondition，禁止重试")

    return RetryDecision(
        allowed=True, bounded=True, check_postcondition=False,
        reason="POST_DISPATCH + IDEMPOTENT：允许有界重试")


def max_attempts_for(retryable: bool, config_max_attempts: int | None = None
                     ) -> int:
    """有界重试的次数上限。**来自配置不来自用例**（7.4）。

    不可重试 → 0（不给「重试次数」这个东西，避免调用方误用）。
    """
    if not retryable:
        return 0
    if config_max_attempts is None:
        return DEFAULT_MAX_ATTEMPTS
    return max(1, int(config_max_attempts))
