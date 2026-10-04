"""Task 1.1 / P2-01：Trace 数据审计（纯查询，不写库）。

口径（写死在本文件，测试按此钉）：

1. **seedable_accepts 是 E5 的种子清单**：recovery_reviews 中 review_status
   = 'ACCEPT' 的记录，join recoveries → steps → testcase_runs → runs 补全
   `seed_run_id / seed_step_id / seed_recovery_review_id` 三件套 + app_id /
   screen / target / 候选信息。REJECT 与 PENDING 不出现在清单里。
2. **悬空追溯链不得当种子**：recoveries.step_id 关联不到 steps（P0 遗留
   `step_id=0` 写入）→ 行保留在清单里但 `seed_ready=False`——审计要暴露
   数据质量问题，不是静默过滤。
3. **locator_failure_top**：按 (target_id, failure_type) 聚合 steps 中
   failure_type 非空的行，次数降序；distinct_runs 反映跨 run 重复
   （"同一问题反复出现"是 Experience 价值的前提）。
4. 汇总口径：runs / steps / 悬空 recoveries / review 状态分布各自独立统计。
5. CLI：--db 指定库，--out 落盘 markdown 报告，退出码 0。
"""

from __future__ import annotations

import pytest

from pathlib import Path

from scripts.p2_audit_recoveries import (
    audit_summary,
    locator_failure_top,
    main,
    recovery_breakdown,
    recovery_provenance,
    render_markdown,
    review_breakdown,
    screen_target_pairs,
    seedable_accepts,
)
from tracer.storage import TraceStore


@pytest.fixture()
def audit_db(tmp_path):
    """造一个最小但结构完整的 P1 schema 库（raw SQL 插入，钉审计口径）。

    布局：
      r1/tc1: s1,s2 失败 login_button；rec1(挂 s1, RECOVERED) ← rev1 ACCEPT
      r2/tc2: s3 失败 login_button、s4 失败 signin_button；
              rec2(step_id=999 悬空) ← rev2 ACCEPT（必须 seed_ready=False）
              rec3(挂 s4, RECOVERED) ← rev3 REJECT、rev4 PENDING
      s5 无失败（不进 failure 统计）
    """
    store = TraceStore(tmp_path / "audit.db")
    conn = store.conn
    conn.executemany(
        "INSERT INTO runs (run_id, trace_schema_version, app_bundle_id,"
        " app_build, llm_enabled, start_time) VALUES (?,?,?,?,?,?)",
        [("r1", "0.1", "com.demo.app", "1025", 1, "2026-10-01T00:00:00Z"),
         ("r2", "0.1", "com.demo.app", "1025", 0, "2026-10-02T00:00:00Z")])
    conn.executemany(
        "INSERT INTO testcase_runs (run_id, testcase_id, status)"
        " VALUES (?,?,?)",
        [("r1", "tc_login", "RECOVERED"), ("r2", "tc_search", "FAIL")])
    conn.executemany(
        "INSERT INTO steps (testcase_run_id, step_index, step_type, target_id,"
        " status, failure_type, failure_phase) VALUES (?,?,?,?,?,?,?)",
        [(1, 0, "tap", "login_button", "RECOVERED", "ELEMENT_NOT_FOUND",
          "PRE_DISPATCH"),
         (1, 1, "tap", "login_button", "RECOVERED", "ELEMENT_NOT_FOUND",
          "PRE_DISPATCH"),
         (2, 0, "tap", "login_button", "FAIL", "ELEMENT_NOT_FOUND",
          "PRE_DISPATCH"),
         (2, 1, "input", "signin_button", "FAIL", "ELEMENT_NOT_FOUND",
          "PRE_DISPATCH"),
         (2, 2, "assert", "home_title", "SUCCESS", None, None)])
    conn.executemany(
        "INSERT INTO recoveries (step_id, kind, expected_target,"
        " candidate_target, screen, app_build, result, accepted)"
        " VALUES (?,?,?,?,?,?,?,?)",
        [(1, "LLM", "login_button", "signin_button", "LoginView", "1025",
          "RECOVERED", 1),
         (999, "LLM", "login_button", "signin_button", "LoginView", "1025",
          "", 0),
         (4, "LLM", "signin_button", "signin_field", "LoginView", "1025",
          "RECOVERED", 1)])
    conn.executemany(
        "INSERT INTO recovery_reviews (recovery_id, review_status, reviewer)"
        " VALUES (?,?,?)",
        [(1, "ACCEPT", "alice"), (2, "ACCEPT", "alice"),
         (3, "REJECT", "bob"), (3, "PENDING", None)])
    conn.commit()
    return store


