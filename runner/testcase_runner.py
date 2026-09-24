"""TestcaseRunner — 执行 YAML 用例并记录 trace（Stage 5）。

ponytail: wait/assert 用轮询 find 实现；变量解析就在执行前一行正则。
"""

from __future__ import annotations

import re
import time

from environment.secrets import SecretProvider
from executor.executor import ElementNotFound, Executor
from session.app_session import AppSession
from tracer.recorder import Recorder

MAX_ID = re.compile(r"^\$\{(\w+)\}$")


class TestFailure(Exception):
    """测试用例失败（与 InfraError 严格区分）。"""


class TestcaseRunner:
    def __init__(self, executor: Executor, app: AppSession,
                 recorder: Recorder, secrets: SecretProvider):
        self.ex = executor
        self.app = app
        self.rec = recorder
        self.secrets = secrets

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
            status = "INFRA_FAILURE" if type(e).__name__ == "InfraError" else "FAIL"
            self.rec.end_run(run_id, status)
            raise

    def _run_step(self, run_id: str, idx: int, step) -> None:
        t0 = time.time()
        locator = [{"type": "accessibility_id", "value": step.target}] if step.target else None
        status, error = "SUCCESS", None
        try:
            getattr(self, f"_do_{step.action}")(step)
        except Exception as e:
            status, error = "FAILED", f"{type(e).__name__}: {e}"
            raise
        finally:
            self.rec.record_step(run_id, idx, step.action, locator, status, error,
                                 int((time.time() - t0) * 1000))

    # --- actions ---
    def _do_launch_app(self, step) -> None:
        self.app.launch()

    def _do_tap(self, step) -> None:
        self.ex.tap([{"type": "accessibility_id", "value": step.target}])

    def _do_input(self, step) -> None:
        self.ex.input([{"type": "accessibility_id", "value": step.target}],
                      self._resolve(step.value))

    def _wait_exists(self, target: str, timeout: int):
        deadline = time.time() + timeout
        loc = [{"type": "accessibility_id", "value": target}]
        while time.time() < deadline:
            try:
                return self.ex.find(loc)
            except ElementNotFound:
                time.sleep(0.5)
        raise TestFailure(f"timeout waiting for {target}")

    def _do_wait_exists(self, step) -> None:
        self._wait_exists(step.target, step.timeout or 10)

    def _do_assert_exists(self, step) -> None:
        self._wait_exists(step.target, step.timeout or 5)
