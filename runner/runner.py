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

import inspect
import time
import warnings
from dataclasses import dataclass, field

from executor.executor import AmbiguousElement, ElementNotFound
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
    # Task 2.7：postcondition 具体 spec（不只布尔）。None = 未声明。
    postcondition_spec: object | None = None
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
                 recorder=None, run_id: str | None = None,
                 tc_run_id: int | None = None,
                 postcondition_checker=None):
        self.ex = executor
        self.ds = device_session
        self.guard = guard
        self.rec = recorder
        self.run_id = run_id
        self.tc_run_id = tc_run_id
        # Task 2.7：postcondition 执行端（H7 的最后一步）。签名单参
        # (spec) -> bool（满足=True）；None = 不执行（旧调用方不受影响）。
        self.postcondition_checker = postcondition_checker
        # R15-1：recorder 首参必填时必须在**构造期**就要求对应 id——放到运行期
        # 才发现的后果是每步 trace 写失败，报告上只表现为「步骤失败但 trace
        # 空」，看不出是配置错。判据用签名探测，不用 try/except。
        self._first_arg, self._first_arg_name = _required_first_arg(recorder)
        if self._first_arg is not None and getattr(self, self._first_arg) is None:
            raise ValueError(
                f"StepRunner(recorder=...) requires {self._first_arg_name}: "
                f"this recorder's record_step takes it as a required positional "
                f"arg. Pass {self._first_arg_name}=... explicitly.")

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
            # SECURITY_BLOCKED 的重试决策与普通 PRE_DISPATCH 失败**不同**：
            # guard 判定是确定性的（同样的输入必然再拦一次），「允许重试」
            # 在这里语义不通——review P15 P3-5。给一个专门的决策对象：
            # allowed=False + 明确 reason，别让调用方误以为可以重试。
            return _done(ok=False, failure_type="SECURITY_BLOCKED",
                         error=e.reason,
                         phase=None,
                         retry_decision=RetryDecision(
                             allowed=False, bounded=False,
                             check_postcondition=False,
                             reason="SECURITY_BLOCKED：Guard 判定确定性，"
                                    "重试必然再拦（10.1 不可绕过）"))

        # 2. ensure_alive —— WDA 健康检查，失败按 7.5 抛 InfraError
        #    （基础设施故障，不是测试失败；调用方据此重跑/终止）
        self.ds.ensure_alive()

        # 3. find —— PRE_DISPATCH：任何失败都说明动作从未发出
        try:
            found = self.ex.find(ctx.strategies)
        except InfraError as e:
            # 设备层故障向上冒，不归测试失败。附着幂等性信息：调用方（7.5
            # WDA 判定）要靠它记录「故障时非幂等是否已发出」——find 阶段
            # 动作必然未发出，flag 恒 False。
            e.non_idempotent_dispatched = False
            e.effective_idempotency = ctx.idempotency
            raise
        except Exception as e:
            return _done(ok=False, phase=FailurePhase.PRE_DISPATCH,
                         failure_type=_classify_find_error(e),
                         error=f"{type(e).__name__}: {e}",
                         retry_decision=retry_decision(
                             FailurePhase.PRE_DISPATCH, ctx.idempotency,
                             ctx.has_postcondition))

        # 2.6 e2e 实锤的契约归一：生产 Executor.find 返回单个元素（歧义抛
        # AmbiguousElement），测试替身/部分驱动返回列表。统一成列表后走
        # 同一套 H3 歧义判定，两种契约都兼容。
        elements = list(found) if isinstance(found, (list, tuple)) else [found]

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
            # 2.7 M2 Gate 真机实锤（第三处契约断层）：生产 Executor 没有
            # 统一 `perform(action, element, value)`——它按动作分派
            # tap(locator)/input(locator, value)。StepRunner 此前按
            # perform 契约调，接真实 Executor 必 AttributeError。这里按
            # action 分派；测试替身若提供 perform 仍优先（兼容旧测试）。
            self._dispatch_action(ctx, element)
        except InfraError as e:
            # 设备层故障：动作可能已发出 → 标记置位后上抛。flag 挂在异常上
            # （StepOutcome 没机会产出）：7.5 判「能否重跑」依赖它，漏了
            # 就会把「tap 中途断连」误判成可重跑 → 重复点击。
            e.non_idempotent_dispatched = non_idem_dispatched
            e.effective_idempotency = ctx.idempotency
            raise
        except Exception as e:
            return _done(ok=False, phase=FailurePhase.POST_DISPATCH,
                         failure_type="ACTION_OUTCOME_UNKNOWN",
                         error=f"{type(e).__name__}: {e}",
                         non_idempotent_dispatched=non_idem_dispatched,
                         retry_decision=retry_decision(
                             FailurePhase.POST_DISPATCH, ctx.idempotency,
                             ctx.has_postcondition))

        # 5. postcondition 检查（Task 2.7 / H7 闭环）：非幂等步骤动作已发出
        #    但结果未知时，postcondition 是**唯一**可判定途径。语义：
        #    - 满足 → 动作成功（ok=True）
        #    - 不满足 → RECOVERED 候选（kind=postcondition），不是 FAIL——
        #      恢复判定归 RecoveryEngine（7.3），这里只如实报告。
        post_detail: dict = {}
        if (ctx.postcondition_spec is not None
                and self.postcondition_checker is not None):
            try:
                ok_post = bool(self.postcondition_checker(
                    ctx.postcondition_spec))
            except Exception as e:  # noqa: BLE001 — 检查器故障不是动作失败
                # checker 自身炸了 → 如实记 UNKNOWN，不当成功也不当失败
                # （把观测故障伪装成判定结果是 8.2 反模式）
                ok_post = None
                post_detail["postcondition_error"] = (
                    f"{type(e).__name__}: {e}")
            else:
                post_detail["postcondition_satisfied"] = ok_post
            if ok_post is False:
                return _done(ok=False, phase=FailurePhase.POST_DISPATCH,
                             failure_type="ACTION_OUTCOME_UNKNOWN",
                             error="postcondition not satisfied",
                             non_idempotent_dispatched=non_idem_dispatched,
                             retry_decision=retry_decision(
                                 FailurePhase.POST_DISPATCH, ctx.idempotency,
                                 ctx.has_postcondition),
                             detail=post_detail)
        elif ctx.postcondition_spec is not None:
            post_detail["postcondition_skipped"] = "no checker injected"

        return _done(ok=True, non_idempotent_dispatched=non_idem_dispatched,
                     locator_strategy=self._first_strategy_type(ctx),
                     detail=post_detail)

    def _dispatch_action(self, ctx: RunStepContext, element) -> None:
        """POST_DISPATCH 动作分派（2.7 M2 Gate 契约修正）。

        生产 Executor 的契约是**按动作分派的方法**：
        tap(locator) / input(locator, value) —— 没有统一 perform()。
        测试替身若自带 perform(action, element, value) 仍优先（旧测试
        不断裂）。未知 action fail-loud（不静默跳过）。
        """
        perform = getattr(self.ex, "perform", None)
        if callable(perform):
            perform(ctx.action, element, ctx.value)
            return
        if ctx.action == "tap":
            self.ex.tap(ctx.strategies)
        elif ctx.action == "input":
            self.ex.input(ctx.strategies, ctx.value or "")
        else:
            raise ValueError(
                f"action {ctx.action!r} has no dispatch: Executor exposes "
                f"tap(locator)/input(locator, value) only")

    def dispatch(self, ctx: RunStepContext, element) -> None:
        """恢复路径重发动作的公开入口（9.2 settle 重试 / run_memo）。

        与 `_dispatch_action` 同一分派逻辑；公开别名是因为 Recovery 的
        `redispatch` callable 由 pipeline 装配，摸私有方法是坏邻居。
        """
        self._dispatch_action(ctx, element)

    @staticmethod
    def _first_strategy_type(ctx: RunStepContext) -> str | None:
        """首条策略的 type（trace 落 locator_strategy 列）。

        两种策略形态都兼容：Executor Locator 契约的 `{"type","value"}`
        dict（2.7 M2 Gate 起生产用这个）与历史 `("type","value")` 元组
        （测试替身/旧调用方）——normalize 到 dict 判定，不猜。
        """
        if not ctx.strategies:
            return None
        first = ctx.strategies[0]
        if isinstance(first, dict):
            return first.get("type")
        return first[0]

    # --- trace ---

    def _record(self, out: StepOutcome, ctx: RunStepContext) -> None:
        """写 trace。Redactor 前置由 recorder/storage 自己保证（H8）——
        这里不预脱敏也不后脱敏，避免出现第二条「写前/写后」路径。

        **兼容判定用签名探测，不用 `except TypeError` 盲捕**（R15-1）：
        旧实现靠「调用抛 TypeError」判断 recorder 是不是老接口，有两个致命
        问题——① 老 `tracer.recorder.Recorder.record_step` 首参 `run_id` 是
        **必填**，回退调用漏传 → 回退自身 TypeError → 被外层 except 兜住 →
        steps 表一行都写不进（探针实锤：跑一步 SUCCESS，steps 0 行）；
        ② recorder **函数体内**的 TypeError（编程错误）会被误判成「签名不兼容」
        再走一次回退，把 bug 伪装成兼容路径。
        现在先 `inspect` 签名决定走哪条路，只有「签名确实不兼容」才回退。

        **写盘异常不改变步骤结论**（但也不静默）：trace 是观测手段，磁盘满
        / 库锁了不该把一个 SUCCESS 的步骤变成崩溃。捕获后 `warnings.warn`
        ——不吞，但不让它打断执行；Report 侧对 warning 计数暴露 trace 丢失
        （Task 2.6），不靠抛异常阻断运行。
        """
        if self.rec is None:
            return
        payload = dict(
            step_index=out.step_index,
            action_type=out.action,
            status="SUCCESS" if out.ok else "FAILED",
            error=out.error,
            latency_ms=out.latency_ms,
        )
        try:
            # 首参是 run_id 还是 tc_run_id，按探测结果传（P0 Recorder 用前者，
            # TraceStore 用后者——两者都是必填位置参，漏了必 TypeError）。
            head = {}
            if self._first_arg == "run_id":
                head["run_id"] = self.run_id
            elif self._first_arg == "tc_run_id":
                head["tc_run_id"] = self.tc_run_id
            if _accepts_new_fields(self.rec):
                self.rec.record_step(
                    **head, **payload,
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
            else:
                # 老 Recorder（窄签名）：只收基础字段。M2 Gate 后随 P0 Recorder
                # 一起退役。
                self.rec.record_step(**head, **payload)
        except Exception as e:  # noqa: BLE001 — 见 docstring
            warnings.warn(f"trace write failed: {e!r}", RuntimeWarning,
                          stacklevel=2)


def _required_first_arg(recorder) -> tuple[str | None, str | None]:
    """探测 recorder.record_step 的**必填首参**，返回 (StepRunner 属性名, 参数名)。

    只认两种实际存在的形态：
      - `run_id`     —— P0 `tracer.recorder.Recorder`
      - `tc_run_id`  —— `tracer.storage.TraceStore`（run_id 已在 tc_run_id 里）

    探不到（无 record_step / 签名不可读）→ (None, None)，不强求任何 id。
    """
    if recorder is None:
        return None, None
    fn = getattr(recorder, "record_step", None)
    if not callable(fn):
        return None, None
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return None, None
    for name in ("run_id", "tc_run_id"):
        p = sig.parameters.get(name)
        if p is not None and p.default is inspect.Parameter.empty:
            return name, name
    return None, None


def _accepts_new_fields(recorder) -> bool:
    """recorder.record_step 是否接受 schema 0.1 的新字段（纯签名探测）。"""
    if recorder is None:
        return True
    fn = getattr(recorder, "record_step", None)
    if not callable(fn):
        return True
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return True   # 探不到签名（C 扩展等）→ 乐观尝试新路径
    params = sig.parameters
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return True   # **kwargs 兜底，什么都收
    return "effective_risk" in params


def _classify_find_error(e: Exception) -> str:
    """find 阶段异常 → failure_type。fail-loud：认不出来的抛出去而非归
    ELEMENT_NOT_FOUND（那会把编程错误伪装成测试失败）。

    Task 2.6：ElementNotFound 从 StepRunner 的 find 契约里显式映射——
    pipeline 注入的 locator 引擎抛它时，failure_type 必须是 8.2 枚举值
    （原归 `FIND_ERROR:ElementNotFound`，CI 无法按症状聚合）。
    """
    if isinstance(e, AmbiguousElement):
        return "AMBIGUOUS_ELEMENT"
    if isinstance(e, ElementNotFound):
        return "ELEMENT_NOT_FOUND"
    if isinstance(e, ValueError):
        return "ELEMENT_NOT_FOUND"
    return f"FIND_ERROR:{type(e).__name__}"
