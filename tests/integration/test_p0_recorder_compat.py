"""P0 Recorder 兼容回退的真实性测试（R15-1，P1）。

R15-1 探针实锤：`StepRunner._record` 用 `except TypeError` 区分新旧
Recorder，兼容回退调用漏传真实 `Recorder.record_step` 的**必填首参
run_id** → 回退自身必然 TypeError → 被外层 except 兜住 → steps 表 0 行。

**为什么原来的测试没抓到**：`test_old_p0_recorder_still_works` 用了替身
`_OldP0Recorder(step_index, action_type, ...)`——**没有 run_id**，与真实
签名不符。这是 R2-0（mock 走旧 schema 假绿）的同款模式：替身保真度不足，
测的是想象中的接口。

本文件的全部要点：**用真实 `tracer.recorder.Recorder`，不用替身**。
"""

from __future__ import annotations

import sqlite3
import warnings

import pytest

from executor.guard import EnvKind, Guard
from runner.runner import RunStepContext, StepRunner
from testcase.schema import Idempotency, Risk


class _FakeElement:
    pass


class _Executor:
    def __init__(self, found=None):
        self.found = found if found is not None else [_FakeElement()]
        self.calls: list[str] = []

    def find(self, strategies):
        self.calls.append("find")
        return list(self.found)

    def perform(self, action, element, value=None):
        self.calls.append("perform")


class _DS:
    def ensure_alive(self):
        pass


def _ctx(**kw) -> RunStepContext:
    base = dict(element_id="login_button", screen_id="LoginView",
                strategies=(("accessibility id", "login_button"),),
                action="tap", risk=Risk.LOW,
                idempotency=Idempotency.IDEMPOTENT, step_index=0)
    base.update(kw)
    return RunStepContext(**base)


def _rows(recorder) -> int:
    return recorder.conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0]


# --- R15-1 核心：真实 P0 Recorder 必须真的写进 steps 表 ---


def test_real_p0_recorder_actually_writes_a_row(tmp_path):
    """R15-1 回归：真实 `tracer.recorder.Recorder` + StepRunner 跑一步 SUCCESS
    → steps 表必须有 1 行。

    R15-1 之前这条必然 0 行：兼容回退漏传必填的 run_id，回退自身 TypeError
    被外层 except 吞掉，只留一条 RuntimeWarning。测试用 `warnings.simplefilter
    ("error")` 把 warning 升级成失败——**不允许靠 warning 蒙混过关**。
    """
    from tracer.recorder import Recorder
    rec = Recorder(tmp_path / "p0.db")
    runner = StepRunner(_Executor(), _DS(), Guard(EnvKind.SANDBOX), recorder=rec,
                        run_id="run_r15")

    with warnings.catch_warnings():
        # trace 写失败是异常路径；成功路径不得产生任何 warning
        warnings.simplefilter("error")
        out = runner.run_step(_ctx())

    assert out.ok
    assert _rows(rec) == 1, (
        "真实 P0 Recorder 一步都没写进去（R15-1：回退漏传 run_id）")


def test_real_p0_recorder_row_has_correct_run_id(tmp_path):
    """写入的行必须挂在正确的 run_id 下——回退补的 run_id 不能是空串/
    硬编码，否则 P0 脚本按 run_id 关联会错（坑 14）。"""
    from tracer.recorder import Recorder
    rec = Recorder(tmp_path / "p0.db")
    rec.start_run("run_abc")
    runner = StepRunner(_Executor(), _DS(), Guard(EnvKind.SANDBOX), recorder=rec,
                        run_id="run_abc")

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        runner.run_step(_ctx())

    row = rec.conn.execute("SELECT run_id, action_type, status FROM steps").fetchone()
    assert tuple(row) == ("run_abc", "tap", "SUCCESS")


def test_real_p0_recorder_writes_failure_row(tmp_path):
    """失败路径同样要写进去（error 字段）——不只是成功路径。"""
    from tracer.recorder import Recorder
    rec = Recorder(tmp_path / "p0.db")
    runner = StepRunner(_Executor(found=[]), _DS(), Guard(EnvKind.SANDBOX),
                        recorder=rec, run_id="run_x")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = runner.run_step(_ctx())
    assert not out.ok
    assert _rows(rec) == 1
    row = rec.conn.execute("SELECT status, error FROM steps").fetchone()
    assert row[0] == "FAILED" and "no element matched" in (row[1] or "")


