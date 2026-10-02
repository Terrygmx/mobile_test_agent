"""cli.pipeline — Task 2.6：新执行管线的真实接线点（R15-2 验收核销）。

R16 修复后职责：
  - discover()：--suite/--tag/--case 三种筛选（14.6），递归发现
    suites/<suite>/*.yaml；坏 YAML / 无命中是 PreflightError（8.4 exit 3）；
  - run_case()：一条 TestCase → env.prepare（precondition 执行）→ 逐 step
    组装 RunStepContext → StepRunner.run_step → Lifecycle.record_step →
    Lifecycle.end_testcase → env.cleanup（H10）；
  - run_all()：套件语义收口到 SuiteRunner（prepare/cleanup/H10 中止，
    R16-4：不再内联重写 abort 循环）+ TraceStore run 级记录 → RunResult。

7.4 推导纪律（R16-1）：step 声明与 element 元数据**一律过 policy 纯函数
取严**——调用方只传声明源，绝不自行 or 链合并（or 链曾让用例层一行
`risk: LOW` 压掉元素 metadata 的 HIGH，安全级绕过点，探针实锤）。
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml

from runner.lifecycle import Lifecycle
from runner.result import RunResult, TestcaseResult
from source.screen import current_screen
from runner.runner import RunStepContext, StepOutcome, StepRunner
from session.device_session import InfraError
from testcase.schema import ActionStep, AssertionStep, TestCase, WaitSpec, WaitStep

__all__ = ["SessionPipeline", "PipelineDeps", "make_postcondition_checker"]

# App/driver 级动作（6.2 action 里的三种非元素动作）：不走 7.1 的
# find/perform 元素管线。launch/terminate 归 AppSession，back 归 driver，
# swipe 归 Executor——旧 runner（testcase_runner._do_*）同款分流。
_APP_LEVEL_ACTIONS = frozenset(
    {"launch_app", "terminate_app", "back", "swipe"})


class _NoopApp:
    """fake-driver 的 AppSession 桩（pipeline 内建，调用方无需注入）。"""

    def launch(self, arguments=None):
        pass

    def terminate(self):
        pass


class _NoopDeviceSession:
    """fake-driver 的 device session 桩：ensure_alive 返回自身，back no-op。"""

    def ensure_alive(self):
        return self

    def back(self):
        pass


@dataclass
class PipelineDeps:
    """真机运行所需的外部依赖。测试传 None + --fake-driver 走桩。

    `env`（EnvironmentManager）由 cli.main 在真机装配时注入；fake-driver
    与单测传 no-op 桩（precondition/cleanup 语义真实执行，动作是桩）。
    """

    executor: object | None = None
    app: object = None
    device_session: object | None = None
    store: object | None = None            # TraceStore
    db_path: Path | None = None
    appium_url: str = "http://127.0.0.1:4723"
    repo: object | None = None
    secrets: object | None = None
    env: object = None                     # EnvironmentManager（R16-4）

    def connect(self) -> None:  # 真机版由 cli.main 覆写
        """建立设备会话。默认 no-op（FakeDriver 模式）。"""


class _WdaRerunRequested(Exception):
    """内部控制流：WDA 重启成功，用例按 attempt=N 重跑（7.5）。

    不出 SessionPipeline——外部世界看到的是普通 TestcaseResult。
    """

    def __init__(self, attempt: int):
        super().__init__(f"rerun testcase with attempt={attempt}")
        self.attempt = attempt


class SessionPipeline:
    """把 TestCase 变成新管线的真实执行。"""

    class PreflightError(Exception):
        """前置配置错误（8.4 exit 3）。"""

    def __init__(self, suites_root: Path | str | None, *,
                 store=None, deps: PipelineDeps | None = None,
                 recovery=None):
        self.suites_root = Path(suites_root) if suites_root else None
        self.store = store
        self.deps = deps
        # Task 4.1：RecoveryEngine（确定性半边）。None = 不恢复（旧行为，
        # P0 链路不受影响）——接线由 cmd_run 按组件装配注入。
        self.recovery = recovery
        # 4.2：恢复后的定位覆盖（aux 步骤重跑用）——LLM 候选策略按目标 ref
        # 记住，locate() 优先消费。run 级生命周期（与 RUN_MEMO 同语义：
        # 同 run 同 build 内复用，pipeline 实例即 run 作用域）。
        self._recovered_locators: dict[str, list[dict]] = {}

    # --- 发现 ---

    def discover(self, *, suite: str | None = None, tag: str | None = None,
                 case: str | None = None) -> list[TestCase]:
        if self.suites_root is None or not self.suites_root.is_dir():
            raise self.PreflightError(f"suites root 不存在: {self.suites_root}")
        cases: list[TestCase] = []
        # 递归发现：套件目录按 <suites_root>/<suite>/*.yaml 组织（suites/smoke/），
        # 顶层平铺也兼容。坏 YAML 是 PreflightError，不静默跳过。
        for path in sorted(self.suites_root.rglob("*.yaml")):
            if path.name.startswith("_"):
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except yaml.YAMLError as e:
                raise self.PreflightError(f"坏 YAML: {path}: {e}") from None
            cases.append(parse_case(data, path))
        if suite is not None:
            cases = [c for c in cases if c.suite == suite]
        if tag is not None:
            cases = [c for c in cases if tag in c.tags]
        if case is not None:
            cases = [c for c in cases if c.id == case]
        if not cases:
            raise self.PreflightError(
                f"筛选无命中（suite={suite} tag={tag} case={case}）——"
                f"前置配置错误，不作为空集成功返回")
        return cases

    # --- 单条用例（新管线桥） ---

    def run_case(self, runner: StepRunner, lifecycle: Lifecycle,
                 tc: TestCase, *, run_id: str,
                 manage_env: bool = True) -> TestcaseResult:
        """一条 TestCase：prepare → 逐 step → end → cleanup（H10）。

        `manage_env=False` 供 SuiteRunner 驱动（run_all）使用——套件层已经
        负责 prepare/cleanup，这里再跑一遍就是双重复位。

        wait/assertion 步骤不在 StepRunner 的 find/perform 管线里（7.1 只管
        动作步骤），走 executor.wait/assertion 引擎；失败映射按 8.2。

        7.5 WDA 中途故障：非幂等未发出 → 重启 + 重跑本条（attempt=2，最多
        1 次）；已发出 → INFRA_FAILURE 不重跑；重启预算耗尽 → 终止 run。
        """
        attempt = 1
        while True:
            try:
                return self._run_case_once(
                    runner, lifecycle, tc, run_id=run_id,
                    manage_env=manage_env, attempt=attempt)
            except _WdaRerunRequested as signal:
                attempt = signal.attempt

    def _run_case_once(self, runner: StepRunner, lifecycle: Lifecycle,
                       tc: TestCase, *, run_id: str, manage_env: bool,
                       attempt: int) -> TestcaseResult:
        from executor.assertion import AssertionEngine
        from executor.wait import WaitEngine

        t0 = time.time()
        if self.store is not None:
            lifecycle.begin_testcase(run_id, tc.id, attempt=attempt)

        wait_engine = WaitEngine(
            runner.ex, self._locator_for(runner)) \
            if runner is not None else None
        assertion_engine = AssertionEngine(
            runner.ex, self._locator_for(runner)) \
            if runner is not None else None

        status = "PASS"
        failure_type = None
        failure_phase = None
        cleanup_status: str | None = None
        detail: dict = {}
        step_recovered: str | None = None   # 本步刚恢复的 kind（record 用）
        recovered_kinds: list[str] = []     # 8.1：任一步恢复 → 用例 RECOVERED

        if manage_env:
            try:
                self._prepare(tc)
            except Exception as e:  # noqa: BLE001
                # prepare 失败 = 环境问题，不是这条用例的 bug（11.2）
                status = "ENVIRONMENT_FAILURE"
                failure_type = "PREPARE_FAILED"
                detail["error"] = f"{type(e).__name__}: {e}"

        if status == "PASS":
            for idx, step in enumerate(tc.steps):
                try:
                    if isinstance(step, ActionStep):
                        # app/driver 级动作不走 find/perform 管线（7.1 只管
                        # 元素动作）：launch/terminate/back/swipe 在 P0 旧
                        # runner 也是分派到 AppSession/driver 的。2.7 M2 Gate
                        # 真机首跑实锤：当普通元素动作跑会 find(()) 空 →
                        # ELEMENT_NOT_FOUND（App 级动作压根没有 target）。
                        if step.action in _APP_LEVEL_ACTIONS:
                            t1 = time.time()
                            ok, err = self._run_app_level_action(
                                runner, step.action, step)
                            self._record_aux_step(
                                lifecycle, idx, step.action, "",
                                latency_ms=int((time.time() - t1) * 1000),
                                ok=ok,
                                failure_type=(None if ok
                                              else "ACTION_FAILED"),
                                detail=({"error": err} if err else None))
                            if not ok:
                                status = "FAIL"
                                failure_type = "ACTION_FAILED"
                                detail["error"] = err or ""
                                break
                            continue
                        step_ctx, outcome = self._run_action_step(
                            runner, step, idx)
                        rec = None
                        if (not outcome.ok and self.recovery is not None
                                and outcome.phase is not None):
                            rec = self.recovery.recover(self._recovery_context(
                                runner, step, outcome, step_ctx, tc, attempt))
                            if rec.recovered:
                                # 恢复成功：步骤继续，但终态是 RECOVERED 不是
                                # SUCCESS（8.1；RECOVERED 不得掩盖为 PASS）
                                outcome.ok = True
                                outcome.detail["recovery_kind"] = rec.kind
                                outcome.detail["recovery"] = rec.detail
                                step_recovered = rec.kind
                            else:
                                outcome.detail["recovery_attempted"] = rec.detail
                                # 矩阵 #9/#10：LLM 链拒绝/预算耗尽的 failure_type
                                # 替换步骤症状（8.2 语义——它就是最终症状）
                                if rec.failure_type:
                                    outcome.failure_type = rec.failure_type
                        # 失败步骤也落 steps 表——只记成功会让 trace
                        # 「看起来跑到一半就没了」，排障无从下手
                        step_row_id = lifecycle.record_step(
                            outcome, step_index=idx,
                            status="RECOVERED" if step_recovered else None)
                        if step_recovered and rec is not None:
                            # 9.2 末步：恢复必须写 recoveries 行（kind 用
                            # 14.2 大写枚举）；LLM 恢复建 PENDING review。
                            self._write_recovery_row(
                                step_row_id, outcome.element_id or "", rec)
                            recovered_kinds.append(step_recovered)
                            step_recovered = None
                        rec = None
                        if not outcome.ok:
                            # 8.1/8.4：Guard 拦截是安全策略终态——用例
                            # BLOCKED（exit 4），不是 FAIL（exit 1）。语义
                            # 分叉只此一处，退出码经 compute_exit_code。
                            status = ("BLOCKED" if outcome.failure_type
                                      == "SECURITY_BLOCKED" else "FAIL")
                            failure_type = outcome.failure_type
                            failure_phase = (outcome.phase.value
                                             if outcome.phase else None)
                            detail = dict(outcome.detail)
                            detail.setdefault("error", outcome.error)
                            break
                    elif isinstance(step, WaitStep):
                        t1 = time.time()
                        try:
                            wait_engine.wait_for(step.wait_for)
                        except InfraError:
                            raise
                        except Exception as e:
                            # 矩阵 #11/#12：screen 目标超时先做终态分类——
                            # 页面无任何已登记 marker=CURRENT_SCREEN_UNKNOWN、
                            # 多 marker 无 modal=SCREEN_AMBIGUOUS；「别的屏
                            # 在当前」才是 #13 的 WAIT_TIMEOUT（目标屏已登记
                            # 但未出现）。分类只拉一次 page_source（终态时）。
                            screen_ftype = self._screen_wait_failure(
                                runner, step.wait_for, e)
                            if screen_ftype is not None:
                                self._record_aux_step(
                                    lifecycle, idx, "wait_for",
                                    _target_label(step.wait_for.target),
                                    latency_ms=int((time.time() - t1) * 1000),
                                    ok=False, failure_type=screen_ftype,
                                    detail={"error": str(e)})
                                status = "FAIL"
                                failure_type = screen_ftype
                                detail["error"] = str(e)
                                break
                            # ⑥（4.2 顺延）：wait 超时进恢复管线——决策表
                            # 默认不准入（on_wait_timeout=false），开了才走
                            rec = self._aux_recover(
                                runner, tc, attempt, step.wait_for.target,
                                e, "wait")
                            if rec is None or not rec.recovered \
                                    or rec.strategy is None:
                                raise
                            self._recovered_locators[
                                self._ref_key(step.wait_for.target)] = \
                                [dict(rec.strategy)]
                            wait_engine.wait_for(step.wait_for)
                            self._record_aux_recovered(
                                lifecycle, idx, "wait_for",
                                _target_label(step.wait_for.target),
                                rec, latency_ms=int((time.time() - t1) * 1000))
                            recovered_kinds.append(rec.kind)
                            continue
                        self._record_aux_step(
                            lifecycle, idx, "wait_for",
                            _target_label(step.wait_for.target),
                            latency_ms=int((time.time() - t1) * 1000))
                    elif isinstance(step, AssertionStep):
                        t1 = time.time()
                        try:
                            result = assertion_engine.check(step.assertion)
                        except InfraError:
                            raise
                        except Exception as e:
                            # H6：断言**值**失败不进恢复；目标漂移（定位失效）
                            # 是 find 语义 → 矩阵 #15（kind=assertion_target
                            # 指恢复上下文，recoveries.kind 仍是机制枚举）
                            if type(e).__name__ != "AssertionTargetDrift":
                                raise
                            rec = self._aux_recover(
                                runner, tc, attempt, step.assertion.target,
                                e, "assert")
                            if rec is None or not rec.recovered \
                                    or rec.strategy is None:
                                raise
                            self._recovered_locators[
                                self._ref_key(step.assertion.target)] = \
                                [dict(rec.strategy)]
                            result = assertion_engine.check(step.assertion)
                            if not result.passed:
                                raise  # 覆盖重验仍不过 → 原异常语义 FAIL
                            self._record_aux_recovered(
                                lifecycle, idx, "assert",
                                _target_label(step.assertion.target),
                                rec, latency_ms=int((time.time() - t1) * 1000),
                                context="assertion_target")
                            recovered_kinds.append(rec.kind)
                            continue
                        self._record_aux_step(
                            lifecycle, idx, "assert",
                            _target_label(step.assertion.target),
                            latency_ms=int((time.time() - t1) * 1000),
                            ok=result.passed,
                            failure_type=(None if result.passed
                                          else "ASSERTION_VALUE_MISMATCH"),
                            detail={"assertion": {
                                "condition": result.condition,
                                "target": result.target,
                                "expected": _jsonable(result.expected),
                                "actual": _jsonable(result.actual),
                            }})
                        if not result.passed:
                            status = "FAIL"
                            failure_type = "ASSERTION_VALUE_MISMATCH"
                            # P3-2 拍板：值不匹配 ≠ 目标漂移。detail key 用
                            # assertion_value（原 assertion_target 标签错位），
                            # 结构化结果入用例 detail，核销 R12-3/5 端到端。
                            detail["assertion_value"] = {
                                "condition": result.condition,
                                "target": result.target,
                                "expected": _jsonable(result.expected),
                                "actual": _jsonable(result.actual),
                            }
                            break
                except InfraError as e:
                    # 7.5：WDA 中途故障不是测试失败。失败步骤也落 steps 表
                    # ——异常上附着「非幂等是否已发出」（StepRunner 打标，
                    # dispatch 中途断连也算已发出），lifecycle 据此判能否重跑。
                    status, failure_type = "INFRA_FAILURE", "WDA_FAILURE"
                    detail["error"] = f"{type(e).__name__}: {e}"
                    detail["wda_died"] = True
                    lifecycle.record_step(StepOutcome(
                        ok=False, step_index=idx,
                        element_id=(step.target.id
                                    if getattr(step, "target", None) else ""),
                        action=getattr(step, "action", None),
                        failure_type="WDA_FAILURE",
                        error=f"{type(e).__name__}: {e}",
                        effective_idempotency=getattr(
                            e, "effective_idempotency", None),
                        non_idempotent_dispatched=bool(getattr(
                            e, "non_idempotent_dispatched", False)),
                        detail={"error": detail["error"]}), step_index=idx)
                    break
                except Exception as e:  # noqa: BLE001 — 映射见 _map_exception
                    status, failure_type = _map_exception(e)
                    failure_phase = getattr(e, "phase", None)
                    detail["error"] = f"{type(e).__name__}: {e}"
                    # R12-3/5：AssertionValueMismatch 自带结构化结果，落库
                    result = getattr(e, "result", None)
                    if result is not None:
                        detail["assertion_value"] = {
                            "condition": result.condition,
                            "target": result.target,
                            "expected": _jsonable(result.expected),
                            "actual": _jsonable(result.actual),
                        }
                    break

        # H10：cleanup 必须执行；失败升级 ENVIRONMENT_FAILURE 而非吞掉。
        # （manage_env=False 时由 SuiteRunner 负责，这里不动。）
        if manage_env:
            cleanup_status = "OK"
            try:
                self._cleanup(tc)
            except Exception as e:  # noqa: BLE001
                from runner.suite import CleanupError
                if isinstance(e, CleanupError):
                    cleanup_status = "FAILED"
                    if status == "PASS":
                        status = "ENVIRONMENT_FAILURE"
                        failure_type = "CLEANUP_FAILED"
                        detail = {"cleanup_error": str(e)}
                    else:
                        detail["cleanup_error"] = str(e)
                else:
                    raise

        # 8.1 优先级：FAIL > RECOVERED > PASS——有步骤恢复成功且最终没有
        # 更高优先级失败 → 用例 RECOVERED（exit 5：无 FAIL 但需人工确认）
        if recovered_kinds and status == "PASS":
            status = "RECOVERED"
            detail["recovery_kinds"] = recovered_kinds

        result = TestcaseResult(
            testcase_id=tc.id, status=status, failure_type=failure_type,
            failure_phase=failure_phase, cleanup_status=cleanup_status,
            duration_ms=int((time.time() - t0) * 1000), detail=detail)
        if self.store is not None:
            lifecycle.end_testcase(
                status, failure_type=failure_type,
                # manage_env=False（套件路径）时 cleanup 归 SuiteRunner，
                # 这里写 PENDING——不是 OK（还没 cleanup 呢）也不是 None。
                # 套件层成功回 OK / 失败由 R18-3 回写 ENVIRONMENT_FAILURE。
                cleanup_status=cleanup_status or (
                    None if manage_env else "PENDING"))

        # 7.5：WDA 故障后的重跑/终止判定（result 已落库，attempt 行终态如实）
        if result.detail.get("wda_died"):
            return self._wda_aftermath(runner, lifecycle, tc, attempt, result)
        return result

    def _wda_aftermath(self, runner, lifecycle, tc, attempt,
                       result: TestcaseResult) -> TestcaseResult:
        """7.5 WDA 故障处置：非幂等已发出 → 不重跑；预算内 → 重跑
        attempt+1；重跑/重启预算耗尽 → INFRA_FAILURE 终止。"""
        from runner.lifecycle import NonIdempotentDispatched

        try:
            lifecycle.handle_wda_failure(
                tc.id, attempt,
                device_session=runner.ds if runner is not None else None)
        except NonIdempotentDispatched as nie:
            # H7/7.5 最硬约束：后果不可回滚，宁可失败也不猜
            result.detail["rerun_skipped"] = str(nie)
            return result
        except InfraError as ie:
            result.detail["rerun_skipped"] = f"{type(ie).__name__}: {ie}"
            # 重启预算（run 级）耗尽 → 终止 run：上层 run_all 据此停调度
            result.detail["terminate_run"] = (
                lifecycle.wda_restarts_this_run
                > lifecycle.policy.max_restart_per_run)
            return result
        raise _WdaRerunRequested(attempt=attempt + 1)

    def _recovery_context(self, runner, step, outcome, step_ctx, tc,
                          attempt):
        """从步骤执行上下文装配 RecoveryContext（9.1）。

        设备交互端以 callable 注入——引擎不持有 Executor（9.1 隔离纪律）。
        source_metadata 置 None：12.3 两键 metadata → reconcile_local 子集
        的适配在 Task 4.2（LLM prompt 同需该切片）一并做。
        """
        from agent.context import RecoveryContext

        ex = runner.ex if runner is not None else None
        checker = (getattr(runner, "postcondition_checker", None)
                   if runner is not None else None)
        post_fn = None
        if step_ctx.postcondition_spec is not None and checker is not None:
            post_fn = lambda: checker(step_ctx.postcondition_spec)  # noqa: E731
        return RecoveryContext(
            failure_type=outcome.failure_type,
            phase=outcome.phase,
            element_id=step_ctx.element_id,
            screen_id=step_ctx.screen_id,
            strategies=step_ctx.strategies,
            action=step_ctx.action,
            value=step_ctx.value,
            effective_risk=step_ctx.risk,
            effective_idempotency=step_ctx.idempotency,
            has_postcondition=step_ctx.has_postcondition,
            expected_type=getattr(step_ctx, "expected_type", None),
            source_metadata=None,
            refind=(lambda: ex.find(step_ctx.strategies))
            if ex is not None else None,
            # RUN_MEMO 策略消费端（9.4）：memo 存的恢复策略经它重找
            find_with=(lambda strategies: ex.find(list(strategies)))
            if ex is not None else None,
            redispatch=(lambda element: runner.dispatch(step_ctx, element))
            if runner is not None else None,
            page_source=(getattr(ex, "page_source", None)
                         if ex is not None else None),
            postcondition_check=post_fn,
            testcase_id=tc.id, attempt=attempt, app_build="local")

    def _run_app_level_action(self, runner: StepRunner, action: str,
                              step: ActionStep) -> tuple[bool, str | None]:
        """App/driver 级动作执行（M2 Gate 真机实锤后新增）。

        分流（与 P0 testcase_runner._do_* 一致）：
          launch_app    → deps.app.launch()
          terminate_app → deps.app.terminate()
          back          → device session 的 driver.back()
          swipe         → executor.swipe(step.direction)

        返回 (ok, error)。FAIL 语义：普通 FAIL + ACTION_FAILED（8.4 exit 1）
        ——这是用例动作执行失败，不是环境问题。
        """
        try:
            if action == "launch_app":
                self._app_or_stub(_NoopApp).launch()
            elif action == "terminate_app":
                self._app_or_stub(_NoopApp).terminate()
            elif action == "back":
                self._device_session_or_stub(runner).ensure_alive().back()
            elif action == "swipe":
                if hasattr(runner.ex, "swipe"):
                    runner.ex.swipe(step.direction or "up")
                else:
                    return False, "executor has no swipe()"
            else:
                return False, f"unknown app-level action {action!r}"
        except SessionPipeline.PreflightError:
            raise
        except Exception as e:  # noqa: BLE001 — 步骤失败如实上报
            return False, f"{type(e).__name__}: {e}"
        return True, None

    def _app_or_stub(self, stub_cls):
        """deps.app 存在用之；否则走 no-op 桩（fake-driver 模式）。

        桩的语义是「调用成功」——fake-driver 的定位本来就是跑通管线，
        不验证设备侧效果（那是真机 Gate 的职责）。
        """
        if self.deps is not None and self.deps.app is not None:
            return self.deps.app
        return stub_cls()

    def _device_session_or_stub(self, runner: StepRunner):
        """取 device session：deps → runner.ds → no-op 桩。

        测试替身常把 `ensure_alive` 写成 `pass`（返回 None）——直接拿
        runner.ds 去 `.ensure_alive().back()` 会 NoneType 崩。这里只要
        ensure_alive 拿不到真 driver 就回落 no-op 桩（back 是 no-op，
        fake-driver 不验证设备侧效果）。
        """
        ds = None
        if self.deps is not None and self.deps.device_session is not None:
            ds = self.deps.device_session
        elif runner is not None and getattr(runner, "ds", None) is not None:
            ds = runner.ds
        else:
            return _NoopDeviceSession()
        try:
            alive = ds.ensure_alive()
        except Exception:  # noqa: BLE001 — 探测失败回落桩
            return _NoopDeviceSession()
        if alive is None:
            return _NoopDeviceSession()
        return ds

    def _run_action_step(self, runner: StepRunner, step: ActionStep,
                         idx: int) -> StepOutcome:
        # R16-1：7.4「取更严格」只能由 policy 纯函数裁决——step 声明与
        # element metadata 两个声明源都传进去，None 源自动跳过，一个不丢。
        # （原 `step.risk or meta_risk` 曾让 LOW 声明压掉 metadata HIGH。）
        from executor.policy import effective_idempotency, effective_risk

        ref = step.target
        element_id = ref.id if ref is not None else ""
        eff = None
        # screen 引用不进 find/perform 管线（它是 wait/assert 的目标）
        # Executor.find 的 Locator 契约是 list[dict]（strat["type"] 下标
        # 访问）——2.7 M2 Gate 真机实锤：传 (type, value) 元组会 TypeError。
        strategies: tuple = tuple()
        if ref is not None and getattr(ref, "type", "element") == "element":
            if self.deps and self.deps.repo is not None:
                eff = self.deps.repo.resolve(ref, build="local")
                # element_id 必须是解析后的裸 id（eff.id）——ref.id 是容器
                # 前缀引用（LoginView.username_field），源 metadata 里只有
                # 裸 id；带着前缀进恢复上下文，reconcile 会把「源里有、
                # 运行时没有」的 DRIFT 误判成 UNKNOWN（M4 Gate 真机实锤）。
                element_id = eff.id
                strategies = tuple({"type": s.type, "value": s.value}
                                   for s in eff.strategies)
                meta_risk = getattr(eff, "risk", None)
                meta_idem = getattr(eff, "idempotency", None)
                screen_id = eff.screen
            else:
                strategies = ({"type": "accessibility_id",
                               "value": element_id},)
                meta_risk = None
                meta_idem = None
                screen_id = ""
        else:
            meta_risk = None
            meta_idem = None
            screen_id = ""

        risk = effective_risk(step=step.risk, element=meta_risk,
                              element_id=element_id)
        idem = effective_idempotency(step.idempotency, meta_idem,
                                     element_id=element_id)
        ctx = RunStepContext(
            element_id=element_id, screen_id=screen_id,
            strategies=strategies, action=step.action, value=step.value,
            risk=risk, idempotency=idem,
            has_postcondition=step.postcondition is not None,
            # H7 闭环：spec 也传下去（不只布尔）——执行端见
            # make_postcondition_checker / StepRunner.postcondition_checker
            postcondition_spec=step.postcondition,
            expected_type=getattr(eff, "type", None),
            step_index=idx)
        return ctx, runner.run_step(ctx)
    def _record_aux_step(self, lifecycle: Lifecycle, idx: int, step_type: str,
                         target_id: str, *, ok: bool = True,
                         failure_type: str | None = None,
                         latency_ms: int = 0, detail: dict | None = None,
                         status: str | None = None
                         ) -> int | None:
        """wait/assert 步骤也落 steps 表（P3-1：TraceStore 全步可见）。

        `status` 覆盖：aux 恢复成功终态 RECOVERED（8.1，不是 SUCCESS）。
        返回 steps 行 id——aux 恢复也要落 recoveries 行（9.2）。"""
        if self.store is None:
            return None
        return lifecycle.record_step(StepOutcome(
            ok=ok, step_index=idx, action=step_type, element_id=target_id,
            failure_type=failure_type, latency_ms=latency_ms,
            detail=detail or {}), step_index=idx, status=status)

    def _write_recovery_row(self, step_row_id: int | None, target_id: str,
                            rec, context: str | None = None) -> None:
        """recoveries 行（kind=机制枚举）+ LLM 恢复建 PENDING review
        （9.2 末步 / 9.5 流程入口）。动作步与 aux 步共用。"""
        if self.store is None or not step_row_id:
            return
        kind_map = {"settle_retry": "SETTLE_RETRY", "postcondition":
                    "POSTCONDITION", "run_memo": "RUN_MEMO",
                    "local_reconcile": "DETERMINISTIC_CANDIDATE"}
        rec_kind = kind_map.get(rec.kind, (rec.kind or "LLM").upper())
        rec_id = self.store.record_recovery(
            step_row_id, rec_kind, expected_target=target_id,
            candidate_target=(rec.strategy or {}).get("value"),
            candidate_type=rec.detail.get("candidate_type"),
            screen=rec.detail.get("screen"),
            app_build="local", result="RECOVERED", accepted=True)
        if rec.kind == "llm":
            self.store.create_review(rec_id)

    def _record_aux_recovered(self, lifecycle, idx, step_type, target_id,
                              rec, *, latency_ms: int = 0,
                              context: str | None = None) -> None:
        """aux 步骤恢复成功：steps 行 RECOVERED + recoveries 行。"""
        step_row_id = lifecycle.record_step(
            StepOutcome(ok=True, step_index=idx, action=step_type,
                        element_id=target_id, latency_ms=latency_ms,
                        detail={"recovery_kind": rec.kind,
                                "recovery_context": context,
                                "recovery": rec.detail}),
            step_index=idx, status="RECOVERED")
        self._write_recovery_row(step_row_id, target_id, rec, context)

    def _screen_wait_failure(self, runner, wait_spec, exc) -> str | None:
        """screen 目标 wait 超时的终态分类（13.2；矩阵 #11/#12 vs #13）。

        - 页面可见 marker 互斥不明（≥2 且无 modal）→ SCREEN_AMBIGUOUS；
        - 页面无任何已登记 marker 且**目标屏已登记**（app 侧丢了 marker，
          「Screen marker 缺失」的字面义）→ CURRENT_SCREEN_UNKNOWN；
        - 目标屏未登记（lint 本应拦的退化形态）或别的屏在当前 → None
          （维持 WAIT_TIMEOUT，#13）。
        非 screen 目标 / 非 WaitTimeout / 无 repo → None（不分类）。
        """
        from executor.wait import WaitTimeout

        if not isinstance(exc, WaitTimeout):
            return None
        if getattr(wait_spec.target, "type", "element") != "screen":
            return None
        repo = self.deps.repo if self.deps else None
        ex = runner.ex if runner is not None else None
        if repo is None or ex is None \
                or not callable(getattr(ex, "page_source", None)):
            return None
        try:
            target_registered = repo.resolve(wait_spec.target, build="local")
        except Exception:  # noqa: BLE001 — 目标屏未登记 → 退化形态不分类
            return None
        try:
            res = current_screen(ex.page_source(), repo)
        except Exception:  # noqa: BLE001 — page_source 故障不改变症状
            return None
        if res.status == "SCREEN_AMBIGUOUS":
            return "SCREEN_AMBIGUOUS"
        if res.status == "CURRENT_SCREEN_UNKNOWN" \
                and getattr(target_registered, "marker", None):
            return "CURRENT_SCREEN_UNKNOWN"
        return None

    def _aux_recover(self, runner, tc, attempt, target_ref, exc,
                     step_type: str):
        """aux 步骤（wait/assert）的恢复入口（4.2 ⑤⑥）。

        断言目标漂移（矩阵 #15）与 wait 超时（on_wait_timeout 接通）经
        引擎取回**已校验的候选策略**——aux 无 dispatch 语义，ctx.redispatch
        为 None，引擎只验证不执行；重跑由调用方在覆盖定位后做一次。
        返回 None = 未准入/未恢复（调用方按原异常走 FAIL）。
        """
        if self.recovery is None:
            return None
        from agent.context import RecoveryContext
        from executor.policy import FailurePhase, Idempotency

        failure_type, _ = _map_exception(exc)
        phase = FailurePhase.PRE_DISPATCH
        ref_id = getattr(target_ref, "id", "") or ""
        eff_screen, eff_type = "", None
        strategies: tuple = ()
        repo = self.deps.repo if self.deps else None
        if repo is not None and ref_id:
            try:
                eff = repo.resolve(target_ref, build="local")
                eff_screen = eff.screen
                eff_type = getattr(eff, "type", None)
                strategies = tuple({"type": st.type, "value": st.value}
                                   for st in eff.strategies)
            except Exception:  # noqa: BLE001 — 引用解析失败照常进引擎
                pass
        ex = runner.ex if runner is not None else None
        ctx = RecoveryContext(
            failure_type=failure_type, phase=phase,
            element_id=ref_id, screen_id=eff_screen,
            strategies=strategies,
            action=None,                      # aux 无动作语义
            effective_idempotency=Idempotency.IDEMPOTENT,  # 读操作可重验
            expected_type=eff_type,
            refind=(lambda: ex.find(list(strategies)))
            if ex is not None and strategies else None,
            find_with=(lambda sts: ex.find(list(sts)))
            if ex is not None else None,
            redispatch=None,                  # 只验证不执行（H7 精神）
            page_source=(getattr(ex, "page_source", None)
                         if ex is not None else None),
            testcase_id=tc.id, attempt=attempt, app_build="local")
        return self.recovery.recover(ctx)

    @staticmethod
    def _ref_key(target_ref) -> str:
        return f"{getattr(target_ref, 'type', 'element')}:" \
               f"{getattr(target_ref, 'id', target_ref)}"

    def _locator_for(self, runner: StepRunner):
        def locate(target_ref):
            # 恢复覆盖优先（4.2 aux 重跑）：该目标被 LLM 候选恢复过 → 直接
            # 用恢复策略定位。不查 repo——漂移元素在 repo 里 resolve 会失败。
            override = self._recovered_locators.get(self._ref_key(target_ref))
            if override:
                return [dict(s) for s in override]
            if self.deps and self.deps.repo is not None:
                eff = self.deps.repo.resolve(target_ref, build="local")
                if hasattr(eff, "marker"):
                    return [{"type": "accessibility_id", "value": eff.marker}]
                return [{"type": s.type, "value": s.value}
                        for s in eff.strategies
                        if s.type in ("accessibility_id", "predicate",
                                      "class_chain")]
            return [{"type": "accessibility_id", "value": target_ref.id}]
        return locate

    def _prepare(self, tc: TestCase) -> None:
        if self.deps is not None and self.deps.env is not None:
            self.deps.env.prepare(tc)

    def _cleanup(self, tc: TestCase) -> None:
        if self.deps is not None and self.deps.env is not None:
            self.deps.env.cleanup(tc)

    def _write_cleanup_failure(self, result) -> None:
        """R18-3：cleanup 失败后把该 testcase_run 的真实终态回写 trace。

        run_case(manage_env=False) 已先落了 PASS 终态，suite 层 cleanup
        失败无人回写 → testcase_runs 两行 PASS 但 runs=INFRA，矛盾。
        这里用 lifecycle 的 end_testcase 重发终态（H10 升级后的）。
        lifecycle 同步持有 tc_run_id 游标，Detail 幂等。
        """
        if self.store is None:
            return
        lc = self._lifecycle
        if lc is None:
            return
        lc.end_testcase(
            result.status, failure_type=result.failure_type,
            cleanup_status=result.cleanup_status)

    # --- 整个 run ---

    def run_all(self, cases: list[TestCase], *, run_id: str,
                failure_policy: str = "ABORT_SUITE") -> RunResult:
        """多条用例 → RunResult。R16-4：套件语义收口到 SuiteRunner
        （prepare/cleanup/H10 中止），不再内联重写 abort 循环。

        run 级 TraceStore 记录（start_run/end_run）由**调用方**负责——
        cli.main 需要在组件装配失败时也写 ABORTED 终态，start 时机因此
        早于 run_all。run_id 由调用方生成（uuid，P3-3 防同秒碰撞）。
        """
        from runner.suite import FailurePolicy, SuiteRunner, SuiteAborted

        run = RunResult(run_id=run_id, suite=cases[0].suite if cases else None)
        self._run_id = run_id
        try:
            policy = (failure_policy if isinstance(failure_policy,
                                                   FailurePolicy)
                      else FailurePolicy[failure_policy])
            suite_runner = SuiteRunner(
                env=self._suite_env(), run_one=self._suite_run_one,
                failure_policy=policy,
                # R18-3：cleanup 失败回写 testcase_run（否则 trace 停在
                # run_case 落的 PASS，排障看到矛盾终态）。
                on_cleanup_failure=self._write_cleanup_failure)
            try:
                suite_run = suite_runner.run_suite(cases)
                run.results.extend(suite_run.results)
            except SuiteAborted:
                # H10 中止：SuiteRunner 把已跑结果留在自己的 RunResult 里，
                # 但 **run_suite 中途 raise 时那个对象拿不到**（返回值没到）。
                # 从 SuiteRunner 实例的累积列表取回——已跑结果必须保留：
                # 「跑到哪因环境问题停了」是报告要展示的事实，清掉等于
                # 伪造「没跑过」。
                run.results.extend(suite_runner.results)
                # 中止标记（aborted_suite/remaining）补在最后一条上：
                # run_suite 是在 raise 前一刻写进它自己的 result dict 的，
                # 经上面 extend 回来的对象同一身份，直接读。
                if run.results:
                    last = run.results[-1]
                    last.detail.setdefault(
                        "aborted_suite",
                        last.detail.get("aborted_suite", True))
                    last.detail.setdefault(
                        "remaining",
                        len(cases) - len(run.results))
        finally:
            if self.store is not None:
                self.store.end_run(run_id, status=_run_status(run),
                                   exit_code=run.exit_code)
        return run

    def _suite_run_one(self, tc: TestCase) -> TestcaseResult:
        """SuiteRunner 的 run_one：跑步骤（env 由套件层管，manage_env=False）。"""
        runner = self._step_runner
        if runner is None:
            # R16-2：组件未装配是前置配置错误，fail-loud（exit 3 语义），
            # 不伪装成用例 FAIL 写脏 trace.db
            raise self.PreflightError(
                "执行组件未装配（StepRunner 为 None）——真机装配在 Task 2.7，"
                "当前请使用 --fake-driver")
        # H7 闭环：postcondition 执行端注入（Task 2.7）。只在没注入过时建——
        # 每次建新 checker 会丢掉上层对同一 checker 的复用/计数语义。
        if runner.postcondition_checker is None:
            runner.postcondition_checker = make_postcondition_checker(
                self, runner)
        lifecycle = self._lifecycle or Lifecycle(store=self.store)
        return self.run_case(runner, lifecycle, tc, run_id=self._run_id,
                             manage_env=False)

    def _suite_env(self):
        """SuiteRunner 需要 EnvironmentManager；无 deps.env 时给 no-op。"""
        if self.deps is not None and self.deps.env is not None:
            return self.deps.env

        class _NoopEnv:
            def prepare(self, tc):
                pass

            def cleanup(self, tc=None):
                pass

        return _NoopEnv()

    # 真机版由 cli.main 注入；run_all 路径经 _suite_run_one 消费
    _step_runner: StepRunner | None = None
    _lifecycle: Lifecycle | None = None
    _run_id: str = ""


def _run_status(run: RunResult) -> str:
    if run.passed:
        return "PASS"
    if any(r.status in ("INFRA_FAILURE", "ENVIRONMENT_FAILURE")
           for r in run.results):
        return "INFRA_FAILURE"
    if all(r.status in ("PASS", "RECOVERED") for r in run.results):
        # 8.1：RECOVERED 是独立终态——exit 5 的 run 不能落 FAIL
        # （M4 Gate 真机实锤：漂移 run exit_code=5 而 runs.status=FAIL）
        return "RECOVERED"
    return "FAIL"


def _map_exception(e: Exception) -> tuple[str, str]:
    """7.3/8.2：wait/assertion 引擎异常 → (status, failure_type)。"""
    from executor.assertion import AssertionValueMismatch, AssertionTargetDrift
    from executor.executor import AmbiguousElement, ElementNotFound
    from executor.wait import WaitTimeout
    from session.device_session import InfraError

    if isinstance(e, InfraError):
        return "INFRA_FAILURE", "WDA_FAILURE"
    if isinstance(e, WaitTimeout):
        return "FAIL", "WAIT_TIMEOUT"
    if isinstance(e, AssertionValueMismatch):
        return "FAIL", "ASSERTION_VALUE_MISMATCH"
    if isinstance(e, AssertionTargetDrift):
        # P3-4：8.2 枚举无此值，归最接近项（detail 保留结构化结果）
        return "FAIL", "ELEMENT_NOT_FOUND"
    if isinstance(e, AmbiguousElement):
        return "FAIL", "AMBIGUOUS_ELEMENT"
    if isinstance(e, ElementNotFound):
        return "FAIL", "ELEMENT_NOT_FOUND"
    return "FAIL", type(e).__name__


def parse_case(data: dict, path: Path) -> TestCase:
    try:
        from testcase.schema import parse_testcase_dict
        return parse_testcase_dict(data)
    except Exception as e:
        raise SessionPipeline.PreflightError(
            f"用例加载失败 {path}: {e}") from None


def _jsonable(v):
    """AssertionResult 的 expected/actual 可能是驱动对象；trace 只存 JSON。"""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def make_postcondition_checker(pipeline: "SessionPipeline",
                               runner: StepRunner):
    """Task 2.7：postcondition 执行端。复用 WaitEngine 的条件矩阵
    （同一套 wait_for 语义：满足即真、超时即假）——不为 postcondition
    另造一套判定，避免两套真相漂移。

    `spec` 是 testcase.schema.Postcondition（target/condition/timeout）：
    转成 WaitSpec 跑一次 wait_for。这是 H7 的最后一步闭环——此前
    StepRunner 只把 has_postcondition 用于重试决策，从不执行检查。
    """
    from executor.wait import WaitEngine, WaitTimeout

    locator_for = pipeline._locator_for(runner)

    def _check(spec) -> bool:
        kwargs = dict(target=spec.target, condition=spec.condition,
                      timeout=getattr(spec, "timeout", 10) or 10,
                      polling_interval=None)
        if "expected" in WaitSpec.model_fields:
            kwargs["expected"] = getattr(spec, "expected", None)
        engine = WaitEngine(runner.ex, locator_for)
        try:
            engine.wait_for(WaitSpec(**kwargs))
        except WaitTimeout:
            return False
        return True

    return _check


def _target_label(target) -> str:
    """TargetRef → steps.target_id 可读标签（screen:HomeView / LoginView.x）。"""
    tid = getattr(target, "id", "") or ""
    if getattr(target, "type", "element") == "screen":
        return f"screen:{tid}"
    return tid
