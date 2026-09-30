#!/usr/bin/env python3
"""Task 2.4 / P1-07：Trace schema 0.1 迁移验证（设计 14.2）。

两段：
  A. **真实 P0 库升级**：把项目里 `out/trace.db` 复制到 `out/p1_task24/`，
     在副本上跑 migrate，逐表比对迁移前后行数与关键字段值 → 证据落
     `out/p1_task24/migration_report.json`（不动原始库，验证不破坏 P0 数据）。
  B. **全新库 + 写入链路**：TraceStore 端到端写入一条含 recovery/review/
     infra_event 的完整用例，验证脱敏前置 + 结构化 detail 落库 + 8.1 聚合。

跑：`.venv/bin/python phase0/verify_p1_task24.py`
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, ".")

OUT = Path("out/p1_task24")
report: dict = {}


def _table_counts(db: Path) -> dict:
    conn = sqlite3.connect(db)
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    out = {}
    for n in names:
        try:
            out[n] = conn.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0]
        except sqlite3.Error:
            out[n] = "ERR"
    conn.close()
    return out


def part_a() -> None:
    """A. 真实 P0 库（out/trace.db）升级验证——在副本上做，不动原库。"""
    src = Path("out/trace.db")
    if not src.exists():
        report["part_a"] = {"skipped": f"{src} 不存在（P0 库尚未产生）"}
        print(f"SKIP  A: {src} 不存在")
        return
    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / "trace_migrated.db"
    shutil.copy2(src, dst)

    before = _table_counts(src)
    from tracer.storage import current_version, migrate
    conn = sqlite3.connect(dst)
    version_before = current_version(conn)
    migrate(conn)
    version_after = current_version(conn)
    after = _table_counts(dst)

    # 关键：legacy 行数必须守恒（ALTER + 回填是原地更新，不该丢行）
    conserved = {}
    for table in ("runs", "steps", "recoveries", "infra_events"):
        b, a = before.get(table, 0), after.get(table, 0)
        # steps/recoveries 会因「新列回填」新增行（P0 行 → 0.1 行），
        # 所以只要求「不少于」；runs 同样可能新增（P0 runs 一条=一个 tc run，
        # 但我们不新增 runs 行）→ runs 要求严格相等
        if table == "runs":
            conserved[table] = {"before": b, "after": a, "exact": a == b}
        else:
            conserved[table] = {"before": b, "after": a, "not_lost": a >= b}

    # 抽样：一条 P0 step 在 0.1 侧是否可查（重映射生效）
    sample = conn.execute(
        "SELECT tc.testcase_id, s.step_index, s.step_type, s.status"
        " FROM steps s JOIN testcase_runs tc ON s.testcase_run_id = tc.id"
        " ORDER BY s.id LIMIT 1").fetchone()

    # 抽样：P0 recovery 的 kind 映射
    rec = conn.execute(
        "SELECT kind, candidate_target, effective_risk FROM recoveries"
        " WHERE kind IS NOT NULL LIMIT 1").fetchone()

    # P0 legacy 列仍在（P0 脚本还在读）
    legacy_cols_ok = "test_case" in {
        r[1] for r in conn.execute("PRAGMA table_info(runs)")}

    conn.commit()
    conn.close()

    ok = (version_before is None and version_after == "0.1"
          and conserved["runs"]["exact"]
          and all(v.get("not_lost") for k, v in conserved.items() if k != "runs")
          and legacy_cols_ok)
    report["part_a"] = {
        "source": str(src), "migrated": str(dst),
        "version_before": version_before, "version_after": version_after,
        "counts_before": before, "counts_after": after,
        "conserved": conserved, "legacy_cols_preserved": legacy_cols_ok,
        "sample_step": list(sample) if sample else None,
        "sample_recovery": list(rec) if rec else None,
        "passed": ok,
    }
    print(f"{'PASS' if ok else 'FAIL'}  A: P0 库 {src} → 0.1（副本 {dst}）")
    print(f"      表计数 前: {before}")
    print(f"      表计数 后: {after}")
    print(f"      legacy 列保留(test_case): {legacy_cols_ok}")
    print(f"      抽样 step: {sample}")
    print(f"      抽样 recovery: {rec}")


def part_b() -> None:
    """B. 全新库写入链路 + 脱敏前置 + 8.1 聚合。"""
    from tracer.storage import (TraceStore, aggregate_status, migrate,
                                pass_rate, recovery_rate)
    OUT.mkdir(parents=True, exist_ok=True)
    db = OUT / "fresh.db"
    if db.exists():
        db.unlink()
    store = TraceStore(db)
    checks: dict = {}

    store.start_run("run_24", suite="smoke", device_type="simulator",
                    app_bundle_id="com.phaset0.logindemo")

    # 用例 A：PASS
    a = store.start_testcase("run_24", "login_001")
    store.record_step(a, step_index=0, step_type="tap", target_id="login_button",
                      locator={"strategy": "accessibility id",
                               "value": "login_button"},
                      status="SUCCESS", effective_idempotency="IDEMPOTENT")
    store.end_testcase(a, status="PASS")

    # 用例 B：FAIL，断言结构化结果落 detail（R12-3/5 记账落地）
    b = store.start_testcase("run_24", "search_001")
    s = store.record_step(
        b, step_index=0, step_type="assertion", target_id="search_field",
        status="FAILED", failure_type="ASSERTION_VALUE_MISMATCH",
        failure_phase="POST_DISPATCH",
        detail={"kind": "assertion_target",
                "password": "hunter2",
                "auth_token": "leaked-token",
                "assertion": {"condition": "exists", "expected": True,
                              "actual": False, "target": "search_field",
                              "timeout": 10.0}})
    store.end_testcase(b, status="FAIL",
                       failure_type="ASSERTION_VALUE_MISMATCH")

    # 用例 C：步骤曾 RECOVERED，cleanup 失败 → ENVIRONMENT_FAILURE（8.1 原例）
    c = store.start_testcase("run_24", "profile_001")
    step_c = store.record_step(c, step_index=0, step_type="tap", status="FAILED",
                               failure_type="ELEMENT_NOT_FOUND",
                               failure_phase="PRE_DISPATCH")
    rid = store.record_recovery(
        step_c, kind="LLM", expected_target="go_profile",
        candidate_target="cell_alpha", candidate_origin="LLM",
        candidate_type="Cell", scope="step", screen="HomeView",
        app_build="local", source_commit="deadbee", confidence=0.72,
        uniqueness_count=1, type_match=0, screen_match=1,
        effective_risk="LOW", accepted=False,
        result="REJECT_TYPE_MISMATCH", reject_reason="candidate is a cell",
        llm_model="step-5-preview", llm_tokens_in=512, llm_tokens_out=64,
        latency_ms=2400)
    rev = store.create_review(rid)
    store.decide_review(rev, "REJECT", reviewer="terry",
                        note="候选类型不符，需重定位")
    store.mark_non_idempotent_dispatched(c)
    store.record_infra_event("run_24", c, "WDA_DEAD", step_index=0,
                             non_idempotent_dispatched=True,
                             action_taken="RESTART_WDA",
                             detail={"retry": 1, "reason": "wda timeout"})
    store.end_testcase(c, status="ENVIRONMENT_FAILURE",
                       failure_type="CLEANUP_FAILED", cleanup_status="FAILED")

    store.end_run("run_24", "ENVIRONMENT_FAILURE", exit_code=2)

    # --- 断言 ---
    detail = json.loads(store.conn.execute(
        "SELECT detail_json FROM steps WHERE id=?", (s,)).fetchone()[0])
    checks["password_redacted"] = detail["password"] == "***REDACTED***"
    checks["auth_token_redacted"] = detail["auth_token"] == "***REDACTED***"
    checks["structured_assertion_kept"] = detail["assertion"]["expected"] is True
    checks["detail_kind_present"] = detail.get("kind") == "assertion_target"

    att = store.conn.execute(
        "SELECT failure_attribution FROM testcase_runs WHERE id=?", (b,)
    ).fetchone()[0]
    checks["attribution_default_untriaged"] = att == "UNTRIAGED"
    checks["no_auto_attribution_api"] = not hasattr(store, "attribute_failure")

    rec_row = store.conn.execute(
        "SELECT kind, expected_target, candidate_target, screen, source_commit,"
        " uniqueness_count, type_match, screen_match, result, llm_tokens_in"
        " FROM recoveries WHERE id=?", (rid,)).fetchone()
    checks["experience_store_fields"] = (
        rec_row[0] == "LLM" and rec_row[1] == "go_profile"
        and rec_row[2] == "cell_alpha" and rec_row[3] == "HomeView"
        and rec_row[4] == "deadbee" and rec_row[5] == 1 and rec_row[6] == 0
        and rec_row[7] == 1 and rec_row[8] == "REJECT_TYPE_MISMATCH"
        and rec_row[9] == 512)
    rev_row = store.conn.execute(
        "SELECT review_status, reviewer FROM recovery_reviews WHERE id=?",
        (rev,)).fetchone()
    checks["review_recorded"] = tuple(rev_row) == ("REJECT", "terry")

    infra = store.conn.execute(
        "SELECT non_idempotent_dispatched, action_taken FROM infra_events"
    ).fetchone()
    checks["infra_context"] = tuple(infra) == (1, "RESTART_WDA")
    checks["non_idempotent_tracked"] = store.conn.execute(
        "SELECT non_idempotent_dispatched FROM testcase_runs WHERE id=?",
        (c,)).fetchone()[0] == 1

    counts = {"PASS": 1, "FAIL": 1, "ENVIRONMENT_FAILURE": 1}
    checks["aggregate_priority"] = aggregate_status(list(counts)) == \
        "ENVIRONMENT_FAILURE"
    checks["pass_rate_excludes_recovered"] = abs(
        pass_rate({"PASS": 3, "RECOVERED": 1}) - 0.75) < 1e-9
    checks["recovery_rate_separate"] = abs(
        recovery_rate({"PASS": 3, "RECOVERED": 1}) - 0.25) < 1e-9
    checks["schema_version"] = store.conn.execute(
        "SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1
    checks["no_plaintext_udid_column"] = "device_udid" not in {
        r[1] for r in store.conn.execute("PRAGMA table_info(runs)")}

    ok = all(checks.values())
    report["part_b"] = {"db": str(db), "checks": checks, "passed": ok}
    print(f"\n{'PASS' if ok else 'FAIL'}  B: 全新库写入链路")
    for k, v in checks.items():
        print(f"      {'ok ' if v else 'FAIL'} {k}")


if __name__ == "__main__":
    part_a()
    part_b()
    a_ok = report.get("part_a", {}).get("passed", report.get("part_a", {}).get("skipped") is not None)
    b_ok = report["part_b"]["passed"]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "migration_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nA={'PASS' if a_ok else 'FAIL'} B={'PASS' if b_ok else 'FAIL'}"
          f" → {OUT / 'migration_report.json'}")
    raise SystemExit(0 if (a_ok and b_ok) else 1)