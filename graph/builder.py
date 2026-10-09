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

## Source Graph（`source_of='source'`，设计 12.1 的另一半）

```text
Source Graph ← Source Metadata 的导航信息
```

**实测结论（2026-10-05 扫描，范围＝仓库全量的 `source_metadata.json`，含
`out/` 下的工作副本；`out/` 每次 run 都会新增副本，故不写具体文件数）**：当前
metadata 格式**不含任何导航声明**——没有 nav / transition / goto / navigate /
action / tap / push / segue 字段（逐关键词扫过，零命中）。所以：

- **节点 = metadata 的 `screens`**（顶层声明列表）。它与 Repository 的屏 id 同源
  ——扫描器为每个条目写一份 `generated/<build>/screens/<id>.yaml`（真机实测
  10 个屏文件与 `screens` 列表逐项一致）。
- **转移如实为空**（plan 明文「metadata 无导航声明时如实为空，不推测」，E12）。

⚠️ **不用 `screen_elements[].name`**：那是**元素组名**，与屏 id 不同名——真机
metadata 里 `screens` 含 `SpikeSheet`/`SpikeTab`，而 `screen_elements[].name`
含 `SpikeScreenRoot`/`SpikeTabScreen`（后者在 `generated/<build>/elements/` 下、
**不是**屏）。拿它当节点会让源图与运行时图**不同名**：真机 10 个名字里 8 个
相同、2 个错位（`SpikeSheet`/`SpikeTab` 漏掉、`SpikeScreenRoot`/`SpikeTabScreen`
凭空多出），那 2 对屏在 diff 里会被误报成 NOT_OBSERVED + ADDED。
（早先这里写的是「diff 全变 ADDED/NOT_OBSERVED」——夸大了，实际只错 2 个。
决定不变，但理由要准：用一个夸大的后果去支撑一个正确的决定，将来会被当反例。）

