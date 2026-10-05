"""builder.py — Runtime Graph 构建（设计 12.1；Task 5.1 / P2-11）。

```text
Runtime Graph ← P1 Trace 中已执行用例**实际到达**的 Screen 转移
```

## 什么算「到达了一个屏」

只有两类**观测**算数（E12：不做自动探索，没有证据就没有节点）：

| 证据 | 来源 | 为什么算观测 |
|---|---|---|
| `wait_screen` | `wait_for screen:X` 步骤成功 | wait 引擎校验过 X 的 marker——屏是被**验证**过的，不是猜的 |
| `recovery_observed` | 恢复期 `stage: screen / outcome: FOUND` | 引擎当场用 marker 识别出了屏（`source/screen.py::current_screen`） |

**刻意不算的一类**：`steps.target_id` 形如 `Screen.element` 时能读出「目标登记在
哪个屏」——但那是**元数据声明**，不是运行时观测。把它当 runtime 证据会让
Runtime Graph 里混进 Source Graph 的信息，设计 12.2 的 diff（runtime vs source）
就失去意义了。若将来确实需要，应作为第三类证据（如 `element_found`）单独加入
**并同步 diff 口径**，而不是悄悄并进现有两类。

## 转移的触发条件

`trigger` = **紧邻「目标屏被观测到」之前的那个步骤**（`<step_type>:<target_id>`）。
设计 12.2 的 `CHANGED`（「同一触发条件，目标 Screen 变化」）要用它做连接键，
所以它进转移的唯一键：同一对屏由不同动作触发是**两条**不同的转移。

## 时间

`steps` 表**没有时间戳**（P1 schema 如此），所以 `observed_at` 取所属
`testcase_run` 的 `end_time`（缺则 `start_time`）——可得的最细粒度，不假装更细。
`first_seen` / `last_seen` 由此而来。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from graph.models import (
    EVIDENCE_RECOVERY_OBSERVED,
    EVIDENCE_WAIT_SCREEN,
    RUNTIME,
    RuntimeGraph,
    ScreenNode,
    ScreenTransition,
    TraceStep,
)

__all__ = [
    "SCREEN_TARGET_PREFIX",
    "is_screen_wait",
    "observed_screens",
    "build_runtime_graph",
    "read_trace_steps",
]

SCREEN_TARGET_PREFIX = "screen:"
"""`wait_for` 的屏目标形态：`screen:<ScreenId>`（P1 的 `WaitSpec.target.type`）。"""

# 步骤状态里哪些算「这一步达成了」
_OK_STATUSES = ("SUCCESS", "RECOVERED")


def is_screen_wait(step: TraceStep) -> bool:
    """这一步是不是「等待某屏出现」且**成功**了。"""
    return (step.step_type == "wait_for"
            and (step.status or "") in _OK_STATUSES
            and (step.target_id or "").startswith(SCREEN_TARGET_PREFIX))


def observed_screens(step: TraceStep) -> list[tuple[str, str]]:
    """这一步给出的屏观测 `[(screen_id, evidence), ...]`（可能为空/多条）。

    纯函数、无副作用；两类证据的来源见模块 docstring。失败步骤（status 不在
    `SUCCESS`/`RECOVERED`）的 `wait_for` 不算——**等待超时意味着没到达**。
    """
    out: list[tuple[str, str]] = []
    if is_screen_wait(step):
        out.append((step.target_id[len(SCREEN_TARGET_PREFIX):],
                    EVIDENCE_WAIT_SCREEN))
    for st in _stages(step.detail):
        if (st.get("stage") == "screen" and st.get("outcome") == "FOUND"
                and st.get("screen")):
            out.append((st["screen"], EVIDENCE_RECOVERY_OBSERVED))
    return out


def _stages(detail: dict | None) -> list[dict]:
    """步骤 detail 里恢复引擎的 stages（成功与未恢复两条路径各一个键）。"""
    if not detail:
        return []
    section = detail.get("recovery") or detail.get("recovery_attempted") or {}
    return section.get("stages") or []


def _trigger_of(step: TraceStep | None) -> str:
    if step is None:
        return ""
    return f"{step.step_type}:{step.target_id or ''}"


def build_runtime_graph(steps: list[TraceStep], *, app_id: str = "",
                        app_build: str = "") -> RuntimeGraph:
    """**纯函数**：trace 步骤行 → Runtime Graph。

    按 `testcase_run_id` 分组、按 `step_index` 排序，逐 run 抽出「屏观测序列」，
    再把相邻的不同屏记成一条转移（同 run 内连续重复的同一屏只算一次访问——
    一个屏被连点三次不产生三条自环转移）。

    跨 run 合并：节点的 `visit_count` 累加、`evidence` 取并集、`first/last_seen`
    取最早/最晚；转移按 `(from, to, trigger)` 合并计数。
    """
    by_run: dict[int, list[TraceStep]] = {}
    for st in steps:
        by_run.setdefault(st.testcase_run_id, []).append(st)

    nodes: dict[str, dict] = {}
    trans: dict[tuple[str, str, str], dict] = {}

    for run_id in sorted(by_run):
        rows = sorted(by_run[run_id], key=lambda s: s.step_index)
        observations: list[tuple[str, str, str | None, str]] = []
        prev: TraceStep | None = None
        for st in rows:
            for screen, evidence in observed_screens(st):
                observations.append((screen, evidence, st.observed_at,
                                     _trigger_of(prev)))
            prev = st

        for screen, evidence, at, _ in observations:
            slot = nodes.setdefault(screen, {"count": 0, "evidence": set(),
                                             "first": None, "last": None})
            slot["count"] += 1
            slot["evidence"].add(evidence)
            slot["first"] = _min_ts(slot["first"], at)
            slot["last"] = _max_ts(slot["last"], at)

        for (a, _ea, at_a, _ta), (b, _eb, at_b, trigger) in zip(
                observations, observations[1:]):
            if a == b:
                continue        # 同一屏连续观测 = 一次访问，不产生自环转移
            key = (a, b, trigger)
            slot = trans.setdefault(key, {"count": 0, "first": None,
                                          "last": None})
            slot["count"] += 1
            slot["first"] = _min_ts(slot["first"], at_b or at_a)
            slot["last"] = _max_ts(slot["last"], at_b or at_a)

    return RuntimeGraph(
        app_id=app_id, app_build=app_build,
        nodes=tuple(
            ScreenNode(screen_id=s, source_of=RUNTIME,
                       visit_count=v["count"],
                       evidence=tuple(sorted(v["evidence"])),
                       first_seen=v["first"], last_seen=v["last"])
            for s, v in sorted(nodes.items())),
        transitions=tuple(
            ScreenTransition(from_screen=k[0], to_screen=k[1], trigger=k[2],
                             source_of=RUNTIME, count=v["count"],
                             first_seen=v["first"], last_seen=v["last"])
            for k, v in sorted(trans.items())))


def _min_ts(a: str | None, b: str | None) -> str | None:
    """ISO 串可直接字典序比较（同为 UTC、同格式）；None 不参与。"""
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


def _max_ts(a: str | None, b: str | None) -> str | None:
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


# --- trace 读取（本模块唯一的 I/O 区：只搬数据，不做判定） ------------------


def read_trace_steps(trace_db: str | Path, *,
                     app_build: str | None = None
                     ) -> tuple[list[TraceStep], str, str]:
    """读 trace 的 steps → `(steps, app_id, app_build)`。

    `app_build=None` 时取库里**全部** run，但要求它们的 `(app_id, app_build)`
    一致——两个 build 的屏混进同一张图是错的（diff 的 base/build 语义会失效），
    所以混了直接 fail-loud 而不是挑一个。

    时间取所属 testcase_run 的 `end_time`（缺则 `start_time`）；`steps` 表本身
    没有时间戳（P1 schema 如此），不假装更细。
    """
    conn = sqlite3.connect(str(trace_db))
    conn.row_factory = sqlite3.Row
    try:
        where, params = "", []
        if app_build is not None:
            where = " WHERE r.app_build=?"
            params.append(app_build)
        runs = conn.execute(
            f"SELECT DISTINCT r.app_bundle_id AS app_id, r.app_build AS build"
            f" FROM runs r{where}", params).fetchall()
        scopes = {(r["app_id"] or "", r["build"] or "") for r in runs}
        if not scopes:
            return [], "", (app_build or "")
        if len(scopes) > 1:
            raise ValueError(
                f"trace 里有多个 (app_id, app_build) 范围：{sorted(scopes)}"
                f"；图必须限定在单一范围（用 app_build= 过滤或分开建图）")
        app_id, build = next(iter(scopes))

        rows = conn.execute(
            "SELECT s.testcase_run_id, s.step_index, s.step_type, s.target_id,"
            " s.status, s.detail_json,"
            " COALESCE(t.end_time, t.start_time) AS observed_at"
            " FROM steps s JOIN testcase_runs t"
            " ON t.id = s.testcase_run_id"
            " JOIN runs r ON r.run_id = t.run_id"
            + (" WHERE r.app_build=?" if app_build is not None else "")
            + " ORDER BY s.testcase_run_id, s.step_index",
            params).fetchall()
    finally:
        conn.close()

    steps = [TraceStep(
        testcase_run_id=r["testcase_run_id"], step_index=r["step_index"],
        step_type=r["step_type"] or "", target_id=r["target_id"] or "",
        status=r["status"],
        detail=json.loads(r["detail_json"]) if r["detail_json"] else {},
        observed_at=r["observed_at"]) for r in rows]
    return steps, app_id, build
