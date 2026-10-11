"""subagent.py — Planning Subagent（设计 §9.1；Task 6.4 / P3-29）。

「只产出 TestPlan，不执行」（设计 §9.1 原文）：`plan()` 是 `planner.plan`
（M2 本体）的**薄封装**——不新写评分/排序/LLM 门槛，只补任务状态机的外壳
（IDLE→PLANNING→IDLE）与升级检查。F3/F4 的复用断言由本体自己的测试持有，
这里只钉「真的调的是本体」（import 级）。

落点说明见 `subagents/__init__.py`（plan 原写 `agents/planner_agent.py`，
两条分层守卫都不允许——偏离登记）。薄封装本身只有一层外壳：
状态机推进 + 委托本体，不新写任何业务规则。
"""
from __future__ import annotations

from agents.base import Subagent
from agents.state import AgentEvent

__all__ = ["PlannerAgent"]


class PlannerAgent(Subagent):
    """Planning Subagent。无设备工具（本类的正常用法不注入 executor）——
    它的工具面只有候选创建类与只读知识类。"""

    def plan(self, planner_input, *, knowledge, cases, metadata, repository,
             llm=None, budget=None, base_metadata=None,
             no_history_reason=None):
        """委托 `planner.plan`（M2 本体），外壳：PLAN→PLANNING → COMPLETE→IDLE。

        异常**不上报升级**（由 manager 决定）——任务停在 PLANNING，
        manager 捕到后走 `escalate`（转换表允许 PLANNING→ESCALATED）。
        """
        from planner.planner import plan as _plan     # M2 本体（复用不重写）

        self._ensure_runnable()
        self._apply(AgentEvent.PLAN)                  # IDLE → PLANNING
        result = _plan(planner_input, knowledge=knowledge, cases=cases,
                       metadata=metadata, repository=repository, llm=llm,
                       budget=budget or self.llm_budget,
                       base_metadata=base_metadata,
                       no_history_reason=no_history_reason)
        self._apply(AgentEvent.COMPLETE)              # PLANNING → IDLE
        return result
