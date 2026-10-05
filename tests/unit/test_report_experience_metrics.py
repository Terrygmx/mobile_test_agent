"""Task 4.3 / 设计 17：Experience 指标聚合（纯函数）+ 报告渲染。

plan step 1 的核心断言：**Resolution Rate 的分母 = 全部 Recovery Attempts**
——含「Experience MISS 后回落 LLM 的」，也含「因缺键/缺页面根本没查库的」。
后者是本次实现相对 plan 字面（「数据源为 trace `experience_*` 事件」）的扩展：
`experience_lookup` 事件只覆盖真的查了库的尝试，拿它当分母会系统性高估命中率。
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from experience.models import CandidateSeed, ExperienceStatus
from experience.store import SQLiteExperienceStore
from repository.loader import LocatorStrategy
from report.experience_metrics import (
    ExperienceMetrics,
    collect_experience_metrics,
    compute_experience_metrics,
    entered_recovery,
    render_experience_metrics_section,
)

APP = "com.phaset0.logindemo"


# --- 构造原始行（都是「已经取出来的行」，纯函数不吃别的） --------------------


def _step(*, recovered_kind=None, recovery=None, attempted=None,
          extra=None) -> dict:
    d = dict(extra or {})
    if recovered_kind:
        d["recovered_kind"] = recovered_kind
    if recovery is not None:
        d["recovery"] = recovery
    if attempted is not None:
        d["recovery_attempted"] = attempted
    return d


def _lookup(source: str, latency_ms: int) -> dict:
    return {"event_type": "experience_lookup",
            "detail": {"app_id": APP, "screen_id": "LoginView",
                       "target_id": "username_field", "candidates": 1,
                       "source": source, "latency_ms": latency_ms}}


def _execution(exp_id: str, execution: str = "SUCCESS") -> dict:
    return {"event_type": "experience_execution",
            "detail": {"experience_id": exp_id, "execution": execution,
                       "result": "RECOVERED_EXPERIENCE"}}


def _exp(exp_id: str, status: ExperienceStatus = ExperienceStatus.CANDIDATE,
         *, promoted: bool = False) -> object:
    from datetime import datetime, timezone
    from experience.models import Experience
    return Experience(
        experience_id=exp_id, app_id=APP, screen_id="LoginView",
        target_id="username_field",
        strategy=LocatorStrategy(type="accessibility_id", value="u_v2",
                                 origin="experience"),
        origin="LLM_ACCEPTED_RECOVERY", status=status,
        seed_run_id="run_seed", seed_step_id=1, seed_recovery_review_id=1,
        promoted=promoted,
        promoted_commit=("abc1234" if promoted else None),
        created_at=datetime(2026, 10, 5, tzinfo=timezone.utc))


# --- ① Resolution Rate 的分母口径（本任务的核心） ---------------------------


def test_miss_then_llm_counts_in_denominator_not_numerator():
    """Experience MISS 后回落 LLM 的步骤：**计入分母、不计入分子**。

    这是 plan step 1 点名的口径。若分母只算「命中过 Experience 的步骤」，
    这条会被整条漏掉，命中率被系统性高估。
    """
    steps = [
        _step(recovered_kind="RECOVERED_EXPERIENCE",
              recovery={"stages": [{"stage": "experience", "outcome": "hit"}]}),
        _step(recovered_kind="RECOVERED_LLM",
              attempted={"stages": [
                  {"stage": "experience", "outcome": "miss"},
                  {"stage": "llm", "outcome": "candidate"}]}),
    ]
    m = compute_experience_metrics(step_details=steps)

    assert m.recovery_attempts == 2, "两条都进过分母"
    assert m.experience_resolved == 1
    assert m.resolution_rate == pytest.approx(0.5)


def test_attempts_that_never_queried_the_store_still_count():
    """缺键 / 无 store / 读不到页面 → 也是 Recovery Attempt（进分母）。

    这四类**没查库**（Task 2.4 明文不发 `experience_lookup` 事件），若分母取
    事件数就整批漏掉——所以分母取 trace `steps` 的恢复标记。
    """
    steps = [
        _step(attempted={"stages": [{"stage": "experience",
                                     "outcome": "incomplete_key"}]}),
        _step(attempted={"stages": [{"stage": "experience",
                                     "outcome": "no_page_source"}]}),
        _step(recovered_kind="RECOVERED_EXPERIENCE", recovery={"stages": []}),
    ]
    m = compute_experience_metrics(step_details=steps)

    assert m.recovery_attempts == 3
    assert m.experience_resolved == 1


def test_aux_rerun_failure_counts_as_attempt_without_classification():
    """aux 重跑失败的步骤：`recovery_kind` 有、`recovered_kind` 无。

    它进过分母（确实尝试过恢复），但不进分子（终态 FAILED，§10 的分类标签
    只盖在成功恢复的步骤上）。
    """
    steps = [_step(extra={"recovery_kind": "experience",
                          "recovery_context": "aux_rerun_failed",
                          "recovery": {"stages": []}})]
    m = compute_experience_metrics(step_details=steps)

    assert m.recovery_attempts == 1
    assert m.experience_resolved == 0
    assert m.resolution_rate == 0.0, "有分母时 0 是**真实**的 0（全没解决）"


def test_non_recovery_steps_are_not_attempts():
    steps = [{"status": "PASS"}, {"error": "boom"}, {}]
    m = compute_experience_metrics(step_details=steps)
    assert m.recovery_attempts == 0
    assert m.resolution_rate is None, \
        "没跑过 → N/A；写成 0.0 会被读成「跑了全失败」"


def test_resolution_rate_is_step_level_not_candidate_level():
    """一次尝试里试了 3 条候选、最后第 3 条救回 → 分子只 +1。

    候选级的 execution 事件会有 3 条（1 SUCCESS + 2 FAILURE），拿它当分子
    会把「一次恢复」算成多次，率超过 1。
    """
    steps = [_step(recovered_kind="RECOVERED_EXPERIENCE",
                   recovery={"stages": []})]
    events = [_execution("e1", "FAILURE"), _execution("e2", "FAILURE"),
              _execution("e3", "SUCCESS")]
    m = compute_experience_metrics(step_details=steps, events=events)

    assert m.experience_resolved == 1
    assert m.resolution_rate == pytest.approx(1.0)


def test_by_recovered_kind_breakdown():
    steps = [
        _step(recovered_kind="RECOVERED_EXPERIENCE", recovery={}),
        _step(recovered_kind="RECOVERED_EXPERIENCE", recovery={}),
        _step(recovered_kind="RECOVERED_LLM", recovery={}),
        _step(recovered_kind="RECOVERED_ASSERTION_TARGET", recovery={}),
    ]
    m = compute_experience_metrics(step_details=steps)

    assert m.by_recovered_kind == {"RECOVERED_EXPERIENCE": 2,
                                   "RECOVERED_LLM": 1,
                                   "RECOVERED_ASSERTION_TARGET": 1}
    assert m.resolution_rate == pytest.approx(0.5)


def test_entered_recovery_marker_set():
    """四个标记键任意一个即算尝试（管线四条互斥写入路径）。"""
    for key in ("recovery", "recovery_attempted", "recovered_kind",
                "recovery_kind"):
        assert entered_recovery({key: 1}) is True
    assert entered_recovery({"status": "FAIL"}) is False
    assert entered_recovery(None) is False


# --- ②③ 状态计数与 Promotion Rate -------------------------------------------


def test_status_counts_and_promotion_rate():
    exps = [
        _exp("e1", ExperienceStatus.VERIFIED),
        _exp("e2", ExperienceStatus.VERIFIED, promoted=True),
        _exp("e3", ExperienceStatus.CANDIDATE),
        _exp("e4", ExperienceStatus.DEGRADED),
        _exp("e5", ExperienceStatus.REJECTED),
    ]
    m = compute_experience_metrics(experiences=exps)

    assert m.status_counts == {"CANDIDATE": 1, "VERIFIED": 2, "DEGRADED": 1,
                               "REJECTED": 1}
    assert m.promoted == 1
    assert m.promotion_rate == pytest.approx(0.5)


def test_promotion_rate_none_without_verified():
    m = compute_experience_metrics(experiences=[_exp("e1")])
    assert m.promotion_rate is None, "没有 VERIFIED → 分母为 0 → N/A"


def test_promoted_but_recovering_flags_reverted_promotions():
    """review_p2_task42 建议动作 #2：区分「revert 后的预期恢复」与真回归。

    promoted=True 却仍在产生成功的 execution → 其策略没在 find 链生效
    （很可能已 revert）。这是**推断**，所以只做计数 + 报告里注明依据。
    """
    exps = [_exp("e1", ExperienceStatus.VERIFIED, promoted=True),
            _exp("e2", ExperienceStatus.VERIFIED, promoted=True),
            _exp("e3", ExperienceStatus.VERIFIED)]
    events = [_execution("e1", "SUCCESS"),        # e1 仍在被恢复 → 可疑
              _execution("e3", "FAILURE")]        # 失败不算「命中」
    m = compute_experience_metrics(experiences=exps, events=events)

    assert m.promoted == 2
    assert m.promoted_but_recovering == 1


# --- ④ 延迟梯度 -------------------------------------------------------------


def test_latency_gradient_splits_cache_and_store():
    events = [_lookup("store", 30), _lookup("store", 10),
              _lookup("cache", 1), _lookup("cache", 3)]
    steps = [_step(attempted={"stages": [
        {"stage": "llm_call", "outcome": "ok", "latency_ms": 400}]})]
    m = compute_experience_metrics(step_details=steps, events=events)

    assert m.latency["cache"] == pytest.approx(2.0)
    assert m.latency["store"] == pytest.approx(20.0)
    assert m.latency["llm"] == pytest.approx(400.0)
    assert m.latency_samples == {"cache": 2, "store": 2, "llm": 1}
    assert m.latency["cache"] < m.latency["store"] < m.latency["llm"]


def test_latency_unmeasurable_segments_are_absent_not_zero():
    """不可测的段**不出现**在 dict 里（渲染层标 N/A）。

    填 0 会被读成「快到测不出」，而实际是「没有这个计时点」——那是另一种谎。
    `deterministic`（纯进程内）与 `promoted`（P1 find 链）都不在本表。
    """
    m = compute_experience_metrics()
    assert m.latency == {"cache": None, "store": None, "llm": None}
    assert "deterministic" not in m.latency and "promoted" not in m.latency


def test_latency_ignores_non_int_latency():
    events = [{"event_type": "experience_lookup",
               "detail": {"source": "store", "latency_ms": None}}]
    m = compute_experience_metrics(events=events)
    assert m.latency["store"] is None and m.latency_samples["store"] == 0


# --- ⑤ Revalidation 成功率 --------------------------------------------------


def test_revalidation_rate():
    state_events = [
        {"to_status": "DEGRADED", "reason": "SLIDING_WINDOW"},
        {"to_status": "VERIFIED", "reason": "REVALIDATED"},
        {"to_status": "DEGRADED", "reason": "SLIDING_WINDOW"},
        {"to_status": "DEGRADED", "reason": "SLIDING_WINDOW"},
        {"to_status": "VERIFIED", "reason": "REVALIDATED"},
    ]
    m = compute_experience_metrics(state_events=state_events)
    assert m.revalidation_attempts == 3
    assert m.revalidation_successes == 2
    assert m.revalidation_rate == pytest.approx(2 / 3)


def test_revalidation_rate_none_when_never_degraded():
    m = compute_experience_metrics(state_events=[
        {"to_status": "VERIFIED", "reason": "THRESHOLD_MET"}])
    assert m.revalidation_attempts == 0
    assert m.revalidation_rate is None


# --- 渲染 -------------------------------------------------------------------


def test_render_shows_na_for_missing_denominator_and_revert_note():
    m = compute_experience_metrics(experiences=[
        _exp("e1", ExperienceStatus.VERIFIED, promoted=True)],
        events=[_execution("e1", "SUCCESS")])
    html = render_experience_metrics_section(m)

    assert "Resolution Rate" in html and "N/A" in html
    assert "0 / 0" in html, "分子分母都要显示，别只给一个比率"
    assert "已 Promote 但仍在产生恢复命中" in html
    assert "<strong>\n1</strong>" in html, "推断计数要出现在报告里"
    assert "git revert" in html, "报告要说明这是「预期恢复」而非新回归"
    assert "推断" in html, "启发式结论必须标注为推断"


def test_render_lists_unmeasurable_segments_with_reason():
    html = render_experience_metrics_section(compute_experience_metrics())
    assert "deterministic" in html and "promoted" in html
    assert "不同量级" in html, "要写清为什么不把 promoted 放进梯度表"


def test_render_run_report_includes_metrics_only_when_given():
    from runner.result import RunResult
    from report.html import render_run_report

    run = RunResult(run_id="run_x", suite="s")
    assert "Experience 指标" not in render_run_report(run)
    html = render_run_report(run, experience_metrics=compute_experience_metrics(
        step_details=[_step(recovered_kind="RECOVERED_EXPERIENCE",
                            recovery={})]))
    assert "Experience 指标（设计 17）" in html
    assert "Resolution Rate" in html


# --- 采集（I/O 区）端到端 ---------------------------------------------------


def _trace_db(tmp_path) -> str:
    """最小 trace 库：steps + infra_events（真实列名）。"""
    db = tmp_path / "trace.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE steps (id INTEGER PRIMARY KEY, detail_json TEXT);
        CREATE TABLE infra_events (id INTEGER PRIMARY KEY, event_type TEXT,
                                   detail_json TEXT);
    """)
    conn.execute("INSERT INTO steps (detail_json) VALUES (?)",
                 (json.dumps({"recovered_kind": "RECOVERED_EXPERIENCE",
                              "recovery": {"stages": [
                                  {"stage": "llm_call", "latency_ms": 250}]}}),))
    conn.execute("INSERT INTO steps (detail_json) VALUES (?)",
                 (json.dumps({"recovery_attempted": {"stages": [
                     {"stage": "experience", "outcome": "miss"}]}}),))
    conn.execute("INSERT INTO steps (detail_json) VALUES (?)",
                 (json.dumps({"status": "PASS"}),))
    conn.execute("INSERT INTO infra_events (event_type, detail_json)"
                 " VALUES (?,?)",
                 ("experience_lookup",
                  json.dumps({"source": "store", "latency_ms": 12,
                              "candidates": 1})))
    conn.commit()
    conn.close()
    return str(db)


