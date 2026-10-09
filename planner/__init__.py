"""planner — Test Planner（P3-A / 设计 §5；Task 2.1 起）。

⚠️ 本 `__init__` 只 re-export **不依赖 `agents`** 的模块
（`models` / `prioritizer` / `risk`），**不要**在这里 re-export `planner.planner`。

理由（Task 2.3 落地时核实过，与 Task 2.1 的初版措辞不同）：编排层
`planner/planner.py` **今天并不 import `agents`**（落库是 CLI 的职责，编排层只产出
`TestPlan`），所以「成环」这条路径**现在不成立**。禁令保留是因为另外两条：

1. **`import planner` 的代价**：`planner.planner` 会拉起 `planner.impact` →
   `source.build_diff`、`source.coverage`、`repository.resolver`——把它们绑进包初始化
   会让「只想用 `TestCaseMeta`」的调用方也付这笔钱；
2. **成环会立刻回来**：M6 的自主闭环很可能让编排层直接落 `agent.db`。到那时
   `agents.storage → planner/__init__ → planner.planner → agents.storage` 就是真的环，
   而它只会在**某个** import 顺序下炸——正是最难查的那类。

需要编排层的调用方写 `from planner.planner import plan`（显式、代价可见）。
`agents/**` 对 `planner` 的依赖为**零**是另一条守卫（`planner/models.py` 的「分层」段）。
"""
from planner.models import (
    PLAN_SCHEMA_VERSION,
    PlannerInput,
    TestPlan,
    TestPlanTask,
    plan_id_for,
)
from planner.prioritizer import (
    HISTORY_WEIGHT,
    IMPACT_WEIGHT,
    RISK_FLOOR,
    TestCaseMeta,
    order_key,
    priority_score,
)
from planner.risk import case_risk

__all__ = [
    "PLAN_SCHEMA_VERSION",
    "PlannerInput",
    "TestPlan",
    "TestPlanTask",
    # models（Task 2.3 / P3-07）：plan_id 的唯一生成点
    "plan_id_for",
    # prioritizer（Task 2.2 / P3-06）
    "TestCaseMeta", "priority_score", "order_key",
    "IMPACT_WEIGHT", "HISTORY_WEIGHT", "RISK_FLOOR",
    # risk（Task 2.2 / P3-06）
    "case_risk",
]
