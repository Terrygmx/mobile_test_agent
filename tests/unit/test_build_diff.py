"""build_diff.py 测试（12.6 Build-level Reconciliation / Task 3.3 第 2 步）。

12.6 原文约束（逐条钉住）：
  - 范围 = **仅限已有用例实际到达的 Screen**，不做自动探索；
  - 输出 ADDED / REMOVED / RENAMED? / UNCHANGED / UNKNOWN，
    P1 只要求前三类中**可判定的部分**（RENAMED? 带问号 = 启发式候选，
    不得当判定结论、不得影响退出码）；
  - 独立任务，**不阻塞日常回归** → 默认不因 REMOVED 而非零退出。
"""
from __future__ import annotations

from source.build_diff import (
    SourceDiffReport, diff_builds, format_report, reached_screens,
)
from testcase.schema import parse_testcase_dict


def _el(eid: str, rt: str = "literal", etype: str = "button") -> dict:
    return {"id": eid, "accessibility_id": eid, "resolution_type": rt,
            "type": etype, "container_type": "S",
            "source": {"file": "a.swift", "line": 1}}


def _meta(*screens: tuple[str, list[dict]], declared=None) -> dict:
    return {"screens": list(declared if declared is not None
                            else [s[0] for s in screens]),
            "screen_elements": [{"name": n, "elements": els}
                                for n, els in screens]}


def _case(steps: list[dict], cid="c1"):
    return parse_testcase_dict({
        "schema_version": "0.2", "id": cid, "name": cid, "suite": "s",
        "steps": steps})


# --- 范围：只比用例实际到达的 Screen ---------------------------------------

def test_scope_is_only_screens_reached_by_cases():
    """12.6：仅限已有用例实际到达的 Screen——metadata 里存在但没用例碰过的
    页面不参与 diff（否则每次改无关页面都报一堆 ADDED/REMOVED）。"""
    old = _meta(("HomeView", [_el("go_profile")]), ("DetailView", [_el("t")]))
    new = _meta(("HomeView", [_el("go_profile")]), ("DetailView", []))
    cases = [_case([{"action": "tap", "target": "HomeView.go_profile"}])]
    r = diff_builds(old, new, cases)
    assert r.scope_screens == ("HomeView",)
    assert not r.drift          # DetailView 的删除不影响结论


def test_scope_includes_screen_targets_and_unique_short_names():
    """两种到达：`screen:X` 显式等待 + `X.elem` 限定名 + **全局唯一的短名**。

    短名不是「不猜」——唯一时可确定（与 resolver 4.1 同一判据），漏掉它会
    让「只写短名」的用例整屏不参与 diff，那正是要检出的盲区。
    """
    cases = [
        _case([{"wait_for": {"target": "screen:LoginView",
                             "condition": "active"}}], "a"),
        _case([{"action": "tap", "target": "go_profile"}], "b"),
    ]
    old = _meta(("LoginView", [_el("u")]), ("HomeView", [_el("go_profile")]))
    assert reached_screens(cases, old) == ("HomeView", "LoginView")


def test_ambiguous_short_name_does_not_guess_a_screen():
    """短名跨屏同名 → 无法确定到达哪屏，**不猜**（12.2），该屏不进范围。"""
    old = _meta(("A", [_el("dup")]), ("B", [_el("dup")]))
    cases = [_case([{"action": "tap", "target": "dup"}])]
    assert reached_screens(cases, old) == ()


def test_unreferenced_screen_never_enters_scope():
    old = _meta(("A", [_el("x")]), ("B", [_el("y")]))
    new = _meta(("A", [_el("x")]), ("B", []))
    r = diff_builds(old, new, [_case([{"action": "tap", "target": "A.x"}])])
    assert r.scope_screens == ("A",)
    assert ("B", "y") not in r.removed
    assert r.counts.get("REMOVED", 0) == 0


# --- ADDED / REMOVED / UNCHANGED ------------------------------------------

def test_added_removed_unchanged():
    old = _meta(("HomeView", [_el("keep"), _el("gone")]))
    new = _meta(("HomeView", [_el("keep"), _el("fresh")]))
    r = diff_builds(old, new, [_case([
        {"action": "tap", "target": "HomeView.keep"},
        {"action": "tap", "target": "HomeView.gone"},
    ])])
    assert r.counts == {"UNCHANGED": 1, "REMOVED": 1, "ADDED": 1}
    assert ("HomeView", "gone") in r.removed
    assert ("HomeView", "fresh") in r.added
    assert ("HomeView", "keep") in r.unchanged


