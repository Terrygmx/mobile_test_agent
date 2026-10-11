"""agent.py — Bounded Exploration 的主循环（设计 7.1；Task 4.1 / P3-14）。

```text
Observe（page_source → screen_fingerprint → 图上 node 匹配，经 get_current_screen）
   ↓
Decide（decider 回调——4.1 是注入的剧本/策略，4.2 起默认换 frontier 选择）
   ↓
Guard（两层：① 探索侧 LOW-only 闸——effective_risk!=LOW 不执行、留 BLOCK
        审计、循环继续；② toolkit.call 内建的 P1 Guard——CRITICAL/blocked_
        targets/production 的兜底，BLOCK 是结果不是异常）
   ↓
Execute（同一 toolkit）
   ↓
Verify（post-observe：新 State？指纹变化？→ novelty + visited）
```

**每步落 agent_trace**（append-only，审计根基）：observation 带前后屏与
指纹，decision 带工具/目标/风险预判，guard_result 带裁决，result 带结论。

三条红线在本模块的落点：
- **F12**：设备动作的唯一通路是构造注入的 `AgentToolkit`——构造函数类型
  断言 + AST 守卫（explorer 包无直接设备访问）双保险；
- **F1**：风险判定与 Guard 是 P1 的同一个本体（`explorer/policy.py`）；
  BLOCK 的步骤**不执行但必须留痕**（toolkit 的 audit 已写一条，循环自己的
  步骤记录再写一条——两处视角不同：前者是「它想干什么」，后者是「这步的
  结果」），循环**继续**而不是死在被拦的步骤上；
- **F11 前置**：4.1 的循环只有 COMPLETED 一种终态；预算/loop/frontier 空
  的终态词表由 4.2/4.3/4.4 增量加进 `ExplorationOutcome`（登记：词表随
  任务定型，与迁移链「只向后追加」同款思路）。
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Callable

from agents.models import AgentState
from agents.tools import AgentToolkit
from explorer.policy import EXECUTION_TOOLS, action_kwargs, pre_judge
from graph.models import RuntimeGraph

__all__ = ["Decider", "ExplorationError", "ExplorationOutcome",
           "ExplorationResult", "Explorer"]


class ExplorationOutcome(str, enum.Enum):
    """探索终态词表（设计 7.2/7.3；增量定型）。

    4.1 只有 COMPLETED；4.2 加 FRONTIER_EMPTY、4.3 加四类
    `*_BUDGET_EXCEEDED`、4.4 加 LOOP_DETECTED——每项都有各自的测试与
    矩阵行，不提前占词。
    """

    COMPLETED = "COMPLETED"


class ExplorationError(RuntimeError):
    """observe 失败等**无法继续探索**的运行期错误。

    设备级故障（InfraError）由 toolkit 照原样上抛、不走这里——那是环境
    坏了，不是探索的结论；这里是「观测拿不到结论」（如 page_source 空）。
    """


# decider 契约：(observation, graph, visited) -> ProposedAction | None。
# 返回 None = 没有下一步（→ COMPLETED）。observation 是 get_current_screen
# 的 value（status/screen/visible_markers/fingerprint），visited 是**指纹**
# 列表（循环检测 4.4 与 frontier 的 novelty 都消费它）。
Decider = Callable[[dict, RuntimeGraph, list[str]], "ProposedAction | None"]


@dataclass(frozen=True)
class ExplorationResult:
    """一次探索的结论（CLI 与 Gate 断言的读取口）。"""

    outcome: ExplorationOutcome
    steps: int
    visited_fingerprints: tuple[str, ...]


class Explorer:
    """Bounded Exploration 主循环。**只经 toolkit 执行**（F12）。"""

    def __init__(self, *, toolkit: AgentToolkit, graph: RuntimeGraph,
                 agent_db, task_id: str, decider: Decider, repository,
                 build: str = "local") -> None:
        if not isinstance(toolkit, AgentToolkit):
            raise TypeError(
                "Explorer 只经 AgentToolkit 执行动作（F12：唯一执行通路）——"
                "直接注入 executor/driver 在构造上不可达")
        if agent_db is None or task_id is None:
            raise ValueError(
                "agent_db/task_id 必填——每步落 agent_trace 是审计根基（F1），"
                "没有留痕通路的探索不允许启动")
        if not callable(decider):
            raise TypeError("decider 必须可调用（Decider 契约见模块 docstring）")
        self._toolkit = toolkit
        self._graph = graph
        self._db = agent_db
        self._task_id = task_id
        self._decider = decider
        self._repository = repository
        self._build = build

    # --- 主循环 -------------------------------------------------------------

    def run(self) -> ExplorationResult:
        task = self._db.get_task(self._task_id)
        if task is None:
            raise ValueError(f"no such agent task: {self._task_id}")
        goal = task.goal
        self._db.update_task_state(self._task_id, AgentState.EXECUTING)
        visited: list[str] = []
        steps = 0
        while True:
            observation = self._observe()
            fingerprint = observation.get("fingerprint")
            if fingerprint and fingerprint not in visited:
                visited.append(fingerprint)

            action = self._decider(observation, self._graph, visited)
            if action is None:
                break
            if action.tool not in EXECUTION_TOOLS:
                raise ValueError(
                    f"decider 提议的 {action.tool!r} 不是执行类动作"
                    f"（EXECUTION_TOOLS = {sorted(EXECUTION_TOOLS)}）——"
                    f"候选类工具不是探索动作，fail-loud")
            judgment = pre_judge(action, repository=self._repository,
                                 build=self._build)
            if not judgment.auto_execute:
                # 设计 7.1：探索侧 **LOW 才允许自动执行**——P1 Guard 在
                # sandbox 下放行 HIGH（人工用例本来就可以），自主探索不能
                # 借这个口子自动跑 HIGH+。拦截点在本循环（同一个
                # effective_risk 本体判定，F1），**不进 toolkit**、动作
                # 不执行、审计留痕后继续（矩阵 #13 前半）。
                self._db.append_trace(
                    self._task_id, step_index=steps,
                    state=AgentState.DECIDING, goal=goal,
                    observation={
                        "screen": observation.get("screen"),
                        "fingerprint": fingerprint,
                    },
                    decision={
                        "tool": action.tool, "target": action.target,
                        "risk": judgment.risk, "auto_execute": False,
                        "guard_result": "BLOCK",
                        "reason": (
                            f"effective_risk={judgment.risk}：探索只自动执行"
                            f" LOW（设计 7.1），HIGH+ 交人工/审计，不自动执行"),
                    },
                    guard_result="BLOCK", result="BLOCKED")
                steps += 1
                continue
            result = self._toolkit.call(action.tool, **action_kwargs(action))

            # Verify：post-observe 看 State 变没变（设计 7.1 的「新 State？」）。
            # post-observe 失败不否定已执行的动作——降级为「验证不到」，记录后继续。
            try:
                post_obs = self._observe()
            except ExplorationError:
                post_obs = {}
            post_fp = post_obs.get("fingerprint")
            is_new = bool(post_fp) and post_fp not in visited

            self._db.append_trace(
                self._task_id, step_index=steps,
                state=AgentState.EXECUTING, goal=goal,
                observation={
                    "screen": observation.get("screen"),
                    "fingerprint": fingerprint,
                    "post_screen": post_obs.get("screen"),
                    "post_fingerprint": post_fp,
                },
                decision={
                    "tool": action.tool, "target": action.target,
                    "risk": judgment.risk,
                    "auto_execute": judgment.auto_execute,
                },
                guard_result=result.guard,
                result=self._step_result(result),
                novelty=1.0 if is_new else 0.0)
            steps += 1

            if result.guard == "BLOCK":
                # 被拦：不执行已由 toolkit 保证；本步不留新指纹，循环继续
                # （矩阵 #13 前半：拦截是「这步没做成」，不是「探索失败」）
                continue
            if post_fp and is_new:
                visited.append(post_fp)

        self._db.update_task_state(self._task_id, AgentState.IDLE,
                                   outcome=ExplorationOutcome.COMPLETED.value)
        return ExplorationResult(
            outcome=ExplorationOutcome.COMPLETED, steps=steps,
            visited_fingerprints=tuple(visited))

    # --- 内部 ---------------------------------------------------------------

    def _observe(self) -> dict:
        """一步观测（get_current_screen：屏判定 + 指纹，F8 同一套哈希）。

        观测拿不到 → `ExplorationError`（fail-loud）：没有观测就没有
        decide 的输入，装作什么都没发生继续跑只会产出没人能读懂的 trace。
        """
        result = self._toolkit.call("get_current_screen")
        if not result.ok or not isinstance(result.value, dict):
            raise ExplorationError(
                f"observe 失败（tool=get_current_screen, "
                f"ok={result.ok}, failure_type={result.failure_type}, "
                f"error={result.error!r}）——没有观测就没有 decide 的输入")
        return result.value

    @staticmethod
    def _step_result(result) -> str:
        """工具结论 → trace 的 result 词（BLOCKED / OK / 失败分类）。"""
        if result.guard == "BLOCK":
            return "BLOCKED"
        if result.ok:
            return "OK"
        return result.step_failure_type or "TOOL_FAILED"
