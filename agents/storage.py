"""storage.py — agent.db 的读写入口（SQLiteAgentStore；Task 1.1 / P3-01）。

设计 §11：独立库文件（默认 `DEFAULT_AGENT_DB` = `out/agent.db`），延续
P2「按写者边界分库」与 E9 单写者纪律（进程锁 + `BEGIN IMMEDIATE`，实现单点
在 `source/sqlite_tx.write_tx`）。迁移复用 P2 泛化执行器（graph.db 同款
用法），幂等可重放；迁移链 `agents/migrations/` 按里程碑增量
（001 → 002/003/004 随 M3/M4/M5 定型）。

⚠️ **CLI `--agent-db` 已接线**（Task 2.4 的 `mta plan` 直接 import `DEFAULT_AGENT_DB`
作 `default`——与 graph 侧 `DEFAULT_GRAPH_DB` → `--graph-db` 同款，避免两处字面量
漂移）。此前 review_p3_task11 P3-1 记的「docstring 写了 `--agent-db` 而该 flag 零命中」
就此核销。

append-only：agent_trace 只 insert 不 update——每步留痕是审计根基。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from agents.models import AgentState, AgentTask, AgentTraceEntry
from experience.schema_migrations import latest_version, migrate
from source.sqlite_tx import write_tx

__all__ = ["AGENT_MIGRATIONS_DIR", "AGENT_SCHEMA_VERSION", "DEFAULT_AGENT_DB",
           "SQLiteAgentStore"]

AGENT_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
AGENT_SCHEMA_VERSION = latest_version(AGENT_MIGRATIONS_DIR, "agents.migrations")

# agent.db 的默认路径（**单点**）。CLI `--agent-db`（Task 2.4 的 `mta plan` 起）
# 直接 import 本常量作 default——与 graph 侧 `DEFAULT_GRAPH_DB` 同款，两处字面量必漂移。
DEFAULT_AGENT_DB = Path("out/agent.db")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class SQLiteAgentStore:
    """agent.db 读写入口。构造即迁移（幂等）。写 = 进程锁 + BEGIN
    IMMEDIATE（E9）；读 = 每调用独立连接，可并发。

    `db_path` 缺省 `DEFAULT_AGENT_DB`（与 `SQLiteExperienceStore` 的
    `out/experience.db` 同款：**可变状态库**给默认值，便于库内自洽地开箱即用）。
    """

    def __init__(self, db_path: str | Path = DEFAULT_AGENT_DB) -> None:
        self._path = str(db_path)
        parent = Path(self._path).parent
        if parent and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        conn = self._connect()
        try:
            migrate(conn, migrations_dir=AGENT_MIGRATIONS_DIR,
                    pkg="agents.migrations")
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        # busy_timeout 单点 5s（与 experience/store.py 逐字一致；review_p3_task11_final
        # P3-2：本处曾抄 experience **已修掉**的 `timeout=30` + `PRAGMA 5000`
        # ——PRAGMA 后设者胜，`timeout=30` 是**死字面量**，读者会以为等 30s。
        # 同一个模式在 experience 侧已被判过一次 P3，「已修过的模式会被复制」。
        conn = sqlite3.connect(self._path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _write_tx(self):
        """E9 单写者：进程锁串行化全部写方法（实现单点在
        `source/sqlite_tx.write_tx`，与 experience / graph 三处共用）。

        ⚠️ 本方法早先是手写的 `_Tx` 类，把 `_connect()` 与
        `BEGIN IMMEDIATE` 放在保护区间**之外**（review_p3_task11 P2-2 实测）：
        一次 `database is locked` 后进程锁**永不释放**，该 store 此后所有写
        永久挂死在 `acquire()` 上——审计留痕（`agent_trace`）从那一刻起整段
        丢失，且没有任何自愈机制。收敛到共享实现后这类错误只有一处可能犯。
        """
        return write_tx(self._write_lock, self._connect)

    # --- agent_tasks ----------------------------------------------------------

    def create_task(self, task_id: str, *, agent_type: str, goal: str,
                    constraints: dict | None = None) -> AgentTask:
        now = _now()
        with self._write_tx() as conn:
            conn.execute(
                "INSERT INTO agent_tasks (task_id, agent_type, goal,"
                " constraints_json, state, start_time)"
                " VALUES (?,?,?,?, 'IDLE', ?)",
                (task_id, agent_type, goal,
                 json.dumps(constraints or {}, ensure_ascii=False), now))
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> AgentTask | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM agent_tasks WHERE task_id=?",
                               (task_id,)).fetchone()
            return self._task_from_row(row) if row else None
        finally:
            conn.close()

    def update_task_state(self, task_id: str, state: AgentState | str,
                          *, outcome: str | None = None,
                          end_time: str | None = None) -> None:
        state_value = state.value if isinstance(state, AgentState) else state
        if state_value not in {s.value for s in AgentState}:
            raise ValueError(
                f"state 只接受 AgentState 枚举值，got {state_value!r}")
        with self._write_tx() as conn:
            cur = conn.execute(
                "UPDATE agent_tasks SET state=?, outcome=COALESCE(?, outcome),"
                " end_time=COALESCE(?, end_time) WHERE task_id=?",
                (state_value, outcome, end_time, task_id))
            if cur.rowcount == 0:
                raise ValueError(f"no such agent task: {task_id}")

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> AgentTask:
        return AgentTask(
            task_id=row["task_id"], agent_type=row["agent_type"],
            goal=row["goal"],
            constraints=json.loads(row["constraints_json"] or "{}"),
            state=AgentState(row["state"]),
            start_time=row["start_time"], end_time=row["end_time"],
            outcome=row["outcome"])

    # --- agent_trace（追加式） ---------------------------------------------------

    def append_trace(self, task_id: str, *, step_index: int | None = None,
                     state: AgentState | None = None,
                     goal: str | None = None,
                     observation: dict | None = None,
                     decision: dict | None = None,
                     guard_result: str | None = None,
                     result: str | None = None,
                     novelty: float | None = None,
                     llm_calls_used: int = 0) -> AgentTraceEntry:
        if self.get_task(task_id) is None:
            raise ValueError(f"no such agent task: {task_id}")
        entry = AgentTraceEntry(
            task_id=task_id, step_index=step_index, state=state, goal=goal,
            observation=observation or {}, decision=decision or {},
            guard_result=guard_result, result=result, novelty=novelty,
            llm_calls_used=llm_calls_used)
        with self._write_tx() as conn:
            conn.execute(
                "INSERT INTO agent_trace (task_id, step_index, state, goal,"
                " observation_json, decision_json, guard_result, result,"
                " novelty, llm_calls_used, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (entry.task_id, entry.step_index,
                 entry.state.value if entry.state else None, entry.goal,
                 json.dumps(entry.observation, ensure_ascii=False),
                 json.dumps(entry.decision, ensure_ascii=False),
                 entry.guard_result, entry.result, entry.novelty,
                 entry.llm_calls_used, entry.created_at
                 .strftime("%Y-%m-%dT%H:%M:%S.%fZ")))
        return entry

    def get_trace(self, task_id: str) -> list[AgentTraceEntry]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM agent_trace WHERE task_id=? ORDER BY id ASC",
                (task_id,)).fetchall()
            return [AgentTraceEntry(
                task_id=r["task_id"], step_index=r["step_index"],
                state=AgentState(r["state"]) if r["state"] else None,
                goal=r["goal"],
                observation=json.loads(r["observation_json"] or "{}"),
                decision=json.loads(r["decision_json"] or "{}"),
                guard_result=r["guard_result"], result=r["result"],
                novelty=r["novelty"], llm_calls_used=r["llm_calls_used"],
                created_at=datetime.strptime(
                    r["created_at"], "%Y-%m-%dT%H:%M:%S.%fZ"
                ).replace(tzinfo=timezone.utc)) for r in rows]
        finally:
            conn.close()

    # --- test_plans ---------------------------------------------------------------

    def save_plan(self, plan_id: str, *, app_build: str | None,
                  git_commit: str | None, tasks: list[dict]) -> None:
        with self._write_tx() as conn:
            conn.execute(
                "INSERT INTO test_plans (plan_id, app_build, git_commit,"
                " created_at, tasks_json) VALUES (?,?,?,?,?)"
                " ON CONFLICT(plan_id) DO UPDATE SET app_build=excluded.app_build,"
                " git_commit=excluded.git_commit, tasks_json=excluded.tasks_json",
                (plan_id, app_build, git_commit, _now(),
                 json.dumps(tasks, ensure_ascii=False)))

    def get_plan(self, plan_id: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM test_plans WHERE plan_id=?",
                               (plan_id,)).fetchone()
            if row is None:
                return None
            return {"plan_id": row["plan_id"],
                    "app_build": row["app_build"],
                    "git_commit": row["git_commit"],
                    "created_at": row["created_at"],
                    "tasks": json.loads(row["tasks_json"] or "[]")}
        finally:
            conn.close()

    def list_plans(self) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM test_plans ORDER BY created_at DESC,"
                " rowid DESC").fetchall()
            return [{"plan_id": r["plan_id"], "app_build": r["app_build"],
                     "git_commit": r["git_commit"],
                     "created_at": r["created_at"],
                     "tasks": json.loads(r["tasks_json"] or "[]")}
                    for r in rows]
        finally:
            conn.close()
