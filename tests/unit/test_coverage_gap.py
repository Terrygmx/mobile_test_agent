"""Task 3.2（P3-10）Coverage Gap 计算（设计 6.3）。

口径（本次拍板，见 `docs/p3_data_audit.md` 的 Task 3.2 记录）：

- **全集 = Runtime Graph 的转移**（`graph_query` 的返回值），不是 Source Graph：
  设计 6.3 的话是「现有用例**从未触发过**的 Transition」——「触发」是运行时概念；
  且实测 `build_source_graph` 的转移**恒为 0**（metadata 无导航声明，
  `graph/builder.py` 已注明），拿它当全集，`coverage_gap` 恒返回空列表。
- **「已覆盖」= 用例静态推导**：从用例的 `wait_for screen:X` /
  `postcondition screen:X` 序列推出相邻屏转移，`trigger` 取紧邻的前一个动作
  （`action:target`，与 `graph/builder.py::_trigger_of` 同一形状）。
  不读 trace、不执行——纯函数。
  ⚠️ **为什么不拿 runtime 图自己当「已覆盖」**：runtime 图的每条转移都来自
  **已执行过的 trace**，全集减自身恒为空，判据没有鉴别力。
- **分类标签是附注字段，不是分类器**：设计 6.3 的七类（Happy Path / Boundary /
  Negative / Exception / State Transition / Concurrency-Timing / Recovery）本次只做
  **初版启发**（按转移形状打标），不建立独立分类体系。

「同一个函数/同一套逻辑」的显式断言（plan §2 第 1 条）：`coverage_gap` 的
trigger 形状必须复用 `graph/builder.py::_trigger_of`，不另写一份拼接。
"""
from __future__ import annotations

import pytest

from generator.coverage import (
    CATEGORY_BRANCHING,
    CATEGORY_ERROR_RECOVERY,
    CATEGORY_HAPPY_PATH,
    UncoveredTransition,
    coverage_gap,
    covered_transitions,
)
from graph.models import RUNTIME, RuntimeGraph, ScreenTransition
from testcase.schema import parse_testcase_dict

APP = "com.phaset0.logindemo"

# --- 夹具：一张 4 转移的 runtime 图 -------------------------------------------

GRAPH = RuntimeGraph(
    app_id=APP, app_build="1026", source_of=RUNTIME,
    transitions=(
        ScreenTransition("HomeView", "SearchView", "tap:HomeView.go_search",
                         source_of=RUNTIME, count=64),
        ScreenTransition("HomeView", "ProfileView", "tap:HomeView.go_profile",
                         source_of=RUNTIME, count=38),
        ScreenTransition("SearchView", "DetailView", "tap:SearchView.cell_beta",
                         source_of=RUNTIME, count=9),
        ScreenTransition("DetailView", "HomeView", "back:", source_of=RUNTIME,
                         count=9),
    ),
)


def _case(cid: str, steps: list) -> object:
    return parse_testcase_dict(
        {"schema_version": "0.2", "id": cid, "name": cid, "steps": steps})


def _screen_wait(screen: str) -> dict:
    return {"wait_for": {"target": f"screen:{screen}", "condition": "active",
                         "timeout": 10}}


# logout_001 的真形状（suites/smoke/logout_001.yaml）：覆盖 Home→Profile、
# Profile→Login 两条转移
LOGOUT = _case("logout_001", [
    {"action": "tap", "target": "LoginView.login_button"},
    _screen_wait("HomeView"),
    {"action": "tap", "target": "HomeView.go_profile"},
    _screen_wait("ProfileView"),
    {"action": "tap", "target": "ProfileView.logout_button",
     "postcondition": {"target": "screen:LoginView", "condition": "active",
                       "timeout": 10}},
])


# --- 判据 ①：Source 有而用例未覆盖的 Transition → 正确识别 --------------------

def test_uncovered_transitions_are_identified():
    gaps = coverage_gap(GRAPH, [LOGOUT])
    got = {(g.from_screen, g.to_screen, g.trigger) for g in gaps}
    # logout_001 覆盖 Home→Profile / Profile→Login（后者不在图里）
    assert ("HomeView", "SearchView", "tap:HomeView.go_search") in got
    assert ("SearchView", "DetailView", "tap:SearchView.cell_beta") in got
    assert ("DetailView", "HomeView", "back:") in got


def test_uncovered_transition_carries_evidence_fields():
    g = next(x for x in coverage_gap(GRAPH, [LOGOUT])
             if x.to_screen == "SearchView")
    assert isinstance(g, UncoveredTransition)
    assert g.from_screen == "HomeView"
    assert g.to_screen == "SearchView"
    assert g.trigger == "tap:HomeView.go_search"
    assert g.observed_count == 64        # runtime 图上它被真实跑到过 64 次
    assert g.category                    # 附注分类，非空


