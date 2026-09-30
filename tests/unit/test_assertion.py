"""Task 2.2（P1-05）Assertion Engine 单测（设计 7.3 Assertion 段）。

口径：
  - 7 条件矩阵：exists / not_exists / text_equals / text_contains /
    element_count / enabled / disabled；
  - **值断言失败**（目标在、值不符）→ `AssertionValueMismatch`，直接 FAIL
    无 Recovery（H6）——不轮询到 timeout（终态判定，不是等待）；
  - **目标定位失败**（ElementNotFound / AmbiguousElement，且非 not_exists）→
    `AssertionTargetDrift` 标记，供 Recovery 分流（允许进 Recovery，第 9 节规则）；
  - `not_exists` 目标找不到 = passed（不涉及漂移）；
  - 轮询只用于「目标还没出现」的过渡态，上限 `timeout`；值不符一旦目标可定位即判定。
"""
from __future__ import annotations

import pytest

from executor.assertion import (
    AssertionEngine,
    AssertionTargetDrift,
    AssertionValueMismatch,
    AssertionResult,
)
from executor.executor import AmbiguousElement, ElementNotFound
from testcase.schema import AssertionSpec, TargetRef

AssertionEngine.__test__ = False  # type: ignore[attr-defined]
AssertionValueMismatch.__test__ = False  # type: ignore[attr-defined]
AssertionTargetDrift.__test__ = False  # type: ignore[attr-defined]


class FakeElement:
    def __init__(self, text="", displayed=True, enabled=True):
        self.text = text
        self._displayed = displayed
        self._enabled = enabled

    def is_displayed(self):
        return self._displayed

    def is_enabled(self):
        return self._enabled


class FakeExecutor:
    """可控 find 结果序列；耗尽后重复最后一项（轮询场景）。"""

    def __init__(self, sequence, repeat_last=False):
        self.sequence = list(sequence)
        self.repeat_last = repeat_last
        self.calls: list[str] = []
        self._last = None

    def find(self, locator):
        self.calls.append(locator[0]["value"])
        if self.sequence:
            self._last = self.sequence.pop(0)
        elif self.repeat_last and self._last is not None:
            pass
        else:
            raise ElementNotFound("exhausted")
        if isinstance(self._last, Exception):
            raise self._last
        return self._last


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def _engine(ex, clock=None) -> AssertionEngine:
    clock = clock or FakeClock()
    return AssertionEngine(
        ex,  # type: ignore[arg-type]
        locator_for=lambda target: [{"type": "accessibility_id", "value": target.id}],
        clock=clock,
        sleep=clock.sleep,
    )


def _spec(condition, expected=None, target=None, **kw) -> AssertionSpec:
    kw.setdefault("timeout", 1.0)
    return AssertionSpec(
        target=target or TargetRef(id="logout_button"),
        condition=condition, expected=expected, **kw)


# --- exists / not_exists ---

def test_exists_passes_when_found():
    ex = FakeExecutor([FakeElement()])
    r = _engine(ex).check(_spec("exists"))
    assert r.passed and r.actual == "found"


def test_not_exists_passes_when_absent_immediately():
    """7.3：not_exists 目标找不到即成功，不涉及漂移。"""
    clock = FakeClock()
    ex = FakeExecutor([ElementNotFound("gone")])
    r = _engine(ex, clock).check(_spec("not_exists"))
    assert r.passed
    assert clock.sleeps == []  # 不轮询等满 timeout


def test_not_exists_fails_as_value_mismatch_when_present():
    """not_exists 目标**在** = 值不符（断言终态失败），不是漂移。"""
    ex = FakeExecutor([FakeElement()], repeat_last=True)
    with pytest.raises(AssertionValueMismatch) as ei:
        _engine(ex).check(_spec("not_exists"))
    r = ei.value.result
    assert not r.passed and r.expected is False and r.actual == "present"


# --- 值断言：目标可定位后立即判定，不轮询 ---

@pytest.mark.parametrize("condition,expected,text", [
    ("text_equals", "退出登录", "退出登录"),
    ("text_contains", "退出", "退出登录"),
])
def test_text_assertions_pass(condition, expected, text):
    ex = FakeExecutor([FakeElement(text=text)])
    assert _engine(ex).check(_spec(condition, expected)).passed


@pytest.mark.parametrize("condition,expected,text", [
    ("text_equals", "登录", "退出登录"),
    ("text_contains", "首页", "退出登录"),
])
def test_text_assertions_fail_fast_as_value_mismatch(condition, expected, text):
    """H6：值不符直接 FAIL 无 Recovery——不轮询等文本变成期望值。"""
    clock = FakeClock()
    ex = FakeExecutor([FakeElement(text=text)], repeat_last=True)
    with pytest.raises(AssertionValueMismatch) as ei:
        _engine(ex, clock).check(_spec(condition, expected, timeout=5.0))
    r = ei.value.result
    assert not r.passed
    assert r.expected == expected and r.actual == text
    assert r.target == "logout_button"
    assert clock.sleeps == []  # 终态判定，不等 timeout


def test_enabled_disabled_pass():
    ex = FakeExecutor([FakeElement(enabled=True)])
    assert _engine(ex).check(_spec("enabled")).passed
    ex2 = FakeExecutor([FakeElement(enabled=False)])
    assert _engine(ex2).check(_spec("disabled")).passed


@pytest.mark.parametrize("condition,enabled", [("enabled", False), ("disabled", True)])
def test_enabled_disabled_mismatch_fails_fast(condition, enabled):
    clock = FakeClock()
    ex = FakeExecutor([FakeElement(enabled=enabled)], repeat_last=True)
    with pytest.raises(AssertionValueMismatch):
        _engine(ex, clock).check(_spec(condition, timeout=5.0))
    assert clock.sleeps == []