# --- 1. seedable_accepts（E5 种子清单） ---


def test_seedable_accepts_linked_chain(audit_db):
    rows = seedable_accepts(audit_db.conn)
    assert len(rows) == 2                      # ACCEPT 的两条（REJECT/PENDING 不进）

    ok = next(r for r in rows if r["seed_recovery_review_id"] == 1)
    assert ok["seed_ready"] is True
    assert ok["seed_run_id"] == "r1"
    assert ok["seed_step_id"] == 1
    assert ok["app_id"] == "com.demo.app"
    assert ok["screen"] == "LoginView"
    assert ok["target_id"] == "login_button"
    assert ok["candidate_target"] == "signin_button"
    assert ok["app_build"] == "1025"


def test_seedable_accepts_dangling_not_ready(audit_db):
    """悬空 step_id 的 ACCEPT 行保留在清单里，但 seed_ready=False（暴露问题）"""
    bad = next(r for r in seedable_accepts(audit_db.conn)
               if r["seed_recovery_review_id"] == 2)
    assert bad["seed_ready"] is False
    assert bad["seed_run_id"] is None


def test_seedable_accepts_excludes_reject_and_pending(audit_db):
    ids = {r["seed_recovery_review_id"] for r in seedable_accepts(audit_db.conn)}
    assert 3 not in ids                         # rec3 的 REJECT/PENDING 都不进


# --- 2. locator_failure_top ---


def test_locator_failure_top_groups_and_orders(audit_db):
    rows = locator_failure_top(audit_db.conn, top_n=10)
    assert [(r["target_id"], r["failure_type"], r["n"], r["distinct_runs"])
            for r in rows] == [
        ("login_button", "ELEMENT_NOT_FOUND", 3, 2),
        ("signin_button", "ELEMENT_NOT_FOUND", 1, 1)]


def test_locator_failure_top_limit(audit_db):
    assert len(locator_failure_top(audit_db.conn, top_n=1)) == 1


# --- 3. 汇总与数据质量 ---


def test_audit_summary(audit_db):
    s = audit_summary(audit_db.conn)
    assert s["runs_total"] == 2
    assert s["tc_runs_total"] == 2
    assert s["steps_total"] == 5
    assert s["steps_with_failure"] == 4
    assert s["llm_enabled_null"] == 0
    assert s["date_min"] == "2026-10-01"
    assert s["date_max"] == "2026-10-02"


def test_recovery_provenance_dangling(audit_db):
    p = recovery_provenance(audit_db.conn)
    assert p["recoveries_total"] == 3
    assert p["dangling_step"] == 1              # step_id=999 那条
    assert p["with_review"] == 3                # rec1/rec2/rec3 各有 review


def test_recovery_breakdown(audit_db):
    rows = {(r["kind"], r["result"], r["accepted"]): r["n"]
            for r in recovery_breakdown(audit_db.conn)}
    assert rows[("LLM", "RECOVERED", 1)] == 2
    assert rows[("LLM", "", 0)] == 1


def test_review_breakdown(audit_db):
    assert {r["review_status"]: r["n"]
            for r in review_breakdown(audit_db.conn)} == {
        "ACCEPT": 2, "REJECT": 1, "PENDING": 1}


# --- 4. 报告与 CLI ---


