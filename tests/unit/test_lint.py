"""Task 1.3（P1-01）mta lint 失败测试（设计 6.4，退出码 3 的前置门）。

判定口径（设计 6.4 逐条）：
  - schema 非法（YAML 不可解析 / schema_version 缺失或未知 / extra=forbid 字段）→ ERROR；
  - target 均能解析（unknown_target）且无歧义（ambiguous_target）→ ERROR（4.1 不允许运行时猜）；
  - `${VAR}` 均可由 SecretProvider 解析（H9：密钥只经 SecretProvider）→ ERROR；
  - 无 sleep（H11：Appium implicit wait=0，用例禁止 sleep）→ ERROR；
  - `active` 仅限 screen target（6.2）→ ERROR（schema 层已拦，lint 对原始 dict 再拦一层）；
  - 非幂等步骤缺 postcondition → **WARNING**（仅警告，退出码仍 0）；
  - CLI 退出码：有 ERROR → 3，仅 WARNING / 干净 → 0。
"""
from __future__ import annotations

import pytest

from environment.secrets import SecretProvider
from repository.loader import load_element_dir, load_screen_dir
from repository.resolver import Repository
from testcase.schema import parse_testcase_dict
from testcase.lint import LintIssue, Severity, lint, max_severity, lint_exit_code


# --- 夹具 ---

GEN_ELEMENTS = """\
kind: element
id: login_button
screen: LoginView
type: button
strategies:
  - {type: accessibility_id, value: login_button, origin: source}
metadata:
  risk: LOW
  idempotency: IDEMPOTENT
  data_class: PUBLIC
"""

GEN_SCREENS = """\
kind: screen
id: LoginView
marker: screen.LoginView
kind_hint: page
"""


class DictSecrets(SecretProvider):
    """测试用 SecretProvider：dict 驱动（真实链路用 EnvSecretProvider）。"""

    def __init__(self, known: set[str] | None = None) -> None:
        self.known = known or set()

    def get(self, key: str) -> str:
        if key not in self.known:
            raise KeyError(f"secret {key} not found")
        return "x"


def _repo() -> Repository:
    return Repository(
        generated_elements=load_element_dir([("LoginView.yaml", GEN_ELEMENTS)]),
        generated_screens=load_screen_dir([("LoginView.yaml", GEN_SCREENS)]),
    )


def _tc(**overrides):
    base = {
        "schema_version": "0.2",
        "id": "t1",
        "name": "t",
        "steps": [{"action": "tap", "target": "login_button"}],
    }
    base.update(overrides)
    return base


# --- 6.4：schema 非法 → ERROR ---

def test_schema_invalid_yaml_reports_error():
    issues = lint([{"schema_version": "0.2", "id": "t", "steps": "not-a-list"}], _repo(),
                  DictSecrets())
    assert any(i.code == "schema_invalid" and i.severity is Severity.ERROR for i in issues)


def test_schema_missing_version_reports_error():
    issues = lint([{"id": "t", "name": "t", "steps": []}], _repo(), DictSecrets())
    assert any(i.code == "schema_invalid" for i in issues)


def test_schema_unknown_version_reports_error():
    issues = lint([{**_tc(), "schema_version": "9.9"}], _repo(), DictSecrets())
    assert any(i.code == "schema_invalid" for i in issues)


def test_schema_valid_case_parses_clean():
    issues = lint([_tc()], _repo(), DictSecrets())
    assert issues == []


# --- 6.4：target 解析 + 歧义（4.1） ---

def test_unknown_target_reports_error():
    issues = lint([_tc(steps=[{"action": "tap", "target": "ghost_button"}])],
                  _repo(), DictSecrets())
    assert any(i.code == "unknown_target" and i.severity is Severity.ERROR for i in issues)


def test_ambiguous_target_reports_error():
    gen = load_element_dir([
        ("HomeView.yaml", """\
kind: element
id: confirm_button
screen: HomeView
type: button
strategies:
  - {type: accessibility_id, value: confirm_button, origin: source}
"""),
        ("SettingsView.yaml", """\
kind: element
id: confirm_button
screen: SettingsView
type: button
strategies:
  - {type: accessibility_id, value: settings_confirm, origin: source}
"""),
    ])
    repo = Repository(generated_elements=gen)
    issues = lint([_tc(steps=[{"action": "tap", "target": "confirm_button"}])],
                  repo, DictSecrets())
    assert any(i.code == "ambiguous_target" and i.severity is Severity.ERROR for i in issues)


