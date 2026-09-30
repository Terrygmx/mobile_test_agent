"""Task 2.5 / P1-07：policy + guard 纯函数（设计 7.4 重试规则表 + 10.1 Guard）。

口径写死在本文件 docstring（测试按此钉，不按实现写）。全部纯函数、可脱离
设备单测（H18）。

7.4 幂等性推导 `effective_idempotency(step, element)`：
  1. 声明取更严格者：NON_IDEMPOTENT > UNKNOWN > IDEMPOTENT；
  2. 无任何声明：命中关键词启发式 → NON_IDEMPOTENT，否则 IDEMPOTENT；
  3. UNKNOWN 一律按 NON_IDEMPOTENT 处理（第 3 条是「处理方式」，返回值
     仍是 UNKNOWN；下游 retry 决策必须把 UNKNOWN 当 NON）。

7.4 风险推导 `effective_risk = max(step, element, screen, env, heuristic_bump)`：
  - heuristic_bump：未显式声明 element.risk 且命中关键词 → HIGH；
  - 显式声明（哪怕 LOW）则不启发（H17）。

7.4 重试规则表：
  | 失败阶段 | 有效幂等性 | 自动重试 |
  |---|---|---|
  | PRE_DISPATCH | 任意 | 允许 |
  | POST_DISPATCH | IDEMPOTENT | 允许，有界（max_attempts 来自配置） |
  | POST_DISPATCH | NON_IDEMPOTENT/UNKNOWN | **禁止**；查 postcondition |

10.1 Guard：
  - CRITICAL → SECURITY_BLOCKED，除非 sandbox 且 allow_in_sandbox=true；
  - production → 拒所有 HIGH/CRITICAL，且需 --allow-production；
  - blocked_targets（Screen/Element/Action 模式）直接拦截；
  - Guard 不受 testcase 字段与 LLM 输出影响。

H7（硬约束）：POST_DISPATCH 的非幂等步骤**绝不重试**。
"""

from __future__ import annotations

import pytest

from executor.policy import (
    Idempotency,
    RetryDecision,
    effective_idempotency,
    effective_risk,
    keyword_heuristic_hit,
    retry_decision,
    max_attempts_for,
    FailurePhase,
    _IDEMPOTENCY_STRICTNESS,
)
from executor.guard import (
    Guard,
    GuardViolation,
    EnvKind,
    BlockedTarget,
)
from testcase.schema import Risk


# --- 7.4-1 声明取更严格者 ---

@pytest.mark.parametrize("step_decl,element_decl,expected", [
    ("NON_IDEMPOTENT", "IDEMPOTENT", "NON_IDEMPOTENT"),
    ("IDEMPOTENT", "NON_IDEMPOTENT", "NON_IDEMPOTENT"),
    ("NON_IDEMPOTENT", "UNKNOWN", "NON_IDEMPOTENT"),
    ("UNKNOWN", "IDEMPOTENT", "UNKNOWN"),
    ("IDEMPOTENT", "IDEMPOTENT", "IDEMPOTENT"),
    ("UNKNOWN", "UNKNOWN", "UNKNOWN"),
    ("NON_IDEMPOTENT", "NON_IDEMPOTENT", "NON_IDEMPOTENT"),
])
def test_declared_idempotency_takes_stricter(step_decl, element_decl, expected):
    got = effective_idempotency(step_decl, element_decl)
    assert got.value == expected


def test_declared_strictness_order():
    """NON > UNKNOWN > IDEMPOTENT（7.4-1 的严格度序）。"""
    assert _IDEMPOTENCY_STRICTNESS["NON_IDEMPOTENT"] > \
        _IDEMPOTENCY_STRICTNESS["UNKNOWN"] > _IDEMPOTENCY_STRICTNESS["IDEMPOTENT"]


# --- 7.4-2 无声明 → 关键词启发式 ---

@pytest.mark.parametrize("element_id", [
    "submit_order", "pay_button", "payment_confirm", "order_delete",
    "checkout_now", "purchase_btn", "remove_item", "refund_btn",
    "transfer_funds", "withdraw_all", "confirm_pay",
])
def test_keyword_heuristic_marks_non_idempotent(element_id):
    """7.4-2：命中关键词 → NON_IDEMPOTENT（无声明时）。"""
    assert keyword_heuristic_hit(element_id)
    assert effective_idempotency(None, None, element_id=element_id) == \
        Idempotency.NON_IDEMPOTENT


