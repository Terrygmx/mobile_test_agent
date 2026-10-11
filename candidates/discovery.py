"""discovery.py — Discovery Event 的行模型与图登记（设计 §7.5 / §11；Task 4.5 / P3-18）。

设计 §7.5 原文：

```text
Discovery Event（新 Screen/Transition/Element）
      ↓
Candidate（与 P2 Graph 的 ADDED 分类一致，不是新概念）
      ↓
人工审核（F6）
      ↓
纳入 Source/Runtime Graph 的"已知"集合
```

## 「与 ADDED 一致，不是新概念」怎么落

发现事件**不新建一套分类**：`graph/diff.py` 早就有 `ADDED`（运行时到了、声明
没有）与 `NOT_OBSERVED`（声明有、运行时没到）。探索的发现是**第三个来源**
——运行时到了、声明没有、**而且是被主动探索撞见的**——按设计的话它就是
`ADDED` 的另一种产生方式，所以人工确认后**登记进 Source Graph**（声明面）
而不是 Runtime Graph：`source` 面是「人确认过的已知资产」，`runtime` 面是
「trace 实际到达过」的观测记录。把发现写进 runtime 面会让「观测」与「确认」
混成一体，设计 12.2 的 diff（runtime vs source）就失去了意义（E12：图只来自
已有 Testcase / Trace / Source Metadata，**不做自动探索**——探索的产物必须
**经人工确认**才进图，这正是 F6 那道闸门的存在理由）。

## 与 `candidates/test_case.py` / `bug.py` 的分工

本模块只有**行模型 + dict 互转 + 图登记的纯函数**，SQL 不在本包——agent.db 的
单写者是 `agents/storage.py`（E9）。`register` 是**纯函数**（F13）：图库由调用方
注入，函数本身不碰文件、不碰设备，可脱离设备单测。

## 写路径

`register` **不自己 upsert 整张图**，而是「读出该范围的 Source 面 → 合并新条目
→ 写回」。理由：`GraphStore.upsert_graph` 是**按范围整体替换**的（P2 的设计，
重跑 `graph build` 必须幂等），所以登记一次发现必须先把既有声明面读出来，
否则会把别处已确认的发现**擦掉**。这条在读-改-写的窗口上不是原子的——但
登记是**人工审核后的低频操作**（一次一个 ACCEPT），且 `GraphStore` 的写走
`BEGIN IMMEDIATE` 单写者；并发登记同一范围是运维层面的事，不在本任务的设计
范围内（登记幂等：同一条已登记 → 返回 False 且不写库）。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from graph.models import (EVIDENCE_DISCOVERY, SOURCE, RuntimeGraph, ScreenNode,
                          ScreenTransition)

__all__ = ["DISCOVERY_KINDS", "DISCOVERY_REVIEW_STATUSES", "DiscoveryEvent",
           "graph_registration", "register"]

# 设计 §11 的 kind 词表（迁移 006 的 CHECK 是**两处字面量**，一致性有测试钉）。
DISCOVERY_KINDS: tuple[str, ...] = ("NEW_SCREEN", "NEW_TRANSITION",
                                    "NEW_ELEMENT", "ANOMALY")

# F6 的审核三态（与 `test_candidates` 同一套；迁移 006 的 CHECK 同表）。
DISCOVERY_REVIEW_STATUSES: tuple[str, ...] = ("PENDING", "ACCEPT", "REJECT")

# 两类「图资产」的 kind —— 人工 ACCEPT 后登记进 Source Graph。
_GRAPH_NODE_KINDS = frozenset({"NEW_SCREEN"})
_GRAPH_TRANSITION_KINDS = frozenset({"NEW_TRANSITION"})


class _Strict(BaseModel):
    """多余字段拒绝（与 `candidates/test_case.py` / `bug.py` 同款）。"""

    model_config = ConfigDict(extra="forbid")


class DiscoveryEvent(_Strict):
    """设计 §11 `discovery_events` 行。

    `created_at` 由 store 在写入时取（与 `TestCandidate` / `BugCandidate` 同款
    ——调用方不塞时间戳）。
    """

    event_id: str
    kind: str
    detail: dict[str, Any] = Field(default_factory=dict)
    exploration_task_id: str | None = None
    review_status: str = "PENDING"
    reviewer: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None
    created_at: str = ""

    def to_store_dict(self) -> dict[str, Any]:
        return {"event_id": self.event_id, "kind": self.kind,
                "detail": dict(self.detail),
                "exploration_task_id": self.exploration_task_id,
                "review_status": self.review_status,
                "reviewer": self.reviewer, "reviewed_at": self.reviewed_at,
                "review_note": self.review_note, "created_at": self.created_at}

    @classmethod
    def from_store_dict(cls, row: dict[str, Any]) -> "DiscoveryEvent":
        return cls(event_id=row["event_id"], kind=row["kind"],
                   detail=dict(row.get("detail") or {}),
                   exploration_task_id=row.get("exploration_task_id"),
                   review_status=row.get("review_status") or "PENDING",
                   reviewer=row.get("reviewer"),
                   reviewed_at=row.get("reviewed_at"),
                   review_note=row.get("review_note"),
                   created_at=row.get("created_at") or "")


def graph_registration(event: dict) -> tuple[str, dict] | None:
    """`(资产类型, 标识字段)` 或 `None`（不是图资产的 kind）。**纯函数**（F13）。

    | kind | 产物 |
    |---|---|
    | `NEW_SCREEN` | `("node", {"screen_id": …})` |
    | `NEW_TRANSITION` | `("transition", {from_screen, to_screen, trigger})` |
    | `NEW_ELEMENT` | `None`（图没有元素层——设计 12 的两面都只到屏与转移） |
    | `ANOMALY` | `None`（不是资产，是「解释不了」的信号，只留痕） |

    缺识别字段 → `ValueError`（上游发事件时就该带上，静默登记一个无名节点
    是本仓最防的「看起来正常」形态）。
    """
    kind = event.get("kind")
    detail = event.get("detail") or {}
    if kind in _GRAPH_NODE_KINDS:
        screen_id = (detail.get("screen_id") or "").strip()
        if not screen_id:
            raise ValueError(
                f"NEW_SCREEN 事件缺 detail.screen_id——登记不了无名节点"
                f"（event={event.get('event_id')!r}）")
        return "node", {"screen_id": screen_id}
    if kind in _GRAPH_TRANSITION_KINDS:
        frm = (detail.get("from_screen") or "").strip()
        to = (detail.get("to_screen") or "").strip()
        if not frm:
            raise ValueError(
                f"NEW_TRANSITION 事件缺 detail.from_screen（event="
                f"{event.get('event_id')!r}）")
        if not to:
            raise ValueError(
                f"NEW_TRANSITION 事件缺 detail.to_screen（event="
                f"{event.get('event_id')!r}）")
        return "transition", {"from_screen": frm, "to_screen": to,
                              "trigger": (detail.get("trigger") or "")}
    return None


def register(graph_store, event: dict, *, app_id: str = "",
             app_build: str = "") -> bool | None:
    """把一条**已人工确认**的发现登记进 Source Graph。**幂等**。

    返回：
    - `True`   —— 本次真的新增了一个节点/转移；
    - `False`  —— 已登记过（重复 ACCEPT / 重放），库未改动；
    - `None`   —— 该 kind 不是图资产（`NEW_ELEMENT` / `ANOMALY`），不进图。

    实现是「读出现有 Source 面 → 合并 → 写回」（见模块 docstring：不能直接
    `upsert_graph` 一张只有新条目的图，那会**按范围整体替换**掉别处已确认的
    发现）。写入的条目 `visit_count=0` / `count=0` 且证据标记
    `discovery`——**发现不是访问**：没人（也没用例）到过这个屏，只是它被
    确认存在。
    """
    mapped = graph_registration(event)
    if mapped is None:
        return None
    asset, ids = mapped
    current = graph_store.load_graph(app_id, app_build, SOURCE)
    if asset == "node":
        if current.node(ids["screen_id"]) is not None:
            return False
        merged = RuntimeGraph(
            app_id=app_id, app_build=app_build, source_of=SOURCE,
            nodes=current.nodes + (ScreenNode(
                ids["screen_id"], source_of=SOURCE, visit_count=0,
                evidence=(EVIDENCE_DISCOVERY,)),),
            transitions=current.transitions)
    else:
        existing = {(t.from_screen, t.to_screen, t.trigger)
                    for t in current.transitions}
        key = (ids["from_screen"], ids["to_screen"], ids["trigger"])
        if key in existing:
            return False
        merged = RuntimeGraph(
            app_id=app_id, app_build=app_build, source_of=SOURCE,
            nodes=current.nodes,
            transitions=current.transitions + (ScreenTransition(
                ids["from_screen"], ids["to_screen"], ids["trigger"],
                source_of=SOURCE, count=0),))
    graph_store.upsert_graph(merged)
    return True
