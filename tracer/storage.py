"""Trace schema 0.1 —— 迁移与存储接口（设计 14.2，P1-07 / Task 2.4）。

三块：
  1. `migrate(conn)`：无 schema_migrations 的 P0 库 → 0.1（数据保留），
     全新库 → 直建 migrations/001；重复调用幂等。
  2. 纯函数：`aggregate_status`（8.1 终态优先级）、`pass_rate`/`recovery_rate`
     （8.3 通过率口径）。
  3. `TraceStore`：写入接口。**Redactor 前置不变**（H8 / 14.4）——所有
     detail/locator 一律先 redact 再写，不存在先写后脱敏的路径。

关于目录名：plan 写 `trace/`，但本地包名 `trace` 与 Python stdlib 冲突
（P0 已因此改名 `tracer/`），本模块沿用 `tracer/`，不重蹈。

关于 legacy 列：P0 的 `runs.test_case` / `steps.run_id` / `recoveries.strategy`
等旧列在 0.1 中不再有对应位置，但**保留不删**——P0 脚本（run_phase0_demo、
verify_stage*）还在读它们，M2 Gate 后才退役。现在删会打断那些脚本。
"""

from __future__ import annotations

import calendar
import json
import sqlite3
import time
from pathlib import Path

from tracer.redactor import redact

SCHEMA_VERSION = "0.1"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# --- 8.1 / 8.2 枚举（真源在此；DDL 注释里的清单与此处一致，有测试钉住） ---

TESTCASE_STATUSES = {
    "PASS", "RECOVERED", "FAIL", "INFRA_FAILURE", "ENVIRONMENT_FAILURE",
    "BLOCKED", "ABORTED", "SKIPPED",
}
# 尝试态：不是终态，不参与聚合（否则运行中的步骤会把已定 PASS 拉低）
ATTEMPT_STATUSES = {"PENDING", "RUNNING"}

STEP_STATUSES = {
    "PENDING", "RUNNING", "SUCCESS", "FAILED", "RECOVERED", "SKIPPED",
}

FAILURE_TYPES = {
    "ELEMENT_NOT_FOUND", "AMBIGUOUS_ELEMENT", "WAIT_TIMEOUT",
    "ASSERTION_VALUE_MISMATCH", "ACTION_OUTCOME_UNKNOWN",
    "CURRENT_SCREEN_UNKNOWN", "SCREEN_AMBIGUOUS", "APP_CRASH", "WDA_FAILURE",
    "APPIUM_FAILURE", "DEVICE_UNAVAILABLE", "ENV_RESET_FAILED",
    "CLEANUP_FAILED", "UNSUPPORTED_RESET", "SECRET_NOT_FOUND",
    "BUILD_METADATA_MISMATCH", "SECURITY_BLOCKED", "LLM_DISABLED",
    "LLM_BUDGET_EXCEEDED", "LLM_TIMEOUT", "LLM_PROVIDER_ERROR",
    "LLM_INVALID_OUTPUT", "LLM_LOW_CONFIDENCE", "LLM_TARGET_NOT_FOUND",
    "LLM_TARGET_AMBIGUOUS", "LLM_TARGET_TYPE_MISMATCH",
    "LLM_TARGET_SCREEN_MISMATCH", "LLM_RISK_BLOCKED",
    "LLM_REDISPATCH_FAILED",
}

ATTRIBUTIONS = {
    "UNTRIAGED", "APP_DEFECT", "AUTOMATION_DEFECT", "ENVIRONMENT_DEFECT",
    "TEST_DATA_DEFECT", "INFRASTRUCTURE_DEFECT",
}

FAILURE_PHASES = {"PRE_DISPATCH", "POST_DISPATCH"}

RECOVERY_KINDS = {"LLM", "POSTCONDITION", "SETTLE_RETRY", "RUN_MEMO",
                  "DETERMINISTIC_CANDIDATE"}

REVIEW_STATUSES = {"PENDING", "ACCEPT", "REJECT"}

INFRA_EVENT_TYPES = {
    "WDA_DEAD", "WDA_RESTARTED", "APPIUM_SESSION_RECREATED",
    "DEVICE_UNAVAILABLE",
}

