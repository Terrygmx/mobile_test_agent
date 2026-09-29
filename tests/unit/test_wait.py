"""Task 2.1（P1-05）Wait Engine 单测（设计 7.3）。

口径：
  - 超时抛 `WaitTimeout`（failure_type=WAIT_TIMEOUT），**不是** ElementNotFound；
  - `polling_interval` 来自 WaitSpec / WaitConfig，测试里改值能被观测到 → 非写死；
  - `not_exists` 命中即返回（不等满 timeout）；
  - 条件矩阵 exists/not_exists/visible/enabled/disabled/text_equals/text_contains/active；
  - `active` 廉价路径：FakeDriver 记录调用，断言全程没碰 page_source；
  - 树静止判定：哈希不变才停，树还在变则继续等。
"""
from __future__ import annotations

import pytest

from executor.executor import AmbiguousElement, ElementNotFound
from executor.wait import (
    UnsupportedWaitCondition,
    WaitConfig,
    WaitEngine,
    WaitTimeout,
)
from testcase.schema import TargetRef, WaitSpec

WaitEngine.__test__ = False  # type: ignore[attr-defined]
WaitTimeout.__test__ = False  # type: ignore[attr-defined]


class FakeElement:
    """属性可在轮询中变化，模拟「元素晚出现 / 文本后到 / 转场中」。"""

    def __init__(self, text="", displayed=True, enabled=True):
        self.text = text
        self._displayed = displayed
        self._enabled = enabled

    def is_displayed(self):
        return self._displayed

    def is_enabled(self):
        return self._enabled


class FakeExecutor:
    """记录每次 driver 调用，供「廉价路径」断言。"""

    def __init__(self, sequence, repeat_last=False):
        self.sequence = list(sequence)  # 每项：element / Exception / page_source 字符串
        self.repeat_last = repeat_last
        self.calls: list[str] = []
        self._last = None
        self._trees: list[str] = []

    def find(self, locator):
        self.calls.append(f"find:{locator[0]['value']}")
        if self.sequence:
            self._last = self.sequence.pop(0)
        elif self.repeat_last and self._last is not None:
            pass  # 重复上一次的 find 结果（轮询场景）
        else:
            raise ElementNotFound("exhausted")
        if isinstance(self._last, Exception):
            raise self._last
        return self._last

    def page_source(self):
        self.calls.append("page_source")
        return self._trees.pop(0) if self._trees else "<AppiumAUT/>"


class FakeClock:
    """手动推进的 monotonic 时钟 + sleep 记录。"""

    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


def _engine(executor, clock, config=None, seq_extra=()):
    return WaitEngine(
        executor=executor,  # type: ignore[arg-type]
        locator_for=lambda target: [{"type": "accessibility_id", "value": target.id}],
        config=config or WaitConfig(),
        clock=clock,
        sleep=clock.sleep,
    )


def _spec(**kw):
    kw.setdefault("target", TargetRef(id="go_search"))
    return WaitSpec(**kw)


# --- 超时语义 ---

def test_timeout_raises_wait_timeout_not_element_not_found():
    clock = FakeClock()
    ex = FakeExecutor([ElementNotFound("nope")])
    with pytest.raises(WaitTimeout) as ei:
        _engine(ex, clock).wait_for(_spec(condition="exists", timeout=1.0))
    assert ei.value.failure_type == "WAIT_TIMEOUT"
    assert "WAIT_TIMEOUT" in str(ei.value)
    assert not isinstance(ei.value, ElementNotFound)


def test_unsupported_condition_fails_loud():
    clock = FakeClock()
    ex = FakeExecutor([FakeElement()])
    bad = WaitSpec(target=TargetRef(id="x"), condition="exists", timeout=1.0)
    object.__setattr__(bad, "condition", "sparkles")  # 绕过 schema Literal 校验
    with pytest.raises(UnsupportedWaitCondition):
        _engine(ex, clock).wait_for(bad)


# --- polling_interval 不写死 ---

def test_polling_interval_from_spec_not_hardcoded():
    clock = FakeClock()
    ex = FakeExecutor([ElementNotFound("1")] * 3 + [FakeElement()])
    _engine(ex, clock).wait_for(
        _spec(condition="exists", timeout=10.0, polling_interval=1.25)
    )
    assert clock.sleeps == [1.25, 1.25, 1.25]


def test_polling_interval_falls_back_to_config():
    clock = FakeClock()
    ex = FakeExecutor([ElementNotFound("1")])
    cfg = WaitConfig(polling_interval=0.4, default_timeout=5.0)
    with pytest.raises(WaitTimeout):
        _engine(ex, clock, cfg).wait_for(_spec(condition="exists", timeout=2.0))
    assert set(clock.sleeps) == {0.4}


def test_config_rejects_nonpositive_intervals():
    with pytest.raises(ValueError):
        WaitConfig(polling_interval=0)


# --- not_exists ---

def test_not_exists_returns_immediately_when_absent():
    clock = FakeClock()
    ex = FakeExecutor([ElementNotFound("gone")])
    _engine(ex, clock).wait_for(_spec(condition="not_exists", timeout=30.0))
    assert clock.sleeps == []  # 命中即返回，没有等满 timeout


