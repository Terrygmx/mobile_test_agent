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
                            PlannerInput, TestPlan, TestPlanTask, plan_id_for,
                            require_paths)

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


@pytest.mark.parametrize("bad", [[], (), [""], ["   "], ["ok", ""], ["ok", "  "]])
def test_empty_or_blank_reasons_are_rejected(bad):
    """`reasons` 为空、或**含空串/纯空白** → **拒绝创建**（F13 的落点）。

    只判容器会被**一个空串**绕过，而空串恰恰是生产者最容易写出的值（Task 2.2/2.3 的
    理由都是拼接/模板渲染出来的，`f"{impact}"` 在 `impact` 为空时就是 `""`）——
    `reasons=[""]` 给出的可解释性恰好为零，与「裸分数」在对账时没有区别。

    ⚠️ 参数**不做 `list()` 转换**（review_p3_task21 P3-3）：上一版是
    `parametrize("bad", [[], ()])` + `reasons=list(bad)`，而 `list([])` 与 `list(())`
    都是 `[]` —— 两条 test id 喂的是**同一个实参**，测试数 +1、覆盖面 +0。
    现在 `[]` 与 `()` 各自直接作为 `reasons` 传入（两种真的不同的输入形态，
    顺带钉住 pydantic 对 tuple 的归一化）。
    """
    with pytest.raises(ValidationError, match="reasons"):
        TestPlanTask(**_task(reasons=bad))


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
    store.save_plan(**_plan().to_store_dict())
    raw = store.get_plan("plan_1")["tasks"]      # 已经是 json.loads 过的列表
    assert raw == [{"testcase_id": "login_001", "priority": 70,
                    "reasons": ["impact: 命中改动文件"],
                    "source": "existing"}]


# --- 分层守卫（防包级循环） ----------------------------------------------------


