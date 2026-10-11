"""Batch B：`except InfraError` 处的 **App 崩溃判定**（设计 P1 §8.2）。

缺陷形态（`docs/p3_data_audit.md` §8.3）：App 真崩了也记 `WDA_FAILURE` +
`INFRA_FAILURE`，于是 `crash_detected`（权重 5）恒 False，归因指向环境。
本文件钉住修好之后的行为。

三条边界（每条都要有反钉，否则「接线」会退化成「总是记 APP_CRASH」）：
1. **没注入探测 = 旧行为**（既有调用点零改动）；
2. **探测说没崩 = 旧行为**（旁路证据不升级结论）；
3. **App 崩溃不触发 WDA 重跑**（`_wda_aftermath` 是给 WDA 抖动用的）。
"""
from __future__ import annotations

import pytest

from cli.pipeline import SessionPipeline, _crash_verdict
from session.device_session import InfraError
from telemetry.collectors import CrashReport


def _report():
    return CrashReport(path="/x/a.ips", bundle_id="com.demo",
                       timestamp="2026-10-11 20:00:00.00 +0800",
                       incident_id="INC-1", exception_type="EXC_CRASH",
                       signal="SIGSEGV", termination="Segmentation fault: 11",
                       faulting_thread=0, top_frame="main")


class _Probe:
    """`CrashProbe` 的替身（只看 `crashed_since` 的结论）。"""

    def __init__(self, report=None, *, boom=False):
        self.report = report
        self.boom = boom
        self.calls = []

    def crashed_since(self, since):
        self.calls.append(since)
        if self.boom:
            raise OSError("探测坏了")
        return self.report


# ======================================================================
# 1. `_crash_verdict`：纯函数，两个来源合成一个结论
# ======================================================================


def test_verdict_without_a_probe_keeps_the_old_classification():
    """**零改动兼容**：`crash_probe=None`（既有全部调用点）→ 旧行为。"""
    assert _crash_verdict(None, since=None) is None


def test_verdict_returns_the_report_when_the_app_crashed():
    probe = _Probe(_report())
    assert _crash_verdict(probe, since=None).incident_id == "INC-1"


def test_verdict_is_none_when_the_probe_says_no_crash():
    assert _crash_verdict(_Probe(None), since=None) is None


def test_verdict_is_none_when_the_probe_itself_fails():
    """探测失败**不升级**结论（`None` = 没有证据说它崩了 → 旧行为）。"""
    assert _crash_verdict(_Probe(boom=True), since=None) is None


# ======================================================================
# 2. 落到 `_map_exception` 的口径：崩溃 → (FAIL, APP_CRASH)
# ======================================================================


def test_a_crash_maps_to_fail_app_crash_not_infra_failure():
    """`APP_CRASH` 是**用例失败**不是基础设施失败：App 崩了是 App 的账。

    ⚠️ 与 `test_dry_run.py` 的 `("INFRA_FAILURE", "WDA_FAILURE")` 那条是
    **两个方向**：那里没有崩溃报告（探测缺席/说没崩），这里探测到了。
    """
    from cli.pipeline import _infra_outcome
    status, ftype = _infra_outcome(InfraError("WDA 连不上"), _report())
    assert (status, ftype) == ("FAIL", "APP_CRASH")


def test_no_crash_report_keeps_wda_failure():
    from cli.pipeline import _infra_outcome
    status, ftype = _infra_outcome(InfraError("WDA 连不上"), None)
    assert (status, ftype) == ("INFRA_FAILURE", "WDA_FAILURE")


def test_infra_outcome_does_not_depend_on_the_exception_message():
    """判据是**崩溃报告**（观测），不是异常文本（措辞会变）。反钉：换一条
    完全不同的异常消息，结论只由报告的有无决定。"""
    from cli.pipeline import _infra_outcome
    for msg in ("WDA died", "Connection refused", "", "App crashed"):
        assert _infra_outcome(InfraError(msg), _report())[1] == "APP_CRASH"
        assert _infra_outcome(InfraError(msg), None)[1] == "WDA_FAILURE"


# ======================================================================
# 3. 崩溃的用例**不进** WDA 重跑路径
# ======================================================================


def test_a_crashed_testcase_does_not_enter_the_wda_rerun_path():
    """`_wda_aftermath` 会重启 WDA 再跑一遍——对已经崩掉的 App，重启 WDA
    不是处置（那是把 App 缺陷当抖动）。判据：`wda_died` 不置位。"""
    from cli.pipeline import _infra_detail
    detail = _infra_detail(InfraError("x"), _report())
    assert detail["wda_died"] is False
    assert detail["app_crash"]["signal"] == "SIGSEGV"
    # 旧行为：没有报告时照旧置位（重跑路径不变）
    assert _infra_detail(InfraError("x"), None)["wda_died"] is True
