"""Task 2.4 / P1-07：Trace schema 0.1 迁移（设计 14.2 + 8.1/8.2）。

口径（写死在本文件 docstring，测试按此钉，不按实现写）：

1. **版本链**：`schema_migrations` 记录已应用版本；`current_version(conn)`
   返回库当前 schema 版本。P0 库（无 schema_migrations）→ 迁移到 0.1。
2. **数据保留**：P0 的 runs/steps/recoveries/infra_events 行必须在迁移后
   仍可查到。testcase_runs 由旧 runs 回填（一条旧 run = 一条 testcase_run），
   steps.run_id → steps.testcase_run_id 重映射。
3. **纯函数**：testcase 最终状态优先级 8.1
   `ENVIRONMENT_FAILURE > INFRA_FAILURE > BLOCKED > FAIL > RECOVERED > PASS`；
   ATTEMPT 状态（RUNNING/PENDING）不参与终态聚合。
4. **failure_attribution 默认 UNTRIAGED**（8.2）：系统不得自动写 APP_DEFECT。
5. **recoveries 新列齐全**（14.2 + 附录：Experience Store 原始数据）：
   kind / 校验五项（uniqueness_count, type_match, screen_match, confidence,
   effective_risk）/ llm_tokens_in / llm_tokens_out / expected_target /
   candidate_target / screen / app_build / source_commit / result。
6. **R12-3/5 记账落地**：断言的结构化结果（expected/actual/target）落
   `steps.detail_json`，不再只留格式化异常字符串；`detail.kind` 有消费点。
7. **Redactor 前置不变**（H8 / 14.4）：写任何表前先 redact，不存在先写后脱敏。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from tracer.storage import (
    SCHEMA_VERSION,
    current_version,
    migrate,
    aggregate_status,
    pass_rate,
)

# --- 14.2 DDL 期望列（逐表钉住，防「少写一列事后才发现」） ---

EXPECTED_COLUMNS = {
    "schema_migrations": {"version", "applied_at"},
    "runs": {
        "run_id", "trace_schema_version", "mta_version", "suite", "filter_json",
        "app_bundle_id", "app_version", "app_build", "app_git_commit",
        "metadata_version", "metadata_build", "metadata_git_commit",
        "metadata_mismatch", "metadata_mismatch_override",
        "device_type", "device_name", "ios_version", "device_udid_hash",
        "env_kind", "llm_enabled", "llm_calls",
        "start_time", "end_time", "status", "exit_code",
    },
    "testcase_runs": {
        "id", "run_id", "testcase_id", "attempt", "status", "failure_type",
        "failure_attribution", "non_idempotent_dispatched", "cleanup_status",
        "start_time", "end_time", "duration_ms", "detail_json",
    },
    "steps": {
        "id", "testcase_run_id", "step_index", "step_type",
        "target_id", "locator_origin", "locator_strategy",
        "effective_idempotency", "effective_risk",
        "status", "failure_type", "failure_phase", "failure_attribution",
        "latency_ms", "detail_json", "screenshot_path", "ui_tree_path",
        "step_schema_version",
    },
    "recoveries": {
        "id", "step_id", "kind",
        "expected_target", "candidate_target", "candidate_origin", "candidate_type",
        "scope", "screen", "app_build", "source_commit",
        "confidence", "uniqueness_count", "type_match", "screen_match",
        "effective_risk", "accepted", "result", "reject_reason",
        "llm_model", "llm_tokens_in", "llm_tokens_out", "latency_ms",
    },
    "recovery_reviews": {
        "id", "recovery_id", "review_status", "reviewer", "reviewed_at", "note",
    },
    "infra_events": {
        "id", "run_id", "testcase_run_id", "event_type", "step_index",
        "non_idempotent_dispatched", "action_taken", "timestamp", "detail_json",
    },
}

# 14.2 注释里枚举的 failure_phase 取值
FAILURE_PHASES = {"PRE_DISPATCH", "POST_DISPATCH"}
# 14.2 recoveries.kind 取值
RECOVERY_KINDS = {"LLM", "POSTCONDITION", "SETTLE_RETRY", "RUN_MEMO",
                  "DETERMINISTIC_CANDIDATE",
                  # P2 Task 2.4：设计 3.1 的第六种机制（Experience 命中解决，
                  # LLM 未被调用）。P1 设计 14.2 的枚举没有它——P2 必须扩展，
                  # 否则 recoveries 行记不下 Experience 恢复（fail-loud 抛错）。
                  "EXPERIENCE"}
# 8.2 failure_attribution 取值
ATTRIBUTIONS = {"UNTRIAGED", "APP_DEFECT", "AUTOMATION_DEFECT",
                "ENVIRONMENT_DEFECT", "TEST_DATA_DEFECT", "INFRASTRUCTURE_DEFECT"}
# 8.1 testcase 终态
TESTCASE_STATUSES = {"PASS", "RECOVERED", "FAIL", "INFRA_FAILURE",
                     "ENVIRONMENT_FAILURE", "BLOCKED", "ABORTED", "SKIPPED"}


def _cols(conn, table) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _p0_db() -> sqlite3.Connection:
    """P0（Task 2.3 之前）的库结构 + 少量真实数据，用于测升级路径。"""
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
    CREATE TABLE runs (
        run_id TEXT PRIMARY KEY, test_case TEXT, app_version TEXT, app_build TEXT,
        source_commit TEXT, start_time TEXT, end_time TEXT, status TEXT);
    CREATE TABLE steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, step_index INTEGER,
        action_type TEXT, locator TEXT, status TEXT, error TEXT,
        latency_ms INTEGER, screenshot_path TEXT, ui_tree_path TEXT);
    CREATE TABLE recoveries (
        id INTEGER PRIMARY KEY AUTOINCREMENT, step_id INTEGER, strategy TEXT,
        llm_target TEXT, confidence REAL, risk_level TEXT, latency_ms INTEGER,
        accepted BOOLEAN);
    CREATE TABLE infra_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, event_type TEXT,
        timestamp TEXT);
    """)
    conn.executescript("""
    INSERT INTO runs VALUES ('run01','login_001','debug','local','abc123',
                            '2026-09-30T10:00:00Z','2026-09-30T10:01:00Z','PASS');
    INSERT INTO steps VALUES (NULL,'run01',0,'tap','{"id":"login_button"}',
                             'SUCCESS',NULL,120,NULL,NULL);
    INSERT INTO steps VALUES (NULL,'run01',1,'input','{"id":"username_field"}',
                             'SUCCESS',NULL,80,NULL,NULL);
    INSERT INTO recoveries VALUES (NULL,1,'LLM_RECONCILE','login_button',0.92,
                                  'LOW',1500,1);
    INSERT INTO infra_events VALUES (NULL,'run01','WDA_RESTARTED',
                                     '2026-09-30T10:00:30Z');
    """)
    conn.commit()
    return conn