@pytest.mark.parametrize("element_id", [
    "search_field", "username_input", "profile_header", "cell_alpha",
    "login_button", "back_button", "detail_title",
])
def test_safe_elements_default_idempotent(element_id):
    assert not keyword_heuristic_hit(element_id)
    assert effective_idempotency(None, None, element_id=element_id) == \
        Idempotency.IDEMPOTENT


def test_explicit_declaration_disables_heuristic_h17():
    """H17：显式声明优先于启发式。即使 id 含关键词，显式 IDEMPOTENT 也生效
    （lint 会提示补声明，但执行时以声明为准）。"""
    assert effective_idempotency("IDEMPOTENT", None,
                                 element_id="submit_order") == \
        Idempotency.IDEMPOTENT


def test_heuristic_looks_at_label_and_accessibility_id():
    """启发式作用于 element id / label / accessibility_id（7.4-2）。"""
    assert effective_idempotency(None, None,
                                 label="confirm_pay") == \
        Idempotency.NON_IDEMPOTENT
    assert effective_idempotency(None, None,
                                 accessibility_id="confirm_pay") == \
        Idempotency.NON_IDEMPOTENT


def test_heuristic_does_not_do_semantic_expansion():
    """7.4 的关键词表是**明文英文枚举**，不做语义扩展：中文「支付」不在
    表内 → 不命中 → 默认 IDEMPOTENT。这是刻意的（表可配置、行为可预测），
    代价是中文/同义表述的元素必须显式声明 idempotency——lint 会提示。
    扩表属于改 spec，不在 Executor 层偷偷做。"""
    assert effective_idempotency(None, None, label="支付") == \
        Idempotency.IDEMPOTENT


# --- 7.4 effective_risk ---

def test_effective_risk_is_max_of_declarations():
    assert effective_risk(step="LOW", element="HIGH", screen="MEDIUM",
                          env="LOW") == Risk.HIGH
    assert effective_risk(step="CRITICAL", element="LOW", screen="LOW",
                          env="LOW") == Risk.CRITICAL
    assert effective_risk(step="MEDIUM", element="MEDIUM", screen="LOW",
                          env="LOW") == Risk.MEDIUM


def test_undeclared_all_defaults_to_low():
    assert effective_risk(None, None, None, None) == Risk.LOW


def test_heuristic_bump_to_high_when_element_risk_undeclared():
    """7.4：未声明 element.risk 且命中关键词 → bump 到 HIGH。"""
    assert effective_risk(None, None, None, None,
                          element_id="pay_button") == Risk.HIGH


def test_explicit_element_risk_disables_heuristic_bump_h17():
    """H17：显式声明哪怕是 LOW，也不 bump。"""
    assert effective_risk(None, "LOW", None, None,
                          element_id="pay_button") == Risk.LOW
    assert effective_risk(None, "CRITICAL", None, None,
                          element_id="login_button") == Risk.CRITICAL


def test_heuristic_cannot_lower_an_existing_higher_risk():
    assert effective_risk(step="CRITICAL", element=None, screen=None, env=None,
                          element_id="pay_button") == Risk.CRITICAL


def test_env_risk_participates_in_max():
    assert effective_risk(None, None, None, "HIGH") == Risk.HIGH


# --- 7.4 重试规则表 ---

def test_pre_dispatch_always_retryable():
    """PRE_DISPATCH：动作从未发出 → 任何幂等性都允许重试。"""
    for idem in (Idempotency.IDEMPOTENT, Idempotency.NON_IDEMPOTENT,
                 Idempotency.UNKNOWN):
        d = retry_decision(FailurePhase.PRE_DISPATCH, idem)
        assert d.allowed, f"{idem} 在 PRE_DISPATCH 应可重试"


def test_post_dispatch_idempotent_retryable_bounded():
    d = retry_decision(FailurePhase.POST_DISPATCH, Idempotency.IDEMPOTENT)
    assert d.allowed and d.bounded
    # 可重试就不需要走 postcondition 分支（字段名是 check_* 不是 requires_*：
    # 「requires」会被误读成「必须提供 postcondition」）
    assert d.check_postcondition is False


def test_post_dispatch_non_idempotent_forbidden_h7():
    """H7：POST_DISPATCH 的非幂等步骤绝不重试（H7 是硬约束）。"""
    for idem in (Idempotency.NON_IDEMPOTENT, Idempotency.UNKNOWN):
        d = retry_decision(FailurePhase.POST_DISPATCH, idem)
        assert not d.allowed, f"{idem} 在 POST_DISPATCH 必须禁重试"
        assert d.failure_type_when_not_allowed == "ACTION_OUTCOME_UNKNOWN"