def test_render_markdown_contains_key_sections(audit_db):
    md = render_markdown(audit_summary(audit_db.conn),
                         locator_failure_top(audit_db.conn),
                         recovery_breakdown(audit_db.conn),
                         review_breakdown(audit_db.conn),
                         recovery_provenance(audit_db.conn),
                         seedable_accepts(audit_db.conn))
    assert md.startswith("# ")
    for key in ("runs", "login_button", "seed_run_id", "ACCEPT", "dangling"):
        assert key in md


def test_cli_main_writes_report(audit_db, tmp_path, capsys):
    out = tmp_path / "report.md"
    rc = main(["--db", str(audit_db.conn.execute(
        "PRAGMA database_list").fetchone()[2]), "--out", str(out)])
    assert rc == 0
    assert out.exists()
    assert "login_button" in out.read_text(encoding="utf-8")
    assert "P2 Trace" in capsys.readouterr().out


# --- 6. review_m5 → p2 review P3-1/P3-2：(screen,target) 维度 + app_id ---


def test_screen_target_pairs_dedupe(audit_db):
    """ACCEPT 恢复按 (screen, expected, candidate) 去重计数——Experience
    主键 (app_id, screen_id, target_id) 的去重依据。"""
    from scripts.p2_audit_recoveries import screen_target_pairs

    conn = audit_db.conn
    conn.row_factory = None  # _rows 自己会设
    pairs = screen_target_pairs(conn)
    # rec1（挂 s1）与 rec2（悬空）都是 ACCEPT 且同为
    # (LoginView, login_button→signin_button)——同 pair 计 2；
    # rec3 是 REJECT/PENDING，不进对。
    by_key = {(p["screen"], p["expected_target"],
               p["candidate_target"]): p for p in pairs}
    assert ("LoginView", "login_button", "signin_button") in by_key
    assert by_key[("LoginView", "login_button",
                   "signin_button")]["n"] == 2
    # 跨 run 去重：rec2 悬空挂不到 run → 只有 r1 一个 run
    assert by_key[("LoginView", "login_button",
                   "signin_button")]["distinct_runs"] == 1


def test_render_markdown_app_id_and_pairs(audit_db):
    """P3-2：种子清单输出 app_id 列（Candidate 主键第一段）；
    P3-1：pairs 段渲染。"""
    conn = audit_db.conn
    seeds = seedable_accepts(conn)
    md = render_markdown(
        audit_summary(conn), [], recovery_breakdown(conn),
        review_breakdown(conn), recovery_provenance(conn), seeds,
        screen_target_pairs(conn))
    assert "app_id" in md and "com.demo.app" in md
    assert "(screen, target) 去重对" in md


def test_main_json_export(audit_db, tmp_path, capsys):
    """P3-2：--out 同时落机器可读 JSON（M2 Task 2.3 消费）。"""

    import json

    db = tmp_path / "audit.db"
    audit_db.conn.commit()
    # 原库在 tmp_path 下的另一路径——直接对同一文件再开一个连接读即可
    src = audit_db.conn.execute(
        "PRAGMA database_list").fetchall()[0][2]
    db.write_bytes(Path(src).read_bytes())
    out = tmp_path / "report.md"
    code = main(["--db", str(db), "--out", str(out)])
    assert code == 0
    jpath = out.with_suffix(".json")
    assert jpath.exists(), "JSON 必须与 markdown 同盘"
    data = json.loads(jpath.read_text())
    assert "seedable_accepts" in data and "screen_target_pairs" in data
    assert data["seedable_accepts"][0]["app_id"] == "com.demo.app"


def test_script_runs_directly_by_path(tmp_path):
    """P2-1（review_p2_task23）：脚本**直跑**（脚本路径，非 `-m`）必须可用。

    `p2_seed_recoveries.sh` 收尾打印的复核命令、以及 Task 1.1 review 记录的
    实跑方式，都是「`python scripts/p2_audit_recoveries.py …`」这一形态；包
    路径导入（本文件其余用例）天然测不到这条调用面。
    """
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    db = tmp_path / "empty.db"
    TraceStore(db)                       # 建出真实 schema 的空库
    proc = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "p2_audit_recoveries.py"),
         "--db", str(db)],
        cwd=str(repo_root), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "P2 Trace 审计" in proc.stdout