# --- 1. 版本链 ---

def test_fresh_db_creates_schema_0_1_and_records_version():
    conn = sqlite3.connect(":memory:")
    migrate(conn)
    assert current_version(conn) == SCHEMA_VERSION
    row = conn.execute("SELECT version, applied_at FROM schema_migrations").fetchone()
    assert row[0] == SCHEMA_VERSION and row[1], "applied_at 必须留痕"


def test_migrate_is_idempotent():
    conn = sqlite3.connect(":memory:")
    migrate(conn)
    migrate(conn)  # 二次迁移不应重复插入版本行
    n = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert n == 1


def test_p0_db_migrates_to_0_1():
    conn = _p0_db()
    assert current_version(conn) is None, "P0 库无 schema_migrations"
    migrate(conn)
    assert current_version(conn) == SCHEMA_VERSION


def test_migration_preserves_p0_runs_data():
    conn = _p0_db()
    migrate(conn)
    row = conn.execute(
        "SELECT run_id, app_build, source_commit, status FROM runs WHERE run_id='run01'"
    ).fetchone()
    assert row == ("run01", "local", "abc123", "PASS")


def test_migration_backfills_testcase_runs_from_p0_runs():
    """P0 一条 run = 一条用例（runner.run 自己 start_run，坑 14）→ 回填一条
    testcase_run，status 与旧 runs.status 一致。"""
    conn = _p0_db()
    migrate(conn)
    rows = conn.execute(
        "SELECT run_id, testcase_id, status, failure_attribution FROM testcase_runs"
    ).fetchall()
    assert rows == [("run01", "login_001", "PASS", "UNTRIAGED")]


