"""retrieval.py — `KnowledgeSources` 的装配入口（F3；Task 2.2 / P3-06）。

**只装配，不新增检索方法**（plan Task 2.2 的明文要求）：本模块把「用哪些 store /
哪个 trace 库 / 哪些用例 / 哪个 app_build」这件事收成一处，检索口径全部留在
`experience/knowledge.py::P2KnowledgeSources`（它是四查询的唯一实现，见那里的
「底座」表）。

## 为什么值得有一个（而不是让调用方直接 `P2KnowledgeSources(...)`）

1. **单一入口**：P3 的 planner / explorer / diagnosis 都 `from knowledge import
   build_knowledge`，不各自伸进 `experience.knowledge`——F3 的「不新建平行的检索
   接口」需要一个**看得见的**入口，否则「复用同一个 Protocol」只是口头约定。
2. **`app_build` 的作用域语义在这里显式化**：它是 `graph_query` / `trace_history`
   的 trace 范围（两个 build 不许混进一张图/一段历史）——装配处是唯一能一眼看到
   「这次规划针对哪个 build」的地方。

## ⚠️ 对 plan Files 的一处偏离：**没有 `graph store` 参数**

plan 写的是「组合 experience_store/trace_db/**graph store**/cases/app_build」，
但 P2 的实现**不接受** graph store：`P2KnowledgeSources.graph_query` 用
`read_trace_steps` + `build_runtime_graph` **从 trace 现建**运行时图——那是 12.1
的定档（「trace 是运行时图的唯一来源」）。若这里再传一个 `graph.db` 句柄，就会
出现**第二个图来源**，正是 12.1 要避免的。故**不设该参数**（也不设一个收了不用的
死参数），已在 `docs/p3_data_audit.md` 登记。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from experience.knowledge import KnowledgeSources, P2KnowledgeSources

__all__ = ["build_knowledge"]


def build_knowledge(*, experience_store, trace_db: str | Path,
                    cases: Any = (), app_build: str | None = None
                    ) -> KnowledgeSources:
    """把既有数据源组合成 `KnowledgeSources`（**无副作用、不读库**）。

    | 参数 | 用途 |
    |---|---|
    | `experience_store` | `experience_lookup` 的底座（`ExperienceStore` 协议） |
    | `trace_db` | `graph_query` / `trace_history` 的 trace 库路径 |
    | `cases` | 已解析的用例对象（`impact_of` 的反向索引数据源） |
    | `app_build` | trace 范围（两个 build 不许混进一张图/一段历史） |

    **构造期不碰磁盘**：`P2KnowledgeSources` 把 `trace_db` 存起来、查询时才读
    （`_read_steps` 还会把裸 sqlite 异常转成可诊断的 `ValueError`）。所以「库还没
    初始化」不会在装配处炸，而是在第一次查询时给出可读的错——装配与查询分开，
    便于测试注入替身。
    """
    return P2KnowledgeSources(experience_store=experience_store,
                              trace_db=trace_db, cases=cases,
                              app_build=app_build)