# --- 签名探测：区分「签名不兼容」与「调用方拼错 kwarg」 ---


def test_unsupported_kwarg_is_not_silently_downgraded_to_legacy():
    """R15-1 附带风险：`except TypeError` 会把**调用方自己拼错的新 kwarg**
    静默降级成「旧 Recorder 回退」——编程错误被当成兼容问题掩盖。

    这里用一个「签名不兼容但支持新字段」的 recorder 验证：新调用失败时
    必须能区分「它不认识这些 kwarg」和「它认识但内部出错」。
    """
    calls: list[dict] = []

    class _TypeErrorInBodyRecorder:
        """支持全部新 kwarg，但**函数体内部**抛 TypeError。

        这种情况不是签名不兼容，回退会把真实编程错误伪装成兼容问题。"""

        def record_step(self, **kw):
            calls.append(kw)
            raise TypeError("内部 bug: unsupported operand type(s)")

    runner = StepRunner(_Executor(), _DS(), Guard(EnvKind.SANDBOX),
                        recorder=_TypeErrorInBodyRecorder(), run_id="r1")
    with pytest.warns(RuntimeWarning, match="trace write failed"):
        out = runner.run_step(_ctx())
    assert out.ok, "步骤结论不受 trace 影响"
    # 只调了一次：说明没有把「内部 TypeError」误当成签名不兼容再回退一次
    assert len(calls) == 1, (
        "函数体 TypeError 被当成签名不兼容 → 多调了一次回退路径，"
        "编程错误被伪装成兼容路径（R15-1 附带风险）")


def test_recorder_without_run_id_fails_loudly_at_construction(tmp_path):
    """StepRunner 用了真实 P0 Recorder 就必须给 run_id——不给要在**构造期**
    就响，而不是等到每步 trace 写失败才发现。

    fail-loud 优先：等到运行期才发现「trace 全丢」，报告里看不出是配置错。"""
    from tracer.recorder import Recorder
    rec = Recorder(tmp_path / "p0.db")
    with pytest.raises(ValueError, match="run_id"):
        StepRunner(_Executor(), _DS(), Guard(EnvKind.SANDBOX), recorder=rec)


def test_trace_store_recorder_needs_no_run_id():
    """TraceStore 的 record_step 首参是**必填的 tc_run_id**（不是 run_id），
    StepRunner 必须按签名探测传对的那个——R15-1 修复过程中这里也翻过车：
    修 P0 的 run_id 时若写死，新路径会把 TraceStore 也打挂。"""
    class _StoreLike:
        def __init__(self):
            self.calls = []

        def record_step(self, tc_run_id, step_index, **kw):
            self.calls.append((tc_run_id, step_index))

    store = _StoreLike()
    runner = StepRunner(_Executor(), _DS(), Guard(EnvKind.SANDBOX), recorder=store,
                        tc_run_id=42)
    out = runner.run_step(_ctx())
    assert out.ok
    assert len(store.calls) == 1, "TraceStore 形态的 recorder 不该被要求 run_id"
    assert store.calls[0] == (42, 0)


def test_trace_store_form_fails_loudly_without_tc_run_id():
    """对称：TraceStore 形态不给 tc_run_id 也要在构造期响。"""
    class _StoreLike:
        def record_step(self, tc_run_id, step_index, **kw):
            pass

    with pytest.raises(ValueError, match="tc_run_id"):
        StepRunner(_Executor(), _DS(), Guard(EnvKind.SANDBOX),
                   recorder=_StoreLike())


# --- 兼容路径本身不该掩盖真实错误 ---


def test_recorder_closed_db_still_warns_not_raises(tmp_path):
    """真实写盘失败（库被关）不得把步骤变成崩溃——但必须 warn。"""
    from tracer.recorder import Recorder
    rec = Recorder(tmp_path / "p0.db")
    runner = StepRunner(_Executor(), _DS(), Guard(EnvKind.SANDBOX), recorder=rec,
                        run_id="r1")
    rec.conn.close()
    with pytest.warns(RuntimeWarning, match="trace write failed"):
        out = runner.run_step(_ctx())
    assert out.ok
