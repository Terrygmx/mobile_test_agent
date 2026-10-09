"""Task 2.1 / P3-05：Test Planner 数据模型 + Plan 持久化（设计 §5.1 / §11）。

plan Steps 的三条：序列化/反序列化往返；`reasons` 为空 → 拒绝创建；`source` 仅
`existing` / `generated_gap` 两值。另加：`schema_version` 与 TestCase 的版本**无关**、
`priority` 值域、`extra=forbid`、以及**分层守卫**（`agents/storage.py` 不 import
`planner`——防包级循环，见 `planner/models.py` 的「分层」段）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.storage import SQLiteAgentStore
from planner.models import (PLAN_SCHEMA_VERSION, PRIORITY_MAX, PRIORITY_MIN,
                            PlannerInput, TestPlan, TestPlanTask)

_ROOT = Path(__file__).resolve().parents[2]


def _task(**over) -> dict:
    base = {"testcase_id": "login_001", "priority": 70,
            "reasons": ["impact: 命中改动文件"], "source": "existing"}
    base.update(over)
    return base


def _plan(**over) -> TestPlan:
    base = {"plan_id": "plan_1", "app_build": "1025", "git_commit": "abc123",
            "tasks": [TestPlanTask(**_task())]}
    base.update(over)
    return TestPlan(**base)


@pytest.fixture()
def store(tmp_path) -> SQLiteAgentStore:
    return SQLiteAgentStore(tmp_path / "agent.db")


# --- 模型 ---------------------------------------------------------------------


def test_plan_version_is_independent_of_testcase_schema():
    """`schema_version="0.1"` 是 **TestPlan 自己的**版本，与 TestCase 的 0.2 无关。

    两处容易混：`testcase/schema.py::SCHEMA_VERSION`（"0.2"）是用例 YAML 的版本。
    这条断言把「两个常量是不同版本线」钉住——若有人把 testcase 的常量搬过来当默认
    值，它会红。
    """
    from testcase.schema import SCHEMA_VERSION as TESTCASE_VERSION

    assert PLAN_SCHEMA_VERSION == "0.1"
    assert TESTCASE_VERSION == "0.2"
    assert PLAN_SCHEMA_VERSION != TESTCASE_VERSION
    assert _plan().schema_version == PLAN_SCHEMA_VERSION
    assert _plan().schema_version != TESTCASE_VERSION


def test_round_trip_model_json():
    """模型 ↔ JSON 往返（`model_dump` / `model_validate`）逐字段相等。"""
    plan = _plan()
    again = TestPlan.model_validate(plan.model_dump(mode="json"))
    assert again == plan
    assert again.tasks[0].source == "existing"


def test_task_source_accepts_only_the_two_design_values():
    """设计 §5.1 的 `Literal["existing", "generated_gap"]`——第三值 fail-loud。"""
    assert TestPlanTask(**_task(source="existing")).source == "existing"
    assert TestPlanTask(**_task(source="generated_gap")).source == "generated_gap"
    with pytest.raises(ValidationError):
        TestPlanTask(**_task(source="generated"))


@pytest.mark.parametrize("bad", [[], ()])
def test_empty_reasons_is_rejected(bad):
    """`reasons` 为空 → **拒绝创建**（可解释性是 F13 的落地，不是裸分数）。

    `priority` 单独存在时人无法复核、也无法与 LLM 的解释对账。
    """
    with pytest.raises(ValidationError, match="reasons"):
        TestPlanTask(**_task(reasons=list(bad)))


def test_priority_must_be_within_design_range():
    """设计 §5.1 的行内注释：「priority: int  # 0-100，确定性评分」。

    超出范围说明**算分逻辑有 bug**（不是「分数就是高」）→ fail-loud 比让 300 分
    排在最前好。两端点（0 / 100）是合法值。
    """
    assert TestPlanTask(**_task(priority=PRIORITY_MIN)).priority == 0
    assert TestPlanTask(**_task(priority=PRIORITY_MAX)).priority == 100
    for bad in (PRIORITY_MIN - 1, PRIORITY_MAX + 1, 300, -5):
        with pytest.raises(ValidationError):
            TestPlanTask(**_task(priority=bad))


def test_unknown_keys_are_rejected():
    """`extra=forbid`：schema 漂移要 fail-loud（与 agents/experience 同款）。"""
    with pytest.raises(ValidationError):
        TestPlanTask(**_task(bogus=1))
    with pytest.raises(ValidationError):
        TestPlan(**{**_plan().model_dump(), "bogus": 1})
    with pytest.raises(ValidationError):
        PlannerInput(app_build="1", git_commit="c", changed_files=[], bogus=1)


def test_empty_changed_files_and_empty_tasks_are_legal():
    """**空 `changed_files` / 空 `tasks` 是合法输入**（设计矩阵 #6：改动为空 →
    空 Plan 且明示）。

    把「空」当错误会让「这次真没改什么」与「git diff 失败了」混为一谈——后者在
    `source/git_diff.py` 已经是 fail-loud 的 `GitDiffError`，这里不该再兜一层。
    """
    assert PlannerInput(app_build="1", git_commit="c",
                        changed_files=[]).changed_files == []
    assert _plan(tasks=[]).tasks == []


# --- 持久化（agent.db 的 test_plans 表） ---------------------------------------


def test_plan_persistence_round_trip(store):
    """`TestPlan` → `save_plan` → `get_plan` → `from_store_dict` 逐字段相等。"""
    plan = _plan(tasks=[TestPlanTask(**_task()),
                        TestPlanTask(**_task(testcase_id="pay_002", priority=95,
                                             source="generated_gap",
                                             reasons=["risk: CRITICAL 元素"]))])
    store.save_plan(**plan.to_store_dict())

    row = store.get_plan("plan_1")
    assert row is not None
    assert row["app_build"] == "1025" and row["git_commit"] == "abc123"
    assert row["created_at"], "created_at 是**行**的元数据（DB 拥有）"
    back = TestPlan.from_store_dict(row)
    assert back == plan
    assert [t.testcase_id for t in back.tasks] == ["login_001", "pay_002"]


def test_from_store_dict_ignores_db_metadata(store):
    """读回时**忽略 DB 列**（`created_at`），但**缺字段仍然 fail-loud**。

    `extra="forbid"` 保护的是「输入」而不是「读出」：DB 的列不是用户写错的字段。
    """
    store.save_plan(**_plan().to_store_dict())
    row = store.get_plan("plan_1")
    assert "created_at" in row and "schema_version" not in row
    plan = TestPlan.from_store_dict(row)
    assert plan.schema_version == PLAN_SCHEMA_VERSION, "DB 不存版本 → 取默认值"
    with pytest.raises(KeyError):
        TestPlan.from_store_dict({"plan_id": "x"})       # 缺 app_build/git_commit


def test_get_plan_returns_none_for_unknown_id(store):
    assert store.get_plan("nope") is None


def test_save_plan_upserts_same_plan_id(store):
    """同一 `plan_id` 重存 → 覆盖（不新增行）。"""
    store.save_plan(**_plan().to_store_dict())
    store.save_plan(**_plan(tasks=[]).to_store_dict())
    rows = store.list_plans()
    assert len(rows) == 1
    assert TestPlan.from_store_dict(rows[0]).tasks == []


def test_saved_tasks_json_is_the_model_shape(store):
    """落库的 `tasks_json` 是**模型的 JSON 形状**（`source` 是字符串、键集固定）。

    这条防的是「往返恰好相等但落库形状漂移」——下游（报告 / `mta plan --json`）
    读的是库里的 JSON，不是内存里的模型。
    """
    import json

    store.save_plan(**_plan().to_store_dict())
    raw = json.loads(json.dumps(store.get_plan("plan_1")["tasks"]))
    assert raw == [{"testcase_id": "login_001", "priority": 70,
                    "reasons": ["impact: 命中改动文件"],
                    "source": "existing"}]


# --- 分层守卫（防包级循环） ----------------------------------------------------


def test_agents_storage_does_not_import_planner():
    """`agents/storage.py` **不得** import 任何 `planner.*`。

    这不是洁癖：`planner/__init__.py` 会 re-export `planner.planner`（Task 2.3 起，
    它反过来 import `agents.storage`）——一旦 `agents.storage` 反向 import
    `planner.models`，包初始化期就会形成环。存取转换因此留在 `planner/models.py`
    （`to_store_dict` / `from_store_dict`），`agents` 侧对 `planner` 的依赖为**零**。
    """
    src = (_ROOT / "agents" / "storage.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
        elif isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
    assert not [m for m in imported if m.split(".")[0] == "planner"], \
        f"agents/storage.py 不该 import planner：{imported}"
