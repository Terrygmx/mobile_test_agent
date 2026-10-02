"""policy.py — Recovery 决策表（9.2 前半 + 7.4 重试规则表的纯函数，H18）。

设计 9.2 流水线前半的判定全部收口在这里（脱离设备单测）：

  Failure
   ├─ 是 Wait Timeout？     → 默认不进入 Recovery（recovery.on_wait_timeout=false）
   ├─ 是 Assertion 值失败？  → 不进入 Recovery（H6：值断言失败不是定位问题，
   │                            重试/恢复只会掩盖真实的期望值错误）
   ├─ 基础设施问题？         → 按 7.5 处理（Lifecycle.handle_wda_failure），
   │                            不进入本决策表
   ├─ 按 7.4 判定 phase × 幂等性 → 得到「允许的动作集合」
   │     POST_DISPATCH + 非幂等 → 只允许 postcondition 检查，之后结束（H7）
   └─ Settle 重试：有界，默认 1 次（覆盖渲染延迟，不是无限重试）

决策表只回答「允许做什么」，不回答「做不做得到」——执行归 RecoveryEngine。
SECURITY_BLOCKED 不进恢复：Guard 判定是确定性的，同样的输入必然再拦一次
（review P15 P3-5 同款结论）。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from executor.policy import FailurePhase, Idempotency

__all__ = ["RecoveryAction", "RecoveryConfig", "admitted_actions"]


class RecoveryAction(str, Enum):
    """9.2 流水线的动作词汇表。顺序即流水线执行顺序。"""

    POSTCONDITION_CHECK = "postcondition_check"   # H7：非幂等 POST_DISPATCH 唯一出口
    RUN_MEMO = "run_memo"                         # 9.4：同 run 内已校验恢复复用
    SETTLE_RETRY = "settle_retry"                 # 9.2 第 5 步：有界 re-find + 重发
    LOCAL_RECONCILE = "local_reconcile"           # 9.2 第 7 步：当前屏 Source 对比
    LLM_CANDIDATE = "llm_candidate"               # 9.2 第 8 步（Task 4.2）


@dataclass
class RecoveryConfig:
    """恢复配置。默认值即设计值（9.2 / 7.5），mta.yaml 可覆盖（15 节）。

    `on_wait_timeout` 是 **P1 预留旋钮**（review_m4_task41 P3-2 定档）：
    决策表按它放行，但 wait 步骤走 aux 分支不进恢复管线——Task 4.2 接通
    前，端到端置 True 也没有代码路径能兑现（不宣称可用，防「参数存在=
    功能存在」，R17-1/2 同纪律）。
    """

    on_wait_timeout: bool = False       # 9.2：Wait Timeout 默认不进 Recovery
    settle_max_attempts: int = 1        # 9.2：有界，默认 1 次
    settle_wait_s: float = 0.5          # 「短暂等待」的默认时长


# phase × 幂等性 → 允许的动作集合（7.4 重试规则表的恢复侧投影）。
# 元组顺序即引擎执行顺序（9.2 流水线从上到下：settle → 当前屏对比 →
# 同 run 复用 → LLM；POSTCONDITION_CHECK 最便宜且最确定，永远最先）。
_PRE_DISPATCH_ALLOWED: tuple[RecoveryAction, ...] = (
    RecoveryAction.SETTLE_RETRY,
    RecoveryAction.LOCAL_RECONCILE,
    RecoveryAction.RUN_MEMO,
    RecoveryAction.LLM_CANDIDATE,
)
_POST_DISPATCH_IDEMPOTENT_ALLOWED: tuple[RecoveryAction, ...] = (
    RecoveryAction.POSTCONDITION_CHECK,
    RecoveryAction.SETTLE_RETRY,       # 幂等动作允许有界重试（7.4 POST_DISPATCH×IDEMPOTENT）
    RecoveryAction.LOCAL_RECONCILE,
    RecoveryAction.RUN_MEMO,
    RecoveryAction.LLM_CANDIDATE,
)
_POST_DISPATCH_NON_IDEMPOTENT_ALLOWED: tuple[RecoveryAction, ...] = (
    RecoveryAction.POSTCONDITION_CHECK,   # H7：只允许查 postcondition，绝不重发
)


def admitted_actions(
    failure_type: str | None,
    phase: FailurePhase | None,
    idempotency: Idempotency | None,
    has_postcondition: bool,
    config: RecoveryConfig | None = None,
) -> tuple[RecoveryAction, ...] | None:
    """9.2 前半决策表。返回 None = 不进入 Recovery（维持原失败）。

    `idempotency=None` 按 UNKNOWN 处理（7.4-3：UNKNOWN 一律当非幂等）——
    与 `_is_non_idempotent`（lifecycle）同一口径，两处不得分叉。
    """
    cfg = config or RecoveryConfig()

    # H6：断言值失败永不恢复——期望值错了重试也是错，恢复只会掩盖。
    if failure_type == "ASSERTION_VALUE_MISMATCH":
        return None
    # 9.2：Wait Timeout 默认不进 Recovery；显式开启才走恢复流水线。
    if failure_type == "WAIT_TIMEOUT" and not cfg.on_wait_timeout:
        return None
    # Guard 拦截是确定性判定（10.1 不可绕过），恢复必然再拦。
    if failure_type == "SECURITY_BLOCKED":
        return None

    non_idempotent = idempotency in (
        Idempotency.NON_IDEMPOTENT, Idempotency.UNKNOWN) \
        or (idempotency is None and phase is FailurePhase.POST_DISPATCH)

    if phase is FailurePhase.POST_DISPATCH:
        if non_idempotent:
            # H7：非幂等动作可能已生效，重发会造成重复支付/重复下单——
            # 后果不可回滚，宁可失败也不猜。postcondition 是唯一可判定途径。
            if has_postcondition:
                return _POST_DISPATCH_NON_IDEMPOTENT_ALLOWED
            return None    # → FAIL(ACTION_OUTCOME_UNKNOWN)（StepRunner 已如此）
        return _POST_DISPATCH_IDEMPOTENT_ALLOWED
    if phase is FailurePhase.PRE_DISPATCH:
        return _PRE_DISPATCH_ALLOWED
    # 无 phase（app 级动作失败等）不在 9.2 流水线范围内。
    return None
