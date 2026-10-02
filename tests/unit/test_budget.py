"""Task 4.2：LLM Budget / Circuit Breaker（10.5）。

H18：纯内存状态机。矩阵 #10 的「熔断后不发新 API 调用」在引擎层有
FakeLLM 计数断言（test_llm_contract / fault_injection），这里验状态机本身。
"""
from __future__ import annotations

from llm.budget import BudgetConfig, LLMBudget


def test_per_run_limit():
    b = LLMBudget(max_calls_per_run=2)
    assert b.try_acquire("tc1") is True
    assert b.try_acquire("tc1") is True
    assert b.try_acquire("tc1") is False, "per-run 上限拒绝"


def test_per_testcase_limit():
    """10.5：per-run + per-testcase 双限独立扣减——单用例烧满不能吃光
    别的用例的配额，反之亦然。"""
    b = LLMBudget(config=BudgetConfig(max_calls_per_run=10,
                                      max_calls_per_testcase=2))
    assert b.try_acquire("tc1") is True
    assert b.try_acquire("tc1") is True
    assert b.try_acquire("tc1") is False, "per-testcase 上限拒绝"
    assert b.try_acquire("tc2") is True, "别的用例不受 tc1 影响"


def test_breaker_after_consecutive_failures():
    """10.5：连续 3 次失败 → 熔断，本 run 后续不再调用。"""
    b = LLMBudget(config=BudgetConfig(max_calls_per_run=10,
                                      breaker_consecutive_failures=3))
    for _ in range(3):
        assert b.try_acquire("tc1") is True
        b.record_failure()
    assert b.broken is True
    assert b.try_acquire("tc1") is False, "熔断后不再发名额"


def test_breaker_counts_consecutive_not_total():
    """「连续」失败：RECOVERED 复位计数；未达阈值不熔断。"""
    b = LLMBudget(config=BudgetConfig(breaker_consecutive_failures=3))
    b.try_acquire("tc"); b.record_failure()
    b.try_acquire("tc"); b.record_failure()
    b.try_acquire("tc"); b.record_success()      # 复位
    b.try_acquire("tc"); b.record_failure()
    b.try_acquire("tc"); b.record_failure()
    assert b.broken is False, "成功复位后未再连续 3 次"
    b.try_acquire("tc"); b.record_failure()
    assert b.broken is True


def test_breaker_irreversible_within_run():
    """熔断不可逆（10.5「本 run 后续不再调用」）：成功也不解锁。"""
    b = LLMBudget(config=BudgetConfig(breaker_consecutive_failures=2))
    b.try_acquire("tc"); b.record_failure()
    b.try_acquire("tc"); b.record_failure()
    assert b.broken is True
    b.record_success()
    assert b.try_acquire("tc") is False, "熔断后 success 不解锁"


def test_p0_compat_max_calls_per_run_attr():
    """P0 契约保留（verify_stage8）：位置参数 + max_calls_per_run 属性。"""
    b = LLMBudget(max_calls_per_run=5)
    assert b.max_calls_per_run == 5
    assert b.try_acquire() is True, "无参调用（P0 形态）可用"


def test_config_defaults_are_design_values():
    """10.5 默认值：10/3/20/0.85/3——改动即设计变更，测试拦住。"""
    c = BudgetConfig()
    assert c.max_calls_per_run == 10
    assert c.max_calls_per_testcase == 3
    assert c.timeout_seconds == 20
    assert c.min_confidence == 0.85
    assert c.breaker_consecutive_failures == 3


def test_p0_noarg_call_exempt_from_per_testcase_limit():
    """review_m4_task42 P3-3：P0 无参形态全落 None 桶——若套 per-testcase
    上限，verify_stage8 第 4 次调用就会被拒（一踩就炸的兼容边界）。
    无参 = 只受 per-run 限。"""
    b = LLMBudget()   # 默认 per-testcase=3, per-run=10
    assert all(b.try_acquire() for _ in range(10)), \
        "无参调用不受 per-testcase 限（P0 兼容）"
    assert b.try_acquire() is False, "per-run 上限仍然生效"
