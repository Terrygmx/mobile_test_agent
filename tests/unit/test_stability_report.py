"""p1_stability_report 聚合核心（M5 / Task 5.1）。

H18：合成 TraceStore db + 内存调用窗 → aggregate() 纯函数验证，离设备。
"""
from __future__ import annotations

import pytest

from scripts.p1_stability_report import aggregate, load_invocations, main
from tracer.storage import TraceStore

T0 = "2026-10-03T00:00:00Z"


def _iso(offset_s: int) -> str:
    """T0 + offset 秒（与 storage._now() 同格式的 UTC 串）。"""
    import datetime
    t = datetime.datetime(2026, 10, 3, 0, 0, 0,
                          tzinfo=datetime.timezone.utc) \
        + datetime.timedelta(seconds=offset_s)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _set_dur(db, table, where: str, ms: int) -> None:
    """合成时长直接 UPDATE——duration_ms 由 lifecycle 时钟计算，
    合成数据不经它（Report 读的是列值，谁写的无所谓）。"""
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute(f"UPDATE {table} SET duration_ms=? WHERE {where}", (ms,))
    conn.commit()
    conn.close()


def _set_start(db, run_id: str, iso: str) -> None:
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("UPDATE runs SET start_time=? WHERE run_id=?", (iso, run_id))
    conn.commit()
    conn.close()


def _build_db(path):
    """两轮合成数据：
    round 1: run A（PASS 用例×2 + FAIL×1 untriaged + RECOVERED×1）
    round 2: run B（同 4 条全 PASS——tc_002 跨轮 FAIL/PASS → flaky）
    """
    store = TraceStore(path)
    store.start_run("stab_r1", suite="smoke", env_kind="sandbox")
    # start_run 的 start_time 恒写 _now()（fields 同名键被跳过）——
    # 合成时间窗靠直接 UPDATE 对齐
    _set_start(path, "stab_r1", _iso(0))
    store.update_run_llm("stab_r1", llm_calls=0, llm_enabled=0)
    s1 = store.start_testcase("stab_r1", "tc_001")
    store.end_testcase(s1, status="PASS")
    s2 = store.start_testcase("stab_r1", "tc_002")
    store.end_testcase(s2, status="FAIL", failure_type="WAIT_TIMEOUT")
    # 未归因 → UNTRIAGED
    s3 = store.start_testcase("stab_r1", "tc_003")
    store.end_testcase(s3, status="RECOVERED")
    s4 = store.start_testcase("stab_r1", "tc_004")
    store.end_testcase(s4, status="PASS")
    store.record_infra_event("stab_r1", None, "WDA_DEAD",
                             action_taken="RESTART_WDA")
    store.end_run("stab_r1", status="FAIL", exit_code=1)
    _set_dur(path, "testcase_runs", "run_id='stab_r1' AND testcase_id='tc_001'", 100)
    _set_dur(path, "testcase_runs", "run_id='stab_r1' AND testcase_id='tc_002'", 200)
    _set_dur(path, "testcase_runs", "run_id='stab_r1' AND testcase_id='tc_003'", 300)
    _set_dur(path, "testcase_runs", "run_id='stab_r1' AND testcase_id='tc_004'", 400)

    store.start_run("stab_r2", suite="smoke", env_kind="sandbox")
    _set_start(path, "stab_r2", _iso(60))
    store.update_run_llm("stab_r2", llm_calls=2, llm_enabled=1)
    for cid in ("tc_001", "tc_002", "tc_003", "tc_004"):
        s = store.start_testcase("stab_r2", cid)
        store.end_testcase(s, status="PASS")
    store.end_run("stab_r2", status="PASS", exit_code=0)
    for i, cid in enumerate(("tc_001", "tc_002", "tc_003", "tc_004"), 1):
        _set_dur(path, "testcase_runs",
                 f"run_id='stab_r2' AND testcase_id='{cid}'", 100 * i)


def _invocations():
    return [
        {"round": "1", "suite": "smoke", "seq": "1",
         "start": _iso(-1), "end": _iso(30), "exit_code": "1",
         "duration_s": "5"},
        {"round": "2", "suite": "smoke", "seq": "1",
         "start": _iso(59), "end": _iso(90), "exit_code": "0",
         "duration_s": "4"},
    ]


def test_aggregate_counts_and_ratios(tmp_path):
    db = tmp_path / "t.db"
    _build_db(db)
    r = aggregate(db, _invocations())
    assert r["runs"] == 2
    assert r["run_status_counts"] == {"FAIL": 1, "PASS": 1}
    assert r["testcase_runs"] == 8
    # 8.3 口径：RECOVERED 不进分子——r1 PASS×2 + r2 PASS×4 = 6 / 8
    assert r["tc_pass_rate"] == 6 / 8
    assert r["tc_status_counts"]["RECOVERED"] == 1
    assert r["tc_status_counts"]["FAIL"] == 1


