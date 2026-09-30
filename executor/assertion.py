"""Assertion Engine（设计 7.3 Assertion 段，Task 2.2 / P1-05）。

两条路径，语义严格分开（H6 / 7.3）：
  - **值断言失败**（目标定位到了、值不符）→ `AssertionValueMismatch` 携带结构化
    结果（expected / actual / target / passed），直接 FAIL，**无 Recovery**；
    判定是终态的——目标在手就不轮询，等文本「自己变成期望值」没有意义。
  - **目标定位失败**（ElementNotFound / AmbiguousElement）→ 轮询至 timeout
    （给晚到元素机会），仍失败则抛 `AssertionTargetDrift`——供 Recovery 分流，
    允许进 Recovery（第 9 节规则，`detail.kind = "assertion_target"`）。
  - `not_exists` 目标找不到 = passed（7.3 明确：不涉及漂移）；目标在 = 值不符。

`element_count` 的实现口径：Executor.find 的契约是 0 / 1 / ≥2（≥2 抛
AmbiguousElement fail-closed），engine 无法数出精确大数——actual ∈
{0, 1, ">=2"}。要精确计数需要 Executor 加 find_all，P1 用例矩阵里没有
count>1 的场景，先按此口径（记账 Task 2.3+ 需要 count>1 时扩展）。
"""
from __future__ import annotations

from dataclasses import dataclass
from time import monotonic, sleep
from typing import Callable

from executor.executor import AmbiguousElement, ElementNotFound, Executor, Locator
from testcase.schema import AssertionSpec, TargetRef

POLLING_INTERVAL = 0.3  # 仅用于「目标未定位」过渡态轮询；值判定不轮询

# 与 executor.wait._CONDITION_ATTR 同源不同集：断言没有 visible/active（不是等待条件），
# wait 没有 element_count（数量对等待无意义）。矩阵分开维护，各自 fail-loud。
_ASSERTION_CONDITIONS = {
    "exists", "not_exists", "text_equals", "text_contains",
    "element_count", "enabled", "disabled",
}


class AssertionValueMismatch(Exception):
    """H6：值断言失败，直接 FAIL 无 Recovery。`result` 携带结构化结果。"""

    __test__ = False  # type: ignore[attr-defined]  # pytest collection

    def __init__(self, result: "AssertionResult"):
        self.result = result
        super().__init__(
            f"ASSERTION_VALUE_MISMATCH: target={result.target!r} "
            f"condition={result.condition!r} expected={result.expected!r} "
            f"actual={result.actual!r}"
        )


class AssertionTargetDrift(Exception):
    """断言目标定位失败（ID 漂移）。允许进 Recovery（7.3 / 第 9 节）。"""

    __test__ = False  # type: ignore[attr-defined]

    def __init__(self, result: "AssertionResult"):
        self.result = result
        super().__init__(
            f"ASSERTION_TARGET_DRIFT: target={result.target!r} "
            f"condition={result.condition!r} not resolvable after "
            f"{result.timeout}s"
        )


@dataclass(frozen=True)
class AssertionResult:
    """7.3 结构化断言结果。"""

    condition: str
    target: str
    expected: object
    actual: object
    passed: bool
    timeout: float = 0.0