def test_dynamic_not_compared_but_visible():
    """12.2 dynamic 无可定位 id → 不进 diff 判定（否则会报一堆假 REMOVED），
    但两侧的 dynamic 都要能看见（不判定 ≠ 不可见）。"""
    old = _meta(("HomeView", [_el("cell_", "dynamic")]))
    new = _meta(("HomeView", []))
    r = diff_builds(old, new, [_case([{"action": "tap", "target": "HomeView.a"}])])
    assert r.counts.get("REMOVED", 0) == 0
    assert r.dynamic_skipped == 1


# --- RENAMED? 与 UNKNOWN ---------------------------------------------------

def test_renamed_is_heuristic_candidate_not_verdict():
    """同屏一增一删且类型相同 → RENAMED? 候选。必须带 '?' 语义：
    既不进 counts 的判定桶，也不影响 ok。"""
    old = _meta(("HomeView", [_el("old_btn")]))
    new = _meta(("HomeView", [_el("new_btn")]))
    r = diff_builds(old, new, [_case([{"action": "tap", "target": "HomeView.x"}])])
    assert r.renamed_candidates == (("HomeView", "old_btn", "new_btn"),)
    assert r.ok is True


def test_renamed_not_guessed_when_ambiguous():
    """两侧各多一增一删 → 不猜（宁可报两条独立 ADDED/REMOVED）。"""
    old = _meta(("HomeView", [_el("a1"), _el("a2")]))
    new = _meta(("HomeView", [_el("b1"), _el("b2")]))
    r = diff_builds(old, new, [_case([{"action": "tap", "target": "HomeView.x"}])])
    assert r.renamed_candidates == ()


def test_referenced_screen_absent_from_metadata_is_unknown():
    """用例引用了 metadata 里没有的 screen → UNKNOWN（不是 REMOVED：
    没扫到 ≠ 被删了，12.2 不得猜）。"""
    old = _meta(("HomeView", [_el("x")]))
    new = _meta(("HomeView", [_el("x")]))
    cases = [_case([{"wait_for": {"target": "screen:GhostView",
                                  "condition": "active"}}])]
    r = diff_builds(old, new, cases)
    assert r.unknown_screens == ("GhostView",)
    assert r.counts.get("REMOVED", 0) == 0
    assert r.ok is False        # UNKNOWN 必须让人看见，不算通过


# --- 不阻塞日常回归 --------------------------------------------------------

def test_removed_does_not_fail_by_default():
    old = _meta(("HomeView", [_el("gone")]))
    new = _meta(("HomeView", []))
    r = diff_builds(old, new, [_case([{"action": "tap",
                                       "target": "HomeView.gone"}])])
    assert r.removed
    assert r.ok is True          # 12.6：独立任务，不阻塞回归


def test_fail_on_drift_opt_in():
    old = _meta(("HomeView", [_el("gone")]))
    new = _meta(("HomeView", []))
    cases = [_case([{"action": "tap", "target": "HomeView.gone"}])]
    r = diff_builds(old, new, cases, fail_on_drift=True)
    assert r.ok is False


# --- 报告形态 --------------------------------------------------------------

def test_format_report_shows_all_classes():
    old = _meta(("HomeView", [_el("keep"), _el("gone")]), ("Other", [_el("z")]))
    new = _meta(("HomeView", [_el("keep"), _el("fresh")]), ("Other", [_el("z")]))
    ghost = parse_testcase_dict({
        "schema_version": "0.2", "id": "c2", "name": "c2", "suite": "s",
        "steps": [{"wait_for": {"target": "screen:GhostView",
                                "condition": "active"}}]})
    cases = [_case([{"action": "tap", "target": "HomeView.keep"},
                    {"action": "tap", "target": "HomeView.gone"}]), ghost]
    text = format_report(diff_builds(old, new, cases))
    assert "UNCHANGED" in text and "HomeView.keep" in text
    assert "REMOVED" in text and "HomeView.gone" in text
    assert "ADDED" in text and "HomeView.fresh" in text
    assert "UNKNOWN" in text and "GhostView" in text
    # 范围必须打出来（防止「以为比了全量」/「没报=没变」），UNKNOWN 的屏
    # 也在 scope 里（它被引用了，只是不可判定）
    assert "scope: GhostView, HomeView" in text
    assert isinstance(diff_builds(old, new, cases), SourceDiffReport)


def test_empty_scope_is_zero_not_crash():
    r = diff_builds(_meta(("A", [_el("x")])), _meta(("A", [_el("x")])), [])
    assert r.counts == {}
    assert r.scope_screens == ()
    assert "scope: (none)" in format_report(r)


def test_to_dict_is_json_serializable():
    import json
    old = _meta(("HomeView", [_el("gone")]))
    new = _meta(("HomeView", []))
    r = diff_builds(old, new, [_case([{"action": "tap",
                                       "target": "HomeView.gone"}])])
    json.dumps(r.to_dict())        # 不抛即契约成立
    d = r.to_dict()
    assert d["scope_screens"] == ["HomeView"]
    assert d["removed"] == ["HomeView.gone"]