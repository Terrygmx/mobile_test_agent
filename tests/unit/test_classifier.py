"""Task 5.2 / P3-21：规则优先的假设引擎（设计 §8.2 / F10）。

plan Steps 的判据：
1. **crash 场景 → 规则直接出 APP_DEFECT、LLM 零调用**（矩阵 #14）；
2. **漂移 + diff 命中 → UI_DRIFT**（复用 P2 reconciliation 记录与 `graph/diff.py`，
   不重算）；
3. **规则无法判定 → LLM 补充且 origin 标注**（权重最低，设计 §8.3
   `llm_hypothesis_only: 1`）；
4. 5xx + UI 正常 → ENVIRONMENT_DEFECT；
5. 断言值不符 → **不进诊断流程**（设计 §8.2 末条）。
"""
from __future__ import annotations

import pytest

from diagnosis.analyzer import analyze
from diagnosis.classifier import (crash_detected, is_assertion_only_failure,
                                  rule_based_hypotheses)
from diagnosis.context import DiagnosisContext
from llm.budget import LLMBudget


def _step(index=0, status="FAILED", failure_type=None, detail=None,
          step_type="tap", target_id="login_button", tc_run=1):
    from graph.models import TraceStep
    return TraceStep(testcase_run_id=tc_run, step_index=index,
                     step_type=step_type, target_id=target_id, status=status,
                     detail=detail or {}, failure_type=failure_type)


def _ctx(**kw) -> DiagnosisContext:
    base = dict(run_id="run_1", app_id="app", app_build="1026")
    base.update(kw)
    return DiagnosisContext(**base)


# --- crash_detected（新增判定；P1 不存在这个函数） ------------------------------


def test_crash_detected_from_failure_type():
    ctx = _ctx(steps=(_step(1, failure_type="APP_CRASH"),))
    assert crash_detected(ctx) is True


def test_crash_detected_from_history_too():
    """历史里的崩溃也算——同类失败以前崩过，这次的现场更可能是同一回事。"""
    ctx = _ctx(steps=(_step(1, failure_type="ELEMENT_NOT_FOUND"),),
               history=(_step(0, failure_type="APP_CRASH", tc_run=9),))
    assert crash_detected(ctx) is True


def test_crash_detected_needs_a_failure_when_only_infra_event():
    """单独一条 WDA_DEAD **不是**「App 崩了」——基础设施抖动与 App 缺陷是两回事。"""
    ctx = _ctx(steps=(_step(0, status="SUCCESS"),),
               infra_events=({"event_type": "WDA_DEAD"},))
    assert crash_detected(ctx) is False
    ctx2 = _ctx(steps=(_step(1, failure_type="WAIT_TIMEOUT"),),
                infra_events=({"event_type": "WDA_DEAD"},))
    assert crash_detected(ctx2) is True


def test_no_crash_signal_at_all():
    assert crash_detected(_ctx(steps=(_step(1, failure_type="WAIT_TIMEOUT"),))) \
        is False


# --- 规则：crash → APP_DEFECT，LLM 零调用（矩阵 #14） ---------------------------


class _CountingLLM:
    def __init__(self):
        self.calls = 0

    def complete(self, prompt, timeout=20):
        self.calls += 1
        return '{"hypotheses": [{"statement": "llm guess", "confidence": 0.5}]}'


def test_crash_scenario_yields_app_defect_with_zero_llm_calls():
    """**矩阵 #14**：crash 场景规则直接出结论，LLM 一次都不调。"""
    ctx = _ctx(steps=(_step(0, status="SUCCESS"),
                      _step(1, failure_type="APP_CRASH")))
    llm = _CountingLLM()
    result = analyze(ctx, llm=llm, budget=LLMBudget(max_calls_per_run=5))

    assert llm.calls == 0, "规则命中不得调用 LLM（省成本也更可信）"
    assert result.llm_calls_used == 0
    assert result.rule_hit is True
    assert len(result.hypotheses) == 1
    h = result.hypotheses[0]
    assert h.origin == "rule_based"
    assert "崩溃" in h.statement
    assert [e.kind for e in h.evidence] == ["crash_detected"]


def test_crash_hypothesis_carries_a_traceable_source():
    ctx = _ctx(steps=(_step(3, failure_type="APP_CRASH", tc_run=7),))
    h = rule_based_hypotheses(ctx)[0]
    assert h.evidence[0].source.startswith("trace:")
    assert "7" in h.evidence[0].source and "3" in h.evidence[0].source


# --- 规则：UI_DRIFT（复用 P2 reconciliation 记录 + graph_diff） -----------------


