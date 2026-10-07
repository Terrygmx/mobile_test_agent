"""Task 1.1 / P3-01：SQLiteAgentStore（agent.db；设计 §11 首批三表）。

- 迁移复用 P2 泛化执行器（graph.db 同款），幂等可重放；
- 单写者（E9 惯例：进程锁 + BEGIN IMMEDIATE）——并发写不丢（矩阵 #3）；
- agent_trace 追加式（不 UPDATE）；
- test_plans.tasks_json 往返序列化。
"""
from __future__ import annotations

import threading

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
