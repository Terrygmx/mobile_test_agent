"""Batch B：把「WDA 死了」与「App 死了」分开（设计 P1 §8.2 的第三条信号）。

## 缺陷

`cli/pipeline.py` 的 `except InfraError` 把**一切**基础设施异常记成
`WDA_FAILURE` + status `INFRA_FAILURE`。App 真崩了也走这条路 → 落库分类是
「基础设施故障」→ `diagnosis/classifier.py::crash_detected`（权重 5）返回
False → 归因指向环境而不是 App。**这是把 App 缺陷记成环境缺陷**，方向反了。

设计 P1 §8.2 对 `APP_CRASH` 的规定是三条信号：「App 运行状态 / Appium session
状态 / **崩溃日志**」。第三条本仓从未实现——本文件测它的落点。

## 口径（每条都有反钉）

1. **探测失败不改结论**：探不到（目录不存在 / 没崩过）→ 保持旧行为
   （`WDA_FAILURE` / `INFRA_FAILURE`）。旁路证据**不能**把不确定变成「崩了」。
2. **没注入探测 = 旧行为**（`crash_probe=None`）：既有调用点零改动，
   P1/P2/P3 全量回归是这次接线的验收标准。
3. **App 崩溃不触发 WDA 重跑**：`_wda_aftermath` 会重启 WDA 再跑一遍——对一个
   已经崩掉的 App，重启 WDA 不是处置（那是把 App 缺陷当成抖动）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from telemetry.collectors import CrashReport
from telemetry.probe import CrashProbe


def _report(incident: str = "INC-1", *, ts="2026-10-11 20:00:00.00 +0800"):
    return CrashReport(path=f"/x/{incident}.ips", bundle_id="com.demo",
                       timestamp=ts, incident_id=incident,
                       exception_type="EXC_CRASH", signal="SIGSEGV",
                       termination="Segmentation fault: 11",
                       faulting_thread=0, top_frame="main")


class _FakeCollector:
    def __init__(self, report=None):
        self.report = report
        self.calls = []

    def latest(self, bundle_id, *, since=None):
        self.calls.append((bundle_id, since))
        return self.report


# ======================================================================
# 1. CrashProbe
# ======================================================================


def test_probe_reports_a_crash_found_after_the_run_started():
    col = _FakeCollector(_report())
    probe = CrashProbe(collector=col, bundle_id="com.demo")
    started = datetime(2026, 10, 11, 19, 0, tzinfo=timezone(timedelta(hours=8)))
    rep = probe.crashed_since(started)
    assert rep is not None and rep.incident_id == "INC-1"
    # 时间窗必须**下推到 collector**（收窄在取数处做，不在拿到之后筛）
    assert col.calls == [("com.demo", started)]


def test_probe_returns_none_when_nothing_crashed():
    probe = CrashProbe(collector=_FakeCollector(None), bundle_id="com.demo")
    assert probe.crashed_since(datetime.now(timezone.utc)) is None


def test_probe_requires_a_bundle_id():
    with pytest.raises(ValueError):
        CrashProbe(collector=_FakeCollector(), bundle_id="")


def test_probe_swallows_collector_errors_into_none():
    """**旁路证据不许推翻主结论**：探测自己坏了 → `None`（= 按旧行为记
    `WDA_FAILURE`），不是把异常冒到 run 上让整次回归失败。"""
    class _Boom:
        def latest(self, bundle_id, *, since=None):
            raise OSError("诊断目录权限不对")
    probe = CrashProbe(collector=_Boom(), bundle_id="com.demo")
    assert probe.crashed_since(datetime.now(timezone.utc)) is None


def test_probe_does_not_block_forever_when_the_report_is_slow():
    """崩溃报告是**异步**写的（勘察 §8.2 实测：SIGSEGV 后 6~8s 才落盘）。
    探测要等，但**必须有界**——否则一次崩溃把回归挂在无限轮询上。"""
    class _Later:
        def __init__(self):
            self.n = 0
        def latest(self, bundle_id, *, since=None):
            self.n += 1
            return _report() if self.n >= 3 else None
    slept = []
    probe = CrashProbe(collector=_Later(), bundle_id="com.demo",
                       wait_seconds=5.0, poll_seconds=0.5,
                       sleep=slept.append)
    rep = probe.crashed_since(datetime.now(timezone.utc))
    assert rep is not None
    assert len(slept) == 2          # 等两次就命中，不把 5s 睡满


def test_probe_gives_up_after_the_deadline():
    slept = []
    probe = CrashProbe(collector=_FakeCollector(None), bundle_id="com.demo",
                       wait_seconds=1.0, poll_seconds=0.5,
                       sleep=slept.append)
    assert probe.crashed_since(datetime.now(timezone.utc)) is None
    assert len(slept) == 2          # 有界：0.5 + 0.5 到 1.0 就停


def test_probe_with_zero_wait_does_a_single_shot():
    col = _FakeCollector(None)
    probe = CrashProbe(collector=col, bundle_id="com.demo", wait_seconds=0)
    probe.crashed_since(datetime.now(timezone.utc))
    assert len(col.calls) == 1      # 不等，只问一次（测试/离线路径用）
