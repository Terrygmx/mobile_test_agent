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


def _step(*, recovered_kind=None, mechanism=None, recovery=None,
          attempted=None, extra=None) -> dict:
    """构造一条 steps 的 detail（**按管线真实形状**）。

    `mechanism`（= `recovery_kind`）与 `recovered_kind` 在真实恢复步骤上
    **同时存在**（`cli/pipeline.py` 的两条成功路径都写两个键）——fixture 只写
    一个就会造出生产不可能出现的形状，让按机制名判的分子与按标签判的分子
    看起来一样。review_p2_task43 P2-2 的探针表逐形状核过。
    """
    d = dict(extra or {})
    if mechanism:
        d["recovery_kind"] = mechanism
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
        _step(recovered_kind="RECOVERED_EXPERIENCE", mechanism="experience",
              recovery={"stages": [{"stage": "experience", "outcome": "hit"}]}),
        _step(recovered_kind="RECOVERED_LLM", mechanism="llm",
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
        _step(recovered_kind="RECOVERED_EXPERIENCE", mechanism="experience",
              recovery={"stages": []}),
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
                   mechanism="experience", recovery={"stages": []})]
    events = [_execution("e1", "FAILURE"), _execution("e2", "FAILURE"),
              _execution("e3", "SUCCESS")]
    m = compute_experience_metrics(step_details=steps, events=events)

    assert m.experience_resolved == 1
    assert m.resolution_rate == pytest.approx(1.0)


def test_by_recovered_kind_breakdown():
    """分类分布按 §10 标签分组；分子按**机制名**判——两者刻意不同。

    这里 4 步里 3 步是 Experience 救的（两条 `RECOVERED_EXPERIENCE` + 一条
    `RECOVERED_ASSERTION_TARGET`，后者的标签被上下文压过但机制仍是 experience）
    → 分子 3、分母 4。分布表照旧按 §10 标签拆成三档，读者能同时看到
    「谁救的」与「对使用者意味着什么」（review_p2_task43 P2-2）。
    """
    steps = [
        _step(recovered_kind="RECOVERED_EXPERIENCE", mechanism="experience",
              recovery={}),
        _step(recovered_kind="RECOVERED_EXPERIENCE", mechanism="experience",
              recovery={}),
        _step(recovered_kind="RECOVERED_LLM", mechanism="llm", recovery={}),
        _step(recovered_kind="RECOVERED_ASSERTION_TARGET",
              mechanism="experience", recovery={}),
    ]
    m = compute_experience_metrics(step_details=steps)

    assert m.by_recovered_kind == {"RECOVERED_EXPERIENCE": 2,
                                   "RECOVERED_LLM": 1,
                                   "RECOVERED_ASSERTION_TARGET": 1}
    assert m.experience_resolved == 3, "标签是断言目标、机制仍是 experience"
    assert m.resolution_rate == pytest.approx(0.75)


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
    """延迟分段：cache 与 store 按 source 分组，llm 取 `llm_call` stage。

    ⚠️ 本测试断言 `cache < store < llm` 用的是**自己注入的数据**
    （1/3ms vs 10/30ms vs 400ms），**不是**「系统真的满足该梯度」的证据——
    纯函数测试证明不了系统行为（review_p2_task43 P3-6）。真实梯度要等基线
    数据积累后从产物里读（设计 §17：「先建基线，不预设绝对数值」）。
    """
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


