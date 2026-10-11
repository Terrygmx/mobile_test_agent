"""agent_metrics.py — Agent 指标聚合（设计 §13.1；Task 6.5 / P3-30）。

设计 §13.1 的五组指标里**能从库里自动算的**全在这里；`Selection Precision` /
`Diagnosis Accuracy` 依赖**人工标注真值**——库里没有真值，collect **绝不**
假装能算（字段在 AgentMetrics 上不存在，`test_collect_never_produces_ground_
truth_metrics` 钉死）；它们只在 Benchmark 的**标注集**内计算
（`agents/benchmark.py`，每类 case 自带预期）。

## 口径登记（分母是指标的全部语义）

| 指标 | 分子 | 分母 | 为什么 |
|---|---|---|---|
| Duplicate Rate | `DUPLICATE` 留痕数 | 入库候选 + `SEMANTIC_REJECTED` + `DUPLICATE` | 「生成了」= 产出了候选形状；`SCHEMA_INVALID` 连形状都没解析出来，进分母会让重复率被 schema 噪声稀释 |
| Reproduction Rate | `reproducible=True` | `reproducible IS NOT NULL` 的 bug | 「还没跑复现」既不是真也不是假；进分母会把「没判」算成「复现不了」 |
| Duplicate Bug Rate | `status=DUPLICATE` | 全部 bug | 与上面刻意不同：dedup 合并是**判定后**的事，全量分母才反映重复占比 |
| Review Cost | `reviewed_at` 非空的行数 / 平均 `reviewed_at - created_at` | 三类候选各自统计 | 未决行不构成「人工成本」——设计 13.1 的话术是「人工复核量应该下降」 |

设计 §13.1 的重心原话：「最重要的不是 LLM 调用量，而是 Coverage / Cost /
Human Effort 三者的关系」——LLM Cost 在这里只是配菜（`llm_calls_by_agent`），
Human Review Cost 与产出计数才是主菜。

模式沿 `report/experience_metrics.py`：**纯计算函数 + collect 搬运函数 +
render HTML 片段**，渲染层不读库。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

__all__ = ["AgentMetrics", "collect_agent_metrics", "compute_agent_metrics",
           "render_agent_metrics_section"]

# 留痕表里「产出过候选形状」的原因（Duplicate Rate 分母的组成部分）。
_SHAPEFUL_REJECTIONS = frozenset({"SEMANTIC_REJECTED", "DUPLICATE"})
_DISCOVERY_KINDS = ("NEW_SCREEN", "NEW_TRANSITION", "NEW_ELEMENT", "ANOMALY")
_TIME_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


@dataclass(frozen=True)
class AgentMetrics:
    """设计 §13.1 的可自动计算子集（frozen：指标是快照，不是活引用）。"""

    # Generation
    generated: int = 0
    accepted: int = 0
    duplicates: int = 0
    duplicate_rate: float | None = None
    # Exploration
    new_screens: int = 0
    new_transitions: int = 0
    new_elements: int = 0
    anomalies: int = 0
    bugs_found: int = 0
    bugs_confirmed: int = 0
    # Diagnosis
    reproduction_rate: float | None = None
    duplicate_bug_rate: float | None = None
    # Overall
    llm_calls_by_agent: dict = None          # type: ignore[assignment]
    llm_calls_total: int = 0
    reviewed_count: int = 0
    review_latency_seconds: float | None = None


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, _TIME_FMT)
    except ValueError:
        return None


def compute_agent_metrics(*, candidate_rows: list[dict],
                          rejection_rows: list[dict],
                          bug_rows: list[dict],
                          discovery_rows: list[dict],
                          task_rows: list[dict],
                          trace_rows: list[dict]) -> AgentMetrics:
    """纯计算：全部输入是已取出的行（dict）——I/O 在 collect，规则在这里。"""
    generated = len(candidate_rows) + sum(
        1 for r in rejection_rows
        if r.get("reason") in _SHAPEFUL_REJECTIONS)
    duplicates = sum(1 for r in rejection_rows
                     if r.get("reason") == "DUPLICATE")
    accepted = sum(1 for c in candidate_rows
                   if c.get("review_status") == "ACCEPT")

    kinds = [r.get("kind") for r in discovery_rows]
    judged = [b for b in bug_rows if b.get("reproducible") is not None]
    # ⚠️ sqlite 没有布尔类型：`reproducible` 回来是 **int 0/1**，`is True`
    # 会把「真复现」判成「没复现」——经 DB 取的行一律走真值判断（纯 dict
    # 喂进来的 bool 同样命中）。
    reproduced = sum(1 for b in judged if b.get("reproducible"))

    # LLM Cost：trace 的 llm_calls_used 按 task 的 agent_type 聚合。
    agent_of = {t.get("task_id"): t.get("agent_type") or "?" for t in task_rows}
    by_agent: dict[str, int] = {}
    for tr in trace_rows:
        at = agent_of.get(tr.get("task_id"), "?")
        by_agent[at] = by_agent.get(at, 0) + int(tr.get("llm_calls_used") or 0)

    # Human Review Cost：三类候选的已决行，耗时 = reviewed_at - created_at。
    latencies: list[float] = []
    reviewed = 0
    for row in (*candidate_rows, *discovery_rows):
        end, start = _parse(row.get("reviewed_at")), _parse(row.get("created_at"))
        if end is not None and start is not None:
            reviewed += 1
            latencies.append(max((end - start).total_seconds(), 0.0))
    # bug 候选的「审核」列是 review_decision（M5 的状态机落库），同权计入。
    for row in bug_rows:
        end, start = _parse(row.get("reviewed_at")), _parse(row.get("created_at"))
        if end is not None and start is not None:
            reviewed += 1
            latencies.append(max((end - start).total_seconds(), 0.0))

    return AgentMetrics(
        generated=generated, accepted=accepted, duplicates=duplicates,
        duplicate_rate=_rate(duplicates, generated),
        new_screens=kinds.count("NEW_SCREEN"),
        new_transitions=kinds.count("NEW_TRANSITION"),
        new_elements=kinds.count("NEW_ELEMENT"),
        anomalies=kinds.count("ANOMALY"),
        bugs_found=len(bug_rows),
        bugs_confirmed=sum(1 for b in bug_rows
                           if b.get("status") == "CONFIRMED"),
        reproduction_rate=_rate(reproduced, len(judged)),
        duplicate_bug_rate=_rate(sum(1 for b in bug_rows
                                     if b.get("status") == "DUPLICATE"),
                                 len(bug_rows)),
        llm_calls_by_agent=by_agent,
        llm_calls_total=sum(by_agent.values()),
        reviewed_count=reviewed,
        review_latency_seconds=_mean(latencies))


def collect_agent_metrics(*, agent_db, trace_db) -> AgentMetrics:
    """从 agent.db（+ 可选 trace.db）搬行，交给纯计算（模式同 experience_metrics）。

    `agent_db` 收**路径或 `SQLiteAgentStore` 实例**（指标是只读聚合，不该
    逼调用方先掏出路径字符串）。`trace_db` 形参保留（调用方签名对称 / 未来
    跨库指标），当前指标全部来自 agent.db——不给（None）不报错。
    """
    path = getattr(agent_db, "_path", None) or str(agent_db)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        candidate_rows = [dict(r) for r in conn.execute(
            "SELECT candidate_id, review_status, reviewed_at, created_at"
            " FROM test_candidates")]
        rejection_rows = [dict(r) for r in conn.execute(
            "SELECT rejection_id, reason FROM test_candidate_rejections")]
        bug_rows = [dict(r) for r in conn.execute(
            "SELECT candidate_id, status, reproducible, reviewed_at,"
            " created_at FROM bug_candidates")]
        discovery_rows = [dict(r) for r in conn.execute(
            "SELECT event_id, kind, review_status, reviewed_at, created_at"
            " FROM discovery_events")]
        task_rows = [dict(r) for r in conn.execute(
            "SELECT task_id, agent_type FROM agent_tasks")]
        trace_rows = [dict(r) for r in conn.execute(
            "SELECT task_id, llm_calls_used FROM agent_trace")]
    finally:
        conn.close()
    return compute_agent_metrics(
        candidate_rows=candidate_rows, rejection_rows=rejection_rows,
        bug_rows=bug_rows, discovery_rows=discovery_rows,
        task_rows=task_rows, trace_rows=trace_rows)


# --- 渲染（HTML 片段；纯字符串拼接，无 I/O） -------------------------------------


def _esc(text) -> str:
    import html
    return html.escape(str(text))


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def render_agent_metrics_section(m: AgentMetrics) -> str:
    """`mta report` 的 agent 指标段（渲染层不读库，模式同 experience_metrics）。"""
    by_agent = ", ".join(f"{_esc(k)}: {v}" for k, v in
                         sorted((m.llm_calls_by_agent or {}).items())) or "—"
    return f"""