def test_collect_end_to_end(tmp_path):
    store = SQLiteExperienceStore(tmp_path / "exp.db")
    store.create_candidate(CandidateSeed(
        review_id=1, recovery_id=1, seed_run_id="run_seed", seed_step_id=1,
        seed_recovery_review_id=1, app_id=APP, screen_id="LoginView",
        target_id="username_field",
        strategy=LocatorStrategy(type="accessibility_id", value="u_v2",
                                 origin="experience")))

    m = collect_experience_metrics(trace_db=_trace_db(tmp_path),
                                   experience_store=store)

    assert m.recovery_attempts == 2
    assert m.experience_resolved == 1
    assert m.resolution_rate == pytest.approx(0.5)
    assert m.latency["store"] == pytest.approx(12.0)
    assert m.latency["llm"] == pytest.approx(250.0)
    assert m.status_counts["CANDIDATE"] == 1
    assert m.events.get("experience_lookup") == 1


def test_collect_reads_state_events_for_revalidation(tmp_path):
    store = SQLiteExperienceStore(tmp_path / "exp.db")
    exp = store.create_candidate(CandidateSeed(
        review_id=1, recovery_id=1, seed_run_id="run_seed", seed_step_id=1,
        seed_recovery_review_id=1, app_id=APP, screen_id="LoginView",
        target_id="username_field",
        strategy=LocatorStrategy(type="accessibility_id", value="u_v2",
                                 origin="experience")))
    store.update_status(exp.experience_id, ExperienceStatus.VERIFIED, "M")
    store.update_status(exp.experience_id, ExperienceStatus.DEGRADED, "W")
    store.update_status(exp.experience_id, ExperienceStatus.VERIFIED,
                        "REVALIDATED")

    m = collect_experience_metrics(trace_db=_trace_db(tmp_path),
                                   experience_store=store)

    assert m.revalidation_attempts == 1
    assert m.revalidation_successes == 1
    assert m.revalidation_rate == pytest.approx(1.0)
    assert m.status_counts["VERIFIED"] == 1


