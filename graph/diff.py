"""diff.py — Graph Diff（设计 12.2 / 12.3；Task 5.3 / P2-13）。

## 五类判定（设计 12.2 原文）

```text
ADDED         Runtime 出现了 Source 未声明的转移（如 Login → LoginError）
REMOVED       需谨慎：仅当 Source 明确标注过、且 Runtime 多次尝试确认不存在时才标；
              默认优先标 NOT_OBSERVED
CHANGED       同一触发条件，目标 Screen 变化
NOT_OBSERVED  Source 有、Runtime 未覆盖到（用例没走到，不代表被删除）
UNKNOWN       无法判定
```

方向约定：`base` = **参照面**（Source 声明 / 旧 build），`new` = **当前面**
（Runtime 观测 / 新 build）。同一个纯函数服务两种用法：

- **Runtime vs Source**（同 build）：`diff_graphs(source, runtime)` —— 五类判定；
- **Build-to-Build**（设计 12.3）：`diff_graphs(old_runtime, new_runtime,
  allow_build_change=True)`。

## 三处口径（都从设计的字面推出来）

1. **REMOVED 默认不可达**。设计明文「仅当 Source 明确标注过、**且** Runtime 多次
   尝试确认不存在」——后者需要**负证据**（「试过、确认不在」），而运行时图只记
   「到达了什么」（Task 5.1 的口径：超时的屏 wait 不算到达）。所以本函数把负证据
   做成显式入参 `absent_confirmations`（键 = 条目身份串），**当前无生产者**：
   REMOVED 在真实数据上不可达，正是设计想要的「默认优先标 NOT_OBSERVED」
   （矩阵 #16：Source 有、用例从未覆盖 → `NOT_OBSERVED`，不是 `REMOVED`）。
   要让它可达，需扩运行时图记录负证据（**独立立项**，不在本任务里顺手做）。
2. **UNKNOWN = 参照面为空、无法判定**。库里「没构建过」与「构建了但为空」不可
   区分（空图不留行），而两者都落进同一个结论：**参照面为空时，当前面独有的条目
   既可能是「新增」也可能是「声明缺失」**——这正是「无法判定」。所以参照面为空
   时不产出 ADDED，而是全部标 UNKNOWN 并写明原因。（这也顺带堵住一个静默误读：
   多数现成 metadata 没有 `screens`，源图本就为空，若照常判 ADDED 会把运行时
   全部算成「未声明」。）
3. **跨范围必须 fail-loud**。`app_id` 不同 → `ValueError`；`app_build` 不同而
   调用方没显式声明这是 build-to-build → `ValueError`。否则两面 scope 不重叠时
   会安静地报满屏 `NOT_OBSERVED`（review_p2_task52 P3-1 要求的接线前置）。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from graph.models import RuntimeGraph, ScreenNode, ScreenTransition

__all__ = [
    "ADDED", "REMOVED", "CHANGED", "NOT_OBSERVED", "UNKNOWN",
    "DEFAULT_REMOVAL_CONFIRMATIONS",
    "DiffEntry", "GraphDiff",
    "diff_graphs", "transition_key",
]

ADDED = "ADDED"
REMOVED = "REMOVED"
CHANGED = "CHANGED"
NOT_OBSERVED = "NOT_OBSERVED"
UNKNOWN = "UNKNOWN"

ALL_KINDS = (ADDED, REMOVED, CHANGED, NOT_OBSERVED, UNKNOWN)

DEFAULT_REMOVAL_CONFIRMATIONS = 3
"""标 REMOVED 所需的「运行时确认不存在」次数下界（设计说「多次」，此处取 3）。