def test_migration_remaps_steps_run_id_to_testcase_run_id():
    conn = _p0_db()
    migrate(conn)
    tc_run_id = conn.execute(
        "SELECT id FROM testcase_runs WHERE run_id='run01'").fetchone()[0]
    idxs = [r[0] for r in conn.execute(
        "SELECT step_index FROM steps WHERE testcase_run_id=? ORDER BY step_index",
        (tc_run_id,))]
    assert idxs == [0, 1], "两步都要重映射到新 testcase_run_id"


def test_migration_maps_p0_recovery_columns():
    """P0 strategy→kind、llm_target→candidate_target、risk_level→effective_risk。
    迁移不能把已有恢复证据丢掉（Experience Store 原始数据，14.2 强调）。"""
    conn = _p0_db()
    migrate(conn)
    row = conn.execute(
        "SELECT kind, candidate_target, confidence, effective_risk, accepted"
        " FROM recoveries").fetchone()
    assert row[0] in RECOVERY_KINDS
    assert row[1] == "login_button"
    assert row[2] == 0.92
    assert row[4] == 1


def test_migration_preserves_infra_events_and_adds_new_columns():
    conn = _p0_db()
    migrate(conn)
    row = conn.execute(
        "SELECT run_id, event_type, timestamp, non_idempotent_dispatched"
        " FROM infra_events").fetchone()
    assert row[0] == "run01" and row[1] == "WDA_RESTARTED"
    assert row[3] == 0, "新列有默认值，不该是 NULL"


def test_migration_does_not_duplicate_rows():
    """行数守恒：迁移是**原地** UPDATE（A），不是 INSERT 另起新行。

    这是真踩过的 bug：首版迁移给 steps 插 0.1 新行，行数直接翻倍
    （真实库 234 → 468），而 P0 脚本按精确 step id 关联 recoveries
    （坑 14）——AUTOINCREMENT 继续往后排会让 legacy id 与新行错位。
    """
    conn = _p0_db()
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("runs", "steps", "recoveries", "infra_events")}
    migrate(conn)
    for table, n in before.items():
        after = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert after == n, f"{table} 行数从 {n} 变成 {after}（迁移不该增删行）"


def test_migration_keeps_legacy_step_ids_stable():
    """legacy step id 必须不变（P0 脚本按 id 关联 recoveries，坑 14）。"""
    conn = _p0_db()
    before = [r[0] for r in conn.execute("SELECT id FROM steps ORDER BY id")]
    migrate(conn)
    after = [r[0] for r in conn.execute("SELECT id FROM steps ORDER BY id")]
    assert before == after


# --- 2. 表结构逐列 ---

@pytest.mark.parametrize("table", sorted(EXPECTED_COLUMNS))
def test_table_has_all_design_columns(table):
    conn = sqlite3.connect(":memory:")
    migrate(conn)
    missing = EXPECTED_COLUMNS[table] - _cols(conn, table)
    assert not missing, f"{table} 缺列: {sorted(missing)}"


def test_new_tables_created_on_fresh_db():
    conn = sqlite3.connect(":memory:")
    migrate(conn)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(EXPECTED_COLUMNS) <= names


def test_failure_phase_values_are_documented_constants():
    from tracer.storage import FAILURE_PHASES as FP
    assert FP == FAILURE_PHASES


def test_recovery_kind_values():
    from tracer.storage import RECOVERY_KINDS as K
    assert K == RECOVERY_KINDS


def test_attribution_values():
    from tracer.storage import ATTRIBUTIONS as A
    assert A == ATTRIBUTIONS


def test_testcase_status_values():
    from tracer.storage import TESTCASE_STATUSES as S
    assert S == TESTCASE_STATUSES


def test_device_udid_hash_is_hash_not_raw_udid():
    """隐私：14.2 用 device_udid_hash 而非 device_udid。DDL 不应出现明文列。"""
    conn = sqlite3.connect(":memory:")
    migrate(conn)
    cols = _cols(conn, "runs")
    assert "device_udid_hash" in cols
    assert "device_udid" not in cols


# --- 3. 状态优先级聚合（8.1，纯函数） ---

