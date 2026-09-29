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

from environment.secrets import SecretProvider
from executor.executor import AmbiguousElement, ElementNotFound, Executor, Locator
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
                 repository: "Repository | None" = None):
        self.ex = executor
        self.app = app
        self.rec = recorder
        self.secrets = secrets
        # review P0-1：{"metadata": ..., "budget": ..., "llm": ...}，可选
        self.recovery_ctx = recovery_context
        # Task 1.6（P1-04）：传入 Repository 时 target 走 4.1 语义解析
        # （TargetRef → resolve → strategies），不传回落 P0 硬编码链。
        self.repo = repository
        # review R2-3：recovery 行 id（steps 落库后回填 step_id 用）
        self._last_rec_row: int | None = None

    def _resolve(self, value: str | None) -> str | None:
        """'${VAR}' → secret。只在完整匹配时解析，防止部分替换泄漏。"""
        if value and (m := MAX_ID.match(value)):
            return self.secrets.get(m.group(1))
        return value

    def run(self, tc) -> str:
        run_id = self.rec.start_run(tc.id)
        try:
            reset = tc.precondition.get("reset")
            if reset:
                self.app.reset_state(reset, app_path=tc.precondition.get("app_path"))
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
            # review P0-1/R2-1：元素定位失败 → 自动 reconcile + recovery；
            # 恢复成功则不 raise，继续执行后续步骤（用例最终 PASS，设计目标 7）
            if (isinstance(e, (ElementNotFound, AmbiguousElement))
                    and step.target and self.recovery_ctx):
                rec_result = self._try_recovery(step, e)
                if rec_result and rec_result["status"] == "RECOVERED":
                    status, error = "RECOVERED", None
                else:
                    raise
            else:
                raise
        finally:
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
        """
        from agent.recovery import recover  # 局部导入避免循环依赖

        ctx = self.recovery_ctx
        try:
            result = recover(step.target, error, self.ex, ctx["metadata"],
                             ctx["budget"], ctx["llm"], recorder=self.rec,
                             step_action=step.action, step_value=self._resolve(step.value))
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

    # Task 2.1（wait engine）替换点：目前用固定 settle 缓冲近似「树静止」。
    # 实测（Task 1.6 probe4/5）：marker 可 find ≠ 手势层就绪——成功后立即 tap
    # 会被 SwiftUI 吞掉（NavigationStack 转场期间）。wait engine 落地时改为
    # 连续 N 次 page_source 树哈希不变的「静止判定」，删掉这个 sleep。
    TAP_SETTLE_SECONDS = 1.0

    def _do_wait_for(self, step) -> None:
        """Task 1.6 最小 wait：轮询 find（wait engine 完整版在 Task 2.1）。

        R10-4：condition 白名单外一律 fail-loud，不静默降级为「元素存在」。
        """
        w = step.wait_for
        if w.condition not in ("active", "exists"):
            raise TestFailure(
                f"wait_for condition {w.condition!r} not supported until "
                f"Task 2.1 wait engine (only active/exists here)"
            )
        deadline = time.time() + w.timeout
        interval = w.polling_interval or 0.5
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                self.ex.find(self._locator_chain(w.target))
                time.sleep(self.TAP_SETTLE_SECONDS)  # 手势层就绪缓冲，见 TAP_SETTLE_SECONDS
                return
            except (ElementNotFound, AmbiguousElement) as e:
                last_err = e
                time.sleep(interval)
        raise TestFailure(
            f"wait_for {w.target.id!r} condition={w.condition!r} "
            f"timeout after {w.timeout}s: {last_err}"
        )

    def _do_assertion(self, step) -> None:
        """Task 1.6 最小 assertion：exists 轮询复用 wait 路径。
        其余条件（text_equals 等）Task 2.2 Assertion Engine 落地。"""
        a = step.assertion
        if a.condition not in ("exists",):
            raise TestFailure(
                f"assertion condition {a.condition!r} not supported until Task 2.2"
            )
        deadline = time.time() + a.timeout
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                self.ex.find(self._locator_chain(a.target))
                return
            except (ElementNotFound, AmbiguousElement) as e:
                last_err = e
                time.sleep(0.5)
        raise TestFailure(
            f"assertion {a.target.id!r} condition={a.condition!r} "
            f"timeout after {a.timeout}s: {last_err}"
        )

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
