"""Task 2.5 / P1-07：SuiteRunner + H10 收口。

**本文件是 R13-2 的核销点**：EnvironmentManager.prepare/cleanup 此前零
消费（实现存在、链路不通，review R13-2 记账到 2.5）。这里接线并钉住
H10 语义：cleanup 失败 = ENVIRONMENT_FAILURE，默认**终止套件**。

为什么默认中止：带着脏环境跑下一条，等于把「环境没复位」伪装成「下一条
用例的 bug」——排障方向被带偏，且脏状态累积。
"""

from __future__ import annotations

import pytest

from environment.manager import CleanupError
from runner.suite import (
    FailurePolicy,
    SuiteAborted,
    SuiteRunner,
    _classify,
)


# ------------------------------------------------------------ fakes

class FakeEnv:
    def __init__(self, cleanup_error=None):
        self.cleanup_error = cleanup_error
        self.prepared = []
        self.cleaned = []

    def prepare(self, tc):
        self.prepared.append(tc.id)

    def cleanup(self, tc):
        self.cleaned.append(tc.id)
        if self.cleanup_error:
            raise self.cleanup_error


class FakeTC:
    def __init__(self, tc_id):
        self.id = tc_id
        self.precondition = {}


def _suite(env, run_one=None, policy=FailurePolicy.ABORT_SUITE):
    return SuiteRunner(env=env, failure_policy=policy,
                       run_one=run_one or (lambda tc: None))


# ------------------------------------------------------------ 正常路径

def test_prepare_and_cleanup_both_run_for_each_testcase():
    env = FakeEnv()
    s = _suite(env)
    s.run_testcase(FakeTC("a"))
    s.run_testcase(FakeTC("b"))
    assert env.prepared == ["a", "b"]
    assert env.cleaned == ["a", "b"]


def test_all_pass_gives_exit_code_zero():
    env = FakeEnv()
    s = _suite(env)
    run = s.run_suite([FakeTC("a"), FakeTC("b")])
    assert run.exit_code == 0
    assert run.summary.counts == {"PASS": 2}


# ------------------------------------------------------------ H10 核心

def test_cleanup_failure_makes_environment_failure_h10():
    env = FakeEnv(cleanup_error=CleanupError("reset hook crashed"))
    s = _suite(env)
    r = s.run_testcase(FakeTC("a"))
    assert r.status == "ENVIRONMENT_FAILURE"
    assert r.failure_type == "CLEANUP_FAILED"
    assert r.cleanup_status == "FAILED"


def test_cleanup_failure_aborts_suite_by_default_h10():
    """H10 默认：cleanup 失败 → 终止套件，后续用例不跑。"""
    env = FakeEnv(cleanup_error=CleanupError("dirty env"))
    s = _suite(env, policy=FailurePolicy.ABORT_SUITE)
    ran = []

    def run_one(tc):
        ran.append(tc.id)

    s.run_one = run_one
    with pytest.raises(SuiteAborted) as ei:
        s.run_suite([FakeTC("a"), FakeTC("b"), FakeTC("c")])
    assert ran == ["a"], "cleanup 失败后不该再跑 b/c"
    assert ei.value.failed_testcase_id == "a"
    assert "H10" in ei.value.reason


def test_cleanup_failure_can_continue_if_policy_says_so():
    """显式 CONTINUE 时允许继续——但 ENVIRONMENT_FAILURE 仍记录。"""
    env = FakeEnv(cleanup_error=CleanupError("dirty"))
    s = _suite(env, policy=FailurePolicy.CONTINUE)
    ran = []

    def run_one(tc):
        ran.append(tc.id)

    s.run_one = run_one
    run = s.run_suite([FakeTC("a"), FakeTC("b")])
    assert ran == ["a", "b"]
    assert run.summary.counts == {"ENVIRONMENT_FAILURE": 2}
    assert run.exit_code == 2


def test_suite_abort_keeps_already_run_results():
    """中止时已跑结果必须保留——报告要看「跑到哪因环境停了」，
    清掉等于伪造「没跑过」。"""
    env = FakeEnv()
    calls = {"n": 0}

    def run_one(tc):
        calls["n"] += 1
        if calls["n"] == 2:
            env.cleanup_error = CleanupError("boom")

    s = _suite(env)
    s.run_one = run_one          # ← 漏了这行，s 用的还是 no-op lambda
    with pytest.raises(SuiteAborted):
        s.run_suite([FakeTC("a"), FakeTC("b"), FakeTC("c")])
    # run_testcase 逐条 append 到 s.results
    assert [r.testcase_id for r in s.results] == ["a", "b"]


