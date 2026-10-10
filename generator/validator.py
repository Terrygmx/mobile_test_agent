"""validator.py — 候选的两道校验（设计 6.1；Task 3.4 / P3-12）。

设计 6.1 的次序里，Schema Validation 与 Semantic Validation 是**两道独立**的闸门：

```
LLM 生成候选步骤序列
        ↓
Schema Validation：直接复用 mta lint（target 是否存在、action 是否合法……）
        ↓
Semantic Validation：步骤序列是否与 UI Graph 的已知转移矛盾
        ↓
Duplicate Detection（F8）→ Dry Run（F7）→ Candidate Test
```

## 第一道：`validate_schema` —— **零新实现**

F5 的落点是「Candidate Test 就是 P1 TestCase，走同一套校验」。故本函数**只**
转调 `testcase/lint.py::lint`（`mta lint` 的底层），不新造校验器——第二份
「target 是否存在」的实现必然与第一份漂移，而漂移的方向恰好是「生成器放行了
`mta lint` 会拦的东西」，人工审核时看不出。

## 第二道：`validate_semantic` —— 判据是「与**已知观测**矛盾」

### ⚠️ 为什么**不**用 Source Graph（设计 6.1 字面的「UI Graph」）

设计写「步骤序列是否与 UI Graph 的已知转移矛盾」。实测 `build_source_graph`
的转移**恒为 0**（`graph/builder.py:281` 的模块 docstring：当前 metadata 格式
不含任何导航声明）——拿它当判据，本函数会**恒返回「无矛盾」**：形式合法、
语义等于没有（`generator/coverage.py` 对同一件事已登记过一次）。故判据取
**Runtime Graph**（`knowledge.graph_query` 的返回值，来自真实执行过的 trace）。

### 📌 口径（本次拍板，plan Steps 明文要求登记）

**未知 Screen / 新元素一律标记 `unknown` 放行，交给 Dry Run 验证。**

三条理由：

1. Source Graph 转移恒空（上节），拿它判「矛盾」没有鉴别力；
2. Runtime Graph 是**过去运行的采样**，不是真值——对**未被采样到**的路径判
   「矛盾」，误杀的恰恰是「我们要生成的新用例」（覆盖率补全的对象）；
3. 设计 6.1 的次序本就把 Dry Run 放在语义校验**之后**：真伪由**执行**定，
   语义校验只挡**结构性**错误。

### 判据的精确形状

一条候选的 `(from_screen, trigger, to_screen)` 三元组：

- 图对 `(from_screen, trigger)` **没有任何观测** → `unknown`（放行，理由 2）；
- 有观测，且其中**有一条**落在 `to_screen` → `consistent`（放行）；
- 有观测，且**没有一条**落在 `to_screen` → **`contradicted`（拒绝）**。

**多条转移时的优先级（review_p3_task34 P1-1 修订）**：一条候选通常声明多条
转移，结论按 **`contradicted`（任一）> `unknown`（任一，且无矛盾）> `consistent`
（全部有观测）** 取。**先找矛盾、再谈未知**——矛盾是「图说这条路走不到那儿」
的硬结论，不该被同一候选里另一条**没被采样过**的转移稀释。初版按
`sorted(declared)` 逐条短路（遇零观测即 `return unknown`），于是结论取决于
**字典序**：零观测的转移排在前面就掩蔽了后面的已知矛盾 → 漏放。**这是漏放
方向，下面那条「推论」当时只登记了误拒方向。**

⚠️ 推论（一并登记）：这是「与**已知观测**矛盾」，**不是**「与真值矛盾」。
`to_screen` 拼错、或 App 真的多了一条图里没有的路径，都会落在 `contradicted`
里——**误拒是可能的**。代价可接受，因为拒绝**落在留痕表**里
（`test_candidate_rejections`，带 `reason` + `detail`），人工看得见、能翻案；
而漏放（把矛盾当 unknown 放行）会把错误推给 Dry Run，那里的代价是一次设备
执行——两者都不是静默的，但**前者更便宜且不烧设备**。**误拒方向已登记，
漏放方向由 P1-1 修订关掉**：现在只有「**全部**转移都无矛盾」才会因 unknown
放行，任一已知矛盾都拦得住。

H18/F13：本模块**纯函数、离设备离网络**（没有 import `cli` / `executor` /
`session` / `sqlite3`）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from graph.models import RUNTIME, RuntimeGraph
from repository.resolver import LintIssue, Repository
from testcase.lint import Severity, lint, max_severity
from testcase.schema import TestCase

from generator.coverage import _arrivals

__all__ = [
    "SEMANTIC_CONSISTENT", "SEMANTIC_CONTRADICTED", "SEMANTIC_UNKNOWN",
    "SemanticVerdict", "schema_errors", "validate_schema",
    "validate_semantic",
]

SEMANTIC_CONSISTENT = "consistent"
SEMANTIC_CONTRADICTED = "contradicted"
SEMANTIC_UNKNOWN = "unknown"


@dataclass(frozen=True)
class SemanticVerdict:
    """语义校验的结论。**不是 bool**——`unknown` 与 `consistent` 都放行，
    但它们在审计里是两件事（「图里确认过」vs「图里没有这条，交给 Dry Run」），
    合成一个 bool 就再也分不开了。
    """

    status: str
    reason: str = ""
    # 判据用到的具体观测（`"LoginView->HomeView@tap:LoginView.login_button"`）——
    # 拒绝时列出来，人工能一眼看到「图里同 (from, trigger) 的实测落点是哪几个」。
    observed: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.status not in (SEMANTIC_CONSISTENT, SEMANTIC_CONTRADICTED,
                               SEMANTIC_UNKNOWN):
            raise ValueError(f"unknown semantic status: {self.status!r}")


def validate_schema(tc: TestCase, *, repository: Repository,
                    secrets) -> list[LintIssue]:
    """`mta lint` 的**同一实现**（F5：不新造校验器）。

    返回 `lint()` 的原始 `LintIssue` 列表——**不做**「ERROR/WARNING 分流」：
    分流判据是 `max_severity`，调用方（生成器）用它决定要不要入库。这里再
    包一层 bool 会让「哪几条 ERROR」在调用方拿不到（拒绝留痕要写进去）。
    """
    return lint([tc], repository, secrets)


def schema_errors(issues: Iterable[LintIssue]) -> list[LintIssue]:
    """从 lint 结果里挑出 ERROR 级（矩阵 #7 的拒绝判据）。

    ⚠️ 用 `max_severity` 的**同一个枚举**（`Severity.ERROR`）判，不写字符串
    字面量 `"ERROR"`——`LintIssue.severity` 的类型由 `testcase/lint.py` 拥有，
    这里硬编码字符串会在它改成枚举/换词表时静默失配（`==` 恒 False → **一条
    都不拒** → 矩阵 #7 退化成永假）。
    """
    return [i for i in issues if i.severity is Severity.ERROR]


def _candidate_transitions(tc: TestCase) -> set[tuple[str, str, str]]:
    """候选**声明**的转移集合 `{(from, to, trigger)}`。

    推导**复用 `generator/coverage.py::_arrivals`**（plan §2 第 1 条：同一套
    逻辑只许有一处实现）——「用例文本 → 屏到达序列」这个映射若在覆盖计算与
    语义校验里各写一份，两者对同一条用例的判断会分叉，而分叉的表现是
    「覆盖说没覆盖、语义说矛盾」，人查不出哪份对。
    """
    arrivals = _arrivals(tc)
    out: set[tuple[str, str, str]] = set()
    for (a, _ta), (b, tb) in zip(arrivals, arrivals[1:]):
        if a == b:
            continue        # 同屏连续观测 = 一次访问，不产生自环（与 runtime 图同款）
        out.add((a, b, tb))
    return out


def validate_semantic(tc: TestCase, *, graph: RuntimeGraph) -> SemanticVerdict:
    """**纯函数**：候选声明的转移 vs Runtime Graph 的**已知观测**。

    | 结论 | 条件 | 处置 |
    |---|---|---|
    | `consistent` | 每条声明的转移都在图里有观测 | 放行 |
    | `unknown` | 图对 `(from, trigger)` 零观测（新路径/新元素/新屏） | **放行**，交给 Dry Run |
    | `contradicted` | 图对 `(from, trigger)` 有观测，但**没有一条**落在声明的 `to` | 拒绝 + 留痕 |

    ⚠️ **拒绝要求「图对该 (from, trigger) 有观测」**：没有观测就无从谈矛盾
    （那正是 `unknown`）。把「没观测」也判矛盾，等于用一张不完整的图否掉所有
    新路径——`coverage_gap` 存在的意义就是找这些路径，那样两半会互相抵消。

    ⚠️ 传 `source_of='source'` 的图 → `ValueError`（与 `coverage_gap` 同款纪律）：
    源图转移恒空，静默接受它会让本函数**恒判 consistent**，「没有矛盾」与
    「判据没生效」长得一模一样。
    """
    if graph.source_of != RUNTIME:
        raise ValueError(
            f"validate_semantic 只接受 runtime 图（判据是「与**已知观测**矛盾」，"
            f"Source Graph 的转移当前恒为空），got source_of={graph.source_of!r}")

    declared = _candidate_transitions(tc)
    if not declared:
        # ⚠️ **一个到达点推不出转移**：`_arrivals` 给出的是 `[(屏, 触发)]`，
        # 相邻两点才构成 `(from, to, trigger)`。候选若只声明了一个到达点
        # （或一个都没有），它的**起点屏是未知的**——那时判「consistent」是
        # 空话（「声明的转移全部有观测支持」在空集上恒真），会让审计以为
        # 「语义校验过了」。如实报 `unknown`：这一条我们**没法判**。
        return SemanticVerdict(
            status=SEMANTIC_UNKNOWN,
            reason=(f"候选只声明了 {len(_arrivals(tc))} 个屏到达点——"
                    f"推不出 (from, trigger) 转移（起点屏未声明），"
                    f"语义校验无从下手，放行交给 Dry Run 验证"))

    # 图按 (from, trigger) 索引 → 实测落点集合
    observed: dict[tuple[str, str], set[str]] = {}
    for t in graph.transitions:
        observed.setdefault((t.from_screen, t.trigger), set()).add(t.to_screen)

    # ⚠️ **必须扫全量，不许在第一条转移上短路**（review_p3_task34 P1-1 实锤）。
    # 逐条「遇到零观测就 `return unknown`」的写法让「有没有矛盾」取决于
    # `sorted(declared)` 的**字典序**：一条候选只要**任意**一条零观测转移排在
    # 已知矛盾转移之前，矛盾就被掩蔽 → 落进 `unknown`（放行）→ 推给 Dry Run
    # 烧设备。真语料 20 例里 13 例有声明转移，其 sorted-first **全是
    # `HomeView` 打头**，即真实形态天然命中这条短路。
    #
    # 正确的次序是**先找矛盾，再谈未知**：矛盾是「图说这条路走不到那儿」的
    # 硬结论，不该被同一候选里另一条**没被采样过**的转移稀释。三条优先级：
    #   contradicted（任一） > unknown（任一，且无矛盾） > consistent（全部有观测）
    unknown: list[tuple[str, str, str]] = []
    contradictions: list[tuple[tuple[str, str, str], tuple[str, ...]]] = []
    for (from_screen, to_screen, trigger) in sorted(declared):
        landings = observed.get((from_screen, trigger))
        if not landings:
            unknown.append((from_screen, to_screen, trigger))
        elif to_screen not in landings:
            contradictions.append((
                (from_screen, to_screen, trigger),
                tuple(sorted(f"{from_screen}->{s}@{trigger}" for s in landings))))

    if contradictions:
        # 全部矛盾都列出来（不是只报第一条）——人工一次看全，不必来回翻案。
        listed = tuple(o for _, obs in contradictions for o in obs)
        detail = "；".join(
            f"候选声明 {f}->{t}@{g}，但图里同一 (from, trigger) 的实测落点是 "
            f"{list(obs)}" for (f, t, g), obs in contradictions)
        return SemanticVerdict(
            status=SEMANTIC_CONTRADICTED,
            reason=f"{detail}——结构性矛盾",
            observed=listed)

    if unknown:
        f, _t, g = unknown[0]
        more = f"（另有 {len(unknown) - 1} 条转移同样零观测）" if len(unknown) > 1 \
            else ""
        return SemanticVerdict(
            status=SEMANTIC_UNKNOWN,
            reason=(f"图对 ({f}, {g!r}) 零观测{more}"
                    f"——新路径/新元素，放行交给 Dry Run 验证"),
            observed=())

    return SemanticVerdict(status=SEMANTIC_CONSISTENT,
                           reason="候选声明的转移全部有已知观测支持")


# `max_severity` 在这里 re-export，让「schema 校验的入口」只有本模块一处——
# 未来调用方（Task 3.5 的 CLI）若要「只看最严重级别」不必再 import
# `testcase.lint`。⚠️ 当前生成器**用的是 `schema_errors`**（它要拿到具体的
# ERROR 列表写进拒绝留痕，不是只要一个级别），故本 re-export 目前零消费。
__all__ += ["max_severity"]
