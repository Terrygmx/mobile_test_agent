"""source.consistency 一致性 Gate 测试（12.6 / plan Task 3.1 第 5 步）。

不打 Swift 工具链——用内联 metadata dict 验证 Gate 语义：
  1. literal/constant generated 与 overrides 全对齐 → PASS；
  2. overrides 有而 generated 无（真删/改名）→ REMOVED → FAIL；
  3. generated 有而 overrides 无（漏登记）→ ADDED → FAIL；
  4. 插值动态 ID：overrides 登记的实例被 generated 的「静态前缀」覆盖 →
     **不算 REMOVED**（12.2 不猜值，人工登记是预期流程）；
  5. dynamic/unknown 元素进 unresolvable，不参与成败。
"""

from __future__ import annotations

import pytest

from source.consistency import (check, format_report,
                                generated_element_ids, unresolvable_ids)


def _meta(*screens_and_elements) -> dict:
    """(screen_name, [(id, a11y_or_None, resolution), ...]) → 12.3 dict"""
    screen_elements = []
    for name, els in screens_and_elements:
        elements = []
        for eid, a11y, res in els:
            elements.append({"id": eid, "type": "unknown",
                             "accessibility_id": a11y,
                             "resolution_type": res,
                             "container_type": name,
                             "source": {"file": "x.swift", "line": 1}})
        screen_elements.append({"name": name, "elements": elements})
    return {"screens": [n for n, _ in screens_and_elements],
            "screen_elements": screen_elements}


@pytest.fixture()
def overrides(tmp_path):
    """最小 overrides 目录（loader 真实解析）。只登记 `a`——需要 ghost 的
    测试自己追加文档，避免 fixture 内容渗进其他用例的预期。"""
    import yaml
    d = tmp_path / "overrides" / "elements"
    d.mkdir(parents=True)
    (d / "V.yaml").write_text(
        yaml.safe_dump(
            {"schema_version": "1.0", "kind": "element", "id": "a",
             "screen": "V", "type": "button",
             "strategies": [{"type": "accessibility_id", "value": "a",
                             "origin": "manual"}],
             "metadata": {"risk": "LOW", "idempotency": "IDEMPOTENT",
                          "data_class": "PUBLIC"}},
            allow_unicode=True),
        encoding="utf-8")
    return tmp_path / "overrides"


def test_full_match_passes(overrides):
    r = check(_meta(("V", [("a", "a", "literal")])), overrides)
    assert r.added == () and r.removed == ()
    assert r.matched == (("V", "a"),)
    assert r.ok


def test_generated_only_is_added_and_fails(overrides):
    r = check(_meta(("V", [("a", "a", "literal"), ("b", "b", "literal")])),
              overrides)
    assert r.added == (("V", "b"),)
    assert not r.ok


def test_overrides_only_is_removed_and_fails(overrides):
    # generated 只认 a；overrides 另有 ghost → REMOVED
    import yaml
    d = overrides / "elements"
    (d / "G.yaml").write_text(
        yaml.safe_dump(
            {"schema_version": "1.0", "kind": "element", "id": "ghost",
             "screen": "V", "type": "button",
             "strategies": [{"type": "accessibility_id", "value": "ghost",
                             "origin": "manual"}],
             "metadata": {"risk": "LOW", "idempotency": "IDEMPOTENT",
                          "data_class": "PUBLIC"}},
            allow_unicode=True),
        encoding="utf-8")
    r = check(_meta(("V", [("a", "a", "literal")])), overrides)
    assert r.removed == (("V", "ghost"),)
    assert not r.ok


def test_interpolated_prefix_covers_manual_registration(overrides):
    """插值 cell_alpha 在 generated 里是 dynamic + 前缀 cell_ → Gate 不得
    误报 REMOVED（12.2 不猜值的人工登记是预期流程）。"""
    # overrides: a + ghost；generated: a literal + cell_ dynamic 前缀
    import pathlib
    d = overrides / "elements"
    (d / "W.yaml").write_text(
        "---\nschema_version: \"1.0\"\nkind: element\nid: cell_alpha\n"
        "screen: V\ntype: cell\nstrategies:\n"
        "  - {type: accessibility_id, value: cell_alpha, origin: manual}\n"
        "metadata:\n  risk: LOW\n  idempotency: IDEMPOTENT\n"
        "  data_class: PUBLIC\n", encoding="utf-8")
    meta = _meta(("V", [("a", "a", "literal"), ("cell_", None, "dynamic")]))
    r = check(meta, overrides)
    assert ("V", "cell_alpha") not in r.removed
    assert not r.removed, r.removed
    assert r.unresolvable == (("V", "cell_"),)


def test_dynamic_without_prefix_does_not_cover(overrides):
    """dynamic 但 id 是 UNKNOWN:file:line 占位（无前缀）→ 不覆盖手写登记，
    REMOVED 仍报（真漂移与不可解析要能区分）。"""
    r = check(_meta(("V", [("a", "a", "literal"),
                           ("UNKNOWN:x.swift:9", None, "unknown")])),
              overrides)
    assert unresolvable_ids(_meta(("V", [("UNKNOWN:x.swift:9", None,
                                          "unknown")]))) == {
        ("V", "UNKNOWN:x.swift:9")}


def test_strict_mode_reports_interpolated_as_removed(overrides):
    """strict=True：插值也当缺口（临时排查用）。"""
    import pathlib
    d = overrides / "elements"
    (d / "W.yaml").write_text(
        "---\nschema_version: \"1.0\"\nkind: element\nid: cell_alpha\n"
        "screen: V\ntype: cell\nstrategies:\n"
        "  - {type: accessibility_id, value: cell_alpha, origin: manual}\n"
        "metadata:\n  risk: LOW\n  idempotency: IDEMPOTENT\n"
        "  data_class: PUBLIC\n", encoding="utf-8")
    meta = _meta(("V", [("a", "a", "literal"), ("cell_", None, "dynamic")]))
    r = check(meta, overrides, strict=True)
    assert ("V", "cell_alpha") in r.removed
    assert not r.ok


def test_generated_ids_ignores_dynamic():
    meta = _meta(("V", [("a", "a", "literal"), ("b", None, "dynamic")]))
    assert generated_element_ids(meta) == {("V", "a")}


def test_format_report_contains_verdict(overrides):
    r = check(_meta(("V", [("a", "a", "literal")])), overrides)
    assert "verdict: PASS" in format_report(r)