def test_not_exists_times_out_when_element_persists():
    clock = FakeClock()
    ex = FakeExecutor([FakeElement()] * 20)
    with pytest.raises(WaitTimeout):
        _engine(ex, clock).wait_for(_spec(condition="not_exists", timeout=0.5))
    assert clock.sleeps  # 确实轮询过


# --- 条件矩阵 ---

@pytest.mark.parametrize("condition", ["exists", "visible", "enabled",
                                       "text_equals", "text_contains"])
def test_conditions_pass_on_matching_element(condition):
    clock = FakeClock()
    expected = {"text_equals": "搜索", "text_contains": "搜"}.get(condition)
    el = FakeElement(text="搜索")
    ex = FakeExecutor([el])
    _engine(ex, clock).wait_for(
        _spec(condition=condition, expected=expected, timeout=1.0)
    )
    assert len(ex.calls) == 1  # 满足即停


@pytest.mark.parametrize("condition", ["disabled"])
def test_disabled_passes_on_disabled_element(condition):
    clock = FakeClock()
    ex = FakeExecutor([FakeElement(enabled=False)])
    _engine(ex, clock).wait_for(_spec(condition=condition, timeout=1.0))
    assert len(ex.calls) == 1


@pytest.mark.parametrize("condition,enabled", [("enabled", False), ("disabled", True)])
def test_enabled_disabled_reject_wrong_state(condition, enabled):
    clock = FakeClock()
    ex = FakeExecutor([FakeElement(enabled=enabled)])
    with pytest.raises(WaitTimeout):
        _engine(ex, clock).wait_for(_spec(condition=condition, timeout=0.5))


def test_visible_rejects_invisible_element():
    clock = FakeClock()
    ex = FakeExecutor([FakeElement(displayed=False)])
    with pytest.raises(WaitTimeout):
        _engine(ex, clock).wait_for(_spec(condition="visible", timeout=0.5))


@pytest.mark.parametrize("condition,expected", [
    ("text_equals", "登录"),
    ("text_contains", "账"),
])
def test_text_conditions_reject_mismatch(condition, expected):
    clock = FakeClock()
    ex = FakeExecutor([FakeElement(text="退出登录")])
    with pytest.raises(WaitTimeout):
        _engine(ex, clock).wait_for(
            _spec(condition=condition, expected=expected, timeout=0.5)
        )


def test_element_appearing_late_is_picked_up():
    clock = FakeClock()
    ex = FakeExecutor([ElementNotFound("1"), ElementNotFound("2"), FakeElement()])
    _engine(ex, clock).wait_for(_spec(condition="exists", timeout=10.0))
    assert len(clock.sleeps) == 2


def test_ambiguous_element_does_not_satisfy_not_exists():
    clock = FakeClock()
    ex = FakeExecutor([AmbiguousElement("2 matches")], repeat_last=True)
    with pytest.raises(WaitTimeout):
        _engine(ex, clock).wait_for(_spec(condition="not_exists", timeout=0.5))


# --- active 廉价路径（7.3：不为此拉 page_source） ---

def test_screen_active_never_fetches_page_source():
    clock = FakeClock()
    ex = FakeExecutor([FakeElement(displayed=True)])
    _engine(ex, clock).wait_for(
        _spec(target=TargetRef(type="screen", id="HomeView"),
              condition="active", timeout=1.0)
    )
    assert "page_source" not in ex.calls
    assert ex.calls == ["find:HomeView"]


def test_screen_active_requires_visible_marker():
    clock = FakeClock()
    ex = FakeExecutor([FakeElement(displayed=False)])
    with pytest.raises(WaitTimeout):
        _engine(ex, clock).wait_for(
            _spec(target=TargetRef(type="screen", id="HomeView"),
                  condition="active", timeout=0.5)
        )
    assert "page_source" not in ex.calls


# --- 树静止判定（替代固定 sleep 缓冲） ---

def test_settle_returns_when_tree_hash_stable():
    clock = FakeClock()
    ex = FakeExecutor([])
    ex._trees = ["<A/>", "<A/>"]  # stable_polls=2 → 首次 + 1 次对比
    _engine(ex, clock).wait_for_settle()
    assert ex.calls == ["page_source", "page_source"]
    assert clock.sleeps == [0.2]


def test_settle_keeps_waiting_while_tree_changes():
    clock = FakeClock()
    ex = FakeExecutor([])
    ex._trees = ["<A/>", "<B/>", "<B/>"]
    _engine(ex, clock, WaitConfig(stable_polls=2)).wait_for_settle()
    assert ex.calls.count("page_source") == 3  # 变了就继续等


def test_settle_requires_three_consecutive_identical_trees():
    clock = FakeClock()
    ex = FakeExecutor([])
    ex._trees = ["<A/>", "<A/>", "<B/>", "<B/>", "<B/>"]
    _engine(ex, clock, WaitConfig(stable_polls=3)).wait_for_settle()
    assert ex.calls.count("page_source") == 5