**当前无生产者**（见模块 docstring 口径 1）——这个常量与它的判据一起备好，
等运行时图开始记负证据时生效；数值取 3 是保守判断（「多次」不是「一次」）。
"""


def transition_key(from_screen: str, to_screen: str, trigger: str) -> str:
    """转移条目的**身份串**（`absent_confirmations` 的键）。

    含 `to_screen`：CHANGED 的定义是「同一触发条件、目标变化」，所以
    「(from, trigger) 下的旧目标」才是被替换掉的那个条目。
    """
    return f"{from_screen}->{to_screen}@{trigger}"


@dataclass(frozen=True)
class DiffEntry:
    """一条差异。节点类填 `screen_id`，转移类填 `from/to/trigger`。"""

    kind: str
    screen_id: str | None = None
    from_screen: str | None = None
    to_screen: str | None = None
    trigger: str | None = None
    detail: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in ALL_KINDS:
            raise ValueError(f"未知 diff kind: {self.kind!r}")
        if self.screen_id is None and not (self.from_screen and self.to_screen):
            raise ValueError("DiffEntry 必须给出 screen_id 或 from/to")
        if not isinstance(self.detail, dict):
            # review_p2_task54 P3-1：builder 恒传 dict，但 dataclass 不强制
            # 类型——下游/脚本直构 `detail=None` 的 CHANGED 行会在 render 的
            # detail.get 上 AttributeError（构造入口不设防，消费端裸假设，
            # 与 4.2 review P3-1 的 effective_risk 类型地雷同族）。
            raise TypeError(
                f"detail must be dict, got {type(self.detail).__name__}:"
                f" {self.detail!r}")

    @property
    def is_node(self) -> bool:
        return self.screen_id is not None

    @property
    def key(self) -> str:
        """条目身份串（与 `absent_confirmations` 的键同域）。"""
        if self.is_node:
            return str(self.screen_id)
        return transition_key(self.from_screen or "", self.to_screen or "",
                              self.trigger or "")

    def render(self) -> str:
        """一行文本（CLI 与报告共用，避免两处各写一遍格式）。"""
        if self.is_node:
            return f"{self.kind:13s} screen {self.screen_id}"
        arrow = f"{self.from_screen} -> {self.to_screen}"
        trig = f" (trigger={self.trigger or '-'})"
        extra = ""
        if self.kind == CHANGED and self.detail.get("was_to"):
            extra = f"  [原目标: {self.detail['was_to']}]"
        return f"{self.kind:13s} transition {arrow}{trig}{extra}"


@dataclass(frozen=True)
class GraphDiff:
    """一次比较的结果（纯值；`summary()` 只汇总、不重算判据）。"""

    app_id: str = ""
    base_build: str = ""
    build: str = ""
    base_source_of: str = ""
    source_of: str = ""
    entries: tuple[DiffEntry, ...] = ()
    reason: str | None = None

    def of_kind(self, kind: str) -> tuple[DiffEntry, ...]:
        return tuple(e for e in self.entries if e.kind == kind)

    @property
    def counts(self) -> dict:
        out = {k: 0 for k in ALL_KINDS}
        for e in self.entries:
            out[e.kind] += 1
        return out

    @property
    def changed(self) -> bool:
        """是否有**非 UNKNOWN** 的差异（UNKNOWN 是「没判定」，不是「有变化」）。"""
        return any(e.kind != UNKNOWN for e in self.entries)

    def summary(self) -> dict:
        return {"app_id": self.app_id, "base_build": self.base_build,
                "build": self.build, "base_source_of": self.base_source_of,
                "source_of": self.source_of, "counts": self.counts,
                "reason": self.reason}


def _nodes_of(graph: RuntimeGraph | None) -> dict[str, ScreenNode]:
    return {n.screen_id: n for n in (graph.nodes if graph else ())}


def _trans_of(graph: RuntimeGraph | None
              ) -> dict[tuple[str, str], list[ScreenTransition]]:
    """转移按 `(from_screen, trigger)` 索引 → **目标列表**。

    CHANGED 的连接键正是 `(from_screen, trigger)`，但 schema 的唯一键含
    `to_screen`——**允许**同一 `(from, trigger)` 有多个目标共存（同一个动作在
    不同 run 里去了不同地方，或应用真有分支）。早先这里用单值字典
    `out[key] = t`，同键第二条**静默覆盖**第一条（review_p2_task53 P3-1 实测：
    `(A,tap:go)→B` 与 `(A,tap:go)→C` 共存时 B 彻底消失，**真删一条转移也发现
    不了**）。改成列表后由 `_diff_transitions` 显式处理多目标。
    """
    out: dict[tuple[str, str], list[ScreenTransition]] = {}
    for t in (graph.transitions if graph else ()):
        out.setdefault((t.from_screen, t.trigger), []).append(t)
    for v in out.values():
        v.sort(key=lambda x: x.to_screen)
    return out


def diff_graphs(base: RuntimeGraph | None, new: RuntimeGraph | None, *,
                absent_confirmations: Mapping[str, int] | None = None,
                removal_min_confirmations: int = DEFAULT_REMOVAL_CONFIRMATIONS,
                allow_build_change: bool = False) -> GraphDiff:
    """**纯函数**：`base`（参照面）→ `new`（当前面）的差异。

    `base` / `new` 为 `None` = 那一面**没有图**（未构建 / 未提供）→ 全部 UNKNOWN。
    两侧都存在但 `base` 为空（0 节点）→ 同样 UNKNOWN（见模块 docstring 口径 2）。
    """
    if removal_min_confirmations < 1:
        raise ValueError(
            f"removal_min_confirmations 必须 >= 1，实得 {removal_min_confirmations}")
    absent = dict(absent_confirmations or {})

    diff = GraphDiff(
        app_id=(new or base).app_id if (new or base) else "",
        base_build=base.app_build if base else "",
        build=new.app_build if new else "",
        base_source_of=base.source_of if base else "",
        source_of=new.source_of if new else "")

    # --- 跨范围守卫（口径 3） ---------------------------------------------
    if base is not None and new is not None:
        if base.app_id != new.app_id:
            raise ValueError(
                f"两面 app_id 不同（{base.app_id!r} vs {new.app_id!r}）——"
                f"跨 app 比较没有意义")
        if base.app_build != new.app_build and not allow_build_change:
            raise ValueError(
                f"两面 app_build 不同（{base.app_build!r} vs {new.app_build!r}）"
                f"：跨 build 比较请显式传 allow_build_change=True"
                f"（build-to-build diff，设计 12.3）；否则 scope 不重叠会安静地"
                f"报满屏 NOT_OBSERVED")

    # --- UNKNOWN：任一面缺失/为空 -----------------------------------------
    # **两面同等保守**（review_p2_task53 P2-1）。早先只对参照面做保守处理，
    # 于是「当前面没有数据」会走正常分支、参照面条目全判 NOT_OBSERVED 且
    # `changed=True`（CLI exit 1）——而「空图不留行」让「没构建过」与「构建了
    # 但为空」在库里不可区分，用户拿到的是一个**自信的错误结论**（「用例没走到
    # 这些屏」），真相是「你还没建运行时图」。
    # 代价：真·「观测到 0 个屏」也会变 UNKNOWN——但那个信号在 `graph build`
    # 时已经用 `GRAPH WARN: 运行时图为空` 给过一次了。
    if base is None or new is None:
        side = "参照面" if base is None else "当前面"
        return _all_unknown(diff, base, new,
                            f"{side}没有图（未构建或未提供）")
    if not base.nodes and not base.transitions:
        return _all_unknown(
            diff, base, new,
            "参照面为空（没有声明/没有观测）——当前面独有的条目既可能是"
            "「新增」也可能是「参照缺失」，无法判定")
    if not new.nodes and not new.transitions:
        return _all_unknown(
            diff, base, new,
            "当前面为空（没有观测到任何屏，或该 build 的运行时图还没建）——"
            "参照面独有的条目既可能是「用例没走到」也可能是「没有数据」，"
            "无法判定；先确认 `mta graph build` 是否建过这一面")

    # --- 节点 -------------------------------------------------------------
    base_nodes, new_nodes = _nodes_of(base), _nodes_of(new)
    entries: list[DiffEntry] = []
    for sid in sorted(set(base_nodes) | set(new_nodes)):
        if sid in new_nodes and sid not in base_nodes:
            entries.append(DiffEntry(ADDED, screen_id=sid, detail={
                "visits": new_nodes[sid].visit_count,
                "evidence": list(new_nodes[sid].evidence)}))
        elif sid in base_nodes and sid not in new_nodes:
            conf = absent.get(sid, 0)
            if conf >= removal_min_confirmations:
                entries.append(DiffEntry(REMOVED, screen_id=sid, detail={
                    "absent_confirmations": conf}))
            else:
                entries.append(DiffEntry(NOT_OBSERVED, screen_id=sid, detail={
                    "absent_confirmations": conf}))

    # --- 转移（连接键 = (from_screen, trigger)，见设计 12.2 的 CHANGED） ----
    entries.extend(_diff_transitions(base, new, absent,
                                     removal_min_confirmations))

    return GraphDiff(app_id=diff.app_id, base_build=diff.base_build,
                     build=diff.build, base_source_of=diff.base_source_of,
                     source_of=diff.source_of, entries=tuple(entries))


def _diff_transitions(base: RuntimeGraph, new: RuntimeGraph,
                      absent: dict[str, int],
                      removal_min: int) -> list[DiffEntry]:
    """转移级差异：按 `(from_screen, trigger)` 连接，**按目标集合比较**。

    - 两面都只有**一个**目标且不同 → `CHANGED`（设计 12.2 的原义，带 `was_to`）；
    - 其余情况（某面缺该键、或任一面有**多目标**）→ 逐目标判 `ADDED` /
      `NOT_OBSERVED` / `REMOVED`。

    为什么多目标要降级成逐目标判定：CHANGED 的模型是「一个触发条件一个目标」，
    而 schema 允许同键多目标共存。多目标时**不编一个 CHANGED**、也**不丢任何
    一条**——逐目标报出来，读者自己看「这个动作去了两处」（那本身就是有价值
    的信号：应用不稳定或真有分支）。
    """
    out: list[DiffEntry] = []
    base_trans, new_trans = _trans_of(base), _trans_of(new)
    for key in sorted(set(base_trans) | set(new_trans)):
        bs = base_trans.get(key, [])
        ns = new_trans.get(key, [])
        base_tos = {t.to_screen for t in bs}
        new_tos = {t.to_screen for t in ns}

        if len(bs) == 1 and len(ns) == 1 and bs[0].to_screen != ns[0].to_screen:
            out.append(DiffEntry(
                CHANGED, from_screen=ns[0].from_screen,
                to_screen=ns[0].to_screen, trigger=ns[0].trigger,
                detail={"was_to": bs[0].to_screen, "count": ns[0].count}))
            continue

        multi = len(bs) > 1 or len(ns) > 1
        for t in ns:
            if t.to_screen in base_tos:
                continue
            out.append(DiffEntry(
                ADDED, from_screen=t.from_screen, to_screen=t.to_screen,
                trigger=t.trigger,
                detail={"count": t.count, **({"multi_target": True} if multi
                                             else {})}))
        for t in bs:
            if t.to_screen in new_tos:
                continue
            conf = absent.get(transition_key(t.from_screen, t.to_screen,
                                             t.trigger), 0)
            kind = REMOVED if conf >= removal_min else NOT_OBSERVED
            out.append(DiffEntry(
                kind, from_screen=t.from_screen, to_screen=t.to_screen,
                trigger=t.trigger,
                detail={"absent_confirmations": conf,
                        **({"multi_target": True} if multi else {})}))
    return out


def _all_unknown(diff: GraphDiff, base: RuntimeGraph | None,
                 new: RuntimeGraph | None, reason: str) -> GraphDiff:
    """两面能列出的条目全部标 UNKNOWN（带原因）——不是「没差异」。"""
    entries: list[DiffEntry] = []
    for graph in (base, new):
        for n in (graph.nodes if graph else ()):
            entries.append(DiffEntry(UNKNOWN, screen_id=n.screen_id,
                                     detail={"reason": reason}))
        for t in (graph.transitions if graph else ()):
            entries.append(DiffEntry(UNKNOWN, from_screen=t.from_screen,
                                     to_screen=t.to_screen, trigger=t.trigger,
                                     detail={"reason": reason}))
    # 两面都列出时去重（同一条目只报一次）
    seen, uniq = set(), []
    for e in entries:
        if e.key in seen:
            continue
        seen.add(e.key)
        uniq.append(e)
    return GraphDiff(app_id=diff.app_id, base_build=diff.base_build,
                     build=diff.build, base_source_of=diff.base_source_of,
                     source_of=diff.source_of, entries=tuple(uniq),
                     reason=reason)
