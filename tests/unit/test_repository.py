"""Task 1.2（P1-02）Repository resolver 失败测试（设计 5.1~5.5）。

判定口径：
  - resolve 三种 target 语法（4.1）：短名（全局唯一才允许）、限定名 `Screen.elem`、
    `screen:<Name>` → EffectiveScreen；
  - 5.3 合并规则：overrides > generated；replace 取代 / prepend+append 与 generated 合并；
  - 不能静默覆盖：合并结果每条策略保留 origin；override 值与 generated 冲突 →
    EffectiveElement.warnings 含 override_shadows_source；
  - 短名歧义：两个 Screen 下同名元素 → AmbiguousReferenceError（lint 场景可捕 LintIssue，
    不允许运行时猜，4.1）；
  - Screen 合并：overrides/screens 与 generated/screens 合并，marker/kind_hint 同规则。
"""
from __future__ import annotations

import pytest

from repository.loader import (
    RepositoryLoaderError,
    load_element_dir,
    load_screen_dir,
)
from repository.resolver import (
    AmbiguousReferenceError,
    EffectiveElement,
    EffectiveScreen,
    Repository,
    UnknownReferenceError,
)
from testcase.schema import ActionStep, TargetRef, TestCase


# --- 测试夹具：generated 定义（字符串 YAML，无需临时目录） ---

GENERATED_ELEMENTS_LOGIN = """\
kind: element
id: login_button
screen: LoginView
type: button
strategies:
  - {type: accessibility_id, value: login_button, origin: source,
     source_file: LoginView.swift, source_line: 42}
  - {type: predicate, value: "label == '登录'", origin: source}
metadata:
  risk: LOW
  idempotency: IDEMPOTENT
  data_class: PUBLIC
"""

GENERATED_ELEMENTS_CONFIRM_HOME = """\
kind: element
id: confirm_button
screen: HomeView
type: button
strategies:
  - {type: accessibility_id, value: confirm_button, origin: source,
     source_file: HomeView.swift, source_line: 10}
"""

GENERATED_ELEMENTS_CONFIRM_SETTINGS = """\
kind: element
id: confirm_button
screen: SettingsView
type: button
strategies:
  - {type: accessibility_id, value: settings_confirm, origin: source}
"""

GENERATED_SCREENS = """\
kind: screen
id: LoginView
marker: screen.LoginView
kind_hint: page
"""


def _repo(
    generated_elements: str = GENERATED_ELEMENTS_LOGIN,
    generated_screens: str = GENERATED_SCREENS,
    override_elements: str = "",
    override_screens: str = "",
    extra_generated_elements: str = "",
) -> Repository:
    """从字符串 YAML 构造 Repository（loader 低层入口直接吃 (filename, text) 对）。"""
    gen_elements = load_element_dir([("LoginView.yaml", generated_elements)])
    if extra_generated_elements:
        gen_elements.update(
            load_element_dir([("SettingsView.yaml", extra_generated_elements)])
        )
    gen_screens = load_screen_dir([("LoginView.yaml", generated_screens)])
    ov_elements = (
        load_element_dir([("LoginView.yaml", override_elements)])
        if override_elements
        else {}
    )
    ov_screens = (
        load_screen_dir([("LoginView.yaml", override_screens)])
        if override_screens
        else {}
    )
    return Repository(
        generated_screens=gen_screens,
        generated_elements=gen_elements,
        override_screens=ov_screens,
        override_elements=ov_elements,
    )


# --- resolve：三种 target 语法（4.1） ---

def test_resolve_short_name_unique():
    eff = _repo().resolve("login_button", build="b1")
    assert isinstance(eff, EffectiveElement)
    assert eff.id == "login_button" and eff.screen == "LoginView"
    assert eff.strategies[0].type == "accessibility_id"
    assert eff.strategies[0].value == "login_button"


def test_resolve_qualified_name():
    eff = _repo(generated_elements=GENERATED_ELEMENTS_CONFIRM_HOME).resolve(
        "HomeView.confirm_button", build="b1"
    )
    assert eff.id == "confirm_button" and eff.screen == "HomeView"


def test_resolve_screen_sugar():
    eff = _repo().resolve("screen:LoginView", build="b1")
    assert isinstance(eff, EffectiveScreen)
    assert eff.id == "LoginView" and eff.marker == "screen.LoginView"


def test_resolve_unknown_id_raises():
    with pytest.raises(UnknownReferenceError):
        _repo().resolve("no_such_element", build="b1")


def test_ambiguous_short_name_raises():
    """两个 Screen 下同名元素：短名歧义 → 明确报错（4.1 不允许运行时猜）。"""
    repo = _repo(
        generated_elements=GENERATED_ELEMENTS_CONFIRM_HOME,
        extra_generated_elements=GENERATED_ELEMENTS_CONFIRM_SETTINGS,
    )
    with pytest.raises(AmbiguousReferenceError):
        repo.resolve("confirm_button", build="b1")
    # 限定名仍可解析
    assert repo.resolve("SettingsView.confirm_button", build="b1").screen == "SettingsView"


# --- 5.3 合并规则：replace / prepend / append ---

OVERRIDE_REPLACE = """\
kind: element
id: login_button
screen: LoginView
mode: replace
strategies:
  - {type: accessibility_id, value: signin_button, origin: manual}
"""

