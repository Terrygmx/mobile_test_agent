"""Task 1.1（P1-01）TestCase Schema 0.1 失败测试（设计 6.1/6.2/6.3）。

判定口径：
  - TargetRef 字符串糖："login_button" → element；"screen:HomeView" → screen；
  - 未知字段一律 ValidationError（extra="forbid"，设计 6.3 末行）；
  - schema_version 不认识 → 明确报错（按版本分发 parser，6.3 末注）；
  - wait 条件 `active` 仅允许 screen 目标（设计 6.2 条件清单）；
  - 用例级 `retry` 被拒（H7：重试归 Executor Policy，用例作者不可绕过）。
"""
import pytest
from pydantic import ValidationError

from testcase.schema import (
    ActionStep,
    AssertionStep,
    Idempotency,
    Risk,
    TargetRef,
    TestCase,
    WaitStep,
)
from testcase.loader import load_testcase, load_testcase_from_dict


# --- TargetRef 语法糖（设计 6.3 model_validator(mode="before")） ---

def test_target_ref_plain_str_is_element():
    assert TargetRef.model_validate("login_button") == TargetRef(
        type="element", id="login_button"
    )


def test_target_ref_screen_sugar():
    assert TargetRef.model_validate("screen:HomeView") == TargetRef(
        type="screen", id="HomeView"
    )


def test_target_ref_explicit_dict():
    ref = TargetRef.model_validate({"type": "screen", "id": "LoginView"})
    assert ref.type == "screen" and ref.id == "LoginView"


# --- extra="forbid"（设计 6.3：未知字段一律报错） ---

def test_unknown_step_field_rejected():
    with pytest.raises(ValidationError):
        ActionStep.model_validate(
            {"action": "tap", "target": "login_button", "typo_field": 1}
        )


def test_unknown_testcase_field_rejected():
    with pytest.raises(ValidationError):
        TestCase.model_validate(
            {
                "schema_version": "0.2",
                "id": "t",
                "name": "t",
                "steps": [],
                "unknown_key": "x",
            }
        )


# --- H7：用例级 retry 不可声明（重试归 Executor Policy） ---

def test_retry_key_forbidden():
    with pytest.raises(ValidationError):
        TestCase.model_validate(
            {"schema_version": "0.2", "id": "t", "name": "t", "retry": 2, "steps": []}
        )


# --- schema_version 分发（不认识 → 明确报错） ---

def test_unknown_schema_version_errors():
    with pytest.raises(ValueError, match="schema_version"):
        load_testcase_from_dict(
            {"schema_version": "9.9", "id": "x", "name": "x", "steps": []}
        )


# --- wait 条件矩阵（设计 6.2）：active 仅限 screen 目标 ---

def test_wait_condition_active_screen_only():
    # element 目标 + active → 拒绝
    with pytest.raises(ValidationError):
        WaitStep.model_validate(
            {"wait_for": {"target": "login_button", "condition": "active"}}
        )
    # screen 目标 + active → 合法
    step = WaitStep.model_validate(
        {"wait_for": {"target": "screen:HomeView", "condition": "active"}}
    )
    assert step.wait_for.target.type == "screen"


def test_wait_condition_unknown_rejected():
    with pytest.raises(ValidationError):
        WaitStep.model_validate(
            {"wait_for": {"target": "login_button", "condition": "hover"}}
        )


# --- 枚举（设计 6.3） ---

def test_idempotency_enum_values():
    assert Idempotency.IDEMPOTENT.value == "IDEMPOTENT"
    assert Idempotency.NON_IDEMPOTENT.value == "NON_IDEMPOTENT"
    assert Idempotency.UNKNOWN.value == "UNKNOWN"


def test_risk_enum_is_int_1_to_4():
    assert [r.value for r in Risk] == [1, 2, 3, 4]


def test_action_step_defaults_per_design():
    step = ActionStep.model_validate({"action": "tap", "target": "login_button"})
    assert step.sensitive is False
    assert step.idempotency is None      # 未声明 → 走 7.4 默认推导
    assert step.risk is None
    assert step.postcondition is None


# --- 端到端最小用例：三类 step 混排 + discriminated 解析 ---