@pytest.mark.parametrize("statuses,expected", [
    (["PASS"], "PASS"),
    (["PASS", "RECOVERED"], "RECOVERED"),
    (["RECOVERED", "FAIL"], "FAIL"),
    (["FAIL", "BLOCKED"], "BLOCKED"),
    (["BLOCKED", "INFRA_FAILURE"], "INFRA_FAILURE"),
    (["INFRA_FAILURE", "ENVIRONMENT_FAILURE"], "ENVIRONMENT_FAILURE"),
    # 8.1 原例：动作曾 RECOVERED 成功但 cleanup 失败 → ENVIRONMENT_FAILURE
    (["RECOVERED", "ENVIRONMENT_FAILURE"], "ENVIRONMENT_FAILURE"),
    # 顺序无关
    (["ENVIRONMENT_FAILURE", "PASS"], "ENVIRONMENT_FAILURE"),
    (["FAIL", "PASS", "RECOVERED"], "FAIL"),
])
def test_aggregate_status_priority(statuses, expected):
    assert aggregate_status(statuses) == expected


def test_aggregate_status_ignores_attempt_states():
    """RUNNING/PENDING 是尝试态不是终态，不参与聚合（否则正在跑的步骤会
    把已定的 PASS 拉低）。"""
    assert aggregate_status(["PASS", "RUNNING"]) == "PASS"
    assert aggregate_status(["FAIL", "PENDING"]) == "FAIL"


def test_aggregate_status_empty_is_none():
    assert aggregate_status([]) is None


def test_aggregate_status_aborted_only():
    """R14-5：只剩 ABORTED 时返回 ABORTED（不在优先级链，走 seen[0] 分支）。"""
    assert aggregate_status(["ABORTED"]) == "ABORTED"


def test_aggregate_status_skipped_only():
    assert aggregate_status(["SKIPPED"]) == "SKIPPED"


def test_aggregate_status_aborted_beats_skipped_regardless_of_order():
    """ABORTED/SKIPPED 不在 8.1 优先级链里（该链只覆盖 PASS→ENV 六档）。
    语义上 ABORTED（用例跑挂被中止）比 SKIPPED（没跑）更严重，因此两者
    混合时固定返回 ABORTED，**不依赖输入顺序**。

    实现当前是 `return seen[0]`（依赖顺序），所以这条测试在修复前会红——
    这正是 R14-5 要求专项覆盖的原因：混合场景此前只在别的参数化里被顺带
    覆盖，且顺序恰好一致，掩盖了「结果取决于输入顺序」的事实。
    """
    assert aggregate_status(["SKIPPED", "ABORTED"]) == "ABORTED"
    assert aggregate_status(["ABORTED", "SKIPPED"]) == "ABORTED"


def test_aggregate_status_aborted_does_not_beat_real_failures():
    """优先级链上的终态必须压过 ABORTED/SKIPPED（它们只是兜底分支）。"""
    assert aggregate_status(["ABORTED", "FAIL"]) == "FAIL"
    assert aggregate_status(["SKIPPED", "PASS"]) == "PASS"
    assert aggregate_status(["ABORTED", "ENVIRONMENT_FAILURE"]) == \
        "ENVIRONMENT_FAILURE"


def test_aggregate_status_unknown_status_raises():
    """fail-loud：未知状态不静默当 PASS（否则新状态漏统计还不报错）。"""
    from tracer.storage import TestcaseStatusError
    with pytest.raises(TestcaseStatusError):
        aggregate_status(["PASS", "WAT"])


# --- 4. 通过率口径（8.3） ---

def test_pass_rate_excludes_recovered():
    """8.3：PASS Rate = PASS/TOTAL，不得用 (PASS+RECOVERED)/TOTAL。"""
    assert pass_rate({"PASS": 3, "RECOVERED": 1}) == 0.75
    assert pass_rate({"PASS": 3, "RECOVERED": 1, "FAIL": 0}) == 0.75


def test_recovery_rate_is_separate():
    from tracer.storage import recovery_rate
    assert recovery_rate({"PASS": 3, "RECOVERED": 1}) == 0.25


def test_pass_rate_zero_total_is_zero_not_crash():
    assert pass_rate({}) == 0.0


# --- 5. Redactor 前置不变（H8 / 14.4） ---