<section class="agent-metrics">
  <h2>Agent 指标（设计 §13.1；P3-30）</h2>
  <div class="cards">
    <div class="card"><b>Generation</b>
      <div>generated {_esc(m.generated)} · accepted {_esc(m.accepted)} ·
           duplicate rate {_pct(m.duplicate_rate)}</div></div>
    <div class="card"><b>Exploration</b>
      <div>新屏 {_esc(m.new_screens)} · 新转移 {_esc(m.new_transitions)} ·
           新元素 {_esc(m.new_elements)} · 异常 {_esc(m.anomalies)} ·
           bugs {_esc(m.bugs_found)}（confirmed {_esc(m.bugs_confirmed)}）</div></div>
    <div class="card"><b>Diagnosis</b>
      <div>复现率 {_pct(m.reproduction_rate)} ·
           duplicate bug rate {_pct(m.duplicate_bug_rate)}</div></div>
    <div class="card"><b>LLM Cost</b><div>{by_agent}
      （总 {_esc(m.llm_calls_total)}）</div></div>
    <div class="card"><b>Human Review Cost</b>
      <div>已决 {_esc(m.reviewed_count)} 条 ·
           平均耗时 {_esc(m.review_latency_seconds)}s</div></div>
  </div>
  <p class="note">Selection Precision / Diagnosis Accuracy 依赖标注真值，
只在 Benchmark 标注集内计算（`mta agent benchmark`）。</p>
</section>"""
