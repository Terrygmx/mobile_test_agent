"""TestcaseRunner — 执行 YAML 用例并记录 trace（Stage 5）。

ponytail: wait/assert 用轮询 find 实现；变量解析就在执行前一行正则。

review 修复：P0-1 ElementNotFound 时自动 reconcile+recover（steps.status=RECOVERED
真正出现在自动流程中）；P1-1 失败步骤截屏+存 page_source；P1-4 isinstance 判断
InfraError；P1-5 trace 记录真实执行的完整 locator 链；P2-2 删除幽灵文档引用。
Recovery 相关依赖（metadata/budget/llm）为可选参数，不传则不启用自动恢复。
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from environment.reset import ResetExecutor
from environment.secrets import SecretProvider
from executor.assertion import AssertionEngine, AssertionTargetDrift, AssertionValueMismatch
from executor.executor import AmbiguousElement, ElementNotFound, Executor, Locator
from executor.wait import WaitConfig, WaitEngine, WaitTimeout
from repository.resolver import (
    AmbiguousReferenceError,
    Repository,
    UnknownReferenceError,
)
from session.app_session import AppSession
from session.device_session import InfraError
from tracer.recorder import Recorder

MAX_ID = re.compile(r"^\$\{(\w+)\}$")

ARTIFACT_DIR = Path("out/artifacts")


def _str_target(text: str):
    """P0 裸字符串 → TargetRef（4.1 语法：screen: 前缀 / 短名）。"""
    from testcase.schema import TargetRef

    if text.startswith("screen:"):
        return TargetRef(type="screen", id=text.removeprefix("screen:"))
    return TargetRef(type="element", id=text)

# Recorder.record_step 的 locator 参数是 dict|None，Locator 是 list[dict]；
# record_step 内部 json.dumps 直接可序列化两者，类型层面放宽：
StepLocator = Locator | None


class TestFailure(Exception):
    """测试用例失败（与 InfraError 严格区分）。"""


class TestcaseRunner:
    def __init__(self, executor: Executor, app: AppSession,
                 recorder: Recorder, secrets: SecretProvider,
                 recovery_context: dict | None = None,
                 repository: "Repository | None" = None,
                 wait_config: WaitConfig | None = None,
                 device_type: str = "simulator"):
        self.ex = executor
        self.app = app
        self.rec = recorder
        self.secrets = secrets
        # review P0-1：{"metadata": ..., "budget": ..., "llm": ...}，可选
        self.recovery_ctx = recovery_context
        # Task 1.6（P1-04）：传入 Repository 时 target 走 4.1 语义解析
        # （TargetRef → resolve → strategies），不传回落 P0 硬编码链。
        self.repo = repository
        # Task 2.1：等待参数来自配置（`mta.yaml` wait: 段），不在代码里写死
        self.wait_config = wait_config or WaitConfig()
        # R13-1：reset 执行路径切到 ResetExecutor（不再直调 P0
        # app.reset_state）。原因：lint 11.2 预检按 11.1 新矩阵放行
        # RESET_STATE/LOGOUT，而 P0 SUPPORTED_STRATEGIES 不含这两者——
        # 不接线就是「lint 放行、运行时炸」，正好击穿 11.2 承诺。两条路径
        # 并存期间 lint 与执行可能分叉，禁止绕过 ResetExecutor 直调。
        self._reset_executor = ResetExecutor(app, device_type=device_type)
        self._device_type = device_type
        self._wait_engine_cached: WaitEngine | None = None
        self._assertion_engine_cached: AssertionEngine | None = None
        # R11-2：settle 放行记录（每 run 重置），供 trace / 事后区分「放行过」
        self.settle_timeouts: list[str] = []
        # review R2-3：recovery 行 id（steps 落库后回填 step_id 用）
        self._last_rec_row: int | None = None

    def _resolve(self, value: str | None) -> str | None:
        """'${VAR}' → secret。只在完整匹配时解析，防止部分替换泄漏。"""
        if value and (m := MAX_ID.match(value)):
            return self.secrets.get(m.group(1))
        return value

    def run(self, tc) -> str:
        run_id = self.rec.start_run(tc.id)
        self.settle_timeouts.clear()  # R11-2：放行记录按 run 隔离
        try:
            reset = tc.precondition.get("reset")
            if reset:
                # R13-1：走 ResetExecutor（11.1 矩阵 + A4 hook 路径），不再
                # 直调 P0 app.reset_state。app_path 仅 REINSTALL 需要。
                self._reset_executor.reset(
                    reset, app_path=tc.precondition.get("app_path"))
            for i, step in enumerate(tc.steps):
                self._run_step(run_id, i, step)
            self.rec.end_run(run_id, "PASS")
            return "PASS"
        except Exception as e:
            # review P1-4：isinstance 而非字符串比较，异常包装后仍正确
            status = "INFRA_FAILURE" if isinstance(e, InfraError) else "FAIL"
            self.rec.end_run(run_id, status)
            raise

    def _run_step(self, run_id: str, idx: int, step) -> None:
        t0 = time.time()
        # Task 1.6：新 schema 步骤形态分派（6.3：wait_for / assertion / action）
        if hasattr(step, "wait_for"):
            action = "wait_for"
        elif hasattr(step, "assertion"):
            action = "assertion"
        else:
            action = step.action
        try:
            locator = (self._locator_chain(step.target)
                       if getattr(step, "target", None) else None)
        except (UnknownReferenceError, AmbiguousReferenceError):
            # 4.1：引用错误是**用例缺陷**（lint 应已拦截），运行期直接 FAIL
            # 该步并终止用例，不做 recovery（recovery 修的是定位失效，不是坏引用）。
            self.rec.record_step(run_id, idx, action, None, "FAILED",
                                 f"unresolvable target (4.1): {step.target}",
                                 0)
            raise TestFailure(f"step {idx}: unresolvable target {step.target}") from None
        status, error = "SUCCESS", None
        try:
            getattr(self, f"_do_{action}")(step)
        except Exception as e:
            status, error = "FAILED", f"{type(e).__name__}: {e}"
            # Recovery 分流（7.3 / 第 9 节）：
            #   - 定位失败（ElementNotFound / AmbiguousElement）→ 可恢复；
            #   - 断言目标漂移（AssertionTargetDrift）→ 可恢复（detail.kind=assertion_target）；
            #   - 断言值不符（AssertionValueMismatch）→ H6：直接 FAIL，无 Recovery；
            #   - 等待超时（WaitTimeout）→ 默认不恢复（recovery.on_wait_timeout 才放行）。
            recoverable = isinstance(
                e, (ElementNotFound, AmbiguousElement, AssertionTargetDrift))
            if (recoverable and step.target and self.recovery_ctx):
                rec_result = self._try_recovery(step, e)
                if rec_result and rec_result["status"] == "RECOVERED":
                    status, error = "RECOVERED", None
                else:
                    raise
            else:
                raise
        finally:
            # R11-2：settle 放行记进该步 error 字段（SUCCESS 步 error 原本为 None）。
            # 目的：事后能区分「树真静止了」和「到上限放行的」。trace schema 0.1
            # 没有独立 warning 列，加列是 Task 2.4 迁移的事，这里不越界改 schema。
            if self.settle_timeouts:
                notice = "; ".join(self.settle_timeouts)
                self.settle_timeouts.clear()
                error = f"{error} | {notice}" if error else notice
            # value 字段不落 trace（密码等输入值，设计文档硬约束：写盘前脱敏）
            shot, tree = self._save_evidence(run_id, idx) if status != "SUCCESS" else (None, None)
            step_id = self.rec.record_step(run_id, idx, action, locator, status,
                                           error, int((time.time() - t0) * 1000),
                                           screenshot_path=shot, ui_tree_path=tree)
            # review R2-3：recover() 返回 rec_row_id（精确行），按行 id 回填
            # step_id，替代全局 MAX(id)——并发/多 run 下不会改错行
            if status == "RECOVERED" and self._last_rec_row:
                self.rec.conn.execute(
                    "UPDATE recoveries SET step_id=? WHERE id=?",
                    (step_id, self._last_rec_row))
                self.rec.conn.commit()
                self._last_rec_row = None

    def _try_recovery(self, step, error):
        """P0-1：把 Stage 8 的 recover 挂进自动流程。失败不掩盖原异常。

        review R2-3：recovery 行先落库（step_id=0），steps 行后落库，
        拿到精确行 id 后在此回填——不依赖全局 MAX(id)。
        Task 2.2：step 可能是 WaitStep/AssertionStep（无 .action 属性，坑 17），
        动作名用调用点已分派的形态推断，不能摸 step.action。
        """
        from agent.recovery import recover  # 局部导入避免循环依赖

        # 分派后动作名：action 步骤直接取；wait/assertion 步骤的「动作」
        # 对 recovery 的意义是「LLM 该做 tap 还是 input」——断言漂移恢复
        # 目前只在 tap 语义下有意义（重新定位目标元素），统一给 tap。
        action = getattr(step, "action", "tap")
        value = getattr(step, "value", None)
        ctx = self.recovery_ctx
        try:
            result = recover(step.target, error, self.ex, ctx["metadata"],
                             ctx["budget"], ctx["llm"], recorder=self.rec,
                             step_action=action, step_value=self._resolve(value))
            # review R2-3：暂存精确 recoveries 行 id，steps 落库后回填
            self._last_rec_row = result.get("rec_row_id")
            return result
        except Exception:
            return None

    def _save_evidence(self, run_id: str, idx: int) -> tuple[str | None, str | None]:
        """review P1-1：失败步骤落 UI 证据（截屏 + page_source 副本）。"""
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        base = ARTIFACT_DIR / f"{run_id}_step{idx}"
        shot = f"{base}.png"
        tree = f"{base}.xml"
        try:
            self.ex.screenshot(shot)
        except Exception:
            shot = None
        try:
            Path(tree).write_text(self.ex.page_source())
        except Exception:
            tree = None
        return shot, tree

    # --- actions ---
    def _do_launch_app(self, step) -> None:
        self.app.launch()

    def _do_terminate_app(self, step) -> None:
        self.app.terminate()

    def _do_back(self, step) -> None:
        self.ex.ds.ensure_alive().back()

    # Task 2.1：等待语义移交 executor/wait.py（设计 7.3）。此前的固定 settle 缓冲
    # 已删除——实测（Task 1.6 probe4/5）marker 可 find ≠ 手势层就绪，现在用「连续 N
    # 次 page_source 树哈希不变」的静止判定替代，见 WaitEngine.wait_for_settle。

    def _wait_engine(self) -> WaitEngine:
        if self._wait_engine_cached is None:
            self._wait_engine_cached = WaitEngine(
                self.ex, self._locator_chain, config=self.wait_config,
            )
        return self._wait_engine_cached

    def _assertion_engine(self) -> AssertionEngine:
        if self._assertion_engine_cached is None:
            self._assertion_engine_cached = AssertionEngine(
                self.ex, self._locator_chain,
            )
        return self._assertion_engine_cached

    def _do_wait_for(self, step) -> None:
        """Task 2.1：完整条件矩阵走 WaitEngine。

        7.3：超时抛 WaitTimeout（不是 ELEMENT_NOT_FOUND），且默认不触发 Recovery
        （`recovery.on_wait_timeout: false`）——多半是后端慢/网络/App bug/数据问题，
        让 LLM 去「修」只会放大成本。`on_wait_timeout=true` 时才放行走 recovery。
        """
        try:
            self._wait_engine().wait_for(step.wait_for)
        except WaitTimeout as e:
            if self.recovery_ctx and self.recovery_ctx.get("on_wait_timeout"):
                raise
            raise TestFailure(str(e)) from None
        # 静止判定：命中条件后等 UI 树稳定，避免转场期间 tap 被 SwiftUI 吞掉。
        # screen target 默认走 7.3 廉价路径（明确不拉 page_source）→ 不做静止判定。
        # R11-1 实测该豁免会让 `active → tap` 丢 tap（3 轮 Gate 挂 2 轮），故给出
        # 开关 settle_on_screen_wait；M3 gesture-ready 信号落地后应能关掉它。
        spec = step.wait_for
        is_screen = spec.target.type == "screen"
        if not is_screen or self.wait_config.settle_on_screen_wait:
            if not self._wait_engine().wait_for_settle():
                self.settle_timeouts.append(
                    f"step settle: ui tree never stable within "
                    f"{self.wait_config.settle_timeout}s (proceeded anyway)"
                )

    def _do_assertion(self, step) -> None:
        """Task 2.2：断言走 AssertionEngine（设计 7.3）。

        三态分派在 _run_step 的 recovery 分流里做：
          - passed → 返回；
          - AssertionValueMismatch → H6 直接 FAIL（recovery 白名单外，自然不进恢复）；
          - AssertionTargetDrift → 进 Recovery，恢复失败仍以漂移异常 FAIL。
        """
        try:
            self._assertion_engine().check(step.assertion)
        except AssertionTargetDrift as e:
            # 漂移必须保持原类型抛出——_run_step 的 recovery 分流靠 isinstance
            # 识别它；无 recovery_ctx 时漂移没有出路，转 TestFailure。
            if not self.recovery_ctx:
                raise TestFailure(str(e)) from None
            raise
        except AssertionValueMismatch as e:
            # H6：值不符直接 FAIL。与 WaitTimeout 同款收口成 TestFailure，
            # 保持 runner 对外契约（TestFailure=测试失败 / InfraError=基础设施）
            raise TestFailure(str(e)) from None

    def _do_tap(self, step) -> None:
        self.ex.tap(self._locator_chain(step.target))

    def _do_input(self, step) -> None:
        self.ex.input(self._locator_chain(step.target),
                      self._resolve(step.value))

    def _locator_chain(self, target) -> Locator:
        """Task 1.6：有 Repository 时按 4.1 语义解析（TargetRef/短名/限定名/
        screen: 语法）→ EffectiveElement.strategies 顺序即尝试顺序；
        无 Repository 回落 P0 硬编码链（id 失效降级 ObjC 锚点）。

        screen target（wait_for active）返回 marker accessibility_id 单策略。
        """
        if self.repo is not None:
            ref = target if not isinstance(target, str) else _str_target(target)
            eff = self.repo.resolve(ref, build="local")
            if hasattr(eff, "marker"):  # EffectiveScreen
                return [{"type": "accessibility_id", "value": eff.marker}]
            return [
                {"type": s.type, "value": s.value}
                for s in eff.strategies
                if s.type in ("accessibility_id", "predicate", "class_chain")
            ]
        # P0 路径：target 是裸字符串
        return [
            {"type": "accessibility_id", "value": target},
            {"type": "predicate", "value": f"name == '{target}'"},
        ]

    def _wait_exists(self, target: str, timeout: int):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                return self.ex.find(self._locator_chain(target))
            except ElementNotFound:
                time.sleep(0.5)
        raise TestFailure(f"timeout waiting for {target}")

    def _do_wait_exists(self, step) -> None:
        self._wait_exists(step.target, step.timeout or 10)

    def _do_assert_exists(self, step) -> None:
        self._wait_exists(step.target, step.timeout or 5)
