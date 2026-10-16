"""ActionStep.optional：条件性前置步（2026-10-16 乐速通真机接入）。

背景：乐速通 uat 冷启动后会弹「更新提示」（服务器 4.2.31 > 装机 4.2.30），
模态弹窗把首页内容全部置成 visible=false，不点掉任何首页断言都过不了。
但 App 追上版本后弹窗就不出现了——用普通 tap 会让用例在「没弹」时假红。
故加 optional：定位不到 = 条件不成立，跳过记 SUCCESS。

⚠️ 口径（本测试钉住）：optional **只豁免定位失败**。动作执行中途抛的别的错
（比如 tap 命中后元素又消失）照旧 FAIL——否则 optional 会变成「随便失败都能过」。
"""
from __future__ import annotations

import pytest

from executor.executor import ElementNotFound
from executor.wait import WaitConfig
from runner.testcase_runner import TestcaseRunner, TestFailure
from testcase.schema import ActionStep, TargetRef

TestcaseRunner.__test__ = False  # type: ignore[attr-defined]
TestFailure.__test__ = False  # type: ignore[attr-defined]


class FakeElement:
    def tap(self):
        pass


class FakeExecutor:
    """`error` 非 None 时 tap 直接抛；否则正常「点中」。"""

    def __init__(self, error=None):
        self.error = error
        self.tap_calls = 0

    def tap(self, locator):
        self.tap_calls += 1
        if self.error is not None:
            raise self.error
        return None

    def page_source(self):
        return "<stable/>"

    def screenshot(self, path):
        pass


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


def _runner(ex, **kw):
    return TestcaseRunner(
        executor=ex, app=None, recorder=FakeRecorder(), secrets=None,
        wait_config=WaitConfig(polling_interval=0.01, default_timeout=0.05,
                               stable_polls=2, stable_interval=0.01),
        **kw,
    )


def _tap_step(**kw):
    kw.setdefault("action", "tap")
    kw.setdefault("target", TargetRef(id="update_dialog_cancel"))
    return ActionStep(**kw)


def test_optional_tap_skipped_when_element_absent():
    """弹窗没弹 → find 抛 ElementNotFound → 跳过且记 SUCCESS，不抛。"""
    ex = FakeExecutor(error=ElementNotFound("no such element"))
    r = _runner(ex)
    r._run_step("r1", 1, _tap_step(optional=True))
    action, status, error = r.rec.steps[-1]
    assert status == "SUCCESS", f"optional 步定位不到必须记 SUCCESS：{status}"
    assert "skipped" in (error or ""), f"要能区分「真做了」和「跳过了」：{error!r}"
    assert ex.tap_calls == 1


def test_optional_tap_runs_when_element_present():
    """弹窗弹了 → 正常 tap，记 SUCCESS 且**不带** skipped 说明。"""
    ex = FakeExecutor()
    r = _runner(ex)
    r._run_step("r1", 1, _tap_step(optional=True))
    action, status, error = r.rec.steps[-1]
    assert status == "SUCCESS"
    assert error is None, f"真的执行了不该留 skipped 说明：{error!r}"


def test_non_optional_tap_still_fails_when_absent():
    """没标 optional → 保持原契约：定位不到照旧往上抛。"""
    ex = FakeExecutor(error=ElementNotFound("no such element"))
    r = _runner(ex)
    with pytest.raises(ElementNotFound):
        r._run_step("r1", 1, _tap_step())


def test_optional_does_not_swallow_non_locator_errors():
    """⚠️ optional 只豁免定位失败：find 之外的错照旧抛。

    否则 optional 会退化成「这一步随便失败都能过」——那比假红更坏，
    因为它把真故障静默吞掉。"""
    ex = FakeExecutor(error=RuntimeError("WDA 连接断了"))
    r = _runner(ex)
    with pytest.raises(RuntimeError):
        r._run_step("r1", 1, _tap_step(optional=True))


def test_optional_skips_ambiguity_too():
    """多个匹配（弹窗里 Button/StaticText 同名）也算「定位不成」→ 跳过。

    真机实测：`取消` 在树里有两个同名节点，用 accessibility_id 单策略会
    AmbiguousElement；改用 predicate 限定 type 才唯一。若有人把元素定义改回
    单策略，optional 会让这步静默跳过而不是红——所以用例里那步必须
    optional，且元素定义必须是 predicate（见 overrides 注释）。"""
    from executor.executor import AmbiguousElement

    ex = FakeExecutor(error=AmbiguousElement("2 matches"))
    r = _runner(ex)
    r._run_step("r1", 1, _tap_step(optional=True))
    assert r.rec.steps[-1][1] == "SUCCESS"