# --- junit（plan Files 清单里的一项：明细带 recovered_kind） -----------------


def test_junit_detail_carries_recovered_kind():
    """plan 要求 junit 明细带 `recovered_kind`——Task 2.4 已落地，此处钉住。"""
    from runner.result import RunResult, TestcaseResult
    from report.junit import junit_xml

    run = RunResult(run_id="run_j", suite="s")
    run.results.append(TestcaseResult(
        testcase_id="login_001", status="RECOVERED",
        detail={"recovered_kinds": ["RECOVERED_EXPERIENCE"]}))
    xml = junit_xml(run)
    assert "recovered_kind=RECOVERED_EXPERIENCE" in xml


def test_metrics_dataclass_defaults_are_empty_not_fake():
    m = ExperienceMetrics()
    assert m.resolution_rate is None and m.promotion_rate is None
    assert m.revalidation_rate is None
    assert m.status_counts == {} and m.recovery_attempts == 0


# --- CLI 端到端：`mta run --html` 真的带出指标段 -----------------------------


def test_cli_run_html_includes_metrics_when_exp_db_exists(tmp_path):
    """G10 的可观测面：报告首页必须真的出现指标段。

    单独测渲染函数不够——`_collect_metrics_if_any` 的接线（库存在才采集、
    采集喂给报告）才是「指标可观测」的落点。
    """
    from cli.main import main

    sdir = tmp_path / "suites"
    sdir.mkdir()
    (sdir / "plain_001.yaml").write_text(
        'schema_version: "0.2"\nid: login_001\nname: 登录\nsuite: smoke\n'
        "steps:\n  - action: launch_app\n", encoding="utf-8")

    exp_db = tmp_path / "exp.db"
    SQLiteExperienceStore(exp_db).create_candidate(CandidateSeed(
        review_id=1, recovery_id=1, seed_run_id="run_seed", seed_step_id=1,
        seed_recovery_review_id=1, app_id=APP, screen_id="LoginView",
        target_id="username_field",
        strategy=LocatorStrategy(type="accessibility_id", value="u_v2",
                                 origin="experience")))
    html = tmp_path / "report.html"

    code = main(["run", "--case", "login_001", "--suites-root", str(sdir),
                 "--db", str(tmp_path / "trace.db"), "--fake-driver",
                 "--no-llm", "--exp-db", str(exp_db), "--html", str(html)])

    assert code == 0
    body = html.read_text(encoding="utf-8")
    assert "Experience 指标（设计 17）" in body
    assert "Resolution Rate" in body
    assert "CANDIDATE" in body and "1" in body, "库里的候选数要出现在状态计数里"


