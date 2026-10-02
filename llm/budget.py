"""LLMBudget — Budget / Circuit Breaker（10.5，Task 4.2 重构）。

P0 语义保留（verify_stage8 回归路径）：`try_acquire()` 无参 + per-run 上限
（`max_calls_per_run` 属性仍在，P0 读它做展示）。

P1 增量（10.5）：
- **per-run + per-testcase 双限**：两个计数器独立扣减，任一超限即拒绝；
- **熔断**：连续 `breaker_consecutive_failures` 次（默认 3）失败 → 本 run
  后续不再调用，`try_acquire` 恒 False 且**不再发起 API 调用**（矩阵 #10：
  FakeLLM 计数断言的就是这条）。熔断不因单次成功复位——连续失败说明
  提供方/漂移形态系统性异常，继续调用只是烧钱（10.5「本 run 后续不再
  调用」）；
- 失败定义（10.5「连续失败」的 P1 定档）：一次 LLM 恢复尝试未以 RECOVERED
  收尾——provider 异常、LLM_INVALID_OUTPUT、校验链拒绝都算；RECOVERED
  才复位连续计数。

H18：纯内存状态机，无 IO。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["LLMBudget", "BudgetConfig"]


@dataclass
class BudgetConfig:
    """10.5 配置默认值。mta.yaml `llm:` 段可覆盖（15 节）。"""

    max_calls_per_run: int = 10
    max_calls_per_testcase: int = 3
    timeout_seconds: int = 20
    min_confidence: float = 0.85
    breaker_consecutive_failures: int = 3


class LLMBudget:
    def __init__(self, max_calls_per_run: int | None = None,
                 config: BudgetConfig | None = None,
                 max_calls_per_testcase: int | None = None,
                 breaker_consecutive_failures: int | None = None):
        self.config = config or BudgetConfig()
        # P0 兼容：同名参数覆盖 config（旧调用方 `LLMBudget(max_calls_per_run=5)`）
        if max_calls_per_run is not None:
            self.config.max_calls_per_run = max_calls_per_run
        if max_calls_per_testcase is not None:
            self.config.max_calls_per_testcase = max_calls_per_testcase
        if breaker_consecutive_failures is not None:
            self.config.breaker_consecutive_failures = \
                breaker_consecutive_failures
        self._used_run = 0
        self._used_testcase: dict[str | None, int] = {}
        self._consecutive_failures = 0
        self._broken = False

    @property
    def max_calls_per_run(self) -> int:
        """P0 兼容属性（verify_stage8 消费）。"""
        return self.config.max_calls_per_run

    @property
    def calls_used(self) -> int:
        """本 run 已发起的 API 调用数（报告 LLM Invocation Rate 分子）。"""
        return self._used_run

    @property
    def broken(self) -> bool:
        return self._broken

    def try_acquire(self, testcase_id: str | None = None) -> bool:
        """扣减一个调用名额。False = 不得发起 API 调用（调用方必须如实
        记 LLM_BUDGET_EXCEEDED，不得绕过计数直调 provider）。"""
        if self._broken:
            return False
        if self._used_run >= self.config.max_calls_per_run:
            return False
        # P0 无参形态（verify_stage8 回归路径）豁免 per-testcase 限：
        # 它不知道 testcase 归属，全落 None 桶——套 3 次上限会在第 4 次调用
        # 炸掉 P0 回归（review P3-3 实锤边界）。无参 = 只受 per-run 限。
        if testcase_id is not None:
            used_tc = self._used_testcase.get(testcase_id, 0)
            if used_tc >= self.config.max_calls_per_testcase:
                return False
            self._used_testcase[testcase_id] = used_tc + 1
        self._used_run += 1
        return True

    def record_failure(self) -> None:
        """一次未以 RECOVERED 收尾的尝试。连续达阈值 → 熔断（本 run 内
        不可逆）。"""
        self._consecutive_failures += 1
        if self._consecutive_failures >= \
                self.config.breaker_consecutive_failures:
            self._broken = True

    def record_success(self) -> None:
        """RECOVERED：连续失败计数复位（熔断已触发则不可逆）。"""
        self._consecutive_failures = 0
