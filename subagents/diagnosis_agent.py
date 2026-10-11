"""diagnosis_agent.py — Diagnosis Subagent（设计 §9.1；Task 6.4 / P3-29）。

「只读 Trace/Logs，产出 Hypothesis」（设计 §9.1 原文）：`analyze()` 是
`diagnosis.analyzer.analyze`（M5 本体）的**薄封装**——不新写规则/LLM 门槛/
预算扣减，只补任务状态机的外壳（EXECUTE→EXECUTING → ANALYZE→ANALYZING →
COMPLETE→IDLE）与 LLM invalid 计数。
"""
from __future__ import annotations

from agents.base import Subagent
from agents.state import AgentEvent

__all__ = ["DiagnosisAgent"]


class DiagnosisAgent(Subagent):
    """Diagnosis Subagent。只读：analyze 消费的 DiagnosisContext 是**已取到**
    的数据（取数在 `build_context`，M5 的口径）——本类不做任何设备动作。"""

    def analyze(self, ctx, *, llm=None, budget=None):
        """委托 `diagnosis.analyzer.analyze`（M5 本体）。

        LLM 被调了但产出无法用（`llm_calls_used > 0` 且零假设）→
        `note_llm_invalid()`（升级触发之一「连续 LLM_INVALID_OUTPUT」的
        计数点）；规则命中或没调 LLM 都不计数。
        """
        from diagnosis.analyzer import analyze as _analyze  # M5 本体

        self._ensure_runnable()
        self._apply(AgentEvent.EXECUTE)               # IDLE → EXECUTING
        self._apply(AgentEvent.ANALYZE)               # EXECUTING → ANALYZING
        result = _analyze(ctx, llm=llm,
                          budget=budget or self.llm_budget)
        if llm is not None and result.llm_calls_used > 0 \
                and not result.hypotheses:
            self.note_llm_invalid()
        self._apply(AgentEvent.COMPLETE)              # ANALYZING → IDLE
        return result