def test_detail_json_written_through_redactor(tmp_path):
    """R12-3/5 落地：结构化断言结果落 detail_json，且**先脱敏**。
    password/token 等字段值必须是 ***REDACTED***，不是明文。"""
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    detail = {
        "kind": "assertion_target",
        "password": "hunter2",
        "nested": {"auth_token": "abc123"},
        "assertion": {"expected": 3, "actual": 0, "target": "login_button"},
    }
    sid = store.start_testcase("run1", "login_001")
    step_id = store.record_step(
        sid, step_index=0, step_type="assertion", status="FAILED",
        detail=detail, failure_type="ASSERTION_VALUE_MISMATCH")
    raw = store.conn.execute(
        "SELECT detail_json FROM steps WHERE id=?", (step_id,)).fetchone()[0]
    parsed = json.loads(raw)
    assert parsed["password"] == "***REDACTED***"
    assert parsed["nested"]["auth_token"] == "***REDACTED***"
    # 结构化字段仍在（脱敏不吞数据）
    assert parsed["assertion"] == {"expected": 3, "actual": 0,
                                   "target": "login_button"}
    assert parsed["kind"] == "assertion_target", "R12-5: detail.kind 有消费点"


def test_locator_passes_through_redactor(tmp_path):
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    sid = store.start_testcase("run1", "login_001")
    step_id = store.record_step(
        sid, step_index=0, step_type="tap",
        locator={"strategy": "accessibility id", "value": "ok_button"})
    raw = store.conn.execute(
        "SELECT locator_strategy FROM steps WHERE id=?", (step_id,)).fetchone()[0]
    assert json.loads(raw)["strategy"] == "accessibility id"


# --- 6. TraceStore 写入接口 ---

def test_start_run_and_testcase_records_schema_version(tmp_path):
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run9", suite="smoke", device_type="simulator")
    row = store.conn.execute(
        "SELECT trace_schema_version, suite, device_type FROM runs WHERE run_id='run9'"
    ).fetchone()
    assert row[0] == SCHEMA_VERSION
    assert row[1] == "smoke" and row[2] == "simulator"


def test_failure_attribution_defaults_untriaged(tmp_path):
    """8.2：系统默认 UNTRIAGED，且 store 不提供「自动写 APP_DEFECT」的路径。"""
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run1")
    sid = store.start_testcase("run1", "login_001")
    store.end_testcase(sid, status="FAIL",
                       failure_type="ELEMENT_NOT_FOUND")
    row = store.conn.execute(
        "SELECT failure_attribution FROM testcase_runs WHERE id=?", (sid,)).fetchone()
    assert row[0] == "UNTRIAGED"
    assert not hasattr(store, "attribute_failure"), \
        "不得提供自动归因 API（8.2：不得仅凭 ELEMENT_NOT_FOUND 判 APP_DEFECT）"


def test_non_idempotent_dispatched_tracked(tmp_path):
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run1")
    sid = store.start_testcase("run1", "login_001")
    store.mark_non_idempotent_dispatched(sid)
    row = store.conn.execute(
        "SELECT non_idempotent_dispatched FROM testcase_runs WHERE id=?",
        (sid,)).fetchone()
    assert row[0] == 1


def test_cleanup_status_recordable(tmp_path):
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run1")
    sid = store.start_testcase("run1", "login_001")
    store.end_testcase(sid, status="ENVIRONMENT_FAILURE",
                       failure_type="CLEANUP_FAILED", cleanup_status="FAILED")
    row = store.conn.execute(
        "SELECT cleanup_status, failure_type FROM testcase_runs WHERE id=?",
        (sid,)).fetchone()
    assert tuple(row) == ("FAILED", "CLEANUP_FAILED")


def test_duration_ms_computed(tmp_path):
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run1")
    sid = store.start_testcase("run1", "login_001")
    store.end_testcase(sid, status="PASS")
    d = store.conn.execute(
        "SELECT duration_ms FROM testcase_runs WHERE id=?", (sid,)).fetchone()[0]
    assert d is not None and d >= 0


