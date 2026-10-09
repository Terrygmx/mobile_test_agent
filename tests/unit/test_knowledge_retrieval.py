"""Task 2.2 / P3-06：KnowledgeSources 装配 + `trace_history` filters 扩展。

fixture 用**真 `TraceStore`** 写 trace（不手搓 SQL——读的是真实列名与真实
`detail` 形状，替身 SQL 会让「列名写错」这类错误静默通过；沿用
`test_graph_builder.py` 的既有手法）。
"""
from __future__ import annotations

import pytest

from knowledge import build_knowledge
from experience.knowledge import KnowledgeSources
from tracer.storage import TraceStore

APP = "com.phaset0.logindemo"
BUILD = "1026"


def _trace(tmp_path, *, build=BUILD, name="trace.db") -> str:
    """写一条真 trace：两个用例、三种失败分类、一个成功步骤。"""
    db = tmp_path / name
    store = TraceStore(db)
    store.start_run("run_1", suite="smoke", app_bundle_id=APP, app_build=build)

    tc1 = store.start_testcase("run_1", "login_001", attempt=1)
    store.record_step(tc1, 0, "launch_app", status="SUCCESS")
    store.record_step(tc1, 1, "tap", target_id="login_button",
                      status="FAILED", failure_type="ELEMENT_NOT_FOUND")
    store.end_testcase(tc1, "FAIL", failure_type="ELEMENT_NOT_FOUND")

    tc2 = store.start_testcase("run_1", "pay_002", attempt=1)
    store.record_step(tc2, 0, "launch_app", status="SUCCESS")
    store.record_step(tc2, 1, "tap", target_id="confirm_pay_button",
                      status="FAILED", failure_type="APP_CRASH")
    store.end_testcase(tc2, "FAIL", failure_type="APP_CRASH")

    tc3 = store.start_testcase("run_1", "search_003", attempt=1)
    store.record_step(tc3, 0, "launch_app", status="SUCCESS")
    store.end_testcase(tc3, "PASS")
    store.end_run("run_1", "FAIL")
    return str(db)


def _case(case_id: str, target: str) -> dict:
    return {"schema_version": "0.2", "id": case_id, "name": case_id,
            "suite": "smoke", "steps": [
                {"action": "launch_app"},
                {"action": "tap", "target": target}]}


def _cases(*pairs):
    from testcase.schema import parse_testcase_dict
    return [parse_testcase_dict(_case(cid, t)) for cid, t in pairs]


@pytest.fixture()
def trace_db(tmp_path) -> str:
    return _trace(tmp_path)


# --- 装配 ---------------------------------------------------------------------


def test_build_knowledge_satisfies_the_protocol(trace_db, tmp_path):
    """返回的实例满足 `KnowledgeSources` Protocol（`runtime_checkable`）。"""
    from experience import SQLiteExperienceStore

    ks = build_knowledge(experience_store=SQLiteExperienceStore(
        tmp_path / "experience.db"), trace_db=trace_db, cases=_cases(
            ("login_001", "HomeView.login_button")), app_build=BUILD)
    assert isinstance(ks, KnowledgeSources)


def test_build_knowledge_adds_no_retrieval_method(trace_db, tmp_path):
    """**只装配，不新增检索方法**（plan Task 2.2 明文 / F3）。

    工厂的公开面必须与 Protocol 的四方法一致——多一个 `search_*` 之类就是
    「平行的检索接口」的开端。
    """
    from experience import SQLiteExperienceStore

    ks = build_knowledge(experience_store=SQLiteExperienceStore(
        tmp_path / "experience.db"), trace_db=trace_db)
    public = {n for n in dir(ks) if not n.startswith("_")}
    assert public == {"experience_lookup", "graph_query", "impact_of",
                      "trace_history"}, f"多出/少了方法：{public}"


