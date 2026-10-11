"""Task 5.6 / P3-25：`mta diagnose` CLI（设计 §8；M5 的出口）。

判据：

1. **F7 启动期拒绝**：production 下自主命令不可用（`_check_autonomous_env`）；
2. **主路径**：crash trace → 规则出假设（LLM 零调用）→ 落一条 `bug_candidates`
   行，状态推进到 `HUMAN_REVIEW`（送上审核台），`attribution_hypothesis` 过阈值
   才给建议（F10）；
3. **无假设也是结论**：规则无结论且 `--no-llm` → exit 0、不落候选；
4. **前置错误 → exit 3**：`--run-id` 不存在 / 拿不到 app_id；
5. **口径登记**：没有 `--base-build` 时**不写** `dedup_key`（三元组缺 source_change）。

设备侧不装配（`_run_reproduction` 在设备不可用时如实记「无法确认」），故这些
用例全是**离线**的——真机复现由 M5 Gate 覆盖。
"""
from __future__ import annotations

import json

import pytest

from agents.storage import SQLiteAgentStore
from cli.main import main
from tracer.storage import TraceStore


def _seed_crash_run(trace_db, *, run_id="run_crash", app_build="local",
                    app_id="com.demo"):
    """一个「tap 之后 App 崩了」的最小失败现场。"""
    store = TraceStore(str(trace_db))
    store.start_run(run_id, suite="smoke", device_type="simulator",
                    app_build=app_build, app_bundle_id=app_id)
    tc = store.start_testcase(run_id, "pay_001")
    store.record_step(tc, 0, "tap", target_id="pay_button", status="SUCCESS")
    store.record_step(tc, 1, "wait_for", target_id="screen:PayDone",
                      status="FAILED", failure_type="APP_CRASH",
                      detail={"screen": "PayView"})
    store.end_testcase(tc, "FAIL")
    store.end_run(run_id, "FAILED", 1)
    return run_id


