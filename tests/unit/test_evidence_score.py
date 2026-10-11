"""Task 5.3 / P3-22：Evidence Score（设计 §8.3 / F10 / F13）。

plan Steps 的判据：
1. 各证据项**按配置权重**累加（权重读 `agents/policy_config.py` 真值源）；
2. **排序由 Evidence Score 决定，不由 LLM confidence 决定**——同证据集下
   confidence 高低不改变次序（本任务的核心钉子）；
3. 低于阈值 → `suggested_attribution` 为空（矩阵 #16 前半）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents.policy_config import (ATTRIBUTION_THRESHOLD,
                                  DIAGNOSIS_EVIDENCE_WEIGHTS)
from diagnosis.evidence import (rank_hypotheses, score_evidence,
                                suggested_attribution)
from diagnosis.models import EvidenceItem, Hypothesis


def _ev(*kinds):
    return [EvidenceItem(kind=k) for k in kinds]


def _hyp(statement, confidence, *kinds):
    return Hypothesis(statement=statement, confidence=confidence,
                      origin="rule_based", evidence=_ev(*kinds))


# --- 1. 权重求和（真值源 + 按类别去重） ----------------------------------------


def test_weights_come_from_the_policy_truth_source():
    """权重表只有一处字面量（`DIAGNOSIS_EVIDENCE_WEIGHTS`）——校准改那一处。"""
    for kind, weight in DIAGNOSIS_EVIDENCE_WEIGHTS.items():
        assert score_evidence(_ev(kind)) == weight, f"{kind} 权重与真值源不符"


def test_evidence_items_accumulate_across_categories():
    assert score_evidence(_ev("crash_detected", "backend_evidence")) == \
        DIAGNOSIS_EVIDENCE_WEIGHTS["crash_detected"] + \
        DIAGNOSIS_EVIDENCE_WEIGHTS["backend_evidence"]
    assert score_evidence(_ev(*DIAGNOSIS_EVIDENCE_WEIGHTS)) == \
        sum(DIAGNOSIS_EVIDENCE_WEIGHTS.values())


def test_same_category_counts_once_not_once_per_item():
    """**按类别**求和（口径登记，见模块 docstring）：同一个事实记十条不会让
    证据更强——`same_issue_multiple_tests` 尤其明显（影响 100 个用例的根因
    若按条目累加会得 300 分，跨候选就再也比不了大小）。"""
    assert score_evidence(_ev("crash_detected")) == \
        score_evidence(_ev("crash_detected") * 10)


def test_empty_evidence_scores_zero():
    assert score_evidence([]) == 0


def test_injected_weights_override_the_truth_source():
    """校准通道：`weights` 可注入（policy 覆盖 / 单测）。"""
    assert score_evidence(_ev("crash_detected"),
                          weights={"crash_detected": 100}) == 100


def test_unknown_kind_fails_loud_even_with_injected_weights():
    """第二道闸门：注入的 weights 可能不认识这个 kind——静默加 0 分会让
    「证据提交了、其实一分没加」溜过去（第一道在 `EvidenceItem.kind`）。"""
    duck = SimpleNamespace(kind="not_a_real_kind")
    with pytest.raises(ValueError, match="not_a_real_kind"):
        score_evidence([duck])
    # 真值源里有、注入表里没有 → 同样 fail-loud（不是静默按真值源补）
    with pytest.raises(ValueError, match="crash_detected"):
        score_evidence(_ev("crash_detected"), weights={"backend_evidence": 2})


# --- 2. 排序由 Score 决定，不由 confidence 决定（核心钉子） ---------------------


def test_ranking_is_decided_by_evidence_score_not_confidence():
    """设计 §8.3 标题原文：「Evidence Score（**替代单纯 LLM confidence**）」。

    强证据 + 低 confidence 必须排在弱证据 + 高 confidence **之前**。
    """
    strong = _hyp("强证据的假设", 0.10, "crash_detected", "reproducible_3_times")
    weak = _hyp("弱证据的假设", 0.99, "llm_hypothesis_only")
    ranked = rank_hypotheses([weak, strong])
    assert [h.statement for h, _ in ranked] == \
        ["强证据的假设", "弱证据的假设"]
    assert [s for _, s in ranked] == [10, 1]


def test_same_evidence_set_orders_identically_whatever_the_confidence():
    """**本任务的核心钉子**：同证据集（= 同分）下 confidence **完全不影响**
    次序——排序键里带上它，这条就破。

    ⚠️ 构造要点（2026-10-11 实测教训）：**confidence 必须在同一个列表内部
    与 statement 次序相反**，否则把 confidence 塞进排序键的 mutant 照样全绿
    ——两组各自单调的 confidence（0.05… / 0.95…）在组内是常数序，与
    statement 序同向，mutant 观察不到差异。第一版就是这样漏掉的。
    """
    kinds = ("backend_evidence", "source_change_correlated")
    # a:0.1  b:0.5  c:0.9 —— confidence 递增，而期望次序是 statement 递增
    mixed = [_hyp("a 假设", 0.1, *kinds), _hyp("b 假设", 0.5, *kinds),
             _hyp("c 假设", 0.9, *kinds)]
    assert [h.statement for h, _ in rank_hypotheses(mixed)] == \
        ["a 假设", "b 假设", "c 假设"]

    # 反过来（confidence 递减）次序**不变**——这才证明它没进排序键
    flipped = [_hyp("a 假设", 0.9, *kinds), _hyp("b 假设", 0.5, *kinds),
               _hyp("c 假设", 0.1, *kinds)]
    assert [h.statement for h, _ in rank_hypotheses(flipped)] == \
        ["a 假设", "b 假设", "c 假设"]


def test_equal_scores_break_ties_on_statement_for_stability():
    """同分不能靠输入顺序（那会让结果依赖调用方怎么拼列表）——按 statement 定序。"""
    b = _hyp("b 假设", 0.5, "backend_evidence")
    a = _hyp("a 假设", 0.5, "backend_evidence")
    assert [h.statement for h, _ in rank_hypotheses([b, a])] == \
        ["a 假设", "b 假设"]


def test_ranking_returns_score_alongside_each_hypothesis():
    """分数与假设同行——调用方不必重算（重算会引入第二个求值时机）。"""
    ranked = rank_hypotheses([_hyp("x", 0.5, "crash_detected")])
    assert ranked[0][1] == DIAGNOSIS_EVIDENCE_WEIGHTS["crash_detected"]
    assert rank_hypotheses([]) == []


def test_ranking_honours_injected_weights():
    a = _hyp("a", 0.5, "backend_evidence")
    b = _hyp("b", 0.5, "llm_hypothesis_only")
    flipped = rank_hypotheses([a, b], weights={"backend_evidence": 1,
                                               "llm_hypothesis_only": 9})
    assert [h.statement for h, _ in flipped] == ["b", "a"]


# --- 3. 阈值闸门 + 方向闸门（矩阵 #16 前半） -----------------------------------


def test_below_threshold_has_no_suggested_attribution():
    """**矩阵 #16 前半**：分数不够 → 保持 UNTRIAGED（返回 None）。"""
    assert ATTRIBUTION_THRESHOLD == 10          # 初版（M1 定档）
    weak = _hyp("只有崩溃一条证据", 0.9, "crash_detected")     # 5 分
    assert score_evidence(weak.evidence) < ATTRIBUTION_THRESHOLD
    assert suggested_attribution(weak) is None


