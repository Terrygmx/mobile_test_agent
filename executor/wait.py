"""Wait Engine（设计 7.3，Task 2.1 / P1-05）。

四条硬约束，全部有单测兜底：
  1. 轮询：find → 检查条件 → sleep(polling_interval) → 直到 timeout；
     超时抛 `WaitTimeout`（failure_type = WAIT_TIMEOUT），**不是** ELEMENT_NOT_FOUND——
     两者语义不同：后者是定位失败（可进 Recovery），前者多为后端慢/App bug/数据问题
     （7.3：默认不触发 Recovery / LLM）。
  2. `polling_interval` / `default_timeout` 来自 `WaitConfig`（对应 `mta.yaml` 的
     `wait:` 段，设计 15），或用例的 WaitSpec 显式声明；**不写死**。
  3. implicit wait = 0（H11）：否则 `not_exists` 会被隐式等待拖慢。Executor 在
     接管 driver 时显式清零。
  4. `wait_for screen active` 走廉价路径：只 find marker + visible，不拉 page_source。

「树静止判定」补 Task 1.6 教训（见 runner TAP_SETTLE_SECONDS 注释）：marker 可 find
≠ 手势层就绪，SwiftUI NavigationStack 转场期间立即 tap 会被吞。这里用「连续 N 次
page_source 哈希不变」替代固定 sleep 缓冲。
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Callable

from executor.executor import AmbiguousElement, ElementNotFound, Executor, Locator
from testcase.schema import TargetRef, WaitSpec

# 设计 15：wait: {default_timeout: 10, polling_interval: 0.3}
DEFAULT_TIMEOUT = 10.0
DEFAULT_POLLING_INTERVAL = 0.3

# 树静止判定参数（Task 1.6 probe4/5 实测 1.0s 固定缓冲不足且浪费）
DEFAULT_STABLE_POLLS = 2
DEFAULT_STABLE_INTERVAL = 0.2

# 条件 → 需要读元素哪个属性；None = 只要求 find 成功
_CONDITION_ATTR: dict[str, str | None] = {
    "exists": None,
    "not_exists": None,
    "active": "visible",
    "visible": "visible",
    "enabled": "enabled",
    "disabled": "enabled",
    "text_equals": "text",
    "text_contains": "text",
}

# 布尔条件：目标属性必须等于该值才算满足
_EXPECT_ENABLED = {"enabled": True, "disabled": False}


class WaitTimeout(Exception):
    """7.3：轮询超时。与 ElementNotFound 严格区分（不进 Recovery）。"""

    failure_type = "WAIT_TIMEOUT"


class UnsupportedWaitCondition(ValueError):
    """condition 不在矩阵内 → fail-loud，不静默降级为「元素存在」（R10-4 同款口径）。"""


@dataclass(frozen=True)
class WaitConfig:
    """`mta.yaml` 的 `wait:` 段（设计 15）。默认值只在此处定义，代码别处不写死。"""

    default_timeout: float = DEFAULT_TIMEOUT
    polling_interval: float = DEFAULT_POLLING_INTERVAL
    stable_polls: int = DEFAULT_STABLE_POLLS
    stable_interval: float = DEFAULT_STABLE_INTERVAL

    def __post_init__(self) -> None:
        for name in ("default_timeout", "polling_interval", "stable_interval"):
            if getattr(self, name) <= 0:
                raise ValueError(f"wait config {name} must be > 0")
        if self.stable_polls < 1:
            raise ValueError("wait config stable_polls must be >= 1")


LocatorFn = Callable[[TargetRef], Locator]
# clock / sleep 注入：单测不需要真等（默认 time.monotonic / time.sleep）
Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class WaitEngine:
    """把 `WaitSpec` 变成「轮询直到满足或超时」。无状态，可复用。

    `locator_for` 由调用方（runner）注入：定位策略解析归 Repository（4.1/5.5），
    wait engine 不自己碰 repo。
    """

    def __init__(
        self,
        executor: Executor,
        locator_for: LocatorFn,
        config: WaitConfig | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleeper = time.sleep,
    ) -> None:
        self.ex = executor
        self.locator_for = locator_for
        self.config = config or WaitConfig()
        self.clock = clock
        self.sleep = sleep

    # --- 条件判定 ---

    def _check(self, spec: WaitSpec, locator: Locator) -> tuple[bool, str | None]:
        """返回 (满足?, 失败原因)。找不到元素 → (False, 原因)，不抛。"""
        if spec.condition not in _CONDITION_ATTR:
            raise UnsupportedWaitCondition(
                f"wait condition {spec.condition!r} unsupported, "
                f"supported={sorted(_CONDITION_ATTR)}"
            )
        try:
            el = self.ex.find(locator)
        except ElementNotFound as e:
            # not_exists 的「满足」形态就是找不到（7.3：不能被 implicit wait 拖慢）
            if spec.condition == "not_exists":
                return True, None
            return False, f"element not found: {e}"
        except AmbiguousElement as e:
            # 歧义 = 目标确实「在」（2 个以上），not_exists 不成立；其余条件继续等
            return False, f"ambiguous element: {e}"

        if spec.condition == "not_exists":
            return False, f"element present: {locator}"
        if spec.condition == "exists":
            return True, None

        attr = _CONDITION_ATTR[spec.condition]
        if attr == "visible":
            if bool(el.is_displayed()) is True:
                return True, None
            return False, "element not visible"
        if attr == "enabled":
            want = _EXPECT_ENABLED[spec.condition]
            if bool(el.is_enabled()) is want:
                return True, None
            return False, f"element enabled={el.is_enabled()} expected {want}"
        if attr == "text":
            text = el.text or ""
            if spec.condition == "text_equals" and text == spec.expected:
                return True, None
            if spec.condition == "text_contains" and str(spec.expected) in text:
                return True, None
            return False, f"text {text!r} does not satisfy {spec.condition}"
        raise UnsupportedWaitCondition(  # pragma: no cover - 上面的矩阵已穷举
            f"wait condition {spec.condition!r} has no check"
        )

    # --- 主入口 ---

    def wait_for(self, spec: WaitSpec) -> None:
        """满足即返回；超时抛 `WaitTimeout`。

        `screen active` 的廉价路径在 7.3 明确：只 find marker + visible——本方法
        对所有条件都只做 `find_elements`，不读 page_source（`is_displayed` 是元素级
        查询，不是整树快照），FakeDriver 记录调用可证。
        """
        if spec.condition not in _CONDITION_ATTR:
            raise UnsupportedWaitCondition(
                f"wait condition {spec.condition!r} unsupported, "
                f"supported={sorted(_CONDITION_ATTR)}"
            )
        locator = self.locator_for(spec.target)
        timeout = spec.timeout if spec.timeout > 0 else self.config.default_timeout
        interval = spec.polling_interval or self.config.polling_interval
        deadline = self.clock() + timeout
        last = "condition never evaluated"
        while True:
            ok, reason = self._check(spec, locator)
            if ok:
                return
            last = reason or last
            if self.clock() >= deadline:
                break
            self.sleep(interval)
        raise WaitTimeout(
            f"WAIT_TIMEOUT: target={spec.target.id!r} condition={spec.condition!r} "
            f"timeout={timeout}s polling_interval={interval}; last: {last}"
        )

    # --- 树静止（替代 TAP_SETTLE_SECONDS 固定缓冲） ---

    def wait_for_settle(self) -> None:
        """连续 `stable_polls` 次 page_source 哈希不变 → 认为 UI 树已静止。

        首次取树 + 之后每次取树都算 round-trip；间隔 `stable_interval`。
        与固定 sleep 相比：不达标就继续等（SwiftUI 转场可能任意长），达标即返回。
        """
        previous = self._tree_hash()
        streak = 1
        while streak < self.config.stable_polls:
            self.sleep(self.config.stable_interval)
            current = self._tree_hash()
            if current == previous:
                streak += 1
            else:
                streak = 1  # 树还在变（转场进行中）→ 重新计数，不提前返回
            previous = current

    def _tree_hash(self) -> str:
        return hashlib.sha256(self.ex.page_source().encode("utf-8")).hexdigest()
