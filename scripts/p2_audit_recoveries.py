"""P2-01 — P1 Trace 数据审计（纯查询，不写库）。

回答三个问题（设计 1.2 的"真实数据基础"前提）：
  1. P1 积累的 Trace 里，Locator Failure 是否重复出现、集中在哪些 target？
  2. 哪些 Recovery 被人工 ACCEPT——即可做 Experience 种子的清单（E5）？
  3. 存量数据的质量缺口（悬空追溯链 / 空 result / 缺 review）有多大？

所有函数只读 sqlite3 连接（row_factory=Row 由调用方或 main 设置），
不依赖设备、不写库——审计本身不产生数据（E5：真实事件只能真跑出来）。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# 本脚本的既有调用惯例是**直跑**（`python scripts/p2_audit_recoveries.py …`
# ——p2_seed_recoveries.sh 收尾打印的复核命令、review_p2_task11 记录的实跑
# 方式都是这个形态）。直跑时 sys.path[0]=scripts/，包内导入会
# ModuleNotFoundError（review_p2_task23 P2-1 实锤），故显式补仓根。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracer.storage import SEED_SELECT  # noqa: E402  (需在 sys.path 补根之后)

DEFAULT_DB = "out/trace.db"


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    if not isinstance(conn.row_factory, type(sqlite3.Row)):
        conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# --- 汇总 ---


def audit_summary(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT (SELECT count(*) FROM runs) AS runs_total,"
        "       (SELECT count(*) FROM testcase_runs) AS tc_runs_total,"
        "       (SELECT count(*) FROM steps) AS steps_total,"
        "       (SELECT count(*) FROM steps WHERE failure_type IS NOT NULL)"
        "         AS steps_with_failure,"
        "       (SELECT count(*) FROM runs WHERE llm_enabled IS NULL)"
        "         AS llm_enabled_null,"
        "       (SELECT min(date(start_time)) FROM runs) AS date_min,"
        "       (SELECT max(date(start_time)) FROM runs) AS date_max"
    ).fetchone()
    return dict(row)


# --- Locator Failure 重复出现（Experience 价值的前提） ---


def locator_failure_top(conn: sqlite3.Connection,
                        top_n: int = 10) -> list[dict]:
    return _rows(
        conn,
        "SELECT s.target_id, s.failure_type, count(*) AS n,"
        "       count(DISTINCT tc.run_id) AS distinct_runs"
        " FROM steps s"
        " JOIN testcase_runs tc ON tc.id = s.testcase_run_id"
        " WHERE s.failure_type IS NOT NULL"
        " GROUP BY s.target_id, s.failure_type"
        " ORDER BY n DESC, s.target_id"
        " LIMIT ?", (top_n,))


# --- Recovery / Review 分布 ---


def recovery_breakdown(conn: sqlite3.Connection) -> list[dict]:
    return _rows(
        conn,
        "SELECT kind, COALESCE(result, '') AS result,"
        "       COALESCE(accepted, 0) AS accepted, count(*) AS n"
        " FROM recoveries GROUP BY kind, result, accepted"
        " ORDER BY n DESC")


def review_breakdown(conn: sqlite3.Connection) -> list[dict]:
    return _rows(
        conn,
        "SELECT review_status, count(*) AS n FROM recovery_reviews"
        " GROUP BY review_status ORDER BY n DESC")


# --- 数据质量：追溯链完整性 ---


def recovery_provenance(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT (SELECT count(*) FROM recoveries) AS recoveries_total,"
        "       (SELECT count(*) FROM recoveries r"
        "          LEFT JOIN steps s ON s.id = r.step_id"
        "         WHERE s.id IS NULL) AS dangling_step,"
        "       (SELECT count(DISTINCT r.id) FROM recoveries r"
        "          JOIN recovery_reviews rr ON rr.recovery_id = r.id)"
        "         AS with_review"
    ).fetchone()
    return dict(row)


# --- (screen, target) 去重对（review P3-1：Experience 主键
# (app_id, screen_id, target_id) 的去重依据，M2 Store lookup 语义）---


def screen_target_pairs(conn: sqlite3.Connection) -> list[dict]:
    """ACCEPT 的恢复按 (screen, expected→candidate) 去重计数。"""
    return _rows(
        conn,
        "SELECT rec.screen, rec.expected_target, rec.candidate_target,"
        "       count(*) AS n, count(DISTINCT tc.run_id) AS distinct_runs"
        " FROM recovery_reviews rr"
        " JOIN recoveries rec ON rec.id = rr.recovery_id"
        " LEFT JOIN steps s ON s.id = rec.step_id"
        " LEFT JOIN testcase_runs tc ON tc.id = s.testcase_run_id"
        " WHERE rr.review_status = 'ACCEPT'"
        " GROUP BY rec.screen, rec.expected_target, rec.candidate_target"
        " ORDER BY rec.screen, rec.expected_target")


# --- E5 种子清单：ACCEPT + 三件套可追溯 ---


def seedable_accepts(conn: sqlite3.Connection) -> list[dict]:
    """recovery_reviews 里 ACCEPT 的记录，join 出 CandidateSeed 需要的全部字段。

    `seed_ready`：三件套（run/step/review id）+ screen + target 齐全且
    追溯链不断。悬空链的行**保留**并标 False——审计要暴露质量缺口，
    不是静默过滤（这些行即使有 ACCEPT 也不能做种子，M2 的
    create_candidate 会因 E5 校验拒绝它们）。

    join 片段与 `TraceStore.get_review_seed`（Task 2.3 的消费入口）共用
    `tracer.storage.SEED_SELECT`——同一 join 两处维护会漂移。
    """
    rows = _rows(
        conn,
        SEED_SELECT + " WHERE rr.review_status = 'ACCEPT' ORDER BY rr.id")
    for r in rows:
        r["seed_ready"] = all([
            r["seed_run_id"] is not None,
            r["seed_step_id"] not in (None, 0),
            r["seed_recovery_review_id"] is not None,
            r["screen"],
            r["target_id"],
        ])
    return rows


# --- 报告 ---


def render_markdown(summary: dict, failures: list[dict], recoveries: list[dict],
                    reviews: list[dict], provenance: dict,
                    seeds: list[dict],
                    pairs: list[dict] | None = None) -> str:
    pairs = pairs or []
    lines = ["# P2 Trace 数据审计报告", ""]
    lines += ["## 汇总", "",
              f"- runs：{summary['runs_total']}"
              f"（{summary['date_min']} ~ {summary['date_max']}）",
              f"- testcase_runs：{summary['tc_runs_total']}",
              f"- steps：{summary['steps_total']}"
              f"（含失败 {summary['steps_with_failure']}）",
              f"- runs.llm_enabled 为 NULL：{summary['llm_enabled_null']}", ""]

    lines += ["## Locator Failure 重复出现（top）", "",
              "| target_id | failure_type | 次数 | 跨 run 数 |", "|---|---|---|---|"]
    lines += [f"| {f['target_id']} | {f['failure_type']} | {f['n']}"
              f" | {f['distinct_runs']} |" for f in failures] or ["|（无）||||"]

    lines += ["", "## Recovery 分布", "",
              "| kind | result | accepted | 条数 |", "|---|---|---|---|"]
    lines += [f"| {r['kind']} | {r['result']} | {r['accepted']} | {r['n']} |"
              for r in recoveries]

    lines += ["", "## Review 分布", "",
              "| review_status | 条数 |", "|---|---|"]
    lines += [f"| {r['review_status']} | {r['n']} |" for r in reviews] \
        or ["|（无）| |"]

    lines += ["", "## (screen, target) 去重对（Experience 主键维度）", "",
              "| screen | expected | candidate | 条数 | 跨 run 数 |",
              "|---|---|---|---|---|"]
    lines += [f"| {p['screen']} | {p['expected_target']}"
              f" | {p['candidate_target']} | {p['n']} | {p['distinct_runs']} |"
              for p in pairs] or ["|（无）|||||"]

    lines += ["", "## 追溯链质量", "",
              f"- recoveries 总数：{provenance['recoveries_total']}",
              f"- step_id 悬空 dangling（关联不到 steps）："
              f"{provenance['dangling_step']}",
              f"- 有 review 记录的 recovery：{provenance['with_review']}", ""]

    lines += ["## 可做 Experience 种子的 ACCEPT 清单（E5）", "",
              "| review_id | app_id | seed_run_id | seed_step_id | screen"
              " | target_id | candidate | seed_ready |",
              "|---|---|---|---|---|---|---|---|"]
    lines += [f"| {s['seed_recovery_review_id']} | {s['app_id']} |"
              f" {s['seed_run_id']} | {s['seed_step_id']} | {s['screen']}"
              f" | {s['target_id']} | {s['candidate_target']}"
              f" | {s['seed_ready']} |"
              for s in seeds] or ["|（无）||||||||"]
    ready = sum(1 for s in seeds if s["seed_ready"])
    lines += ["", f"**seed_ready=True 的种子：{ready} 条**（成功标准要求"
              " 50+ 条真实 Recovery 事件基线）", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="P2-01 Trace 数据审计（纯查询，不写库）")
    parser.add_argument("--db", default=DEFAULT_DB,
                        help=f"TraceStore SQLite 路径（默认 {DEFAULT_DB}）")
    parser.add_argument("--top", type=int, default=10,
                        help="Locator Failure top N（默认 10）")
    parser.add_argument("--out", default=None,
                        help="markdown 报告落盘路径（默认只打印摘要）")
    args = parser.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        print(f"AUDIT ERROR: {db} 不存在", file=sys.stderr)
        return 2
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row

    summary = audit_summary(conn)
    failures = locator_failure_top(conn, top_n=args.top)
    recoveries = recovery_breakdown(conn)
    reviews = review_breakdown(conn)
    provenance = recovery_provenance(conn)
    seeds = seedable_accepts(conn)
    pairs = screen_target_pairs(conn)
    conn.close()

    md = render_markdown(summary, failures, recoveries, reviews,
                         provenance, seeds, pairs)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"P2 Trace 审计报告已写 {args.out}")
        # P3-2：机器可读出力同盘——M2 Task 2.3 消费 CandidateSeed 用，
        # markdown 只给人看。
        import json
        json_path = Path(args.out).with_suffix(".json")
        json_path.write_text(json.dumps({
            "summary": summary,
            "seedable_accepts": seeds,
            "screen_target_pairs": pairs,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"机器可读指标已写 {json_path}")

    ready = sum(1 for s in seeds if s["seed_ready"])
    print(f"P2 Trace 审计：runs={summary['runs_total']} "
          f"steps_fail={summary['steps_with_failure']} "
          f"recoveries={provenance['recoveries_total']} "
          f"(dangling={provenance['dangling_step']}) "
          f"reviews={sum(r['n'] for r in reviews)} "
          f"seed_ready={ready}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
