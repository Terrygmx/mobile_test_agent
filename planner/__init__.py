"""planner — Test Planner（P3-A / 设计 §5；Task 2.1 起）。

⚠️ 本 `__init__` 只 re-export **不依赖 `agents`** 的模块
（`models` / `prioritizer` / `risk`），**不要**在这里 re-export `planner.planner`
（Task 2.3 落地）：`agents/**` 刻意**不** import 任何 `planner.*`（见
`planner/models.py` 的「分层」段），而 `planner.planner` 反过来要 import
`agents.storage` —— 一旦本文件把 `planner.planner` 拉进包初始化，就会形成
「`agents.storage` → `planner/__init__` → `planner.planner` → `agents.storage`（半初始化）」
的**包级循环**。保持本文件只碰「叶子」模块——**它们都不 import `agents` 侧任何
模块**（`risk` 内部引用同包的 `prioritizer`，同包内不成环）。
"""
from planner.models import (
    PLAN_SCHEMA_VERSION,
    PlannerInput,
    TestPlan,
    TestPlanTask,
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
    # prioritizer（Task 2.2 / P3-06）
    "TestCaseMeta", "priority_score", "order_key",
    "IMPACT_WEIGHT", "HISTORY_WEIGHT", "RISK_FLOOR",
    # risk（Task 2.2 / P3-06）
    "case_risk",
]