@pytest.fixture()
def proj(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    gen = tmp_path / "repository" / "generated" / "local"
    gen.mkdir(parents=True)
    (gen / "source_metadata.json").write_text(
        '{"app_version": "debug", "build": "local", "git_commit": "deadbeef",'
        ' "screens": [], "screen_elements": []}', encoding="utf-8")
    (tmp_path / "suites" / "smoke").mkdir(parents=True)
    return {"tmp": tmp_path, "gen": gen, "out": out,
            "trace_db": out / "trace.db", "agent_db": out / "agent.db",
            "graph_db": out / "graph.db", "exp_db": out / "experience.db",
            "suites_root": tmp_path / "suites"}


def _argv(proj, *extra):
    return ["diagnose", "--run-id", "run_crash",
            "--generated", str(proj["gen"]),
            "--suites-root", str(proj["suites_root"]),
            "--db", str(proj["trace_db"]),
            "--graph-db", str(proj["graph_db"]),
            "--agent-db", str(proj["agent_db"]),
            "--exp-db", str(proj["exp_db"]),
            "--bundle-id", "com.demo", "--no-llm", *extra]


# --- 1. 注册与前置 -------------------------------------------------------------


def test_diagnose_is_registered_and_run_id_is_required():
    from cli.main import build_parser
    args = build_parser().parse_args(["diagnose", "--run-id", "r1"])
    assert args.command == "diagnose" and args.run_id == "r1"
    assert args.step is None and args.base_build is None
    with pytest.raises(SystemExit):
        build_parser().parse_args(["diagnose"])


def test_production_is_refused(proj, capsys):
    with pytest.raises(SystemExit) as e:
        main(_argv(proj, "--env-kind", "production"))
    assert "F7" in str(e.value)


def test_unknown_run_id_is_a_preflight_error(proj, capsys):
    _seed_crash_run(proj["trace_db"])
    argv = [a for a in _argv(proj)]
    argv[argv.index("--run-id") + 1] = "run_nope"
    assert main(argv) == 3
    out = capsys.readouterr().out
    assert out.startswith("PREFLIGHT ERROR:") and "Traceback" not in out


def test_unreadable_trace_db_is_a_preflight_error(proj, capsys):
    """trace 库不存在/没初始化 → **干净的**前置错误，不是 traceback。

    M5 Gate 的 G2a 实测抓到：`--db` 指向一个还没有表的库时，
    `knowledge.trace_history` 抛 `ValueError`，它**不在** `build_context`
    外面那个 `except DiagnosisContextError` 里 → 一路冒到 `main()`，
    用户看到一屏 traceback 和 exit 1。

    既有的 `test_unknown_run_id_is_a_preflight_error` 抓不到这条：它先
    `_seed_crash_run` 把库建好了，走的是「库在、run 不在」那条路。
    """
    # 注意：**不**调 `_seed_crash_run`——trace 库压根不存在
    assert not proj["trace_db"].exists()
    assert main(_argv(proj)) == 3
    out = capsys.readouterr().out
    assert out.startswith("PREFLIGHT ERROR:") and "Traceback" not in out
    assert "trace" in out


def test_unknown_step_is_a_preflight_error(proj, capsys):
    _seed_crash_run(proj["trace_db"])
    assert main(_argv(proj, "--step", "99")) == 3
    assert "step_index" in capsys.readouterr().out


# --- 2. 主路径 -----------------------------------------------------------------


def test_crash_run_yields_a_bug_candidate_at_human_review(proj, capsys):
    """crash trace → 规则出假设 → `bug_candidates` 行 → `HUMAN_REVIEW`。"""
    _seed_crash_run(proj["trace_db"])
    assert main(_argv(proj)) == 0
    out = capsys.readouterr().out
    assert "diagnose: exit 0" in out
    rows = SQLiteAgentStore(proj["agent_db"]).list_bug_candidates()
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "HUMAN_REVIEW", "诊断后必须送上审核台"
    assert row["failure_type"] == "APP_CRASH"
    assert row["reviewer"] is None and row["reviewed_at"] is None, \
        "系统推进不写审核三列（F10）"
    assert any(e["kind"] == "crash_detected" for e in row["evidence"])
    # 崩溃单独 5 分 < 阈值 10 → 不自动归因（矩阵 #16 前半）
    assert row["attribution_hypothesis"] == "UNTRIAGED"


def test_reproduction_evidence_pushes_the_score_over_the_threshold(
        proj, capsys, monkeypatch):
    """**handover §9 的「5.6 开工前置①」**：复现证据必须并进归因的证据集。

    崩溃单独只值 5 分 < 阈值 10（矩阵 #16 前半）；设计 §8.3 让它配
    `reproducible_3_times`（5 分）才过线——**前提是复现证据真的进了同一份
    证据集**。早先 `cmd_diagnose` 先算归因、后跑复现，于是哪怕真的重跑了
    三次，落库的 `attribution_hypothesis` 仍是 `UNTRIAGED`（M5 Gate 实测：
    分数 10 但归因空，自相矛盾）。

    设备侧在这里用**注入**的 `ReproResult`（真机复现归 Gate 覆盖）。
    """
    import cli.main as cli_main
    from diagnosis.models import EvidenceItem
    from diagnosis.reproducer import ReproResult

    monkeypatch.setattr(
        cli_main, "_run_reproduction",
        lambda *a, **kw: ReproResult(
            reproducible=True, reproduction_count=3, attempts=3,
            evidence=(EvidenceItem(
                kind="reproducible_3_times",
                detail="3 次重跑全部复现了同样的失败",
                source="repro:testcase=pay_001"),)))
    _seed_crash_run(proj["trace_db"])
    assert main(_argv(proj)) == 0
    row = SQLiteAgentStore(proj["agent_db"]).list_bug_candidates()[0]
    kinds = {e["kind"] for e in row["evidence"]}
    assert kinds == {"crash_detected", "reproducible_3_times"}, kinds
    assert row["evidence_score"] == 10, row["evidence_score"]
    assert row["attribution_hypothesis"] == "APP_DEFECT"
    assert row["reproducible"] is True and row["reproduction_count"] == 3


def test_reproduction_evidence_is_not_counted_twice(proj, capsys,
                                                    monkeypatch):
    """反钉子：复现证据只并进**一次**（`_with_extra_evidence` 去重 + 分数
    只由 `score_evidence` 按类求和得出，不另加一份）。

    重复记账会让 `evidence` 列表里出现两条一模一样的证据（人工看 bundle
    时会以为有两个独立来源），也会让分数虚高。
    """
    import cli.main as cli_main
    from diagnosis.models import EvidenceItem
    from diagnosis.reproducer import ReproResult

    monkeypatch.setattr(
        cli_main, "_run_reproduction",
        lambda *a, **kw: ReproResult(
            reproducible=True, reproduction_count=3, attempts=3,
            evidence=(EvidenceItem(
                kind="reproducible_3_times", detail="d", source="s"),)))
    _seed_crash_run(proj["trace_db"])
    assert main(_argv(proj)) == 0
    row = SQLiteAgentStore(proj["agent_db"]).list_bug_candidates()[0]
    repro = [e for e in row["evidence"] if e["kind"] == "reproducible_3_times"]
    assert len(repro) == 1, repro
    assert row["evidence_score"] == 10, row["evidence_score"]


def test_history_corroboration_reaches_the_candidate(proj, capsys):
    """`same_issue_multiple_tests` 真的流到候选行（**同一个 run 内两个用例**）。

    M5 Gate 实测：权重表里这一项（3 分）从 M1 起就在，但**没有任何生产者**——
    真机漂移现场的分数上限因此只有 `source_change_correlated`(3) +
    `reproducible_3_times`(5) = 8，永远过不了阈值 10，
    `attribution_hypothesis` 恒为 `UNTRIAGED`（F10 的建议在真机上不可达）。

    ⚠️ 口径：**多个 `testcase_id` 撞上同一个 failure_type** 就算，不管它们是
    不是一个 run 里的——矩阵 #16 后半「三用例同根因 → 一条 Bug +
    `affected_tests` 累加」说的正是这个（同一个 run 里两个用例撞同一个失效，
    就是同一个 issue 影响了两个用例）。判据是「用例数」，不是「run 数」。
    """
    store = TraceStore(str(proj["trace_db"]))
    # 本 run：一个漂移现场（恢复失败 + reconcile 判 DRIFT）
    store.start_run("run_crash", suite="smoke", device_type="simulator",
                    app_build="local", app_bundle_id="com.demo")
    tc = store.start_testcase("run_crash", "gate_login")
    store.record_step(tc, 2, "input", target_id="LoginView.username_field",
                      status="FAILED", failure_type="ELEMENT_NOT_FOUND",
                      detail={"recovery_attempted": {
                          "recovery": "no_llm_engine",
                          "stages": [{"stage": "reconcile",
                                      "outcome": "DRIFT",
                                      "candidates": ["user_field"]}]}})
    store.end_testcase(tc, "FAIL")
    store.end_run("run_crash", "FAILED", 1)
    # 历史：**另一个用例**撞上同一个失败类型
    tc2 = store.start_testcase("run_crash", "gate_drift2")
    store.record_step(tc2, 2, "input", target_id="LoginView.username_field",
                      status="FAILED", failure_type="ELEMENT_NOT_FOUND",
                      detail={"recovery_attempted": {
                          "recovery": "no_llm_engine",
                          "stages": [{"stage": "reconcile",
                                      "outcome": "DRIFT",
                                      "candidates": ["user_field"]}]}})
    store.end_testcase(tc2, "FAIL")
    assert main(_argv(proj)) == 0
    row = SQLiteAgentStore(proj["agent_db"]).list_bug_candidates()[0]
    kinds = {e["kind"] for e in row["evidence"]}
    assert "same_issue_multiple_tests" in kinds, kinds
    assert "same_issue_multiple_tests" in capsys.readouterr().out
    # 3（漂移）+ 3（多用例）；设备不可用 → 无复现证据
    assert row["evidence_score"] == 6, row["evidence_score"]
    assert row["attribution_hypothesis"] == "UNTRIAGED", \
        "6 分 < 阈值 10——佐证只加分，不替人做决定（矩阵 #16 前半）"
    # **evidence 与 bundle 必须对得上**：证据说「命中 2 个用例」，
    # `affected_tests` 就得列 2 个。早先它只取 `ctx.steps[0]`（第一个用例），
    # 人工看 bundle 时会发现数字和列表不符。
    assert row["affected_tests"] == ["gate_drift2", "gate_login"], \
        row["affected_tests"]


def test_the_same_testcase_twice_is_not_multiple_tests(proj, capsys):
    """反钉子：**同一个**用例重跑过 → 不算「多个用例」。

    那是 `reproducible_3_times` 的地盘（重跑三次复现）。两条都给会把同一件事
    记两遍，分数虚高——`score_evidence` 的按类去重挡得住重复条目，挡不住
    「两个不同类别描述同一个事实」。
    """
    store = TraceStore(str(proj["trace_db"]))
    drift_detail = {"recovery_attempted": {
        "recovery": "no_llm_engine",
        "stages": [{"stage": "reconcile", "outcome": "DRIFT",
                    "candidates": ["user_field"]}]}}
    store.start_run("run_old", suite="smoke", device_type="simulator",
                    app_build="local", app_bundle_id="com.demo")
    old = store.start_testcase("run_old", "gate_login")   # 同一个用例名
    store.record_step(old, 2, "input", target_id="LoginView.username_field",
                      status="FAILED", failure_type="ELEMENT_NOT_FOUND",
                      detail=drift_detail)
    store.end_testcase(old, "FAIL")
    store.end_run("run_old", "FAILED", 1)

    store.start_run("run_crash", suite="smoke", device_type="simulator",
                    app_build="local", app_bundle_id="com.demo")
    tc = store.start_testcase("run_crash", "gate_login")
    store.record_step(tc, 2, "input", target_id="LoginView.username_field",
                      status="FAILED", failure_type="ELEMENT_NOT_FOUND",
                      detail=drift_detail)
    store.end_testcase(tc, "FAIL")
    store.end_run("run_crash", "FAILED", 1)

    assert main(_argv(proj)) == 0
    row = SQLiteAgentStore(proj["agent_db"]).list_bug_candidates()[0]
    kinds = {e["kind"] for e in row["evidence"]}
    assert "same_issue_multiple_tests" not in kinds, kinds
    assert row["evidence_score"] == 3, row["evidence_score"]


def test_history_from_another_run_gives_the_corroboration(proj, capsys):
    """另一个 **run** 的同类失败 → 佐证到位，漂移现场的分数从 3 抬到 6。

    `build_context` 只剔掉**本 run** 的步骤，别的 run 的同类失败留在
    `ctx.history` 里——那正是这条证据要抓的东西。
    """
    store = TraceStore(str(proj["trace_db"]))
    drift_detail = {"recovery_attempted": {
        "recovery": "no_llm_engine",
        "stages": [{"stage": "reconcile", "outcome": "DRIFT",
                    "candidates": ["user_field"]}]}}
    store.start_run("run_old", suite="smoke", device_type="simulator",
                    app_build="local", app_bundle_id="com.demo")
    old = store.start_testcase("run_old", "gate_drift2")
    store.record_step(old, 2, "input", target_id="LoginView.username_field",
                      status="FAILED", failure_type="ELEMENT_NOT_FOUND",
                      detail=drift_detail)
    store.end_testcase(old, "FAIL")
    store.end_run("run_old", "FAILED", 1)

    store.start_run("run_crash", suite="smoke", device_type="simulator",
                    app_build="local", app_bundle_id="com.demo")
    tc = store.start_testcase("run_crash", "gate_login")
    store.record_step(tc, 2, "input", target_id="LoginView.username_field",
                      status="FAILED", failure_type="ELEMENT_NOT_FOUND",
                      detail=drift_detail)
    store.end_testcase(tc, "FAIL")
    store.end_run("run_crash", "FAILED", 1)

    assert main(_argv(proj)) == 0
    row = SQLiteAgentStore(proj["agent_db"]).list_bug_candidates()[0]
    kinds = {e["kind"] for e in row["evidence"]}
    assert "same_issue_multiple_tests" in kinds, kinds
    # 3（漂移）+ 3（多用例）；设备不可用 → 无复现证据
    assert row["evidence_score"] == 6, row["evidence_score"]
    assert "gate_login" in row["affected_tests"]
    assert row["attribution_hypothesis"] == "UNTRIAGED", \
        "6 分 < 阈值 10——佐证只加分，不替人做决定（矩阵 #16 前半）"


def test_no_base_build_means_no_dedup_key(proj, capsys):
    """**口径登记**：没有 `--base-build` → 不写 `dedup_key`。

    三元组里的 `source_change` 来自 build-to-build 的 diff；没有基线时算出的
    key 是「半个三元组」，会把两次无关失败判成同根因（错误合并更贵）。
    """
    _seed_crash_run(proj["trace_db"])
    assert main(_argv(proj)) == 0
    row = SQLiteAgentStore(proj["agent_db"]).list_bug_candidates()[0]
    assert row["dedup_key"] is None


def test_rule_hit_does_not_call_llm(proj, capsys):
    """矩阵 #14：crash 场景规则命中 → LLM 零调用（即使给了 LLM 也走规则）。"""
    _seed_crash_run(proj["trace_db"])
    assert main(_argv(proj)) == 0
    out = capsys.readouterr().out
    assert "规则命中" in out


def test_no_hypotheses_is_still_exit_zero_and_writes_nothing(proj, capsys):
    """规则无结论 + `--no-llm` → exit 0，且**不落**空候选（F10 空证据闸门）。"""
    # 一条**非**崩溃、非断言、无 5xx、无漂移的失败 → 规则给不出结论
    store = TraceStore(str(proj["trace_db"]))
    store.start_run("run_crash", suite="smoke", device_type="simulator",
                    app_build="local", app_bundle_id="com.demo")
    tc = store.start_testcase("run_crash", "pay_001")
    store.record_step(tc, 0, "tap", target_id="pay_button", status="FAILED",
                      failure_type="ELEMENT_NOT_FOUND")
    store.end_testcase(tc, "FAIL")
    store.end_run("run_crash", "FAILED", 1)
    assert main(_argv(proj)) == 0
    out = capsys.readouterr().out
    assert "无假设产出" in out
    assert SQLiteAgentStore(proj["agent_db"]).list_bug_candidates() == []


def test_reproduction_is_recorded_as_unknown_without_a_device(proj, capsys):
    """设备不可用 → `reproducible=None`（**不伪装成** False）。

    单测环境没有 booted 模拟器/Appium，`_run_reproduction` 建栈失败 → 如实记
    「无法确认复现」，而不是「复现不了」。
    """
    _seed_crash_run(proj["trace_db"])
    assert main(_argv(proj)) == 0
    row = SQLiteAgentStore(proj["agent_db"]).list_bug_candidates()[0]
    assert row["reproducible"] is None
    assert row["reproduction_count"] == 0


# --- 3. 人工确认（`mta candidate review --kind bug accept`）→ CONFIRMED + Bundle --


def test_human_accept_confirms_and_writes_the_bundle(proj, capsys):
    """诊断 → `HUMAN_REVIEW` → 人工 ACCEPT → `CONFIRMED` + Evidence Bundle。

    这是 M5 的**闭环**：系统只送到审核台（F10 不自动归因），人点头才落
    `CONFIRMED` 与可交付的 Evidence Bundle。
    """
    _seed_crash_run(proj["trace_db"])
    assert main(_argv(proj)) == 0
    cid = SQLiteAgentStore(proj["agent_db"]).list_bug_candidates()[0][
        "candidate_id"]
    bundle_dir = proj["out"] / "bundles"
    rc = main(["candidate", "review", "accept", cid, "--kind", "bug",
               "--reviewer", "alice", "--db", str(proj["agent_db"]),
               "--bundle-dir", str(bundle_dir)])
    assert rc == 0
    assert "CONFIRMED" in capsys.readouterr().out
    row = SQLiteAgentStore(proj["agent_db"]).get_bug_candidate(cid)
    assert row["status"] == "CONFIRMED"
    assert row["reviewer"] == "alice" and row["review_decision"] == "ACCEPT"
    bundle = json.loads((bundle_dir / f"{cid}.json").read_text(encoding="utf-8"))
    assert bundle["status"] == "CONFIRMED" and bundle["kind"] == "bug"


def test_human_reject_needs_a_note(proj, capsys):
    _seed_crash_run(proj["trace_db"])
    assert main(_argv(proj)) == 0
    cid = SQLiteAgentStore(proj["agent_db"]).list_bug_candidates()[0][
        "candidate_id"]
    # reject 缺 --note → argparse required 挡在 handler 之前（exit 2）
    with pytest.raises(SystemExit):
        main(["candidate", "review", "reject", cid, "--kind", "bug",
              "--db", str(proj["agent_db"])])