def test_duration_ms_has_no_timezone_offset(tmp_path):
    """R14-1（P1，探针实锤）：`start_time` 是 UTC 字符串（`_now()` 用 gmtime），
    解析却用 `time.mktime`（按本地时区）→ duration 多出时区偏移。

    本机 UTC+8 实测：真实 sleep 1.2s，记录成 28,801,468ms（多 8 小时）。
    Report 的每条用例耗时/总时长全部失真，后续慢用例分析直接废。

    断言用容差而非精确值：真实 sleep 抖动；但**必须**排除小时级偏差。
    """
    import time
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run1")
    sid = store.start_testcase("run1", "login_001")
    time.sleep(1.2)
    store.end_testcase(sid, status="PASS")
    d = store.conn.execute(
        "SELECT duration_ms FROM testcase_runs WHERE id=?", (sid,)).fetchone()[0]
    # 真实耗时 ~1.2s；容差 2s。旧实现在本机会给 ~28,800,000ms → 挂。
    assert 0 <= d < 60_000, f"duration {d}ms 含时区偏移（本机 UTC+8 会多 8 小时）"


def test_duration_ms_uses_utc_not_local_timezone():
    """直接钉住解析口径：给定一个 UTC 字符串，duration 不能受 TZ 环境变量
    影响。旧实现 time.mktime 受影响（本地 vs UTC 差 8h）。

    注意要**冻结 now** 再跨 TZ 比较：`_duration_ms` 内部调 `time.time()`，
    两次调用之间流逝的毫秒会让结果差 1~2ms，那不是时区偏移。旧实现在
    本机会差 28,800,000ms 量级，1ms 级噪声不可能掩盖它。
    """
    import os
    from unittest import mock
    from tracer import storage

    s = "2026-09-30T04:42:34Z"
    frozen = 1_789_000_000.0  # 固定 now，两个 TZ 下完全可比

    got = {}
    for tz in ("UTC", "Asia/Shanghai"):
        os.environ["TZ"] = tz
        __import__("time").tzset()
        with mock.patch.object(storage.time, "time", return_value=frozen):
            got[tz] = storage._duration_ms(s)
    os.environ.pop("TZ", None)
    __import__("time").tzset()

    assert got["UTC"] == got["Asia/Shanghai"], (
        f"_duration_ms 受 TZ 影响: {got}（UTC 字符串必须用 timegm 解析）")
    # 旧实现在 Asia/Shanghai 下会比 UTC 多 28,800,000ms
    assert got["UTC"] < 60_000_000, f"duration 数量级不对: {got}"


def test_attribution_can_be_set_explicitly_but_never_auto(tmp_path):
    """R14-4：`failure_attribution` 此前无写入入口，列恒 UNTRIAGED。补参数
    供人工 triage 写；仍不提供自动归因 API（8.2 硬约束）。"""
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run1")
    sid = store.start_testcase("run1", "login_001")
    store.end_testcase(sid, status="FAIL",
                       failure_type="ELEMENT_NOT_FOUND",
                       failure_attribution="AUTOMATION_DEFECT")
    row = store.conn.execute(
        "SELECT failure_attribution FROM testcase_runs WHERE id=?",
        (sid,)).fetchone()
    assert row[0] == "AUTOMATION_DEFECT"
    assert not hasattr(store, "attribute_failure")


def test_invalid_attribution_rejected(tmp_path):
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run1")
    sid = store.start_testcase("run1", "tc")
    with pytest.raises(ValueError):
        store.end_testcase(sid, status="FAIL", failure_attribution="MY_GUESS")


def test_recovery_records_experience_store_fields(tmp_path):
    """14.2 强调：recoveries 的 screen/app_build/source_commit/expected_target/
    candidate/result 是 V2 Experience Store 原始数据，P1 必须完整记录。"""
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run1")
    sid = store.start_testcase("run1", "login_001")
    step_id = store.record_step(sid, step_index=0, step_type="tap")
    rid = store.record_recovery(
        step_id, kind="LLM", expected_target="login_button",
        candidate_target="login_submit", candidate_origin="LLM",
        candidate_type="Button", scope="step", screen="LoginView",
        app_build="local", source_commit="abc123", confidence=0.9,
        uniqueness_count=1, type_match=1, screen_match=1,
        effective_risk="LOW", accepted=True, result="TAP_RETRY",
        llm_model="m", llm_tokens_in=100, llm_tokens_out=20)
    row = store.conn.execute(
        "SELECT kind, expected_target, candidate_target, screen, app_build,"
        " source_commit, uniqueness_count, type_match, screen_match, result,"
        " llm_tokens_in, llm_tokens_out FROM recoveries WHERE id=?",
        (rid,)).fetchone()
    assert row[0] == "LLM"
    assert row[1] == "login_button" and row[2] == "login_submit"
    assert row[3] == "LoginView" and row[4] == "local" and row[5] == "abc123"
    assert row[6:9] == (1, 1, 1)
    assert row[9] == "TAP_RETRY"
    assert row[10:12] == (100, 20)