OVERRIDE_PREPEND = """\
kind: element
id: login_button
screen: LoginView
mode: prepend
strategies:
  - {type: accessibility_id, value: signin_button, origin: manual}
"""

OVERRIDE_APPEND = """\
kind: element
id: login_button
screen: LoginView
mode: append
strategies:
  - {type: accessibility_id, value: signin_button, origin: manual}
"""


def test_override_replace():
    eff = _repo(override_elements=OVERRIDE_REPLACE).resolve("login_button", build="b1")
    assert [s.value for s in eff.strategies] == ["signin_button"]
    assert eff.strategies[0].origin == "manual"


def test_override_prepend():
    eff = _repo(override_elements=OVERRIDE_PREPEND).resolve("login_button", build="b1")
    # prepend：override 策略在前，generated 策略原序跟后
    # predicate 策略的 value 是完整表达式 "label == '登录'"
    assert [s.value for s in eff.strategies] == [
        "signin_button", "login_button", "label == '登录'",
    ]


def test_override_append():
    eff = _repo(override_elements=OVERRIDE_APPEND).resolve("login_button", build="b1")
    assert [s.value for s in eff.strategies] == [
        "login_button", "label == '登录'", "signin_button",
    ]


def test_override_shadows_source_warning():
    """override 改写 accessibility_id 值且与 generated 冲突 → warnings 记录（5.3）。"""
    eff = _repo(override_elements=OVERRIDE_REPLACE).resolve("login_button", build="b1")
    assert any(w.startswith("override_shadows_source") for w in eff.warnings)


def test_origin_preserved_after_merge():
    eff = _repo(override_elements=OVERRIDE_PREPEND).resolve("login_button", build="b1")
    assert [s.origin for s in eff.strategies] == ["manual", "source", "source"]


def test_merge_metadata_overrides():
    """override 的 metadata 覆盖 generated（优先级 overrides > generated）。"""
    ov = OVERRIDE_APPEND + "metadata:\n  data_class: SENSITIVE\n"
    eff = _repo(override_elements=ov).resolve("login_button", build="b1")
    assert eff.data_class.name == "SENSITIVE"
    # 未覆盖字段沿用 generated
    assert eff.risk.name == "LOW"


# --- Screen override 合并 ---

OVERRIDE_SCREEN = """\
kind: screen
id: LoginView
marker: screen.LoginView
kind_hint: modal
"""


def test_screen_override_merge():
    eff = _repo(override_screens=OVERRIDE_SCREEN).resolve("screen:LoginView", build="b1")
    assert eff.kind_hint == "modal"


# --- elements_of ---

def test_elements_of():
    elems = _repo().elements_of("LoginView")
    assert [e.id for e in elems] == ["login_button"]
    assert elems[0].strategies[0].origin == "source"


# --- lint 基础（Task 1.3 扩展全量 6.4 检查） ---

def test_lint_reports_ambiguous_and_unknown():
    """lint 返回 LintIssue：短名歧义 / 引用不存在的 ID（4.1 退出码 3 的来源）。"""
    repo = _repo(
        generated_elements=GENERATED_ELEMENTS_CONFIRM_HOME,
        extra_generated_elements=GENERATED_ELEMENTS_CONFIRM_SETTINGS,
    )
    tc = TestCase(
        schema_version="0.1", id="t1", name="t",
        steps=[ActionStep(action="tap", target=TargetRef(id="confirm_button"))],
    )
    issues = repo.lint([tc])
    assert "ambiguous_target" in {i.code for i in issues}


def test_lint_unknown_target_reported():
    repo = _repo()
    tc = TestCase(
        schema_version="0.1", id="t3", name="t",
        steps=[ActionStep(action="tap", target=TargetRef(id="ghost_button"))],
    )
    assert "unknown_target" in {i.code for i in repo.lint([tc])}


def test_lint_clean_case_no_issues():
    repo = _repo()
    tc = TestCase(
        schema_version="0.1", id="t2", name="t",
        steps=[ActionStep(action="tap", target=TargetRef(id="login_button")), ActionStep(action="launch_app")],
    )
    assert repo.lint([tc]) == []


# --- loader 直接校验 ---

def test_loader_rejects_missing_strategies():
    with pytest.raises(RepositoryLoaderError):
        load_element_dir([("bad.yaml", "kind: element\nid: x\nscreen: S\ntype: button\n")])


def test_loader_rejects_bad_strategy_type():
    bad = GENERATED_ELEMENTS_LOGIN.replace("type: accessibility_id", "type: teleport")
    with pytest.raises(RepositoryLoaderError):
        load_element_dir([("LoginView.yaml", bad)])


def test_loader_rejects_duplicate_id():
    with pytest.raises(RepositoryLoaderError):
        load_element_dir([
            ("A.yaml", GENERATED_ELEMENTS_LOGIN),
            ("B.yaml", GENERATED_ELEMENTS_LOGIN),
        ])


def test_loader_allows_same_id_across_screens():
    """语义 ID 跨 Screen 同名是 4.1 歧义规则的前提，loader 必须允许。"""
    defs = load_element_dir([
        ("HomeView.yaml", GENERATED_ELEMENTS_CONFIRM_HOME),
        ("SettingsView.yaml", GENERATED_ELEMENTS_CONFIRM_SETTINGS),
    ])
    assert len(defs) == 2
    assert {k for k in defs} == {("HomeView", "confirm_button"),
                                 ("SettingsView", "confirm_button")}
