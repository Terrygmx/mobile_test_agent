"""context.py — `DiagnosisContext` 装配（设计 §8.1；Task 5.1 / P3-20）。

设计 §8.1 原文：

```text
Test Trace（P1） + Screenshot/UI Tree（P1） + App/Crash/Network Logs
+ Source Diff（P2 Graph Diff） + Experience（P2） + Historical Failures
（KnowledgeSources.trace_history）
```

**全部来自已有系统，不新建数据源**——所以本模块是**纯装配**：把调用方给的
`knowledge`（`KnowledgeSources` 实例）与既有对象收成一个值对象，供规则引擎
（Task 5.2）消费。

## F3：为什么本模块不 import 任何数据源

`diagnosis/**` **不得**直连 `sqlite3` / `tracer` / `graph.storage` /
`experience.store` / `source.sqlite_tx`（守卫
`tests/unit/test_knowledge_retrieval.py::test_diagnosis_package_does_not_touch_data_sources_directly`）。
所有检索都经注入的 `KnowledgeSources`：

| 上下文里的字段 | 来源（唯一通路） |
|---|---|
| `steps`（失败现场的完整步骤序列） | `knowledge.trace_history({"run_id": …})`（Task 5.1 新增的过滤键） |
| `history`（同类失败的历史） | `knowledge.trace_history({"failure_type": …})` |
| `runtime_graph` | `knowledge.graph_query(app_id)` |
| `experiences` | `knowledge.experience_lookup(app_id, screen_id, target_id)` |
| `graph_diff` | `graph.diff.diff_graphs`（**纯函数**，不是数据源——12.2 的判定本体） |

⚠️ **`graph_diff` 由调用方算好传进来**，不是本模块去读图：`diff_graphs` 需要
「参照面」（Source 图或旧 build 图）而参照面的取法取决于 `--base-build` 的
语义（设计 §12.3），那是 CLI 的输入装配职责（Task 5.6）。本模块只声明
「上下文里有这份 diff」——保持纯装配、零 I/O。

## 为什么 `steps` 与 `history` 是两个字段

它们是**两个问题**：`steps` 是「这一次失败现场长什么样」（同 run 的全部步骤，
含成功的前置步骤——规则引擎要靠它判「失败发生在第几步、之前到过哪些屏」）；
`history` 是「这类失败以前出现过吗」（跨 run 的同类失败——矩阵 #16 的
`same_issue_multiple_tests` 证据就从这里来）。合成一个字段会让规则引擎无法
区分「本 run 里重复」与「历史上重复」。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["DiagnosisContext", "DiagnosisContextError"]


class DiagnosisContextError(ValueError):
    """上下文装配的前置不满足（run 不存在 / 该 run 没有失败步骤）。

    **fail-loud**：拿不到失败现场就没有诊断的输入。装作什么都没发生、
    返回一个空上下文，只会让下游产出一条「证据为空」的假设——而那在
    `Hypothesis` 那一层就已经被拒了（F10 第一道防线）。错误要在**离现场
    最近**的地方报出来。
    """


@dataclass(frozen=True)
class DiagnosisContext:
    """一次失败诊断的输入（设计 §8.1 的六类数据）。

    全部字段都是**已取到的数据**（本类是值对象，构造即定值）——取数发生在
    `build_context`，两者分开是为了让规则引擎可以脱离库单测（F13）。
    """

    run_id: str
    app_id: str
    app_build: str
    steps: tuple = ()                     # 失败现场的完整步骤序列（TraceStep）
    history: tuple = ()                   # 同类失败的历史步骤（TraceStep）
    runtime_graph: Any = None             # RuntimeGraph | None
    graph_diff: Any = None                # GraphDiff | None（--base-build 才有）
    experiences: tuple = ()               # Experience 列表
    logs: tuple = ()                      # 日志行（str；来源见下）
    infra_events: tuple = ()              # infra_events 行（dict）
    network_summary: dict = field(default_factory=dict)
    crash_info: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    @property
    def failure_steps(self) -> tuple:
        """`steps` 里**失败的那些**（status=FAILED）——规则引擎的常见入口。"""
        return tuple(s for s in self.steps
                     if getattr(s, "status", None) == "FAILED")

    @property
    def first_failure(self):
        """第一条失败步骤（按 step_index）；没有失败步骤 → None。"""
        failed = sorted(self.failure_steps,
                        key=lambda s: getattr(s, "step_index", 0))
        return failed[0] if failed else None


def build_context(*, knowledge, run_id: str, app_id: str = "",
                  app_build: str = "", screen_id: str = "",
                  target_id: str = "", failure_type: str | None = None,
                  base_graph=None, graph_diff=None,
                  logs: tuple = (), infra_events: tuple = (),
                  network_summary: dict | None = None,
                  crash_info: dict | None = None,
                  history_limit: int = 20,
                  extra: dict | None = None) -> DiagnosisContext:
    """装配一次诊断的上下文（**全部经 `knowledge`**，F3）。

    | 参数 | 用途 |
    |---|---|
    | `knowledge` | `KnowledgeSources` 实例（`knowledge.build_knowledge` 的产物）——**唯一**取数通路 |
    | `run_id` | 失败现场的范围（`trace_history({"run_id": …})`） |
    | `failure_type` | 同类历史失败的过滤键（不传 → 从失败现场的第一条失败步骤推） |
    | `base_graph` | `--base-build` 的参照图（Task 5.6 装配）；给了才算 diff |
    | `graph_diff` | 调用方算好的 diff（与 `base_graph` 二选一；都给了以本参数为准） |

    `logs` / `infra_events` / `network_summary` / `crash_info` 是**调用方取好的**
    原始材料（P1 的 `infra_events` 行由 CLI 从 trace 库取；网络/崩溃摘要是
    `AgentToolkit` 的只读工具产物）——本模块不自己去读，理由与 `graph_diff`
    同款：读它们的入口分属不同子系统，装配层不该知道那些细节。

    **前置校验**（fail-loud）：`run_id` 必填；该 run 取不到任何步骤 →
    `DiagnosisContextError`（拿不到失败现场就不是一次可诊断的失败）。
    """
    if not run_id:
        raise DiagnosisContextError(
            "run_id 必填——诊断的范围就是「哪一次 run 的失败」")

    steps = tuple(knowledge.trace_history({"run_id": run_id}))
    if not steps:
        raise DiagnosisContextError(
            f"run {run_id!r} 没有任何步骤记录——拿不到失败现场，无法诊断"
            f"（检查 --db 指向的 trace 库与 run_id 是否正确）")

    if failure_type is None:
        first_fail = next((s for s in sorted(
            (s for s in steps if getattr(s, "status", None) == "FAILED"),
            key=lambda s: getattr(s, "step_index", 0))), None)
        failure_type = getattr(first_fail, "failure_type", None)

    history: tuple = ()
    if failure_type:
        history = tuple(knowledge.trace_history(
            {"failure_type": failure_type, "limit": history_limit}))
        # 历史里剔掉本次 run 的步骤——「历史上出现过吗」问的是**别的时候**
        # （把本 run 算进去会让矩阵 #16 的 same_issue_multiple_tests 在
        # 「只失败过一次」时就误判为「多次」）
        own_ids = {getattr(s, "testcase_run_id", None) for s in steps}
        history = tuple(s for s in history
                        if getattr(s, "testcase_run_id", None) not in own_ids)

    runtime_graph = None
    if app_id:
        runtime_graph = knowledge.graph_query(app_id)

    experiences: tuple = ()
    if app_id and screen_id and target_id:
        experiences = tuple(
            knowledge.experience_lookup(app_id, screen_id, target_id))

    if graph_diff is None and base_graph is not None and runtime_graph is not None:
        from graph.diff import diff_graphs
        graph_diff = diff_graphs(base_graph, runtime_graph)

    return DiagnosisContext(
        run_id=run_id, app_id=app_id, app_build=app_build, steps=steps,
        history=history, runtime_graph=runtime_graph, graph_diff=graph_diff,
        experiences=experiences, logs=tuple(logs),
        infra_events=tuple(infra_events),
        network_summary=dict(network_summary or {}),
        crash_info=dict(crash_info or {}),
        extra=dict(extra or {}))
