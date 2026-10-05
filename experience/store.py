"""store.py — ExperienceStore（设计 7.1 接口 + E9 单写者，P2-03 / Task 2.1）。

一个接口、一个真实现：

  - `ExperienceStore`（Protocol）：P2 设计 7.1 的接口真身——`lookup` 按
    (app_id, screen_id, target_id) 返回 `Experience` 列表。消费方
    （Runtime Guard / recovery 接线 / CLI）只认这个。
  - `SQLiteExperienceStore`：SQLite 实现。**单写者（E9）**：进程内
    `threading.Lock` 串行化全部写方法 + `BEGIN IMMEDIATE` / `busy_timeout`
    落到 SQLite 层（跨进程也串行）；Reader（lookup/get_runs/list）每次
    调用独立连接，可并发。约定（设计 7.2）：会产生写操作的套件 CI 中
    **串行执行**，或统一经过写入队列——本类不替 CI 做调度。

> **Task 2.4 过渡债清账**：P1 的 `EmptyExperienceStore`（旧签名
> `lookup(app_build, screen, target_id) -> list[dict]` 恒返回 []）**已退役**。
> 旧签名把 `app_build`（哪一次构建）当 `app_id`（哪个 App）用——两个键
> 语义不同，正是 Task 2.4 要清的地雷。空库的语义改由真 Store 承担
> （`lookup` 天然返回 `[]`），"没有 Store" 由 `RecoveryEngine` 的
> `experience_store=None` 显式表达。

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
    PromotionProposal,
    StateEvent,
)
from experience.schema_migrations import migrate

__all__ = ["ExperienceStore", "SQLiteExperienceStore", "record_sample_runs"]


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

    def record_screen_fingerprint(self, experience_id: str,
                                  fingerprint: str) -> None: ...

    def record_state_event(self, experience_id: str, reason: str, *,
                           run_id: str | None = None,
                           app_build: str | None = None,
                           operator: str = "system") -> None: ...

    def has_state_event_since_transition(self, experience_id: str,
                                         reason: str) -> bool: ...

    def get_runs(self, experience_id: str,
                 limit: int | None = None) -> list[ExperienceRun]: ...

    def update_status(self, experience_id: str,
                      new_status: ExperienceStatus, reason: str,
                      *, operator: str = "system",
                      run_id: str | None = None,
                      app_build: str | None = None) -> None: ...

    def list(self, status: ExperienceStatus | None = None) -> list[Experience]: ...

    def get_experience(self, experience_id: str) -> Experience | None: ...

    def get_state_events(self, experience_id: str) -> list[StateEvent]: ...

    def create_promotion_proposal(self, proposal: PromotionProposal) -> PromotionProposal: ...

    def get_promotion_proposal(self, proposal_id: str) -> PromotionProposal | None: ...

    def list_promotion_proposals(self, status: str | None = None) -> list[PromotionProposal]: ...

    def set_promotion_proposal_status(self, proposal_id: str, status: str, *,
                                      git_commit: str | None = None,
                                      reviewer: str | None = None,
                                      decision_note: str | None = None) -> None: ...

    def record_promotion(self, experience_id: str, commit: str) -> None: ...


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

    def get_experience(self, experience_id: str) -> Experience | None:
        """按 id 直查（含 REJECTED——`mta experience show/revalidate` 是
        审计/操作视角，REJECTED 行必须可见；消费视角的 lookup 仍排除）。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM experiences WHERE experience_id=?",
                (experience_id,)).fetchone()
            return self._exp_from_row(row) if row else None
        finally:
            conn.close()

    def get_state_events(self, experience_id: str) -> list[StateEvent]:
        """状态时间线（4.3：状态可复算，不靠当前值反推）。按 id 升序。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM experience_state_events WHERE experience_id=?"
                " ORDER BY id ASC", (experience_id,)).fetchall()
            return [StateEvent(
                experience_id=r["experience_id"],
                from_status=(None if r["from_status"] is None
                             else ExperienceStatus(r["from_status"])),
                to_status=ExperienceStatus(r["to_status"]),
                reason=r["reason"] or "",
                run_id=r["run_id"],
                app_build=r["app_build"],
                operator=r["operator"] or "system",
                created_at=datetime.fromisoformat(
                    r["created_at"].replace("Z", "+00:00")),
            ) for r in rows]
        finally:
            conn.close()

    # --- Promotion Proposal（9.3；表在 002 迁移已建） ---

    @staticmethod
    def _proposal_from_row(row: sqlite3.Row) -> PromotionProposal:
        return PromotionProposal(
            proposal_id=row["proposal_id"],
            experience_id=row["experience_id"],
            diff=row["diff_text"],
            evidence_summary=row["evidence_summary_json"] or "{}",
            status=row["status"],
            manual_override_of_auto_policy=bool(
                row["manual_override_of_auto_policy"]),
            reviewer=row["reviewer"],
            decision_note=row["decision_note"],
            resolved_commit=row["git_commit"],
            created_at=datetime.fromisoformat(
                row["created_at"].replace("Z", "+00:00")),
            decided_at=(datetime.fromisoformat(row["decided_at"]
                                               .replace("Z", "+00:00"))
                        if row["decided_at"] else None),
        )

    def create_promotion_proposal(
            self, proposal: PromotionProposal) -> PromotionProposal:
        now = _now()
        with self._write_tx() as conn:
            conn.execute(
                "INSERT INTO promotion_proposals (proposal_id,"
                " experience_id, diff_text, evidence_summary_json,"
                " manual_override_of_auto_policy, status, reviewer,"
                " decision_note, git_commit, created_at, decided_at)"
                " VALUES (?,?,?,?,?,?,?,?,NULL,?,NULL)",
                (proposal.proposal_id, proposal.experience_id,
                 proposal.diff, proposal.evidence_summary,
                 1 if proposal.manual_override_of_auto_policy else 0,
                 proposal.status, proposal.reviewer,
                 proposal.decision_note, now))
        return proposal

    def get_promotion_proposal(
            self, proposal_id: str) -> PromotionProposal | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM promotion_proposals WHERE proposal_id=?",
                (proposal_id,)).fetchone()
            return self._proposal_from_row(row) if row else None
        finally:
            conn.close()

    def list_promotion_proposals(
            self, status: str | None = None) -> list[PromotionProposal]:
        conn = self._connect()
        try:
            if status is None:
                rows = conn.execute(
                    "SELECT * FROM promotion_proposals ORDER BY created_at"
                    " DESC, rowid DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM promotion_proposals WHERE status=?"
                    " ORDER BY created_at DESC, rowid DESC",
                    (status,)).fetchall()
            return [self._proposal_from_row(r) for r in rows]
        finally:
            conn.close()

    def set_promotion_proposal_status(
            self, proposal_id: str, status: str, *,
            git_commit: str | None = None,
            reviewer: str | None = None,
            decision_note: str | None = None) -> None:
        with self._write_tx() as conn:
            cur = conn.execute(
                "UPDATE promotion_proposals SET status=?, git_commit=?,"
                " reviewer=COALESCE(?, reviewer),"
                " decision_note=COALESCE(?, decision_note), decided_at=?"
                " WHERE proposal_id=?",
                (status, git_commit, reviewer, decision_note, _now(),
                 proposal_id))
            if cur.rowcount == 0:
                raise ValueError(
                    f"no such promotion proposal: {proposal_id}")

    def record_promotion(self, experience_id: str, commit: str) -> None:
        """9.4：promoted / promoted_commit 记账（**不改 status**——两条
        独立时间线）。留一条 from==to 事件（REVALIDATION_REQUIRED 同款
        「不改状态但可审计」的表达；to_status 仍是合法状态值，
        reason 才是 PROMOTED）。"""
        with self._write_tx() as conn:
            cur = conn.execute(
                "UPDATE experiences SET promoted=1, promoted_commit=?,"
                " updated_at=? WHERE experience_id=?",
                (commit, _now(), experience_id))
            if cur.rowcount == 0:
                raise ValueError(f"no such experience: {experience_id}")
            status = conn.execute(
                "SELECT status FROM experiences WHERE experience_id=?",
                (experience_id,)).fetchone()["status"]
            conn.execute(
                "INSERT INTO experience_state_events (experience_id,"
                " from_status, to_status, reason, run_id, app_build,"
                " operator, created_at) VALUES (?,?,?,?,NULL,NULL,?,?)",
                (experience_id, status, status, "PROMOTED", "promoter",
                 _now()))

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

    def record_screen_fingerprint(self, experience_id: str,
                                  fingerprint: str) -> None:
        """E8：更新 fingerprint **观测记录**（重验证通过后调用）。

        只改观测列，不动 `status`、不动 runs——E8 明文「不清空或拒绝该
        Experience」。指纹是**观测**不是判据：判据（`needs_revalidation`）
        每次由当前观测与它现比，所以这里没有「过期」概念。
        """
        with self._write_tx() as conn:
            row = conn.execute(
                "SELECT experience_id FROM experiences WHERE experience_id=?",
                (experience_id,)).fetchone()
            if row is None:
                raise LookupError(f"experience {experience_id!r} 不存在")
            conn.execute(
                "UPDATE experiences SET last_screen_fingerprint=?,"
                " updated_at=? WHERE experience_id=?",
                (fingerprint, _now(), experience_id))

    def record_state_event(self, experience_id: str, reason: str, *,
                           run_id: str | None = None,
                           app_build: str | None = None,
                           operator: str = "system") -> None:
        """**非跳变**的时间线标记（E8 的 `REVALIDATION_REQUIRED`）。

        为什么 from_status == to_status：schema 的 `to_status` 是 NOT NULL
        （「跳变必须有终态」），而这类标记本身没有终态——同值表达「此刻仍是
        这个状态，只是被标记了」，比放宽 NOT NULL 更保守（不改 schema 就
        不破坏既有的「跳变必有终态」不变量）。
        """
        with self._write_tx() as conn:
            row = conn.execute(
                "SELECT status FROM experiences WHERE experience_id=?",
                (experience_id,)).fetchone()
            if row is None:
                raise LookupError(f"experience {experience_id!r} 不存在")
            status = row["status"]
            conn.execute(
                "INSERT INTO experience_state_events (experience_id,"
                " from_status, to_status, reason, run_id, app_build,"
                " operator, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (experience_id, status, status, reason, run_id, app_build,
                 operator, _now()))

    def has_state_event_since_transition(self, experience_id: str,
                                         reason: str) -> bool:
        """**最近一次跳变之后**是否已有该 reason 的事件（标记幂等用）。

        「跳变」= `from_status IS NULL OR from_status != to_status`（首条事件
        from 为 NULL）。以最近一次跳变为界，而不是「历史上有没有过」：状态
        变化之后旧标记就作废了——例如 DEGRADED→VERIFIED 之后又变了指纹，
        必须能重新标记。
        """
        conn = self._connect()
        try:
            last_transition = conn.execute(
                "SELECT MAX(id) FROM experience_state_events"
                " WHERE experience_id=? AND (from_status IS NULL"
                " OR from_status != to_status)",
                (experience_id,)).fetchone()[0] or 0
            found = conn.execute(
                "SELECT 1 FROM experience_state_events WHERE experience_id=?"
                " AND id > ? AND reason=? LIMIT 1",
                (experience_id, last_transition, reason)).fetchone()
            return found is not None
        finally:
            conn.close()

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


# --- 4.7 样本落库（引擎决定写什么，trace 写入方补 steps.id） --------------

def record_sample_runs(store: ExperienceStore, samples: list[dict], *,
                       step_id: int) -> int:
    """把引擎产出的 4.7 样本 payload 落库，返回写入行数。

    **为什么 step_id 不在 payload 里**（Task 2.4 决策记录）：恢复发生在
    `Lifecycle.record_step()` **之前**——那一刻 `steps.id` 尚未分配
    （AUTOINCREMENT，只有 INSERT 之后才有值）。可选的替代是拿 YAML 的
    `step_index` 填进 `step_id`，但那是两个 id 空间互相冒充（本项目在
    「build ≠ bundle」上吃过同款亏），所以拆成两步：引擎产出 payload
    （它掌握 4.7 判定与运行期观测），trace 写入方在 `record_step()` 之后
    补上真 id 再调本函数。

    `result == "SUCCESS"` 时同时 `record_success_build`（设计 4.7 末行 /
    E7：validated_builds 只由成功样本追加）。

    **待定样本必须先回填**（review_p2_task24_final P3-2）：aux 命中的样本由
    引擎产出时 `result=None`（执行结果引擎侧不可观测），要等调用方观测后经
    `agent.recovery.resolve_deferred_sample` 回填。漏了这一步会一路带到
    `ExperienceRun` 的 `Literal["SUCCESS","FAILURE"]` 校验才炸，报错指向
    「result 类型不对」——**看不出是「忘了回填」**。这里前置指名报错，把
    「契约违反」与「数据脏」分开。
    """
    for s in samples:
        exp_id = s["experience_id"]
        if s.get("result") is None:
            raise ValueError(
                f"deferred sample 未回填（experience_id={exp_id!r}）——"
                "aux 命中的待定样本（result=None / pending_observation）"
                "必须先经 agent.recovery.resolve_deferred_sample 回填"
                "观测结果再落库（review_p2_task24_final P3-2）")
        store.record_run(exp_id, ExperienceRun(
            experience_id=exp_id,
            run_id=s["run_id"],
            step_id=step_id,
            app_build=s["app_build"],
            screen_fingerprint=s.get("screen_fingerprint"),
            result=s["result"],
            guard_reason=s.get("guard_reason"),
            effective_risk=s.get("effective_risk"),
            uniqueness_count=s.get("uniqueness_count"),
            element_type_match=s.get("element_type_match"),
            latency_ms=s.get("latency_ms")))
        if s["result"] == "SUCCESS":
            store.record_success_build(exp_id, s["app_build"])
    return len(samples)
