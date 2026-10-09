"""prioritizer.py — 确定性优先级打分（设计 §5.2；Task 2.2 / P3-06）。

设计 §5.2 原文：

```python
def priority_score(tc: TestCaseMeta, impact: ImpactResult, history: FailureHistory, risk: Risk) -> int:
    '''
    纯函数（F13），输入全部来自 P1/P2 已有数据源：
      - impact: KnowledgeSources.impact_of(changed_element) 得到的"受影响用例"
      - history: KnowledgeSources.trace_history(filters) 统计出的历史失败率
      - risk: 用例所涉及元素的 effective_risk（P1）
    评分公式和权重是初版，需在真实数据上校准（沿用 P2 验证阈值的态度，不假装精确）。
    '''
```

设计 §5.2 末句：「LLM 的角色：对评分结果给出**自然语言解释**（写入 `reasons`），
以及在确定性评分打平手时做**次要排序**，**不允许**替代打分本身独立决定顺序」。

## 公式（初版，plan Task 2.2 给的数字）

```text
score = IMPACT_WEIGHT × [用例落在受影响集合里]      # 40
      + HISTORY_WEIGHT × 历史失败率 ∈ [0,1]         # 30
score = max(score, RISK_FLOOR[risk])                # CRITICAL→90 / HIGH→70
```

⚠️ **两组数字的算术结果：HIGH 的 70 与「impact 命中 + 历史满」的 70 会打平**
（base 上限恰好是 40+30=70）。这不是 bug——plan 的判据是「CRITICAL 用例无论
impact/history 多低都进**顶部区间**」（≥70），平局按 `order_key` 的
`testcase_id` 字典序裁决。若将来要「HIGH **严格**高于任何非 HIGH」，把
`RISK_FLOOR[HIGH]` 提到 71 或把 base 上限压到 69——**属调参，初版不动**。

## 纯函数纪律（F13）

`priority_score` **不读库、不看 LLM、不依赖时间**：四个入参就是全部输入，同输入
必同输出（有专测）。数据获取（impact / history / risk）由调用方负责——那是
Task 2.3 的编排。

## ⚠️ 对设计 sketch 的一处偏离：不物化 `ImpactResult` / `FailureHistory`

设计签名里的这两个名字**在设计与 plan 里都没有内容定义**（`graph/impact.py` 的
`ImpactRow` / `ImpactReport` 是**图 diff 报告**，不是「受影响用例集合」）。这里按
「参数实际携带的信息」落成 `Iterable[str]`（受影响用例 id）与 `float`（失败率），
**不给两个单字段的壳类型**（投机字段）。参数名与位置仍与设计一致。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from testcase.schema import Risk

__all__ = [
    "HISTORY_WEIGHT", "IMPACT_WEIGHT", "RISK_FLOOR",
    "TestCaseMeta", "order_key", "priority_score",
]

# --- 初版权重（plan Task 2.2 给的数字；**待真实数据校准**，不假装精确） ---------
IMPACT_WEIGHT = 40      # 用例落在「受影响」集合里
HISTORY_WEIGHT = 30     # × 历史失败率 ∈ [0, 1]
RISK_FLOOR: dict[Risk, int] = {Risk.CRITICAL: 90, Risk.HIGH: 70}

# 设计 §5.1：`priority: int  # 0-100`（`planner/models.py` 的 PRIORITY_MIN/MAX 是同一值）
_PRIORITY_MIN, _PRIORITY_MAX = 0, 100


@dataclass(frozen=True)
class TestCaseMeta:
    """评分用的用例元数据（设计 §5.2 的 `TestCaseMeta`）。

    - `testcase_id`：**打分用它**（是否落在受影响集合里）；
    - `element_ids`：**`planner/risk.py` 用它**（取各自 `effective_risk` 的 max）
      ——放在同一个对象上，是为了让「用例」与「它的风险」这对输入不必由调用方
      用两个平行结构各自维护。
    """

    # 名字以 `Test` 开头是设计 §5.2 的 `TestCaseMeta`（不改名）；pytest 会把导入到
    # 测试模块里的这类类当**测试类**收集 → `__test__ = False`（仓内既有手法，
    # 与 `planner/models.py` 的 `TestPlan` / `TestPlanTask` 同款）。
    __test__ = False

    testcase_id: str
    element_ids: tuple[str, ...] = ()


def priority_score(tc_meta: TestCaseMeta, impact: Iterable[str],
                   history: float, risk: Risk) -> int:
    """设计 §5.2 的确定性打分（**纯函数**，F13）。

    | 参数 | 含义 | 值域 |
    |---|---|---|
    | `tc_meta` | 用例元数据 | `TestCaseMeta` |
    | `impact` | `KnowledgeSources.impact_of(...)` 的结果（受影响用例 id） | `Iterable[str]` |
    | `history` | `trace_history` 统计出的历史失败率 | `float`，**必须 ∈ [0,1]** |
    | `risk` | 用例所涉元素的 `effective_risk`（`planner.risk.case_risk`） | `Risk` |

    `history` 越界 → `ValueError`（**不静默夹取**）：失败率 > 1 是**统计口径的 bug**
    （分子分母弄反了之类），夹到 1 会让「算错了」表现为「历史一直很差」——与
    `planner/models.py` 对 `priority ∈ [0,100]` 的处理同一条理由。

    返回值恒在 `[0, 100]`（设计 §5.1 的 `priority` 值域）。
    """
    if not isinstance(history, (int, float)) or isinstance(history, bool):
        raise ValueError(f"history 必须是 [0,1] 的数值，got {history!r}")
    if not 0.0 <= float(history) <= 1.0:
        raise ValueError(
            f"history 必须在 [0,1]（历史失败率），got {history!r}——"
            f"越界是统计口径的 bug，夹取会让「算错了」表现为「历史一直很差」")
    hit = tc_meta.testcase_id in frozenset(impact)
    score = IMPACT_WEIGHT * int(hit) + HISTORY_WEIGHT * float(history)
    floor = RISK_FLOOR.get(risk)
    if floor is not None:
        score = max(score, floor)
    return int(min(_PRIORITY_MAX, max(_PRIORITY_MIN, round(score))))


def order_key(score: int, testcase_id: str) -> tuple[int, str]:
    """排序键：**分数降序、同分按 `testcase_id` 字典序**（确定性）。

    `sorted(entries, key=lambda e: order_key(e.score, e.testcase_id))` 即可得到设计
    §5.2 要求的确定性顺序——**LLM 只许在「同一个 key」的组内做 tie-break**，
    不允许改变跨组的相对顺序（那是矩阵 #5 的判据，由 Task 2.3 的编排层执行）。
    """
    return (-score, testcase_id)
