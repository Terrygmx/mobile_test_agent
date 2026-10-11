"""Task 6.7 / P3-32：工具层接线补齐（3 知识 + 2 提案）。

M1 建的 22 个工具里只有 12 个有实现；`NOT_WIRED` 表是「哪些还没有」的可读
清单，但里面**点名等的是已完成的里程碑**（"Task 2.2" / "M5" / "M4/M6"——
它们在写下的那天就还没接线，指望它们交付是不成立的）。本任务：

1. **3 个知识工具**接线，且**全部经 `KnowledgeSources`**（F3）——不直接 SQL，
   不建平行检索口。`get_relevant_source` 是例外：源码元数据**不是**
   `KnowledgeSources` 的四查询之一，它由调用方注入（本仓的
   `build_agent_context` 已是同样形状，`agents/context.py`）。
2. **2 个提案工具**接线，且**结构上写不出「已决策」的行**（F10/F6）：
   - `create_test_candidate` 走 `generator/validator.py::validate_schema`
     （F5：`mta lint` 的**同一实现**），ERROR 拒；
   - `create_bug_candidate` 强制 `UNTRIAGED` / `evidence_score=0`，证据非空。
3. **`NOT_WIRED` 缩到 5**，且每条写清「谁真的欠着 + 为什么不猜」
   （决策③「列出不猜」：不为了让表好看而给 3 个诊断数据源编一个假实现）。
"""
from __future__ import annotations

import pytest

from agents.tools import (NOT_WIRED, AgentToolkit, ToolDependencyError,
                          ToolNotWired)
from executor.guard import EnvKind, Guard
from tests.fault_injection.fi_support import FakeExecutor, drift_repo

BUILD = "local"


# --- fixture ------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path):
    return drift_repo(tmp_path)


@pytest.fixture()
def store(tmp_path):
    from agents.storage import SQLiteAgentStore
    st = SQLiteAgentStore(tmp_path / "agent.db")
    st.create_task("task_1", agent_type="explorer", goal="explore Home")
    return st


class _CountingKnowledge:
    """计数替身：证明工具**走 KnowledgeSources** 而不是自己去查库（F3）。"""

    def __init__(self, graph=None, experiences=()):
        self.graph = graph
        self.experiences = list(experiences)
        self.calls: list[tuple] = []

    def experience_lookup(self, app_id, screen_id, target_id):
        self.calls.append(("experience_lookup", app_id, screen_id, target_id))
        return self.experiences

    def graph_query(self, app_id):
        self.calls.append(("graph_query", app_id))
        return self.graph

    def impact_of(self, target_id):       # 未被这两个工具消费，但协议要齐
        self.calls.append(("impact_of", target_id))
        return ()

    def trace_history(self, filters):
        self.calls.append(("trace_history", dict(filters)))
        return []


def _tk(**kw) -> AgentToolkit:
    return AgentToolkit(guard=Guard(EnvKind.SANDBOX), executor=FakeExecutor(),
                        build=BUILD, **kw)


SOURCE_META = {
    "screen_elements": [
        {"name": "HomeView",
         "elements": [{"id": "login_button", "type": "button",
                       "source_file": "HomeView.swift:12"},
                      {"id": "pay_button", "type": "button",
                       "source_file": "HomeView.swift:40"}]},
        {"name": "ProfileView", "elements": []},
    ],
    "file_index": {"HomeView.swift": 120},
}


# ======================================================================
# 1. 三个知识工具
# ======================================================================


def test_get_relevant_experience_goes_through_knowledge_sources():
    """F3：走 `KnowledgeSources.experience_lookup`，不直接查 experience 库。"""
    knowledge = _CountingKnowledge(experiences=[{"experience_id": "exp_1"}])
    res = _tk(knowledge=knowledge).call(
        "get_relevant_experience", app_id="com.demo", screen_id="HomeView",
        target_id="login_button")
    assert res.ok is True and res.guard is None          # 只读组不经 Guard
    assert res.value["experiences"] == [{"experience_id": "exp_1"}]
    assert knowledge.calls == [
        ("experience_lookup", "com.demo", "HomeView", "login_button")]


def test_knowledge_tools_fail_loud_without_the_dependency():
    """缺依赖报 `ToolDependencyError`，**不返回空结果**（否则 Agent 会把
    「没注入」读成「库里没有」，据此做出错误决策）。"""
    tk = _tk()          # 什么都没注入
    for tool, kwargs in (
            ("get_relevant_experience",
             {"app_id": "a", "screen_id": "s", "target_id": "t"}),
            ("get_graph_neighbors", {"app_id": "a", "screen_id": "s"}),
            ("get_relevant_source", {"screen_id": "s"})):
        with pytest.raises(ToolDependencyError):
            tk.call(tool, **kwargs)


