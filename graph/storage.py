"""storage.py — Graph 库（SQLite + 独立迁移链；Task 5.1 / P2-11）。

设计 12 / plan Task 1.2 的关键决策：graph **独立库文件**（默认 `out/graph.db`，
CLI `--graph-db`）+ **独立版本链**（`graph/migrations/`），不挂在 experience 包下。
迁移执行器复用 `experience/schema_migrations.py`（Task 1.2 已按「只认目录 +
版本链」泛化，Task 5.1 把目录改成参数）——两套链各自演进、共用一份实现。

## 为什么 `upsert_graph` 是「按范围整体替换」而不是累加

图的权威输入是 **trace 全量**（`steps` 表里已经累积了所有 run），所以重跑
`graph build` 必须得到同一张图——**幂等**。累加会让第二次 build 把 visit_count
翻倍，而图本身没有「这次增量是哪几个 run」的信息可用于去重。替换语义还有一个
好处：trace 里的旧 run 被清理后，图会跟着收敛，不会留下幽灵节点。
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from graph.builder import build_runtime_graph, read_trace_steps
from graph.models import (
    RUNTIME,
    RuntimeGraph,
    ScreenNode,
    ScreenTransition,
)
from experience.schema_migrations import latest_version, migrate
from source.sqlite_tx import write_tx

__all__ = ["GRAPH_MIGRATIONS_DIR", "GRAPH_SCHEMA_VERSION", "GraphStore",
           "build_and_store"]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

GRAPH_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

GRAPH_SCHEMA_VERSION = latest_version(GRAPH_MIGRATIONS_DIR, "graph.migrations")


class GraphStore:
    """Graph 库的读写入口。构造即迁移（幂等）。"""

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        parent = Path(self._path).parent
        if parent and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        # E9 单写者（review_p3_task11 P2-2 补齐）：本库早先用 `with conn:`
        # 隐式事务，**无进程锁、无 BEGIN IMMEDIATE**——与 experience/agents
        # 三处形态里的第三形态。写纪律现收敛到 source/sqlite_tx.write_tx。
        self._write_lock = threading.Lock()
        conn = self._connect()
        try:
            migrate(conn, migrations_dir=GRAPH_MIGRATIONS_DIR,
                    pkg="graph.migrations")
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        # busy_timeout 单点 5s（与 experience/store.py、agents/storage.py 逐字一致
        # ——review_p3_task11_final P3-2：本处原先是**第三种形态**（不传 timeout、
        # 不设 PRAGMA，靠 sqlite3 默认的 5s）。功能等价，但「哪个才是权威值」被
        # 交给了阅读顺序；显式写出来才可核。
        conn = sqlite3.connect(self._path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _write_tx(self):
        """E9 单写者：进程锁 + `BEGIN IMMEDIATE`（实现单点在
        `source/sqlite_tx.write_tx`，与 experience / agents 共用）。"""
        return write_tx(self._write_lock, self._connect)

    # --- 写 ---

    def upsert_graph(self, graph: RuntimeGraph) -> None:
        """把一张图写进库（按 `(app_id, app_build, source_of)` **整体替换**）。

        见模块 docstring：替换而不是累加，因为权威输入是 trace 全量、重跑必须
        幂等。节点与转移在同一个事务里换掉，读者看不到「一半新一半旧」的图。
        """
        with self._write_tx() as conn:      # 单事务（进程锁 + BEGIN IMMEDIATE）
            # 替换范围用**图自身的** source_of，不是常量 RUNTIME
            # （review_p2_task51 P2-1：硬编码会让写 source 图时先删掉同
            # scope 的 runtime 行——声明面静默清空观察面）。
            conn.execute(
                "DELETE FROM screen_nodes WHERE app_id=? AND app_build=?"
                " AND source_of=?",
                (graph.app_id, graph.app_build, graph.source_of))
            conn.execute(
                "DELETE FROM screen_transitions WHERE app_id=?"
                " AND app_build=? AND source_of=?",
                (graph.app_id, graph.app_build, graph.source_of))
            conn.executemany(
                "INSERT INTO screen_nodes (app_id, app_build, screen_id,"
                " source_of, visit_count, evidence, first_seen, last_seen)"
                " VALUES (?,?,?,?,?,?,?,?)",
                [(graph.app_id, graph.app_build, n.screen_id, n.source_of,
                  n.visit_count, n.evidence_csv, n.first_seen,
                  n.last_seen) for n in graph.nodes])
            conn.executemany(
                "INSERT INTO screen_transitions (app_id, app_build,"
                " from_screen, to_screen, trigger, source_of, count,"
                " first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?)",
                [(graph.app_id, graph.app_build, t.from_screen,
                  t.to_screen, t.trigger, t.source_of, t.count,
                  t.first_seen, t.last_seen) for t in graph.transitions])

    # --- diff 落库（`graph_diffs` 表，Task 5.3 的消费者） ---

    def record_diff(self, diff) -> int:
        """把一次 Graph Diff 落库（按 `(app_id, base_build, build,
        base_source_of, source_of)` **整体替换**）。

        与图同样的替换语义（重跑 `graph diff` 必须幂等），并且同样在**单事务**里
        换掉——读者看不到「一半新一半旧」的差异列表。

        两面来源（`base_source_of`/`source_of`）**进替换键**（review_p2_task53
        P3-2）：否则同一 `(app_id, base_build, build)` 下「源图 vs 运行时」与
        「build-to-build」会互相覆盖，而它们在库里本来就长得一样。
        """
        with self._write_tx() as conn:
            conn.execute(
                "DELETE FROM graph_diffs WHERE app_id=? AND base_build=?"
                " AND build=? AND base_source_of=? AND source_of=?",
                (diff.app_id, diff.base_build, diff.build,
                 diff.base_source_of, diff.source_of))
            conn.executemany(
                "INSERT INTO graph_diffs (app_id, base_build, build,"
                " base_source_of, source_of, kind, screen_id, from_screen,"
                " to_screen, trigger, detail_json, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(diff.app_id, diff.base_build, diff.build,
                  diff.base_source_of, diff.source_of, e.kind, e.screen_id,
                  e.from_screen, e.to_screen, e.trigger,
                  json.dumps(e.detail, ensure_ascii=False), _now())
                 for e in diff.entries])
        return len(diff.entries)

    def load_diff(self, app_id: str = "", base_build: str = "",
                  build: str = "", base_source_of: str | None = None,
                  source_of: str | None = None) -> list[dict]:
        """读回差异行（按 kind 排序，便于报告稳定呈现）。

        两面来源缺省 `None` = **不过滤**（列出该 build 对下的全部差异，行里带
        `base_source_of`/`source_of` 供调用方分辨是哪种比较）。
        """
        where = "app_id=? AND base_build=? AND build=?"
        params: list = [app_id, base_build, build]
        if base_source_of is not None:
            where += " AND base_source_of=?"
            params.append(base_source_of)
        if source_of is not None:
            where += " AND source_of=?"
            params.append(source_of)
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT base_source_of, source_of, kind, screen_id,"
                " from_screen, to_screen, trigger, detail_json, created_at"
                " FROM graph_diffs WHERE " + where
                + " ORDER BY kind, screen_id, from_screen, to_screen",
                params).fetchall()
        finally:
            conn.close()
        return [{"base_source_of": r["base_source_of"],
                 "source_of": r["source_of"],
                 "kind": r["kind"], "screen_id": r["screen_id"],
                 "from_screen": r["from_screen"], "to_screen": r["to_screen"],
                 "trigger": r["trigger"],
                 "detail": json.loads(r["detail_json"]) if r["detail_json"]
                 else {},
                 "created_at": r["created_at"]} for r in rows]

    # --- 读 ---

    def load_graph(self, app_id: str = "", app_build: str = "",
                   source_of: str = RUNTIME) -> RuntimeGraph:
        conn = self._connect()
        try:
            nodes = conn.execute(
                "SELECT screen_id, source_of, visit_count, evidence,"
                " first_seen, last_seen FROM screen_nodes"
                " WHERE app_id=? AND app_build=? AND source_of=?"
                " ORDER BY screen_id",
                (app_id, app_build, source_of)).fetchall()
            trans = conn.execute(
                "SELECT from_screen, to_screen, trigger, source_of, count,"
                " first_seen, last_seen FROM screen_transitions"
                " WHERE app_id=? AND app_build=? AND source_of=?"
                " ORDER BY from_screen, to_screen, trigger",
                (app_id, app_build, source_of)).fetchall()
        finally:
            conn.close()
        return RuntimeGraph(
            app_id=app_id, app_build=app_build, source_of=source_of,
            nodes=tuple(
                ScreenNode(screen_id=r["screen_id"], source_of=r["source_of"],
                           visit_count=r["visit_count"],
                           evidence=tuple(
                               e for e in (r["evidence"] or "").split(",") if e),
                           first_seen=r["first_seen"],
                           last_seen=r["last_seen"]) for r in nodes),
            transitions=tuple(
                ScreenTransition(from_screen=r["from_screen"],
                                 to_screen=r["to_screen"],
                                 trigger=r["trigger"] or "",
                                 source_of=r["source_of"], count=r["count"],
                                 first_seen=r["first_seen"],
                                 last_seen=r["last_seen"]) for r in trans))

    def list_scopes(self) -> list[tuple[str, str, str]]:
        """库里已有的 `(app_id, app_build, source_of)` 范围（CLI `graph show` 用）。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT DISTINCT app_id, app_build, source_of FROM screen_nodes"
                " UNION SELECT DISTINCT app_id, app_build, source_of"
                " FROM screen_transitions ORDER BY app_id, app_build,"
                " source_of").fetchall()
        finally:
            conn.close()
        return [(r["app_id"], r["app_build"], r["source_of"]) for r in rows]


def build_and_store(trace_db: str | Path, graph_db: str | Path, *,
                    app_build: str | None = None) -> RuntimeGraph:
    """便捷入口：trace → 建图 → 落库，返回图（CLI 与 Gate 脚本用）。

    判定全在 `builder`（纯函数），本函数只做「读 → 建 → 写」三步串接——
    **不提供第二条建图路径**（同一概念只许一处实现）。
    """
    steps, app_id, build = read_trace_steps(trace_db, app_build=app_build)
    graph = build_runtime_graph(steps, app_id=app_id, app_build=build)
    GraphStore(graph_db).upsert_graph(graph)
    return graph