⚠️ **给 Task 5.3 的提醒**：源图没有转移 ⇒ 转移级的 `CHANGED`/`REMOVED` 在
「扫描器开始输出导航声明」之前**无数据可判**，diff 实际有数据的是节点级的
`ADDED` / `NOT_OBSERVED`（矩阵 #16 正是后者）。这是 metadata 格式的能力边界，
不是实现缺口。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from graph.models import (
    EVIDENCE_RECOVERY_OBSERVED,
    EVIDENCE_SOURCE_DECLARED,
    EVIDENCE_WAIT_SCREEN,
    RUNTIME,
    SOURCE,
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
    "declared_screens",
    "build_source_graph",
    "read_source_metadata",
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
    """这一步给出的屏观测 `[(screen_id, evidence), ...]`。

    纯函数、无副作用；两类证据的来源见模块 docstring。三条纪律：

    1. 失败步骤（status 不在 `SUCCESS`/`RECOVERED`）的 `wait_for` 不算——
       **等待超时意味着没到达**；
    2. **同一步内同一屏的两类证据合并成一条**（证据取并集）——一次访问不因
       「被两条途径看到」而变成两次（review_p2_task51 P3-2）；
    3. **同一步内出现两个不同屏时以 `wait_screen` 为准**，其余丢弃——人不可能
       同时站在两个屏上，而 `wait_screen` 是**被 wait 引擎校验过**的那一个。
       不这么收口的话会凭空生成一条 `trigger` 为空的伪转移（review_p2_task51
       P3-3）：那条转移既进不了 §12.2 的 CHANGED（无从连接），也不是真实导航。
    """
    merged: dict[str, list[str]] = {}
    if is_screen_wait(step):
        merged.setdefault(step.target_id[len(SCREEN_TARGET_PREFIX):],
                          []).append(EVIDENCE_WAIT_SCREEN)
    for st in _stages(step.detail):
        if (st.get("stage") == "screen" and st.get("outcome") == "FOUND"
                and st.get("screen")):
            merged.setdefault(st["screen"], []).append(
                EVIDENCE_RECOVERY_OBSERVED)
    if not merged:
        return []
    if len(merged) > 1:
        wait = [s for s, evs in merged.items() if EVIDENCE_WAIT_SCREEN in evs]
        if wait:
            merged = {wait[0]: merged[wait[0]]}
        else:       # 只有多类恢复证据（理论上不会出现）→ 取字典序首个，确定性
            first = sorted(merged)[0]
            merged = {first: merged[first]}
    return [(screen, ",".join(sorted(set(evs))))
            for screen, evs in merged.items()]


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
            # `observations` 已保证「一步一屏一条」（observed_screens 合并过），
            # 所以这里每条 = 一次**访问**（`visit_count` 的语义见 models）。
            slot["count"] += 1
            slot["evidence"].update(evidence.split(","))
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
        app_id=app_id, app_build=app_build, source_of=RUNTIME,
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


# --- Source Graph（metadata 侧，纯函数 + 一个读取入口） --------------------


def declared_screens(metadata: dict) -> list[str]:
    """metadata 声明的屏 id（`screens`），**去重后按字典序**。

    `screens` 键**不存在** → `[]`（该 metadata 对屏一无所知，如实为空）；
    存在但不是 list / 元素不是非空字符串 → `ValueError`（格式坏了要炸，
    不要静默产出一张空图——「没声明」与「声明读不出来」是两件事）。
    """
    if "screens" not in metadata:
        return []
    raw = metadata["screens"]
    if not isinstance(raw, list):
        raise ValueError(f"metadata.screens 必须是 list，实得 {type(raw).__name__}")
    out = []
    for item in raw:
        # 空白串不是「有名字的屏」（review_p2_task52 P3-4）：判据与措辞
        # 「非空字符串」严格一致——`"  "` 会成为一个名叫两个空格的节点。
        if not isinstance(item, str) or not item.strip():
            raise ValueError(
                f"metadata.screens 元素必须是非空字符串，实得 {item!r}")
        out.append(item)
    return sorted(set(out))


def _declared_time(metadata: dict) -> str | None:
    """`generated_at` 的类型闸门（review_p2_task52 P3-2）。

    不校验的话 `123` 会被 SQLite 的 TEXT 亲和性**静默落库为 `'123'`**，而
    `['a']`/`{'x':1}` 直到 upsert 时才炸 `ProgrammingError: Error binding
    parameter`——离现场（build）很远。同一个函数里对 `screens` 严、对
    `generated_at` 放任，是两套标准。
    """
    value = metadata.get("generated_at")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"metadata.generated_at 必须是字符串或 None，实得 {value!r}")
    return value


def build_source_graph(metadata: dict, *, app_id: str = "",
                       app_build: str | None = None) -> RuntimeGraph:
    """**纯函数**：Source Metadata → Source Graph（`source_of='source'`）。

    - **节点** = `screens`（声明的屏）；`visit_count` 恒 0——它是**访问**次数，
      而声明面没有「访问」这回事（不为这个字段编一个别的含义）；
      `evidence=('source_declared',)` 是「这是声明不是观测」的留痕；
      `first/last_seen` 取 metadata 的 `generated_at`（声明是什么时候生成的）。
    - **转移** = 空（当前格式无导航声明，见模块 docstring）。

    `app_build` 缺省取 metadata 自己的 `build` 字段——源图的范围应与运行时图
    对齐（diff 按 build 关联，设计 12.3）。`app_id` 无处可读（metadata 不含
    bundle id），必须由调用方给（CLI 的 `--bundle-id`）。
    """
    if app_build is None:
        # **与运行时侧同一个解析入口**（`build_identity.resolve_app_build`）：
        # 源图 scope 必须与运行时图 scope 逐字对齐，否则 diff 找不到同一范围
        # （review_p2_task52 P3-1）。兜底值也不再是本模块自己的 `""`。
        from source.build_identity import resolve_app_build
        app_build = resolve_app_build(metadata)
    generated_at = _declared_time(metadata)
    nodes = tuple(
        ScreenNode(screen_id=s, source_of=SOURCE, visit_count=0,
                   evidence=(EVIDENCE_SOURCE_DECLARED,),
                   first_seen=generated_at, last_seen=generated_at)
        for s in declared_screens(metadata))
    return RuntimeGraph(app_id=app_id, app_build=str(app_build),
                        source_of=SOURCE, nodes=nodes, transitions=())