class AssertionEngine:
    """把 `AssertionSpec` 变成 passed / ValueMismatch / TargetDrift 三态。

    无状态；`locator_for` 由 runner 注入（定位解析归 Repository，4.1/5.5）。
    """

    def __init__(
        self,
        executor: Executor,
        locator_for: Callable[[TargetRef], Locator],
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], None] = sleep,
        polling_interval: float = POLLING_INTERVAL,
    ) -> None:
        self.ex = executor
        self.locator_for = locator_for
        self.clock = clock
        self.sleep = sleep
        self.polling_interval = polling_interval

    # --- 单轮判定（目标在手时调用；驱动异常由调用方兜） ---

    def _judge(self, spec: AssertionSpec, el) -> tuple[bool, object]:
        """返回 (passed, actual)。仅处理「目标已定位」的条件。"""
        if spec.condition == "exists":
            return True, "found"
        if spec.condition == "not_exists":
            return False, "present"
        if spec.condition == "enabled":
            return bool(el.is_enabled()), el.is_enabled()
        if spec.condition == "disabled":
            return not el.is_enabled(), el.is_enabled()
        if spec.condition == "text_equals":
            return el.text == spec.expected, el.text
        if spec.condition == "text_contains":
            return str(spec.expected) in (el.text or ""), el.text
        raise ValueError(  # pragma: no cover — 矩阵外条件在 check 入口已拦
            f"condition {spec.condition!r} has no target-held judge")

    def check(self, spec: AssertionSpec) -> AssertionResult:
        """终态判定入口。passed → 返回结果；否则抛对应异常。"""
        if spec.condition not in _ASSERTION_CONDITIONS:
            raise ValueError(
                f"assertion condition {spec.condition!r} unsupported, "
                f"supported={sorted(_ASSERTION_CONDITIONS)}")
        locator = self.locator_for(spec.target)
        target_id = spec.target.id
        timeout = spec.timeout
        deadline = self.clock() + timeout
        # not_exists 的 expected 语义是布尔（False = 期望目标不存在），schema 的
        # expected 字段此时为 None——结果里用语义值，不能透传 None（R: 结构化结果
        # 必须自解释，消费方不该再按 condition 猜 expected 含义）
        expected = spec.expected
        if spec.condition == "not_exists":
            expected = False

        while True:
            try:
                el = self.ex.find(locator)
            except ElementNotFound:
                # 两个例外出口：
                #   not_exists → 找不到就是成功（7.3），不涉及漂移；
                #   element_count → 找不到意味着 count=0，是「值」判定不是漂移
                #     （哪怕期望非 0——漂移说的是「定位手段失效」，这里定位手段
                #     正常工作了，结果确实是 0 个）。
                # R12-2：count=0 与 expected=0 相等即 passed（expected=0 是合法
                # 值，断言「元素已从列表消失」；expected=0、actual=0 判 FAIL
                # 语义自相矛盾）。下同，AmbiguousElement 分支的 ">=2" 同理。
                if spec.condition == "not_exists":
                    return AssertionResult(spec.condition, target_id,
                                           False, 0, True, timeout)
                if spec.condition == "element_count":
                    if spec.expected == 0:
                        return AssertionResult(spec.condition, target_id,
                                               0, 0, True, timeout)
                    return self._mismatch(spec, target_id, 0, timeout, expected)
            except AmbiguousElement:
                # ≥2 命中：element_count 视为「数量与期望不符」（fail-closed 下
                # 只知道 >=2）；其余条件无法唯一定位目标 → 漂移。
                # R12-2：expected=0 时 ">=2" 必然不符 → 值判定（漂移无关）。
                if spec.condition == "element_count":
                    return self._mismatch(spec, target_id, ">=2", timeout, expected)
                return self._drift(spec, target_id, timeout)
            else:
                if spec.condition == "element_count":
                    return self._mismatch_or_pass(spec, target_id, 1, timeout)
                # R12-1：find 成功后的属性读取（.text / is_enabled）发生在转场期
                # （恰恰是断言最常出现的时刻），可能抛 stale element 一类驱动异常。
                # 与 wait.py R11-3 同款：当作「本轮不满足」继续轮询至 deadline，
                # 不冒泡（否则既不是 ValueMismatch 也不是 Drift，步骤直接 FAILED
                # 且不在 recovery 白名单）。ValueError 保持 fail-loud。
                try:
                    passed, actual = self._judge(spec, el)
                except ValueError:
                    raise
                except Exception as e:  # noqa: BLE001 — 驱动异常类型随 Appium 版本变化
                    if self.clock() >= deadline:
                        return self._drift(spec, target_id, timeout)
                    self.sleep(self.polling_interval)
                    continue
                if passed:
                    return AssertionResult(spec.condition, target_id,
                                           expected, actual, True, timeout)
                # H6：值不符是终态——立即判 FAIL，不轮询等它变
                raise AssertionValueMismatch(
                    self._result(spec, target_id, actual, False, timeout, expected))

            if self.clock() >= deadline:
                return self._drift(spec, target_id, timeout)
            self.sleep(self.polling_interval)

    # --- 结果构造 ---

    @staticmethod
    def _result(spec: AssertionSpec, target_id: str, actual: object,
                passed: bool, timeout: float, expected: object = None) -> AssertionResult:
        # expected=None 时回退 spec.expected；not_exists 用调用方传入的语义值 False
        return AssertionResult(spec.condition, target_id,
                               expected if expected is not None else spec.expected,
                               actual, passed, timeout)

    def _mismatch(self, spec: AssertionSpec, target_id: str, actual: object,
                  timeout: float, expected: object = None) -> "AssertionResult":
        """构造并抛 ValueMismatch（值类失败统一出口）。"""
        result = self._result(spec, target_id, actual, False, timeout, expected)
        raise AssertionValueMismatch(result)

    def _mismatch_or_pass(self, spec: AssertionSpec, target_id: str,
                          actual: int, timeout: float) -> AssertionResult:
        if actual == spec.expected:
            return self._result(spec, target_id, actual, True, timeout)
        raise AssertionValueMismatch(
            self._result(spec, target_id, actual, False, timeout))

    def _drift(self, spec: AssertionSpec, target_id: str,
               timeout: float) -> "AssertionResult":
        result = self._result(spec, target_id, "NOT_RESOLVED", False, timeout)
        raise AssertionTargetDrift(result)
