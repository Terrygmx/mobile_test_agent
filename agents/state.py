"""state.py — Agent 状态机纯函数 + ESCALATED 语义（设计 §9.2/§9.4；Task 6.1 / P3-26）。

设计 §9.2 原文（线性草图）：

```text
IDLE → PLANNING → EXECUTING → OBSERVING → DECIDING → VERIFYING
异常：ANALYZING → RECOVERING → VERIFYING
严重异常：ESCALATED（触发条件：连续 N 次决策被 Guard 拦截 / LLM 判定为 CRITICAL 操作 /
          budget 耗尽但目标未达成 / 连续 LLM_INVALID_OUTPUT）
```

## 口径登记：从散文草图到转换表

散文是**线性**的，但真实执行天然是**环**的——主循环每步都是
Observe→Decide→Execute，转回 EXECUTING 才有下一步。故本表在散文基础上补四类
环/收尾边，每类都有既有调用方背书（**零改动即合法**是验收判据之一）：

| 补的边 | 为什么 | 既有调用方 |
|---|---|---|
| `DECIDING→EXECUTING`（环） | 主循环每步转回执行 | `Explorer.run()`（经 trace 记录，未来 manager 细粒度写态） |
| `VERIFYING→EXECUTING` | 单步验证过后继续下一步 | 同上 |
| `COMPLETE→IDLE`（从 PLANNING/EXECUTING/VERIFYING/ANALYZING/RECOVERING） | 「任务结束、outcome 落库」——IDLE 是任务的**静止态**，不是「没开始」 | `Explorer.run()` 正常返回写 IDLE+outcome（`explorer/agent.py`） |
| `ESCALATE→ESCALATED` 从**任何非终态** | 严重异常不挑时机（设备起不来发生在任务还是 IDLE 时） | `cmd_explore` 两条错误路径（IDLE→ESCALATED） |

**ESCALATED 是终态**：无出边。设计明文它是「系统认为需要人介入」的**正常退出
路径，不是错误态**——任务到此为止，人工接手是新的任务行，不是把这一行再推进。

## ESCALATED 的四 + 一触发（`should_escalate`）

设计 §9.2 给了四个机械触发；§9.4 另保留四类 **LLM Escalation**（Security/
Financial/Destructive/Ambiguous）——「这是安全边界不是性能优化」（设计原文），
命中即直接升级，不等连续计数。五路同时命中时按**安全优先**取最重（优先级
钉死在 `should_escalate`，理由决定人工接手时先看什么）。

F13：本模块全部纯函数，离设备可测。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Sequence

from agents.models import AgentState

__all__ = ["AGENT_STATES", "AGENT_TRANSITIONS", "AgentEvent",
           "EscalationReason", "ILLEGAL_TRANSITION", "IllegalTransition",
           "escalation_context", "successors", "should_escalate",
           "transition"]


class AgentEvent(str, Enum):
    """驱动状态转换的事件词表（转换表第二维）。

    词形与状态名呼应但不一一对应（`COMPLETE` 没有同名状态——「结束」落在
    IDLE 上，outcome 列记录为什么结束）。
    """

    PLAN = "PLAN"            # 开始规划（IDLE→PLANNING）
    EXECUTE = "EXECUTE"      # 开始/继续执行
    OBSERVE = "OBSERVE"      # 观测现场
    DECIDE = "DECIDE"        # 产出决策
    VERIFY = "VERIFY"        # 验证（单步验证或收尾核验）
    ANALYZE = "ANALYZE"      # 进入分析（失败/异常需要诊断）
    RECOVER = "RECOVER"      # 进入恢复
    COMPLETE = "COMPLETE"    # 任务结束（→ IDLE，outcome 落库）
    ESCALATE = "ESCALATE"    # 升级给人工（→ ESCALATED，任何非终态）


class IllegalTransition(ValueError):
    """非法状态转换——fail-loud（配置/编程错误按异常走，不静默）。"""


# 状态全集（供对账用：九态每态都必须有行）。
AGENT_STATES = frozenset(AgentState)

# 转换表（单一真值源）：`AGENT_TRANSITIONS[state][event] -> next state`。
# 缺行 = 该 (态, 事件) 组合非法（`transition` 据此 fail-loud）。
AGENT_TRANSITIONS: dict[AgentState, dict[AgentEvent, AgentState]] = {
    AgentState.IDLE: {
        AgentEvent.PLAN: AgentState.PLANNING,
        AgentEvent.EXECUTE: AgentState.EXECUTING,
    },
    AgentState.PLANNING: {
        AgentEvent.EXECUTE: AgentState.EXECUTING,
        AgentEvent.COMPLETE: AgentState.IDLE,
    },
    AgentState.EXECUTING: {
        AgentEvent.OBSERVE: AgentState.OBSERVING,
        AgentEvent.ANALYZE: AgentState.ANALYZING,
        AgentEvent.VERIFY: AgentState.VERIFYING,
        AgentEvent.COMPLETE: AgentState.IDLE,
    },
    AgentState.OBSERVING: {
        AgentEvent.DECIDE: AgentState.DECIDING,
        AgentEvent.ANALYZE: AgentState.ANALYZING,
    },
    AgentState.DECIDING: {
        AgentEvent.EXECUTE: AgentState.EXECUTING,
        AgentEvent.ANALYZE: AgentState.ANALYZING,
        AgentEvent.VERIFY: AgentState.VERIFYING,
    },
    AgentState.VERIFYING: {
        AgentEvent.EXECUTE: AgentState.EXECUTING,
        AgentEvent.COMPLETE: AgentState.IDLE,
    },
    AgentState.ANALYZING: {
        AgentEvent.RECOVER: AgentState.RECOVERING,
        AgentEvent.VERIFY: AgentState.VERIFYING,
        AgentEvent.COMPLETE: AgentState.IDLE,
    },
    AgentState.RECOVERING: {
        AgentEvent.VERIFY: AgentState.VERIFYING,
        AgentEvent.EXECUTE: AgentState.EXECUTING,
        AgentEvent.ANALYZE: AgentState.ANALYZING,
        AgentEvent.COMPLETE: AgentState.IDLE,
    },
    # 终态：无出边（缺行本身就是「不可再推进」的表达）。
    AgentState.ESCALATED: {},
}

# ESCALATE 从任何非终态可达——生成时合并进上表，保持它是**唯一**真值源。
for _state in AGENT_STATES:
    if _state is not AgentState.ESCALATED:
        AGENT_TRANSITIONS[_state][AgentEvent.ESCALATE] = AgentState.ESCALATED
del _state

# §9.4 保留的四类 LLM Escalation（设计原文逐字；命中即直接升级）。
LLM_POLICY_CATEGORIES = frozenset(
    {"SECURITY", "FINANCIAL", "DESTRUCTIVE", "AMBIGUOUS"})


def transition(state: AgentState | str, event: AgentEvent | str) -> AgentState:
    """状态机纯函数：`(state, event) -> next state`，非法抛 `IllegalTransition`。

    `str` 入参接受（与 `AgentState`/`AgentEvent` 的 value 对得上）——落库侧
    `update_task_state` 读出的是字符串，这里不逼调用方先转枚举。
    """
    s = AgentState(state) if not isinstance(state, AgentState) else state
    e = AgentEvent(event) if not isinstance(event, AgentEvent) else event
    row = AGENT_TRANSITIONS[s]
    if e not in row:
        legal = ", ".join(ev.value for ev in sorted(row, key=lambda x: x.value))
        raise IllegalTransition(
            f"非法状态转换：{s.value} --{e.value}--> ??（{s.value} 上的合法"
            f"事件：{legal or '无——终态'}）")
    return row[e]


def successors(state: AgentState | str) -> frozenset[AgentState]:
    """某状态的全部合法后继（`update_task_state` 的判据来源）。"""
    s = AgentState(state) if not isinstance(state, AgentState) else state
    return frozenset(AGENT_TRANSITIONS[s].values())


class EscalationReason(str, Enum):
    """升级理由（接手包里人工看到的第一个字段）。"""

    GUARD_BLOCK_STREAK = "GUARD_BLOCK_STREAK"    # 连续 N 次被 Guard 拦截
    CRITICAL_ACTION = "CRITICAL_ACTION"          # LLM 判定 CRITICAL 操作
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"        # 预算耗尽但目标未达成
    LLM_INVALID_STREAK = "LLM_INVALID_STREAK"    # 连续 LLM_INVALID_OUTPUT
    LLM_POLICY = "LLM_POLICY"                    # §9.4 四类（安全边界）


def should_escalate(*, guard_block_streak: int, llm_invalid_streak: int,
                    budget_exceeded: bool, goal_achieved: bool,
                    proposed_risk: str | None = None,
                    llm_policy_category: str | None = None,
                    guard_limit: int = 3, llm_invalid_limit: int = 2
                    ) -> EscalationReason | None:
    """设计 §9.2 的四触发 + §9.4 的四类；多触发并存按**安全优先**取最重。

    优先级（钉死）：`LLM_POLICY`（安全边界，最高）> `CRITICAL_ACTION` >
    `GUARD_BLOCK_STREAK` > `LLM_INVALID_STREAK` > `BUDGET_EXHAUSTED`。

    - `proposed_risk`：LLM 决策提议动作的风险档（P1 `effective_risk` 的输出词）。
      只有 `CRITICAL` 触发——HIGH 在探索侧由 LOW-only 闸拦（F1，那是拦截不是
      升级），生产侧由 F7 启动期挡，都不归这里管。
    - `budget_exceeded and goal_achieved` = 正常收工（F11 的 `*_BUDGET_EXCEEDED`
      终态），**不**升级——升级语义是「系统自己走不下去了」。
    - `guard_limit` / `llm_invalid_limit` 缺省对齐 policy 初版值
      （`config/policy.yaml` 的 escalation 段），调用方从 `load_policy()` 取真值。
    """
    if llm_policy_category is not None:
        if llm_policy_category in LLM_POLICY_CATEGORIES:
            return EscalationReason.LLM_POLICY
    if proposed_risk == "CRITICAL":
        return EscalationReason.CRITICAL_ACTION
    if guard_block_streak >= guard_limit:
        return EscalationReason.GUARD_BLOCK_STREAK
    if llm_invalid_streak >= llm_invalid_limit:
        return EscalationReason.LLM_INVALID_STREAK
    if budget_exceeded and not goal_achieved:
        return EscalationReason.BUDGET_EXHAUSTED
    return None


def escalation_context(*, goal: str,
                       executed_steps: Sequence[Mapping[str, Any]],
                       reason: EscalationReason | None,
                       detail: str = "") -> dict[str, Any]:
    """人工接手包（设计 §9.2：ESCALATED 必附「足够上下文供人工快速接手」）。

    三要素：当前 goal、已执行步骤、拦截/升级原因。`reason=None`（没理由的
    升级）在构造上拒绝——没理由的升级让人无从下手，那是错误不是状态。
    """
    if reason is None:
        raise ValueError("ESCALATED 必附升级理由（接手包三要素之一）——"
                         "没理由的升级是错误，不是状态")
    return {"goal": goal,
            "executed_steps": [dict(s) for s in executed_steps],
            "reason": reason.value, "detail": detail}