def test_aggregate_flaky_detection(tmp_path):
    db = tmp_path / "t.db"
    _build_db(db)
    r = aggregate(db, _invocations())
    # tc_002 FAIL→PASS；tc_003 RECOVERED→PASS（跨轮恢复不确定性同样
    # 是 flaky 信号）；其余用例两轮同态
    assert r["flaky_cases"] == ["tc_002", "tc_003"]


def test_aggregate_hygiene_counters(tmp_path):
    db = tmp_path / "t.db"
    _build_db(db)
    r = aggregate(db, _invocations())
    assert r["wda_restarts"] == 1
    assert r["llm_calls"] == 2, "llm_calls 按 runs 实数求和"
    assert r["untriaged_fails"] == 1, "FAIL×UNTRIAGED 必须计数（Gate M5）"
    assert r["running_left"] == 0
    # llm_calls>0 → 卫生违规（稳定 build 的 --no-llm 基线必须 0）
    assert r["hygiene_ok"] is False


def test_aggregate_avg_durations(tmp_path):
    db = tmp_path / "t.db"
    _build_db(db)
    r = aggregate(db, _invocations())
    # run 级时长从 start/end 时间戳计算（runs 表无 duration_ms 列）
    assert r["avg_run_duration_ms"] >= 0
    # 用例时长均值：r1 100/200/300/400 + r2 100..400
    assert r["avg_case_duration_ms"] == (100 + 200 + 300 + 400
                                         + 100 + 200 + 300 + 400) / 8


def test_aggregate_boundary_second_attribution(tmp_path):
    """review_m5_task51 P3-2：run 起始恰在边界秒 → 归属**自己的调用窗**
    （半开区间 [start, end)），不是前一窗。run_id 去重只是兜底。
    stab_r2 start=_iso(60) == 窗1 end == 窗2 start：归属窗2（search）。"""
    db = tmp_path / "t.db"
    _build_db(db)
    invs = [{"round": "1", "suite": "smoke", "seq": "1",
             "start": _iso(-1), "end": _iso(60), "exit_code": "1",
             "duration_s": "5"},
            {"round": "2", "suite": "search", "seq": "1",
             "start": _iso(60), "end": _iso(90), "exit_code": "0",
             "duration_s": "4"}]
    r = aggregate(db, invs)
    assert r["runs"] == 2, "不得重复计数"
    assert r["suite_counts"] == {"smoke": 4, "search": 4}, (
        "stab_r2 的用例必须归属它自己的调用窗（search），不是前窗")


def test_aggregate_wda_procs_gate(tmp_path):
    """P3-3：WDA runner 进程峰值 >2 = 累积泄漏 → 卫生违规；
    未采样（旧格式 CSV 无列）→ 不参与判据。"""
    db = tmp_path / "t.db"
    _build_db(db)
    base = [{"round": "1", "suite": "smoke", "seq": "1",
             "start": _iso(-1), "end": _iso(30), "exit_code": "0",
             "duration_s": "5"}]
    assert aggregate(db, base)["hygiene_ok"] is True, "未采样不判"

    leaky = [dict(base[0], wda_procs="5")]
    assert aggregate(db, leaky)["hygiene_ok"] is False, "峰值 5 > 2 = 泄漏"

    normal = [dict(base[0], wda_procs="1")]
    assert aggregate(db, normal)["hygiene_ok"] is True


def test_render_snapshot_fields():
    """P3-5：render() 字段名快照——渲染层拼错字段至少要被看见。"""
    from scripts.p1_stability_report import render
    r = {"invocations": 2, "invocations_with_runs": 2, "rounds": [1, 2],
         "runs": 2, "run_status_counts": {"PASS": 2},
         "testcase_runs": 8, "tc_status_counts": {"PASS": 8},
         "tc_pass_rate": 1.0, "tc_recovered": 0, "flaky_cases": [],
         "suite_counts": {"smoke": 8}, "wda_restarts": 0,
         "avg_run_duration_ms": 100.0, "avg_case_duration_ms": 10.0,
         "llm_calls": 0, "untriaged_fails": 0, "running_left": 0,
         "wda_procs_max": 1, "hygiene_ok": True}
    text = render(r)
    for needle in ("invocations: 2", "PASS Rate (8.3 口径): 100.0%",
                   "flaky cases: （无）", "WDA restarts: 0",
                   "llm_calls: 0", "hygiene: OK",
                   "WDA runner 进程峰值: 1"):
        assert needle in text, f"渲染缺字段: {needle}"


def test_cli_preflight_missing_csv(tmp_path, capsys):
    code = main(["--csv", str(tmp_path / "nope.csv"),
                 "--db", str(tmp_path / "nope.db")])
    assert code == 3


def test_load_invocations(tmp_path):
    p = tmp_path / "rounds.csv"
    p.write_text(
        "round,suite,seq,start,end,exit_code,duration_s,"
        "running_left,untriaged_fails\n"
        f"1,smoke,1,{_iso(0)},{_iso(5)},1,5,0,1\n", encoding="utf-8")
    rows = load_invocations(p)
    assert rows[0]["round"] == "1" and rows[0]["exit_code"] == "1"
