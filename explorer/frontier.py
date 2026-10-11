"""frontier.py — frontier 选择（设计 7.1；Task 4.2 / P3-15）。

「优先未探索 Transition，不随机点击」：每次 decide 对当前屏列 frontier，
**unexplored（source 声明、runtime 无观测、目标未访问过）优先**，explored
内部按「动过次数少者优先」（novelty 初版打分——设计明文初版需在真实运行后
校准，不假装精确）。

⚠️ 签名偏离登记（plan 写 `select_frontier(graph, visited, current_screen)`
单图入参）：「source 有 / runtime 无」与「runtime visit_count」需要**两面**
图，而 `RuntimeGraph` 的图级 `source_of` 一致性校验禁止混面
（`graph/models.py __post_init__`）——拆成显式 `source` / `runtime` 两个
入参，调用方（`make_frontier_decider` / 4.6 的 CLI 装配）分别持有两面。

⚠️ `visited` 收**屏 id** 不收指纹：指纹只做精确相等（F8），「这个屏来过没」
的可读判据是屏名；指纹视图归循环检测（4.4），两个视图在
`explorer.agent.VisitLog` 里并存。

F13：`select_frontier` 是纯函数——无 IO、无设备、同输入同输出。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from explorer.agent import ExplorationOutcome, StopDecision
from explorer.policy import ProposedAction
from graph.diff import transition_key
from graph.models import RuntimeGraph, ScreenTransition

__all__ = ["FrontierItem", "make_frontier_decider", "select_frontier"]

# unexplored 项的 novelty 初值（设计 7.1「novelty 打分初版」；explored 项是
# 1/(1+次数) ∈ (0,1]，3.0 保证 unexplored 恒居 explored 之前——即使它将来
# 也被标了部分观测，次序也只由 unexplored 布尔决定，分数只做组内排序）。
_UNEXPLORED_SCORE = 3.0


@dataclass(frozen=True)
class FrontierItem:
    """一个候选探索目标：从 `from_screen` 出发的一条转移 + 它的新颖度。"""

    transition: ScreenTransition
    novelty: float
    unexplored: bool


def select_frontier(source: RuntimeGraph, runtime: RuntimeGraph, *,
                    visited, current_screen: str) -> list[FrontierItem]:
    """当前屏的 frontier（按优先级降序；空表 = 没得可探索，不是错误）。

    - **unexplored**：source 声明了这条转移、runtime 从未观测过它、且目标屏
      不在 `visited` 里 → novelty=`_UNEXPLORED_SCORE`；
    - **explored**：runtime 有观测（或目标来过）→ novelty=`1/(1+次数+目标
      visit_count)`——动得越少越靠前；
    - 组间序由 `unexplored` 布尔决定，组内同分由转移身份串
      （`graph.diff.transition_key`，F8 复用同一身份定义）定序——**排序
      确定性**是 F13 的一部分（同输入两次调用同序，测试钉住）。
    """
    if source.source_of == runtime.source_of:
        raise ValueError(
            f"两面图来源相同（{source.source_of!r}）——frontier 需要 "
            f"source（声明面）与 runtime（观测面）各一面，混面会静默算错")
    rt_taken = {transition_key(t.from_screen, t.to_screen, t.trigger)
                for t in runtime.transitions}
    items: list[FrontierItem] = []
    for t in source.transitions:
        if t.from_screen != current_screen:
            continue
        target_node = runtime.node(t.to_screen)
        target_visits = target_node.visit_count if target_node else 0
        seen = (transition_key(t.from_screen, t.to_screen, t.trigger)
                in rt_taken) or (t.to_screen in visited)
        if seen:
            rt_t = next((r for r in runtime.transitions
                         if transition_key(r.from_screen, r.to_screen,
                                           r.trigger)
                         == transition_key(t.from_screen, t.to_screen,
                                           t.trigger)), None)
            novelty = 1.0 / (1.0 + (rt_t.count if rt_t else 0)
                             + target_visits)
            unexplored = False
        else:
            novelty = _UNEXPLORED_SCORE
            unexplored = True
        items.append(FrontierItem(transition=t, novelty=novelty,
                                  unexplored=unexplored))
    items.sort(key=lambda i: (0 if i.unexplored else 1, -i.novelty,
                              i.transition.from_screen,
                              i.transition.to_screen,
                              i.transition.trigger))
    return items


def make_frontier_decider(source: RuntimeGraph, runtime: RuntimeGraph, *,
                          action_for: Callable[[ScreenTransition],
                                               ProposedAction | None]
                          | None = None):
    """把 frontier 包成主循环的 decider（`Explorer(decider=…)` 直接可用）。

    `action_for(transition) -> ProposedAction | None`：把一条转移翻成动作
    ——trigger 的语义是 app 特定的（"tap login_button"？坐标？语音？），
    由调用方注入；**缺省**把 trigger 当 element target tap 一下（trigger
    为空则翻不成）。返回 None = 这条翻不成动作 → 跳到下一项（**None 是
    「跳过」，不是「结束」**）；全部翻不成 / frontier 空 →
    `StopDecision(FRONTIER_EMPTY)`。
    """
    if action_for is None:
        def action_for(t: ScreenTransition) -> ProposedAction | None:
            return (ProposedAction(tool="tap", target=t.trigger)
                    if t.trigger else None)

    def decide(observation: dict, graph, visited):
        items = select_frontier(source, runtime,
                                visited=list(visited.screens),
                                current_screen=observation.get("screen")
                                or "")
        for item in items:
            action = action_for(item.transition)
            if action is not None:
                return action
        return StopDecision(outcome=ExplorationOutcome.FRONTIER_EMPTY,
                            reason="当前屏没有可提议的 frontier 项"
                            f"（screen={observation.get('screen')!r}）")

    return decide