def test_agents_package_does_not_import_planner():
    """`agents/**` **任何模块**都不得 import `planner.*`（依赖方向单向）。

    这不是洁癖，是**包级无环**：`planner` → `agents`（Task 2.3 起 `planner.planner`
    import `agents.storage` 存 Plan），反方向一旦出现就成环。而
    `planner/__init__.py` 会 re-export `planner.planner`，所以 `agents` 侧任何
    module-level 的 `import planner…` 都会在包初始化期把它拉进来。

    ⚠️ **扫描面是 `agents/` 整个包**（review_p3_task21 P3-2）：上一版只扫
    `agents/storage.py`——而「最可能在未来 import `planner` 的恰恰不是它」
    （`agents/tools.py`、`agents/models.py` 都是自然落点），于是
    「agents 侧对 planner 的依赖为零」这句声称**宽于守卫**（A/B：插进 `models.py`
    时 14 passed 全绿）。现在逐文件扫、失败时**指名文件**。

    若将来真有正当需要（例如工具层要声明 plan 的返回类型）：用
    `if TYPE_CHECKING:` 下的 import（不在运行期执行、不成环），并在**本条测试**
    里为它开一个带理由的白名单——别把整条守卫删掉。
    """
    offenders: list[str] = []
    for path in sorted((_ROOT / "agents").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported.extend(a.name for a in node.names)
        bad = [m for m in imported if m.split(".")[0] == "planner"]
        if bad:
            offenders.append(f"{path.relative_to(_ROOT)}: {bad}")

    assert not offenders, (
        "agents 包不该 import planner（依赖方向是 planner → agents，单向）：\n  "
        + "\n  ".join(offenders)
        + "\n修法：把类型/转换留在 planner 侧，或用 `if TYPE_CHECKING:` 下的 import。")
    # 防空转：确认真的扫到了多个文件（范围写错成空时上面的断言会假绿）
    scanned = [p for p in (_ROOT / "agents").rglob("*.py")
               if "__pycache__" not in p.parts]
    assert len(scanned) >= 5, f"只扫到 {len(scanned)} 个 agents/*.py，范围疑似写错"
    assert (_ROOT / "agents" / "storage.py") in scanned, "storage.py 必须被覆盖到"


# --- plan_id 的来源（Task 2.3 前置 ① 的拍板结论） ----------------------------


def test_plan_id_is_stable_for_the_same_inputs():
    """同一输入 → 同一 id：`save_plan` 是 upsert，幂等靠它。"""
    a = plan_id_for("1026", "abc1234", ["a.swift", "b.swift"])
    b = plan_id_for("1026", "abc1234", ["a.swift", "b.swift"])
    assert a == b and a.startswith("plan_")


def test_plan_id_ignores_the_order_of_changed_files():
    """集合相同 → id 相同（`changed_files` 先排序再入哈希）。"""
    assert (plan_id_for("1026", "abc", ["b", "a"]) ==
            plan_id_for("1026", "abc", ["a", "b"]))


@pytest.mark.parametrize("field,value", [
    ("app_build", "1027"), ("git_commit", "deadbee")])
def test_plan_id_changes_when_the_build_identity_changes(field, value):
    """build / commit 变了 → 不同 id（**不互相覆盖**）。"""
    base = plan_id_for("1026", "abc1234", ["a.swift"])
    kwargs = {"app_build": "1026", "git_commit": "abc1234", field: value}
    assert plan_id_for(kwargs["app_build"], kwargs["git_commit"],
                       ["a.swift"]) != base


def test_plan_id_changes_when_changed_files_change():
    """同一 build 下换 base（改动集不同）→ 不同 id。

    这正是「只由 app_build + git_commit 派生」会**静默丢数据**的那个洞：两份 Plan
    撞同一个主键，后写的覆盖前写的。
    """
    assert (plan_id_for("1026", "abc1234", ["a.swift"]) !=
            plan_id_for("1026", "abc1234", ["b.swift"]))


def test_plan_id_is_content_addressed_not_random():
    """确定性：与调用次数/时间无关（F13 的取向延伸到 Plan 的**标识**上）。"""
    ids = {plan_id_for("1026", "abc1234", ["a.swift"]) for _ in range(5)}
    assert len(ids) == 1


def test_plan_id_for_rejects_a_single_string():
    """单个字符串必须拒（review_p3_task23 P3-2）：它是主键 + upsert 键。

    不拒的话 `plan_id_for("1026", "abc", "a.swift")` **不报错**，且 id 与
    `plan_id_for("1026", "abc", list("a.swift"))` **完全相同**——形态与正常 id 无从
    区分，两次 plan 会落到互不相干的行（或覆盖到错误的行），全程无报错。
    """
    with pytest.raises(TypeError, match="可迭代"):
        plan_id_for("1026", "abc", "a.swift")
    with pytest.raises(TypeError, match="可迭代"):
        plan_id_for("1026", "abc", b"a.swift")


def test_plan_id_for_rejects_non_string_paths():
    """非 str 元素也拒——旧实现的 `str(p) for p in changed_files` 会**静默强转**
    （`[42]` → `"42"`），于是「42」与「'42'」两种输入**得到同一个 id**。"""
    with pytest.raises(TypeError, match="str"):
        plan_id_for("1026", "abc", ["a.swift", 42])
    with pytest.raises(TypeError, match="str"):
        plan_id_for("1026", "abc", [42])
    # 只有真的 str 才产 id（`"42"` 是合法的路径字符串，与 42 无关）
    assert plan_id_for("1026", "abc", ["42"]) != plan_id_for("1026", "abc", ["43"])


def test_require_paths_is_the_single_gate():
    """两个调用方共用**同一个**闸门（`plan_id_for` 与 `planner/impact.py`）。"""
    from planner.impact import _paths

    assert _paths(["a", "b"]) == require_paths(["a", "b"], where="x") == ("a", "b")
    for bad, match in (("a.swift", "可迭代"), ([1], "str")):
        with pytest.raises(TypeError, match=match):
            _paths(bad)
        with pytest.raises(TypeError, match=match):
            require_paths(bad, where="x")
