"""coverage.py 测试（设计 12.7 覆盖率指标 / plan Task 3.2）。

指标定义（12.7 原文）：
    Identifier Coverage = 用例引用的 identifier 中被 metadata 正确解析
                          (literal+constant) 的数量 / 用例引用的 identifier 总数
    同时输出 dynamic_ratio、unknown_ratio

分桶口径（本任务定档，12.7 只给公式不给分桶）：
  resolved   —— metadata 里 resolution_type ∈ {literal, constant} 且解析出
                accessibility_id，与引用逐字匹配（12.2 唯一可定位形态）；
  dynamic    —— 引用命中 12.2 插值元素的静态前缀（如 HomeView.cell_alpha
                命中 `cell_`）：**不算覆盖**（不猜值），单列可见；
  unknown    —— 命中 resolution_type=unknown 的元素；
  ambiguous  —— 短名引用（无 `Screen.` 限定）在多个 Screen 各有一个解析值；
  missing    —— metadata 里根本没有这个 identifier（漂移或未登记）。

covered = resolved；分母 = resolved+dynamic+unknown+ambiguous+missing
（**不含 screen 引用**——screen marker 走独立的 screen_coverage）。
"""
from __future__ import annotations

from source.coverage import (
    CoverageReport, compute_coverage, collect_refs, format_report,
)
from testcase.schema import parse_testcase_dict


def _el(eid: str, rt: str = "literal", a11y: str | None = "__same__",
        container: str = "X"):
    # a11y 默认取 eid（literal/constant 形态必然解析出 id）；显式传 None
    # 表示「解析不出」（12.2 dynamic/unknown）
    return {"id": eid, "type": "button",
            "accessibility_id": eid if a11y == "__same__" else a11y,
            "resolution_type": rt, "container_type": container,
            "source": {"file": "a.swift", "line": 1}}


def _meta() -> dict:
    return {
        "screens": ["LoginView", "HomeView"],
        "screen_elements": [
            {"name": "LoginView", "elements": [
                _el("username_field"),
                _el("password_field", "constant", "password_field"),
                _el("cell_", "dynamic", None),
            ]},
            {"name": "HomeView", "elements": [
                _el("go_profile"),
                _el("weird", "unknown", None),
                _el("dup", "literal", "dup", container="HomeView"),
            ]},
            {"name": "OtherView", "elements": [
                _el("dup", "literal", "dup", container="OtherView"),
            ]},
        ],
    }


def _case(steps: list[dict], cid="c1") -> object:
    return parse_testcase_dict({
        "schema_version": "0.2", "id": cid, "name": cid, "suite": "s",
        "steps": steps,
    })


# --- 指标口径 ---------------------------------------------------------------

def test_ratio_literal_and_constant_count_as_covered():
    cases = [_case([
        {"action": "tap", "target": "LoginView.username_field"},
        {"action": "input", "target": "LoginView.password_field",
         "value": "x"},
    ])]
    r = compute_coverage(_meta(), cases)
    assert r.total == 2
    assert r.resolved == 2
    assert r.coverage == 1.0
    assert r.dynamic == 0 and r.unknown == 0 and r.missing == 0


def test_coverage_is_fraction_not_percent():
    cases = [_case([
        {"action": "tap", "target": "LoginView.username_field"},
        {"action": "tap", "target": "HomeView.go_profile"},
        {"action": "tap", "target": "HomeView.weird"},
    ])]
    r = compute_coverage(_meta(), cases)
    assert (r.resolved, r.total) == (2, 3)
    assert r.coverage == 2 / 3          # 0..1，不是百分数
    assert r.unknown_ratio == 1 / 3
    assert r.dynamic_ratio == 0.0


def test_dynamic_prefix_hit_is_not_covered_but_visible():
    """12.2：插值不猜值 → cell_alpha 不算覆盖，但必须单列可见。"""
    cases = [_case([{"action": "tap", "target": "LoginView.cell_alpha"}])]
    r = compute_coverage(_meta(), cases)
    assert r.coverage == 0.0
    assert r.dynamic == 1
    assert r.dynamic_ratio == 1.0
    assert ("LoginView", "cell_alpha") in r.dynamic_refs


