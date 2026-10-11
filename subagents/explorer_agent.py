"""explorer_agent.py — Exploration Subagent（设计 §9.1；Task 6.4 / P3-29）。

「只在 Sandbox 执行，Budget 约束」（设计 §9.1 原文）：sandbox-only 与四维预算
都在 `mta explore` 的两层前置（F7）与 `explorer.state.ExplorationBudget`（F11）
里——本薄封装**不重复实现**，只把 `explorer.Explorer`（M4 本体）抬到 Subagent
底座上。任务状态由 `Explorer.run()` 自己管（IDLE→EXECUTING→IDLE，历史行为，
转换表让它合法）——薄封装不重复写态。
"""
from __future__ import annotations

from agents.base import Subagent

__all__ = ["ExplorerAgent"]


class ExplorerAgent(Subagent):
    """Exploration Subagent。执行通路 = 构造注入的 toolkit（F12，基类保证）。"""

    def explore(self, *, graph, decider, repository, budget=None,
                build: str = "local", loop_window=None,
                max_repeated_state=None, discovery_enabled: bool = False):
        """委托 `explorer.Explorer.run()`（M4 本体）。预算缺省用任务级那份
        （`llm_budget`；四维 budget 由调用方从 policy 装配传入）。

        异常**不上报升级**（由 manager 决定）——`Explorer.run()` 中途抛错时
        任务停在 EXECUTING，manager 捕到后走 `escalate`（EXECUTING→ESCALATED
        合法）。
        """
        from explorer.agent import Explorer            # M4 本体（复用不重写）

        self._ensure_runnable()
        explorer = Explorer(
            toolkit=self._toolkit, graph=graph, agent_db=self._agent_db,
            task_id=self.task_id, decider=decider, repository=repository,
            build=build, loop_window=loop_window,
            max_repeated_state=max_repeated_state, budget=budget,
            llm_budget=self.llm_budget, discovery_enabled=discovery_enabled)
        return explorer.run()