def test_recovery_review_defaults_pending(tmp_path):
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run1")
    sid = store.start_testcase("run1", "tc")
    step_id = store.record_step(sid, step_index=0, step_type="tap")
    rid = store.record_recovery(step_id, kind="LLM", accepted=True)
    review_id = store.create_review(rid)
    row = store.conn.execute(
        "SELECT review_status FROM recovery_reviews WHERE id=?",
        (review_id,)).fetchone()
    assert row[0] == "PENDING"


def test_recovery_review_can_be_accepted(tmp_path):
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run1")
    sid = store.start_testcase("run1", "tc")
    step_id = store.record_step(sid, step_index=0, step_type="tap")
    rid = store.record_recovery(step_id, kind="LLM", accepted=True)
    review_id = store.create_review(rid)
    store.decide_review(review_id, "ACCEPT", reviewer="me", note="ok")
    row = store.conn.execute(
        "SELECT review_status, reviewer, note, reviewed_at FROM recovery_reviews"
        " WHERE id=?", (review_id,)).fetchone()
    assert row[0] == "ACCEPT" and row[1] == "me" and row[2] == "ok"
    assert row[3], "reviewed_at 必须留痕"


def test_infra_event_records_non_idempotent_context(tmp_path):
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run1")
    sid = store.start_testcase("run1", "tc")
    store.record_infra_event(
        "run1", sid, "WDA_DEAD", step_index=3,
        non_idempotent_dispatched=1, action_taken="RESTART_WDA",
        detail={"retry": 1})
    row = store.conn.execute(
        "SELECT run_id, testcase_run_id, event_type, step_index,"
        " non_idempotent_dispatched, action_taken, detail_json FROM infra_events"
    ).fetchone()
    assert row[0] == "run1" and row[1] == sid and row[2] == "WDA_DEAD"
    assert row[3] == 3 and row[4] == 1 and row[5] == "RESTART_WDA"
    assert json.loads(row[6]) == {"retry": 1}


def test_invalid_failure_phase_rejected(tmp_path):
    """fail-loud：failure_phase 只允许 PRE_DISPATCH/POST_DISPATCH/NULL。"""
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run1")
    sid = store.start_testcase("run1", "tc")
    with pytest.raises(ValueError):
        store.record_step(sid, step_index=0, step_type="tap",
                          failure_phase="MID_DISPATCH")


def test_invalid_recovery_kind_rejected(tmp_path):
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run1")
    sid = store.start_testcase("run1", "tc")
    step_id = store.record_step(sid, step_index=0, step_type="tap")
    with pytest.raises(ValueError):
        store.record_recovery(step_id, kind="MAGIC", accepted=True)


def test_invalid_testcase_status_rejected(tmp_path):
    from tracer.storage import TraceStore
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run1")
    sid = store.start_testcase("run1", "tc")
    with pytest.raises(ValueError):
        store.end_testcase(sid, status="WHATEVER")


def test_store_opens_existing_db_without_losing_data(tmp_path):
    """二次打开同库：migrate 幂等 + 已有行不丢。"""
    from tracer.storage import TraceStore
    p = tmp_path / "t.db"
    s1 = TraceStore(p)
    s1.start_run("run_keep")
    s2 = TraceStore(p)
    assert s2.conn.execute(
        "SELECT COUNT(*) FROM runs WHERE run_id='run_keep'").fetchone()[0] == 1

