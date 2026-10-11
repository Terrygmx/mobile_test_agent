"""benchmark.py — Agent Evaluation Benchmark（设计 §13.2；Task 6.5 / P3-30）。

设计 §13.2 原文：「每次修改 Agent（Prompt、模型、规则）→ 跑一遍 Benchmark →
与上一版本对比 → 若关键指标（如 Recovery/Diagnosis 准确率）下降，标记
AGENT_REGRESSION」——Agent 本身的质量回归检测，与 App 回归测试是**并列**
的两件事。

## 固定 7 类 case（设计 §13.2 原文；全部离设备，F13）

| case | 场景 | 标注（预期 top 证据） |
|---|---|---|
| locator_drift | 定位失败 + P2 reconciliation 判 DRIFT | `source_change_correlated` |
| unknown_screen | 定位失败、无任何佐证 | 无假设（诚实空） |
| ambiguous_element | AMBIGUOUS_ELEMENT | 无假设 |
| backend_failure | 5xx + 非 UI 失败 | `backend_evidence` |
| app_crash | APP_CRASH 失败步骤 | `crash_detected` |
| new_transition | 定位失败 + graph_diff 命中非 UNKNOWN | `source_change_correlated` |
| negative_scenario | 断言值不符（归 P1，不进诊断） | 无假设 |

**Diagnosis Accuracy 只在标注集内计算**（口径登记，与 `report/agent_metrics.py`
的 collect 互补）：每类 case 自带标注真值，accuracy = 命中 / 7。场景构造
复用矩阵/单测同款的 detail 形状与 `graph.diff` 判据（不新写场景）；LLM 退化
探针复用 `tests/fault_injection/fi_support.py` 的 `FakeLLM`。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

from diagnosis.analyzer import analyze
from diagnosis.context import DiagnosisContext
from diagnosis.evidence import score_evidence
from graph.diff import ADDED, DiffEntry, GraphDiff
from graph.models import TraceStep
from llm.budget import LLMBudget

__all__ = ["BENCHMARK_CASES", "BenchmarkCase", "BenchmarkResult",
           "CaseResult", "compare_benchmarks", "run_benchmark"]


@dataclass(frozen=True)
class BenchmarkCase:
    """一个固定 case：场景构造 + 标注真值。"""

    case_id: str
    expected: str                      # 预期的 top 证据类别；"none" = 无假设
    build_context: object              # () -> DiagnosisContext


def _step(failure_type=None, detail=None, status="FAILED", index=1):
    return TraceStep(testcase_run_id=1, step_index=index, step_type="tap",
                     target_id="pay_button", status=status,
                     detail=detail or {}, failure_type=failure_type)


_DRIFT_DETAIL = {"recovery_attempted": {
    "recovery": "no_llm_engine",
    "stages": [{"stage": "reconcile", "outcome": "DRIFT",
                "candidates": ["user_field"]}]}}


def _ctx(steps=(), graph_diff=None, network_summary=None) -> DiagnosisContext:
    return DiagnosisContext(run_id="bench", app_id="bench.app",
                            app_build="bench", steps=tuple(steps),
                            graph_diff=graph_diff,
                            network_summary=dict(network_summary or {}))


BENCHMARK_CASES: tuple[BenchmarkCase, ...] = (
    BenchmarkCase("locator_drift", "source_change_correlated",
                  lambda: _ctx(steps=(_step("ELEMENT_NOT_FOUND",
                                           detail=_DRIFT_DETAIL),))),
    BenchmarkCase("unknown_screen", "none",
                  lambda: _ctx(steps=(_step("ELEMENT_NOT_FOUND"),))),
    BenchmarkCase("ambiguous_element", "none",
                  lambda: _ctx(steps=(_step("AMBIGUOUS_ELEMENT"),))),
    BenchmarkCase("backend_failure", "backend_evidence",
                  lambda: _ctx(steps=(_step("WAIT_TIMEOUT"),),
                               network_summary={"server_errors_5xx": 3})),
    BenchmarkCase("app_crash", "crash_detected",
                  lambda: _ctx(steps=(_step("APP_CRASH"),))),
    BenchmarkCase("new_transition", "source_change_correlated",
                  lambda: _ctx(steps=(_step("ELEMENT_NOT_FOUND"),),
                               graph_diff=GraphDiff(entries=(
                                   DiffEntry(kind=ADDED,
                                             screen_id="PayView"),)))),
    BenchmarkCase("negative_scenario", "none",
                  lambda: _ctx(steps=(_step("ASSERTION_VALUE_MISMATCH"),))),
)


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    expected: str
    got: str
    correct: bool


@dataclass(frozen=True)
class BenchmarkResult:
    cases: tuple
    diagnosis_accuracy: float
    llm_path_ok: bool
    llm_calls: int
    duration_ms: float


def _top_evidence_kind(result) -> str:
    """分析结果的 top 证据类别（按 Evidence Score 排序的第一名的首类）。"""
    if not result.hypotheses:
        return "none"
    best = max(result.hypotheses,
               key=lambda h: score_evidence(h.evidence))
    return best.evidence[0].kind if best.evidence else "none"


def _llm_degradation_probe() -> tuple[bool, int]:
    """垃圾 LLM 输出下 `analyze` 必须优雅降级（设计 §13.2 的回归检测点）。

    复用 `fi_support.FakeLLM`（矩阵同款替身）：规则给不出结论 → LLM 被调 →
    返回垃圾 → 不抛异常、零假设、调用计入 P1 预算。哪天这条炸了（把编程
    错误伪装成结论、或忘了扣预算），benchmark 立刻红。
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from tests.fault_injection.fi_support import FakeLLM

    ctx = _ctx(steps=(_step("AMBIGUOUS_ELEMENT"),))
    budget = LLMBudget(max_calls_per_run=2)
    try:
        result = analyze(ctx, llm=FakeLLM(script=["not json"]), budget=budget)
    except Exception:  # noqa: BLE001 — 优雅降级的反面就是「抛了」
        return False, budget.calls_used
    return (not result.hypotheses) and result.llm_calls_used == 1, \
        budget.calls_used