def test_post_dispatch_non_idempotent_with_postcondition_checks_it():
    """7.4 表格最后一行：有 postcondition → 查它 → RECOVERED(postcondition)；
    不成立或无 postcondition → FAIL(ACTION_OUTCOME_UNKNOWN)。"""
    d = retry_decision(FailurePhase.POST_DISPATCH,
                       Idempotency.NON_IDEMPOTENT,
                       has_postcondition=True)
    assert not d.allowed, "有 postcondition 也不等于可以重发动作"
    assert d.check_postcondition is True
    assert d.recovered_kind_if_postcondition_holds == "postcondition"


def test_post_dispatch_non_idempotent_without_postcondition_fails_directly():
    d = retry_decision(FailurePhase.POST_DISPATCH,
                       Idempotency.NON_IDEMPOTENT,
                       has_postcondition=False)
    assert not d.allowed and d.check_postcondition is False


def test_retry_decision_is_pure_and_repeatable():
    """H18：纯函数——同输入同输出，可反复调用无副作用。"""
    args = (FailurePhase.POST_DISPATCH, Idempotency.NON_IDEMPOTENT, True)
    first = retry_decision(*args)
    for _ in range(5):
        assert retry_decision(*args) == first


# --- max_attempts 来自配置不来自用例（7.4） ---

def test_max_attempts_from_config_not_testcase():
    """7.4：有界重试的 max_attempts **来自配置**，不来自用例。"""
    assert max_attempts_for(retryable=True, config_max_attempts=3) == 3
    # 非重试场景返回 0/None，不给「重试次数」这个东西
    assert max_attempts_for(retryable=False, config_max_attempts=3) == 0


def test_max_attempts_defaults_when_config_absent():
    assert max_attempts_for(retryable=True, config_max_attempts=None) >= 1


# --- 10.1 Guard ---

def _ctx(risk="LOW", screen="LoginView", element="login_button",
         action="tap", data_class="NORMAL"):
    from executor.guard import GuardContext
    return GuardContext(risk=Risk[risk], screen_id=screen,
                        element_id=element, action=action,
                        data_class=data_class)


def test_guard_allows_low_risk_in_sandbox():
    g = Guard(env_kind=EnvKind.SANDBOX)
    g.check(_ctx(risk="LOW"))  # 不抛 = 放行


def test_guard_blocks_critical_by_default():
    g = Guard(env_kind=EnvKind.SANDBOX)
    with pytest.raises(GuardViolation) as ei:
        g.check(_ctx(risk="CRITICAL"))
    assert ei.value.failure_type == "SECURITY_BLOCKED"


def test_guard_allows_critical_in_sandbox_with_explicit_allow():
    """10.1 唯一例外：env.kind == sandbox 且元素显式 allow_in_sandbox: true。"""
    g = Guard(env_kind=EnvKind.SANDBOX,
              allow_in_sandbox={"pay_button"})
    g.check(_ctx(risk="CRITICAL", element="pay_button"))


def test_sandbox_allow_does_not_apply_to_other_elements():
    g = Guard(env_kind=EnvKind.SANDBOX,
              allow_in_sandbox={"pay_button"})
    with pytest.raises(GuardViolation):
        g.check(_ctx(risk="CRITICAL", element="delete_button"))


def test_sandbox_allow_does_not_apply_outside_sandbox():
    """allow_in_sandbox 只在 sandbox 生效；staging 上写了也不放行。"""
    g = Guard(env_kind=EnvKind.STAGING, allow_in_sandbox={"pay_button"})
    with pytest.raises(GuardViolation):
        g.check(_ctx(risk="CRITICAL", element="pay_button"))


def test_guard_blocks_high_in_production():
    g = Guard(env_kind=EnvKind.PRODUCTION, allow_production=False)
    with pytest.raises(GuardViolation) as ei:
        g.check(_ctx(risk="HIGH"))
    assert ei.value.failure_type == "SECURITY_BLOCKED"


def test_production_requires_explicit_allow_flag():
    """10.1：production 拒绝 HIGH/CRITICAL，且启动时需 --allow-production。
    给了 flag 不代表放行 HIGH —— 语义是「允许在 production 跑」，
    但 HIGH/CRITICAL 动作仍按各自规则拦截。"""
    g = Guard(env_kind=EnvKind.PRODUCTION, allow_production=True)
    g.check(_ctx(risk="LOW"))  # LOW 可跑
    with pytest.raises(GuardViolation):
        g.check(_ctx(risk="HIGH"))