def test_minimal_case_roundtrip():
    case = TestCase.model_validate(
        {
            "schema_version": "0.2",
            "id": "t_union",
            "name": "union dispatch",
            "steps": [
                {"action": "tap", "target": "login_button"},
                {"wait_for": {"target": "screen:HomeView", "condition": "active"}},
                {"assertion": {"target": "screen:HomeView", "condition": "exists"}},
            ],
        }
    )
    assert isinstance(case.steps[0], ActionStep)
    assert isinstance(case.steps[1], WaitStep)
    assert isinstance(case.steps[2], AssertionStep)


def test_load_testcase_from_dict_minimal():
    case = load_testcase_from_dict(
        {"schema_version": "0.2", "id": "t1", "name": "smoke login", "steps": []}
    )
    assert case.id == "t1" and case.suite is None and case.tags == []


# --- R5-1 修复验证：官方示例契约（§6 示例 + §6.2 条件矩阵） ---

def test_wait_text_condition_with_expected():
    step = WaitStep.model_validate(
        {
            "wait_for": {
                "target": "status_label",
                "condition": "text_contains",
                "expected": "登录成功",
                "polling_interval": 0.3,
            }
        }
    )
    assert step.wait_for.polling_interval == 0.3
    assert step.wait_for.expected == "登录成功"


def test_wait_text_condition_without_expected_rejected():
    with pytest.raises(ValidationError):
        WaitStep.model_validate(
            {"wait_for": {"target": "status_label", "condition": "text_equals"}}
        )


def test_design_section6_example_passes_validation():
    """设计 §6 官方示例（R5-1 修复后契约）必须能过 strict schema。"""
    case = load_testcase_from_dict(
        {
            "schema_version": "0.2",
            "id": "login_001",
            "name": "用户登录",
            "suite": "smoke",
            "tags": ["login", "smoke"],
            "precondition": {"reset": "RESET_STATE"},
            "steps": [
                {"action": "launch_app"},
                {"action": "tap", "target": "login_button"},
                {
                    "action": "input",
                    "target": "username_field",
                    "value": "${TEST_USERNAME}",
                },
                {
                    "action": "input",
                    "target": "password_field",
                    "value": "${TEST_PASSWORD}",
                    "sensitive": True,
                },
                {
                    "action": "tap",
                    "target": "submit_login_button",
                    "idempotency": "IDEMPOTENT",
                },
                {
                    "wait_for": {
                        "target": {"type": "screen", "id": "HomeView"},
                        "condition": "active",
                        "timeout": 10,
                        "polling_interval": 0.3,
                    }
                },
                {
                    "assertion": {
                        "condition": "exists",
                        "target": {"type": "screen", "id": "HomeView"},
                    }
                },
            ],
            "cleanup": {"reset": "RESET_STATE", "failure_policy": "ABORT_SUITE"},
        }
    )
    assert case.steps[5].wait_for.polling_interval == 0.3  # type: ignore[union-attr]
    assert case.steps[6].assertion.condition == "exists"  # type: ignore[union-attr]


# --- R5-2 修复验证：文件级入口不被 strict 旁路 ---

def test_load_testcase_routes_schema_versioned_file_to_strict(tmp_path):
    """带 schema_version 的 YAML 走 strict：未知字段必须被拒（不能回落松散模型）。"""
    p = tmp_path / "case.yaml"
    p.write_text(
        "schema_version: '0.2'\n"
        "id: t_strict\n"
        "name: strict route\n"
        "steps:\n"
        "  - action: tap\n"
        "    target: login_button\n"
        "    bogus_key: 1\n"
    )
    with pytest.raises(ValidationError):
        load_testcase(p)


def test_load_testcase_p0_file_still_loose(tmp_path):
    """P0 旧格式（无 schema_version）仍走松散模型（回归底线）。"""
    p = tmp_path / "p0case.yaml"
    p.write_text(
        "id: p0_case\n"
        "name: p0 loose\n"
        "steps:\n"
        "  - action: tap\n"
        "    target: login_button\n"
    )
    case = load_testcase(p)
    assert case.id == "p0_case" and case.steps[0].target == "login_button"
