"""Task 4.2 / P3-15：Frontier 选择（基于 P2 UI Graph）。

plan Steps 的判据：
1. explored 排在 unexplored 之后（unexplored = source 声明、runtime 无观测；
   explored 内部按目标 visit_count 低者优先——novelty 初版打分）；
2. frontier 为空（全部已知 / 当前屏没有出边）→ 返回空表；接进主循环后
   OUTCOME=FRONTIER_EMPTY 正常结束，**不是报错**；
3. 纯函数（F13）：无 IO、无设备、同输入同输出（排序确定性）。

⚠️ 签名偏离登记：plan 写 `select_frontier(graph, visited, current_screen)`
单图入参，但「source 有 / runtime 无」与「runtime visit_count」需要**两面**
图，而 `RuntimeGraph` 的图级 `source_of` 一致性校验禁止混面（graph/models.py
`__post_init__`）——拆成显式 `source` / `runtime` 两个入参。

⚠️ Decider 契约扩展登记（4.2）：第三参 `visited` 从「指纹列表」扩为
`VisitLog`（fingerprints + screens 两个视图）——4.4 循环检测吃指纹，
frontier 吃屏 id；一个参数两个视图，调用方各取所需。
"""
from __future__ import annotations

import pytest

from agents.storage import SQLiteAgentStore
from agents.tools import AgentToolkit
from executor.guard import EnvKind, Guard
from explorer.agent import Explorer, ExplorationOutcome
from explorer.frontier import (FrontierItem, make_frontier_decider,
                               select_frontier)
from explorer.policy import ProposedAction
from graph.models import (RUNTIME, SOURCE, ScreenNode, ScreenTransition,
                          RuntimeGraph)
from tests.fault_injection.fi_support import FakeExecutor, drift_repo

HOME, SEARCH, PROFILE, DETAIL = ("HomeView", "SearchView", "ProfileView",
                                 "DetailView")


def _src_graph(*edges):
    """edges: (from, to, trigger) 三元组 → source 面（声明，count=0）。"""
    ts = tuple(ScreenTransition(from_screen=a, to_screen=b, trigger=tr,
                                source_of=SOURCE) for a, b, tr in edges)
    screens = sorted({a for a, _, _ in edges} | {b for _, b, _ in edges})
    return RuntimeGraph(app_id="demo", app_build="local", source_of=SOURCE,
                        nodes=tuple(ScreenNode(n, source_of=SOURCE)
                                    for n in screens),
                        transitions=ts)


def _rt_graph(*edges, visits=None):
    """edges: (from, to, trigger, count)；visits: {screen: visit_count}。"""
    ts = tuple(ScreenTransition(from_screen=a, to_screen=b, trigger=tr,
                                source_of=RUNTIME, count=c)
               for a, b, tr, c in edges)
    counts = dict(visits or {})
    screens = sorted({a for a, _, _, _ in edges}
                     | {b for _, b, _, _ in edges} | set(counts))
    return RuntimeGraph(app_id="demo", app_build="local", source_of=RUNTIME,
                        nodes=tuple(ScreenNode(n, source_of=RUNTIME,
                                               visit_count=counts.get(n, 0))
                                    for n in screens),
                        transitions=ts)


# --- 纯函数面 ------------------------------------------------------------------


def test_unexplored_ranks_before_explored():
    src = _src_graph((HOME, SEARCH, "tap s"), (HOME, PROFILE, "tap p"))
    rt = _rt_graph((HOME, PROFILE, "tap p", 2))
    items = select_frontier(src, rt, visited=[], current_screen=HOME)
    assert items, "有 source 出边就该有 frontier"
    assert items[0].transition.to_screen == SEARCH
    assert items[0].unexplored is True
    assert all(i.unexplored is False for i in items[1:])


def test_within_explored_lower_visit_count_first():
    src = _src_graph((HOME, SEARCH, "tap s"), (HOME, PROFILE, "tap p"))
    rt = _rt_graph((HOME, SEARCH, "tap s", 3), (HOME, PROFILE, "tap p", 3),
                   visits={SEARCH: 5, PROFILE: 1})
    items = select_frontier(src, rt, visited=[], current_screen=HOME)
    assert [i.transition.to_screen for i in items] == [PROFILE, SEARCH]


def test_frontier_only_from_current_screen():
    src = _src_graph((HOME, SEARCH, "tap s"), (SEARCH, DETAIL, "tap d"))
    items = select_frontier(src, _rt_graph(), visited=[],
                            current_screen=SEARCH)
    assert [i.transition.to_screen for i in items] == [DETAIL]