def test_missing_identifier_counted_in_denominator():
    cases = [_case([{"action": "tap", "target": "LoginView.ghost"}])]
    r = compute_coverage(_meta(), cases)
    assert (r.resolved, r.missing, r.total) == (0, 1, 1)
    assert r.coverage == 0.0
    assert ("LoginView", "ghost") in r.missing_refs


def test_short_name_ambiguous_is_own_bucket():
    cases = [_case([{"action": "tap", "target": "dup"}])]
    r = compute_coverage(_meta(), cases)
    assert r.ambiguous == 1 and r.resolved == 0 and r.total == 1
    assert r.ambiguous_ratio == 1.0


def test_short_name_unique_resolves():
    cases = [_case([{"action": "tap", "target": "go_profile"}])]
    r = compute_coverage(_meta(), cases)
    assert r.resolved == 1 and r.coverage == 1.0


# --- 引用收集（三种步骤形态 + postcondition） --------------------------------

def test_collect_refs_covers_all_step_shapes():
    tc = _case([
        {"action": "tap", "target": "LoginView.username_field"},
        {"wait_for": {"target": "HomeView.go_profile", "condition": "exists"}},
        {"assertion": {"target": "LoginView.password_field",
                       "condition": "exists"}},
        {"action": "tap", "target": "LoginView.username_field",
         "postcondition": {"target": "LoginView.username_field",
                           "condition": "exists"}},
    ], cid="c2")
    refs = collect_refs([tc])
    # 顺序 = 步骤序；同一步骤里 action target 先于 postcondition target
    assert refs == [
        ("c2", "LoginView.username_field"),
        ("c2", "HomeView.go_profile"),
        ("c2", "LoginView.password_field"),
        ("c2", "LoginView.username_field"),   # 第 4 步的 action target
        ("c2", "LoginView.username_field"),   # 同行 postcondition 也算引用
    ]


def test_collect_refs_skips_screen_targets():
    tc = _case([{"wait_for": {"target": "screen:HomeView",
                              "condition": "active"}}])
    assert collect_refs([tc]) == []      # screen 不进 identifier 分母


def test_screen_coverage_separate_metric():
    cases = [_case([
        {"wait_for": {"target": "screen:LoginView", "condition": "active"}},
        {"wait_for": {"target": "screen:HomeView", "condition": "active"}},
        {"wait_for": {"target": "screen:GhostView", "condition": "active"}},
    ])]
    r = compute_coverage(_meta(), cases)
    assert (r.screens_total, r.screens_resolved) == (3, 2)
    assert r.screen_coverage == 2 / 3
    assert "GhostView" in r.screens_missing
    assert r.total == 0                  # screen 引用不进 identifier 分母


def test_duplicate_refs_counted_once():
    """同一 identifier 在多条用例里引用多次 → 只算 1（覆盖率按标识符集合，
    不是按引用次数——否则改用例条数会凭空改变指标）。"""
    cases = [_case([{"action": "tap", "target": "HomeView.go_profile"}],
                   cid="a"),
             _case([{"action": "tap", "target": "HomeView.go_profile"}],
                   cid="b")]
    r = compute_coverage(_meta(), cases)
    assert r.total == 1 and r.resolved == 1
    assert r.occurrences == 2             # 原始引用次数仍可见


def test_empty_inputs_are_zero_not_crash():
    r = compute_coverage(_meta(), [])
    assert r.total == 0 and r.coverage == 0.0
    assert compute_coverage({"screen_elements": []}, []) .total == 0
    assert "coverage: 0.0%" in format_report(r)


def test_report_lists_buckets_for_humans():
    cases = [
        _case([{"action": "tap", "target": "LoginView.cell_alpha"}], "c1"),
        _case([{"action": "tap", "target": "LoginView.ghost"}], "c2"),
        _case([{"action": "tap", "target": "HomeView.go_profile"}], "c3"),
    ]
    text = format_report(compute_coverage(_meta(), cases))
    assert "coverage: 33.3%" in text
    assert "DYNAMIC" in text and "LoginView.cell_alpha" in text
    assert "MISSING" in text and "LoginView.ghost" in text


def test_report_is_dataclass_repr_safe():
    r = compute_coverage(_meta(), [_case(
        [{"action": "tap", "target": "HomeView.go_profile"}])])
    assert isinstance(r, CoverageReport)
    assert r.to_dict()["coverage"] == 1.0
    assert r.to_dict()["resolved"] == 1