def test_cli_run_html_omits_metrics_when_exp_db_absent(tmp_path):
    """库不存在 → 不采集、不构造（最小副作用），报告不带指标段。"""
    from cli.main import main

    sdir = tmp_path / "suites"
    sdir.mkdir()
    (sdir / "plain_001.yaml").write_text(
        'schema_version: "0.2"\nid: login_001\nname: 登录\nsuite: smoke\n'
        "steps:\n  - action: launch_app\n", encoding="utf-8")
    exp_db = tmp_path / "never_used.db"
    html = tmp_path / "report.html"

    code = main(["run", "--case", "login_001", "--suites-root", str(sdir),
                 "--db", str(tmp_path / "trace.db"), "--fake-driver",
                 "--no-llm", "--exp-db", str(exp_db), "--html", str(html)])

    assert code == 0
    assert not exp_db.exists(), "报告不该顺手把空库建出来"
    assert "Experience 指标" not in html.read_text(encoding="utf-8")


# --- CLI 端到端：`mta run --html` 真的带出指标段 -----------------------------


def test_cli_run_html_includes_metrics_when_exp_db_exists(tmp_path):
    """G10 的可观测面：报告首页必须真的出现指标段。

    单独测渲染函数不够——`_collect_metrics_if_any` 的接线（库存在才采集、
    采集喂给报告）才是「指标可观测」的落点。
    """
    from cli.main import main

    sdir = tmp_path / "suites"
    sdir.mkdir()
    (sdir / "plain_001.yaml").write_text(
        'schema_version: "0.2"\nid: login_001\nname: 登录\nsuite: smoke\n'
        "steps:\n  - action: launch_app\n", encoding="utf-8")

    exp_db = tmp_path / "exp.db"
    SQLiteExperienceStore(exp_db).create_candidate(CandidateSeed(
        review_id=1, recovery_id=1, seed_run_id="run_seed", seed_step_id=1,
        seed_recovery_review_id=1, app_id=APP, screen_id="LoginView",
        target_id="username_field",
        strategy=LocatorStrategy(type="accessibility_id", value="u_v2",
                                 origin="experience")))
    html = tmp_path / "report.html"

    code = main(["run", "--case", "login_001", "--suites-root", str(sdir),
                 "--db", str(tmp_path / "trace.db"), "--fake-driver",
                 "--no-llm", "--exp-db", str(exp_db), "--html", str(html)])

    assert code == 0
    body = html.read_text(encoding="utf-8")
    assert "Experience 指标（设计 17）" in body
    assert "Resolution Rate" in body
    assert "CANDIDATE" in body and "1" in body, "库里的候选数要出现在状态计数里"


def test_cli_run_html_omits_metrics_when_exp_db_absent(tmp_path):
    """库不存在 → 不采集、不构造（最小副作用），报告不带指标段。"""
    from cli.main import main

    sdir = tmp_path / "suites"
    sdir.mkdir()
    (sdir / "plain_001.yaml").write_text(
        'schema_version: "0.2"\nid: login_001\nname: 登录\nsuite: smoke\n'
        "steps:\n  - action: launch_app\n", encoding="utf-8")
    exp_db = tmp_path / "never_used.db"
    html = tmp_path / "report.html"

    code = main(["run", "--case", "login_001", "--suites-root", str(sdir),
                 "--db", str(tmp_path / "trace.db"), "--fake-driver",
                 "--no-llm", "--exp-db", str(exp_db), "--html", str(html)])

    assert code == 0
    assert not exp_db.exists(), "报告不该顺手把空库建出来"
    assert "Experience 指标" not in html.read_text(encoding="utf-8")
