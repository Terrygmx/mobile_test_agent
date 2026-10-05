"""graph — UI State Graph（设计 12；主线 B，M5 / P2-11~14）。

```text
Runtime Graph  ← P1 Trace 中已执行用例实际到达的 Screen 转移
Source Graph   ← Source Metadata 的导航信息
Diff           ← 两者对比（ADDED / REMOVED / CHANGED / NOT_OBSERVED / UNKNOWN）
```

**独立于 Experience 主线**（设计 §14）：独立库文件（默认 `out/graph.db`）+
独立迁移链（`graph/migrations/`）。唯一的外部依赖是
`experience.schema_migrations` 的**通用迁移执行器**（Task 1.2 已按「只认目录 +
版本链」泛化）——那是对工具函数的复用，不是主线耦合：graph 不读 experience 的
任何模型或存储。

**E12 贯穿全包**：图只来自已有 Testcase + 已有 Trace + Source Metadata，
不做自动探索；未观察到的 Source 转移标 `NOT_OBSERVED`，不得标 `REMOVED`。
"""
from pathlib import Path

from graph.builder import (
    build_runtime_graph,
    is_screen_wait,
    observed_screens,
    read_trace_steps,
)
from graph.models import (
    RUNTIME,
    SOURCE,
    RuntimeGraph,
    ScreenNode,
    ScreenTransition,
    TraceStep,
)
from graph.storage import (
    GRAPH_SCHEMA_VERSION,
    GraphStore,
    build_and_store,
)

# 与 experience 侧 DEFAULT_EXPERIENCE_DB 同款单点定义：CLI 的 --graph-db
# 默认值直接 import，两处字面量必漂移。
DEFAULT_GRAPH_DB = Path("out/graph.db")

__all__ = [
    "DEFAULT_GRAPH_DB", "GRAPH_SCHEMA_VERSION",
    "RUNTIME", "SOURCE",
    "RuntimeGraph", "ScreenNode", "ScreenTransition", "TraceStep",
    "GraphStore", "build_and_store",
    "build_runtime_graph", "read_trace_steps", "observed_screens",
    "is_screen_wait",
]
