"""context.py — Recovery Engine 的上下文与结果模型（9.1 / 9.2 / 20 节）。

`RecoveryContext` 按设计 9.1 携带：失败信息（type、phase）、step、
effective_element、effective_risk / idempotency、CurrentScreen、Source 子集、
最近动作、budget 状态、testcase 状态。**引擎不持有 Executor**——设备交互
（re-find / 重新发出动作 / page_source / postcondition 检查）由调用方以
callable 注入（`refind` / `redispatch` / `page_source` / `postcondition_check`），
与「禁止在 Executor 内直接 call_llm()」（9.1）同一隔离纪律：设备访问和
LLM 调用都只存在于注入边界。

`ExperienceStore` / `EmptyExperienceStore` 是设计 20 节的 V2 预留接口——
引擎在「Local Reconciliation 之后、LLM 之前」调用 `lookup()`，P1 恒返回 []。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable

from executor.policy import FailurePhase, Idempotency

__all__ = [
    "RecoveryContext",
    "RecoveryResult",
    "ExperienceStore",
    "EmptyExperienceStore",
]


@runtime_checkable
class ExperienceStore(Protocol):
    """20 节 V2 预留：经验库按 (app_build, screen, target_id) 查恢复策略。"""

    def lookup(self, app_build: str, screen: str,
               target_id: str) -> list[dict]: ...


class EmptyExperienceStore:
    """P1 实现：永远返回 []（20 节）。位置固定在 reconciliation 后、LLM 前。"""

    def lookup(self, app_build: str, screen: str,
               target_id: str) -> list[dict]:
        return []


@dataclass
class RecoveryContext:
    """9.1 上下文。callables 是引擎与设备的唯一通道（H18：判定逻辑离设备）。"""

    # --- 失败信息 ---
    failure_type: str
    phase: FailurePhase | None = None
    # --- 目标元素与动作 ---
    element_id: str | None = None
    screen_id: str | None = None
    strategies: tuple = ()
    action: str | None = None
    value: str | None = None
    effective_risk: object | None = None      # testcase.schema.Risk
    effective_idempotency: Idempotency | None = None
    has_postcondition: bool = False
    # --- Source 子集（9.1「Source 子集」）：当前屏的 metadata 切片，
    #     reconcile_local 的输入。None = 调用方没给，reconciliation 跳过。 ---
    source_metadata: dict | None = None
    # --- 设备交互端（调用方注入，引擎不摸 Executor） ---
    refind: Callable[[], object] | None = None
    redispatch: Callable[[object], None] | None = None
    page_source: Callable[[], str] | None = None
    postcondition_check: Callable[[], bool | None] | None = None
    # --- run / testcase 状态 ---
    testcase_id: str | None = None
    attempt: int = 1
    app_build: str = "local"


@dataclass
class RecoveryResult:
    """recover() 的结论。

    recovered=False 时 failure_type 是**维持原判**的症状（8.2），detail 里
    带「走到哪一步、为什么停」——恢复流水线的可观测性与恢复本身同等重要：
    静默的「试过但没成」会让漂移看起来像 flaky。
    """

    recovered: bool
    kind: str | None = None        # settle_retry / local_reconcile / postcondition / run_memo / llm / experience
    failure_type: str | None = None
    detail: dict = field(default_factory=dict)