def test_crash_needs_corroboration_before_it_is_suggested():
    """跨任务口径（#14 × #16）：矩阵 #14 的 crash 场景只带一条
    `crash_detected`（5 分）——**规则出的是假设，不是归因**。要凑到阈值
    （例如再来一条 `reproducible_3_times`）才给建议方向。"""
    crash_only = _hyp("崩溃", 0.9, "crash_detected")
    assert suggested_attribution(crash_only) is None

    corroborated = _hyp("崩溃且可稳定复现", 0.9,
                        "crash_detected", "reproducible_3_times")
    assert score_evidence(corroborated.evidence) == ATTRIBUTION_THRESHOLD
    assert suggested_attribution(corroborated) == "APP_DEFECT"


def test_above_threshold_maps_direction_from_the_strongest_evidence():
    """方向由**权重最高**的那类映射证据决定（不写死 if 链——权重表是校准点）。"""
    crash = _hyp("崩溃", 0.5, "crash_detected", "reproducible_3_times")
    assert suggested_attribution(crash) == "APP_DEFECT"

    drift = _hyp("漂移", 0.5, "source_change_correlated",
                 "reproducible_3_times", "same_issue_multiple_tests")
    assert score_evidence(drift.evidence) == 11
    assert suggested_attribution(drift) == "AUTOMATION_DEFECT"

    backend = _hyp("5xx", 0.5, "backend_evidence", "reproducible_3_times",
                   "same_issue_multiple_tests")
    assert score_evidence(backend.evidence) == 10
    assert suggested_attribution(backend) == "ENVIRONMENT_DEFECT"