def test_ui_drift_from_the_p2_reconciliation_record():
    """**不重算漂移**：读 `steps.detail_json` 里 P2 恢复流水线写下的记录。"""
    drift = _step(1, failure_type="ELEMENT_NOT_FOUND",
                  detail={"recovery": {"outcome": "DRIFT",
                                       "candidates": ["confirm_pay_button"]}})
    hyps = rule_based_hypotheses(_ctx(steps=(drift,)))
    assert [h.origin for h in hyps] == ["rule_based"]
    assert "漂移" in hyps[0].statement
    assert [e.kind for e in hyps[0].evidence] == ["source_change_correlated"]
    assert "recovery_record" in hyps[0].evidence[0].source


def test_ui_drift_from_a_failed_recovery_stage_record():
    """**真漂移现场的形态**（M5 Gate 2026-10-11 实测修正）。

    恢复**失败**时（元素真改名、无 LLM/经验可救——最常见的一种），
    `cli/pipeline.py` 写的是 `recovery_attempted`，而 `reconcile` 只是其中的
    一个**阶段**：结论在 `stages` 列表里，不在那段顶层的 `outcome`（后者是
    整条流水线的结论，如 `"no_llm_engine"`）。

    早先只读 `detail["recovery"]`，于是**真漂移现场规则不命中**——代码与
    本文件的旧用例都绿（它们造的是恢复**成功**的形状），真机上一检不出来。
    """
    drift = _step(1, failure_type="ELEMENT_NOT_FOUND",
                  detail={"recovery_attempted": {
                      "recovery": "no_llm_engine",
                      "stages": [
                          {"stage": "settle", "outcome": "ElementNotFound"},
                          {"stage": "screen", "outcome": "FOUND",
                           "screen": "LoginView"},
                          {"stage": "reconcile", "outcome": "DRIFT",
                           "candidates": ["user_field"]},
                          {"stage": "llm", "outcome": "disabled"}]}})
    hyps = rule_based_hypotheses(_ctx(steps=(drift,)))
    assert len(hyps) == 1, hyps
    assert [e.kind for e in hyps[0].evidence] == ["source_change_correlated"]
    assert "recovery_record" in hyps[0].evidence[0].source


def test_a_non_drift_recovery_outcome_is_not_a_drift_record():
    """反钉子：流水线结论是别的值（如 `no_llm_engine`）就不算漂移记录——
    只看顶层 `outcome` 会把**任何**恢复失败都判成漂移。"""
    not_drift = _step(1, failure_type="ELEMENT_NOT_FOUND",
                      detail={"recovery_attempted": {
                          "recovery": "no_llm_engine",
                          "stages": [{"stage": "reconcile",
                                      "outcome": "MATCH"}]}})
    assert not [h for h in rule_based_hypotheses(_ctx(steps=(not_drift,)))
                if "漂移" in h.statement]


def test_ui_drift_from_a_graph_diff_hit():
    """`graph_diff` 命中非 UNKNOWN 差异 → UI_DRIFT（复用 `graph.diff` 判据）。"""
    from graph.diff import ADDED, DiffEntry, GraphDiff
    diff = GraphDiff(entries=(DiffEntry(kind=ADDED, screen_id="PayView"),))
    hyps = rule_based_hypotheses(_ctx(
        steps=(_step(1, failure_type="ELEMENT_NOT_FOUND"),), graph_diff=diff))
    assert "漂移" in hyps[0].statement
    assert "graph_diff" in hyps[0].evidence[0].source


def test_an_all_unknown_diff_is_not_a_hit():
    """UNKNOWN 是「没判定」不是「有变化」——不构成漂移证据（`GraphDiff.changed`
    的判据，复用不重算）。"""
    from graph.diff import DiffEntry, GraphDiff, UNKNOWN
    diff = GraphDiff(entries=(DiffEntry(kind=UNKNOWN, screen_id="X"),))
    assert not rule_based_hypotheses(_ctx(
        steps=(_step(1, failure_type="ELEMENT_NOT_FOUND"),), graph_diff=diff))


def test_no_drift_record_and_no_diff_yields_no_drift_hypothesis():
    hyps = rule_based_hypotheses(_ctx(
        steps=(_step(1, failure_type="ELEMENT_NOT_FOUND"),)))
    assert hyps == []


# --- 规则：5xx + UI 正常 → ENVIRONMENT_DEFECT ----------------------------------


def test_server_error_with_healthy_ui_is_an_environment_defect():
    hyps = rule_based_hypotheses(_ctx(
        steps=(_step(1, failure_type="WAIT_TIMEOUT"),),
        network_summary={"server_errors_5xx": 3}))
    assert len(hyps) == 1
    assert "环境缺陷" in hyps[0].statement
    assert [e.kind for e in hyps[0].evidence] == ["backend_evidence"]


def test_server_error_with_an_element_not_found_is_not_an_env_defect():
    """UI 侧有定位失败时，5xx 不构成「UI 正常」的前提。"""
    hyps = rule_based_hypotheses(_ctx(
        steps=(_step(1, failure_type="ELEMENT_NOT_FOUND"),),
        network_summary={"server_errors_5xx": 3}))
    assert hyps == []


