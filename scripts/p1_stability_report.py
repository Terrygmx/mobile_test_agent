"""p1_stability_report.py — M5 稳定性基线聚合（设计 17 M5 / 19 节，Task 5.1）。

输入：rounds CSV（p1_stability_run.sh 落盘，每次 mta run 调用一行，含
时间窗）+ TraceStore SQLite（stability 专用 db）。归因方式：runs.start_time
落在该次调用的时间窗内 → 归属该轮（run_id 由 cmd_run 内部生成，脚本不
控制，时间窗是唯一稳定关联键——csv 记录的是调用前后的 UTC ISO 时刻）。

产出（17 M5 要求的字段，一个不少）：
  - PASS/RECOVERED/FAIL/INFRA/ENV 比例（run 级 + 用例级；用例级 PASS Rate
    按 8.3 口径——RECOVERED 不进分子）
  - flaky 用例清单（同一 testcase 跨轮出现 ≥2 种终态）
  - WDA 重启总数（infra_events action_taken='RESTART_WDA'）
  - 平均耗时（run 级 + 用例级 duration_ms）
  - Gate M5 卫生检查：RUNNING 残留 / 未分类失败（FAIL×UNTRIAGED）/ LLM
    调用数（稳定 build 必须 0）

聚合核心 aggregate() 是纯函数（行数据进、dict 出，H18），CLI 只做 IO。
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

__all__ = ["aggregate", "load_invocations", "main"]


def load_invocations(csv_path: Path) -> list[dict]:
    """rounds CSV → [{round, suite, start, end, exit_code, duration_s}, ...]。"""
    with open(csv_path, newline="", encoding="utf-8") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _in_window(start_iso: str | None, inv_start: str, inv_end: str) -> bool:
    """runs.start_time（'YYYY-MM-DDTHH:MM:SS.ffffff+00:00' 形态）是否落在
    调用时间窗内。字符串比较对同格式 ISO 串即字典序比较，够用且无时区
    解析依赖；start_time 为空（异常行）不归因。"""
    if not start_iso:
        return False
    return inv_start <= start_iso <= inv_end


def aggregate(db_path: Path, invocations: list[dict]) -> dict:
    """聚合核心。纯函数：db 行 + 调用窗 → 指标 dict。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    runs: list[dict] = []
    tc_rows: list[dict] = []
    wda_restarts = 0
    untriaged_fails = 0
    llm_calls = 0
    matched_windows = 0
    seen_run_ids: set[str] = set()

    for inv in invocations:
        inv_start, inv_end = inv["start"], inv["end"]
        rows = conn.execute(
            "SELECT * FROM runs WHERE start_time >= ? AND start_time <= ?",
            (inv_start, inv_end)).fetchall()
        matched = [dict(r) for r in rows
                   if r["run_id"] not in seen_run_ids]
        matched_windows += 1 if matched else 0
        for r in matched:
            # 相邻调用共享边界秒时同 run 可能落两窗——按 run_id 去重，
            # 归属首个命中窗（调用顺序即 CSV 行序）
            seen_run_ids.add(r["run_id"])
            runs.append(r)
            llm_calls += r["llm_calls"] or 0
        tc = conn.execute(
            "SELECT t.* FROM testcase_runs t JOIN runs r"
            " ON t.run_id = r.run_id"
            " WHERE r.start_time >= ? AND r.start_time <= ?",
            (inv_start, inv_end)).fetchall()
        for row in tc:
            d = dict(row)
            d["_suite"] = inv.get("suite", "")
            tc_rows.append(d)
            if d["status"] == "FAIL" and \
                    (d["failure_attribution"] or "UNTRIAGED") == "UNTRIAGED":
                untriaged_fails += 1
        wda = conn.execute(
            "SELECT COUNT(*) FROM infra_events i JOIN runs r"
            " ON i.run_id = r.run_id"
            " WHERE r.start_time >= ? AND r.start_time <= ?"
            " AND i.action_taken = 'RESTART_WDA'",
            (inv_start, inv_end)).fetchone()[0]
        wda_restarts += wda

    running_left = conn.execute(
        "SELECT COUNT(*) FROM runs WHERE status='RUNNING'").fetchone()[0]

    # --- run 级比例 ---
    run_status_counts: dict[str, int] = {}
    for r in runs:
        run_status_counts[r["status"]] = \
            run_status_counts.get(r["status"], 0) + 1
    run_total = len(runs)

    # --- 用例级比例（8.3：PASS Rate = PASS / TOTAL，RECOVERED 不进分子）---
    tc_status_counts: dict[str, int] = {}
    for t in tc_rows:
        tc_status_counts[t["status"]] = \
            tc_status_counts.get(t["status"], 0) + 1
    tc_total = len(tc_rows)

    # --- flaky：同一 testcase 跨轮出现 ≥2 种终态 ---
    by_case: dict[str, set] = {}
    for t in tc_rows:
        by_case.setdefault(t["testcase_id"], set()).add(t["status"])
    flaky = sorted(cid for cid, states in by_case.items() if len(states) > 1)

    # --- 耗时（runs 表无 duration_ms 列，14.2：从 start/end 时间戳算；
    #      testcase_runs 有列，直接读）---
    def _iso_s(ts: str) -> float:
        import datetime
        if not ts:
            return 0.0
        return datetime.datetime.strptime(
            ts, "%Y-%m-%dT%H:%M:%SZ").timestamp()

    durations = [(_iso_s(r["end_time"]) - _iso_s(r["start_time"])) * 1000
                 for r in runs if r["end_time"] and r["start_time"]]
    tc_durations = [t["duration_ms"] for t in tc_rows if t["duration_ms"]]

    return {
        "invocations": len(invocations),
        "invocations_with_runs": matched_windows,
        "rounds": sorted({int(i["round"]) for i in invocations
                          if str(i.get("round", "")).isdigit()}),
        "runs": run_total,
        "run_status_counts": run_status_counts,
        "testcase_runs": tc_total,
        "tc_status_counts": tc_status_counts,
        "tc_pass_rate": (tc_status_counts.get("PASS", 0) / tc_total
                         if tc_total else 0.0),
        "tc_recovered": tc_status_counts.get("RECOVERED", 0),
        "flaky_cases": flaky,
        "wda_restarts": wda_restarts,
        "avg_run_duration_ms": (sum(durations) / len(durations)
                                if durations else 0),
        "avg_case_duration_ms": (sum(tc_durations) / len(tc_durations)
                                 if tc_durations else 0),
        "llm_calls": llm_calls,
        "untriaged_fails": untriaged_fails,
        "running_left": running_left,
        # Gate M5 卫生判据（17 M5：不预设通过率 SLA，只查卫生）
        "hygiene_ok": (running_left == 0 and llm_calls == 0),
    }


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def render(report: dict) -> str:
    """人读报表（stdout）。"""
    lines = [
        f"invocations: {report['invocations']} "
        f"(with runs: {report['invocations_with_runs']})  "
        f"rounds: {report['rounds']}",
        f"runs: {report['runs']}  status: {report['run_status_counts']}",
        f"testcase_runs: {report['testcase_runs']}  "
        f"status: {report['tc_status_counts']}",
        f"PASS Rate (8.3 口径): {_fmt_pct(report['tc_pass_rate'])}  "
        f"RECOVERED: {report['tc_recovered']}",
        f"flaky cases: "
        f"{', '.join(report['flaky_cases']) or '（无）'}",
        f"WDA restarts: {report['wda_restarts']}  "
        f"avg run: {report['avg_run_duration_ms']:.0f}ms  "
        f"avg case: {report['avg_case_duration_ms']:.0f}ms",
        f"llm_calls: {report['llm_calls']}（稳定 build 必须 0）  "
        f"untriaged FAILs: {report['untriaged_fails']}  "
        f"RUNNING 残留: {report['running_left']}",
        f"hygiene: {'OK' if report['hygiene_ok'] else 'VIOLATION'}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="M5 稳定性基线聚合（17 M5 / 19 节）")
    ap.add_argument("--csv", default="out/stability/rounds.csv",
                    help="p1_stability_run.sh 的轮次 CSV（默认 "
                         "out/stability/rounds.csv）")
    ap.add_argument("--db", default="out/stability/trace.db",
                    help="stability 专用 TraceStore（默认 "
                         "out/stability/trace.db）")
    ap.add_argument("--json", dest="json_path", metavar="PATH",
                    help="指标 JSON 落盘路径（可选）")
    args = ap.parse_args(argv)

    csv_path, db_path = Path(args.csv), Path(args.db)
    if not csv_path.is_file():
        print(f"PREFLIGHT ERROR: rounds CSV 不存在: {csv_path} "
              f"(先跑 scripts/p1_stability_run.sh)")
        return 3
    if not db_path.is_file():
        print(f"PREFLIGHT ERROR: trace db 不存在: {db_path}")
        return 3

    report = aggregate(db_path, load_invocations(csv_path))
    print(render(report))
    if args.json_path:
        import json
        Path(args.json_path).write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"json: {args.json_path}")
    # Gate M5 卫生判据违规 → exit 2（与 run 级 INFRA 同语义：环境/卫生问题）
    return 0 if report["hygiene_ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
