"""models.py — UI State Graph 的领域模型（设计 12；Task 5.1 / P2-11）。

三条纪律（都来自设计原文，不是实现偏好）：

1. **E12：图只来自已有 Testcase + 已有 Trace + Source Metadata，不做自动探索。**
   模型上体现为每个节点带 `evidence`（「凭什么认为到达过这个屏」）——没有证据
   就没有节点，不存在「推测出来的屏」。
2. **`source_of` 是图的第一分类维度**：`runtime`（实际到达）与 `source`
   （声明）同结构不同来源，diff 时直接对比（设计 12.1 的三段式）。
3. **`app_id` / `app_build` 是范围**：diff 按 build 关联（设计 12.3），没有范围
   的图无法回答「1024 与 1025 之间变了什么」。

用 frozen dataclass 而不是 pydantic：图节点是**纯值**（聚合产物，无跨字段一致
性约束），与 `experience/models.py` 的 pydantic 模型定位不同——那里承载 E5/E7
的写入闸门，这里只承载「已算好的结果」。唯一的校验是计数非负（算错了要炸，
不要静默产出负 visit_count）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "RUNTIME", "SOURCE",
    "EVIDENCE_WAIT_SCREEN", "EVIDENCE_RECOVERY_OBSERVED",
    "EVIDENCE_SOURCE_DECLARED",
    "ScreenNode", "ScreenTransition", "RuntimeGraph",
]

# `source_of` 的两个取值（设计 12.1：Runtime Graph ← Trace，Source Graph ← Metadata）
RUNTIME = "runtime"
SOURCE = "source"

# 观测证据类别（节点 `evidence` 列的取值；逗号分隔的集合）
EVIDENCE_WAIT_SCREEN = "wait_screen"
"""`wait_for screen:X` 步骤**成功** → X 被 wait 校验过（最强的一类观测）。"""
EVIDENCE_RECOVERY_OBSERVED = "recovery_observed"
"""恢复期屏识别的 `stage: screen / outcome: FOUND` → 引擎当场识别出的屏。"""
EVIDENCE_SOURCE_DECLARED = "source_declared"
"""Source Metadata 声明（Task 5.2 用）——**声明**不是观测，故证据名区分开。"""


@dataclass(frozen=True)
class ScreenNode:
    """一个被观测到（或声明）的屏。"""

    screen_id: str
    source_of: str = RUNTIME
    visit_count: int = 0
    evidence: tuple[str, ...] = ()
    first_seen: str | None = None
    last_seen: str | None = None

    def __post_init__(self) -> None:
        if self.visit_count < 0:
            raise ValueError(f"visit_count 不能为负: {self.visit_count}")
        if not self.screen_id:
            raise ValueError("screen_id 不能为空（空屏名 = 无名节点）")

    @property
    def evidence_csv(self) -> str:
        """落库形态：排序后的逗号分隔（集合语义，写入顺序不影响等值判断）。"""
        return ",".join(sorted(set(self.evidence)))


@dataclass(frozen=True)
class ScreenTransition:
    """一条 `from → to` 的转移，带触发条件与计数。"""

    from_screen: str
    to_screen: str
    trigger: str = ""
    source_of: str = RUNTIME
    count: int = 0
    first_seen: str | None = None
    last_seen: str | None = None

    def __post_init__(self) -> None:
        if self.count < 0:
            raise ValueError(f"count 不能为负: {self.count}")
        if not self.from_screen or not self.to_screen:
            raise ValueError("转移两端都不能为空")


@dataclass(frozen=True)
class RuntimeGraph:
    """一次构建的产物：节点 + 转移（都限定在同一个 `(app_id, app_build)` 范围）。"""

    app_id: str = ""
    app_build: str = ""
    nodes: tuple[ScreenNode, ...] = ()
    transitions: tuple[ScreenTransition, ...] = ()

    def node(self, screen_id: str) -> ScreenNode | None:
        for n in self.nodes:
            if n.screen_id == screen_id:
                return n
        return None

    @property
    def screens(self) -> tuple[str, ...]:
        return tuple(n.screen_id for n in self.nodes)

    @property
    def is_empty(self) -> bool:
        return not self.nodes and not self.transitions

    def summary(self) -> dict:
        """报告/CLI 用的摘要（不重算任何判据，只汇总已算好的值）。"""
        return {
            "app_id": self.app_id,
            "app_build": self.app_build,
            "nodes": len(self.nodes),
            "transitions": len(self.transitions),
            "visits": sum(n.visit_count for n in self.nodes),
            "screens": list(self.screens),
        }


@dataclass(frozen=True)
class TraceStep:
    """builder 的输入行（已从 trace 读出并补上归属信息）。

    `observed_at` 来自**所属 testcase_run** 的时间：`steps` 表本身没有时间戳
    （P1 schema 如此），所以时间精度到 testcase_run 级——这是可得的最细粒度，
    不假装更细。
    """

    testcase_run_id: int
    step_index: int
    step_type: str
    target_id: str = ""
    status: str | None = None
    detail: dict = field(default_factory=dict)
    observed_at: str | None = None
