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
from session.app_session import AppSession
from session.device_session import InfraError
from tracer.recorder import Recorder

MAX_ID = re.compile(r"^\$\{(\w+)\}$")

ARTIFACT_DIR = Path("out/artifacts")

# Recorder.record_step 的 locator 参数是 dict|None，Locator 是 list[dict]；
# record_step 内部 json.dumps 直接可序列化两者，类型层面放宽：
StepLocator = Locator | None


class TestFailure(Exception):
    """测试用例失败（与 InfraError 严格区分）。"""


class TestcaseRunner:
    def __init__(self, executor: Executor, app: AppSession,
                 recorder: Recorder, secrets: SecretProvider,
                 recovery_context: dict | None = None):
        self.ex = executor
        self.app = app
        self.rec = recorder
        self.secrets = secrets
        # review P0-1：{"metadata": ..., "budget": ..., "llm": ...}，可选
        self.recovery_ctx = recovery_context

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
        locator = self._locator_chain(step.target) if step.target else None
        status, error = "SUCCESS", None
        try:
            getattr(self, f"_do_{step.action}")(step)
        except Exception as e:
            status, error = "FAILED", f"{type(e).__name__}: {e}"
            # review P0-1：元素定位失败 → 自动 reconcile + recovery
            if (isinstance(e, (ElementNotFound, AmbiguousElement))
                    and step.target and self.recovery_ctx):
                rec_result = self._try_recovery(step, e)
                if rec_result and rec_result["status"] == "RECOVERED":
                    status, error = "RECOVERED", None
            raise
        finally:
            # value 字段不落 trace（密码等输入值，设计文档硬约束：写盘前脱敏）
            shot, tree = self._save_evidence(run_id, idx) if status != "SUCCESS" else (None, None)
            step_id = self.rec.record_step(run_id, idx, step.action, locator, status,
                                           error, int((time.time() - t0) * 1000),
                                           screenshot_path=shot, ui_tree_path=tree)
            if status == "RECOVERED" and self.recovery_ctx and step_id:
                # review P0-2：recoveries.step_id 回填真实 steps.id
                self.rec.conn.execute(
                    "UPDATE recoveries SET step_id=? WHERE id="
                    "(SELECT MAX(id) FROM recoveries)", (step_id,))
                self.rec.conn.commit()

    def _try_recovery(self, step, error):
        """P0-1：把 Stage 8 的 recover 挂进自动流程。失败不掩盖原异常。"""
        from agent.recovery import recover  # 局部导入避免循环依赖

        ctx = self.recovery_ctx
        try:
            return recover(step.target, error, self.ex, ctx["metadata"],
                           ctx["budget"], ctx["llm"], recorder=self.rec,
                           step_action=step.action, step_value=self._resolve(step.value))
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

    def _do_tap(self, step) -> None:
        self.ex.tap(self._locator_chain(step.target))

    def _do_input(self, step) -> None:
        self.ex.input(self._locator_chain(step.target),
                      self._resolve(step.value))

    def _locator_chain(self, target: str) -> Locator:
        """Phase 1 改造：id 失效时降级为 ObjC 常见可达锚点。

        真实 App 靠文案（label=中文标题）可达；name 属性同时承载
        accessibility id 与 label，故 predicate `name == X` 天然兜底两者。
        """
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
