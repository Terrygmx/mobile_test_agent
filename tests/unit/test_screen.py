"""Task 1.4（P1-03）current_screen() 纯函数失败测试（设计 13.2 全分支）。

判定口径（设计 13.2）：
  - 0 个可见 marker → `CURRENT_SCREEN_UNKNOWN`；
  - 1 个 → 该 Screen；
  - 多个：若恰有一个 kind_hint ∈ {modal, overlay} → 取它；否则 `SCREEN_AMBIGUOUS`
    （含 `kind_hint: page` 多个）。
  - 输入是 XML 字符串 + repo，纯函数不发网络（H18）；
  - marker 的判定基于 repo 中 ScreenDef.marker（`screen.<Name>`），而不是
    无差别扫描所有 `screen.` 前缀——repo 未登记的 `screen.x` 不算 marker。
"""
from __future__ import annotations

import pytest

from repository.loader import load_screen_dir
from repository.resolver import Repository
from source.screen import (
    CURRENT_SCREEN_UNKNOWN,
    SCREEN_AMBIGUOUS,
    ScreenResult,
    current_screen,
)


PAGE = """\
<?xml version="1.0" encoding="UTF-8"?>
<AppiumAUT>
  <XCUIElementTypeApplication>
    {content}
  </XCUIElementTypeApplication>
</AppiumAUT>
"""


def _marker_xml(name: str, label: str = "", visible: str = "true") -> str:
    label_attr = f' label="{label}"' if label else ""
    return (f'<XCUIElementTypeOther name="{name}"{label_attr} '
            f'visible="{visible}"></XCUIElementTypeOther>')


def _repo(*screen_yaml: str) -> Repository:
    entries = [
        (f"screen{i}.yaml", text) for i, text in enumerate(screen_yaml)
    ]
    return Repository(generated_screens=load_screen_dir(entries))


LOGIN_PAGE_YAML = """\
kind: screen
id: LoginView
marker: screen.LoginView
kind_hint: page
"""

MODAL_YAML = """\
kind: screen
id: ConfirmDialog
marker: screen.ConfirmDialog
kind_hint: modal
"""

OVERLAY_YAML = """\
kind: screen
id: ToastOverlay
marker: screen.ToastOverlay
kind_hint: overlay
"""

HOME_PAGE_YAML = """\
kind: screen
id: HomeView
marker: screen.HomeView
kind_hint: page
"""


# --- 0 marker → CURRENT_SCREEN_UNKNOWN ---

def test_no_markers_unknown():
    repo = _repo(LOGIN_PAGE_YAML)
    xml = PAGE.format(content=_marker_xml("username_field"))
    result = current_screen(xml, repo)
    assert isinstance(result, ScreenResult)
    assert result.status == CURRENT_SCREEN_UNKNOWN
    assert result.screen is None


def test_unregistered_screen_prefix_not_a_marker():
    """`screen.x` 存在于页面但 repo 未登记该 Screen → 不算 marker。"""
    repo = _repo(LOGIN_PAGE_YAML)
    xml = PAGE.format(content=_marker_xml("screen.UnregisteredView"))
    assert current_screen(xml, repo).status == CURRENT_SCREEN_UNKNOWN


# --- 1 marker → 该 Screen ---

def test_single_page_marker():
    repo = _repo(LOGIN_PAGE_YAML)
    xml = PAGE.format(content=_marker_xml("username_field")
                      + _marker_xml("screen.LoginView"))
    result = current_screen(xml, repo)
    assert result.status == "FOUND"
    assert result.screen == "LoginView"


def test_invisible_marker_ignored():
    """visible=false 的 marker 不参与判定（13.2：visible elements）。"""
    repo = _repo(LOGIN_PAGE_YAML, HOME_PAGE_YAML)
    xml = PAGE.format(content=_marker_xml("screen.LoginView", visible="false")
                      + _marker_xml("screen.HomeView"))
    result = current_screen(xml, repo)
    assert result.screen == "HomeView"


# --- 多 marker：恰一 modal/overlay → 取它 ---

def test_page_plus_modal_picks_modal():
    repo = _repo(LOGIN_PAGE_YAML, MODAL_YAML)
    xml = PAGE.format(content=_marker_xml("screen.LoginView")
                      + _marker_xml("screen.ConfirmDialog"))
    result = current_screen(xml, repo)
    assert result.status == "FOUND"
    assert result.screen == "ConfirmDialog"


def test_page_plus_overlay_picks_overlay():
    repo = _repo(LOGIN_PAGE_YAML, OVERLAY_YAML)
    xml = PAGE.format(content=_marker_xml("screen.LoginView")
                      + _marker_xml("screen.ToastOverlay"))
    assert current_screen(xml, repo).screen == "ToastOverlay"


def test_two_modals_ambiguous():
    """两个 modal：'恰一个'不成立 → AMBIGUOUS。"""
    repo = _repo(MODAL_YAML, OVERLAY_YAML)
    xml = PAGE.format(content=_marker_xml("screen.ConfirmDialog")
                      + _marker_xml("screen.ToastOverlay"))
    assert current_screen(xml, repo).status == SCREEN_AMBIGUOUS


# --- 多 marker：无 modal → SCREEN_AMBIGUOUS ---

def test_two_pages_ambiguous():
    repo = _repo(LOGIN_PAGE_YAML, HOME_PAGE_YAML)
    xml = PAGE.format(content=_marker_xml("screen.LoginView")
                      + _marker_xml("screen.HomeView"))
    result = current_screen(xml, repo)
    assert result.status == SCREEN_AMBIGUOUS
    assert result.screen is None


def test_malformed_xml_raises_value_error():
    """page_source 不是合法 XML → ValueError（调用方按 CURRENT_SCREEN_UNKNOWN
    或 infra 错误处理，纯函数自身不吞异常）。"""
    with pytest.raises(ValueError):
        current_screen("not < xml", _repo(LOGIN_PAGE_YAML))


# --- 廉价路径支撑（13.2 注：wait_for screen active 直接查 marker） ---

def test_marker_visible_helper():
    from source.screen import marker_visible
    repo = _repo(LOGIN_PAGE_YAML)
    xml = PAGE.format(content=_marker_xml("screen.LoginView"))
    assert marker_visible(xml, repo, "LoginView") is True
    xml_hidden = PAGE.format(content=_marker_xml("screen.LoginView", visible="false"))
    assert marker_visible(xml_hidden, repo, "LoginView") is False