def run_benchmark(*, store=None, label: str = "bench") -> BenchmarkResult:
    """跑全部 7 类 case + LLM 退化探针；`store` 给了就落一行 `benchmark_runs`。"""
    t0 = time.monotonic()
    case_results: list[CaseResult] = []
    for case in BENCHMARK_CASES:
        result = analyze(case.build_context(), llm=None, budget=None)
        got = _top_evidence_kind(result)
        case_results.append(CaseResult(case_id=case.case_id,
                                       expected=case.expected, got=got,
                                       correct=(got == case.expected)))
    llm_ok, llm_calls = _llm_degradation_probe()
    out = BenchmarkResult(
        cases=tuple(case_results),
        diagnosis_accuracy=(sum(1 for c in case_results if c.correct)
                            / len(case_results)),
        llm_path_ok=llm_ok, llm_calls=llm_calls,
        duration_ms=round((time.monotonic() - t0) * 1000, 3))
    if store is not None:
        store.save_benchmark_run(
            f"bench_{uuid.uuid4().hex[:8]}", label=label, metrics=out)
    return out


def compare_benchmarks(baseline: BenchmarkResult, candidate: BenchmarkResult,
                       *, threshold: float = 0.05) -> list[str]:
    """两版对比：关键指标下降超阈值 → 返回回归清单（非空 = AGENT_REGRESSION）。

    关键指标（设计 §13.2「如 Recovery/Diagnosis 准确率」）：`diagnosis_accuracy`
    的降幅、逐 case 的翻转（对→错）、`llm_path_ok` 的断链。阈值初版 0.05
    （7 类 case 一类翻 = 0.143，必触发；阈值挡的是浮点噪声与边缘抖动）。
    """
    regressions: list[str] = []
    if candidate.diagnosis_accuracy < baseline.diagnosis_accuracy - threshold:
        regressions.append(
            f"diagnosis_accuracy 下降：{baseline.diagnosis_accuracy:.3f} → "
            f"{candidate.diagnosis_accuracy:.3f}（阈值 {threshold}）")
    baseline_by_id = {c.case_id: c for c in baseline.cases}
    for c in candidate.cases:
        was = baseline_by_id.get(c.case_id)
        if was is not None and was.correct and not c.correct:
            regressions.append(f"case {c.case_id} 翻转：命中 → 未命中")
    if baseline.llm_path_ok and not candidate.llm_path_ok:
        regressions.append("llm_path 断链：垃圾 LLM 输出不再优雅降级")
    return regressions
