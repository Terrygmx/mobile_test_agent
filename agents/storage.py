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

    # --- test_candidates（迁移 002；Task 3.4 / P3-12） ---------------------------
    #
    # ⚠️ **写 SQL 全在本模块**（plan §2 注意事项 #5：agent.db 是可变状态库，
    # 单写者 = agents/storage.py）。`candidates/test_case.py` 只提供模型与
    # dict 互转，不碰 sqlite——那条边界有守卫（`test_repo_hygiene.py` 扫
    # `_STORE_FILES` 的写语句区间）。

    def save_candidate(self, candidate_id: str, *, testcase_yaml: str,
                       generation_evidence: dict,
                       generated_by: str | None = None,
                       fingerprint: str | None = None,
                       dry_run_status: str = "PENDING",
                       dry_run_detail: dict | None = None) -> dict:
        """落一条候选（upsert：`candidate_id` 是主键）。

        **幂等**：同 id 重存覆盖（与 `save_plan` 同款）——生成器重跑同一批 gap
        会算出同 id 的候选，不该线性增长出重复行。

        `created_at` 只在**首次插入**时取 `_now()`（`ON CONFLICT` 分支不更新它）
        ——「这条候选什么时候第一次生成出来的」是审计事实，重存不该改写它。

        ⚠️ **入库的候选必有指纹**（`fingerprint is None` → `ValueError`）。
        迁移 002 的 docstring 早就声称有这道闸门（`fingerprint` 列在 DDL 里可空
        是为**留痕表**的 `SCHEMA_INVALID` 行服务的，两表列定义同形而语义不同）
        ——review_p3_task34 P2-1 实锤它**当时并不存在**。为什么必须 fail-loud：
        `generator.generate` 建去重基准时写的是 `if cand.get("fingerprint")`，
        一个 `NULL` 指纹的候选会被**静默跳过** → 它**永久不参与去重** → 同一条
        用例可以被反复生成入库，而每一步都「看起来正常」。

        ⚠️ **已人工决策的候选不可覆写**（AE 第 1 层，handover §9 前置 2）：
        探针 AE 实测 upsert 的 `ON CONFLICT` 不含 `review_status` → 生成器重跑
        同 gap 会改写已 ACCEPT 候选的 `testcase_yaml` 而 `review_status` 保持
        ACCEPT → 落盘文件与库内容分叉。已决行经生成器写路径 fail-loud（第 2 层
        ——generate 的预过滤——让这条闸门在日常路径上根本轮不到触发）。

        ⚠️ **`review_status` 不是本方法的形参**（review_p3_task35 P2-2）：
        本方法**只写 `'PENDING'`**（字面量，见下方 INSERT）——`review_status`
        的唯一写路径是 `decide_candidate`。早先版本接受 `review_status=` 形参，
        于是「唯一写路径」是**过度声称**：这条旁路能产出 `ACCEPT` 但
        `reviewer`/`reviewed_at` 全是 `NULL` 的行（看起来像已审核，实则无人审核），
        而它绕过了 `decide_candidate` 的全部领域校验（note 必填 / 不可二次决策）。
        **形参删掉而不是加断言**：断言仍留一个可被绕过的入口，而删掉之后调用方
        `save_candidate(..., review_status=…)` 会以 `TypeError` fail-loud。
        """
        if not fingerprint:
            raise ValueError(
                f"入库的候选必须有 fingerprint（F8 去重的前提），"
                f"got candidate_id={candidate_id!r}, fingerprint={fingerprint!r}")
        with self._write_tx() as conn:
            existing = conn.execute(
                "SELECT review_status FROM test_candidates WHERE candidate_id=?",
                (candidate_id,)).fetchone()
            if existing is not None and existing["review_status"] != "PENDING":
                raise ValueError(
                    f"候选 {candidate_id} 已人工决策"
                    f"（{existing['review_status']}）——生成器不得覆写（AE，"
                    f"handover §9 前置 2）；翻案走人工通道")
            conn.execute(
                "INSERT INTO test_candidates (candidate_id, testcase_yaml,"
                " generated_by, generation_evidence_json, fingerprint,"
                " dry_run_status, dry_run_detail_json, review_status,"
                " created_at)"
                " VALUES (?,?,?,?,?,?,?,'PENDING',?)"
                " ON CONFLICT(candidate_id) DO UPDATE SET"
                " testcase_yaml=excluded.testcase_yaml,"
                " generated_by=excluded.generated_by,"
                " generation_evidence_json=excluded.generation_evidence_json,"
                " fingerprint=excluded.fingerprint,"
                " dry_run_status=excluded.dry_run_status,"
                " dry_run_detail_json=excluded.dry_run_detail_json",
                (candidate_id, testcase_yaml, generated_by,
                 json.dumps(generation_evidence, ensure_ascii=False),
                 fingerprint, dry_run_status,
                 json.dumps(dry_run_detail or {}, ensure_ascii=False),
                 _now()))
        row = self.get_candidate(candidate_id)
        assert row is not None       # 刚写完必在
        return row

    def get_candidate(self, candidate_id: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM test_candidates WHERE candidate_id=?",
                (candidate_id,)).fetchone()
            return self._candidate_from_row(row) if row else None
        finally:
            conn.close()

    def list_candidates(self, *, review_status: str | None = None
                        ) -> list[dict]:
        """候选列表（新在前）。`review_status=None` = 不过滤（全量）。

        生成器的去重需要「**未决集**」（`review_status='PENDING'`）——已
        ACCEPT/REJECT 的候选不该再挡住新候选（Task 3.5 的审核会消费它们）。
        """
        conn = self._connect()
        try:
            if review_status is None:
                rows = conn.execute(
                    "SELECT * FROM test_candidates ORDER BY created_at DESC,"
                    " rowid DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM test_candidates WHERE review_status=?"
                    " ORDER BY created_at DESC, rowid DESC",
                    (review_status,)).fetchall()
            return [self._candidate_from_row(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def _candidate_from_row(row: sqlite3.Row) -> dict:
        return {"candidate_id": row["candidate_id"],
                "testcase_yaml": row["testcase_yaml"],
                "generated_by": row["generated_by"],
                "generation_evidence": json.loads(
                    row["generation_evidence_json"] or "{}"),
                "fingerprint": row["fingerprint"],
                "dry_run_status": row["dry_run_status"],
                "dry_run_detail": json.loads(
                    row["dry_run_detail_json"] or "{}"),
                "review_status": row["review_status"],
                "review_note": row["review_note"],
                "reviewer": row["reviewer"],
                "reviewed_at": row["reviewed_at"],
                "created_at": row["created_at"]}

    def decide_candidate(self, candidate_id: str, *, review_status: str,
                         reviewer: str, note: str | None = None) -> None:
        """`review_status` 的**唯一写路径**（Task 3.5 / F6；`save_candidate`
        只写 `'PENDING'`，连形参都不收——review_p3_task35 P2-2）。

        reviewer / `reviewed_at`（`_now()` 盖章，单一来源）/ `review_note`
        （迁移 004）一次落齐。PENDING→X 的状态机校验在领域层
        （`candidates/review.py::decide_candidate`）——这里只管写，与
        `tracer/storage.py::decide_review:653` 的分工同款。
        """
        with self._write_tx() as conn:
            conn.execute(
                "UPDATE test_candidates SET review_status=?, reviewer=?,"
                " reviewed_at=?, review_note=? WHERE candidate_id=?",
                (review_status, reviewer, _now(), note, candidate_id))

    # --- test_candidate_rejections（迁移 002；矩阵 #7/#8 的留痕） ----------------

    def save_rejection(self, rejection_id: str, *, reason: str,
                       candidate_id: str | None = None,
                       fingerprint: str | None = None,
                       duplicate_of: str | None = None,
                       detail: dict | None = None) -> dict:
        """落一条拒绝/SKIP 留痕。**append-only**（同 id 重放 → `IntegrityError`）。

        为什么不是 upsert：留痕的语义是「生成器**试过**这些」。若某次生成里同一
        个候选被拒两次，那是两件事（比如先 lint 挡下、修好后又撞指纹），合并成
        一行会让审计看不全。`rejection_id` 由调用方生成（uuid），撞 id 是 bug，
        fail-loud 比静默覆盖好。
        """
        with self._write_tx() as conn:
            conn.execute(
                "INSERT INTO test_candidate_rejections (rejection_id,"
                " candidate_id, reason, fingerprint, duplicate_of,"
                " detail_json, created_at) VALUES (?,?,?,?,?,?,?)",
                (rejection_id, candidate_id, reason, fingerprint, duplicate_of,
                 json.dumps(detail or {}, ensure_ascii=False), _now()))
        row = self.get_rejection(rejection_id)
        assert row is not None
        return row

    def get_rejection(self, rejection_id: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM test_candidate_rejections WHERE rejection_id=?",
                (rejection_id,)).fetchone()
            return self._rejection_from_row(row) if row else None
        finally:
            conn.close()

    def list_rejections(self, *, reason: str | None = None) -> list[dict]:
        conn = self._connect()
        try:
            if reason is None:
                rows = conn.execute(
                    "SELECT * FROM test_candidate_rejections"
                    " ORDER BY created_at DESC, rowid DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM test_candidate_rejections WHERE reason=?"
                    " ORDER BY created_at DESC, rowid DESC",
                    (reason,)).fetchall()
            return [self._rejection_from_row(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def _rejection_from_row(row: sqlite3.Row) -> dict:
        return {"rejection_id": row["rejection_id"],
                "candidate_id": row["candidate_id"],
                "reason": row["reason"],
                "fingerprint": row["fingerprint"],
                "duplicate_of": row["duplicate_of"],
                "detail": json.loads(row["detail_json"] or "{}"),
                "created_at": row["created_at"]}

    # --- bug_candidates（迁移 005；Task 5.1 / P3-20） --------------------------

    def save_bug_candidate(self, candidate_id: str, *, title: str,
                           evidence: list[dict],
                           failure_type: str = "",
                           severity: str = "MEDIUM",
                           attribution_hypothesis: str = "UNTRIAGED",
                           evidence_score: int = 0,
                           reproducible: bool | None = None,
                           reproduction_count: int = 0,
                           dedup_key: str | None = None,
                           affected_tests: list[str] | None = None) -> dict:
        """落一条 Bug Candidate（upsert：`candidate_id` 是主键）。

        **幂等**：同 id 重存覆盖（与 `save_candidate` / `save_plan` 同款）——
        诊断重跑同一次失败会算出同 id 的候选，不该线性增长出重复行。
        `created_at` 只在**首次插入**时取 `_now()`。

        ⚠️ **`status` 不是本方法的形参**（与 `save_candidate` 的
        `review_status` 同款处置，review_p3_task35 P2-2）：本方法**只写
        `'DISCOVERED'`**（字面量，见下方 INSERT）。状态推进的唯一入口是
        Task 5.6 的状态机——接受 `status=` 形参会让「DISCOVERED→…→CONFIRMED」
        这条链**存在一条绕过转换校验的旁路**（能产出 `CONFIRMED` 但
        `reviewer`/`reviewed_at` 全 `NULL` 的行，看起来像已确认、实则无人确认）。
        **形参删掉而不是加断言**：删掉之后调用方传它会以 `TypeError` fail-loud。

        ⚠️ **已人工决策的候选不可覆写**（与 `save_candidate` 的 AE 闸门同款）：
        探针 AE 在 test_candidates 上实锤过「upsert 不含 review_status → 重跑
        覆写已决行」的形态；这里提前堵住——`status != 'DISCOVERED'` 的行
        经本方法重存 fail-loud，翻案走人工通道。

        `evidence` 是 `EvidenceItem` 的 `model_dump(mode="json")` 列表
        （`candidates/bug.py::BugCandidate.to_store_dict` 的产物）。
        """
        if not title.strip():
            raise ValueError("bug 候选必须有 title（审核队列里无法辨认）")
        with self._write_tx() as conn:
            existing = conn.execute(
                "SELECT status FROM bug_candidates WHERE candidate_id=?",
                (candidate_id,)).fetchone()
            if existing is not None and existing["status"] != "DISCOVERED":
                raise ValueError(
                    f"bug 候选 {candidate_id} 已推进"
                    f"（{existing['status']}）——诊断重跑不得覆写（与 "
                    f"save_candidate 的 AE 闸门同款）；翻案走人工通道")
            conn.execute(
                "INSERT INTO bug_candidates (candidate_id, title, severity,"
                " failure_type, attribution_hypothesis, evidence_json,"
                " evidence_score, reproducible, reproduction_count, dedup_key,"
                " status, affected_tests_json, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,'DISCOVERED',?,?)"
                " ON CONFLICT(candidate_id) DO UPDATE SET"
                " title=excluded.title, severity=excluded.severity,"
                " failure_type=excluded.failure_type,"
                " attribution_hypothesis=excluded.attribution_hypothesis,"
                " evidence_json=excluded.evidence_json,"
                " evidence_score=excluded.evidence_score,"
                " reproducible=excluded.reproducible,"
                " reproduction_count=excluded.reproduction_count,"
                " dedup_key=excluded.dedup_key,"
                " affected_tests_json=excluded.affected_tests_json",
                (candidate_id, title, severity, failure_type,
                 attribution_hypothesis,
                 json.dumps(evidence, ensure_ascii=False), evidence_score,
                 None if reproducible is None else int(reproducible),
                 reproduction_count, dedup_key,
                 json.dumps(affected_tests or [], ensure_ascii=False), _now()))
        row = self.get_bug_candidate(candidate_id)
        assert row is not None       # 刚写完必在
        return row

    def get_bug_candidate(self, candidate_id: str) -> dict | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM bug_candidates WHERE candidate_id=?",
                (candidate_id,)).fetchone()
            return self._bug_from_row(row) if row else None
        finally:
            conn.close()

    def list_bug_candidates(self, *, status: str | None = None,
                            dedup_key: str | None = None) -> list[dict]:
        """Bug 候选列表（新在前）。两个过滤键都可空（None = 不过滤）。

        `status`：审核队列按状态取（Task 5.6 的 `HUMAN_REVIEW` 队列）。
        `dedup_key`：去重合并查同根因的既有行（Task 5.5 / F8）。
        """
        conn = self._connect()
        try:
            where, params = [], []
            if status is not None:
                where.append("status=?")
                params.append(status)
            if dedup_key is not None:
                where.append("dedup_key=?")
                params.append(dedup_key)
            sql = "SELECT * FROM bug_candidates"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY created_at DESC, rowid DESC"
            return [self._bug_from_row(r)
                    for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    @staticmethod
    def _bug_from_row(row: sqlite3.Row) -> dict:
        return {"candidate_id": row["candidate_id"],
                "title": row["title"],
                "severity": row["severity"],
                "failure_type": row["failure_type"],
                "attribution_hypothesis": row["attribution_hypothesis"],
                "evidence": json.loads(row["evidence_json"] or "[]"),
                "evidence_score": row["evidence_score"],
                # ⚠️ 三态列：`None` 是「还没试过复现」，**不是** False
                # （`candidates/bug.py::from_store_dict` 同款处置）
                "reproducible": (None if row["reproducible"] is None
                                 else bool(row["reproducible"])),
                "reproduction_count": row["reproduction_count"],
                "dedup_key": row["dedup_key"],
                "status": row["status"],
                "affected_tests": json.loads(
                    row["affected_tests_json"] or "[]"),
                "reviewer": row["reviewer"],
                "review_decision": row["review_decision"],
                "reviewed_at": row["reviewed_at"],
                "created_at": row["created_at"]}
