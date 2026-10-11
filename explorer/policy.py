"""policy.py — 探索动作的候选构造与风险预判（设计 7.1；Task 4.1 / P3-14）。

两件事，都只是**薄层**：

1. **候选动作构造**：decider 产出 `ProposedAction`，这里翻成
   `AgentToolkit.call` 的 kwargs——工具层的参数词汇（`target` / `value` /
   `direction` / `condition`）在这里定形，循环本体不拼字典。
2. **风险预判**：`effective_risk`==LOW 才标 `auto_execute`，非 LOW 交 Guard
   拦截路径（`toolkit.call` 的 BLOCK 是**结果**不是异常——F1 的裁决点在
   toolkit 的 Guard，预判只是让每步的 decision 里带上风险标注，供审计与
   后续的 ESCALATED 统计消费）。

⚠️ **F1：判定本体是 `executor.policy.effective_risk`**（plan 4.1 ⚠️ 点名的
复用起点）——不新建第二套风险推导。import 级断言在
`tests/unit/test_explorer_guard.py::test_explorer_shares_the_p1_risk_function`；
explorer 包唯一放行的 `executor.*` import 就是本文件这一条（AST 守卫）。
"""
from __future__ import annotations

from dataclasses import dataclass

from agents.tools import GUARDED_TOOLS
from executor.policy import effective_risk
from testcase.schema import Risk

__all__ = ["EXECUTION_TOOLS", "PreJudgment", "ProposedAction",
           "action_kwargs", "pre_judge"]

# 探索 decider 可提议的**执行类**动作（GUARDED_TOOLS 的子集）。候选类工具
# （create_*）不是「动作」——发现事件的产生归主循环（Task 4.5），不由
# decider 提议；`run_testcase`/`run_candidate_test` 是用例执行入口，不是
# 单步探索动作，也不在此列。
EXECUTION_TOOLS: frozenset[str] = frozenset({
    "tap", "input", "swipe", "back", "wait",
    "assert_exists", "assert_text", "screenshot",
})
assert EXECUTION_TOOLS <= GUARDED_TOOLS, (
    "EXECUTION_TOOLS 必须是 GUARDED_TOOLS 的子集（探索动作一律经 Guard，F1）"
    "——两表漂移说明有人在 tools.py 动了 GUARDED_TOOLS 而没同步这里")

_TARGETED = frozenset({"tap", "input", "wait", "assert_exists", "assert_text"})


@dataclass(frozen=True)
class ProposedAction:
    """decider 产出的候选动作。字段是工具层 kwargs 的**超集**——
    没用到的字段保持 None，由 `action_kwargs` 按工具取用。"""

    tool: str
    target: str | None = None
    value: str | None = None
    direction: str | None = None
    condition: str | None = None
    expected: str | None = None


def action_kwargs(action: ProposedAction) -> dict:
    """`ProposedAction` → `AgentToolkit.call(tool, **kwargs)` 的 kwargs。

    缺必填字段（如 `tap` 没有 target）是 decider 的编程错误 → fail-loud，
    不填默认值硬跑。
    """
    if action.tool not in EXECUTION_TOOLS:
        raise ValueError(
            f"{action.tool!r} 不是执行类动作（EXECUTION_TOOLS = "
            f"{sorted(EXECUTION_TOOLS)}）——候选类工具不是探索动作")
    if action.tool in _TARGETED and not action.target:
        raise ValueError(f"{action.tool} 需要 target，got {action.target!r}")
    if action.tool == "input":
        return {"target": action.target, "value": action.value or ""}
    if action.tool == "tap":
        return {"target": action.target}
    if action.tool == "swipe":
        return {"direction": action.direction or "up"}
    if action.tool == "back":
        return {}
    if action.tool == "screenshot":
        return {}
    if action.tool == "wait":
        kw: dict = {"target": action.target,
                    "condition": action.condition or "exists"}
        if action.expected is not None:
            kw["expected"] = action.expected
        return kw
    if action.tool == "assert_exists":
        return {"target": action.target}
    # assert_text（最后一个成员；expected 必填——没有期望值的文本断言没意义）
    if action.expected is None:
        raise ValueError("assert_text 需要 expected（期望文本）")
    return {"target": action.target, "expected": action.expected}


@dataclass(frozen=True)
class PreJudgment:
    """一步动作的风险预判结果（进 agent_trace 的 decision，供审计与
    ESCALATED 统计消费；**不是**放行凭证——裁决在 toolkit 的 Guard）。"""

    risk: str
    auto_execute: bool
    screen_id: str
    element_id: str


def pre_judge(action: ProposedAction, *, repository, build: str = "local"
              ) -> PreJudgment:
    """风险预判：与工具层 Guard 的 `_guard_context` **同式**推导
    （`effective_risk(element=…, element_id=…)`，无 target 走兜底 LOW）。

    只读（resolve 是纯查询），不碰设备。判定与 toolkit 内建的 Guard **同源
    同式**（同一函数、同一输入），但裁决点不同：P1 Guard 在 sandbox 放行
    HIGH（人工用例的合法空间），自主探索的 LOW-only 闸在**本函数的调用方**
    （`explorer.agent`）——`auto_execute=False` 的步不会被发起调用。
    """
    if action.target is None:
        risk = effective_risk()
        return PreJudgment(risk=risk.name, auto_execute=risk is Risk.LOW,
                           screen_id="", element_id="")
    eff = repository.resolve(action.target, build=build)
    if hasattr(eff, "marker"):            # EffectiveScreen
        screen_id, element_id, declared = eff.id, "", None
    else:                                 # EffectiveElement
        screen_id = eff.screen
        element_id = eff.id
        declared = getattr(eff, "risk", None)
    risk = effective_risk(element=declared, element_id=element_id or None)
    return PreJudgment(risk=risk.name, auto_execute=risk is Risk.LOW,
                       screen_id=screen_id, element_id=element_id)