# --- 判据 ②：已覆盖的不重复列出 ----------------------------------------------

def test_covered_transition_is_not_listed():
    got = {(g.from_screen, g.to_screen, g.trigger)
           for g in coverage_gap(GRAPH, [LOGOUT])}
    assert ("HomeView", "ProfileView", "tap:HomeView.go_profile") not in got


def test_result_has_no_duplicates():
    gaps = coverage_gap(GRAPH, [])
    keys = [(g.from_screen, g.to_screen, g.trigger) for g in gaps]
    assert len(keys) == len(set(keys))


def test_result_is_deterministically_ordered():
    assert coverage_gap(GRAPH, []) == coverage_gap(GRAPH, [])
    first = [(g.from_screen, g.to_screen, g.trigger)
             for g in coverage_gap(GRAPH, [])]
    assert first == sorted(first)


# --- 判据 ③：空图 / 空用例集 → 如实为空 ---------------------------------------

def test_empty_graph_yields_no_gaps():
    empty = RuntimeGraph(app_id=APP, app_build="1026", source_of=RUNTIME)
    assert coverage_gap(empty, [LOGOUT]) == []


def test_graph_without_transitions_yields_no_gaps():
    from graph.models import ScreenNode
    nodes_only = RuntimeGraph(
        app_id=APP, app_build="1026", source_of=RUNTIME,
        nodes=(ScreenNode(screen_id="HomeView", source_of=RUNTIME),))
    assert coverage_gap(nodes_only, [LOGOUT]) == []


def test_empty_test_set_yields_all_transitions():
    assert len(coverage_gap(GRAPH, [])) == 4


def test_empty_inputs_are_empty_not_crash():
    empty = RuntimeGraph(app_id=APP, app_build="1026", source_of=RUNTIME)
    assert coverage_gap(empty, []) == []


# --- 判据 ④：trigger 匹配的严格性（同屏不同触发是两条转移）-------------------

def test_same_screen_pair_different_trigger_is_a_separate_transition():
    g = RuntimeGraph(
        app_id=APP, app_build="b", source_of=RUNTIME,
        transitions=(
            ScreenTransition("HomeView", "DetailView",
                             "tap:HomeView.cell_alpha", source_of=RUNTIME),
            ScreenTransition("HomeView", "DetailView",
                             "tap:HomeView.cell_beta", source_of=RUNTIME),
        ))
    # 用例只覆盖 alpha 那一条
    covering = _case("c", [
        {"action": "tap", "target": "HomeView.cell_alpha"},
        _screen_wait("HomeView"),
        {"action": "tap", "target": "HomeView.cell_alpha"},
        _screen_wait("DetailView"),
    ])
    got = {(x.from_screen, x.to_screen, x.trigger) for x in coverage_gap(g, [covering])}
    assert got == {("HomeView", "DetailView", "tap:HomeView.cell_beta")}


# --- 判据 ⑤：自环不产生转移（与 runtime 图同款纪律）--------------------------

def test_consecutive_same_screen_produces_no_transition():
    """同一屏连续观测 = 一次访问，不产生**自环**转移（与 runtime 图同款纪律）。

    用例侧的噪声是「先 wait HomeView 两次再 wait SearchView」：推导出的序列是
    `[Home, Home, Search]` → 折叠自环后得到 `Home→Search`。

    ⚠️ **首个到达点没有「从哪来」**：它的 trigger 是 `""`（起点不是一条转移），
    所以 `Home→Search` 的 trigger 是第二个 Search 之前的最近动作。这条用例
    **推不出** `Home->Search@tap:HomeView.go_search`（触发动作在第一个 Home
    之前就消费掉了）→ 图上那条转移如实**未被覆盖**。这正是本函数要报的 gap
    形态：「用例到过 SearchView，但没有一条用例**以该动作从该屏出发**到过它」。
    """
    g = RuntimeGraph(
        app_id=APP, app_build="b", source_of=RUNTIME,
        transitions=(ScreenTransition("HomeView", "SearchView",
                                      "tap:HomeView.go_search",
                                      source_of=RUNTIME),))
    noisy = _case("n", [
        {"action": "tap", "target": "HomeView.go_search"},
        _screen_wait("HomeView"),
        _screen_wait("HomeView"),          # 同一屏重复观测 → 折叠
        _screen_wait("SearchView"),
    ])
    got = {(x.from_screen, x.to_screen, x.trigger) for x in coverage_gap(g, [noisy])}
    assert got == {("HomeView", "SearchView", "tap:HomeView.go_search")}