def read_source_metadata(path: str | Path) -> dict:
    """读 `source_metadata.json` → dict。

    文件不存在 / 不是合法 JSON / 顶层不是对象 → 抛错（带路径）。**不吞**：
    `mta graph build --from-source` 拿到一个坏文件时静默产出空图，会让
    「声明面没有屏」与「文件读坏了」在报告上长得一样。
    """
    from source.build_identity import BuildIdentityError, read_metadata

    p = Path(path)
    if not p.is_file():
        # 文件不存在给 `FileNotFoundError`（CLI 的输入错误），其余解析错误
        # 由**全仓唯一的 metadata 解析点**（`build_identity.read_metadata`）
        # 负责，这里只把异常类型翻成图侧的 `ValueError`——解析逻辑不重复实现。
        raise FileNotFoundError(f"source metadata 不存在: {p}")
    try:
        return read_metadata(p)
    except BuildIdentityError as e:
        raise ValueError(f"source metadata 不可用: {e}") from e


# --- trace 读取（本模块唯一的 I/O 区：只搬数据，不做判定） ------------------


def read_trace_steps(trace_db: str | Path, *,
                     app_build: str | None = None
                     ) -> tuple[list[TraceStep], str, str]:
    """读 trace 的 steps → `(steps, app_id, app_build)`。

    `app_build=None` 时取库里**全部** run，但要求它们的 `(app_id, app_build)`
    一致——两个 build 的屏混进同一张图是错的（diff 的 base/build 语义会失效），
    所以混了直接 fail-loud 而不是挑一个。

    ⚠️ **操作提示**：2026-10-05 之前产生的 trace（`runs.app_build` 从未被写入，
    见 `docs/p2_data_audit.md` 的 Task 5.1 记录）该列是**空串**，与修复后的新
    run（有 build id）混在同一个库里会**必然**触发上面这条 fail-loud。此时要么
    显式传 `app_build=`，要么把新 run 写到另一个 trace 库——这不是 bug，是
    「两个范围不许混进一张图」的判定在起作用。

    时间取所属 testcase_run 的 `end_time`（缺则 `start_time`）；`steps` 表本身
    没有时间戳（P1 schema 如此），不假装更细。
    """
    conn = sqlite3.connect(str(trace_db))
    conn.row_factory = sqlite3.Row
    try:
        where, params = "", []
        if app_build is not None:
            # ⚠️ `COALESCE` 必需：scope 值里的 `''` 是**归一化后**的形式
            # （下面 `(r["build"] or "")` 把 NULL 映射成空串），而库里旧 run
            # 的 `app_build` 是 **NULL**——直接 `= ''` 匹配不到任何行，
            # 于是「用 `--build ""` 建图」会静默得到空图。
            where = " WHERE COALESCE(r.app_build,'')=?"
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
            " s.status, s.detail_json, s.failure_type, t.testcase_id,"
            " COALESCE(t.end_time, t.start_time) AS observed_at"
            " FROM steps s JOIN testcase_runs t"
            " ON t.id = s.testcase_run_id"
            " JOIN runs r ON r.run_id = t.run_id"
            + (" WHERE COALESCE(r.app_build,'')=?" if app_build is not None
               else "")
            + " ORDER BY s.testcase_run_id, s.step_index",
            params).fetchall()
    finally:
        conn.close()

    steps = [TraceStep(
        testcase_run_id=r["testcase_run_id"], step_index=r["step_index"],
        step_type=r["step_type"] or "", target_id=r["target_id"] or "",
        status=r["status"],
        detail=json.loads(r["detail_json"]) if r["detail_json"] else {},
        observed_at=r["observed_at"],
        # Task 2.2（P3-06）：两个本来就在库里、早先没读出来的字段——
        # P3 的 trace_history 要靠它们过滤（见 graph/models.TraceStep 的说明）。
        testcase_id=r["testcase_id"] or "",
        failure_type=r["failure_type"]) for r in rows]
    return steps, app_id, build
