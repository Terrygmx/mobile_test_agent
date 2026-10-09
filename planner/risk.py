"""risk.py — 用例风险（**转发** P1 的 `effective_risk`；Task 2.2 / P3-06）。

设计 F1 明文：「**风险判定复用 P1 `effective_risk` + Guard**，不新建风险枚举或判定
逻辑」。所以本模块**没有自己的判定**——它只做一件 P1 没做的事：

> 一条用例可能涉及多个元素，`priority_score` 需要一个**用例级**的风险值。

取值规则 = 用例所涉元素各自 `effective_risk` 的 **max**（设计 §5.3：「业务风险
（`effective_risk`，CRITICAL/HIGH 强制高优先级，**不管改动大小**）」——取 max 而非
均值，因为「一个用例里有 CRITICAL 元素」不该被同用例里的 LOW 元素稀释）。

`effective_risk` 在模块层 re-export：`test_prioritizer.py` 用 **import 级断言**
（`planner.risk.effective_risk is executor.policy.effective_risk`）钉住「转发而非
复制」——光测行为相同挡不住「有人抄了一份判定逻辑、恰好结果一样」。
"""
from __future__ import annotations

from executor.policy import effective_risk
from testcase.schema import Risk

__all__ = ["case_risk", "effective_risk"]

# 兜底：用例不涉及任何元素（例如纯 launch/terminate 的用例）→ LOW。
# 与 P1 的兜底一致（`effective_risk()` 无候选时返回 LOW）。
_DEFAULT = Risk.LOW


def case_risk(tc_meta, repository, *, build: str) -> Risk:
    """用例所涉元素的 `effective_risk` 取 **max**（无元素 → `LOW`）。

    `tc_meta.element_ids` 用 `Repository.resolve` 的语法（裸名或 `Screen.elem`）。
    每个元素的风险走 P1 的同一条路：`effective_risk(element=eff.risk,
    element_id=eff.id)`——**与 `cli/pipeline._prepare` 和 `AgentToolkit._guard_context`
    同式**（三处同式不是三份实现：函数体是同一个）。

    ⚠️ **解析不到的元素会让异常照原样上抛**（`UnknownReferenceError`），**不跳过、
    不当 LOW**：一个 CRITICAL 元素因漂移而解析失败时，静默按 LOW 计会让它在 Plan
    里沉底——**错误的方向恰好是「看不见」那一侧**。要容错的话由调用方显式处理
    （漂移判定是 Task 2.3 的 `planner/impact.py` 的职责，不是本函数顺手兜的）。
    """
    risks = []
    for element_id in getattr(tc_meta, "element_ids", ()):
        eff = repository.resolve(element_id, build=build)
        risks.append(effective_risk(element=getattr(eff, "risk", None),
                                    element_id=eff.id))
    if not risks:
        return _DEFAULT
    return max(risks, key=lambda r: r.value)