# 设计 11.1 的 Experience 事件全集（P2 追加到 P1 Trace）。**不在本集合的
# 事件名一律拒绝**——与 RECOVERY_KINDS / REVIEW_STATUSES 同款 fail-loud：
# 事件名打错必须当场报错，不能静默写进一个查不到的孤儿行。
EXPERIENCE_EVENT_TYPES = {
    "experience_lookup", "experience_hit", "experience_miss",
    "experience_guard_block", "experience_execution",
    "candidate_created", "candidate_verified",
    "experience_degraded", "experience_rejected", "experience_revalidated",
    "promotion_proposed", "promotion_approved",
}

# E5 种子解析（设计 8.1）：recovery_reviews → recoveries → steps →
# testcase_runs → runs 的 join 是「这条 ACCEPT 是否真的可追溯」的唯一事实
# 来源。审计脚本（scripts/p2_audit_recoveries.py 的 seedable_accepts，Task
# 1.1）与本模块 get_review_seed 共用本片段——同一 join 两处维护必然漂移
# （P2-04 的「两套校验合一」同款纪律）。
SEED_SELECT = (
    "SELECT rr.id AS seed_recovery_review_id,"
    " rr.review_status, rr.reviewer, rr.reviewed_at, rr.note,"
    " rec.id AS recovery_id, rec.kind,"
    " rec.step_id AS seed_step_id,"
    " tc.run_id AS seed_run_id, tc.id AS seed_tc_run_id,"
    " ru.app_bundle_id AS app_id,"
    " rec.screen, rec.expected_target AS target_id,"
    " rec.candidate_target, rec.candidate_type, rec.app_build"
    " FROM recovery_reviews rr"
    " JOIN recoveries rec ON rec.id = rr.recovery_id"
    " LEFT JOIN steps s ON s.id = rec.step_id"
    " LEFT JOIN testcase_runs tc ON tc.id = s.testcase_run_id"
    " LEFT JOIN runs ru ON ru.run_id = tc.run_id"
)

# 8.1 终态优先级（高→低）。注意这条链只覆盖 8.1 明列的六档；
# ABORTED / SKIPPED 不在其中（8.1 没给它们的相对顺序），单列一条兜底链。
_STATUS_PRIORITY = [
    "ENVIRONMENT_FAILURE", "INFRA_FAILURE", "BLOCKED", "FAIL",
    "RECOVERED", "PASS",
]
# R14-5：ABORTED（用例跑挂被中止）比 SKIPPED（没跑）严重。旧实现对这两个
# 走 `return seen[0]`，结果取决于输入顺序——同一组状态换个顺序聚合出不同
# 终态，Report 会自相矛盾。这里显式定序，不再依赖调用方传参顺序。
_FALLBACK_PRIORITY = ["ABORTED", "SKIPPED"]


class TestcaseStatusError(ValueError):
    """未知/非法状态。fail-loud：新状态漏统计时必须报错而非静默当 PASS。"""


# --- 8.1 / 8.3 纯函数 ---


def aggregate_status(statuses) -> str | None:
    """多条记录聚合成一个 testcase 最终状态（8.1）。

    只认终态；非终态（PENDING/RUNNING）忽略；空输入 → None。
    未知状态 → TestcaseStatusError（不静默）。
    """
    seen = []
    for s in statuses:
        if s is None:
            continue
        if s in ATTEMPT_STATUSES:
            continue
        if s not in TESTCASE_STATUSES:
            raise TestcaseStatusError(f"unknown testcase status: {s!r}")
        seen.append(s)
    if not seen:
        return None
    for candidate in _STATUS_PRIORITY:
        if candidate in seen:
            return candidate
    # 只剩 ABORTED / SKIPPED（不在 8.1 链里）→ 走显式兜底链（R14-5）
    for candidate in _FALLBACK_PRIORITY:
        if candidate in seen:
            return candidate
    raise TestcaseStatusError(f"no rankable status in: {seen!r}")


def pass_rate(counts: dict) -> float:
    """8.3：PASS Rate = PASS / TOTAL。**不得**用 (PASS+RECOVERED)/TOTAL。"""
    total = sum(counts.values())
    return (counts.get("PASS", 0) / total) if total else 0.0


