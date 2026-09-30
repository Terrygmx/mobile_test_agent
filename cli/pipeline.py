"""cli.pipeline — Task 2.6：新执行管线的真实接线点（R15-2 验收核销）。

R15-2 的口径：StepRunner/Lifecycle/SuiteRunner 此前只被 tests/ 引用，
生产链路（TestcaseRunner/gate/cli）零引用。本模块是**第一个生产消费方**：
`mta run` → SessionPipeline → StepRunner（7.1）+ Lifecycle（7.5）+
TraceStore（14.2）+ SuiteRunner（套件语义）。

分工：
  - discover()：--suite/--tag/--case 三种筛选（14.6），无命中是
    PreflightError（8.4 exit 3），不是空集成功；
  - run_case()：一条 TestCase → 逐 step 组装 RunStepContext →
    StepRunner.run_step → Lifecycle.record_step → Lifecycle.end_testcase；
  - run_all()：多条用例 + SuiteRunner 的 H10 语义 + TraceStore run 级
    记录 → RunResult。

真机接入（Appium URL/凭证/设备 caps）由 cli.main 按 config 组装
PipelineDeps；测试注入 FakeDriver 级组件（--fake-driver / 直接构造）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from runner.lifecycle import Lifecycle
from runner.result import RunResult, TestcaseResult
from runner.runner import RunStepContext, StepOutcome, StepRunner
from testcase.schema import ActionStep, AssertionStep, TestCase, WaitStep

__all__ = ["SessionPipeline", "PipelineDeps"]


@dataclass
class PipelineDeps:
    """真机运行所需的外部依赖。测试传 None + --fake-driver 走桩。"""

    executor: object | None = None
    app: object | None = None
    device_session: object | None = None
    store: object | None = None            # TraceStore
    db_path: Path | None = None
    appium_url: str = "http://127.0.0.1:4723"
    repo: object | None = None
    secrets: object | None = None

    def connect(self) -> None:  # 真机版由 cli.main 覆写
        """建立设备会话。默认 no-op（FakeDriver 模式）。"""


class SessionPipeline:
    """把 TestCase 变成新管线的真实执行。"""

    class PreflightError(Exception):
        """前置配置错误（8.4 exit 3）。"""

    def __init__(self, suites_root: Path | str | None, *,
                 store=None, deps: PipelineDeps | None = None):
        self.suites_root = Path(suites_root) if suites_root else None
        self.store = store
        self.deps = deps

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
                 tc: TestCase, *, run_id: str) -> TestcaseResult:
        """一条 TestCase：start_testcase → 逐 step → end_testcase。

        wait/assertion 步骤不在 StepRunner 的 find/perform 管线里（7.1 只管
        动作步骤），走 executor.wait/assertion 引擎；失败映射按 8.2。
        """
        from executor.assertion import AssertionEngine
        from executor.wait import WaitEngine

        t0 = time.time()
        tc_run_id = lifecycle.begin_testcase(run_id, tc.id) \
            if self.store is not None else 0

        step_runner = runner
        wait_engine = WaitEngine(
            runner.ex, self._locator_for(runner)) \
            if runner is not None else None
        assertion_engine = AssertionEngine(
            runner.ex, self._locator_for(runner)) \
            if runner is not None else None

        status = "PASS"
        failure_type = None
        failure_phase = None
        detail: dict = {}

        for idx, step in enumerate(tc.steps):
            try:
                if isinstance(step, ActionStep):
                    outcome = self._run_action_step(step_runner, step, idx)
                    if not outcome.ok:
                        status = "FAIL"
                        failure_type = outcome.failure_type
                        failure_phase = (outcome.phase.value
                                         if outcome.phase else None)
                        break
                    lifecycle.record_step(outcome, step_index=idx)
                elif isinstance(step, WaitStep):
                    wait_engine.wait_for(step.wait_for)
                elif isinstance(step, AssertionStep):
                    result = assertion_engine.check(step.assertion)
                    if not result.passed:
                        status = "FAIL"
                        failure_type = "ASSERTION_VALUE_MISMATCH"
                        detail["kind"] = "assertion_target"
                        break
            except Exception as e:  # noqa: BLE001 — 统一映射见 _map_exception
                status, failure_type = _map_exception(e)
                failure_phase = getattr(e, "phase", None)
                detail["error"] = f"{type(e).__name__}: {e}"
                break

        duration_ms = int((time.time() - t0) * 1000)
        result = TestcaseResult(
            testcase_id=tc.id, status=status, failure_type=failure_type,
            failure_phase=failure_phase, duration_ms=duration_ms,
            detail=detail)
        if self.store is not None:
            lifecycle.end_testcase(status, failure_type=failure_type)
        return result

    def _run_action_step(self, runner: StepRunner, step: ActionStep,
                         idx: int) -> StepOutcome:
        from executor.policy import effective_idempotency, effective_risk
        from testcase.schema import Idempotency

        ref = step.target
        element_id = ref.id if ref is not None else ""
        # screen 引用不进 find/perform 管线（它是 wait/assert 的目标）
        strategies: tuple = tuple()
        if ref is not None and getattr(ref, "type", "element") == "element":
            if self.deps and self.deps.repo is not None:
                eff = self.deps.repo.resolve(ref, build="local")
                strategies = tuple((s.type, s.value) for s in eff.strategies)
                meta_risk = eff.risk
                meta_idem = eff.idempotency
                screen_id = eff.screen
            else:
                strategies = (("accessibility_id", element_id),)
                meta_risk = None
                meta_idem = None
                screen_id = ""
        else:
            meta_risk = None
            meta_idem = None
            screen_id = ""

        # 7.4 推导：step 声明 > element 元数据 > 启发式（纯函数，单测已钉）
        idem = step.idempotency or meta_idem
        risk = step.risk or meta_risk
        ctx = RunStepContext(
            element_id=element_id, screen_id=screen_id,
            strategies=strategies, action=step.action, value=step.value,
            risk=risk, idempotency=idem,
            has_postcondition=step.postcondition is not None,
            step_index=idx)
        return runner.run_step(ctx)

    def _locator_for(self, runner: StepRunner):
        def locate(target_ref):
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

    # --- 整个 run ---

    def run_all(self, cases: list[TestCase], *, run_id: str,
                failure_policy: str = "ABORT_SUITE",
                run_one=None) -> RunResult:
        """多条用例 → RunResult。store 存在时写 runs/testcase_runs。"""
        from runner.suite import FailurePolicy, SuiteRunner

        run = RunResult(run_id=run_id, suite=cases[0].suite if cases else None)
        if self.store is not None:
            self.store.start_run(run_id, suite=run.suite)
        try:
            for tc in cases:
                if run_one is not None:
                    result = run_one(tc)
                else:
                    result = self.run_case(
                        self._step_runner, self._lifecycle, tc, run_id=run_id)
                run.add(result)
                if (result.status == "ENVIRONMENT_FAILURE"
                        and failure_policy == "ABORT_SUITE"):
                    remaining = [c.id for c in cases[len(run.results):]]
                    run.results[-1].detail["aborted_suite"] = True
                    run.results[-1].detail["remaining"] = remaining
                    break
        finally:
            if self.store is not None:
                self.store.end_run(run_id, status=_run_status(run),
                                   exit_code=run.exit_code)
        return run

    # 真机版由 cli.main 注入；FakeDriver 测试直接调 run_case
    _step_runner: StepRunner | None = None
    _lifecycle: Lifecycle | None = None


def _run_status(run: RunResult) -> str:
    if run.passed:
        return "PASS"
    if any(r.status in ("INFRA_FAILURE", "ENVIRONMENT_FAILURE")
           for r in run.results):
        return "INFRA_FAILURE"
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
        # P3-4：8.2 枚举无此值，归最接近项（detail 保留 kind）
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
