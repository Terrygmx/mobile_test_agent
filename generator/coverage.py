"""coverage.py — Coverage Gap 计算（设计 6.3；Task 3.2 / P3-10）。

```python
coverage_gap(graph, existing_tests) -> list[UncoveredTransition]
```

## 全集为什么是 Runtime Graph（对设计 6.3 字面的一处偏离，已登记）

设计原文写 `graph: ScreenGraph`、docstring 写「**Source Graph** 中存在、但现有
用例从未触发过的 Transition」。本次取 **Runtime Graph**（`graph_query` 的返回值），
理由两条：

1. 「**触发**」是运行时概念——一条转移「存在」不等于它被跑到过；
2. 实测 `build_source_graph` 的转移**恒为 0**：当前 metadata 格式不含任何导航
   声明（`graph/builder.py` 的模块 docstring 已逐关键词扫过并注明）。拿它当
   全集，`coverage_gap` 会**恒返回空列表**——形式合法、语义等于没有，
   正是本仓反复踩的那一类。

⚠️ 因此本函数**拒绝 `source_of='source'` 的图**（`ValueError`）：静默接受一张
恒空的图，等于把「源图没有导航声明」伪装成「没有 gap」。等扫描器开始输出导航
声明，那时是**改这里**（并取并集）的立项，不是现在放宽。

## 「已覆盖」为什么是静态推导（不读 trace）

Runtime 图的每条转移**都来自已执行过的 trace**：以它为全集、又拿它当「已覆盖」
集，是自己减自己 → 恒为空，判据没有鉴别力。所以「已覆盖」从**用例文本**推：
用例声明了 `wait_for screen:X`（或 `postcondition screen:X`）即声明「到达过 X」，
相邻两个到达点 + 触发动作 = 一条转移。

实测：真语料 20 个用例推导出的覆盖集与真 trace 建的 runtime 图 **8/8 吻合、
零误报**；去掉 `login_again_001` 后如实多出 1 条 gap
（`LoginView→HomeView@tap:LoginView.login_button`）。

**trigger 形状复用 `graph/builder.py::_trigger_of`**（`action:target_id`），
不另写一份拼接——plan §2 第 1 条的「同一套逻辑」要求。

## 分类标签是附注，不是分类器（plan Steps 明文）

设计 6.3 的七类（Happy Path / Boundary / Negative / Exception / State
Transition / Concurrency-Timing / Recovery）本次只做**初版启发**：按转移的形状
打一个标，供后续生成排序参考。**不建立独立分类体系**，也不假装这个标签有语义
保证。
"""
from __future__ import annotations

from dataclasses import dataclass

from graph.builder import _trigger_of
from graph.models import RUNTIME, RuntimeGraph, TraceStep

__all__ = [
    "CATEGORY_HAPPY_PATH", "CATEGORY_BRANCHING", "CATEGORY_ERROR_RECOVERY",
    "UncoveredTransition", "coverage_gap", "covered_transitions",
]

# 分类标签（设计 6.3 七类的**初版启发**：只取本次能由转移形状判别的三类）
#
# ⚠️ **判据只有「触发动作的类型」**（`_classify(trigger)` 只拿到 trigger 串）。
# 早先的 docstring 声称「不是从详情返回」「目标屏是返回路径」——那需要
# `to_screen` 与一张「返回路径表」，`_classify` **结构上拿不到**，属声称宽于
# 实现（review_p3_task32 P3-6）。实测反例：`DetailView→HomeView@tap:...back_cell`
# 会被打成 `Happy Path`——它**确实是**一条回边，只是触发动作不是 `back`。
# 下方 docstring 只描述**实际判据**；要让分类名副其实，是给 `_classify` 补
# 屏侧上下文（或改叫别的标签）的立项。
CATEGORY_HAPPY_PATH = "Happy Path"
"""触发动作**不是** `back` / 重启动作——即绝大多数「点一下到下一个屏」。

⚠️ 这是**兜底标签**，不是「真的是 happy path」的断言：`tap:back_cell` 这类
返回动作同样是 `tap`，会被打上本标签。
"""
CATEGORY_BRANCHING = "State Transition"
"""触发动作是 `back`（返回）。"""

CATEGORY_ERROR_RECOVERY = "Recovery"
"""触发动作是 `terminate_app` / `launch_app`（重启动作 → 恢复语义）。"""

_RECOVERY_ACTIONS = frozenset({"terminate_app", "launch_app"})
_BACK_ACTIONS = frozenset({"back"})

