"""Task 6.3 / P3-28：Scoped Agent Context（设计 §9.3 / F3）。

判据：
1. **只检索当前任务相关子集**——context 体积随任务收窄，而不是随 trace 总量
   增长（「不做把全部 Source + 全部 Trace 塞进去的全文 RAG」，设计原文）；
2. **F3**：全部经 P2 `KnowledgeSources` 四方法——import 级断言（Fake knowledge
   计数），不直接 SQL、不新建平行检索接口；
3. **口径登记（用户拍板 2026-10-11）**：`relevant_history` **不做屏级过滤**——
   trace 的 steps 表不记 screen（P1 schema 如此），`screen_id` 过滤键在
   `experience/knowledge.py` 里 fail-loud 不可用。用可用键（app_build + limit）
   取最近步骤，屏级收窄等数据源补齐（待拍板项保持登记，不闭）。
   钉子：builder **绝不**把 `screen_id` 塞进 filters——那会当场 ValueError。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from agents.context import AgentContext, build_agent_context
from agents.models import AgentState, AgentTask
from graph.models import ScreenNode


# --- 替身 ---------------------------------------------------------------------

@dataclass(frozen=True)
class _Graph:
    """`RuntimeGraph` 的替身——真图靠 `.nodes`（`graph/models.py::ScreenNode`）。"""

    nodes: tuple = ()


@dataclass
class _RecordingKnowledge:
    """KnowledgeSources 的记录替身：四方法各自记调用参数（F3 断言用）。"""

    graph: _Graph = field(default_factory=_Graph)
    experiences: tuple = ()
    history: tuple = ()
    calls: list = field(default_factory=list)

    def graph_query(self, app_id):
        self.calls.append(("graph_query", app_id))
        return self.graph

    def experience_lookup(self, app_id, screen_id, target_id):
        self.calls.append(("experience_lookup", app_id, screen_id, target_id))
        return list(self.experiences)

    def trace_history(self, filters):
        """真实现「先过滤、limit 最后取**最后** N 条」（knowledge.py 口径）。"""
        self.calls.append(("trace_history", dict(filters)))
        limit = filters.get("limit")
        return list(self.history if limit is None else self.history[-limit:])

    def impact_of(self, target_id):
        self.calls.append(("impact_of", target_id))
        return ()


def _task(goal="explore Home", agent_type="explorer") -> AgentTask:
    return AgentTask(task_id="t1", agent_type=agent_type, goal=goal,
                     state=AgentState.IDLE)


def _source_metadata():
    return {"screen_elements": [
        {"name": "LoginView", "elements": [
            {"id": "username_field", "type": "input"},
            {"id": "login_button", "type": "button"}]},
        {"name": "HomeView", "elements": [
            {"id": "go_profile", "type": "button"},
            {"id": "go_search", "type": "button"}]},
        {"name": "ProfileView", "elements": [
            {"id": "logout_button", "type": "button"}]},
    ]}


# --- 1. 四字段经 KnowledgeSources（F3） -----------------------------------------


def test_all_four_fields_go_through_knowledge_sources():
    k = _RecordingKnowledge()
    build_agent_context(_task(), k, app_id="com.demo",
                        current_screen="HomeView", target_id="go_profile",
                        app_build="1026", source_metadata=_source_metadata())
    kinds = [c[0] for c in k.calls]
    # graph_query / experience_lookup / trace_history 都被走；impact_of 不在
    # 本 builder 的四个字段里（那是 planner 的），少调是**对的**。
    assert "graph_query" in kinds and "experience_lookup" in kinds \
        and "trace_history" in kinds
    assert "impact_of" not in kinds


def test_experience_lookup_gets_screen_and_target():
    k = _RecordingKnowledge()
    build_agent_context(_task(), k, app_id="com.demo",
                        current_screen="HomeView", target_id="go_profile")
    exp_calls = [c for c in k.calls if c[0] == "experience_lookup"]
    assert exp_calls == [("experience_lookup", "com.demo", "HomeView",
                          "go_profile")]


# --- 2. 体积随任务收窄 -----------------------------------------------------------


def test_context_narrows_to_the_current_screen():
    """三个屏的 metadata → context 的 source 子集只有**当前屏**。"""
    k = _RecordingKnowledge(graph=_Graph(nodes=(
        ScreenNode(screen_id="LoginView"), ScreenNode(screen_id="HomeView"),
        ScreenNode(screen_id="ProfileView"))))
    ctx = build_agent_context(_task(), k, app_id="com.demo",
                              current_screen="HomeView",
                              source_metadata=_source_metadata())
    assert [s.screen_id for s in ctx.current_screen] == ["HomeView"], \
        "graph 子集只含当前屏，不是全图"
    assert [e["id"] for e in ctx.relevant_source] == ["go_profile", "go_search"], \
        "source 子集只含当前屏的元素，不是全部 5 个"


def test_unknown_screen_yields_empty_subsets_not_a_crash():
    """当前屏不在图/metadata 里 → 子集为空（新屏探索是常态，不是错误）。"""
    k = _RecordingKnowledge()
    ctx = build_agent_context(_task(), k, app_id="com.demo",
                              current_screen="BrandNewView",
                              source_metadata=_source_metadata())
    assert ctx.current_screen == () and ctx.relevant_source == ()


def test_history_is_limited_not_whole_trace():
    """relevant_history 取**最近 N 条**（limit 进 filters）——不是全量 trace。"""
    k = _RecordingKnowledge(history=tuple(f"step_{i}" for i in range(50)))
    ctx = build_agent_context(_task(), k, app_id="com.demo",
                              current_screen="HomeView", history_limit=5)
    assert len(ctx.relevant_history) == 5
    hist = [c for c in k.calls if c[0] == "trace_history"]
    assert hist[0][1].get("limit") == 5


def test_history_never_passes_the_screen_id_filter():
    """**用户拍板口径（2026-10-11）**：屏级过滤等数据源补齐——builder 绝不把
    `screen_id` 塞进 filters（真 `KnowledgeSources` 对它是 fail-loud）。"""
    k = _RecordingKnowledge()
    build_agent_context(_task(), k, app_id="com.demo",
                        current_screen="HomeView")
    hist = [c for c in k.calls if c[0] == "trace_history"]
    assert "screen_id" not in hist[0][1], hist


def test_no_screen_means_no_screen_scoped_lookups():
    """没观测到屏（任务刚开始）→ 三个屏级检索都跳过，context 其余为空——
    不拿 `None` 去查（`experience_lookup(None, None)` 是瞎查）。"""
    k = _RecordingKnowledge()
    ctx = build_agent_context(_task(), k, app_id="com.demo")
    kinds = [c[0] for c in k.calls]
    assert "experience_lookup" not in kinds
    assert ctx.relevant_experience == () and ctx.current_screen == () \
        and ctx.relevant_source == (), \
        "没有屏就不该切 source 子集（否则会把全量元素塞进 context）"


# --- 3. 模型纪律 ----------------------------------------------------------------


def test_agent_context_is_frozen():
    """检索结果是**快照**——可变会让「决策依据」事后可改（审计根基）。"""
    k = _RecordingKnowledge()
    ctx = build_agent_context(_task(), k, app_id="com.demo",
                              current_screen="HomeView",
                              source_metadata=_source_metadata())
    with pytest.raises(Exception):
        ctx.app_id = "other"


def test_app_id_is_required():
    """`graph_query` 必须给 app_id（设计两参签名不可实现的偏离登记，见模块
    docstring）——不给就 fail-loud，不从 goal 里猜。"""
    with pytest.raises(ValueError, match="app_id"):
        build_agent_context(_task(), _RecordingKnowledge())