# --- element_count ---

def test_element_count_passes_on_exact_match():
    ex = FakeExecutor([FakeElement()], repeat_last=True)
    assert _engine(ex).check(_spec("element_count", 1)).passed


def test_element_count_mismatch_fails_fast():
    """executor.find 0/1/2+ → 无法数出 2 个；≥2 命中 AmbiguousElement，
    对 element_count 而言就是「数量与期望不符」。"""
    ex = FakeExecutor([AmbiguousElement("2 matches")], repeat_last=True)
    with pytest.raises(AssertionValueMismatch) as ei:
        _engine(ex).check(_spec("element_count", 2))
    assert ei.value.result.actual == ">=2"
    assert ei.value.result.expected == 2


def test_element_count_nonzero_missing_is_mismatch_not_drift():
    """element_count 期望非 0：目标找不到是**值**不符（数量确实是 0），不是漂移。"""
    ex = FakeExecutor([ElementNotFound("none")], repeat_last=True)
    with pytest.raises(AssertionValueMismatch) as ei:
        _engine(ex).check(_spec("element_count", 1))
    assert ei.value.result.actual == 0
    assert ei.value.result.expected == 1


# --- R12-2：expected=0 是合法断言（「元素已从列表消失」）---

def test_element_count_zero_absent_passes():
    """expected=0、目标找不到 → actual=0 = expected → 必须 PASS，不是 mismatch。"""
    clock = FakeClock()
    ex = FakeExecutor([ElementNotFound("none")])
    r = _engine(ex, clock).check(_spec("element_count", 0))
    assert r.passed
    assert (r.expected, r.actual) == (0, 0)


def test_element_count_zero_present_is_mismatch():
    """expected=0、目标在 → 值不符（actual=1 ≠ 0）。"""
    ex = FakeExecutor([FakeElement()], repeat_last=True)
    with pytest.raises(AssertionValueMismatch) as ei:
        _engine(ex).check(_spec("element_count", 0))
    assert ei.value.result.actual == 1


def test_element_count_zero_ambiguous_is_mismatch():
    """expected=0、≥2 命中 → ">=2" 必然不符 → 值判定（不是漂移）。"""
    ex = FakeExecutor([AmbiguousElement("2")], repeat_last=True)
    with pytest.raises(AssertionValueMismatch) as ei:
        _engine(ex).check(_spec("element_count", 0))
    assert ei.value.result.actual == ">=2"


# --- R12-1：属性读取 stale 防护（R11-3 同源洞） ---

class StaleTextElement:
    """转场期典型：find 成功但读属性抛驱动异常。不继承 FakeElement（.text 属性冲突）。"""

    @property
    def text(self):
        raise RuntimeError("stale element reference: element is not attached")

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True


def test_stale_attr_read_retries_then_succeeds():
    clock = FakeClock()
    ex = FakeExecutor([StaleTextElement(), FakeElement(text="退出登录")])
    r = _engine(ex, clock).check(_spec("text_contains", "退出", timeout=5.0))
    assert r.passed
    assert len(clock.sleeps) == 1


def test_stale_attr_read_until_timeout_marks_drift_not_bubble():
    """stale 持续到 deadline → TargetDrift（可恢复），不是裸驱动异常冒泡。"""
    clock = FakeClock()
    ex = FakeExecutor([StaleTextElement()], repeat_last=True)
    with pytest.raises(AssertionTargetDrift) as ei:
        _engine(ex, clock).check(_spec("text_contains", "退出", timeout=0.5))
    assert ei.value.result.actual == "NOT_RESOLVED"


def test_stale_read_does_not_mask_internal_errors():
    """引擎自身的编程错误（ValueError）不许被 stale 防护吞掉。"""
    clock = FakeClock()
    ex = FakeExecutor([FakeElement()], repeat_last=True)
    bad = AssertionSpec(target=TargetRef(id="x"), condition="exists", timeout=1.0)
    object.__setattr__(bad, "condition", "sparkles")
    with pytest.raises(ValueError):
        _engine(ex, clock).check(bad)


# --- 目标定位失败 → 漂移 ---

@pytest.mark.parametrize("condition", ["exists", "text_equals", "enabled"])
def test_target_not_found_marks_drift(condition):
    ex = FakeExecutor([ElementNotFound("nope")], repeat_last=True)
    with pytest.raises(AssertionTargetDrift) as ei:
        _engine(ex).check(_spec(condition, expected="x"))
    assert ei.value.result.passed is False


def test_ambiguous_target_also_marks_drift():
    """≥2 命中 = 无法唯一定位目标 = 目标定义失效，与 ElementNotFound 同类。"""
    ex = FakeExecutor([AmbiguousElement("2")], repeat_last=True)
    with pytest.raises(AssertionTargetDrift):
        _engine(ex).check(_spec("exists"))


def test_drift_polls_until_timeout_before_marking():
    """漂移判定要给「晚到元素」机会：轮询到 timeout 才标漂移，不是首轮就放弃。"""
    clock = FakeClock()
    ex = FakeExecutor([ElementNotFound("1"), ElementNotFound("2"), FakeElement()])
    assert _engine(ex, clock).check(_spec("exists", timeout=5.0)).passed
    assert len(clock.sleeps) == 2


# --- 结果结构 ---

def test_result_carries_expected_actual_target():
    ex = FakeExecutor([FakeElement(text="A")], repeat_last=True)
    try:
        _engine(ex).check(_spec("text_equals", "B"))
        raise AssertionError("should raise")
    except AssertionValueMismatch as e:
        r: AssertionResult = e.result
        assert (r.expected, r.actual, r.target, r.passed) == ("B", "A", "logout_button", False)