# 否定型等待条件：`wait_for screen:X condition=<这些>` **不表示到达过 X**。
# 设计 6.3 的 WaitSpec 条件矩阵里，屏语义下会成立的「否定」只有 `not_exists`；
# `disabled` 对屏不成立（屏没有 disabled 态），一并排除以防将来 schema 放开。
NEGATIVE_WAIT_CONDITIONS = frozenset({"not_exists", "disabled"})


@dataclass(frozen=True)
class UncoveredTransition:
    """一条「图里有、用例没覆盖」的转移。

    `observed_count` 取自 runtime 图：这条转移**真实被跑到过**多少次——它是
    「值得补一条用例」的权重依据（跑得越多的路径越该有回归），不是本函数
    算出来的判据。
    """

    from_screen: str
    to_screen: str
    trigger: str = ""
    observed_count: int = 0
    category: str = CATEGORY_HAPPY_PATH

    def __post_init__(self) -> None:
        # 与 `ScreenNode` / `ScreenTransition` 同款纪律：屏名必须是非空 str。
        # 放行 `42` 或 `""` 会让 gap 里出现一条**永远对不上图的假转移**——
        # 「过滤生效了只是图里没有」与「字段形态坏了」在结果上长得一样。
        for name in ("from_screen", "to_screen"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(
                    f"{name} 必须是非空 str，got {value!r}")
        if not isinstance(self.trigger, str):
            raise ValueError(f"trigger 必须是 str，got {self.trigger!r}")
        # ⚠️ `bool` 是 `int` 子类：`observed_count=True` 会悄悄变成 1 次观测
        # （MEMORY §5.A 明文点名要显式排除的一类）。
        if (not isinstance(self.observed_count, int)
                or isinstance(self.observed_count, bool)):
            raise ValueError(
                f"observed_count 必须是 int（不含 bool），got "
                f"{self.observed_count!r}")
        if self.observed_count < 0:
            raise ValueError(f"observed_count 不能为负: {self.observed_count}")


def _classify(trigger: str) -> str:
    """初版启发：按触发动作的形状打标（**附注**，不是分类器）。"""
    action = trigger.split(":", 1)[0] if trigger else ""
    if action in _RECOVERY_ACTIONS:
        return CATEGORY_ERROR_RECOVERY
    if action in _BACK_ACTIONS:
        return CATEGORY_BRANCHING
    return CATEGORY_HAPPY_PATH


def _step_trigger(step) -> str | None:
    """用例步骤 → trigger 形状（复用 `_trigger_of`，不另写拼接）。

    只有**动作步骤**能当触发（`tap` / `back` / `input`…）；`wait_for` /
    `assertion` 是观测，不是触发。

    ⚠️ **这是「实测如此」，不是「构造保证」**（review_p3_task32 P3-8）：
    真 trace 的 8 条转移实测全是动作步骤（`tap` 7 / `back` 1，零 wait/assert），
    **但 runtime 侧的 trigger 取值域本来更宽**——`graph/builder.py::_trigger_of`
    取的是「紧邻前一步」，那一步**可以是任何 `step_type`**。真语料里一条都没
    命中，所以今天的覆盖推导与 runtime 图对齐；一旦 trace 出现
    `wait_for:` 前缀的 trigger，这里会**多出一条覆盖不到的（假）gap**。
    真实语料命中 **0** 次；Task 3.4 接线后若真出现，升级为 P2 并改这里。

    复用方式是**造一个真的 `TraceStep`** 喂给 `_trigger_of`，而不是在这里再写
    一遍 `f"{action}:{target}"`——「trigger 形状」这个概念只许有一处实现。
    """
    action = getattr(step, "action", None)
    if action is None:
        return None
    target = getattr(step, "target", None)
    # ⚠️ **不用 `getattr(target, "id", "")` 兜底**（MEMORY §5.A 同形第 5 次）：
    # 传错形态时它会塌成 `action:` —— 一条 trigger 为空串的伪转移看起来
    # 「正常」，实则永远匹配不上图上的任何转移。`ActionStep.target` 要么是
    # `TargetRef`（有 `.id`）要么是 `None`（无目标动作），直接取属性即可。
    target_id = target.id if target is not None else ""
    if not isinstance(target_id, str):
        raise ValueError(f"target.id 必须是 str，got {target_id!r}")
    return _trigger_of(TraceStep(testcase_run_id=0, step_index=0,
                                 step_type=action, target_id=target_id))


def _arrivals(tc) -> list[tuple[str, str]]:
    """用例声明的屏到达序列 `[(screen_id, trigger)]`。

    到达点的两种写法（与 runtime 侧 `observed_screens` 的两类证据对应）：
      - `wait_for screen:X`（**成功**的 wait 才意味着到达——用例文本里无法判
        成败，按声明算）；
      - `postcondition screen:X`（动作后的目标状态，同样是「到过 X」的声明）。

    `trigger` = 到达点之前**最近的一个动作**（跳过中间的 wait/assertion）。
    """
    out: list[tuple[str, str]] = []
    pending: str | None = None
    for step in getattr(tc, "steps", []) or []:
        action = getattr(step, "action", None)
        # 1) postcondition screen:X —— 触发就是本步骤自己的动作
        post = getattr(step, "postcondition", None)
        if (post is not None and action is not None
                and getattr(getattr(post, "target", None), "type", None)
                == "screen"):
            out.append((post.target.id, _step_trigger(step) or ""))
            pending = None
            continue
        # 2) wait_for screen:X
        wait = getattr(step, "wait_for", None)
        if wait is not None:
            tgt = getattr(wait, "target", None)
            # ⚠️ **否定等待不是到达**（review_p3_task32 P2-1）：`not_exists`
            # 断言的是「X **不**在这里」——真语料 `suites/account/
            # login_negative_002.yaml` 就是这个形态（空密码登录失败后断言
            # `screen:HomeView not_exists`）。把它算成到达会**凭空造一条覆盖**，
            # 让真 gap 静默塌成 0（「没覆盖」这一侧的错误，正是 fail-loud 的对象）。
            # `disabled` 在屏语义下不成立（屏没有 disabled 态），一并排除。
            if (getattr(tgt, "type", None) == "screen"
                    and wait.condition not in NEGATIVE_WAIT_CONDITIONS):
                out.append((tgt.id, pending or ""))
            pending = None
            continue    # wait 不是触发，不算「最近动作」
        # 3) 动作步骤 → 记下来当后续到达点的触发
        if action is not None:
            pending = _step_trigger(step)
            continue
        # 4) 裸 assertion（无 action）→ 不是到达点也不是触发
    return out


def covered_transitions(existing_tests) -> set[tuple[str, str, str]]:
    """用例集声明的转移集合 `{(from, to, trigger)}`（静态推导，不读 trace）。

    与 `coverage_gap` 共用这一个推导——「覆盖」只有一份定义。
    """
    out: set[tuple[str, str, str]] = set()
    for tc in existing_tests or ():
        arrivals = _arrivals(tc)
        for (a, _ta), (b, tb) in zip(arrivals, arrivals[1:]):
            if a == b:
                # 同一屏连续观测 = 一次访问，不产生自环转移（与 runtime 图同款）
                continue
            out.add((a, b, tb))
    return out


def coverage_gap(graph: RuntimeGraph,
                 existing_tests) -> list[UncoveredTransition]:
    """**纯函数**：图里有、但现有用例从未覆盖的转移（设计 6.3）。

    | 参数 | 语义 |
    |---|---|
    | `graph` | **Runtime Graph**（`source_of='runtime'`）；传 source 图 → `ValueError` |
    | `existing_tests` | 已解析的 `TestCase` 列表（`pipeline.discover()` 的产物） |

    返回按 `(from, to, trigger)` 排序（**确定性**：同样输入必得同样顺序，
    Task 3.4 的生成顺序与 Task 3.5 的展示顺序都依赖这一点）。
    """
    if graph.source_of != RUNTIME:
        raise ValueError(
            f"coverage_gap 只接受 runtime 图（转移全集来自实际到达过的路径），"
            f"got source_of={graph.source_of!r}。Source Graph 的转移当前恒为空"
            f"（metadata 无导航声明）——**不静默返回空 gap**（那会把「源图没有"
            f"导航声明」伪装成「没有 gap」）")
    covered = covered_transitions(existing_tests)
    gaps = [
        UncoveredTransition(from_screen=t.from_screen, to_screen=t.to_screen,
                            trigger=t.trigger, observed_count=t.count,
                            category=_classify(t.trigger))
        for t in graph.transitions
        if (t.from_screen, t.to_screen, t.trigger) not in covered
    ]
    # ⚠️ **不假设图上无重复**：`RuntimeGraph.__post_init__` **不**去重（去重是
    # `build_runtime_graph` 建图时的行为，构造点可以直接塞重复转移进来）。
    # 直接 return 会把同一条 gap 吐两遍 → Task 3.4 按 gap 生成时会拿到重复候选。
    # 用 `UncoveredTransition` 的值语义（frozen dataclass）去重，保留首次出现。
    deduped: dict[tuple[str, str, str], UncoveredTransition] = {}
    for g in gaps:
        deduped.setdefault((g.from_screen, g.to_screen, g.trigger), g)
    return sorted(deduped.values(),
                  key=lambda g: (g.from_screen, g.to_screen, g.trigger))