# --- 6.4：${VAR} secret 解析（H9） ---

def test_unresolvable_secret_reports_error():
    tc = _tc(steps=[{"action": "input", "target": "login_button", "value": "${TEST_PASSWORD}"}])
    issues = lint([tc], _repo(), DictSecrets())
    assert any(i.code == "unknown_secret" and i.severity is Severity.ERROR for i in issues)


def test_resolvable_secret_no_issue():
    tc = _tc(steps=[{"action": "input", "target": "login_button", "value": "${TEST_PASSWORD}"}])
    issues = lint([tc], _repo(), DictSecrets(known={"TEST_PASSWORD"}))
    assert not any(i.code == "unknown_secret" for i in issues)


def test_plain_value_not_treated_as_secret():
    tc = _tc(steps=[{"action": "input", "target": "login_button", "value": "plain"}])
    assert not any(i.code == "unknown_secret" for i in lint([tc], _repo(), DictSecrets()))


# --- 6.4：无 sleep（H11） ---
# sleep 不是合法 action（schema 6.2 Literal 拦截）；lint 层对「原始 dict 直接构造」
# 与「绕过 Literal 的扩展 parser」双保险。

def test_sleep_action_reports_error():
    # sleep 不在 schema Literal 白名单 → schema_invalid（含 sleep 语义提示）
    issues = lint([_tc(steps=[{"action": "tap", "target": "login_button"},
                              {"action": "sleep", "timeout": 5}])],
                  _repo(), DictSecrets())
    codes = {i.code for i in issues}
    assert "schema_invalid" in codes or "sleep_forbidden" in codes
    # 有 ERROR 级
    assert max_severity(issues) is Severity.ERROR


def test_sleep_code_reported_when_schema_permits():
    """若后续 schema 放宽 action 白名单，lint 的 sleep_forbidden 仍兜底。"""
    from testcase.lint import _check_sleep
    parsed = parse_testcase_dict(_tc())  # 干净用例
    issues: list[LintIssue] = []
    _check_sleep([s.model_dump() for s in parsed.steps] + [{"action": "sleep"}],
                 parsed.id, issues)
    assert any(i.code == "sleep_forbidden" and i.severity is Severity.ERROR
               for i in issues)


# --- 6.4：active 仅限 screen（6.2；对原始 dict 再拦一层） ---
# schema 的 WaitSpec 校验器已拒绝 element+active；lint 层兜底直接检查原始 dict。

def test_active_on_element_reports_error():
    from testcase.lint import _check_active_scope
    issues: list[LintIssue] = []
    _check_active_scope([{"wait_for": {"target": "login_button", "condition": "active"}}],
                        "t1", issues)
    assert any(i.code == "active_on_element" and i.severity is Severity.ERROR
               for i in issues)
    # schema 层确实会拒绝（双重防线确认）
    with pytest.raises(Exception):
        parse_testcase_dict(_tc(steps=[{"wait_for": {"target": "login_button",
                                                     "condition": "active"}}]))


def test_active_on_screen_sugar_ok():
    """Task 1.6 回归：`screen:HomeView` 字符串语法糖是 screen target，
    不得误报 active_on_element（原实现 isinstance(str) 一刀切误拦真实用例）。"""
    from testcase.lint import _check_active_scope
    issues: list[LintIssue] = []
    _check_active_scope([{"wait_for": {"target": "screen:HomeView",
                                       "condition": "active"}}], "t1", issues)
    assert not any(i.code == "active_on_element" for i in issues)
    # 显式 dict 形式同样放行
    _check_active_scope([{"wait_for": {"target": {"type": "screen", "id": "HomeView"},
                                       "condition": "active"}}], "t1", issues)
    assert not any(i.code == "active_on_element" for i in issues)


# --- 6.4：非幂等缺 postcondition → WARNING ---

def test_non_idempotent_without_postcondition_warns():
    tc = _tc(steps=[{"action": "tap", "target": "login_button",
                     "idempotency": "NON_IDEMPOTENT"}])
    issues = lint([tc], _repo(), DictSecrets())
    assert any(i.code == "missing_postcondition" and i.severity is Severity.WARNING
               for i in issues)
    # 警告不产生退出码 3
    assert lint_exit_code([tc], _repo(), DictSecrets()) == 0


