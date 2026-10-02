"""Task 2.5 / P1-07：SuiteRunner + FailurePolicy（多用例套件 + H10 收口）。

**H10（硬约束）**：cleanup 失败 = ENVIRONMENT_FAILURE，**默认终止套件**，
不得继续跑下一条。理由：带着脏环境跑下一条，等于把「环境没复位」伪装成
「下一条用例的 bug」——排障方向被带偏，而且脏状态会累积。

**本模块是 R13-2 的收口点**：EnvironmentManager.prepare/cleanup 此前零
消费（实现存在、链路不通）。这里接线。

`FailurePolicy`：11.2 的 failure_policy，当前支持 ABORT_SUITE（默认）与
CONTINUE。
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from environment.manager import CleanupError, EnvironmentManager
from runner.result import RunResult, TestcaseResult
from tracer.storage import aggregate_status

__all__ = ["SuiteRunner", "FailurePolicy", "SuiteAborted"]


class FailurePolicy(str, enum.Enum):
    """11.2 cleanup 失败后的处置。"""

    ABORT_SUITE = "ABORT_SUITE"   # 默认（H10）
    CONTINUE = "CONTINUE"


class SuiteAborted(Exception):
    """套件被中止。**不算测试失败**——它是 H10 的正常后果。"""

    def __init__(self, reason: str, failed_testcase_id: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.failed_testcase_id = failed_testcase_id


@dataclass
class SuiteRunner:
    """多用例套件执行器。

    职责边界（刻意收窄）：
      - 每条用例：`env.prepare()` → 跑 → `finally: env.cleanup()`；
      - cleanup 失败 → CleanupError → 该条 ENVIRONMENT_FAILURE +
        按 failure_policy 决定中止或继续（H10）；
      - 不做 retry/recovery（那是 StepRunner 与 RecoveryEngine 的事）。

    `execute(tc)` 返回 `TestcaseResult`。用例内部怎么跑由 `run_one` 注入
    （P0 TestcaseRunner 或未来的新管线），本类只管套件语义与状态归集。
    """

    env: EnvironmentManager
    failure_policy: FailurePolicy = FailurePolicy.ABORT_SUITE
    run_one: Callable[[object], None] | None = None
    results: list = field(default_factory=list)
    # R18-3：cleanup 失败时回写 trace 的通道（可选）。新管线的
    # run_case(manage_env=False) 已在本类之前落了 testcase_run，cleanup
    # 失败无人回写 → trace 显示 PASS 但套件中止的矛盾。注入方
    # （cli.pipeline）提供 (testcase_result) -> None，零依赖不倒挂。
    on_cleanup_failure: Callable[[TestcaseResult], None] | None = None

    def __post_init__(self):
        if self.run_one is None:
            raise ValueError(
                "SuiteRunner.run_one 必须注入（Callable[[testcase], None]）："
                "套件语义不负责「怎么跑一条用例」，那是 runner 的职责")

    def run_testcase(self, tc) -> TestcaseResult:
        """跑一条用例：prepare → run_one → finally cleanup（H10）。"""
        result = TestcaseResult(testcase_id=tc.id, status="PASS")
        self.env.prepare(tc)
        try:
            # run_one 的返回值是**真实结果**（新管线 run_case 产出的
            # TestcaseResult：failure_type/duration/detail 都在里面）。
            # 2.7 H10 注入验证实锤：原实现丢弃返回值只用开头构造的空
            # PASS result——_suite_run_one 的真实状态被抹掉，H10 的
            # ENVIRONMENT_FAILURE/CLEANUP_FAILED 永远看不到。
            outcome = self.run_one(tc)
            if isinstance(outcome, TestcaseResult):
                result = outcome
        except Exception as e:
            status, failure_type = _classify(e)
            result.status = status
            result.failure_type = failure_type
        finally:
            # H10：cleanup 必须执行；失败不是「吞掉」，是升级成 ENVIRONMENT_FAILURE
            try:
                self.env.cleanup(tc)
                result.cleanup_status = "OK"
            except CleanupError as e:
                result.cleanup_status = "FAILED"
                result.status = "ENVIRONMENT_FAILURE"
                result.failure_type = "CLEANUP_FAILED"
                result.detail["cleanup_error"] = str(e)
                # R18-3：回写 trace——否则 run_case 早先落的 testcase_run
                # 停在 PASS，排障时看到「PASS 但套件中止」的矛盾终态。
                if self.on_cleanup_failure is not None:
                    try:
                        self.on_cleanup_failure(result)
                    except Exception:  # noqa: BLE001 — 回写失败不影响 H10 升级
                        pass
        # 8.1 终态聚合：`cleanup` 的失败已在上面的 except 里直接改写
        # status（ENVIRONMENT_FAILURE），这里不再对单元素列表调
        # aggregate_status —— 那是恒等操作（review P3-2：原实现
        # `aggregate_status([result.status]) or result.status` 是无操作，
        # 看起来像想合并前次 attempt 的状态而留下的残迹）。
        # 跨 attempt 的终态合并交给 suite 层（那里有多个 result 可聚合）。
        self.results.append(result)
        return result

    def run_suite(self, testcases) -> RunResult:
        """跑整个套件。H10：cleanup 失败默认中止（后续用例不跑）。

        中止时已跑的结果**保留**在 RunResult 里——报告需要看到「跑到哪
        因环境问题停了」，把已跑结果清掉等于伪造「没跑过」。
        """
        # run_id 必须全局唯一（14.2 `runs.run_id` 是主键，撞了会覆盖历史）。
        # review P3-3：原实现 `f"suite_{len(self.results)}"` 跨套件必撞
        # （每个 SuiteRunner 都从 0 开始数）。用 uuid + 时间戳，形状与 P0
        # Recorder 的 `start_run` 一致。
        run = RunResult(run_id=f"suite_{uuid.uuid4().hex[:8]}")
        # 防御性收口：循环内 len(list(testcases)) 会重复消费——调用方传
        # generator 时第二次 list() 得到空集，remaining 统计静默失真。
        # 现调用方都传 list，这里物化一次杜绝隐患。
        testcases = list(testcases)
        for tc in testcases:
            try:
                result = self.run_testcase(tc)
            except SuiteAborted:
                raise
            run.add(result)
            if result.detail.get("terminate_run"):
                # 7.5：wda.max_restart_per_run 耗尽 → 终止 run。比 H10 更硬
                # 的一档：预算耗尽意味着会话恢复通道已枯竭，继续调度只会
                # 逐条 INFRA_FAILURE——剩余用例不跑（不算进 total）。
                run.results[-1].detail.setdefault("aborted_suite", True)
                run.results[-1].detail.setdefault(
                    "remaining", len(list(testcases)) - len(run.results))
                raise SuiteAborted(
                    f"7.5：{tc.id} wda.max_restart_per_run 耗尽，终止 run",
                    failed_testcase_id=tc.id)
            if (result.status == "ENVIRONMENT_FAILURE"
                    and self.failure_policy is FailurePolicy.ABORT_SUITE):
                # 剩余用例标 ABORTED？不标——它们**没跑**，不能算进 total
                # （算进去会稀释 pass_rate）。单独记一笔。
                run.results[-1].detail["aborted_suite"] = True
                run.results[-1].detail["remaining"] = \
                    len(list(testcases)) - len(run.results)
                raise SuiteAborted(
                    f"H10：{tc.id} cleanup 失败 ENVIRONMENT_FAILURE，"
                    f"按默认策略终止套件",
                    failed_testcase_id=tc.id)
        return run


def _classify(e: Exception) -> tuple[str, str | None]:
    """异常 → (终态, failure_type)。fail-loud：认不出来的按 FAIL + 类型名，
    不静默归 ELEMENT_NOT_FOUND（那会把编程错误伪装成测试失败）。"""
    from session.app_session import UnsupportedResetError
    from session.device_session import InfraError
    from executor.executor import ElementNotFound, AmbiguousElement
    from executor.wait import WaitTimeout
    from executor.assertion import (AssertionValueMismatch,
                                    AssertionTargetDrift)

    if isinstance(e, CleanupError):
        return "ENVIRONMENT_FAILURE", "CLEANUP_FAILED"
    if isinstance(e, InfraError):
        return "INFRA_FAILURE", "WDA_FAILURE"
    if isinstance(e, UnsupportedResetError):
        return "ENVIRONMENT_FAILURE", "UNSUPPORTED_RESET"
    if isinstance(e, AssertionValueMismatch):
        return "FAIL", "ASSERTION_VALUE_MISMATCH"
    if isinstance(e, AssertionTargetDrift):
        # 8.2 的 failure_type 枚举里**没有** ASSERTION_TARGET_DRIFT（spec
        # 缺口，review P3-4）——8.2 枚举是设计文档，不擅自扩。这里先归到
        # 最接近的既有项，但**不丢信息**：调用方（2.6 JUnit/Report 消费
        # failure_type 前）会从 detail["kind"] 读到 "assertion_target"，
        # 7.3 明确该值。补枚举的事记在 p1_schema_review.md，待 2.6 拍板。
        return "FAIL", "ELEMENT_NOT_FOUND"
    if isinstance(e, WaitTimeout):
        return "FAIL", "WAIT_TIMEOUT"
    if isinstance(e, AmbiguousElement):
        return "FAIL", "AMBIGUOUS_ELEMENT"
    if isinstance(e, ElementNotFound):
        return "FAIL", "ELEMENT_NOT_FOUND"
    return "FAIL", type(e).__name__