def recovery_rate(counts: dict) -> float:
    """8.3：Recovery Rate = RECOVERED / TOTAL（与 PASS Rate 分开报）。"""
    total = sum(counts.values())
    return (counts.get("RECOVERED", 0) / total) if total else 0.0


# --- 迁移 ---


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone() is not None


def _columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def current_version(conn) -> str | None:
    """已应用的 schema 版本；P0 库（无表）→ None。"""
    if not _table_exists(conn, "schema_migrations"):
        return None
    row = conn.execute(
        "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def _migration_sql() -> str:
    return (MIGRATIONS_DIR / "001_trace_schema_0_1.sql").read_text()


def _add_missing_columns(conn, table: str, specs: dict) -> None:
    """ALTER TABLE ADD COLUMN 逐列补齐（SQLite 支持 ADD COLUMN，不能加
    NOT NULL 无默认值的列——testcase_runs 那种新表直接 CREATE 即可）。

    为什么不 DROP 重建：P0 脚本（run_phase0_demo / verify_stage*）还在读
    `runs.test_case` / `steps.run_id` / `recoveries.strategy` 等 legacy 列，
    删表重建会打断它们。ALTER 增量加列让旧列与新列并存，M2 Gate 后退役
    P0 脚本时再单独做一次收尾清理。
    """
    existing = _columns(conn, table)
    for col, decl in specs.items():
        if col in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


# P0 legacy 表 → 0.1 需要新增的列（类型/默认值必须匹配新建表语义）
_P0_ALTER_COLUMNS = {
    "runs": {
        "trace_schema_version": "TEXT",
        "mta_version": "TEXT",
        "suite": "TEXT",
        "filter_json": "TEXT",
        "app_bundle_id": "TEXT",
        "app_git_commit": "TEXT",
        "metadata_version": "TEXT",
        "metadata_build": "TEXT",
        "metadata_git_commit": "TEXT",
        "metadata_mismatch": "INTEGER DEFAULT 0",
        "metadata_mismatch_override": "INTEGER DEFAULT 0",
        "device_type": "TEXT",
        "device_name": "TEXT",
        "ios_version": "TEXT",
        "device_udid_hash": "TEXT",
        "env_kind": "TEXT",
        "llm_enabled": "INTEGER",
        "llm_calls": "INTEGER DEFAULT 0",
        "exit_code": "INTEGER",
    },
    "steps": {
        "testcase_run_id": "INTEGER",
        "step_type": "TEXT",
        "target_id": "TEXT",
        "locator_origin": "TEXT",
        "locator_strategy": "TEXT",
        "effective_idempotency": "TEXT",
        "effective_risk": "TEXT",
        "failure_type": "TEXT",
        "failure_phase": "TEXT",
        "failure_attribution": "TEXT DEFAULT 'UNTRIAGED'",
        "detail_json": "TEXT",
        "step_schema_version": "TEXT",
    },
    "recoveries": {
        "kind": "TEXT",
        "expected_target": "TEXT",
        "candidate_target": "TEXT",
        "candidate_origin": "TEXT",
        "candidate_type": "TEXT",
        "scope": "TEXT",
        "screen": "TEXT",
        "app_build": "TEXT",
        "source_commit": "TEXT",
        "uniqueness_count": "INTEGER",
        "type_match": "INTEGER",
        "screen_match": "INTEGER",
        "effective_risk": "TEXT",
        "result": "TEXT",
        "reject_reason": "TEXT",
        "llm_model": "TEXT",
        "llm_tokens_in": "INTEGER",
        "llm_tokens_out": "INTEGER",
    },
    "infra_events": {
        "testcase_run_id": "INTEGER",
        "step_index": "INTEGER",
        "non_idempotent_dispatched": "INTEGER DEFAULT 0",
        "action_taken": "TEXT",
        "detail_json": "TEXT",
    },
}

# 全新库直接 CREATE 的表（含 P0 库里没有的那些）
_CREATE_ONLY = ("schema_migrations", "testcase_runs", "recovery_reviews")


def _migrate_p0_to_0_1(conn) -> None:
    """P0 → 0.1：ALTER 补列 + 建新表 + 回填映射字段。

    搬运行序（顺序有依赖：testcase_runs 必须先建好，steps 才能拿到 id）：
      runs.test_case            → testcase_runs（一条旧 run = 一条用例，坑 14）
      steps.run_id              → steps.testcase_run_id（反查 run_id）
      recoveries.strategy       → recoveries.kind（映射到 14.2 的 kind 枚举）
      recoveries.llm_target     → candidate_target
      recoveries.risk_level     → effective_risk
    旧列全部保留（P0 脚本仍在读）。
    """
    is_p0 = _table_exists(conn, "runs") and "test_case" in _columns(conn, "runs")

    # 先把 P0 独有数据抓出来（ALTER 之前），再建表/加列
    legacy_runs = legacy_steps = legacy_recoveries = []
    if is_p0:
        legacy_runs = conn.execute(
            "SELECT run_id, test_case, app_version, app_build, source_commit,"
            " start_time, end_time, status FROM runs").fetchall()
        legacy_steps = conn.execute(
            "SELECT id, run_id, step_index, action_type, locator, status,"
            " error, latency_ms, screenshot_path, ui_tree_path FROM steps"
        ).fetchall()
        legacy_recoveries = conn.execute(
            "SELECT id, step_id, strategy, llm_target, confidence, risk_level,"
            " latency_ms, accepted FROM recoveries").fetchall()
        for table, specs in _P0_ALTER_COLUMNS.items():
            _add_missing_columns(conn, table, specs)

    # 建缺失的表（全新库时建全部 7 张）
    conn.executescript(_migration_sql())

    if not is_p0:
        return

    # runs：回填 0.1 语义列（app_git_commit/metadata_git_commit ← source_commit）
    for (run_id, test_case, app_version, app_build, source_commit,
         start_time, end_time, status) in legacy_runs:
        conn.execute(
            "UPDATE runs SET trace_schema_version=?, app_version=?, app_build=?,"
            " app_git_commit=?, metadata_git_commit=?, start_time=?, end_time=?,"
            " status=? WHERE run_id=?",
            (SCHEMA_VERSION, app_version, app_build, source_commit,
             source_commit, start_time, end_time, status, run_id),
        )
        # P0 一条 run = 一条用例 → 回填 testcase_runs
        conn.execute(
            "INSERT INTO testcase_runs (run_id, testcase_id, attempt, status,"
            " start_time, end_time, detail_json) VALUES (?,?,?,?,?,?,?)",
            (run_id, test_case or run_id, 1, status, start_time, end_time,
             json.dumps({"migrated_from": "p0"})),
        )

    # steps：**原地**回填（ALTER 已把 testcase_run_id/step_type 等加到这张表）。
    # 不用 INSERT 另起新行——P0 脚本按精确 step id 关联 recoveries（坑 14），
    # 插新行会让 AUTOINCREMENT 继续往后排，legacy id 与新行 id 错位。
    run_id_to_tc = {
        r[0]: r[1] for r in conn.execute(
            "SELECT tr.run_id, tr.id FROM testcase_runs tr JOIN runs r"
            " ON tr.run_id = r.run_id")
    }
    for (sid, run_id, step_index, action_type, locator, status, error,
         latency_ms, shot, ui_tree) in legacy_steps:
        tc_id = run_id_to_tc.get(run_id)
        if tc_id is None:
            continue  # 孤儿 step（run 已被删）：legacy 行保留，不硬塞
        conn.execute(
            "UPDATE steps SET testcase_run_id=?, step_type=?,"
            " locator_strategy=coalesce(locator_strategy, ?),"
            " detail_json=coalesce(detail_json, ?),"
            " step_schema_version=? WHERE id=?",
            (tc_id, action_type, locator,
             json.dumps({"error": error} if error else {"migrated_from": "p0"}),
             SCHEMA_VERSION, sid),
        )

    # recoveries：**原地**回填新列（ALTER 已把 kind/candidate_target 等
    # 加到这张表上；再插一行会得到重复记录，且 P0 脚本按原 id 查会错位）。
    # step_id 保持原值——steps 也是原地更新，legacy id 未变，无需重映射。
    kind_map = {"LLM_RECONCILE": "LLM", "SETTLE": "SETTLE_RETRY",
                "POSTCONDITION": "POSTCONDITION", "RUN_MEMO": "RUN_MEMO",
                "DETERMINISTIC": "DETERMINISTIC_CANDIDATE"}
    for (rid, step_id, strategy, llm_target, confidence, risk_level,
         latency_ms, accepted) in legacy_recoveries:
        conn.execute(
            "UPDATE recoveries SET kind=?, candidate_target=?,"
            " effective_risk=?, llm_model=coalesce(llm_model, ?) WHERE id=?",
            (kind_map.get(strategy, "LLM"), llm_target, risk_level, strategy,
             rid),
        )


def migrate(conn) -> str:
    """把 conn 升到 SCHEMA_VERSION，返回版本。幂等。"""
    existing = current_version(conn)
    if existing == SCHEMA_VERSION:
        return SCHEMA_VERSION
    if existing is not None and existing != SCHEMA_VERSION:
        raise TestcaseStatusError(
            f"unsupported trace schema version: {existing!r} "
            f"(this build knows {SCHEMA_VERSION})")
    _migrate_p0_to_0_1(conn)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version, applied_at)"
        " VALUES (?,?)", (SCHEMA_VERSION, _now()))
    conn.commit()
    return SCHEMA_VERSION