def test_get_graph_neighbors_reports_both_directions():
    """邻接 = 出边 + 入边两个方向（图是无向的可达性事实，不是有向边集）。"""
    from graph.models import (RuntimeGraph, ScreenNode, ScreenTransition)
    graph = RuntimeGraph(
        app_id="com.demo", app_build=BUILD, source_of="source",
        nodes=(ScreenNode(screen_id="HomeView", source_of="source"),
               ScreenNode(screen_id="ProfileView", source_of="source")),
        transitions=(
            ScreenTransition("HomeView", "ProfileView",
                             "tap:HomeView.go_profile", source_of="source"),
            ScreenTransition("ProfileView", "HomeView",
                             "tap:ProfileView.back", source_of="source",
                             count=3),
            # 与 HomeView 无关的边——必须在结果里消失
            ScreenTransition("AView", "BView", "tap:AView.go", source_of="source"),
        ))
    knowledge = _CountingKnowledge(graph=graph)
    res = _tk(knowledge=knowledge).call(
        "get_graph_neighbors", app_id="com.demo", screen_id="HomeView")
    assert knowledge.calls == [("graph_query", "com.demo")]
    value = res.value
    assert value["in_graph"] is True
    assert {(n["screen"], n["direction"]) for n in value["neighbors"]} == {
        ("ProfileView", "outbound"), ("ProfileView", "inbound")}
    # 反钉：无关的 AView→BView 没被带进来（否则邻接退化成「整张图」）
    assert "AView" not in {n["screen"] for n in value["neighbors"]}
    inbound = next(n for n in value["neighbors"] if n["direction"] == "inbound")
    assert inbound["trigger"] == "tap:ProfileView.back" and inbound["count"] == 3


def test_get_graph_neighbors_marks_an_unknown_screen_as_not_in_graph():
    """未知屏**不是错误**（新屏是常态——与 `build_agent_context` 同口径），
    但必须说清 `in_graph=False`，不能让它读成「没有邻居=已穷尽」。"""
    from graph.models import RuntimeGraph
    knowledge = _CountingKnowledge(graph=RuntimeGraph(app_id="com.demo"))
    res = _tk(knowledge=knowledge).call(
        "get_graph_neighbors", app_id="com.demo", screen_id="BrandNewView")
    assert res.ok is True
    assert res.value["in_graph"] is False and res.value["neighbors"] == []


def test_get_relevant_source_slices_the_injected_metadata():
    """源码元数据**不是** `KnowledgeSources` 的四查询之一——注入而非新建
    检索口（与 `build_agent_context` 同一形状，`agents/context.py`）。"""
    res = _tk(source_metadata=SOURCE_META).call(
        "get_relevant_source", screen_id="HomeView")
    assert res.value["screen"] == "HomeView"
    assert [e["id"] for e in res.value["elements"]] == [
        "login_button", "pay_button"]


def test_get_relevant_source_returns_empty_for_an_unregistered_screen():
    res = _tk(source_metadata=SOURCE_META).call(
        "get_relevant_source", screen_id="NeverScannedView")
    assert res.ok is True and res.value["elements"] == []


# ======================================================================
# 2. create_test_candidate：过 lint（F5）才入库
# ======================================================================


def _case_dict(candidate_id: str = "cand_x", *,
               element="login_button") -> dict:
    return {
        "schema_version": "0.2", "id": candidate_id, "name": "点登录",
        "suite": "smoke", "steps": [
            {"action": "tap", "target": {"type": "element", "id": element}}],
    }


def _case_toolkit(repo, store, *, with_secrets=True):
    from environment.secrets import EnvSecretProvider
    return _tk(repository=repo, agent_db=store, task_id="task_1",
               secrets=EnvSecretProvider() if with_secrets else None)


def test_create_test_candidate_writes_a_pending_row(repo, store):
    tk = _case_toolkit(repo, store)
    res = tk.call("create_test_candidate", candidate_id="cand_x",
                  testcase=_case_dict())
    assert res.ok is True
    assert res.value["passed"] is True
    # ⚠️ 结构上写不出「已决策」的行：`review_status` 只有 `decide_candidate`
    # 能改（agents/storage.py 的 docstring），这里只能读到 PENDING。
    assert res.value["review_status"] == "PENDING"
    row = store.get_candidate("cand_x")
    assert row["fingerprint"] == res.value["fingerprint"]   # F8 去重前提


def test_create_test_candidate_rejects_a_schema_error(repo, store):
    """F5：**同一个** lint 实现。元数据里没有的元素必须拒——不是「先入库再说」。"""
    tk = _case_toolkit(repo, store)
    res = tk.call("create_test_candidate", candidate_id="cand_bad",
                  testcase=_case_dict(element="no_such_element"))
    assert res.value["passed"] is False
    assert res.value["failure_type"] == "SCHEMA_INVALID"
    assert res.value["issues"], "拒绝必须留痕（哪几条 ERROR）"
    assert store.get_candidate("cand_bad") is None      # 没有落库


def test_create_test_candidate_refuses_to_skip_validation(repo, store):
    """缺 `repository`/`secrets` → `ToolDependencyError`，**不是**「跳过校验
    直接入库」：绕过一次校验的候选会永久污染人工审核队列。"""
    tk = AgentToolkit(guard=Guard(EnvKind.SANDBOX), agent_db=store,
                      build=BUILD)          # repository / secrets 都没注入
    with pytest.raises(ToolDependencyError):
        tk.call("create_test_candidate", candidate_id="cand_x",
                testcase=_case_dict())
    assert store.get_candidate("cand_x") is None