def test_non_idempotent_with_postcondition_no_warning():
    tc = _tc(steps=[{"action": "tap", "target": "login_button",
                     "idempotency": "NON_IDEMPOTENT",
                     "postcondition": {"target": "screen:LoginView", "condition": "exists"}}])
    assert not any(i.code == "missing_postcondition"
                   for i in lint([tc], _repo(), DictSecrets()))


def test_r7_2_unknown_without_postcondition_warns():
    """R7-2：设计 7.4「UNKNOWN 一律按 NON_IDEMPOTENT 处理」→ 缺 postcondition
    同样警告。"""
    tc = _tc(steps=[{"action": "tap", "target": "login_button",
                     "idempotency": "UNKNOWN"}])
    issues = lint([tc], _repo(), DictSecrets())
    assert any(i.code == "missing_postcondition" and i.severity is Severity.WARNING
               for i in issues)
    assert lint_exit_code([tc], _repo(), DictSecrets()) == 0


def test_r7_2_unknown_with_postcondition_no_warning():
    tc = _tc(steps=[{"action": "tap", "target": "login_button",
                     "idempotency": "UNKNOWN",
                     "postcondition": {"target": "screen:LoginView", "condition": "exists"}}])
    assert not any(i.code == "missing_postcondition"
                   for i in lint([tc], _repo(), DictSecrets()))


def test_idempotent_step_no_postcondition_check():
    assert not any(i.code == "missing_postcondition"
                   for i in lint([_tc()], _repo(), DictSecrets()))


# --- R10-2：runner 未消费的声明 fail-loud（Task 2.7 翻正记录）---

def test_r10_2_declared_postcondition_now_consumed():
    """Task 2.7 翻正：postcondition 已由 StepRunner.postcondition_checker
    真实执行（H7 闭环），declaration_not_consumed 不再拦。lint 现在只拦
    RUNNER_UNCONSUMED 里未来的新键——保持空集但保留机制。
    """
    issues = lint([_tc(steps=[{"action": "tap", "target": "login_button",
                               "postcondition": {"target": "screen:LoginView",
                                                 "condition": "active"}}])],
                  _repo(), DictSecrets())
    assert not any(i.code == "declaration_not_consumed" for i in issues)


def test_r10_2_no_declaration_no_issue():
    assert not any(i.code == "declaration_not_consumed"
                   for i in lint([_tc()], _repo(), DictSecrets()))


# --- R10-4：wait_for condition 白名单（runner 层，间接验证 schema 条件枚举） ---

def test_r10_4_wait_condition_enum():
    """schema 层 condition 是闭集 Literal；runner 白名单外 fail-loud 的行为
    由 runner 集成测试覆盖（真机 Gate），这里验证 text_contains 可过 schema。"""
    parsed = parse_testcase_dict(_tc(steps=[{"wait_for": {
        "target": "login_button", "condition": "text_contains",
        "expected": "x"}}]))
    assert parsed.steps[0].wait_for.condition == "text_contains"


# --- 退出码表 ---

def test_exit_code_error_is_3():
    issues = lint([_tc(steps=[{"action": "tap", "target": "ghost"}])], _repo(), DictSecrets())
    assert max_severity(issues) is Severity.ERROR
    assert lint_exit_code([_tc(steps=[{"action": "tap", "target": "ghost"}])],
                          _repo(), DictSecrets()) == 3


def test_exit_code_warning_only_is_0():
    tc = _tc(steps=[{"action": "tap", "target": "login_button",
                     "idempotency": "NON_IDEMPOTENT"}])
    issues = lint([tc], _repo(), DictSecrets())
    assert max_severity(issues) is Severity.WARNING
    assert lint_exit_code([tc], _repo(), DictSecrets()) == 0


def test_exit_code_clean_is_0():
    assert lint_exit_code([_tc()], _repo(), DictSecrets()) == 0


def test_parse_then_lint_on_pydantic_objects():
    """lint 接受 dict（loader 前置校验用）与 Pydantic TestCase（CLI 常态）两种输入。"""
    parsed = [parse_testcase_dict(_tc())]
    assert lint(parsed, _repo(), DictSecrets()) == []
