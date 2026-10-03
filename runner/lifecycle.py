"""lifecycle — 7.5 WDA 故障处理 + TraceStore 写入（R14-2 收口）。

`TraceStore`（Task 2.4 建的 schema 0.1 写入层）此前零消费——runner /
Recorder / gate 脚本全无引用（review R14-2 P2 记账到 2.5）。本模块由
`SuiteRunner` 持有，把用例级执行结果写进 TraceStore。

R15-2 更正：本模块**不**从 `TestcaseRunner` 取结果（旧 P0 runner 不用
Lifecycle），也不被生产链路调用——目前只有 `tests/` 在用。真正的接线在
Task 2.6（`mta run` 把 SuiteRunner 接成入口）。此处「接线」指模块间的
依赖已建立且有测试覆盖，不等于生产链路已切换。

7.5 WDA 语义：
  - 每个 action 前 ensure_alive；失败 → restart_wda → 复检；仍失败抛
    InfraError（INFRA_FAILURE，不是测试失败）；
  - 每个 testcase 维护 `non_idempotent_dispatched`：只要有非幂等步骤到达过
    POST_DISPATCH 就置 true；
  - WDA 中途故障：false → 重启 + **重跑该 testcase**（attempt=2）；
    true → **不重跑**，直接 INFRA_FAILURE + 写 infra_events；
  - `wda.max_restart_per_run`（默认 2）超过则终止 run（INFRA_FAILURE）。

「不重跑」是硬约束：非幂等动作可能已在服务端生效，重跑会造成重复下单/
重复支付。这类后果不可回滚，所以宁可失败也不猜。
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field

from executor.policy import Idempotency
from session.device_session import InfraError
from tracer.storage import TraceStore

__all__ = ["WdaPolicy", "Lifecycle", "NonIdempotentDispatched"]


def _is_non_idempotent(out) -> bool:
    """这个 step 是否算「非幂等动作已发出」。

    看**两个**信号而不是只信 `out.non_idempotent_dispatched`：后者是
    StepRunner 在走完 perform 后才置的，find 阶段失败时它是 False（正确），
    但外部构造的 outcome（测试替身、未来的 recovery 路径）可能压根没设这个
    字段——那就退到 `effective_idempotency` 推导，避免「明明是非幂等步骤
    却记成没发出」，把 7.5 的最硬约束漏掉。

    UNKNOWN 一律当非幂等（7.4-3）。
    """
    if out.non_idempotent_dispatched:
        return True
    idem = out.effective_idempotency
    if idem is None:
        return False
    if isinstance(idem, str):
        idem = Idempotency(idem)
    return idem in (Idempotency.NON_IDEMPOTENT, Idempotency.UNKNOWN)


@dataclass
class WdaPolicy:
    """WDA 重启策略。默认值来自设计 7.5。"""

    max_restart_per_run: int = 2
    max_testcase_rerun: int = 1     # 同一 testcase 最多重跑 1 次（attempt=2）


class NonIdempotentDispatched(Exception):
    """WDA 故障时该 testcase 已有非幂等动作发出 → **不得重跑**（7.5）。

    单独一个异常类型而不是返回值：调用方必须显式处理（要么不重跑、要么
    记 infra_event），不能默默 continue。
    """

    def __init__(self, testcase_id: str, step_index: int | None = None):
        super().__init__(
            f"{testcase_id}: 非幂等动作已发出（step={step_index}），"
            f"WDA 故障后不得重跑（7.5）")
        self.testcase_id = testcase_id
        self.step_index = step_index


@dataclass
class Lifecycle:
    """用例级生命周期：WDA 故障判定 + TraceStore 写入。

    与 StepRunner 的分工：StepRunner 只管单步的阶段判定；
    `non_idempotent_dispatched` 是**跨 step 累积**的状态，必须由用例级持有。
    """

    store: TraceStore | None = None
    policy: WdaPolicy = field(default_factory=WdaPolicy)

    # --- 7.5 跨 step 累积状态 ---

    def __post_init__(self):
        self.reset_testcase_state()
        self.wda_restarts_this_run = 0   # run 级预算，跨用例累计

    def reset_testcase_state(self) -> None:
        """每条用例开始前清状态。

        注意**不动** `wda_restarts_this_run`——那是 run 级预算（7.5 的
        `max_restart_per_run` 是整个 run 的上限，不是每条用例的）。误清会让
        重启预算无限续杯，「超过上限终止 run」形同虚设。
        """
        self.non_idempotent_dispatched = False
        self.dispatched_step_index: int | None = None

    def note_dispatch(self, step_index: int, non_idempotent: bool) -> None:
        """记录「动作已发出」。非幂等步骤一旦到达 POST_DISPATCH 就置位，
        且**永不回落**（后果不可回滚）。"""
        if non_idempotent:
            self.non_idempotent_dispatched = True
            self.dispatched_step_index = step_index

    def handle_wda_failure(self, testcase_id: str, attempt: int,
                           device_session=None) -> None:
        """WDA 故障处理（7.5）。

        抛 `NonIdempotentDispatched` 表示不得重跑；其他情况在重启成功后
        返回，调用方可重跑该 testcase。重启预算耗尽 → InfraError。
        """
        if self.non_idempotent_dispatched:
            self._record_infra_event(
                testcase_id, "WDA_DEAD",
                action_taken="SKIP_RERUN",
                step_index=self.dispatched_step_index)
            raise NonIdempotentDispatched(testcase_id,
                                          self.dispatched_step_index)

        if attempt > self.policy.max_testcase_rerun:
            self._record_infra_event(testcase_id, "WDA_DEAD",
                                     action_taken="GIVE_UP",
                                     step_index=self.dispatched_step_index)
            raise InfraError(
                f"{testcase_id}: WDA 故障且已重跑 "
                f"{self.policy.max_testcase_rerun} 次仍失败（7.5）")

        self.wda_restarts_this_run += 1
        if self.wda_restarts_this_run > self.policy.max_restart_per_run:
            self._record_infra_event(testcase_id, "WDA_DEAD",
                                     action_taken="TERMINATE_RUN",
                                     step_index=self.dispatched_step_index)
            raise InfraError(
                f"wda.max_restart_per_run="
                f"{self.policy.max_restart_per_run} 已耗尽，终止 run（7.5）")

        # 7.5 要求 restart → **复检**。复检由调用方重跑时的首轮
        # ensure_alive 功能性覆盖；这里负责的是「restart 真的发生了」。
        # review P15 P3-6：原实现用 getattr 探测 restart_wda，缺失时静默
        # 跳过却仍记 action_taken=RESTART_WDA —— 账实不符，事后看 trace
        # 会以为重启过了。缺失时如实记 SKIP_NO_RESTART_API 并 warn。
        action_taken = "RESTART_WDA"
        if device_session is None:
            action_taken = "SKIP_NO_DEVICE_SESSION"
        else:
            restart = getattr(device_session, "restart_wda", None)
            if not callable(restart):
                action_taken = "SKIP_NO_RESTART_API"
                warnings.warn(
                    f"{testcase_id}: device_session has no restart_wda(); "
                    f"WDA was NOT restarted (recorded as {action_taken})",
                    RuntimeWarning, stacklevel=2)
            else:
                restart()
        self._record_infra_event(testcase_id, "WDA_DEAD",
                                 action_taken=action_taken,
                                 step_index=self.dispatched_step_index)

    # --- TraceStore 写入（R14-2） ---

    def _record_infra_event(self, testcase_id, event_type, action_taken,
                            step_index=None) -> None:
        if self.store is None:
            return
        self.store.record_infra_event(
            run_id=self.run_id or "", tc_run_id=self.tc_run_id,
            event_type=event_type, step_index=step_index,
            non_idempotent_dispatched=self.non_idempotent_dispatched,
            action_taken=action_taken,
            detail={"reason": "wda failure", "attempt": self.attempt})

    # 由 record_* 入口设置的关联 id
    run_id: str = ""
    tc_run_id: int | None = None
    attempt: int = 1
    # R18-4/R17-3：本 run 已落库的 step 总数（LLM Invocation Rate 的
    # 分母必须是步骤数，不是用例数——report 侧曾用 len(run.results)
    # 冒充，比率被系统性放大 20 条套件 ~7 倍）。
    steps_recorded: int = 0

    def begin_testcase(self, run_id: str, testcase_id: str,
                       attempt: int = 1) -> int:
        """开始一条用例，返回 testcase_run id（R14-2 接线点）。"""
        self.run_id = run_id
        self.attempt = attempt
        self.reset_testcase_state()
        if self.store is None:
            self.tc_run_id = None
            return 0
        self.tc_run_id = self.store.start_testcase(run_id, testcase_id,
                                                   attempt=attempt)
        return self.tc_run_id

    def record_step(self, out, step_index: int = 0,
                    status: str | None = None) -> None:
        """把 StepOutcome 写进 TraceStore（R14-2）。

        `detail` 透传断言结构化结果（R12-3/5 的端到端落点）。Redactor 前置
        由 TraceStore 保证（H8），这里不预脱敏——避免出现第二条路径。

        `status` 覆盖：Recovery Engine 恢复成功的步骤终态是 RECOVERED
        （8.1 步骤状态），不是 SUCCESS——「失败后恢复」与「一次成功」在
        trace 上必须可区分（RECOVERED 不得掩盖 flaky，19 节）。
        """
        self.note_dispatch(step_index, _is_non_idempotent(out))
        if self.store is None or self.tc_run_id is None:
            return None
        # R18-4/R17-3：无条件累计步骤数（LLM Invocation Rate 分母）
        self.steps_recorded += 1
        # locator 兜底：StepOutcome.locator_strategy 只在成功路径填；失败
        # 路径（找元素就没找到）没有策略信息。此时用 target_id 兜一条
        # `target:<id>`——**不是**声称某条策略命中过，而是「本次尝试的目标
        # 是它」。空 locator 会让报告里这一步没有任何定位信息，排障时无从
        # 判断是找不到还是压根没查。
        locator = None
        if out.locator_strategy:
            locator = {"strategy": out.locator_strategy,
                       "value": out.element_id}
        elif out.element_id:
            locator = {"strategy": "target", "value": out.element_id}
        row_id = self.store.record_step(
            self.tc_run_id,
            step_index=step_index,
            step_type=out.action,
            target_id=out.element_id,
            # 写入接口的参数叫 locator（内容是 {strategy, value}），
            # 它落库到 steps.locator_strategy 列
            locator=locator,
            effective_idempotency=(out.effective_idempotency.name
                                   if out.effective_idempotency else None),
            effective_risk=(out.effective_risk.name
                            if out.effective_risk else None),
            status=status or ("SUCCESS" if out.ok else "FAILED"),
            failure_type=out.failure_type,
            failure_phase=(out.phase.value if out.phase else None),
            latency_ms=out.latency_ms,
            detail=out.detail or None)
        # 返回 steps 行 id：恢复成功的步骤要落 recoveries 行（9.2），行关联
        # 需要精确 id（P0-2 教训：不要拿 MAX(id) 猜）。
        return row_id

    def end_testcase(self, status: str, failure_type: str | None = None,
                     cleanup_status: str | None = None,
                     detail: dict | None = None) -> None:
        """结束用例，写入终态 + non_idempotent_dispatched（7.5）。

        `detail`（可选）进 testcase_runs.detail_json——存储层先 redact。
        M5 基线实锤：FAIL 时管线内存里有 error 文本但此处不接收，trace
        的 detail_json 恒空，排障断流。"""
        if self.store is None or self.tc_run_id is None:
            return
        self.store.end_testcase(
            self.tc_run_id, status=status, failure_type=failure_type,
            cleanup_status=cleanup_status, detail=detail,
            non_idempotent_dispatched=self.non_idempotent_dispatched)
