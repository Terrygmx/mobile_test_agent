"""Task 6.5 / P3-30：Agent 指标（设计 §13.1）——分母口径与数据源钉子。

判据：
1. **分母口径**（plan Steps 明示）：Duplicate Rate = DUPLICATE 留痕数 /
   generated，generated = 入库候选 + SEMANTIC_REJECTED + DUPLICATE 留痕
   （SCHEMA_INVALID 连候选形状都没有，**不进分母**——口径登记见模块 docstring）；
2. **LLM Cost 来自 agent_trace 的 llm_calls_used**，按 agent_type 聚合
   （F4 的用量真值源就是 LLMBudget 的落痕处）；
3. **Human Review Cost**：三类候选的已决数 + 决策耗时（reviewed_at - created_at）；
4. **口径登记**：Selection Precision / Diagnosis Accuracy 依赖标注真值——
   **collect 不产出**（只在 Benchmark 标注集内计算，`agents/benchmark.py`）。
"""
from __future__ import annotations

import sqlite3

import pytest

from agents.storage import SQLiteAgentStore
from report.agent_metrics import collect_agent_metrics, compute_agent_metrics


# --- 数据准备 -------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path) -> SQLiteAgentStore:
    return SQLiteAgentStore(tmp_path / "agent.db")


def _rows(store: SQLiteAgentStore) -> tuple[list, list]:
    """agent_tasks / agent_trace 的整表快照（collect 的输入形状）。"""
    conn = store._connect()
    try:
        tasks = [dict(r) for r in conn.execute(
            "SELECT task_id, agent_type FROM agent_tasks")]
        traces = [dict(r) for r in conn.execute(
            "SELECT task_id, llm_calls_used FROM agent_trace")]
    finally:
        conn.close()
    return tasks, traces


def _seed(store: SQLiteAgentStore, *, candidates=(), rejections=(),
          bugs=(), discoveries=(), tasks_traces=()):
    for kw in candidates:
        cid = kw.pop("cid")
        review_status = kw.pop("review_status", "PENDING")
        store.save_candidate(cid, **kw)
        if review_status != "PENDING":
            store.decide_candidate(cid, review_status=review_status,
                                   reviewer="seed")
    for kw in rejections:
        rid = kw.pop("rid")
        store.save_rejection(rid, **kw)
    for kw in bugs:
        bid = kw.pop("bid")
        status = kw.pop("_status", "DISCOVERED")
        store.save_bug_candidate(bid, **kw)
        if status != "DISCOVERED":
            # 状态推进的唯一入口是 decide_bug_candidate（save 连形参都不收）
            store.decide_bug_candidate(bid, status=status)
    for kw in discoveries:
        eid = kw.pop("eid")
        store.save_discovery_event(eid, **kw)
    for task_id, agent_type, traces in tasks_traces:
        store.create_task(task_id, agent_type=agent_type, goal="g")
        for t in traces:
            store.append_trace(task_id, **t)


def _candidate(cid, review_status="PENDING"):
    return dict(cid=cid, testcase_yaml="id: x\nschema_version: '0.2'\n",
                generation_evidence={"gap_ids": ["g1"]},
                generated_by="llm", fingerprint=f"fp_{cid}",
                dry_run_status="PASS", review_status=review_status)


def _bug(bid, *, status="DISCOVERED", reproducible=None, count=0):
    # `status` 不是 save_bug_candidate 的形参（唯一写路径是 decide_bug_candidate）
    return dict(bid=bid, title="t", evidence=[],
                failure_type="APP_CRASH",
                reproducible=reproducible, reproduction_count=count,
                _status=status)


def _rejection(rid, reason, fingerprint="fp"):
    return dict(rid=rid, reason=reason, fingerprint=fingerprint)


def _discovery(eid, kind):
    return dict(eid=eid, kind=kind)


def _trace_task(task_id, agent_type, llm_calls):
    return (task_id, agent_type,
            [{"step_index": i, "llm_calls_used": n}
             for i, n in enumerate(llm_calls)])


# --- 1. Generation ---------------------------------------------------------------


def test_duplicate_rate_denominator_is_pinned(store):
    """`dup / generated`，generated = 入库候选 + SEMANTIC_REJECTED + DUPLICATE。

    SCHEMA_INVALID **不进分母**：它连候选的形状都没解析出来，把它算成
    「生成了」会让重复率被 schema 噪声稀释——口径登记，钉死。
    """
    _seed(store,
          candidates=[_candidate("c1", "ACCEPT"), _candidate("c2")],
          rejections=[_rejection("r1", "DUPLICATE"),
                      _rejection("r2", "SEMANTIC_REJECTED"),
                      _rejection("r3", "SCHEMA_INVALID", fingerprint=None)])
    tasks, traces = _rows(store)
    m = compute_agent_metrics(
        candidate_rows=store.list_candidates(),
        rejection_rows=store.list_rejections(),
        bug_rows=store.list_bug_candidates(),
        discovery_rows=store.list_discovery_events(),
        task_rows=tasks, trace_rows=traces)
    assert m.generated == 4          # 2 入库 + SEMANTIC_REJECTED + DUPLICATE
    assert m.duplicates == 1
    assert m.duplicate_rate == 0.25