def test_store_open_sweeps_stale_running_runs(tmp_path):
    """review_m4_task43 P3-4：打开库即清理 RUNNING 残留（Recorder R2-6
    同款纪律）——管线 bug 级异常穿透 run_all 时 runs 行不留无终态。"""
    from tracer.storage import TraceStore
    p = tmp_path / "t.db"
    s1 = TraceStore(p)
    s1.start_run("run_stale")
    # 模拟进程崩溃：没有 end_run，直接重新打开
    s2 = TraceStore(p)
    row = s2.conn.execute(
        "SELECT status, end_time FROM runs WHERE run_id='run_stale'"
    ).fetchone()
    assert row["status"] == "FAIL"
    assert row["end_time"] is not None
    # 新 run 不受影响
    s2.start_run("run_fresh")
    s2.end_run("run_fresh", status="PASS", exit_code=0)
    assert s2.conn.execute(
        "SELECT status FROM runs WHERE run_id='run_fresh'").fetchone()[0] \
        == "PASS"


# --- review_m5_task51 P3-4：triage 工具通道（人工归因的审计化） ---

def test_triage_testcase_sets_attribution_and_audit_record(tmp_path):
    from tracer.storage import TraceStore
    import json as _json
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run_t")
    sid = store.start_testcase("run_t", "tc_1")
    store.end_testcase(sid, status="FAIL", failure_type="WAIT_TIMEOUT")
    store.triage_testcase(sid, "ENVIRONMENT_DEFECT", "模拟器转场抖动")
    row = store.conn.execute(
        "SELECT failure_attribution, detail_json FROM testcase_runs"
        " WHERE id=?", (sid,)).fetchone()
    assert row["failure_attribution"] == "ENVIRONMENT_DEFECT"
    d = _json.loads(row["detail_json"])
    assert d["triage"]["attribution"] == "ENVIRONMENT_DEFECT"
    assert d["triage"]["tool"] == "mta report triage"
    assert d["triage"]["note"] == "模拟器转场抖动"
    assert d["triage"]["ts"]


def test_triage_testcase_re_triage_keeps_previous(tmp_path):
    from tracer.storage import TraceStore
    import json as _json
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run_t")
    sid = store.start_testcase("run_t", "tc_1")
    store.end_testcase(sid, status="FAIL", failure_type="WAIT_TIMEOUT")
    store.triage_testcase(sid, "ENVIRONMENT_DEFECT", "初判")
    store.triage_testcase(sid, "APP_DEFECT", "复核后改判")
    d = _json.loads(store.conn.execute(
        "SELECT detail_json FROM testcase_runs WHERE id=?",
        (sid,)).fetchone()["detail_json"])
    assert d["triage"]["attribution"] == "APP_DEFECT"
    assert d["triage"]["previous"]["attribution"] == "ENVIRONMENT_DEFECT"
    assert d["triage"]["previous"]["note"] == "初判"


def test_triage_testcase_rejects_bad_input(tmp_path):
    from tracer.storage import TraceStore
    import pytest
    store = TraceStore(tmp_path / "t.db")
    store.start_run("run_t")
    sid = store.start_testcase("run_t", "tc_1")
    store.end_testcase(sid, status="FAIL")
    with pytest.raises(ValueError):
        store.triage_testcase(sid, "NOT_AN_ATTRIBUTION", "n")
    with pytest.raises(ValueError):
        store.triage_testcase(sid, "APP_DEFECT", "   ")
    with pytest.raises(LookupError):
        store.triage_testcase(99999, "APP_DEFECT", "n")


def test_cli_report_triage(tmp_path, capsys):
    from cli.main import main
    from tracer.storage import TraceStore
    db = tmp_path / "t.db"
    store = TraceStore(db)
    store.start_run("run_t")
    sid = store.start_testcase("run_t", "tc_1")
    store.end_testcase(sid, status="FAIL")
    code = main(["report", "triage", str(sid), "--attribution",
                 "ENVIRONMENT_DEFECT", "--note", "单发抖动",
                 "--db", str(db)])
    assert code == 0
    out = capsys.readouterr().out
    assert "ENVIRONMENT_DEFECT" in out
    # 坏归因 → argparse choices 层 exit 2（与 review CLI 分层先例一致；
    # agent/storage 层 ValueError 守卫供直调方）；不存在 id → exit 3
    with pytest.raises(SystemExit) as ei:
        main(["report", "triage", str(sid), "--attribution", "BOGUS",
              "--note", "x", "--db", str(db)])
    assert ei.value.code == 2
    code = main(["report", "triage", "424242", "--attribution",
                 "APP_DEFECT", "--note", "x", "--db", str(db)])
    assert code == 3
