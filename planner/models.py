"""planner/models.py — Test Planner 的数据模型（设计 §5.1；Task 2.1 / P3-05）。

设计 §5.1 逐字：

```python
class PlannerInput(BaseModel):
    app_build: str
    git_commit: str
    changed_files: list[str]            # git diff 结果

class TestPlanTask(BaseModel):
    testcase_id: str
    priority: int                        # 0-100，确定性评分
    reasons: list[str]                   # 可解释，不是"LLM 觉得"
    source: Literal["existing", "generated_gap"]

class TestPlan(BaseModel):
    schema_version: str = "0.1"
    plan_id: str
    app_build: str; git_commit: str
    tasks: list[TestPlanTask]
```

## `schema_version` 是 **TestPlan 自己的版本**

`"0.1"` 与 TestCase 的 `"0.2"`（`testcase/schema.py::SCHEMA_VERSION`）**无关**——
两者是不同资产的版本线。别把 `testcase.schema` 的版本常量搬过来当默认值
（`test_planner_models.py::test_plan_version_is_independent_of_testcase_schema`
把这件事钉住）。

## 两条「不校验」的刻意选择

1. **`changed_files` 允许为空**：那是合法输入（设计矩阵 #6：改动为空 → 空 Plan 且
   **明示**）。把「空」当错误会让「这次真没改什么」与「git diff 失败了」混为一谈——
   后者在 `source/git_diff.py` 已经是 fail-loud 的 `GitDiffError`，这里不该再兜一层。
2. **`tasks` 允许为空**：同上（空 Plan 是合法产物）。

## 两条「校验」的由来

- **`reasons` 非空**（plan Task 2.1 Steps）：可解释性是 F13 的落地——「**不是裸分数**」。
  一个只有 `priority` 没有理由的排序结果，人无法复核，也无法与 LLM 的解释对账。
- **`priority ∈ [0, 100]`**：设计 §5.1 的行内注释就是「0-100，确定性评分」。超出范围
  说明算分逻辑有 bug（而不是「分数就是高」），fail-loud 比让 300 分排在最前好。

## 分层：`planner` 依赖 `agents.storage`，反之不成立

`TestPlan` 落 `agent.db` 的 `test_plans` 表，但**存取转换由本模块提供**
（`to_store_dict` / `from_store_dict`），`agents/storage.py` 的 `save_plan` /
`get_plan` 收发的仍是**裸 dict**：

- 那张表的 `tasks_json` 是**不透明 JSON blob**（不像 `agent_tasks` 有逐字段列），
  store 没有理由知道 `TestPlanTask` 的结构；
- 更要紧的是**避免包级循环**：`agents.storage` 若 import `planner.models`，而
  `planner/__init__.py` 又 re-export `planner.planner`（Task 2.3 起要 import
  `agents.storage`），就会在包初始化期形成环。把转换留在本模块，
  **`agents/**` 对 `planner` 的依赖为「零」**——这是结构上的保证，不是「我们小心
  一点」。`test_planner_models.py::test_agents_package_does_not_import_planner`
  逐文件扫整个 `agents/` 包钉住它（依赖方向单向：`planner → agents`）。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "PLAN_SCHEMA_VERSION",
    "PRIORITY_MAX",
    "PRIORITY_MIN",
    "PlanSource",
    "PlannerInput",
    "TestPlan",
    "TestPlanTask",
]

# TestPlan **自己的**版本（设计 §5.1 的 `schema_version: str = "0.1"`）。
# 与 `testcase/schema.py::SCHEMA_VERSION`（"0.2"）是两条独立的版本线。
PLAN_SCHEMA_VERSION = "0.1"

# 设计 §5.1 的行内注释：「priority: int  # 0-100，确定性评分」
PRIORITY_MIN, PRIORITY_MAX = 0, 100

# 设计 §5.1 的 `Literal["existing", "generated_gap"]`——用类型别名而不是 Enum：
# 与设计逐字一致，且 `model_dump(mode="json")` 直接给字符串（落 tasks_json 要的就是它）。
PlanSource = Literal["existing", "generated_gap"]


class _Strict(BaseModel):
    """多余字段拒绝（schema 漂移要 fail-loud，不是吞掉）。

    与 `agents/models.py::_Strict` / `experience/models.py::_Strict` **同款**——
    本仓的约定是**每个包各自定义**这一个三行基类（那两个包就是这么做的），
    而不是跨包 import 一个私有名。
    """

    model_config = ConfigDict(extra="forbid")


class PlannerInput(_Strict):
    """设计 §5.1：Planner 的输入。

    `changed_files` 来自 `source/git_diff.py::changed_files(...).changed_files`
    （Task 1.4）。**允许为空**——见模块 docstring 的「两条不校验」。
    """

    app_build: str
    git_commit: str
    changed_files: list[str] = Field(default_factory=list)


class TestPlanTask(_Strict):
    """设计 §5.1：Plan 里的一条任务。"""

    # 名字以 `Test` 开头是设计 §5.1 逐字要求的（不改名）；但 pytest 会把导入到
    # 测试模块里、名字以 `Test` 开头的类当**测试类**收集并报
    # `PytestCollectionWarning`。`__test__ = False` 是仓内既有手法
    # （`executor/assertion.py` 的两个断言异常、`runner/runner.py` 同款）。
    __test__ = False

    testcase_id: str
    priority: int = Field(ge=PRIORITY_MIN, le=PRIORITY_MAX)
    reasons: list[str]
    source: PlanSource

    @field_validator("reasons")
    @classmethod
    def _reasons_must_not_be_empty(cls, v: list[str]) -> list[str]:
        """容器非空 **且每条 strip 后非空**。

        ⚠️ 只判容器（`if not v`）会被**一个空串**绕过——而空串恰恰是**生产者最容易
        写出的值**：Task 2.2 的 `priority_score` 与 Task 2.3 的 LLM 解释都在字符串
        拼接 / 模板渲染里产出理由（`f"{impact}"` 在 `impact` 为空时就是 `""`）。
        `reasons=[""]` 给出的可解释性**恰好为零**，与「裸分数」在对账时没有区别——
        那正是 F13 要防的东西（review_p3_task21 P3-1）。
        """
        if not v or not all(r.strip() for r in v):
            raise ValueError(
                "reasons 不能为空、也不能含空串/纯空白——可解释性是 F13 的落地："
                "排序结果必须能被人复核（「LLM 觉得」与空串都不算理由）")
        return v


class TestPlan(_Strict):
    """设计 §5.1：一次 Plan 的产物。

    ⚠️ **没有 `created_at`**：设计 §5.1 的模型里没有它，它是 `test_plans` **行**的
    元数据（§11 的表有 `created_at`）→ 由 DB 拥有、由 store 的 dict 携带。
    `from_store_dict` 会**忽略**它（不塞进模型，也不假装模型有）。

    ⚠️ **`schema_version` 当前不落库**（已知缺口，已登记）：设计 §11 的 `test_plans`
    只有 `plan_id / app_build / git_commit / created_at / tasks_json`——**没有版本列**，
    而 `tasks_json` 按列名只装 tasks。后果：**读回的行总是当前版本**，将来把
    `PLAN_SCHEMA_VERSION` 从 `"0.1"` 抬到 `"0.2"` 时，旧行会被**静默**当成新版本。
    两条修法（都要动 schema，**不在 Task 2.1 范围**）：
    ① 迁移 002 加 `schema_version` 列；② 把 `tasks_json` 换成带信封的
    `{"schema_version": …, "tasks": […]}`（列名要一并改）。
    ⚠️ **两种修法都必须同时改 `from_store_dict`**——它现在把 `schema_version` 完全
    忽略、永远取模型默认值（`get_plan` 的返回里根本没有这个键）。只加列不改读取口，
    旧行仍会被静默当成新版本。
    **谁 bump 版本谁先处理这一条**——`docs/p3_data_audit.md` 已登记。
    """

    __test__ = False        # 见 `TestPlanTask` 的说明（pytest 收集抑制）

    schema_version: str = PLAN_SCHEMA_VERSION
    plan_id: str
    app_build: str
    git_commit: str
    tasks: list[TestPlanTask] = Field(default_factory=list)

    # --- 与 agent.db 的 `test_plans` 表互转 -------------------------------------

    def to_store_dict(self) -> dict[str, Any]:
        """→ `SQLiteAgentStore.save_plan(**...)` 的关键字实参形状。

        `tasks` 用 `model_dump(mode="json")`：`source` 落成字符串（JSON 可序列化）。
        `created_at` 与 `schema_version` 都**不在这里**（前者是 DB 元数据，后者当前
        不落库——见类 docstring）。
        """
        return {"plan_id": self.plan_id,
                "app_build": self.app_build,
                "git_commit": self.git_commit,
                "tasks": [t.model_dump(mode="json") for t in self.tasks]}

    @classmethod
    def from_store_dict(cls, row: dict[str, Any]) -> "TestPlan":
        """← `SQLiteAgentStore.get_plan(...)` 的返回。

        **多余的键被忽略**（`created_at` 等 DB 列）：那是「读出」，不是「输入」——
        `extra="forbid"` 保护的是后者。**缺字段仍然 fail-loud**（pydantic 默认）。
        `schema_version` 取模型默认值（DB 不存它，见类 docstring）。
        """
        return cls(plan_id=row["plan_id"], app_build=row["app_build"],
                   git_commit=row["git_commit"],
                   tasks=row.get("tasks") or [])
