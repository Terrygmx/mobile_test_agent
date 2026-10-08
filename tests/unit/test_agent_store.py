"""Task 1.1 / P3-01：SQLiteAgentStore（agent.db；设计 §11 首批三表）。

- 迁移复用 P2 泛化执行器（graph.db 同款），幂等可重放；
- 单写者（E9 惯例：进程锁 + BEGIN IMMEDIATE）——并发写不丢（矩阵 #3）；
- agent_trace 追加式（不 UPDATE）；
- test_plans.tasks_json 往返序列化。
"""
from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

import pytest

from agents.models import AgentState
from agents.storage import AGENT_MIGRATIONS_DIR, AGENT_SCHEMA_VERSION, \
    SQLiteAgentStore


@pytest.fixture()
def store(tmp_path) -> SQLiteAgentStore:
    return SQLiteAgentStore(tmp_path / "agent.db")


def _task(store: SQLiteAgentStore, task_id: str = "task_1",
          agent_type: str = "explorer") -> None:
    store.create_task(task_id, agent_type=agent_type, goal="explore Home")


# --- 迁移（矩阵 #3 前半：幂等可重放） ----------------------------------------


def test_migration_idempotent(tmp_path):
    db = tmp_path / "agent.db"
    SQLiteAgentStore(db)
    SQLiteAgentStore(db)            # 重放：构造即迁移，二次不炸不重复
    from experience.schema_migrations import current_version
    conn = SQLiteAgentStore(db)._connect()
    try:
        version = current_version(conn)
    finally:
        conn.close()
    assert version == AGENT_SCHEMA_VERSION
    assert AGENT_MIGRATIONS_DIR.name == "migrations"


def test_first_batch_tables_exist(store):
    conn = store._connect()
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"agent_tasks", "agent_trace", "test_plans"} <= tables
    assert "test_candidates" not in tables, \
        "002+ 随 M3/M4/M5 各自定型（P2 Task 1.2 教训：不提前建表）"


# --- agent_tasks --------------------------------------------------------------


def test_create_task_defaults_and_state_validation(store):
    _task(store)
    task = store.get_task("task_1")
    assert task.state is AgentState.IDLE
    assert task.goal == "explore Home"
    with pytest.raises(ValueError, match="state"):
        store.update_task_state("task_1", "BOGUS_STATE")


def test_update_task_state(store):
    _task(store)
    store.update_task_state("task_1", AgentState.PLANNING, outcome=None)
    assert store.get_task("task_1").state is AgentState.PLANNING
    with pytest.raises(ValueError, match="no such"):
        store.update_task_state("task_missing", AgentState.PLANNING)


# --- agent_trace（追加式） ------------------------------------------------------


def test_trace_is_append_only(store):
    _task(store)
    store.append_trace("task_1", step_index=0, state=AgentState.OBSERVING,
                       observation={"screen": "HomeView"})
    store.append_trace("task_1", step_index=1, state=AgentState.DECIDING,
                       decision={"tool": "tap"}, guard_result="ALLOW")
    rows = store.get_trace("task_1")
    assert [r.step_index for r in rows] == [0, 1]
    assert rows[1].decision == {"tool": "tap"}
    assert rows[0].observation == {"screen": "HomeView"}
    with pytest.raises(ValueError, match="no such"):
        store.append_trace("task_missing", step_index=0,
                           state=AgentState.IDLE)


# --- test_plans ----------------------------------------------------------------


def test_plan_roundtrip(store):
    tasks = [{"task_id": "t1", "priority": 90, "reasons": ["impact"]},
             {"task_id": "t2", "priority": 40, "reasons": ["history"]}]
    store.save_plan("plan_1", app_build="1026", git_commit="abc1234",
                    tasks=tasks)
    plan = store.get_plan("plan_1")
    assert plan["app_build"] == "1026"
    assert plan["tasks"] == tasks, "tasks_json 往返"
    assert store.get_plan("missing") is None


def test_list_plans_most_recent_first(store):
    store.save_plan("plan_old", app_build="1025", git_commit="a", tasks=[])
    store.save_plan("plan_new", app_build="1026", git_commit="b", tasks=[])
    ids = [p["plan_id"] for p in store.list_plans()]
    assert ids.index("plan_new") < ids.index("plan_old")


# --- 单写者：并发写不丢（矩阵 #3 后半，沿 P2 #12 手法） ------------------------


def test_concurrent_appends_no_lost_update(store):
    _task(store)
    errors: list[str] = []

    def worker(prefix: str) -> None:
        try:
            for i in range(50):
                store.append_trace("task_1", step_index=i,
                                   state=AgentState.OBSERVING,
                                   observation={"w": prefix, "i": i})
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")

    t1 = threading.Thread(target=worker, args=("t1",))
    t2 = threading.Thread(target=worker, args=("t2",))
    t1.start(); t2.start()
    t1.join(); t2.join()
    assert not errors
    rows = store.get_trace("task_1")
    assert len(rows) == 100, "双线程各 50 次 append，计数和 200 的一半也不许丢"


# --- review_p3_task11 P2-2：写事务的异常路径不得泄漏进程锁 --------------------