def test_empty_frontier_returns_empty_list_not_error():
    src = _src_graph((HOME, SEARCH, "tap s"))
    # 当前屏没有 source 出边 → 空表（Loop 据此正常结束）
    assert select_frontier(src, _rt_graph(), visited=[],
                           current_screen=DETAIL) == []


def test_ordering_is_deterministic_and_identity_tie_broken():
    src = _src_graph((HOME, SEARCH, "tap b"), (HOME, SEARCH, "tap a"))
    items = select_frontier(src, _rt_graph(), visited=[], current_screen=HOME)
    keys = [(i.transition.from_screen, i.transition.to_screen,
             i.transition.trigger) for i in items]
    assert keys == sorted(keys)
    again = select_frontier(src, _rt_graph(), visited=[], current_screen=HOME)
    assert keys == [(i.transition.from_screen, i.transition.to_screen,
                     i.transition.trigger) for i in again]


def test_novelty_score_is_higher_for_unexplored():
    src = _src_graph((HOME, SEARCH, "tap s"), (HOME, PROFILE, "tap p"))
    rt = _rt_graph((HOME, PROFILE, "tap p", 2))
    items = select_frontier(src, rt, visited=[], current_screen=HOME)
    assert items[0].novelty > items[1].novelty


def test_frontier_item_is_frozen_value():
    item = FrontierItem(transition=ScreenTransition(from_screen=HOME,
                                                    to_screen=SEARCH),
                        novelty=1.0, unexplored=True)
    with pytest.raises(Exception):
        item.novelty = 2.0


# --- 接进主循环 -----------------------------------------------------------------


def _explorer(tmp_path, task_id, src, rt, **decider_kw):
    repo = drift_repo(tmp_path)
    store = SQLiteAgentStore(tmp_path / "agent.db")
    store.create_task(task_id, agent_type="explorer", goal="explore")
    ex = FakeExecutor()
    toolkit = AgentToolkit(
        guard=Guard(EnvKind.SANDBOX), executor=ex,
        locator_for=lambda t: [{"type": "accessibility_id", "value": t}],
        repository=repo, agent_db=store, task_id=task_id)
    decider = make_frontier_decider(src, rt, **decider_kw)
    return (Explorer(toolkit=toolkit, graph=src, agent_db=store,
                     task_id=task_id, decider=decider, repository=repo),
            store, ex)


def test_frontier_loop_ends_with_frontier_empty_outcome(tmp_path):
    # 当前屏 HomeView 没有任何 source 出边 → 首次 decide 就空 frontier
    explorer, store, _ = _explorer(tmp_path, "task_front", _src_graph(),
                                   _rt_graph())
    result = explorer.run()

    assert result.outcome is ExplorationOutcome.FRONTIER_EMPTY
    assert result.steps == 0
    assert store.get_task("task_front").outcome == "FRONTIER_EMPTY"


def test_frontier_decider_emits_action_for_top_item(tmp_path):
    src = _src_graph((HOME, SEARCH, "login_button"))
    calls = {"n": 0}

    def action_for(transition):
        # 只提议一次，之后视为无动作（避免真循环依赖页面变化）
        calls["n"] += 1
        if calls["n"] > 1:
            return None
        return ProposedAction(tool="tap", target=transition.trigger)

    explorer, store, ex = _explorer(tmp_path, "task_top", src, _rt_graph(),
                                    action_for=action_for)
    result = explorer.run()

    assert result.steps == 1
    assert ex.tap_calls == 1
    trace = store.get_trace("task_top")
    entry = [e for e in trace if e.step_index == 0 and e.decision][0]
    assert entry.decision["target"] == "login_button"
    assert entry.observation["screen"] == HOME


def test_frontier_decider_skips_items_without_action(tmp_path):
    """action_for 对顶项给 None → 跳到下一项，而不是把 None 当「结束」。"""
    src = _src_graph((HOME, SEARCH, "no_mapping"), (HOME, PROFILE, "skip_me"))
    proposals = [None, ProposedAction(tool="swipe")]

    def action_for(transition):
        return proposals.pop(0) if proposals else None

    explorer, store, ex = _explorer(tmp_path, "task_skip", src, _rt_graph(),
                                    action_for=action_for)
    result = explorer.run()

    assert result.steps == 1
    trace = store.get_trace("task_skip")
    entry = [e for e in trace if e.step_index == 0 and e.decision][0]
    assert entry.decision["tool"] == "swipe"