def test_critical_still_blocked_in_production_even_with_allow_flag():
    g = Guard(env_kind=EnvKind.PRODUCTION, allow_production=True)
    with pytest.raises(GuardViolation):
        g.check(_ctx(risk="CRITICAL"))


def test_production_without_allow_flag_blocks_everything():
    g = Guard(env_kind=EnvKind.PRODUCTION, allow_production=False)
    with pytest.raises(GuardViolation) as ei:
        g.check(_ctx(risk="LOW"))
    assert "production" in str(ei.value).lower()


@pytest.mark.parametrize("pattern,element,screen,action,should_block", [
    ("element:delete_*", "delete_button", "ProfileView", "tap", True),
    ("element:delete_*", "login_button", "ProfileView", "tap", False),
    ("screen:PaymentView", "pay_now", "PaymentView", "tap", True),
    ("screen:PaymentView", "pay_now", "HomeView", "tap", False),
    ("action:swipe", "cell_alpha", "HomeView", "swipe", True),
    ("action:swipe", "cell_alpha", "HomeView", "tap", False),
    ("PayButton", "PayButton", "HomeView", "tap", True),
])
def test_blocked_targets_patterns(pattern, element, screen, action,
                                 should_block):
    """10.1：blocked_targets 是 Screen/Element/Action 模式。"""
    g = Guard(env_kind=EnvKind.SANDBOX,
              blocked_targets=[BlockedTarget.parse(pattern)])
    ctx = _ctx(element=element, screen=screen, action=action)
    if should_block:
        with pytest.raises(GuardViolation) as ei:
            g.check(ctx)
        assert ei.value.failure_type == "SECURITY_BLOCKED"
    else:
        g.check(ctx)


def test_blocked_target_parse_supports_three_kinds():
    assert BlockedTarget.parse("element:x").kind == "element"
    assert BlockedTarget.parse("screen:Y").kind == "screen"
    assert BlockedTarget.parse("action:input").kind == "action"
    assert BlockedTarget.parse("PlainName").kind == "element"


def test_guard_violation_carries_reason_not_just_bool():
    """报错要能解释为什么拦（报告要展示，排查需要）。"""
    g = Guard(env_kind=EnvKind.SANDBOX)
    with pytest.raises(GuardViolation) as ei:
        g.check(_ctx(risk="CRITICAL"))
    v = ei.value
    assert v.failure_type == "SECURITY_BLOCKED"
    assert "CRITICAL" in v.reason
    assert v.risk == Risk.CRITICAL


def test_guard_ignores_testcase_level_override():
    """10.1：Guard 不受 testcase 字段影响——用例自称 LOW 也拦。"""
    g = Guard(env_kind=EnvKind.SANDBOX)
    ctx = _ctx(risk="CRITICAL")
    # 模拟用例想通过额外字段绕过：ctx 的 risk 由确定性推导得出，
    # testcase 无法写入——这里断言 GuardContext 没有 testcase 覆盖入口
    assert not hasattr(ctx, "testcase_override")
    with pytest.raises(GuardViolation):
        g.check(ctx)


def test_guard_is_pure_repeatable_h18():
    g = Guard(env_kind=EnvKind.SANDBOX)
    ctx = _ctx(risk="LOW")
    for _ in range(5):
        g.check(ctx)


# --- 交叉：policy 推导结果喂给 guard ---

def test_heuristic_bumped_high_is_blocked_in_production():
    """端到端纯函数链：pay_button 无声明 → bump HIGH → production 拦。"""
    risk = effective_risk(None, None, None, None, element_id="pay_button")
    assert risk == Risk.HIGH
    g = Guard(env_kind=EnvKind.PRODUCTION, allow_production=True)
    with pytest.raises(GuardViolation):
        g.check(_ctx(risk=risk.name, element="pay_button"))


def test_explicit_low_declaration_lets_pay_button_through_production():
    """H17 端到端：显式 LOW → 不 bump → production 放行。"""
    risk = effective_risk(None, "LOW", None, None, element_id="pay_button")
    assert risk == Risk.LOW
    g = Guard(env_kind=EnvKind.PRODUCTION, allow_production=True)
    g.check(_ctx(risk=risk.name, element="pay_button"))


def test_unknown_idempotency_treated_as_non_idempotent_in_retry():
    """7.4-3：UNKNOWN 一律按 NON_IDEMPOTENT 处理。"""
    d = retry_decision(FailurePhase.POST_DISPATCH, Idempotency.UNKNOWN)
    assert not d.allowed
    assert d.failure_type_when_not_allowed == "ACTION_OUTCOME_UNKNOWN"