def _fast_connect(db, *, busy_ms: int = 50):
    """`busy_timeout` 调到 50ms 的连接工厂（测试不必等生产的 5s）。"""
    def connect() -> sqlite3.Connection:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={busy_ms}")
        return conn
    return connect


def test_write_lock_released_after_lock_conflict(tmp_path, monkeypatch):
    """P2-2 钉子：`BEGIN IMMEDIATE` 失败后进程锁必须释放。

    评审 A/B 探针的场景（另一条连接持 `BEGIN EXCLUSIVE`）。修复前
    `_Tx.__enter__` 把 `_connect()` 与 `BEGIN IMMEDIATE` 放在保护区间**外**，
    锁永不释放——该 store 之后**每一次写都阻塞在 `acquire()` 上**（无超时、
    无自愈），审计留痕从那一刻起整段丢失。
    """
    db = tmp_path / "agent.db"
    store = SQLiteAgentStore(db)
    store.create_task("t1", agent_type="planner", goal="g")

    blocker = sqlite3.connect(str(db), isolation_level=None)
    try:
        blocker.execute("BEGIN EXCLUSIVE")
        monkeypatch.setattr(store, "_connect", _fast_connect(db))
        with pytest.raises(sqlite3.OperationalError):
            store.create_task("t2", agent_type="planner", goal="g")
        assert not store._write_lock.locked(), "异常路径必须释放进程锁"
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    store.create_task("t2", agent_type="planner", goal="g")
    assert store.get_task("t2") is not None, "冲突解除后必须还能写（锁没被吃掉）"


def test_update_task_state_failure_also_releases_lock(store, monkeypatch):
    """业务异常（no such task）同样不得泄漏锁——它在 `with` 体内抛出。"""
    store.create_task("t1", agent_type="planner", goal="g")
    with pytest.raises(ValueError, match="no such agent task"):
        store.update_task_state("ghost", AgentState.PLANNING)
    assert not store._write_lock.locked()
    store.update_task_state("t1", AgentState.PLANNING)
    assert store.get_task("t1").state is AgentState.PLANNING


# --- review_p3_task11 P3-3：SQL CHECK 词表 ↔ Python 枚举必须一致 ---------------


def test_state_check_constraint_matches_enum(store):
    """`001_agent_schema.sql` 的 `CHECK (state IN (…))` 与 `AgentState` 逐值一致。

    `state` 的词表有**两处字面量**（SQL 的 CHECK 与 Python 枚举）。当前一致，
    但此前**没有任何测试钉住**——M6 加第 10 态若忘改 SQL，`update_task_state`
    会抛 `IntegrityError`（loud、不会静默坏数据，所以定 P3，但 loud 失败也该
    在 CI 里被更早发现）。手法同 `test_tool_asset_tier_covers_full_allowlist`
    的三方对账。
    """
    conn = store._connect()
    try:
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table'"
            " AND name='agent_tasks'").fetchone()[0]
    finally:
        conn.close()
    block = re.search(r"CHECK\s*\(\s*state\s+IN\s*\((.*?)\)\s*\)", ddl, re.S)
    assert block, f"没解析到 state 的 CHECK 子句：{ddl!r}"
    in_sql = set(re.findall(r"'([^']*)'", block.group(1)))
    assert in_sql == {s.value for s in AgentState}, \
        f"SQL 词表与枚举漂移：仅 SQL 有 {sorted(in_sql - {s.value for s in AgentState})}，"\
        f"仅枚举有 {sorted({s.value for s in AgentState} - in_sql)}"


def test_state_check_rejects_bad_value_written_by_raw_sql(store):
    """绕过 Python 直接写坏值 → CHECK 兜住（`IntegrityError`，不静默落库）。"""
    with pytest.raises(sqlite3.IntegrityError):
        with store._write_tx() as conn:
            conn.execute(
                "INSERT INTO agent_tasks (task_id, agent_type, goal, state)"
                " VALUES ('t','x','g','BOGUS')")
    assert not store._write_lock.locked(), "CHECK 失败同样不得泄漏锁"
    assert store.get_task("t") is None, "坏值没有落库"


# --- review_p3_task11 P3-1：docstring 声称的落点必须真的存在 -------------------


def test_default_agent_db_is_a_real_single_point(tmp_path, monkeypatch):
    """`DEFAULT_AGENT_DB` 常量落地，且 `SQLiteAgentStore()` 真的用它。

    早先 docstring 写「默认 out/agent.db，**CLI --agent-db 可覆盖**」，而
    `--agent-db` 全仓零命中、常量不存在、`db_path` 必填——docstring 描述的是
    「P3 完成态」（review_p3_task11 P3-1）。现在常量落地（CLI 接线归 Task 2.4，
    与 graph 侧 `DEFAULT_GRAPH_DB` → `--graph-db` 同款）。
    """
    from agents import DEFAULT_AGENT_DB
    from agents.storage import DEFAULT_AGENT_DB as from_storage

    assert DEFAULT_AGENT_DB == Path("out/agent.db")
    assert DEFAULT_AGENT_DB is from_storage, "包门面与模块必须是同一个对象"

    monkeypatch.chdir(tmp_path)
    store = SQLiteAgentStore()                      # 不传路径 → 用常量
    assert Path(store._path) == DEFAULT_AGENT_DB
    assert (tmp_path / "out" / "agent.db").exists(), "默认路径真的被创建"
