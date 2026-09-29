"""Task 2.1（P1-05）runner 侧 wait 接线测试。

口径：
  - `wait_for` 走 WaitEngine（不再有 Task 1.6 的 active/exists 白名单拒绝）；
  - 超时 → TestFailure，且**不进** recovery（7.3 on_wait_timeout 默认 false）；
  - `on_wait_timeout: true` 时才把 WaitTimeout 往上抛（交 recovery 分流）；
  - screen target 不做树静止判定（7.3 廉价路径不拉 page_source），
    element target 命中后做静止判定（替代已删除的 TAP_SETTLE_SECONDS）。
"""
from __future__ import annotations

import pytest

from executor.executor import ElementNotFound
from executor.wait import WaitConfig, WaitTimeout
from repository.loader import load_element_dir, load_screen_dir
from repository.resolver import Repository
from runner.testcase_runner import TestcaseRunner, TestFailure
from testcase.schema import TargetRef, WaitSpec

TestcaseRunner.__test__ = False  # type: ignore[attr-defined]
TestFailure.__test__ = False  # type: ignore[attr-defined]


class FakeElement:
    def __init__(self, text="搜索"):
        self.text = text

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True


class FakeExecutor:
    """`sequence` 消费完后重复最后一项（轮询场景需要持续同一结果）。"""

    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.page_source_calls = 0

    def find(self, locator):
        if self.sequence:
            nxt = self.sequence.pop(0)
            self._last = nxt
        else:
            nxt = self._last
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def page_source(self):
        self.page_source_calls += 1
        return "<stable/>"

    def screenshot(self, path):  # 失败步骤的证据采集会调
        pass


class FakeSecrets:
    def get(self, key):
        return "v"


class FakeRecorder:
    def __init__(self):
        self.steps: list[tuple] = []

    def start_run(self, tc):
        return "r1"

    def record_step(self, run_id, idx, action, locator, status, error, ms, **kw):
        self.steps.append((action, status, error))
        return 1

    def end_run(self, run_id, status):
        pass


class _Step:
    def __init__(self, spec: WaitSpec):
        self.wait_for = spec
        self.target = spec.target


def _runner(ex, **kw) -> TestcaseRunner:
    return TestcaseRunner(
        executor=ex, app=None, recorder=FakeRecorder(), secrets=FakeSecrets(),
        wait_config=WaitConfig(polling_interval=0.01, default_timeout=0.05,
                               stable_polls=2, stable_interval=0.01),
        **kw,
    )


def _spec(condition="exists", target=None, **kw) -> WaitSpec:
    kw.setdefault("timeout", 0.05)
    return WaitSpec(target=target or TargetRef(id="HomeView.go_search"),
                    condition=condition, **kw)


def test_wait_for_delegates_to_wait_engine_all_conditions():
    ex = FakeExecutor([FakeElement()])
    # Task 1.6 时期 text_contains 会被 fail-loud 拒绝；现在必须能正常走通
    _runner(ex)._do_wait_for(_Step(_spec("text_contains", expected="搜")))


def test_element_target_wait_triggers_settle():
    ex = FakeExecutor([FakeElement()])
    _runner(ex)._do_wait_for(_Step(_spec()))
    assert ex.page_source_calls == 2  # 首次 + 1 次稳定对比


def test_screen_active_skips_settle():
    ex = FakeExecutor([FakeElement()])
    r = _runner(ex)
    r.repo = _repo()
    r._do_wait_for(_Step(_spec("active", target=TargetRef(type="screen", id="HomeView"))))
    assert ex.page_source_calls == 0


def test_wait_timeout_becomes_test_failure_without_recovery():
    ex = FakeExecutor([ElementNotFound("nope")])
    with pytest.raises(TestFailure) as ei:
        _runner(ex)._do_wait_for(_Step(_spec()))
    assert "WAIT_TIMEOUT" in str(ei.value)


def test_wait_timeout_propagates_when_config_allows_recovery():
    ex = FakeExecutor([ElementNotFound("nope")])
    r = _runner(ex, recovery_context={"on_wait_timeout": True})
    with pytest.raises(WaitTimeout):
        r._do_wait_for(_Step(_spec()))


def _repo() -> Repository:
    gen_el = load_element_dir([("HomeView.yaml", """\
kind: element
id: go_search
screen: HomeView
type: button
strategies:
  - {type: accessibility_id, value: go_search, origin: manual}
""")])
    gen_sc = load_screen_dir([("HomeView.yaml", """\
kind: screen
id: HomeView
marker: screen.HomeView
kind_hint: page
""")])
    return Repository(generated_elements=gen_el, generated_screens=gen_sc)


# --- R11-2：settle 放行必须留痕（否则事后分不清「真静止」和「放行过」） ---

class UnstableExecutor(FakeExecutor):
    """树每次都变 → settle 到上限放行。"""

    def __init__(self, sequence):
        super().__init__(sequence)
        self.n = 0

    def page_source(self):
        self.page_source_calls += 1
        self.n += 1
        return f"<tree seq={self.n}/>"


def test_settle_timeout_is_recorded_on_step_error():
    ex = UnstableExecutor([FakeElement()])
    r = _runner(ex)
    r.wait_config = WaitConfig(polling_interval=0.01, default_timeout=0.05,
                               stable_polls=2, stable_interval=0.01,
                               settle_timeout=0.05)
    r._do_wait_for(_Step(_spec()))
    assert r.settle_timeouts, "放行必须被记录，否则和真静止无法区分"


def test_settle_stable_leaves_no_notice():
    ex = FakeExecutor([FakeElement()])  # page_source 恒定 → 真静止
    r = _runner(ex)
    r._do_wait_for(_Step(_spec()))
    assert r.settle_timeouts == []


def test_screen_wait_skips_settle_by_default():
    """7.3 廉价路径默认：active 不拉 page_source。"""
    ex = FakeExecutor([FakeElement()])
    r = _runner(ex)
    r.repo = _repo()
    r._do_wait_for(_Step(_spec("active", target=TargetRef(type="screen", id="HomeView"))))
    assert ex.page_source_calls == 0


def test_screen_wait_settles_when_config_enabled():
    """R11-1：3 轮实测默认豁免会丢 tap，开关打开后 active 也做静止判定。"""
    ex = FakeExecutor([FakeElement()])
    r = _runner(ex)
    r.repo = _repo()
    r.wait_config = WaitConfig(stable_polls=2, stable_interval=0.01,
                               settle_timeout=0.05, settle_on_screen_wait=True)
    r._do_wait_for(_Step(_spec("active", target=TargetRef(type="screen", id="HomeView"))))
    assert ex.page_source_calls == 2