def test_generation_counts_zero_when_nothing_generated():
    m = compute_agent_metrics(candidate_rows=[], rejection_rows=[],
                              bug_rows=[], discovery_rows=[],
                              task_rows=[], trace_rows=[])
    assert m.generated == 0 and m.accepted == 0 and m.duplicate_rate is None


# --- 2. Exploration --------------------------------------------------------------


def test_exploration_counts_by_discovery_kind(store):
    _seed(store,
          discoveries=[_discovery("e1", "NEW_SCREEN"),
                       _discovery("e2", "NEW_SCREEN"),
                       _discovery("e3", "NEW_TRANSITION"),
                       _discovery("e4", "NEW_ELEMENT"),
                       _discovery("e5", "ANOMALY")],
          bugs=[_bug("b1", status="CONFIRMED"), _bug("b2")])
    tasks, traces = _rows(store)
    m = compute_agent_metrics(
        candidate_rows=[], rejection_rows=[],
        bug_rows=store.list_bug_candidates(),
        discovery_rows=store.list_discovery_events(),
        task_rows=tasks, trace_rows=traces)
    assert m.new_screens == 2 and m.new_transitions == 1
    assert m.new_elements == 1 and m.anomalies == 1
    assert m.bugs_found == 2 and m.bugs_confirmed == 1


# --- 3. Diagnosis ----------------------------------------------------------------


def test_reproduction_rate_denominator_is_judged_bugs(store):
    """复现率 = reproducible=True / **已做出复现判定的** bug
    （`reproducible IS NOT NULL`）——没判过的既不是真也不是假，进分母会
    把「还没跑复现」算成「复现不了」。"""
    _seed(store, bugs=[
        _bug("b1", reproducible=True, count=3),
        _bug("b2", reproducible=False, count=1),
        _bug("b3", status="HUMAN_REVIEW"),          # reproducible=None：未判定
        _bug("b4", status="DUPLICATE")])
    tasks, traces = _rows(store)
    m = compute_agent_metrics(
        candidate_rows=[], rejection_rows=[],
        bug_rows=store.list_bug_candidates(), discovery_rows=[],
        task_rows=tasks, trace_rows=traces)
    assert m.reproduction_rate == 0.5               # 1/2（b3 不进分母）
    assert m.duplicate_bug_rate == 0.25             # 1/4（全量分母）


# --- 4. LLM Cost + Human Review Cost ---------------------------------------------


def test_llm_cost_aggregates_by_agent_type(store):
    _seed(store, tasks_traces=[
        _trace_task("exp_1", "explorer", [1, 2, 0]),
        _trace_task("exp_2", "explorer", [3]),
        _trace_task("dia_1", "diagnosis", [0, 0]),
    ])
    tasks, traces = _rows(store)
    m = compute_agent_metrics(candidate_rows=[], rejection_rows=[],
                              bug_rows=[], discovery_rows=[],
                              task_rows=tasks, trace_rows=traces)
    assert m.llm_calls_by_agent == {"explorer": 6, "diagnosis": 0}
    assert m.llm_calls_total == 6


def test_human_review_cost_counts_and_latency(store):
    _seed(store, candidates=[
        _candidate("c1", "ACCEPT"), _candidate("c2", "REJECT"),
        _candidate("c3")])                        # 未决不进 review 成本
    store.decide_candidate("c1", review_status="ACCEPT", reviewer="alice")
    store.decide_candidate("c2", review_status="REJECT", reviewer="bob")
    tasks, traces = _rows(store)
    m = compute_agent_metrics(
        candidate_rows=store.list_candidates(), rejection_rows=[],
        bug_rows=[], discovery_rows=[], task_rows=tasks, trace_rows=traces)
    assert m.reviewed_count == 2
    assert m.review_latency_seconds is not None   # 同秒决策 → 极小非负值


# --- 5. collect 不产出依赖真值的指标（口径登记） ------------------------------------


def test_collect_never_produces_ground_truth_metrics(store):
    """Selection Precision / Diagnosis Accuracy 需要人工标注真值——`collect`
    从库里拿不到真值，**绝不**假装能算（只在 Benchmark 标注集内算）。"""
    m = collect_agent_metrics(agent_db=store, trace_db=None)
    assert not hasattr(m, "selection_precision")
    assert not hasattr(m, "diagnosis_accuracy")


# --- 6. collect 端到端（真实库） ----------------------------------------------------


def test_collect_agent_metrics_from_a_real_store(store):
    _seed(store, candidates=[_candidate("c1", "ACCEPT")],
          bugs=[_bug("b1", reproducible=True, count=3)],
          discoveries=[_discovery("e1", "NEW_SCREEN")],
          tasks_traces=[_trace_task("exp_1", "explorer", [2])])
    m = collect_agent_metrics(agent_db=store, trace_db=None)
    assert m.generated == 1 and m.accepted == 1
    assert m.new_screens == 1 and m.bugs_found == 1
    assert m.reproduction_rate == 1.0
    assert m.llm_calls_total == 2


def test_collect_handles_a_fresh_database(store):
    """agent_db 里还没有业务行（新工程）→ 全零指标，不炸。"""
    m = collect_agent_metrics(agent_db=store, trace_db=None)
    assert m.generated == 0 and m.bugs_found == 0 and m.llm_calls_total == 0