# --- TraceStore ---


class TraceStore:
    """schema 0.1 的写入接口。打开即迁移（旧库自动升级，数据保留），
    并清理历史异常中断留下的 RUNNING 残留（Recorder R2-6 同款纪律：
    「不写假终态」的另一面是「不留无终态」——进程重启后不可能仍在跑）。
    单写者假设（P1 的 mta run 是顺序 CLI，无并发 run）。"""

    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        migrate(self.conn)
        self.conn.execute(
            "UPDATE runs SET status='FAIL', end_time=? WHERE status='RUNNING'",
            (_now(),))
        self.conn.commit()

    # -- runs --

    def start_run(self, run_id: str, suite: str | None = None,
                  device_type: str | None = None, **fields) -> None:
        cols = ["run_id", "trace_schema_version", "start_time", "status"]
        vals = [run_id, SCHEMA_VERSION, _now(), "RUNNING"]
        for k, v in fields.items():
            if k in _columns(self.conn, "runs") and k not in cols:
                cols.append(k)
                vals.append(v)
        if suite is not None:
            cols.append("suite")
            vals.append(suite)
        if device_type is not None:
            cols.append("device_type")
            vals.append(device_type)
        ph = ",".join("?" * len(cols))
        self.conn.execute(f"INSERT INTO runs ({','.join(cols)}) VALUES ({ph})",
                          tuple(vals))
        self.conn.commit()

    def end_run(self, run_id: str, status: str,
                exit_code: int | None = None) -> None:
        self.conn.execute(
            "UPDATE runs SET end_time=?, status=?, exit_code=? WHERE run_id=?",
            (_now(), status, exit_code, run_id))
        self.conn.commit()

    def update_run_llm(self, run_id: str, *, llm_calls: int,
                       llm_enabled: bool) -> None:
        """10.5/19 节：LLM 调用数（budget 实数）与开关落 runs 行——
        LLM Invocation Rate 的分子从 trace 可复算，不依赖报告进程内存
        （M4 Gate 实锤：end_run 不带 llm_calls → 恒 0，rate 被低估）。"""
        self.conn.execute(
            "UPDATE runs SET llm_calls=?, llm_enabled=? WHERE run_id=?",
            (llm_calls, int(llm_enabled), run_id))
        self.conn.commit()

    # -- testcase_runs --

    def triage_testcase(self, tc_run_id: int, attribution: str,
                        note: str) -> None:
        """人工归因通道（R14-4 消费入口 / review_m5_task51 P3-4）。

        工具化的 triage UPDATE：在 detail_json 里留**结构化审计记录**
        （tool/ts/note，再归因嵌套 previous）——与直接 SQL 补写区分，
        「trace 是原始记录」的可信度靠机制不靠纪律。note 经 redact。
        """
        if attribution not in ATTRIBUTIONS:
            raise ValueError(
                f"invalid failure_attribution: {attribution!r} "
                f"(allowed: {sorted(ATTRIBUTIONS)})")
        if not (note or "").strip():
            raise ValueError("triage 需要 note（归由理由必须留痕）")
        row = self.conn.execute(
            "SELECT detail_json FROM testcase_runs WHERE id=?",
            (tc_run_id,)).fetchone()
        if row is None:
            raise LookupError(f"testcase_run {tc_run_id} 不存在")
        d = json.loads(row["detail_json"] or "{}")
        record: dict = {"attribution": attribution, "note": note.strip(),
                        "tool": "mta report triage", "ts": _now()}
        if "triage" in d:
            record["previous"] = d["triage"]      # 再归因不覆盖旧判定
        d["triage"] = record
        self.conn.execute(
            "UPDATE testcase_runs SET failure_attribution=?, detail_json=?"
            " WHERE id=?",
            (attribution, json.dumps(redact(d), ensure_ascii=False),
             tc_run_id))
        self.conn.commit()

    def start_testcase(self, run_id: str, testcase_id: str,
                       attempt: int = 1) -> int:
        cur = self.conn.execute(
            "INSERT INTO testcase_runs (run_id, testcase_id, attempt, status,"
            " start_time) VALUES (?,?,?,?,?)",
            (run_id, testcase_id, attempt, "RUNNING", _now()))
        self.conn.commit()
        return int(cur.lastrowid)

    def end_testcase(self, tc_run_id: int, status: str,
                     failure_type: str | None = None,
                     failure_attribution: str | None = None,
                     cleanup_status: str | None = None,
                     detail: dict | None = None,
                     non_idempotent_dispatched: bool | None = None) -> int | None:
        """结束用例。`failure_attribution` 供**人工 triage** 或显式规则写入
        （R14-4：此前无任何写入入口，列恒 UNTRIAGED，归因数据流是断的）。

        刻意保持「只能显式传」而非提供 `attribute_failure(...)` 自动归因
        ——8.2 明文：系统不得仅凭 ELEMENT_NOT_FOUND 判 APP_DEFECT。人工
        triage 的 UI/CLI（`mta review`，Task 2.6）落地后再消费本参数。
        """
        if status not in TESTCASE_STATUSES:
            raise ValueError(f"invalid testcase status: {status!r}")
        if failure_attribution is not None and \
                failure_attribution not in ATTRIBUTIONS:
            raise ValueError(
                f"invalid failure_attribution: {failure_attribution!r} "
                f"(allowed: {sorted(ATTRIBUTIONS)})")
        sets = ["status=?", "end_time=?", "failure_type=?", "cleanup_status=?",
                "detail_json=?", "duration_ms=?"]
        tc = self.conn.execute(
            "SELECT start_time FROM testcase_runs WHERE id=?",
            (tc_run_id,)).fetchone()
        duration = _duration_ms(tc["start_time"] if tc else None)
        vals = [status, _now(), failure_type, cleanup_status,
                json.dumps(redact(detail)) if detail else None, duration]
        if failure_attribution is not None:
            sets.append("failure_attribution=?")
            vals.append(failure_attribution)
        if non_idempotent_dispatched is not None:
            sets.append("non_idempotent_dispatched=?")
            vals.append(1 if non_idempotent_dispatched else 0)
        vals.append(tc_run_id)
        self.conn.execute(
            f"UPDATE testcase_runs SET {','.join(sets)} WHERE id=?", tuple(vals))
        self.conn.commit()

    def mark_non_idempotent_dispatched(self, tc_run_id: int) -> None:
        """7.5：NON_IDEMPOTENT 动作已派发（重试/恢复前必须记，后果不可回滚）。"""
        self.conn.execute(
            "UPDATE testcase_runs SET non_idempotent_dispatched=1 WHERE id=?",
            (tc_run_id,))
        self.conn.commit()

    # -- steps --

    def record_step(self, tc_run_id: int, step_index: int, step_type: str,
                    target_id: str | None = None,
                    locator_origin: str | None = None,
                    locator: dict | None = None,
                    effective_idempotency: str | None = None,
                    effective_risk: str | None = None,
                    status: str | None = None,
                    failure_type: str | None = None,
                    failure_phase: str | None = None,
                    latency_ms: int | None = None,
                    detail: dict | None = None,
                    screenshot_path: str | None = None,
                    ui_tree_path: str | None = None) -> int:
        if failure_phase is not None and failure_phase not in FAILURE_PHASES:
            raise ValueError(
                f"invalid failure_phase: {failure_phase!r} "
                f"(allowed: {sorted(FAILURE_PHASES)} or NULL)")
        cur = self.conn.execute(
            "INSERT INTO steps (testcase_run_id, step_index, step_type,"
            " target_id, locator_origin, locator_strategy, effective_idempotency,"
            " effective_risk, status, failure_type, failure_phase,"
            " latency_ms, detail_json, screenshot_path, ui_tree_path,"
            " step_schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tc_run_id, step_index, step_type, target_id, locator_origin,
             json.dumps(redact(locator)) if locator else None,
             effective_idempotency, effective_risk, status, failure_type,
             failure_phase, latency_ms,
             json.dumps(redact(detail)) if detail else None,
             screenshot_path, ui_tree_path, SCHEMA_VERSION))
        self.conn.commit()
        return int(cur.lastrowid)

    # -- recoveries --

    def record_recovery(self, step_id: int, kind: str, *,
                        expected_target: str | None = None,
                        candidate_target: str | None = None,
                        candidate_origin: str | None = None,
                        candidate_type: str | None = None,
                        scope: str | None = None, screen: str | None = None,
                        app_build: str | None = None,
                        source_commit: str | None = None,
                        confidence: float | None = None,
                        uniqueness_count: int | None = None,
                        type_match: int | None = None,
                        screen_match: int | None = None,
                        effective_risk: str | None = None,
                        accepted: bool = False, result: str | None = None,
                        reject_reason: str | None = None,
                        llm_model: str | None = None,
                        llm_tokens_in: int | None = None,
                        llm_tokens_out: int | None = None,
                        latency_ms: int | None = None) -> int:
        if kind not in RECOVERY_KINDS:
            raise ValueError(f"invalid recovery kind: {kind!r} "
                             f"(allowed: {sorted(RECOVERY_KINDS)})")
        cur = self.conn.execute(
            "INSERT INTO recoveries (step_id, kind, expected_target,"
            " candidate_target, candidate_origin, candidate_type, scope, screen,"
            " app_build, source_commit, confidence, uniqueness_count, type_match,"
            " screen_match, effective_risk, accepted, result, reject_reason,"
            " llm_model, llm_tokens_in, llm_tokens_out, latency_ms)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (step_id, kind, expected_target, candidate_target, candidate_origin,
             candidate_type, scope, screen, app_build, source_commit, confidence,
             uniqueness_count, type_match, screen_match, effective_risk,
             1 if accepted else 0, result, reject_reason, llm_model,
             llm_tokens_in, llm_tokens_out, latency_ms))
        self.conn.commit()
        return int(cur.lastrowid)

    # -- recovery_reviews --

    def create_review(self, recovery_id: int) -> int:
        cur = self.conn.execute(
            "INSERT INTO recovery_reviews (recovery_id) VALUES (?)",
            (recovery_id,))
        self.conn.commit()
        return int(cur.lastrowid)

    def list_reviews(self, status: str | None = "PENDING") -> list:
        """review 列表（join recoveries：补丁导出需要的候选信息同行返回）。"""
        sql = ("SELECT r.id AS review_id, r.review_status, r.reviewer,"
               " r.reviewed_at, r.note,"
               " rec.id AS recovery_id, rec.kind, rec.expected_target,"
               " rec.candidate_target, rec.candidate_type, rec.screen,"
               " rec.app_build, rec.result"
               " FROM recovery_reviews r"
               " JOIN recoveries rec ON rec.id = r.recovery_id")
        params: tuple = ()
        if status is not None:
            sql += " WHERE r.review_status=?"
            params = (status,)
        sql += " ORDER BY r.id"
        return self.conn.execute(sql, params).fetchall()

    def get_review(self, review_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT r.id AS review_id, r.review_status, r.reviewer,"
            " r.reviewed_at, r.note, r.recovery_id,"
            " rec.kind, rec.expected_target, rec.candidate_target,"
            " rec.candidate_type, rec.screen, rec.app_build, rec.result"
            " FROM recovery_reviews r"
            " JOIN recoveries rec ON rec.id = r.recovery_id WHERE r.id=?",
            (review_id,)).fetchone()
        return dict(row) if row is not None else None

    def decide_review(self, review_id: int, review_status: str,
                      reviewer: str, note: str | None = None) -> None:
        if review_status not in REVIEW_STATUSES:
            raise ValueError(f"invalid review_status: {review_status!r}")
        self.conn.execute(
            "UPDATE recovery_reviews SET review_status=?, reviewer=?,"
            " reviewed_at=?, note=? WHERE id=?",
            (review_status, reviewer, _now(), redact(note), review_id))
        self.conn.commit()

    def get_review_seed(self, review_id: int) -> dict | None:
        """单条 review 的 E5 种子字段（设计 8.1：ACCEPT → Candidate 的输入）。

        返回的字段是 `CandidateSeed` 的**唯一供给**：三件套（seed_run_id /
        seed_step_id / seed_recovery_review_id）+ app_id / screen / target /
        候选策略信息。追溯链断裂的行**照样返回**（字段为 None），由调用方
        fail-loud 报错——审计要暴露缺口，消费方不得静默跳过（E5）。
        """
        row = self.conn.execute(
            SEED_SELECT + " WHERE rr.id=?", (review_id,)).fetchone()
        return dict(row) if row is not None else None

    # -- Experience 事件（设计 11.1） --

    def record_experience_event(self, event_type: str, *, run_id: str,
                                tc_run_id: int | None = None,
                                detail: dict | None = None) -> None:
        """设计 11.1：Experience 事件**追加到 P1 Trace**，不新建独立存储体系。

        落点是 trace.db 的通用事件流 `infra_events`——P1 schema 0.1（14.2）
        没有独立的 events 表，infra_events 是该库唯一的事件日志；P2 的
        experience_* / candidate_* / promotion_* 事件统一走本入口（Task 2.4
        的 lookup/hit/miss/guard_block/execution 同此）。

        `action_taken` 留空——P1 稳定性报告的 WDA 指标按
        `action_taken='RESTART_WDA'` 过滤（scripts/p1_stability_report.py），
        本类事件不会污染那条判据。
        """
        if event_type not in EXPERIENCE_EVENT_TYPES:
            raise ValueError(
                f"invalid experience event_type: {event_type!r} "
                f"(allowed: {sorted(EXPERIENCE_EVENT_TYPES)})")
        self.record_infra_event(run_id, tc_run_id, event_type, detail=detail)

    # -- infra_events --

    def record_infra_event(self, run_id: str, tc_run_id: int | None,
                           event_type: str, step_index: int | None = None,
                           non_idempotent_dispatched: bool = False,
                           action_taken: str | None = None,
                           detail: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO infra_events (run_id, testcase_run_id, event_type,"
            " step_index, non_idempotent_dispatched, action_taken, timestamp,"
            " detail_json) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, tc_run_id, event_type, step_index,
             1 if non_idempotent_dispatched else 0, action_taken, _now(),
             json.dumps(redact(detail)) if detail else None))
        self.conn.commit()


def _duration_ms(start_time: str | None) -> int | None:
    """started→now 的毫秒数。start_time 缺失 → None（不猜 0，0 会被当
    「瞬间完成」的假数据）。

    R14-1（P1）：`_now()` 用 `gmtime` 写 **UTC** 字符串，解析必须用
    `calendar.timegm`（按 UTC 解释）；旧实现用 `time.mktime`（按**本地**
    时区解释），本机 UTC+8 下 duration 多出整 8 小时——探针实测真实 sleep
    1.2s 记录成 28,801,468ms。Report 的每条耗时/总时长全失真。
    写 UTC 就按 UTC 读，别混。
    """
    if not start_time:
        return None
    try:
        start = calendar.timegm(time.strptime(start_time, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None
    return max(0, int((time.time() - start) * 1000))