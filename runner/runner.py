"""runner.runner — 7.1 Step 执行管线 + 7.5 WDA 故障语义。

管线顺序固定（7.1）：

```python
def run_step(step) -> StepResult:
    ctx = resolve(step)              # EffectiveElement + risk + idempotency
    guard.check(ctx)                 # 违反 → SECURITY_BLOCKED
    device.ensure_alive()            # WDA 健康检查
    el = locator.find(ctx.element)   # ← PRE_DISPATCH
    actions.perform(step, el)        # ← POST_DISPATCH
```

**find 与 perform 必须是两次独立调用**——阶段判定（PRE/POST_DISPATCH）
完全建立在这个前提上：动作是否发出，只有这两步的分界能回答。合并成一次
「find_and_tap」就无法区分「没找到」与「点了但超时」，而这两者的重试
语义完全相反（H7）。

7.5：`non_idempotent_dispatched` 一旦置位就不可回滚（动作可能已经在服务端
生效），后续 WDA 故障时不得重跑该 testcase。
"""

from __future__ import annotations

import time
import warnings
from dataclasses import dataclass, field

from executor.executor import AmbiguousElement
from executor.guard import Guard, GuardViolation
from executor.policy import (
    FailurePhase,
    Idempotency,
    RetryDecision,
    effective_idempotency,
    effective_risk,
    retry_decision,
)
from session.device_session import InfraError
from testcase.schema import Risk

__test__ = False


@dataclass
class RunStepContext:
    """resolve 之后的执行上下文（7.1 第 1 步的产物）。

    risk / idempotency 由 policy 从确定性信息推导后**填进来**——本类不含
    任何让 testcase 绕过 Guard 的字段（10.1）。
    """

    element_id: str
    screen_id: str
    strategies: tuple          # tuple[(strategy, value), ...]，按尝试顺序
    action: str
    value: str | None = None
    risk: Risk | None = None
    idempotency: Idempotency | None = None   # None = 未声明 → 按 7.4 推导
    has_postcondition: bool = False
    step_index: int = 0


@dataclass
class StepOutcome:
    """单步结果。`phase` 只在失败时有值——成功没有「失败阶段」。"""

    ok: bool
    step_index: int = 0
    element_id: str | None = None
    action: str | None = None
    phase: FailurePhase | None = None
    failure_type: str | None = None
    error: str | None = None
    retry_decision: RetryDecision | None = None
    non_idempotent_dispatched: bool = False
    latency_ms: int | None = None
    effective_risk: Risk | None = None
    effective_idempotency: Idempotency | None = None
    locator_strategy: str | None = None
    step_schema_version: str | None = None
    detail: dict = field(default_factory=dict)

    @property
    def dispatch_reached(self) -> bool:
        """是否到达过 POST_DISPATCH（动作已发出）。"""
        return self.phase is FailurePhase.POST_DISPATCH or self.ok


