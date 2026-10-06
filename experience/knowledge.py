"""knowledge.py — KnowledgeSources 四查询接口（设计 19；Task 5.5）。

设计原文：「P2 不实现规划能力，但为 P3 准备四类可查询的知识」——P3 的
自动探索 / 测试规划 / Subagent 建立在这四个查询之上，**不需要重新设计
数据层**。本模块因此是**纯组合**：每个方法都是既有模块的薄委派，无新
存储、无新查询口径（review_p2_task54 建议动作 2：直接以 impact 的两个
原语 collect_case_refs / ref_to_cases 为底，避免第四种查询口径）。

| 查询 | 底座（唯一真值源） |
|---|---|
| `experience_lookup` | `ExperienceStore.lookup`（消费视角：REJECTED 排除，7.1 修订）+ `rank_experiences`（5.1 消费排序）——与引擎 `try_experiences` 同一条口径 |
| `graph_query` | `graph.builder.read_trace_steps` + `build_runtime_graph`（trace 是运行时图的唯一来源，12.1） |
| `impact_of` | `source.coverage.collect_case_refs` / `ref_to_cases` 原语 + `graph.impact.affected_testcases`（11.3 反向索引） |
| `trace_history` | `graph.builder.read_trace_steps`（trace 的 steps 即历史；不复制第二套读取逻辑） |

只读纪律：本接口**没有任何写方法**——P3 的规划能力消费知识，不生产
知识（写路径仍归 Runner / review accept / promoter 各自的闸门）。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from experience.models import Experience
from experience.ranker import rank_experiences
from graph.builder import build_runtime_graph, read_trace_steps
from graph.impact import affected_testcases, ref_index

__all__ = ["KnowledgeSources", "P2KnowledgeSources"]


@runtime_checkable
class KnowledgeSources(Protocol):
    """设计 19 的四查询形状（P3 的知识底座）。"""

    def experience_lookup(self, app_id: str, screen_id: str,
                          target_id: str) -> list[Experience]: ...

    def graph_query(self, app_id: str) -> object: ...

    def impact_of(self, target_id: str) -> tuple[str, ...]: ...

    def trace_history(self, filters: dict) -> list: ...


class P2KnowledgeSources:
    """KnowledgeSources 的 P2 实现：组合现有 store / graph / impact / trace。

    `cases`：已解析的用例对象（`pipeline.discover()` 或
    `parse_testcase_dict` 的产物）——`impact_of` 的反向索引数据源。
    `app_build`：`graph_query` / `trace_history` 的 trace 范围（两个 build
    不许混进一张图/一段历史，read_trace_steps 的判定在起作用）。
    """

    def __init__(self, *, experience_store, trace_db, cases=(),
                 app_build: str | None = None) -> None:
        self._store = experience_store
        self._trace_db = trace_db
        self._cases = list(cases)
        self._app_build = app_build

    def experience_lookup(self, app_id: str, screen_id: str,
                          target_id: str) -> list[Experience]:
        return rank_experiences(
            self._store.lookup(app_id, screen_id, target_id))

    def graph_query(self, app_id: str) -> object:
        steps, trace_app_id, _build = read_trace_steps(
            self._trace_db, app_build=self._app_build)
        if trace_app_id != app_id:
            # 设计 19 的签名按 app_id 查——trace 里是另一个 app 时不猜
            # 「也许你要的就是这份」，fail-loud（与 read_trace_steps 的
            # 「两个范围不许混」同一条纪律）。
            raise ValueError(
                f"trace 的 app_id 是 {trace_app_id!r}，与查询的 "
                f"{app_id!r} 不一致——graph_query 不跨 app 混图")
        return build_runtime_graph(steps, app_id=app_id)

    def impact_of(self, target_id: str) -> tuple[str, ...]:
        return affected_testcases(target_id, ref_index(self._cases))

    def trace_history(self, filters: dict) -> list:
        unknown = set(filters) - {"app_build", "limit"}
        if unknown:
            # 过滤键是接口契约的一部分：静默忽略拼错的键会把「想过滤没
            # 过滤」伪装成「结果恰好都对」。
            raise ValueError(f"未知过滤键: {sorted(unknown)}（支持: "
                             f"app_build, limit）")
        app_build = filters.get("app_build", self._app_build)
        steps, _app_id, _build = read_trace_steps(self._trace_db,
                                                  app_build=app_build)
        limit = filters.get("limit")
        if limit is not None:
            if not isinstance(limit, int) or limit < 0:
                raise ValueError(f"limit 必须是非负 int，got {limit!r}")
            steps = steps[-limit:] if limit else []
        return steps