def test_latency_unmeasurable_segments_are_none_not_zero():
    """不可测的段值是 **None**，不是 0（渲染层标 N/A）。

    填 0 会被读成「快到测不出」，而实际是「没有这个计时点」——那是另一种谎。

    名字里的「不可测」指 `deterministic`（纯进程内，未单独计时）与
    `promoted`（P1 find 链，耗时在整步里）——它们**根本不在 dict 的键里**，
    由渲染层硬编码成 N/A 行。而 `cache` / `store` / `llm` 三个键**始终在**
    （值可能为 None，表示「这一段没有样本」）。两种「没有」不是一回事，
    早先的测试名把它们混为一谈（review_p2_task43 P3-6）。
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
                            mechanism="experience", recovery={})]))
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
                 (json.dumps({"recovery_kind": "experience",
                              "recovered_kind": "RECOVERED_EXPERIENCE",
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


# --- review_p2_task43 的修订钉子（2×P2 + 4×P3） -----------------------------


def test_promotion_rate_numerator_is_limited_to_verified():
    """P2-1：分子限定 VERIFIED，否则 §9.4 场景能算出 **300%**。

    设计 §17 的定义是「Verified 中被 Promote 的比例」。一条 promoted 的经验
    被滑动窗口降级后（§9.4：**不自动撤销**已进 Git 的策略），它仍在
    `promoted` 里却不在 VERIFIED 里——分子不限定就会报出 >100% 的「比例」。
    """
    exps = [
        _exp("e1", ExperienceStatus.VERIFIED, promoted=True),
        _exp("e2", ExperienceStatus.DEGRADED, promoted=True),
        _exp("e3", ExperienceStatus.REJECTED, promoted=True),
    ]
    m = compute_experience_metrics(experiences=exps)

    assert m.status_counts["VERIFIED"] == 1
    assert m.promoted == 1, "只有 VERIFIED 的那条进分子"
    assert m.promotion_rate == pytest.approx(1.0)
    assert _pct_of(m.promotion_rate) <= "100.0%", "比例不得超过 100%"


def _pct_of(x: float | None) -> str:
    return "N/A" if x is None else f"{x * 100:.1f}%"


def test_promotion_rate_render_never_exceeds_100_percent():
    """P2-1 的渲染面：产物里不该出现 `300.0%` 这种数字。"""
    exps = [_exp("e1", ExperienceStatus.VERIFIED, promoted=True),
            _exp("e2", ExperienceStatus.DEGRADED, promoted=True),
            _exp("e3", ExperienceStatus.REJECTED, promoted=True)]
    html = render_experience_metrics_section(
        compute_experience_metrics(experiences=exps))
    assert "300.0%" not in html and "200.0%" not in html
    assert "100.0%" in html


def test_resolution_counts_assertion_target_recovered_by_experience():
    """P2-2：分子按**机制名**判，不按 §10 的分类标签。

    断言目标漂移的上下文（`context="assertion_target"`）把机制名盖成
    `RECOVERED_ASSERTION_TARGET`，而那条路径正是「aux 命中 Experience」
    （矩阵 #15）。用标签当分子会把这一整类记 0——设计 §17 的核心指标对整整
    一类 Experience 恢复失明。
    """
    steps = [
        # aux 断言路径：机制是 experience，但分类标签被上下文压过
        _step(extra={"recovery_kind": "experience",
                     "recovered_kind": "RECOVERED_ASSERTION_TARGET",
                     "recovery_context": "assertion_target",
                     "recovery": {"stages": []}}),
        # 对照：同机制、非断言上下文
        _step(extra={"recovery_kind": "experience",
                     "recovered_kind": "RECOVERED_EXPERIENCE",
                     "recovery": {"stages": []}}),
    ]
    m = compute_experience_metrics(step_details=steps)

    assert m.recovery_attempts == 2
    assert m.experience_resolved == 2, "两条都是 Experience 救的"
    assert m.resolution_rate == pytest.approx(1.0)
    assert m.by_recovered_kind == {"RECOVERED_ASSERTION_TARGET": 1,
                                   "RECOVERED_EXPERIENCE": 1}, \
        "分类标签分布照旧按 §10 的标签分组（与分子判据不同，docstring 已说明）"


def test_aux_rerun_failure_is_not_counted_as_resolved():
    """P2-2 的第二个条件：`recovery_kind` 有但 `recovered_kind` 无 = 没恢复。

    `_aux_rerun_failed` 在**失败**步骤上写 `recovery_kind`（那是事实），
    只判机制名会把失败算成「已解决」。
    """
    steps = [_step(extra={"recovery_kind": "experience",
                          "recovery_context": "aux_rerun_failed",
                          "recovery": {"stages": []}})]
    m = compute_experience_metrics(step_details=steps)
    assert m.recovery_attempts == 1 and m.experience_resolved == 0


def test_revalidation_attempts_ignores_non_transition_markers():
    """P3-1：`REVALIDATION_REQUIRED` 是 from==to 的**非跳变标记**，不算降级。

    E8 的标记刻意写同值行；把 `to_status == "DEGRADED"` 当判据会把当前正处
    DEGRADED 的那条也算成一次「降级尝试」→ 真降级 1 次报成 2 次、成功率
    100% 掉成 50%（降级越频繁偏差越大，方向恒为低估）。
    """
    state_events = [
        {"from_status": "VERIFIED", "to_status": "DEGRADED",
         "reason": "SLIDING_WINDOW"},
        {"from_status": "DEGRADED", "to_status": "DEGRADED",
         "reason": "REVALIDATION_REQUIRED"},     # 非跳变标记，不算
        {"from_status": "DEGRADED", "to_status": "VERIFIED",
         "reason": "REVALIDATED"},
    ]
    m = compute_experience_metrics(state_events=state_events)

    assert m.revalidation_attempts == 1
    assert m.revalidation_successes == 1
    assert m.revalidation_rate == pytest.approx(1.0)


def test_is_state_transition_matches_the_store_sql_predicate():
    """P3-1：跳变判据单点（与 `has_state_event_since_transition` 的 SQL 同义）。"""
    from experience.verifier import is_state_transition

    assert is_state_transition(None, "CANDIDATE") is True, "首条事件无前态"
    assert is_state_transition("CANDIDATE", "VERIFIED") is True
    assert is_state_transition("DEGRADED", "DEGRADED") is False, "非跳变标记"


def test_render_escapes_values_from_the_trace_db():
    """P3-4：trace 库是**可写的外部输入**，渲染必须转义。

    报告会被打开在浏览器里；`steps.detail_json` 里的 `recovered_kind` 值
    原样进 HTML 就是注入面。
    """
    evil = "<script>alert(1)</script>"
    m = compute_experience_metrics(step_details=[
        _step(recovered_kind=evil, recovery={})])
    html = render_experience_metrics_section(m)

    assert evil not in html, "原样出现即为注入"
    assert "&lt;script&gt;" in html


def test_latency_accepts_float_milliseconds():
    """P3-2：延迟是**浮点毫秒**（不再被 `int()` 截断成 0）。

    亚毫秒的 cache/store 操作若一律记 0，设计 §17 的「Cache < Store」梯度
    就成了 `0 < 0`——指标在 ms 粒度上不可验证。
    """
    events = [_lookup("cache", 0.004), _lookup("cache", 0.008),
              _lookup("store", 0.42), _lookup("store", 0.58)]
    m = compute_experience_metrics(events=events)

    assert m.latency["cache"] == pytest.approx(0.006)
    assert m.latency["store"] == pytest.approx(0.5)
    assert m.latency["cache"] < m.latency["store"], "梯度可分辨"


def test_render_shows_sub_millisecond_values_not_zero():
    """P3-2 的渲染面：亚毫秒要看得见，不能显示成 `0.0ms`。"""
    m = compute_experience_metrics(events=[_lookup("cache", 0.004)])
    html = render_experience_metrics_section(m)

    assert "0.004ms" in html, "三位小数保留亚毫秒"
    assert "0.0ms" not in html.replace("0.004ms", ""), \
        "`0.0ms` 只该出现在真的测不到时"
