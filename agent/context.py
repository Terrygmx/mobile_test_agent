"""context.py — Recovery Engine 的上下文与结果模型（9.1 / 9.2 / 20 节）。

`RecoveryContext` 按设计 9.1 携带：失败信息（type、phase）、step、
effective_element、effective_risk / idempotency、CurrentScreen、Source 子集、
最近动作、budget 状态、testcase 状态。**引擎不持有 Executor**——设备交互
（re-find / 重新发出动作 / page_source / postcondition 检查）由调用方以
callable 注入（`refind` / `redispatch` / `page_source` / `postcondition_check`），
与「禁止在 Executor 内直接 call_llm()」（9.1）同一隔离纪律：设备访问和
LLM 调用都只存在于注入边界。

`ExperienceStore` 的**唯一定义**在 `experience.store`（P2 设计 7 节；
本模块旧稿「设计 20 节」编号引用已更正），此处 re-export 防两套接口。
P1 的 `EmptyExperienceStore` 占位已在 Task 2.4 退役（旧签名把 `app_build`
当 `app_id` 用——两个键语义不同）；引擎在「Local Reconciliation 之后、
LLM 之前」消费真 Store。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from executor.policy import FailurePhase, Idempotency
# 全仓唯一定义（review 纪律：防两套接口）——本模块只 re-export
from experience.store import ExperienceStore

__all__ = [
    "RecoveryContext",
    "RecoveryResult",
    "ExperienceStore",
]


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
    # 期望元素类型（9.3-2 类型校验基准；aux 步骤（wait/assert）由管线从
    # Repository 补——候选类型对不上 → LLM_TARGET_TYPE_MISMATCH
    expected_type: str | None = None
    # --- Source 子集（9.1「Source 子集」）：当前屏的 metadata 切片，
    #     reconcile_local 的输入。None = 调用方没给，reconciliation 跳过。 ---
    source_metadata: dict | None = None
    # --- 设备交互端（调用方注入，引擎不摸 Executor） ---
    refind: Callable[[], object] | None = None
    # 按给定策略定位（RUN_MEMO 恢复策略的消费端，9.4）：memo 存的是「用哪条
    # 定位能找到漂移后的目标」，必须按它重找——refind 闭包捕获的是原始
    # strategies，漂移场景下按原策略重找必然再次失败（review P3-1）。
    find_with: Callable[[tuple], object] | None = None
    # 数量观测端（Experience Runtime Guard，设计 5 节）：返回**全部**匹配
    # （0/1/≥2 都要看得见）——异常语义（ElementNotFound/AmbiguousElement）
    # 反推不出真数量，Guard 判不了 NOT_FOUND / EXECUTE / AMBIGUOUS。
    # 生产注入 `Executor.find_all`（Task 2.4 接线地雷 ③ 的定案）。
    find_all: Callable[[list[dict]], list] | None = None
    redispatch: Callable[[object], None] | None = None
    page_source: Callable[[], str] | None = None
    postcondition_check: Callable[[], bool | None] | None = None
    # --- run / testcase 状态 ---
    testcase_id: str | None = None
    attempt: int = 1
    app_build: str = "local"
    # --- Experience Store 消费（P2 设计 4.1 / 7.1） ---
    # `app_id` 是 Store 主键第一段（哪个 App，bundle id）。**与 app_build
    # 严格区分**：`app_build` 是「哪一次构建」（E7 validated_builds /
    # RUN_MEMO 的键）。P1 调用点曾把 build 当 app_id 传进 lookup——两个键
    # 互相冒充是 Task 2.4 要清的过渡债，不得再合并成一个字段。
    app_id: str = ""
    # 追溯链（experience_runs.run_id）；None = 调用方没给 → 样本不落库。
    run_id: str | None = None
    # YAML 步序。**不是** steps.id——恢复发生在 record_step 之前，那时
    # `steps.id` 还不存在；样本落库的 step_id 由 trace 写入方在 record_step
    # 之后补（见 experience.store.record_sample_runs 的决策说明）。
    step_index: int | None = None


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
    # LLM 候选的定位策略（aux 步骤恢复消费：管线把它挂进 recovered_locators
    # 覆盖后重跑 wait/assert——动作步由引擎直接 redispatch，不走这里）
    strategy: dict | None = None

    # `detail` 的约定键（P2 设计 10 节 / Task 2.4）：
    #   recovered_kind        RECOVERED_LLM / RECOVERED_EXPERIENCE / ...——
    #                         明细分类（聚合口径与退出码不变，RECOVERED ≠ PASS）；
    #                         断言目标漂移那一类由管线在 assert 上下文里改写
    #                         （引擎看不到 aux 语义，见 runner.result.recovered_kind）。
    #   experience_runs       待落库的 4.7 样本 payload（引擎决定写什么，
    #                         steps.id 由管线在 record_step 之后补）。
