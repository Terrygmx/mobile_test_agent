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
from runner.runner import RunStepContext, StepOutcome, StepRunner
from testcase.schema import ActionStep, AssertionStep, TestCase, WaitStep

__all__ = ["SessionPipeline", "PipelineDeps"]


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
                 tc: TestCase, *, run_id: str,
                 manage_env: bool = True) -> TestcaseResult:
        """一条 TestCase：prepare → 逐 step → end → cleanup（H10）。

        `manage_env=False` 供 SuiteRunner 驱动（run_all）使用——套件层已经
        负责 prepare/cleanup，这里再跑一遍就是双重复位。

        wait/assertion 步骤不在 StepRunner 的 find/perform 管线里（7.1 只管
        动作步骤），走 executor.wait/assertion 引擎；失败映射按 8.2。
        """
        from executor.assertion import AssertionEngine
        from executor.wait import WaitEngine

        t0 = time.time()
        if self.store is not None:
            lifecycle.begin_testcase(run_id, tc.id)

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
                        outcome = self._run_action_step(runner, step, idx)
                        # 失败步骤也落 steps 表——只记成功会让 trace
                        # 「看起来跑到一半就没了」，排障无从下手
                        lifecycle.record_step(outcome, step_index=idx)
                        if not outcome.ok:
                            status = "FAIL"
                            failure_type = outcome.failure_type
                            failure_phase = (outcome.phase.value
                                             if outcome.phase else None)
                            detail = dict(outcome.detail)
                            detail.setdefault("error", outcome.error)
                            break
                    elif isinstance(step, WaitStep):
                        t1 = time.time()
                        wait_engine.wait_for(step.wait_for)
                        self._record_aux_step(
                            lifecycle, idx, "wait_for",
                            _target_label(step.wait_for.target),
                            latency_ms=int((time.time() - t1) * 1000))
                    elif isinstance(step, AssertionStep):
                        t1 = time.time()
                        result = assertion_engine.check(step.assertion)
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

        result = TestcaseResult(
            testcase_id=tc.id, status=status, failure_type=failure_type,
            failure_phase=failure_phase, cleanup_status=cleanup_status,
            duration_ms=int((time.time() - t0) * 1000), detail=detail)
        if self.store is not None:
            lifecycle.end_testcase(
                status, failure_type=failure_type,
                cleanup_status=cleanup_status)
        return result

    def _run_action_step(self, runner: StepRunner, step: ActionStep,
                         idx: int) -> StepOutcome:
        # R16-1：7.4「取更严格」只能由 policy 纯函数裁决——step 声明与
        # element metadata 两个声明源都传进去，None 源自动跳过，一个不丢。
        # （原 `step.risk or meta_risk` 曾让 LOW 声明压掉 metadata HIGH。）
        from executor.policy import effective_idempotency, effective_risk

        ref = step.target
        element_id = ref.id if ref is not None else ""
        # screen 引用不进 find/perform 管线（它是 wait/assert 的目标）
        strategies: tuple = tuple()
        if ref is not None and getattr(ref, "type", "element") == "element":
            if self.deps and self.deps.repo is not None:
                eff = self.deps.repo.resolve(ref, build="local")
                strategies = tuple((s.type, s.value) for s in eff.strategies)
                meta_risk = getattr(eff, "risk", None)
                meta_idem = getattr(eff, "idempotency", None)
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

        risk = effective_risk(step=step.risk, element=meta_risk,
                              element_id=element_id)
        idem = effective_idempotency(step.idempotency, meta_idem,
                                     element_id=element_id)
        ctx = RunStepContext(
            element_id=element_id, screen_id=screen_id,
            strategies=strategies, action=step.action, value=step.value,
            risk=risk, idempotency=idem,
            has_postcondition=step.postcondition is not None,
            step_index=idx)
        return runner.run_step(ctx)

    def _record_aux_step(self, lifecycle: Lifecycle, idx: int, step_type: str,
                         target_id: str, *, ok: bool = True,
                         failure_type: str | None = None,
                         latency_ms: int = 0, detail: dict | None = None
                         ) -> None:
        """wait/assert 步骤也落 steps 表（P3-1：TraceStore 全步可见）。"""
        if self.store is None:
            return
        lifecycle.record_step(StepOutcome(
            ok=ok, step_index=idx, action=step_type, element_id=target_id,
            failure_type=failure_type, latency_ms=latency_ms,
            detail=detail or {}), step_index=idx)

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

    def _prepare(self, tc: TestCase) -> None:
        if self.deps is not None and self.deps.env is not None:
            self.deps.env.prepare(tc)

    def _cleanup(self, tc: TestCase) -> None:
        if self.deps is not None and self.deps.env is not None:
            self.deps.env.cleanup(tc)

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
                failure_policy=policy)
            try:
                suite_run = suite_runner.run_suite(cases)
                run.results.extend(suite_run.results)
            except SuiteAborted:
                # H10 中止：已跑结果保留（run_suite 已标 aborted_suite/
                # remaining detail），异常不上冒——CLI 要的是退出码不是栈。
                pass
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


def _target_label(target) -> str:
    """TargetRef → steps.target_id 可读标签（screen:HomeView / LoginView.x）。"""
    tid = getattr(target, "id", "") or ""
    if getattr(target, "type", "element") == "screen":
        return f"screen:{tid}"
    return tid