def test_p2_knowledge_sources_is_constructed_in_one_place():
    """`P2KnowledgeSources` 的**构造点**全仓只许一处：`knowledge/retrieval.py`。

    F3 的「单一入口」有两半，上一条守卫只钉住一半（公开面 == 四方法）——它**挡不住**
    「P3 侧直接 `P2KnowledgeSources(...)` 绕过 `build_knowledge`」
    （review_p3_task22 P3-3：`build_knowledge` 的生产调用点当时是**零**，那条声称
    还没有落点，所以补这条机械保证）。

    用 AST 扫**真实构造调用**（`ast.Call`），不扫 docstring/注释里的名字——本模块的
    docstring 就写了「而不是让调用方直接 `P2KnowledgeSources(...)`」，文本扫描会
    把它当成一次构造。`phase0/`（P2 的 gate 脚本 `verify_p2_final.py`）与 `tests/`
    （集成测试直接测那个类）显式排除。
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    excluded = {".venv", ".git", "out", "__pycache__", "tests", "phase0"}
    hits: list[str] = []
    scanned = 0
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if excluded & set(rel.parts):
            continue
        scanned += 1
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (func.id if isinstance(func, ast.Name)
                    else getattr(func, "attr", None))
            if name == "P2KnowledgeSources":
                hits.append(str(rel))
    assert scanned >= 80, f"只扫到 {scanned} 个文件，扫描范围疑似写错"
    assert hits == ["knowledge/retrieval.py"], (
        f"`P2KnowledgeSources` 的构造点必须只有 knowledge/retrieval.py"
        f"（F3 的单一入口）——实得 {hits}")


def test_build_knowledge_does_not_touch_disk_at_construction(tmp_path):
    """构造期不读库：`trace_db` 指到不存在的路径也能装配，查询时才报错。

    装配与查询分开，测试才能注入替身；「库没初始化」也不该在装配处炸。
    """
    from experience import SQLiteExperienceStore

    missing = tmp_path / "nope.db"
    ks = build_knowledge(experience_store=SQLiteExperienceStore(
        tmp_path / "experience.db"), trace_db=missing)
    with pytest.raises(ValueError, match="trace 库不可读"):
        ks.trace_history({})


def test_experience_lookup_uses_the_real_store(trace_db, tmp_path):
    """`experience_lookup` 走真 store（REJECTED 排除 + rank 排序）。"""
    from experience import SQLiteExperienceStore
    from experience.models import CandidateSeed
    from repository.loader import LocatorStrategy

    store = SQLiteExperienceStore(tmp_path / "experience.db")
    store.create_candidate(CandidateSeed(
        review_id=1, recovery_id=1, seed_run_id="run_1", seed_step_id=1,
        seed_recovery_review_id=1, app_id=APP, screen_id="HomeView",
        target_id="login_button",
        strategy=LocatorStrategy(type="accessibility_id", value="login_button",
                                 origin="experience")))
    ks = build_knowledge(experience_store=store, trace_db=trace_db)
    got = ks.experience_lookup(APP, "HomeView", "login_button")
    assert [e.target_id for e in got] == ["login_button"]
    assert ks.experience_lookup(APP, "HomeView", "other") == []


def test_impact_of_uses_the_case_index(trace_db, tmp_path):
    from experience import SQLiteExperienceStore

    ks = build_knowledge(
        experience_store=SQLiteExperienceStore(tmp_path / "e.db"),
        trace_db=trace_db,
        cases=_cases(("login_001", "HomeView.login_button"),
                     ("pay_002", "HomeView.confirm_pay_button")))
    assert ks.impact_of("login_button") == ("login_001",)
    assert ks.impact_of("HomeView.confirm_pay_button") == ("pay_002",)
    assert ks.impact_of("nothing") == ()


def test_graph_query_refuses_a_cross_app_scope(trace_db, tmp_path):
    """`graph_query` 不跨 app 混图（既有语义，别被装配层改掉）。"""
    from experience import SQLiteExperienceStore

    ks = build_knowledge(experience_store=SQLiteExperienceStore(
        tmp_path / "e.db"), trace_db=trace_db)
    assert ks.graph_query(APP) is not None
    with pytest.raises(ValueError, match="不一致"):
        ks.graph_query("com.other.app")


# --- trace_history：新增的三个键 ----------------------------------------------


def test_trace_history_filters_by_testcase_id(trace_db, tmp_path):
    ks = build_knowledge(experience_store=_store(tmp_path), trace_db=trace_db,
                         app_build=BUILD)
    steps = ks.trace_history({"testcase_id": "login_001"})
    assert [s.testcase_id for s in steps] == ["login_001"] * 2
    assert [s.step_index for s in steps] == [0, 1]


def test_trace_history_filters_by_failure_type(trace_db, tmp_path):
    ks = build_knowledge(experience_store=_store(tmp_path), trace_db=trace_db,
                         app_build=BUILD)
    crash = ks.trace_history({"failure_type": "APP_CRASH"})
    assert [s.target_id for s in crash] == ["confirm_pay_button"]
    assert crash[0].failure_type == "APP_CRASH"


def test_trace_history_combines_filters_before_limit(trace_db, tmp_path):
    """**顺序语义**：其余过滤键先应用、`limit` 最后（= 最近 N 条**匹配**的）。

    若反过来（先截断再过滤），`limit=1` + `failure_type=APP_CRASH` 会先取到最后
    一条（`search_003` 的成功步骤）再过滤 → 返回空——那是「limit 改变了过滤的
    含义」，不是调用方想要的。
    """
    ks = build_knowledge(experience_store=_store(tmp_path), trace_db=trace_db,
                         app_build=BUILD)
    assert len(ks.trace_history({"failure_type": "APP_CRASH"})) == 1
    assert len(ks.trace_history({"failure_type": "APP_CRASH", "limit": 1})) == 1
    assert len(ks.trace_history({"testcase_id": "login_001", "limit": 1})) == 1
    assert ks.trace_history({"testcase_id": "nope"}) == []


def test_trace_history_legacy_keys_unchanged(trace_db, tmp_path):
    """旧白名单（`app_build` / `limit`）行为不变——P2 调用方不受影响。"""
    ks = build_knowledge(experience_store=_store(tmp_path), trace_db=trace_db)
    all_steps = ks.trace_history({})
    assert len(all_steps) == 5
    assert len(ks.trace_history({"limit": 2})) == 2
    assert ks.trace_history({"limit": 0}) == []
    assert ks.trace_history({"app_build": BUILD}) == all_steps
    assert ks.trace_history({"app_build": "9999"}) == []


def test_trace_history_unknown_key_still_fails_loud(trace_db, tmp_path):
    ks = build_knowledge(experience_store=_store(tmp_path), trace_db=trace_db)
    with pytest.raises(ValueError, match="未知过滤键"):
        ks.trace_history({"screen": "HomeView"})       # 拼错的键（少 _id）


@pytest.mark.parametrize("key", ["testcase_id", "failure_type"])
def test_trace_history_value_type_is_gated(trace_db, tmp_path, key):
    """非 str/None 的过滤值 → `ValueError`（与 `app_build` 同款闸门）。

    非 str 的值会绑进等值比较、**一条也匹配不到**——「过滤生效了，只是库里没有」
    与「过滤参数是坏的」在结果上长得一样。
    """
    ks = build_knowledge(experience_store=_store(tmp_path), trace_db=trace_db)
    with pytest.raises(ValueError, match=key):
        ks.trace_history({key: 123})
    assert ks.trace_history({key: None}) == ks.trace_history({}), "None = 不过滤"


def test_trace_history_screen_id_fails_loud_instead_of_silently_ignoring(
        trace_db, tmp_path):
    """`screen_id` **不可用** → 明确报错，**不静默返回未过滤的结果**。

    trace 里没有屏信息（`steps` 表不记 screen、`target_id` 是解析后的裸元素 id、
    `detail_json` 也没有）。静默接受一个过滤不了的键，等于把「想过滤没过滤」伪装成
    「结果恰好都对」——那正是本方法上面那条未知键检查要防的事。
    """
    ks = build_knowledge(experience_store=_store(tmp_path), trace_db=trace_db)
    with pytest.raises(ValueError, match="screen_id 过滤暂不可用"):
        ks.trace_history({"screen_id": "HomeView"})


def test_two_builds_in_one_db_are_refused_without_app_build(tmp_path):
    """既有语义（12.1）：不带 `app_build` 读一个混了 build 的库 → fail-loud。"""
    db = tmp_path / "two.db"
    store = TraceStore(db)
    for build in ("1025", "1026"):
        store.start_run(f"run_{build}", app_bundle_id=APP, app_build=build)
        tc = store.start_testcase(f"run_{build}", "login_001")
        store.record_step(tc, 0, "launch_app", status="SUCCESS")
        store.end_testcase(tc, "PASS")
        store.end_run(f"run_{build}", "PASS")
    ks = build_knowledge(experience_store=_store(tmp_path), trace_db=str(db))
    with pytest.raises(ValueError, match="多个"):
        ks.trace_history({})
    assert len(ks.trace_history({"app_build": "1025"})) == 1


def _store(tmp_path):
    from experience import SQLiteExperienceStore
    return SQLiteExperienceStore(tmp_path / "experience.db")


# --- F3 在 M2 侧的 import 级断言（review_p3_task23 P3-3） ---------------------

# 直连数据源的模块前缀——它们各自是某个库的读写实现，绕过 `KnowledgeSources`
# 就是「第二套检索接口」的开端（F3）。`agents` 同时是分层守卫的另一半。
_FORBIDDEN_DATASOURCE_IMPORTS = (
    "sqlite3", "tracer", "agents",
    "graph.storage", "experience.store", "source.sqlite_tx",
)


def _all_imports(path):
    """文件里**全部** import 的模块名（含函数内的按需导入——按需导入也是直连）。"""
    import ast

    out: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
        elif isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
    return out


def _is_datasource(module: str) -> bool:
    return any(module == f or module.startswith(f + ".")
               for f in _FORBIDDEN_DATASOURCE_IMPORTS)


def test_planner_package_does_not_touch_data_sources_directly():
    """**F3 在 M2 侧的 import 级断言**（plan 的 F→矩阵映射：M2–M5 各任务都要有）。

    Task 2.3 是 `KnowledgeSources` 的**第一个生产调用者**——「单一入口」从约定变成事实
    就在这一格；M3–M5 还有 generator / explorer / diagnosis 三个同类调用方要来，这一格
    不钉，就要在每个任务里重复判断一次。

    与已有的两条守卫各钉一半、**互补**：

    | 守卫 | 钉住什么 |
    |---|---|
    | `test_agents_package_does_not_import_planner` | `agents/**` **不** import `planner`（反向依赖） |
    | `test_p2_knowledge_sources_is_constructed_in_one_place` | `P2KnowledgeSources(` 的**构造点**只有 `knowledge/retrieval.py` |
    | **本条** | `planner/**` 不**直连数据源**（`sqlite3` / `tracer` / `graph.storage` / `experience.store` / `source.sqlite_tx` / `agents`） |

    AST 扫**全部** import（含函数内的按需导入），失败**指名文件**；带防空转下界
    （文件数 + 必须覆盖到 `planner/planner.py`——扫描面写错时立刻可见）。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "planner"
    files = [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts]
    assert len(files) >= 6, f"只扫到 {len(files)} 个 planner/*.py，扫描面疑似写错"
    names = {p.name for p in files}
    assert {"planner.py", "impact.py", "models.py"} <= names, names

    offenders = {p.name: sorted({m for m in _all_imports(p) if _is_datasource(m)})
                 for p in files}
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, (
        f"planner/** 必须只经 KnowledgeSources，不得直连数据源（F3）：{offenders}")


def test_datasource_predicate_matches_whole_modules_not_prefixes():
    """判据自证：`graph.storage` 命中，而**同前缀的邻居**（`graph.impact`）不误伤。"""
    assert _is_datasource("sqlite3") and _is_datasource("tracer.storage")
    assert _is_datasource("graph.storage") and _is_datasource("agents.storage")
    assert not _is_datasource("graph.impact")
    assert not _is_datasource("source.build_diff")
    assert not _is_datasource("experience.knowledge")