def test_covered_set_never_contains_self_loop():
    """自环折叠的**直接**判据：推导出的覆盖集里不许出现 `X→X`。

    ⚠️ 为什么必须直接钉 `covered_transitions`：先前的写法只在「图里有自环」时
    才观察得到差异，而 runtime 图按构造**永远没有自环**（builder 折叠过）→
    那条断言在去掉折叠后**仍然全绿**（A/B 探针实测：19 passed，空转）。
    判据要钉在**产生自环的那一侧**，不是钉在结果上看巧合。
    """
    noisy = _case("n", [
        {"action": "launch_app"},
        _screen_wait("HomeView"),
        _screen_wait("HomeView"),          # 同一屏连续观测
        {"action": "tap", "target": "HomeView.go_search"},
        _screen_wait("SearchView"),
    ])
    covered = covered_transitions([noisy])
    assert not any(a == b for a, b, _ in covered), covered
    assert ("HomeView", "SearchView", "tap:HomeView.go_search") in covered


def test_self_loop_never_appears_in_result():
    """推导结果里不出现 `X→X`（自环不是转移）。"""
    assert not any(g.from_screen == g.to_screen
                   for g in coverage_gap(GRAPH, []))


def test_well_formed_case_covers_the_transition():
    """对照：形制正确（触发动作在**到达起点屏之后**）的用例能覆盖它。"""
    g = RuntimeGraph(
        app_id=APP, app_build="b", source_of=RUNTIME,
        transitions=(ScreenTransition("HomeView", "SearchView",
                                      "tap:HomeView.go_search",
                                      source_of=RUNTIME),))
    good = _case("g", [
        {"action": "launch_app"},
        _screen_wait("HomeView"),                       # 起点屏
        {"action": "tap", "target": "HomeView.go_search"},   # 触发
        _screen_wait("SearchView"),                     # 到达
    ])
    assert coverage_gap(g, [good]) == []


# --- 判据 ⑥：分类标签是附注，不是独立分类器 ----------------------------------

def test_category_is_annotation_not_classifier():
    gaps = coverage_gap(GRAPH, [])
    known = {CATEGORY_HAPPY_PATH, CATEGORY_BRANCHING, CATEGORY_ERROR_RECOVERY}
    assert {g.category for g in gaps} <= known


def test_back_transition_is_branching_not_happy_path():
    back = next(g for g in coverage_gap(GRAPH, [])
                if g.trigger == "back:")
    assert back.category == CATEGORY_BRANCHING


# --- 判据 ⑦：「同一套逻辑」的显式断言（plan §2 第 1 条）----------------------

def test_trigger_shape_reuses_graph_builder_primitive():
    """trigger 形状必须复用 `graph/builder.py::_trigger_of`，不另写一份拼接。"""
    from graph.builder import _trigger_of
    from graph.models import TraceStep
    step = TraceStep(testcase_run_id=1, step_index=0, step_type="tap",
                     target_id="HomeView.go_search")
    assert _trigger_of(step) == "tap:HomeView.go_search"
    # coverage_gap 用的就是同一形状（用例侧推导出来的 trigger 与它同域）
    assert any(g.trigger == _trigger_of(step) for g in coverage_gap(GRAPH, []))


def test_covered_transitions_is_the_complement():
    """gap = 图的转移全集 − 用例覆盖集（两函数必须对得上，不是各算各的）。"""
    covered = covered_transitions([LOGOUT])
    assert ("HomeView", "ProfileView", "tap:HomeView.go_profile") in covered
    all_keys = {(t.from_screen, t.to_screen, t.trigger)
                for t in GRAPH.transitions}
    gap_keys = {(g.from_screen, g.to_screen, g.trigger)
                for g in coverage_gap(GRAPH, [LOGOUT])}
    assert gap_keys == all_keys - covered


# --- 判据 ⑧：入参闸门（不做静默降级）-----------------------------------------

def test_rejects_source_graph_as_universe():
    """Source Graph 的转移恒空 → 拿它当全集是「看起来接了、实际恒空」。

    本函数**只收 runtime 图**：传 source 图直接炸，而不是静默返回空 gap。
    """
    from graph.models import SOURCE
    src = RuntimeGraph(app_id=APP, app_build="b", source_of=SOURCE)
    with pytest.raises(ValueError, match="runtime"):
        coverage_gap(src, [LOGOUT])


def test_non_string_screen_id_is_rejected():
    """屏 id 非 str → fail-loud（`UncoveredTransition` 的字段契约）。

    静默放行一个 `int` 屏名，gap 列表里就会出现一条**永远对不上图的假转移**——
    与本仓「无名节点」同一形态。
    """
    with pytest.raises(ValueError):
        UncoveredTransition(from_screen=42, to_screen="SearchView")
