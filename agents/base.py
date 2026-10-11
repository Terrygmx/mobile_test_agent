"""base.py — Subagent 基类：绑定统一工具层（设计 §9.1/§10；Task 6.2 / P3-27）。

设计 §9.1：三个 Subagent（Planning / Exploration / Diagnosis）的所有工具调用
都落到**同一个** `Executor + Guard`，没有任何 Subagent 专属的「绕过」路径（F12）。

## 本类的两条构造纪律

1. **没有 executor / driver / device_session / guard 形参**——绕过 toolkit 的
   通道在类型层面**写不出来**（不是「传了会报错」，是「没地方传」）。执行能力
   只经构造注入的 `AgentToolkit` 一条路。
2. **`llm_budget` 必须就是 P1 `LLMBudget` 实例**（F4）——任务级独立配额，
   熔断逻辑不重写。不给 = 纯规则路径（如实是 `None`，不造假预算）。

## 「同一个 Executor + Guard」的口径（Manager 的装配契约）

toolkit **实例**每个 Subagent 一个（`task_id` 烤在 toolkit 里，`agent_trace`
才能按任务归因——共享一个 toolkit 会让三方的 trace 全记到同一个 task_id 下，
那是审计事故）；但 toolkit 的 `guard` / `executor` **依赖对象**必须同一
（`is` 同一性）——「落到同一个 Executor + Guard」说的是这条链只有一条，不是
「实例只有一份」。Manager（Task 6.4）按此装配。

## 每次调用留痕（§10 的 Audit 层）

toolkit 只在 Guard **BLOCK** 时写 trace（`_audit_block`）；本基类对**每次**
`call` 追加一条 `agent_trace`（decision 带 tool/tier，guard_result 带裁决，
result 带结论）——审计要能回答「这个 Subagent 干过什么」，只有 BLOCK 的痕迹
不够。kwargs 的值**不记**（与 `_audit_block` 同一条：值可能是密码/验证码）。
"""
from __future__ import annotations

from agents.models import AgentState
from agents.state import AgentEvent, transition
from agents.tools import TOOL_ASSET_TIER, AgentToolkit
from llm.budget import LLMBudget

__all__ = ["Subagent"]


class Subagent:
    """三个 Subagent 的公共基座。**唯一执行通路 = 构造注入的 toolkit**（F12）。

    本类不直接实例化（无业务行为）——`subagents/` 下的
    `PlannerAgent` / `ExplorerAgent` / `DiagnosisAgent`（Task 6.4）是它的
    三个消费者（落点理由见该包 docstring）。
    """

    def __init__(self, *, agent_db, task_id: str, toolkit: AgentToolkit,
                 llm_budget: LLMBudget | None = None) -> None:
        # F12：执行通路在构造上唯一。`isinstance` 而不是鸭子类型——能 call 的
        # 东西很多，但只有 AgentToolkit 保证了 Guard 链（三表 + BLOCK 留痕）。
        if not isinstance(toolkit, AgentToolkit):
            raise TypeError(
                "Subagent 只经 AgentToolkit 执行动作（F12：唯一执行通路）——"
                f"直接注入 executor/driver/guard 在构造上不可达，"
                f"got {type(toolkit).__name__}")
        # F4：预算就是 P1 LLMBudget 实例（不新写计数器）。
        if llm_budget is not None and not isinstance(llm_budget, LLMBudget):
            raise TypeError(
                "llm_budget 必须就是 P1 LLMBudget 实例（F4：不新写计数器）——"
                f"got {type(llm_budget).__name__}")
        if agent_db.get_task(task_id) is None:
            raise ValueError(f"no such agent task: {task_id}")
        self._toolkit = toolkit
        self._agent_db = agent_db
        self._task_id = task_id
        # 升级触发的两个连续计数（Task 6.4 的 manager.assess 消费）：
        # - guard_block_streak：call() 返回 BLOCK 时 +1，非 BLOCK 清零（「连续」）；
        # - llm_invalid_streak：LLM 产出无法用（包装层在底层模块报「没有可用
        #   假设」等）时经 note_llm_invalid() +1，产出正常时清零。
        self.guard_block_streak = 0
        self.llm_invalid_streak = 0
        # 公开：manager/报告读用量；`None` = 纯规则路径（不装假预算）。
        self.llm_budget = llm_budget

    # --- 执行通路 ------------------------------------------------------------

    @property
    def task_id(self) -> str:
        return self._task_id

    def _ensure_runnable(self) -> None:
        """**不重试不放宽**（设计 §9.1）：已 ESCALATED 的任务拒绝再跑。

        升级是终态——人工接手是**新的任务行**，不是把这一行重开。fail-loud
        而不是悄悄重试：静默重试正是升级语义要防的「系统自己硬扛」。
        """
        task = self._agent_db.get_task(self._task_id)
        if task is not None and task.state is AgentState.ESCALATED:
            raise RuntimeError(
                f"task {self._task_id} 已 ESCALATED（outcome="
                f"{task.outcome!r}）——升级是终态，不重试不放宽（设计 §9.1）；"
                f"人工接手请新建任务行")

    def _apply(self, event: AgentEvent) -> AgentState:
        """按转换表推进任务状态（读当前态 → transition → 落库）。"""
        task = self._agent_db.get_task(self._task_id)
        current = task.state if task is not None else AgentState.IDLE
        nxt = transition(current, event)
        self._agent_db.update_task_state(self._task_id, nxt)
        return nxt

    def note_llm_invalid(self) -> None:
        """底层模块报告「LLM 产出无法用」时 +1（manager.assess 消费）。"""
        self.llm_invalid_streak += 1

    def call(self, tool: str, **kwargs):
        """经 toolkit 调一个工具 + **每次留痕**（§10 的 Audit 层）。

        留痕失败不让调用结果丢失（先拿结果、后写库；写库炸了如实上抛——
        审计断链比一次调用结果更重要，与本仓 BLOCK 留痕的口径一致）。
        """
        result = self._toolkit.call(tool, **kwargs)
        if result.guard != "BLOCK":
            # BLOCK 的留痕 toolkit 已经写了（`_audit_block`：tool/action/risk/
            # 拦截原因，视角是「它想干什么」）——这里再写一条是重复审计，跳过。
            # 非 BLOCK 的每次调用都写（结果视角：OK / failure_type）。
            self._agent_db.append_trace(
                self._task_id,
                state=AgentState.EXECUTING,
                observation={"tool": tool, "tier":
                             TOOL_ASSET_TIER[tool].value},
                decision={"tool": tool},
                guard_result=result.guard,
                result=("OK" if result.ok else
                        (result.failure_type or "ERROR")))
        # 「连续」计数：BLOCK +1，非 BLOCK（ALLOW / 结果失败）清零——
        # 隔着一次成功动作的两次拦截不构成「连续 N 次决策被拦」。
        self.guard_block_streak = (self.guard_block_streak + 1
                                   if result.guard == "BLOCK" else 0)
        return result
