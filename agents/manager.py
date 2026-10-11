"""manager.py — Test Manager（设计 §9.1；Task 6.4 / P3-29）。

设计 §9.1 的协调者：编排 Planning / Exploration / Diagnosis 三个 Subagent，
统一持有升级路径。

```text
Test Manager（协调）
   ├── Planning Agent   —— 只产出 TestPlan，不执行
   ├── Exploration Agent —— 只在 Sandbox 执行，Budget 约束
   └── Diagnosis Agent  —— 只读 Trace/Logs，产出 Hypothesis
```

## 升级处置 = 停止并输出人工接手包，**不重试不放宽**

`assess()` 是 `agents/state.py::should_escalate` 的**唯一消费点**（连同两个
连续计数：`Subagent.guard_block_streak` / `Subagent.llm_invalid_streak`）。
命中 → `escalate()`：接手包（goal / 已执行步骤 / 原因）整份进 `agent_trace`
（append-only 审计），任务推进到 `ESCALATED`（转换表允许从任何非终态），
outcome 落升级理由。已 ESCALATED 的任务拒绝再次升级/运行（fail-loud）——
人工接手是**新的任务行**，不是把这一行重开。

## 装配口径（F12 的「同一个 Executor + Guard」）

toolkit **实例**每个 agent 一个（task_id 烤在 toolkit 里，trace 按任务归因）；
guard/executor **依赖对象**必须同一——这由 `toolkit_factory` 的调用方保证
（CLI 层建一次 guard/executor 闭在工厂里），本类不碰设备依赖。
"""
from __future__ import annotations

import uuid
from typing import Callable

from agents.models import AgentState
from agents.policy_config import PolicyConfig, load_policy
from agents.state import EscalationReason, should_escalate
from agents.storage import SQLiteAgentStore

__all__ = ["TestManager"]


class TestManager:
    """三 Subagent 的协调者。设备无关（toolkit 经工厂注入，可离线测）。"""

    def __init__(self, *, agent_db: SQLiteAgentStore, goal: str,
                 toolkit_factory: Callable[[str], object] | None = None,
                 policy: PolicyConfig | None = None) -> None:
        self._db = agent_db
        self.goal = goal
        self._toolkit_factory = toolkit_factory
        # policy 缺省读真值源（F7 同款：不给路径读默认，读不到用内置默认）。
        self.policy = policy or load_policy()

    # --- 装配 ----------------------------------------------------------------

    def spawn(self, agent_cls, *, agent_type: str, budget=None):
        """建任务行 + 独立 `LLMBudget`（F4）→ 返回 agent 实例。

        预算缺省从 policy 的探索 LLM 配额取（三 agent 同段初版；调用方可
        覆盖）。**每个 agent 一份**——一个耗尽不得挤占其余（矩阵 #18 后半）。
        """
        from llm.budget import LLMBudget

        task_id = f"{agent_type[:3]}_{uuid.uuid4().hex[:8]}"
        self._db.create_task(task_id, agent_type=agent_type, goal=self.goal)
        llm_budget = budget or LLMBudget(
            max_calls_per_run=self.policy.exploration.max_llm_calls)
        toolkit = (self._toolkit_factory(task_id)
                   if self._toolkit_factory is not None else None)
        return agent_cls(agent_db=self._db, task_id=task_id,
                         toolkit=toolkit, llm_budget=llm_budget)

    # --- 升级路径（§9.1 / §9.2 / §9.4） ---------------------------------------

    def assess(self, agent, *, budget_exceeded: bool = False,
               goal_achieved: bool = False, proposed_risk: str | None = None,
               llm_policy_category: str | None = None) -> dict | None:
        """`should_escalate` 的唯一消费点。命中 → escalate 并返回接手包；
        未命中 → None（调用方继续正常流程）。"""
        reason = should_escalate(
            guard_block_streak=getattr(agent, "guard_block_streak", 0),
            llm_invalid_streak=getattr(agent, "llm_invalid_streak", 0),
            budget_exceeded=budget_exceeded, goal_achieved=goal_achieved,
            proposed_risk=proposed_risk,
            llm_policy_category=llm_policy_category,
            guard_limit=self.policy.escalation.guard_blocks_before_escalate,
            llm_invalid_limit=self.policy.escalation.llm_invalid_before_escalate)
        if reason is None:
            return None
        return self.escalate(
            agent, reason,
            detail={"GUARD_BLOCK_STREAK":
                    f"连续 {agent.guard_block_streak} 次决策被 Guard 拦截",
                    "LLM_INVALID_STREAK":
                    f"连续 {agent.llm_invalid_streak} 次 LLM 产出无法用",
                    }.get(reason.value, ""))

    def escalate(self, agent, reason: EscalationReason, *,
                 detail: str = "") -> dict:
        """升级：接手包进 trace（append-only）+ 任务推进 ESCALATED。

        已 ESCALATED → fail-loud（重复升级是编程错误；终态不可再推进，
        转换表同样会拦）。
        """
        from agents.state import escalation_context

        task = self._db.get_task(agent.task_id)
        if task is None:
            raise ValueError(f"no such agent task: {agent.task_id}")
        if task.state is AgentState.ESCALATED:
            raise RuntimeError(
                f"task {agent.task_id} 已 ESCALATED——终态不可再升级"
                f"（不重试不放宽，设计 §9.1）")
        trace = self._db.get_trace(agent.task_id)
        package = escalation_context(
            goal=task.goal,
            executed_steps=[{"step_index": e.step_index,
                             "decision": e.decision,
                             "guard_result": e.guard_result,
                             "result": e.result} for e in trace],
            reason=reason, detail=detail)
        # 接手包整份落 agent_trace（append-only：审计要能回答「为什么停」）。
        self._db.append_trace(
            agent.task_id, state=AgentState.ESCALATED, goal=task.goal,
            decision={"reason": package["reason"], "detail": detail,
                      "goal": package["goal"],
                      "executed_steps": package["executed_steps"]},
            result="ESCALATED")
        self._db.update_task_state(agent.task_id, AgentState.ESCALATED,
                                   outcome=reason.value)
        return package