class StepRunner:
    """执行单个 step。不持有用例级状态——`non_idempotent_dispatched`
    由调用方（testcase 级）汇总，因为它跨 step 累积（7.5）。"""

    def __init__(self, executor, device_session, guard: Guard,
                 recorder=None):
        self.ex = executor
        self.ds = device_session
        self.guard = guard
        self.rec = recorder

    # --- 7.1 第 1 步：resolve（已在 RunStepContext 构造时完成，这里只做
    #     兜底推导，便于调用方只给最少信息） ---

    def _resolve_defaults(self, ctx: RunStepContext) -> RunStepContext:
        """未显式给 risk/idempotency 时按 7.4 推导。

        **显式给了就不覆盖**（H17）——调用方（repository resolver）已经从
        metadata 拿到确定性声明，这里只补「完全没声明」的空档。

        默认值刻意是 `None` 而不是 `LOW`/`IDEMPOTENT`：给默认值等于宣称
        「调用方已经声明过了」，启发式就永远不触发——`pay_button` 这类
        元素会被静默当成幂等，7.4 的关键词表形同虚设（这是本函数写出来
        后第一个测试就抓到的 bug）。
        """
        if ctx.risk is None:
            ctx.risk = effective_risk(
                element_id=ctx.element_id,
                label=ctx.screen_id,
                accessibility_id=ctx.element_id)
        if ctx.idempotency is None:
            ctx.idempotency = effective_idempotency(
                None, None, element_id=ctx.element_id)
        return ctx

    # --- 7.1 主流程 ---

    def run_step(self, ctx: RunStepContext) -> StepOutcome:
        ctx = self._resolve_defaults(ctx)
        t0 = time.time()

        def _done(**kw) -> StepOutcome:
            kw.setdefault("step_index", ctx.step_index)
            kw.setdefault("element_id", ctx.element_id)
            kw.setdefault("action", ctx.action)
            kw.setdefault("effective_risk", ctx.risk)
            kw.setdefault("effective_idempotency", ctx.idempotency)
            kw["latency_ms"] = int((time.time() - t0) * 1000)
            out = StepOutcome(**kw)
            self._record(out, ctx)
            return out

        # 1. guard —— 先于设备：被拦的动作连设备都不该碰
        from executor.guard import GuardContext
        try:
            self.guard.check(GuardContext(
                risk=ctx.risk, screen_id=ctx.screen_id,
                element_id=ctx.element_id, action=ctx.action))
        except GuardViolation as e:
            # SECURITY_BLOCKED 是 SECURITY_BLOCKED 语义的终态，不再往下走
            return _done(ok=False, failure_type="SECURITY_BLOCKED",
                         error=e.reason,
                         phase=None,
                         retry_decision=retry_decision(
                             FailurePhase.PRE_DISPATCH, ctx.idempotency,
                             ctx.has_postcondition))

        # 2. ensure_alive —— WDA 健康检查，失败按 7.5 抛 InfraError
        #    （基础设施故障，不是测试失败；调用方据此重跑/终止）
        self.ds.ensure_alive()

        # 3. find —— PRE_DISPATCH：任何失败都说明动作从未发出
        try:
            elements = self.ex.find(ctx.strategies)
        except InfraError:
            raise  # 设备层故障向上冒，不归测试失败
        except Exception as e:
            return _done(ok=False, phase=FailurePhase.PRE_DISPATCH,
                         failure_type=_classify_find_error(e),
                         error=f"{type(e).__name__}: {e}",
                         retry_decision=retry_decision(
                             FailurePhase.PRE_DISPATCH, ctx.idempotency,
                             ctx.has_postcondition))

        if not elements:
            return _done(ok=False, phase=FailurePhase.PRE_DISPATCH,
                         failure_type="ELEMENT_NOT_FOUND",
                         error=f"no element matched {ctx.element_id}",
                         retry_decision=retry_decision(
                             FailurePhase.PRE_DISPATCH, ctx.idempotency,
                             ctx.has_postcondition))
        if len(elements) > 1:
            # H3：≥2 个立即 AMBIGUOUS，**禁止取第一个**
            return _done(ok=False, phase=FailurePhase.PRE_DISPATCH,
                         failure_type="AMBIGUOUS_ELEMENT",
                         error=f"{len(elements)} elements matched "
                               f"{ctx.element_id}; 取第一个是禁止的（H3）",
                         retry_decision=retry_decision(
                             FailurePhase.PRE_DISPATCH, ctx.idempotency,
                             ctx.has_postcondition))

        element = elements[0]

        # 4. perform —— POST_DISPATCH：已发出，失败时结果未知
        non_idem_dispatched = ctx.idempotency in (
            Idempotency.NON_IDEMPOTENT, Idempotency.UNKNOWN)
        try:
            self.ex.perform(ctx.action, element, ctx.value)
        except InfraError:
            raise  # 设备层故障：动作可能已发出 → 标记置位后上抛
        except Exception as e:
            return _done(ok=False, phase=FailurePhase.POST_DISPATCH,
                         failure_type="ACTION_OUTCOME_UNKNOWN",
                         error=f"{type(e).__name__}: {e}",
                         non_idempotent_dispatched=non_idem_dispatched,
                         retry_decision=retry_decision(
                             FailurePhase.POST_DISPATCH, ctx.idempotency,
                             ctx.has_postcondition))

        return _done(ok=True, non_idempotent_dispatched=non_idem_dispatched,
                     locator_strategy=(ctx.strategies[0][0]
                                       if ctx.strategies else None))

    # --- trace ---

    def _record(self, out: StepOutcome, ctx: RunStepContext) -> None:
        """写 trace。Redactor 前置由 recorder/storage 自己保证（H8）——
        这里不预脱敏也不后脱敏，避免出现第二条「写前/写后」路径。

        **写盘异常不改变步骤结论**（但也不静默）：trace 是观测手段，磁盘满
        / 库锁了不该把一个 SUCCESS 的步骤变成崩溃，或把 FAIL 记成别的结论。
        这里捕获后 `warnings.warn`——不吞（异常仍会出现在日志里），但不让
        它向上冒泡打断执行。真正「trace 丢了必须有人管」的需求由 Report
        侧对 warning 计数暴露（Task 2.6），不是靠抛异常阻断运行。
        """
        if self.rec is None:
            return
        try:
            self.rec.record_step(
                step_index=out.step_index,
                action_type=out.action,
                status="SUCCESS" if out.ok else "FAILED",
                error=out.error,
                latency_ms=out.latency_ms,
                locator=({"strategy": out.locator_strategy,
                          "value": out.element_id}
                         if out.locator_strategy else None),
                detail=out.detail or None,
                effective_risk=(out.effective_risk.name
                                if out.effective_risk else None),
                effective_idempotency=(out.effective_idempotency.name
                                       if out.effective_idempotency else None),
                failure_phase=(out.phase.value if out.phase else None),
                failure_type=out.failure_type)
        except TypeError:
            # 旧 P0 Recorder 不接新字段——M2 Gate 后退役。当前不静默：
            # 回退到最小字段集，保证 P0 链路不被新参数打断。
            try:
                self.rec.record_step(
                    step_index=out.step_index, action_type=out.action,
                    status="SUCCESS" if out.ok else "FAILED",
                    error=out.error, latency_ms=out.latency_ms)
            except Exception as e:  # noqa: BLE001 — 见 docstring
                warnings.warn(f"trace write failed (P0 fallback): {e!r}",
                              RuntimeWarning, stacklevel=2)
        except Exception as e:  # noqa: BLE001 — 见 docstring
            warnings.warn(f"trace write failed: {e!r}", RuntimeWarning,
                          stacklevel=2)


def _classify_find_error(e: Exception) -> str:
    """find 阶段异常 → failure_type。fail-loud：认不出来的抛出去而非归
    ELEMENT_NOT_FOUND（那会把编程错误伪装成测试失败）。"""
    if isinstance(e, AmbiguousElement):
        return "AMBIGUOUS_ELEMENT"
    if isinstance(e, ValueError):
        return "ELEMENT_NOT_FOUND"
    return f"FIND_ERROR:{type(e).__name__}"