def test_crash_outranks_drift_when_both_are_present():
    """多条方向证据并存 → 权重高的定方向（崩溃 5 > 漂移 3）。"""
    both = _hyp("崩溃 + 漂移", 0.5, "crash_detected",
                "source_change_correlated", "backend_evidence")
    assert score_evidence(both.evidence) == 10
    assert suggested_attribution(both) == "APP_DEFECT"


def test_threshold_is_configurable():
    """阈值本身可覆盖（`EvidencePolicy.attribution_threshold` 的注入形态）。"""
    weak = _hyp("只有崩溃一条证据", 0.9, "crash_detected")
    assert suggested_attribution(weak, threshold=5) == "APP_DEFECT"
    strong = _hyp("崩溃且可复现", 0.9, "crash_detected",
                  "reproducible_3_times")
    assert suggested_attribution(strong, threshold=99) is None


def test_direction_gate_needs_calibration_to_be_reachable():
    """**口径登记（实测发现）**：初版权重下「分数过阈值、但方向不明」**不可达**
    ——三类无方向的证据合计 5+3+1=9 < 阈值 10。所以方向闸门是**为校准留的
    防御**：一旦把 `reproducible_3_times` 之类调高，它就会真的挡住
    「分数很高但说不清是谁的责任」的候选（F10：不猜）。

    本用例钉住这个关系：谁把阈值降到 ≤9 或把无方向证据调高，这里会红——
    那时必须同时补一条**可达**的方向闸门用例（下面那条示范）。
    """
    unmapped_total = sum(v for k, v in DIAGNOSIS_EVIDENCE_WEIGHTS.items()
                         if k not in ("crash_detected",
                                      "source_change_correlated",
                                      "backend_evidence"))
    assert unmapped_total < ATTRIBUTION_THRESHOLD, (
        "无方向证据的总分已经够到阈值——方向闸门变为可达，请补可达用例并"
        "复核 `_KIND_TO_ATTRIBUTION` 是否需要新增映射")

    # 校准后可达：把无方向的 reproducible_3_times 调到 10
    calibrated = {**DIAGNOSIS_EVIDENCE_WEIGHTS, "reproducible_3_times": 10}
    high_and_vague = _hyp("分很高但说不清是谁的责任", 0.9,
                          "reproducible_3_times")
    assert score_evidence(high_and_vague.evidence,
                          weights=calibrated) >= ATTRIBUTION_THRESHOLD
    assert suggested_attribution(high_and_vague, weights=calibrated) is None


def test_score_is_computed_inside_not_passed_in():
    """分数在函数内部现算：两处各算一次就可能用上不同 weights，
    得到一个自相矛盾的结论（所以签名里没有 score 参数）。"""
    import inspect
    params = inspect.signature(suggested_attribution).parameters
    assert "score" not in params


# --- 接线：analyzer 产出的假设经 Evidence Score 排序 ---------------------------


def test_analyzer_hypotheses_rank_by_score():
    """规则产出的假设集（可能多条并存）按证据分排序——规则给的 confidence
    只是各自的主观值，不该决定谁排前面。"""
    from diagnosis.analyzer import analyze
    from diagnosis.context import DiagnosisContext
    from graph.models import TraceStep

    ctx = DiagnosisContext(
        run_id="run_1", app_id="app", app_build="1026",
        steps=(TraceStep(testcase_run_id=1, step_index=1, step_type="tap",
                         target_id="login_button", status="FAILED",
                         detail={"recovery": {"outcome": "DRIFT"}},
                         failure_type="APP_CRASH"),),
        network_summary={"status_code": 503})
    result = analyze(ctx)                      # 规则命中 → LLM 零调用
    assert result.rule_hit is True
    ranked = rank_hypotheses(result.hypotheses)
    scores = [s for _, s in ranked]
    assert scores == sorted(scores, reverse=True)
    assert ranked[0][0].evidence[0].kind == "crash_detected"
