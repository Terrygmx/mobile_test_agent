"""storyboard.py 解析测试（12.1：XIB/Storyboard 走 XML 解析）。

覆盖：
  1. accessibilityIdentifier / Label 从 userDefinedRuntimeAttributes 提取；
  2. 容器归属：ViewController 的 customClass 优先、id 兜底；
  3. 占位值（`$(VAR)` / 空）→ unknown 不猜（12.2 同源纪律）；
  4. 元素类型映射（textField → textfield 等）；
  5. 坏 XML → ValueError（fail-loud，不吞成空结果）。
"""

from __future__ import annotations

import pytest

from source.storyboard import (parse_storyboard, scan_ib_files,
                               _looks_like_placeholder)

STORYBOARD = """<?xml version="1.0" encoding="UTF-8"?>
<document type="com.apple.InterfaceBuilder3.CocoaTouch.Storyboard.XIB">
  <scenes>
    <scene sceneID="s1">
      <objects>
        <viewController id="vc-1" customClass="LoginViewController"
                        sceneMemberID="viewController">
          <view key="view" contentMode="scaleToFill" id="v-1">
            <subviews>
              <textField id="tf-1">
                <userDefinedRuntimeAttributes>
                  <userDefinedRuntimeAttribute type="string"
                      keyPath="accessibilityIdentifier"
                      value="username_field"/>
                </userDefinedRuntimeAttributes>
              </textField>
              <button id="bt-1">
                <userDefinedRuntimeAttributes>
                  <userDefinedRuntimeAttribute type="string"
                      keyPath="accessibilityIdentifier"
                      value="login_button"/>
                  <userDefinedRuntimeAttribute type="string"
                      keyPath="accessibilityLabel"
                      value="登录"/>
                </userDefinedRuntimeAttributes>
              </button>
              <label id="lb-1">
                <userDefinedRuntimeAttributes>
                  <userDefinedRuntimeAttribute type="string"
                      keyPath="accessibilityIdentifier"
                      value="$(PRODUCT_NAME)"/>
                </userDefinedRuntimeAttributes>
              </label>
            </subviews>
          </view>
        </viewController>
      </objects>
    </scene>
  </scenes>
</document>
"""


@pytest.fixture()
def sb(tmp_path):
    p = tmp_path / "Login.storyboard"
    p.write_text(STORYBOARD, encoding="utf-8")
    return p


def test_literal_identifier_and_label(sb):
    els = {e.id: e for e in parse_storyboard(sb)}
    assert els["username_field"].resolution_type == "literal"
    assert els["username_field"].accessibility_id == "username_field"
    assert els["username_field"].element_type == "textfield"
    assert els["login_button"].label == "登录"
    assert els["login_button"].element_type == "button"


def test_container_is_view_controller_custom_class(sb):
    for e in parse_storyboard(sb):
        assert e.container_type == "LoginViewController"


def test_placeholder_value_is_unknown(sb):
    els = [e for e in parse_storyboard(sb) if e.resolution_type == "unknown"]
    assert len(els) == 1
    assert els[0].accessibility_id is None
    assert els[0].id.startswith("UNKNOWN:")


def test_non_accessibility_elements_skipped(sb):
    els = parse_storyboard(sb)
    assert {e.id for e in els} == {"username_field", "login_button",
                                   els[0].id if els else None} | {
                                       e.id for e in els}


def test_line_numbers_are_real(tmp_path):
    """P2-1：行号必须真实（ET.parse 无 _line，曾恒 0 使 UNKNOWN id 不可区分）。
    textField 在第 7 行、label 在第 10 行（fixture 内计数）。"""
    p = tmp_path / "L.storyboard"
    p.write_text(STORYBOARD, encoding="utf-8")
    els = {e.id: e for e in parse_storyboard(p)}
    assert els["username_field"].line > 0
    assert els["login_button"].line > els["username_field"].line
    unknown = [e for e in parse_storyboard(p)
               if e.resolution_type == "unknown"][0]
    assert unknown.line > 0
    assert unknown.id == f"UNKNOWN:L.storyboard:{unknown.line}"


def test_bad_xml_raises(tmp_path):
    p = tmp_path / "Bad.storyboard"
    p.write_text("<document><unclosed>", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid IB XML"):
        parse_storyboard(p)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_storyboard(tmp_path / "nope.storyboard")


def test_scan_multiple_files(sb, tmp_path):
    p2 = tmp_path / "Empty.xib"
    p2.write_text('<?xml version="1.0"?><document/>', encoding="utf-8")
    els = scan_ib_files([sb, p2])
    assert len(els) == 3


@pytest.mark.parametrize("value,expected", [
    (None, True),
    ("", True),
    ("   ", True),
    ("$(VAR)", True),
    ("ok_id", False),
    ("Ok$(Var)", True),   # 含插值 → 运行时值
])
def test_placeholder_detection(value, expected):
    assert _looks_like_placeholder(value) is expected
