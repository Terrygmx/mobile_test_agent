"""Task 6.5 / P3-30：Agent Benchmark（设计 §13.2 / F13）。

判据：
1. **固定 7 类 case**（设计 §13.2 原文）：Locator Drift / Unknown Screen /
   Ambiguous Element / Backend Failure / App Crash / New Transition /
   Negative Scenario——全部离设备（F13），场景构造复用矩阵同款形状；
2. **Diagnosis Accuracy 只在标注集内计算**（口径登记）：每类 case 自带预期
   （top 证据类别 / 明确无假设），accuracy = 命中数 / 7；
3. **compare**：关键指标下降超阈值 → `AGENT_REGRESSION`，exit ≠ 0 供 CI；
4. **LLM 退化探针**：垃圾 LLM 输出下 `analyze` 优雅降级（不抛）——Agent
   自身的回归检测，与 App 回归是并列的两件事（设计原文）。
"""
from __future__ import annotations

import pytest

from agents.benchmark import (BENCHMARK_CASES, CaseResult, compare_benchmarks,
                              run_benchmark)
from agents.storage import SQLiteAgentStore


# --- 1. 固定 case 集 --------------------------------------------------------------


def test_seven_fixed_cases_match_the_design_verbatim():
    """设计 §13.2 的 7 类逐字对账（增删一类都算漂移）。"""
    assert [c.case_id for c in BENCHMARK_CASES] == [
        "locator_drift", "unknown_screen", "ambiguous_element",
        "backend_failure", "app_crash", "new_transition", "negative_scenario"]


def test_every_case_has_a_label():
    """标注真值是 accuracy 的分母来源——没标注的 case 是自欺。"""
    for case in BENCHMARK_CASES:
        assert case.expected in (*[k for k in (
            "source_change_correlated", "crash_detected",
            "backend_evidence")], "none"), case.case_id


def test_benchmark_runs_offline_and_scores_accuracy():
    """全部离设备（F13）+ 标注集内的 accuracy。"""
    result = run_benchmark()
    assert len(result.cases) == 7
    assert 0.0 <= result.diagnosis_accuracy <= 1.0
    assert result.llm_path_ok is True, "垃圾 LLM 输出下必须优雅降级"
    # 确定性规则路径在固定场景上的预期命中（初版实现的烟测锚）：
    got = {c.case_id: c.correct for c in result.cases}
    assert got["locator_drift"] is True
    assert got["backend_failure"] is True
    assert got["app_crash"] is True
    assert got["new_transition"] is True
    assert got["negative_scenario"] is True
    assert got["unknown_screen"] is True          # 诚实空 → 正确
    assert got["ambiguous_element"] is True


def test_run_benchmark_persists_a_row(tmp_path):
    store = SQLiteAgentStore(tmp_path / "agent.db")
    result = run_benchmark()
    run_benchmark(store=store, label="v1")
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "agent.db"))
    try:
        rows = conn.execute(
            "SELECT label, metrics_json FROM benchmark_runs").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "v1"
    assert "diagnosis_accuracy" in rows[0][1]


# --- 2. compare 与 AGENT_REGRESSION ----------------------------------------------


def test_compare_flags_a_regression_when_accuracy_drops():
    good = run_benchmark()          # 7/7 命中（上面的锚）
    bad_cases = [CaseResult(case_id=c.case_id, expected=c.expected, got=c.expected,
                            correct=(c.case_id != "locator_drift"))
                 for c in BENCHMARK_CASES]
    from agents.benchmark import BenchmarkResult
    bad = BenchmarkResult(cases=tuple(bad_cases), diagnosis_accuracy=6 / 7,
                          llm_path_ok=True, llm_calls=0, duration_ms=1)
    regressions = compare_benchmarks(good, bad, threshold=0.05)
    # ⚠️ 两条判定**分别**钉住（不是 `or` 的合并断言）：总准确率掉了、且
    # locator_drift 这一类翻成错——CI 读到的回归清单必须同时说清「掉多少」
    # 和「哪一类翻了」。用 `or` 合并会让任一分支单独坏掉时测试仍全绿
    # （A/B 探针实测：两个分支分别改坏都是 7 passed）。
    assert any("diagnosis_accuracy" in r for r in regressions), regressions
    assert any("locator_drift" in r for r in regressions), regressions
    assert compare_benchmarks(bad, good, threshold=0.05) == [], \
        "改善方向不是回归"


def test_compare_flags_llm_path_breakage_even_when_accuracy_holds():
    good = run_benchmark()
    from agents.benchmark import BenchmarkResult
    broken = BenchmarkResult(cases=good.cases,
                             diagnosis_accuracy=good.diagnosis_accuracy,
                             llm_path_ok=False, llm_calls=0, duration_ms=1)
    regressions = compare_benchmarks(good, broken, threshold=0.05)
    assert any("llm_path" in r for r in regressions)


def test_compare_within_threshold_is_not_a_regression():
    good = run_benchmark()
    regressions = compare_benchmarks(good, good, threshold=0.05)
    assert regressions == []
