"""Task 2.2（P1-05）runner 侧 assertion 接线测试。

口径：
  - `assertion` 步骤走 AssertionEngine（Task 1.6 的 exists-only 白名单已删）；
  - 值不符 → 步骤 FAIL，**不进 recovery**（H6）；
  - 目标漂移 + recovery_ctx → 进 recovery，RECOVERED 则步骤状态 RECOVERED；
  - 目标漂移无 recovery_ctx → 步骤 FAIL（TestFailure）。

测 `_run_step`（不是 `_do_assertion`）：步骤状态与 record_step 落库都在那层。
"""
from __future__ import annotations

import pytest

from executor.assertion import AssertionTargetDrift
from executor.executor import ElementNotFound
from executor.wait import WaitConfig
from repository.loader import load_element_dir, load_screen_dir
from repository.resolver import Repository
from runner.testcase_runner import TestcaseRunner, TestFailure
from testcase.schema import AssertionSpec, TargetRef

TestcaseRunner.__test__ = False  # type: ignore[attr-defined]
TestFailure.__test__ = False  # type: ignore[attr-defined]


class FakeElement:
    def __init__(self, text="退出登录"):
        self.text = text

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True


class FakeExecutor:
    """find 序列耗尽后重复最后一项。"""

    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.page_source_calls = 0
        self._last = None

    def find(self, locator):
        if self.sequence:
            self._last = self.sequence.pop(0)
        elif self._last is not None:
            pass
        else:
            raise ElementNotFound("exhausted")
        if isinstance(self._last, Exception):
            raise self._last
        return self._last

    def page_source(self):
        self.page_source_calls += 1
        return "<stable/>"

    def screenshot(self, path):
        pass


class FakeSecrets:
    def get(self, key):
        return "v"


class FakeRecorder:
    def __init__(self):
        self.steps: list[tuple] = []
        # RECOVERED 路径会做 recoveries.step_id 回填（UPDATE SQL），需要 conn
        self.conn = _FakeConn()

    def start_run(self, tc):
        return "r1"

    def record_step(self, run_id, idx, action, locator, status, error, ms, **kw):
        self.steps.append((action, status, error))
        return len(self.steps)

    def end_run(self, run_id, status):
        pass


class _FakeConn:
    def execute(self, *a, **k):
        return self

    def commit(self):
        pass


class _Step:
    def __init__(self, spec: AssertionSpec):
        self.assertion = spec
        self.target = spec.target


def _repo() -> Repository:
    gen_el = load_element_dir([("ProfileView.yaml", """\
kind: element
id: logout_button
screen: ProfileView
type: button
strategies:
  - {type: accessibility_id, value: logout_button, origin: manual}
""")])
    gen_sc = load_screen_dir([("ProfileView.yaml", """\
kind: screen
id: ProfileView
marker: screen.ProfileView
kind_hint: page
""")])
    return Repository(generated_elements=gen_el, generated_screens=gen_sc)


def _runner(ex, with_repo=True, **kw) -> TestcaseRunner:
    return TestcaseRunner(
        executor=ex, app=None, recorder=FakeRecorder(), secrets=FakeSecrets(),
        wait_config=WaitConfig(polling_interval=0.01, stable_polls=2,
                               stable_interval=0.01),
        repository=_repo() if with_repo else None,
        **kw,
    )


def _spec(condition="exists", expected=None, **kw) -> AssertionSpec:
    kw.setdefault("timeout", 0.05)
    return AssertionSpec(target=TargetRef(id="ProfileView.logout_button"),
                         condition=condition, expected=expected, **kw)


def test_text_contains_assertion_records_success():
    ex = FakeExecutor([FakeElement(text="已退出登录")])
    r = _runner(ex)
    r._run_step("r1", 0, _Step(_spec("text_contains", expected="退出")))
    assert r.rec.steps[-1][1] == "SUCCESS"


def test_value_mismatch_fails_step_without_recovery(monkeypatch):
    """H6：值不符直接 FAIL，recovery_ctx 存在也不许进恢复。"""

    def forbidden(*a, **k):
        raise AssertionError("AssertionValueMismatch must not trigger recovery")

    import agent.recovery as rec_mod
    monkeypatch.setattr(rec_mod, "recover", forbidden)
    ex = FakeExecutor([FakeElement(text="退出登录")])
    r = _runner(ex, recovery_context={"metadata": {}, "budget": None, "llm": None})
    with pytest.raises(TestFailure) as ei:
        r._run_step("r1", 0, _Step(_spec("text_equals", expected="登录中")))
    assert "ASSERTION_VALUE_MISMATCH" in str(ei.value)
    assert r.rec.steps[-1][1] == "FAILED"


def test_target_drift_without_recovery_ctx_becomes_step_failure():
    ex = FakeExecutor([ElementNotFound("drifted")])
    r = _runner(ex)
    with pytest.raises(TestFailure) as ei:
        r._run_step("r1", 0, _Step(_spec("exists")))
    assert "ASSERTION_TARGET_DRIFT" in str(ei.value)


def test_target_drift_with_recovery_ctx_recovers(monkeypatch):
    """漂移 + recovery_ctx → 进 recovery；stub 返回 RECOVERED。"""

    def fake_recover(target, error, *a, **k):
        return {"status": "RECOVERED", "rec_row_id": 1}

    import agent.recovery as rec_mod
    monkeypatch.setattr(rec_mod, "recover", fake_recover)
    ex = FakeExecutor([ElementNotFound("drifted")])
    r = _runner(ex, recovery_context={"metadata": {}, "budget": None, "llm": None})
    r._last_rec_row = None
    # _run_step 里 record_step 返回 1 → _last_rec_row 回填逻辑不炸
    r._run_step("r1", 0, _Step(_spec("exists")))
    assert r.rec.steps[-1][1] == "RECOVERED"


def test_drift_recovery_failure_still_fails_with_drift_error(monkeypatch):
    """恢复失败 → 原漂移异常继续往上（不被吞成含糊失败）。"""

    def fail_recover(target, error, *a, **k):
        return {"status": "LLM_TARGET_NOT_FOUND"}

    import agent.recovery as rec_mod
    monkeypatch.setattr(rec_mod, "recover", fail_recover)
    ex = FakeExecutor([ElementNotFound("drifted")])
    r = _runner(ex, recovery_context={"metadata": {}, "budget": None, "llm": None})
    with pytest.raises(AssertionTargetDrift) as ei:
        r._run_step("r1", 0, _Step(_spec("exists")))
    assert "ASSERTION_TARGET_DRIFT" in str(ei.value)
