"""store.py — ExperienceStore（设计 7.1 接口 + E9 单写者，P2-03 / Task 2.1）。

两个实现、一个过渡现实：

  - `ExperienceStore`（Protocol）：P2 设计 7.1 的接口真身——`lookup` 按
    (app_id, screen_id, target_id) 返回 `Experience` 列表。M2 消费方
    （Runtime Guard / recovery 接线 / CLI）只认这个。
  - `SQLiteExperienceStore`：SQLite 实现。**单写者（E9）**：进程内
    `threading.Lock` 串行化全部写方法 + `BEGIN IMMEDIATE` / `busy_timeout`
    落到 SQLite 层（跨进程也串行）；Reader（lookup/get_runs/list）每次
    调用独立连接，可并发。约定（设计 7.2）：会产生写操作的套件 CI 中
    **串行执行**，或统一经过写入队列——本类不替 CI 做调度。
  - `EmptyExperienceStore`：P1 占位（旧签名 `lookup(app_build, screen,
    target_id) -> list[dict]` 恒返回 []）。**过渡保留**：P1 引擎的调用
    形态与 P2 接口不同，Task 2.4 引擎接真 Store 时统一并退役本类。

追溯与保留纪律：
  - E5：`create_candidate` 只收 `CandidateSeed`（三件套模型层 + Store
    写入前双重校验——Task 1.1 审计的 42 条悬空数据两道闸都过不去）；
  - E12 / 设计 11.2：runs **不物理删除**——DEGRADED/REJECTED 后至少
    额外保留 180 天，本类不提供任何 delete 路径；
  - `validated_builds` 只在 `record_success_build()` 追加（E7 集合语义；
    record_run 不触碰它——静态校验路径不产生 build 验证事实）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

from experience.models import (
    CandidateSeed,
    Experience,
    ExperienceRun,
    ExperienceStatus,
)
from experience.schema_migrations import migrate

__all__ = ["ExperienceStore", "SQLiteExperienceStore", "EmptyExperienceStore"]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@runtime_checkable
class ExperienceStore(Protocol):
    """设计 7.1 接口。实现者：SQLiteExperienceStore（P2 唯一真实现）。"""

    def lookup(self, app_id: str, screen_id: str,
               target_id: str) -> list[Experience]: ...

    def create_candidate(self, seed: CandidateSeed) -> Experience: ...

    def find_by_seed_review(self, review_id: int) -> list[Experience]: ...

    def record_run(self, experience_id: str, run: ExperienceRun) -> None: ...

    def record_success_build(self, experience_id: str, app_build: str) -> None: ...

    def get_runs(self, experience_id: str,
                 limit: int | None = None) -> list[ExperienceRun]: ...

    def update_status(self, experience_id: str,
                      new_status: ExperienceStatus, reason: str,
                      *, operator: str = "system",
                      run_id: str | None = None,
                      app_build: str | None = None) -> None: ...

    def list(self, status: ExperienceStatus | None = None) -> list[Experience]: ...


class EmptyExperienceStore:
    """P1 占位（恒返回 []）——旧签名，Task 2.4 引擎接真 Store 后退役。"""

    def lookup(self, app_build: str, screen: str,
               target_id: str) -> list[dict]:
        return []


class SQLiteExperienceStore:
    """E9 单写者落地（设计 7.2）：写 = 进程锁 + BEGIN IMMEDIATE；
    读 = 每调用独立连接，可并发。"""

    def __init__(self, db_path: Path | str = "out/experience.db"):
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # 单写者（E9）：进程内串行化全部写方法。SQLite 层再叠
        # BEGIN IMMEDIATE + busy_timeout（跨进程串行 + 忙等待）。
        self._write_lock = threading.Lock()
        conn = self._connect()
        try:
            migrate(conn)
        finally:
            conn.close()

    # --- 连接 ---

    def _connect(self) -> sqlite3.Connection:
        # busy_timeout 单点 5s（review P3-2：connect timeout=30 与 PRAGMA
        # 5000 曾意图不一致，后设者胜靠阅读顺序）
        conn = sqlite3.connect(self._path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def _write_tx(self):
        """写方法专用：进程锁内 BEGIN IMMEDIATE，退出时 commit（成功）/
        ROLLBACK（异常）并**必然** close + release——任何早退路径都不得
        泄漏事务或锁（曾致「database is locked」连坐后续操作）。"""
        self._write_lock.acquire()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            conn.close()
            self._write_lock.release()

    # --- 行 ↔ 模型 ---

    @staticmethod
    def _exp_from_row(row: sqlite3.Row) -> Experience:
        from repository.loader import LocatorStrategy
        return Experience(
            experience_id=row["experience_id"],
            app_id=row["app_id"],
            screen_id=row["screen_id"],
            target_id=row["target_id"],
            strategy=LocatorStrategy.model_validate_json(row["strategy_json"]),
            origin=row["origin"],
            status=ExperienceStatus(row["status"]),
            sample_count=row["sample_count"],
            success_count=row["success_count"],
            failure_count=row["failure_count"],
            success_rate=row["success_rate"],
            validated_builds=json.loads(row["validated_builds_json"]
                                        or "[]"),
            last_screen_fingerprint=row["last_screen_fingerprint"],
            seed_run_id=row["seed_run_id"],
            seed_step_id=row["seed_step_id"],
            seed_recovery_review_id=row["seed_recovery_review_id"],
            promoted=bool(row["promoted"]),
            promoted_commit=row["promoted_commit"],
            created_at=datetime.fromisoformat(
                row["created_at"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(
                row["updated_at"].replace("Z", "+00:00")),
        )

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> ExperienceRun:
        return ExperienceRun(
            experience_id=row["experience_id"],
            run_id=row["run_id"],
            step_id=row["step_id"],
            app_build=row["app_build"],
            screen_fingerprint=row["screen_fingerprint"],
            result=row["result"],
            guard_reason=row["guard_reason"],
            effective_risk=row["effective_risk"],
            uniqueness_count=row["uniqueness_count"],
            element_type_match=(None if row["element_type_match"] is None
                                else bool(row["element_type_match"])),
            latency_ms=row["latency_ms"],
            created_at=datetime.fromisoformat(
                row["created_at"].replace("Z", "+00:00")),
        )

    # --- 读（可并发） ---

    def lookup(self, app_id: str, screen_id: str,
               target_id: str) -> list[Experience]:
        """设计 7.1。**REJECTED 不返回**：人工判定「不可用」的策略不该再
        被消费路径看见（REJECTED 行仍在库、list() 可见——审计不丢）。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM experiences WHERE app_id=? AND screen_id=?"
                " AND target_id=? AND status != 'REJECTED'"
                " ORDER BY updated_at DESC, rowid DESC",
                (app_id, screen_id, target_id)).fetchall()
            return [self._exp_from_row(r) for r in rows]
        finally:
            conn.close()

    def find_by_seed_review(self, review_id: int) -> list[Experience]:
        """按**种子 review** 直查（含 REJECTED）——E5 幂等判据的专用查询。

        为什么不能用 `lookup` 兼任（review_p2_task23 P3-3）：`lookup` 有意
        不返回 REJECTED（设计 7.1 修订），于是「同 review 已种过」会退化成
        「恰好还能读见」的涌现属性——置 REJECTED 后再 seed 就会静默多出一行。
        这里**直查不过滤**，把「REJECTED 是否算已种」的语义判断交还消费方
        （`agent.review` 显式排除 REJECTED：人工判过不可用的策略再 ACCEPT
        一次，本就该重新走一遍学习，不算重复）。
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM experiences WHERE seed_recovery_review_id=?"
                " ORDER BY created_at ASC, rowid ASC", (review_id,)).fetchall()
            return [self._exp_from_row(r) for r in rows]
        finally:
            conn.close()

    def get_runs(self, experience_id: str,
                 limit: int | None = None) -> list[ExperienceRun]:
        """按时间升序返回（Verifier 的 runs[-window:] 语义依赖时序）；
        limit 取**最近** N 条。"""
        conn = self._connect()
        try:
            if limit is not None:
                rows = conn.execute(
                    "SELECT * FROM experience_runs WHERE experience_id=?"
                    " ORDER BY id DESC LIMIT ?",
                    (experience_id, limit)).fetchall()
                rows = list(reversed(rows))
            else:
                rows = conn.execute(
                    "SELECT * FROM experience_runs WHERE experience_id=?"
                    " ORDER BY id ASC", (experience_id,)).fetchall()
            return [self._run_from_row(r) for r in rows]
        finally:
            conn.close()

    def list(self, status: ExperienceStatus | None = None) -> list[Experience]:
        conn = self._connect()
        try:
            if status is None:
                rows = conn.execute(
                    "SELECT * FROM experiences ORDER BY updated_at DESC,"
                    " rowid DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM experiences WHERE status=?"
                    " ORDER BY updated_at DESC, rowid DESC",
                    (status.value,)).fetchall()
            return [self._exp_from_row(r) for r in rows]
        finally:
            conn.close()

    # --- 写（E9 单写者） ---

    def create_candidate(self, seed: CandidateSeed) -> Experience:
        """ACCEPT 种子 → CANDIDATE。E5 双重校验（模型已拦，写入前再验
        ——审计 42 条悬空数据两道闸都过不去）。同 (app,screen,target)
        的多次 ACCEPT 各自成行（不同候选策略并存，Guard 排序裁决）。"""
        if not (seed.seed_run_id and seed.seed_step_id
                and seed.seed_recovery_review_id):
            raise ValueError(
                "create_candidate rejected: incomplete seed triplet (E5)")
        experience_id = f"exp_{uuid.uuid4().hex[:12]}"
        now = _now()
        with self._write_tx() as conn:
            conn.execute(
                "INSERT INTO experiences (experience_id, app_id, screen_id,"
                " target_id, strategy_json, origin, status, sample_count,"
                " success_count, failure_count, success_rate,"
                " validated_builds_json, last_screen_fingerprint,"
                " seed_run_id, seed_step_id, seed_recovery_review_id,"
                " promoted, promoted_commit, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,'CANDIDATE',0,0,0,0,'[]',NULL,?,?,?,0,NULL,?,?)",
                (experience_id, seed.app_id, seed.screen_id, seed.target_id,
                 seed.strategy.model_dump_json(), "LLM_ACCEPTED_RECOVERY",
                 seed.seed_run_id, seed.seed_step_id,
                 seed.seed_recovery_review_id, now, now))
            # 状态时间线首行（4.3：CANDIDATE 是生命周期的起点，也要留痕）
            conn.execute(
                "INSERT INTO experience_state_events (experience_id,"
                " from_status, to_status, reason, run_id, app_build,"
                " operator, created_at) VALUES (?,NULL,'CANDIDATE',"
                "'SEEDED',?,?,'system',?)",
                (experience_id, seed.seed_run_id, seed.app_build, now))
        return Experience(
            experience_id=experience_id, app_id=seed.app_id,
            screen_id=seed.screen_id, target_id=seed.target_id,
            strategy=seed.strategy, origin="LLM_ACCEPTED_RECOVERY",
            status=ExperienceStatus.CANDIDATE,
            seed_run_id=seed.seed_run_id, seed_step_id=seed.seed_step_id,
            seed_recovery_review_id=seed.seed_recovery_review_id,
            created_at=datetime.fromisoformat(now.replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(now.replace("Z", "+00:00")))

    def record_run(self, experience_id: str, run: ExperienceRun) -> None:
        """追加一次「实际被尝试」（E11 口径由调用方保证），并原子推进
        Experience 统计。**不触碰 validated_builds**（E7：只在
        record_success_build 追加）。"""
        # review P3-4：两处 experience_id 不一致 = 调用方 bug，显形而非
        # 静默以参数为准
        if run.experience_id not in (None, experience_id):
            raise ValueError(
                f"run.experience_id ({run.experience_id!r}) != 参数"
                f" experience_id ({experience_id!r})")
        with self._write_tx() as conn:
            row = conn.execute(
                "SELECT sample_count, success_count, failure_count"
                " FROM experiences WHERE experience_id=?",
                (experience_id,)).fetchone()
            if row is None:
                raise LookupError(f"experience {experience_id!r} 不存在")
            now = _now()
            conn.execute(
                "INSERT INTO experience_runs (experience_id, run_id,"
                " step_id, app_build, screen_fingerprint, result,"
                " guard_reason, effective_risk, uniqueness_count,"
                " element_type_match, latency_ms, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (experience_id, run.run_id, run.step_id, run.app_build,
                 run.screen_fingerprint, run.result, run.guard_reason,
                 run.effective_risk, run.uniqueness_count,
                 None if run.element_type_match is None
                 else int(run.element_type_match),
                 run.latency_ms, now))
            success = 1 if run.result == "SUCCESS" else 0
            sample = row["sample_count"] + 1
            succ = row["success_count"] + success
            fail = row["failure_count"] + (1 - success)
            conn.execute(
                "UPDATE experiences SET sample_count=?, success_count=?,"
                " failure_count=?, success_rate=?, updated_at=?"
                " WHERE experience_id=?",
                (sample, succ, fail, succ / sample, now, experience_id))

    def record_success_build(self, experience_id: str, app_build: str) -> None:
        """E7：validated_builds 集合语义的唯一追加入口（静态校验路径
        不触碰它）。重复 build 无操作（不报错——幂等）。"""
        with self._write_tx() as conn:
            row = conn.execute(
                "SELECT validated_builds_json FROM experiences"
                " WHERE experience_id=?", (experience_id,)).fetchone()
            if row is None:
                raise LookupError(f"experience {experience_id!r} 不存在")
            builds = json.loads(row["validated_builds_json"] or "[]")
            if app_build not in builds:
                builds.append(app_build)
                conn.execute(
                    "UPDATE experiences SET validated_builds_json=?,"
                    " updated_at=? WHERE experience_id=?",
                    (json.dumps(builds), _now(), experience_id))
            # 集合语义：已存在 → 事务内无写操作，commit 空事务即可

    def update_status(self, experience_id: str,
                      new_status: ExperienceStatus, reason: str,
                      *, operator: str = "system",
                      run_id: str | None = None,
                      app_build: str | None = None) -> None:
        """状态跳变 + experience_state_events 留痕（from/to/reason/
        operator）。同状态为 no-op（Verifier 的 NO_CHANGE 不该产生事件）。
        **runs 不物理删除**（设计 11.2：DEGRADED/REJECTED 后至少额外
        保留 180 天）——本类没有任何 delete 路径。"""
        with self._write_tx() as conn:
            row = conn.execute(
                "SELECT status FROM experiences WHERE experience_id=?",
                (experience_id,)).fetchone()
            if row is None:
                raise LookupError(f"experience {experience_id!r} 不存在")
            old = ExperienceStatus(row["status"])
            if new_status == old:
                return              # 同状态 no-op：Verifier 的 NO_CHANGE
            now = _now()
            conn.execute(
                "INSERT INTO experience_state_events (experience_id,"
                " from_status, to_status, reason, run_id, app_build,"
                " operator, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (experience_id, old.value, new_status.value, reason,
                 run_id, app_build, operator, now))
            conn.execute(
                "UPDATE experiences SET status=?, updated_at=?"
                " WHERE experience_id=?",
                (new_status.value, now, experience_id))
