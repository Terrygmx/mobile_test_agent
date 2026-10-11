"""analyzer.py — 规则优先、LLM 补充的编排（设计 §8.2 / F10；Task 5.2 / P3-21）。

设计 §8.2 末段原文：「**规则给不出结论的**，才补充 LLM 的组合推理，仍然只
产出带 confidence 的 Hypothesis」。

所以本模块的编排是**两段**：

```text
rules = rule_based_hypotheses(ctx)
if rules:                      → 直接返回（**LLM 零调用**）
elif assertion_only(ctx):      → 返回空（P1 的既有结论，诊断无事可做）
else:                          → 一次 LLM 调用，产 origin="llm_hypothesis"
```

## 三条不变量

1. **规则命中 → LLM 零调用**（矩阵 #14）：crash 场景是这条的钉子——省成本，
   也更可信（确定性判据比组合推理可复核）。
2. **LLM 输出仍须挂证据**（`llm_hypothesis_only`，权重表里最低的 1 分）：
   `Hypothesis` 的空证据闸门对 LLM 路径**同样**生效——「LLM 觉得」不是证据，
   「LLM 基于这些观测给出的假设」才是（观测本身进 `EvidenceItem.source`）。
3. **LLM 失败只降级、不抛**（与 `planner/planner.py::_llm_pass` 同款）：
   provider 故障 / 输出不可解析 → 返回已产出的假设（可能为空）+ 一条 note，
   诊断照常收尾。预算扣减走 P1 `LLMBudget`（F4，不新写计数器）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from diagnosis.classifier import is_assertion_only_failure, \
    rule_based_hypotheses
from diagnosis.models import EvidenceItem, Hypothesis

__all__ = ["AnalysisResult", "analyze"]


@dataclass(frozen=True)
class AnalysisResult:
    """一次诊断分析的结论（CLI / Task 5.3 的读取口）。"""

    hypotheses: tuple[Hypothesis, ...] = ()
    llm_calls_used: int = 0
    notes: tuple[str, ...] = ()
    # 规则是否给过结论（诊断「为什么没调 LLM」的可查依据）
    rule_hit: bool = False
    audit: tuple[dict, ...] = field(default_factory=tuple)


def analyze(ctx, *, llm=None, budget=None,
            prompt_builder=None, parser=None) -> AnalysisResult:
    """设计 §8.2 的编排。**规则优先，规则空结果才调 LLM**。

    | 参数 | 说明 |
    |---|---|
    | `llm` / `budget` | **两个都给**才调用 LLM（与 `planner.plan` 同款门槛）——缺任一个即跳过 LLM，只返回规则结果 |
    | `prompt_builder` / `parser` | 可注入（默认 `llm.prompt.build_diagnosis_prompt` / `llm.parser.parse_hypotheses`）——单测传替身，**不 mock provider** |

    ⚠️ **门槛是「两个都给」**（照抄 `planner.plan` 的理由）：只给 `llm` 而不给
    `budget` 时不调用——静默造一个预算对象会让「这次调用有没有被记账」有两个
    来源。预算扣减走 `try_acquire()` 的**无参形态**（一次诊断一次调用，不是
    per-testcase）。
    """
    notes: list[str] = []
    audit: list[dict] = []

    rules = rule_based_hypotheses(ctx)
    if rules:
        # 矩阵 #14：规则命中 → LLM 零调用。这是**省钱也是可信度**的选择
        # （确定性判据可复核，组合推理不可）。
        notes.append(f"规则命中 {len(rules)} 条假设 → 不调用 LLM（设计 §8.2）")
        return AnalysisResult(hypotheses=tuple(rules), rule_hit=True,
                              notes=tuple(notes), audit=tuple(audit))

    if is_assertion_only_failure(ctx):
        # 设计 §8.2 末条：断言值不符是 P1 的既有结论，不进诊断流程——
        # 这里**不是**「规则没命中所以问 LLM」，而是「这次失败不归诊断」。
        notes.append(
            "全部失败步骤都是 ASSERTION_VALUE_MISMATCH——P1 的既有结论"
            "（设计 §8.2 末条），不进诊断流程，也不调用 LLM")
        return AnalysisResult(notes=tuple(notes), audit=tuple(audit))

    if llm is None or budget is None:
        notes.append(
            "规则无结论且 LLM 未启用（llm/budget 未同时注入）→ 无假设产出"
            "（不猜：没有证据的假设构造不出来，F10）")
        return AnalysisResult(notes=tuple(notes), audit=tuple(audit))

    return _llm_pass(ctx, llm=llm, budget=budget, notes=notes, audit=audit,
                     prompt_builder=prompt_builder, parser=parser)


def _llm_pass(ctx, *, llm, budget, notes: list[str], audit: list[dict],
              prompt_builder=None, parser=None) -> AnalysisResult:
    """一次 LLM 调用（规则给不出结论时）。失败只降级、不抛。"""
    if prompt_builder is None:
        from llm.prompt import build_diagnosis_prompt as prompt_builder
    if parser is None:
        from llm.parser import parse_hypotheses as parser

    if not budget.try_acquire():
        # 预算耗尽：如实记录，**不绕过计数直调 provider**（F4）。
        notes.append("LLM 预算耗尽（LLMBudget.try_acquire → False）→ 跳过 LLM")
        return AnalysisResult(notes=tuple(notes), audit=tuple(audit))

    prompt = prompt_builder(ctx)
    timeout = getattr(getattr(budget, "config", None), "timeout_seconds", 20)
    try:
        raw = llm.complete(prompt, timeout=timeout)
    except Exception as e:  # noqa: BLE001 — provider 故障如实降级（不吞掉诊断）
        budget.record_failure()
        notes.append(f"LLM 调用失败（{type(e).__name__}）→ 无 LLM 假设")
        return AnalysisResult(llm_calls_used=1, notes=tuple(notes),
                              audit=tuple(audit))

    parsed = parser(raw)
    hypotheses: list[Hypothesis] = []
    for h in getattr(parsed, "hypotheses", ()):
        # LLM 的每条假设都挂上**同一条**证据：`llm_hypothesis_only`
        # （权重表里最低的 1 分）——它记的是「这条结论来自 LLM 推理」这个
        # 事实本身，`source` 指向喂进去的观测。空证据闸门在这里同样生效。
        hypotheses.append(Hypothesis(
            statement=h["statement"], confidence=float(h["confidence"]),
            origin="llm_hypothesis",
            evidence=[EvidenceItem(
                kind="llm_hypothesis_only",
                detail="LLM 组合推理产出（规则无结论）",
                source=f"trace:run_{ctx.run_id}")]))
    if getattr(parsed, "ignored_fields", None):
        audit.append({"kind": "llm_ignored_fields",
                      "fields": sorted(parsed.ignored_fields)})

    if hypotheses:
        budget.record_success()
    else:
        budget.record_failure()
        notes.append("LLM 输出没有可用假设 → 无假设产出")
    return AnalysisResult(hypotheses=tuple(hypotheses), llm_calls_used=1,
                          notes=tuple(notes), audit=tuple(audit))
