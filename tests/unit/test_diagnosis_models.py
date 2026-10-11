"""Task 5.1 / P3-20：诊断值对象 + 上下文装配（设计 §8.1/§8.3/§11）。

plan Steps 的判据：
1. **Hypothesis 无 evidence → 拒绝**（F10 第一道防线）；
2. `attribution_hypothesis` 默认 `UNTRIAGED`（系统不自动归因）；
3. **context 各字段来源可追溯**（import 级断言：不直接 SQL，只经
   KnowledgeSources/既有 API）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agents.policy_config import DIAGNOSIS_EVIDENCE_WEIGHTS
from diagnosis.models import (ATTRIBUTIONS, BUG_SEVERITIES, BUG_STATUSES,
                              EVIDENCE_KINDS, HYPOTHESIS_ORIGINS,
                              BugCandidateDraft, EvidenceItem, Hypothesis)


# --- EvidenceItem ---------------------------------------------------------------


def test_evidence_kinds_are_derived_from_the_weight_table():
    """证据类别 = 权重表的键集（**派生**，不是手抄——两处永不漂移）。"""
    assert set(EVIDENCE_KINDS) == set(DIAGNOSIS_EVIDENCE_WEIGHTS)
    assert len(EVIDENCE_KINDS) == 6


def test_unknown_evidence_kind_fails_loud():
    """拼错的 kind 必须当场报错——静默加 0 分是最难查的一类。"""
    with pytest.raises(ValueError, match="未知证据类别"):
        EvidenceItem(kind="crashed")          # 真值是 crash_detected


def test_evidence_carries_its_source():
    """证据要能追溯（`source` 记「从哪来」）。"""
    e = EvidenceItem(kind="crash_detected", detail="app 进程消失",
                     source="trace:run_1/step_3")
    assert e.source == "trace:run_1/step_3"


# --- Hypothesis：F10 的第一道防线 -----------------------------------------------


def test_hypothesis_without_evidence_is_rejected():
    """**空 evidence 拒绝创建**（F10：没有证据的假设与「LLM 觉得」没有区别）。"""
    with pytest.raises(ValueError, match="必须至少有一条 evidence"):
        Hypothesis(statement="可能是 App 崩溃", confidence=0.9, evidence=[],
                   origin="rule_based")


def test_hypothesis_with_evidence_is_accepted():
    h = Hypothesis(
        statement="App 在支付页崩溃", confidence=0.8,
        evidence=[EvidenceItem(kind="crash_detected", detail="进程消失")],
        origin="rule_based")
    assert h.evidence[0].kind == "crash_detected"


def test_blank_statement_is_rejected():
    with pytest.raises(ValueError, match="statement 不能为空"):
        Hypothesis(statement="   ", confidence=0.5,
                   evidence=[EvidenceItem(kind="crash_detected")],
                   origin="rule_based")


def test_confidence_must_be_a_probability():
    with pytest.raises(ValueError):
        Hypothesis(statement="x", confidence=1.5,
                   evidence=[EvidenceItem(kind="crash_detected")],
                   origin="rule_based")


def test_unknown_origin_is_rejected():
    with pytest.raises(ValueError, match="未知 origin"):
        Hypothesis(statement="x", confidence=0.5,
                   evidence=[EvidenceItem(kind="crash_detected")],
                   origin="llm")


def test_llm_origin_is_a_registered_value():
    """设计 §8.2 末段：LLM 补充的假设标 `llm_hypothesis`（权重最低那档）。"""
    assert "llm_hypothesis" in HYPOTHESIS_ORIGINS
    assert set(HYPOTHESIS_ORIGINS) == {"rule_based", "llm_hypothesis"}


# --- BugCandidateDraft：系统不自动归因（F10） ------------------------------------


def test_bug_draft_defaults_to_untriaged():
    d = BugCandidateDraft(title="支付页崩溃")
    assert d.attribution_hypothesis == "UNTRIAGED", \
        "系统不得自动归因（8.2：不得仅凭 ELEMENT_NOT_FOUND 判 APP_DEFECT）"
    assert d.severity == "MEDIUM"
    assert d.evidence_score == 0
    assert d.reproducible is None, "三态：None = 还没试过复现"


def test_bug_draft_rejects_unknown_attribution_and_severity():
    with pytest.raises(ValueError, match="未知 attribution"):
        BugCandidateDraft(title="t", attribution_hypothesis="APP_BUG")
    with pytest.raises(ValueError, match="未知 severity"):
        BugCandidateDraft(title="t", severity="P0")


def test_bug_draft_rejects_blank_title():
    with pytest.raises(ValueError, match="title 不能为空"):
        BugCandidateDraft(title="  ")


def test_diagnosis_attributions_match_p1():
    """归因词表是**刻意复制的第二处字面量**（F3 禁止 diagnosis/** 直连
    `tracer.storage`，拿不到那个集合）——复制就必须钉一致性。

    手法同 `test_state_check_constraint_matches_enum`：两处字面量 + 一条对账。
    """
    from tracer.storage import ATTRIBUTIONS as P1_ATTRIBUTIONS

    assert set(ATTRIBUTIONS) == set(P1_ATTRIBUTIONS), \
        "diagnosis 的归因词表与 P1 漂移了"


def test_bug_statuses_cover_the_design_state_machine():
    """设计 §8.5 的状态机词表（转换表在 Task 5.6，本任务只定型词表）。"""
    assert set(BUG_STATUSES) == {
        "DISCOVERED", "ANALYZING", "CANDIDATE", "HUMAN_REVIEW",
        "CONFIRMED", "REJECTED", "DUPLICATE"}


def test_bug_severities_are_four_levels():
    assert set(BUG_SEVERITIES) == {"CRITICAL", "HIGH", "MEDIUM", "LOW"}


# --- F3：diagnosis 包不直连数据源 -----------------------------------------------


_FORBIDDEN = ("sqlite3", "tracer", "graph.storage", "experience.store",
              "source.sqlite_tx", "agents.storage")


def test_diagnosis_package_does_not_touch_data_sources_directly():
    """**F3 在 M5 侧的 import 级断言**（plan 的 F→矩阵映射：M2–M5 各任务都要有）。

    与 `planner/**` / `generator/**` 那两条同款、同一个判据——`diagnosis` 是
    `KnowledgeSources` 的**最后一个**同类调用方（Task 2.3 的 docstring 早已
    预告「M3–M5 还有 generator / explorer / diagnosis 三个同类调用方要来」）。

    AST 扫**全部** import（含函数内的按需导入），失败**指名文件**；带防空转
    下界（文件数 + 必须覆盖到 `context.py`——扫描面写错时立刻可见）。
    """
    root = Path(__file__).resolve().parents[2] / "diagnosis"
    files = [p for p in sorted(root.rglob("*.py"))
             if "__pycache__" not in p.parts]
    assert len(files) >= 3, f"只扫到 {len(files)} 个 diagnosis/*.py，扫描面疑似写错"
    assert {"__init__.py", "models.py", "context.py"} <= {p.name for p in files}

    offenders: dict[str, list[str]] = {}
    for path in files:
        bad: list[str] = []
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.append(node.module)
            elif isinstance(node, ast.Import):
                mods.extend(a.name for a in node.names)
            for m in mods:
                if any(m == f or m.startswith(f + ".") for f in _FORBIDDEN):
                    bad.append(m)
        if bad:
            offenders[path.name] = sorted(set(bad))
    assert not offenders, (
        f"diagnosis/** 必须只经 KnowledgeSources，不得直连数据源（F3）："
        f"{offenders}")


# --- 上下文装配（真 TraceStore 写 trace，读真实列名与形状） ----------------------

from tracer.storage import TraceStore                      # noqa: E402
from knowledge import build_knowledge                      # noqa: E402
from experience import SQLiteExperienceStore               # noqa: E402
from diagnosis.context import (DiagnosisContextError,      # noqa: E402
                               build_context)

APP = "com.phaset0.logindemo"
BUILD = "1026"


@pytest.fixture()
def trace_db(tmp_path) -> str:
    """一次失败 run：launch → tap(FAILED, APP_CRASH)，另有一个历史 run。"""
    db = tmp_path / "trace.db"
    store = TraceStore(db)
    store.start_run("run_1", suite="smoke", app_bundle_id=APP, app_build=BUILD)
    tc = store.start_testcase("run_1", "pay_002", attempt=1)
    store.record_step(tc, 0, "launch_app", status="SUCCESS")
    store.record_step(tc, 1, "tap", target_id="confirm_pay_button",
                      status="FAILED", failure_type="APP_CRASH")
    store.end_testcase(tc, "FAIL", failure_type="APP_CRASH")
    store.end_run("run_1", "FAIL")

    # 历史：另一个 run 的同类失败（矩阵 #16 的 same_issue_multiple_tests 来源）
    store.start_run("run_old", suite="smoke", app_bundle_id=APP,
                    app_build=BUILD)
    tc_old = store.start_testcase("run_old", "pay_001", attempt=1)
    store.record_step(tc_old, 0, "tap", target_id="confirm_pay_button",
                      status="FAILED", failure_type="APP_CRASH")
    store.end_testcase(tc_old, "FAIL", failure_type="APP_CRASH")
    store.end_run("run_old", "FAIL")
    return str(db)


@pytest.fixture()
def knowledge(trace_db, tmp_path):
    return build_knowledge(
        experience_store=SQLiteExperienceStore(tmp_path / "exp.db"),
        trace_db=trace_db, app_build=BUILD)


def test_context_collects_the_failure_scene(knowledge):
    ctx = build_context(knowledge=knowledge, run_id="run_1", app_id=APP,
                        app_build=BUILD)
    assert [s.step_index for s in ctx.steps] == [0, 1]
    assert ctx.steps[0].status == "SUCCESS", "现场要含成功的前置步骤"
    assert ctx.first_failure.step_index == 1
    assert ctx.first_failure.failure_type == "APP_CRASH"


def test_context_history_excludes_the_current_run(knowledge):
    """`history` 问的是「**别的时候**出现过吗」——把本 run 算进去会让
    「只失败过一次」误判成「多次」（矩阵 #16 的证据会凭空多一条）。"""
    ctx = build_context(knowledge=knowledge, run_id="run_1", app_id=APP)
    assert ctx.history, "另一个 run 的同类失败必须在历史里"
    own = {s.testcase_run_id for s in ctx.steps}
    assert not (own & {s.testcase_run_id for s in ctx.history})


def test_context_carries_the_runtime_graph(knowledge):
    ctx = build_context(knowledge=knowledge, run_id="run_1", app_id=APP)
    assert ctx.runtime_graph is not None
    assert ctx.runtime_graph.app_id == APP


def test_context_rejects_an_empty_run(knowledge):
    """拿不到失败现场 → fail-loud（不是返回空上下文让下游产出空假设）。"""
    with pytest.raises(DiagnosisContextError, match="没有任何步骤记录"):
        build_context(knowledge=knowledge, run_id="run_ghost")


def test_context_requires_a_run_id(knowledge):
    with pytest.raises(DiagnosisContextError, match="run_id 必填"):
        build_context(knowledge=knowledge, run_id="")


def test_context_uses_the_injected_knowledge_not_the_database():
    """**F3 的行为级钉子**：装配只经注入的 `knowledge`——给一个会记账的替身，
    取现场必须走它的 `trace_history`（不是自己连库）。"""
    from diagnosis.context import build_context as bc

    calls: list[tuple] = []

    class FakeStep:
        step_index = 0
        status = "FAILED"
        failure_type = None
        testcase_run_id = 1

    class SpyKnowledge:
        def trace_history(self, filters):
            calls.append(tuple(sorted(filters)))
            return [FakeStep()]

        def graph_query(self, app_id):
            raise AssertionError("app_id 没给，不该走到图查询")

        def experience_lookup(self, *a):
            raise AssertionError("screen/target 没给，不该走到经验查询")

        def impact_of(self, target_id):
            raise AssertionError("诊断不消费 impact_of")

    ctx = bc(knowledge=SpyKnowledge(), run_id="run_x")
    assert ctx.steps and ctx.steps[0].status == "FAILED"
    assert calls == [("run_id",)], \
        "按 run 取现场必须用 trace_history 的 run_id 过滤键（F3：唯一取数通路）"