def test_create_test_candidate_accepts_a_parsed_testcase(repo, store):
    """入参既可以是 dict 也可以是已解析 `TestCase`（同一条路径，不两套）。"""
    from testcase.schema import parse_testcase_dict
    tk = _case_toolkit(repo, store)
    res = tk.call("create_test_candidate", candidate_id="cand_parsed",
                  testcase=parse_testcase_dict(_case_dict("cand_parsed")))
    assert res.value["passed"] is True


# ======================================================================
# 3. create_bug_candidate：结构上写不出「已归因/已计分」
# ======================================================================


def test_create_bug_candidate_forces_untriaged_and_zero_score(store):
    tk = _tk(agent_db=store, task_id="task_1")
    res = tk.call("create_bug_candidate", candidate_id="bug_1",
                  title="登录按钮点不动",
                  evidence=[{"kind": "screenshot", "detail": "a.png"}])
    assert res.ok is True and res.value["passed"] is True
    # F10：假设不是事实。`evidence_score` 与 `attribution_hypothesis` **不是
    # 形参**——诊断重跑算出 11 分也不能从工具层写进来，只能由状态机推进。
    assert res.value["status"] == "DISCOVERED"
    row = store.get_bug_candidate("bug_1")
    assert row["attribution_hypothesis"] == "UNTRIAGED"
    assert row["evidence_score"] == 0


def test_create_bug_candidate_cannot_write_a_decided_status(store):
    """`status` 不是形参——传了以 `TypeError` fail-loud（storage 同款处置）。"""
    tk = _tk(agent_db=store, task_id="task_1")
    with pytest.raises(TypeError):
        tk.call("create_bug_candidate", candidate_id="bug_2", title="t",
                evidence=[{"kind": "screenshot"}], status="CONFIRMED")


def test_create_bug_candidate_requires_evidence(store):
    """无证据的 bug 候选 = LLM 的一句话，等于没有（设计 §8.2 的立场）。
    这里**拒绝**，不是留一条空证据行混进审核队列。"""
    tk = _tk(agent_db=store, task_id="task_1")
    res = tk.call("create_bug_candidate", candidate_id="bug_3", title="空证据",
                  evidence=[])
    assert res.value["passed"] is False
    assert res.value["failure_type"] == "NO_EVIDENCE"
    assert store.get_bug_candidate("bug_3") is None


def test_create_bug_candidate_is_reproducible_evidence_not_a_verdict(store):
    """`reproducible` 可以是工具层如实转述的观测（它只是记录），
    但 `attribution_hypothesis` 仍是 UNTRIAGED——观测不是结论。"""
    tk = _tk(agent_db=store, task_id="task_1")
    res = tk.call("create_bug_candidate", candidate_id="bug_4", title="t",
                  evidence=[{"kind": "reproduction", "detail": "3/3"}],
                  reproducible=True, reproduction_count=3,
                  failure_type="ELEMENT_NOT_FOUND")
    assert res.value["passed"] is True
    row = store.get_bug_candidate("bug_4")
    assert row["attribution_hypothesis"] == "UNTRIAGED"


# ======================================================================
# 4. NOT_WIRED：缩到 5，且每条说清谁真的欠着
# ======================================================================


def test_not_wired_shrinks_to_the_five_with_no_data_source():
    assert set(NOT_WIRED) == {
        "get_logs", "get_network_summary", "get_crash_info",
        "run_testcase", "run_candidate_test"}


def test_every_not_wired_entry_names_a_real_remaining_debt():
    """决策③「列出不猜」的反钉：**每条都必须说清缺的是什么数据**，
    且不许出现「等 Task 2.2 / M5 / M4/M6」这种**指向已完成里程碑**的
    指路——它们写下时就没接线，指望它们交付是不成立的（历史误报源）。"""
    stale = ("Task 2.2", "M4", "M5", "M6", "Task 3.4")
    for tool, reason in NOT_WIRED.items():
        assert not any(s in reason for s in stale), (tool, reason)
        assert len(reason) > 30, (tool, "说不清缺什么的数据源")


def test_the_three_five_now_really_run_and_the_rest_still_refuse(repo):
    """接线的那 5 个不再是 `ToolNotWired`；没接线的**照旧**明确报错——
    「没接线」必须是响亮的失败，不是空结果。"""
    tk = _tk(source_metadata=SOURCE_META, knowledge=_CountingKnowledge(),
             repository=repo)
    for tool, kwargs in (
            ("get_relevant_source", {"screen_id": "HomeView"}),
            ("get_relevant_experience",
             {"app_id": "a", "screen_id": "s", "target_id": "t"}),
            ("get_graph_neighbors", {"app_id": "a", "screen_id": "s"})):
        assert not isinstance(
            tk.call(tool, **kwargs).value, ToolNotWired)      # 不抛异常即可
    for tool in sorted(NOT_WIRED):
        with pytest.raises(ToolNotWired):
            tk.call(tool)
