"""Task 2.3 / P3-07：`planner/impact.py` —— changed_files → targets 的适配层。

判据（plan Task 2.3）：
  - 文件级归属（`source.file`）→ `Screen.elem` targets，供 `impact_of` 消费；
  - metadata **结构性**缺归属 → `ImpactMappingError`（fail-loud，不是「空结果」）；
  - 缺归属时可回退 `source/build_diff.py` 的 build 间 drift 面；
  - 「改了但没映射到」的文件必须**可见**（否则「没碰 UI」与「路径形态不符」分不开）。
"""
from __future__ import annotations

import pytest

from planner.impact import (ImpactMappingError, changed_targets, drift_targets,
                            metadata_has_file_attribution,
                            unmatched_changed_files)

APP_FILE = "LoginDemoApp.swift"
OTHER_FILE = "ProfileScreen.swift"


def _metadata(*, with_source: bool = True) -> dict:
    """两个文件 → 4 个元素；`with_source=False` 时**结构性地**没有 `source.file`。"""
    rows = {
        "HomeView": [("login_button", APP_FILE), ("pay_button", APP_FILE),
                     ("confirm_pay_button", APP_FILE)],
        "ProfileView": [("ghost_button", OTHER_FILE)],
    }
    screen_elements = []
    for screen, els in rows.items():
        elements = []
        for elem, file in els:
            el = {"id": elem, "accessibility_id": elem, "type": "button",
                  "resolution_type": "literal"}
            if with_source:
                el["source"] = {"file": file, "line": 1}
            elements.append(el)
        screen_elements.append({"name": screen, "elements": elements})
    return {"build": "local", "screens": sorted(rows),
            "screen_elements": screen_elements}


# --- 主路径：文件级归属 -------------------------------------------------------


def test_changed_file_maps_to_its_elements():
    got = changed_targets([APP_FILE], _metadata())
    assert got == ("HomeView.login_button", "HomeView.pay_button",
                   "HomeView.confirm_pay_button")


def test_only_changed_files_contribute():
    assert changed_targets([OTHER_FILE], _metadata()) == ("ProfileView.ghost_button",)


def test_multiple_files_keep_the_order_of_changes():
    """顺序 = `changes` 的顺序（git 已按路径排序 → 结果确定）。"""
    got = changed_targets([OTHER_FILE, APP_FILE], _metadata())
    assert got[0] == "ProfileView.ghost_button"
    assert "HomeView.login_button" in got


def test_unknown_path_maps_to_nothing_but_is_visible():
    """没映射到**不是错误**，但必须能从 `unmatched_changed_files` 看见。"""
    meta = _metadata()
    assert changed_targets(["nope.swift"], meta) == ()
    assert unmatched_changed_files(["nope.swift"], meta) == ("nope.swift",)
    assert unmatched_changed_files([APP_FILE], meta) == ()


def test_unmatched_and_matched_are_partitioned():
    meta = _metadata()
    changed = ["nope.swift", APP_FILE, "also_nope.swift"]
    assert unmatched_changed_files(changed, meta) == ("nope.swift", "also_nope.swift")
    assert changed_targets(changed, meta) == (
        "HomeView.login_button", "HomeView.pay_button", "HomeView.confirm_pay_button")


def test_duplicate_paths_do_not_duplicate_targets():
    got = changed_targets([APP_FILE, APP_FILE], _metadata())
    assert len(got) == len(set(got)) == 3


def test_element_without_accessibility_id_falls_back_to_id():
    """dynamic/unknown 元素只有 `id`——仍按名字收（`impact_of` 是文本匹配）。"""
    meta = {"screen_elements": [{"name": "HomeView", "elements": [
        {"id": "dyn_label", "resolution_type": "dynamic",
         "source": {"file": APP_FILE}}]}]}
    assert changed_targets([APP_FILE], meta) == ("HomeView.dyn_label",)


def test_element_without_name_is_skipped():
    meta = {"screen_elements": [{"name": "HomeView", "elements": [
        {"resolution_type": "literal", "source": {"file": APP_FILE}}]}]}
    assert changed_targets([APP_FILE], meta) == ()


# --- 失败语义：结构性缺归属 ---------------------------------------------------


def test_metadata_without_attribution_fails_loud():
    """**结构性地**没有 `source.file` + 有改动文件 → `ImpactMappingError`。

    与「映射结果为空」是两回事：静默返回空会让 planner 产出一个看起来完全正常的
    空 Plan（矩阵 #6 的反面）。
    """
    meta = _metadata(with_source=False)
    assert metadata_has_file_attribution(meta) is False
    with pytest.raises(ImpactMappingError, match="source.file"):
        changed_targets([APP_FILE], meta)


def test_no_changes_does_not_need_attribution():
    """没有改动文件时不该因为「metadata 缺归属」报错——没有要映射的东西。"""
    assert changed_targets([], _metadata(with_source=False)) == ()


def test_attribution_predicate_is_true_for_shipped_shape():
    assert metadata_has_file_attribution(_metadata()) is True


# --- 入参闸门 -----------------------------------------------------------------


def test_single_string_is_rejected():
    """单个字符串必须拒：`for p in "a.swift"` 会逐字符迭代出 7 个「路径」。"""
    with pytest.raises(TypeError, match="可迭代"):
        changed_targets(APP_FILE, _metadata())
    with pytest.raises(TypeError, match="可迭代"):
        unmatched_changed_files(APP_FILE, _metadata())


def test_non_string_path_is_rejected():
    with pytest.raises(TypeError, match="str"):
        changed_targets([APP_FILE, 42], _metadata())


# --- 回退面：build 间 drift ---------------------------------------------------


def _case(case_id: str, target: str):
    from testcase.schema import parse_testcase_dict

    return parse_testcase_dict({
        "schema_version": "0.2", "id": case_id, "name": case_id, "suite": "smoke",
        "steps": [{"action": "launch_app"}, {"action": "tap", "target": target}]})


def _build_meta(elements: dict[str, str]) -> dict:
    """`{element_id: screen}` → metadata（单屏，无 source.file）。"""
    by_screen: dict[str, list[dict]] = {}
    for elem, screen in elements.items():
        by_screen.setdefault(screen, []).append(
            {"id": elem, "accessibility_id": elem, "type": "button",
             "resolution_type": "literal"})
    return {"build": "local", "screens": sorted(by_screen),
            "screen_elements": [{"name": s, "elements": els}
                                for s, els in by_screen.items()]}


def test_drift_targets_uses_build_diff():
    """回退面：`added ∪ removed` 即「变了的目标」（复用 `diff_builds`，不重写）。"""
    base = _build_meta({"login_button": "HomeView", "gone_button": "HomeView"})
    new = _build_meta({"login_button": "HomeView", "added_button": "HomeView"})
    cases = [_case("login_001", "HomeView.login_button")]
    got = drift_targets(base, new, cases)
    assert got == ("HomeView.added_button", "HomeView.gone_button")


def test_drift_targets_requires_cases():
    """**空 cases 显式拒绝**：`diff_builds` 的范围由用例到达的屏决定，没有用例就没有
    scope——比出来的空看起来像「没变化」。"""
    base = _build_meta({"login_button": "HomeView"})
    with pytest.raises(ImpactMappingError, match="cases"):
        drift_targets(base, base, [])


def test_drift_targets_is_empty_when_nothing_changed():
    meta = _build_meta({"login_button": "HomeView"})
    assert drift_targets(meta, meta, [_case("login_001", "HomeView.login_button")]) == ()