def test_aborted_suite_marks_remaining_count():
    """中止时记下还剩几条没跑（不进 total——会稀释 pass_rate）。"""
    env = FakeEnv(cleanup_error=CleanupError("boom"))
    s = _suite(env)
    with pytest.raises(SuiteAborted):
        s.run_suite([FakeTC("a"), FakeTC("b"), FakeTC("c")])
    last = s.results[-1]
    assert last.detail.get("aborted_suite") is True
    assert last.detail.get("remaining") == 2


# ------------------------------------------------------------ cleanup 必执行

def test_cleanup_runs_even_when_testcase_fails():
    """普通失败也要 cleanup（11.2：普通失败仍执行 cleanup）。"""
    env = FakeEnv()

    def run_one(tc):
        raise AssertionError("assert failed")

    s = _suite(env, run_one=run_one)
    s.run_testcase(FakeTC("a"))
    assert env.cleaned == ["a"], "用例失败不能跳过 cleanup"


def test_cleanup_error_overrides_pass_status_h10():
    """用例本体 PASS 但 cleanup 失败 → 终态必须是 ENVIRONMENT_FAILURE，
    不能停在 PASS。8.1 优先级：ENV > PASS。"""
    env = FakeEnv(cleanup_error=CleanupError("post-cleanup dirty"))
    s = _suite(env)
    r = s.run_testcase(FakeTC("a"))
    assert r.status == "ENVIRONMENT_FAILURE"
    assert r.status != "PASS"


def test_cleanup_error_overrides_testcase_failure():
    env = FakeEnv(cleanup_error=CleanupError("dirty"))

    def run_one(tc):
        raise AssertionError("assert failed")

    s = _suite(env, run_one=run_one)
    r = s.run_testcase(FakeTC("a"))
    assert r.status == "ENVIRONMENT_FAILURE", "ENV 优先级高于 FAIL（8.1）"
    assert r.failure_type == "CLEANUP_FAILED"


# ------------------------------------------------------------ 异常分类

@pytest.mark.parametrize("exc,status,failure_type", [
    (__import__("session.device_session", fromlist=["InfraError"]).InfraError("wda"),
     "INFRA_FAILURE", "WDA_FAILURE"),
    (__import__("session.app_session", fromlist=["UnsupportedResetError"])
     .UnsupportedResetError("nope"),
     "ENVIRONMENT_FAILURE", "UNSUPPORTED_RESET"),
    (__import__("environment.manager", fromlist=["CleanupError"])
     .CleanupError("x"),
     "ENVIRONMENT_FAILURE", "CLEANUP_FAILED"),
])
def test_classify_known_exceptions(exc, status, failure_type):
    assert _classify(exc) == (status, failure_type)


def test_classify_unknown_exception_keeps_type_name():
    """fail-loud：未知异常不静默归 ELEMENT_NOT_FOUND。"""
    status, ft = _classify(RuntimeError("my bug"))
    assert status == "FAIL"
    assert ft == "RuntimeError", "未知异常要保留类型名供排查"


def test_run_one_required():
    """不注入 run_one 直接报错——套件语义不管「怎么跑一条」。"""
    with pytest.raises(ValueError, match="run_one"):
        SuiteRunner(env=FakeEnv(), run_one=None)


# --- Task 2.7：run_one 返回值的消费（H10 注入验证实锤的 bug） ---

def test_run_one_return_value_is_the_result():
    """2.7 H10 注入验证实锤：原实现丢弃 run_one 返回值、只用开头构造的
    空 PASS result——新管线产出的真实状态（failure_type/duration/detail）
    全被抹掉。这里钉住「run_one 的 TestcaseResult 就是最终结果」。"""
    from runner.result import TestcaseResult

    env = FakeEnv()
    s = _suite(env)

    def run_one(tc):
        return TestcaseResult(
            testcase_id=tc.id, status="FAIL",
            failure_type="ELEMENT_NOT_FOUND",
            failure_phase="PRE_DISPATCH",
            duration_ms=1234,
            detail={"error": "boom"})

    s.run_one = run_one
    r = s.run_testcase(FakeTC("a"))
    assert r.status == "FAIL"
    assert r.failure_type == "ELEMENT_NOT_FOUND"
    assert r.failure_phase == "PRE_DISPATCH"
    assert r.duration_ms == 1234
    assert r.detail == {"error": "boom"}


def test_run_one_exception_still_classified():
    """run_one 抛异常（而非返回）时维持旧行为：异常 → _classify 终态。"""
    from executor.executor import ElementNotFound

    env = FakeEnv()
    s = _suite(env)

    def run_one(tc):
        raise ElementNotFound("gone")

    s.run_one = run_one
    r = s.run_testcase(FakeTC("a"))
    assert r.status == "FAIL"
    assert r.failure_type == "ELEMENT_NOT_FOUND"
    assert r.cleanup_status == "OK"