def test_status_code_form_of_the_summary_also_counts():
    hyps = rule_based_hypotheses(_ctx(
        steps=(_step(1, failure_type="WAIT_TIMEOUT"),),
        network_summary={"status_code": 503}))
    assert hyps and "环境缺陷" in hyps[0].statement


# --- 断言值不符：不进诊断流程（设计 §8.2 末条） ---------------------------------


def test_assertion_value_mismatch_is_not_a_diagnosis_case():
    ctx = _ctx(steps=(_step(1, failure_type="ASSERTION_VALUE_MISMATCH"),))
    assert is_assertion_only_failure(ctx) is True
    assert rule_based_hypotheses(ctx) == []

    llm = _CountingLLM()
    result = analyze(ctx, llm=llm, budget=LLMBudget(max_calls_per_run=5))
    assert llm.calls == 0, "P1 的既有结论不该去问 LLM"
    assert result.hypotheses == ()
    assert any("ASSERTION_VALUE_MISMATCH" in n for n in result.notes)


def test_mixed_failures_are_not_assertion_only():
    ctx = _ctx(steps=(_step(1, failure_type="ASSERTION_VALUE_MISMATCH"),
                      _step(2, failure_type="WAIT_TIMEOUT")))
    assert is_assertion_only_failure(ctx) is False


# --- 规则空 → LLM 补充（origin 标注，权重最低那档） -----------------------------


def test_rules_empty_falls_through_to_llm():
    ctx = _ctx(steps=(_step(1, failure_type="ACTION_OUTCOME_UNKNOWN"),))
    llm = _CountingLLM()
    result = analyze(ctx, llm=llm, budget=LLMBudget(max_calls_per_run=5))

    assert llm.calls == 1
    assert result.llm_calls_used == 1
    assert result.rule_hit is False
    assert len(result.hypotheses) == 1
    h = result.hypotheses[0]
    assert h.origin == "llm_hypothesis"
    assert h.statement == "llm guess"
    assert [e.kind for e in h.evidence] == ["llm_hypothesis_only"], \
        "LLM 假设必须挂 llm_hypothesis_only 证据（权重表最低的 1 分）"


def test_llm_requires_both_provider_and_budget():
    """门槛是「两个都给」（照抄 `planner.plan`）——只给一个时不调用。"""
    ctx = _ctx(steps=(_step(1, failure_type="ACTION_OUTCOME_UNKNOWN"),))
    llm = _CountingLLM()
    result = analyze(ctx, llm=llm)          # 没给 budget
    assert llm.calls == 0
    assert result.hypotheses == ()
    assert any("未同时注入" in n for n in result.notes)


def test_llm_provider_failure_degrades_without_raising():
    class Boom:
        def complete(self, prompt, timeout=20):
            raise RuntimeError("provider down")

    ctx = _ctx(steps=(_step(1, failure_type="ACTION_OUTCOME_UNKNOWN"),))
    budget = LLMBudget(max_calls_per_run=5)
    result = analyze(ctx, llm=Boom(), budget=budget)
    assert result.hypotheses == ()
    assert any("LLM 调用失败" in n for n in result.notes)
    assert budget.calls_used == 1, "调用被记进 P1 LLMBudget（F4）"


def test_llm_budget_exhausted_skips_the_provider():
    class Boom:
        def complete(self, prompt, timeout=20):
            raise AssertionError("预算耗尽不得发起调用")

    ctx = _ctx(steps=(_step(1, failure_type="ACTION_OUTCOME_UNKNOWN"),))
    budget = LLMBudget(max_calls_per_run=0)
    result = analyze(ctx, llm=Boom(), budget=budget)
    assert result.hypotheses == ()
    assert any("预算耗尽" in n for n in result.notes)


def test_unparseable_llm_output_yields_no_hypothesis():
    class Garbage:
        def complete(self, prompt, timeout=20):
            return "not json at all"

    ctx = _ctx(steps=(_step(1, failure_type="ACTION_OUTCOME_UNKNOWN"),))
    budget = LLMBudget(max_calls_per_run=5)
    result = analyze(ctx, llm=Garbage(), budget=budget)
    assert result.hypotheses == ()
    assert any("没有可用假设" in n for n in result.notes)
    assert budget.broken is False or budget.calls_used == 1


# --- F10：规则函数不产出归因结论 -------------------------------------------------


def test_rule_hypotheses_never_assert_an_attribution():
    """规则产出的是**假设**——`Hypothesis` 模型里根本没有归因字段，
    这是 F10 在类型层的落地。"""
    from diagnosis.models import Hypothesis
    assert "attribution" not in Hypothesis.model_fields
    assert not any("attribution" in f for f in Hypothesis.model_fields)
