"""Batch B / 设备侧证据采集（设计 §8.1 的 App/Crash Logs）。

**为什么先写这一层**：`crash_detected`（权重 5）今天**没有生产者**——
`diagnosis/classifier.py` 的判据是「步骤 `failure_type == "APP_CRASH"`」，
而全仓 `"APP_CRASH"` 只出现在测试里（勘察见 `docs/p3_data_audit.md` §8.3）。
App 真崩了，`cli/pipeline.py` 的 `except InfraError` 把它记成
`WDA_FAILURE`/`INFRA_FAILURE`——**App 缺陷被记成基础设施故障**。
崩溃报告是这条判定**唯一**可能的真数据源（设计 P1 §8.2：「App 运行状态 /
Appium session 状态 / **崩溃日志**」三条信号，第三条本仓从未实现）。

**F13（离设备）**：采集器吃「一个 `list[str] -> str` 的可调用对象」，
测试喂假文本。真实现（`subprocess`）只在 `default_runner` 里，测试**不碰**。
所以本文件全部在无模拟器、无 Appium 的机器上可跑。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from telemetry.collectors import (CrashCollector, CrashReport, LogLine,
                                  SyslogCollector, default_runner,
                                  parse_crash_report, parse_ndjson_logs)

# --- 替身：假 runner ----------------------------------------------------------


class _Runner:
    """记录被调用的命令并回放脚本输出（与 `fi_support.FakeExecutor` 同款）。"""

    def __init__(self, outputs: dict[str, str] | None = None,
                 default: str = ""):
        self.outputs = outputs or {}
        self.default = default
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(list(argv))
        for key, out in self.outputs.items():
            if key in " ".join(argv):
                return out
        return self.default


def _ndjson(**kw) -> str:
    row = {"timestamp": "2026-10-11 20:02:51.317342+0800",
           "messageType": "Default", "subsystem": "com.apple.runningboard",
           "category": "connection", "formatString": "Initializing connection",
           "eventMessage": "Initializing connection", "processID": 42}
    row.update(kw)
    return json.dumps(row)


def _ips(*, meta: dict | None = None, body: dict | None = None) -> str:
    """`.ips` 的**两段式**：第 1 行元数据 JSON，其余是正文 JSON。

    ⚠️ 整文件**不是**合法 JSON（`json.load` 直接解析会失败）——这是勘察
    §8.2 实测到的真坑，解析器必须按「首行 + 其余」切。
    """
    m = {"app_name": "LoginDemo", "timestamp": "2026-10-11 18:31:26.00 +0800",
         "bug_type": "309", "incident_id": "7444B058", "bundleID":
         "com.phaset0.logindemo", "os_version": "macOS 27.0.1"}
    m.update(meta or {})
    b = {"exception": {"type": "EXC_CRASH", "signal": "SIGSEGV"},
         "termination": {"namespace": "SIGNAL", "indicator":
                         "Segmentation fault: 11", "byProc": "LoginDemo"},
         "faultingThread": 0,
         "threads": [{"frames": [{"symbol": "mach_msg2_trap"},
                                 {"symbol": "LoginDemoApp.body.getter"}]}],
         "usedImages": [{"name": "LoginDemo"}]}
    b.update(body or {})
    return json.dumps(m) + "\n" + json.dumps(b)


# ======================================================================
# 1. syslog 解析（`simctl spawn <udid> log show --style ndjson`）
# ======================================================================


def test_parse_ndjson_logs_reads_the_fields_the_diagnosis_needs():
    lines = parse_ndjson_logs(_ndjson() + "\n" + _ndjson(
        messageType="Error", subsystem="com.demo.net",
        formatString="request failed: 500", eventMessage="request failed: 500"))
    assert len(lines) == 2
    first = lines[0]
    assert (first.subsystem, first.category) == ("com.apple.runningboard",
                                                 "connection")
    assert first.message == "Initializing connection"
    assert lines[1].level == "Error"


def test_parse_ndjson_logs_tolerates_the_trailing_noise_of_log_show():
    """`log show` 会往 stdout 掺非 JSON 行（进度/告警）。**跳过**而不是炸——
    但跳过要**可数**（`skipped`），否则「一行都没解析出来」看起来像「App
    什么都没打」。"""
    text = "\n".join([_ndjson(), "getpwuid_r did not find a match for uid 501",
                      "", _ndjson(messageType="Error")])
    lines = parse_ndjson_logs(text)
    assert len(lines) == 2
    assert parse_ndjson_logs(text, strict=True) is not None  # 严格模式也不炸


def test_parse_ndjson_logs_skips_a_line_that_starts_like_json_but_is_broken():
    """⚠️ **A/B 探针 B2 补的洞**：现有那条噪声测试（`getpwuid_r …`）**不以
    `{` 开头**，所以走的是 `startswith("{")` 守卫，`except JSONDecodeError`
    那条分支**从未被执行**——把 `json.loads` 的容错整个删掉，测试照样全绿。

    真正能走到那条分支的是「以 `{` 开头但坏」的行：截断的 JSON、日志里
    嵌的半行（`log show` 的输出被信号打断时就是这个形态）。
    """
    text = "\n".join([_ndjson(), '{"timestamp": "2026-10-11 20:00:00",',
                      '{"truncated"', _ndjson(messageType="Error")])
    lines = parse_ndjson_logs(text)
    assert len(lines) == 2, [ln.message for ln in lines]


def test_parse_ndjson_logs_on_empty_output_is_empty_not_an_error():
    """「没采到」与「采到零行」是两件事，本函数只回答后者。"""
    assert parse_ndjson_logs("") == []
    assert parse_ndjson_logs("not json at all\n") == []


# ======================================================================
# 2. 崩溃报告解析（`.ips` 两段式）
# ======================================================================


def test_parse_crash_report_reads_the_two_part_format():
    rep = parse_crash_report(_ips())
    assert isinstance(rep, CrashReport)
    assert rep.bundle_id == "com.phaset0.logindemo"
    assert rep.signal == "SIGSEGV"
    assert rep.exception_type == "EXC_CRASH"
    assert rep.incident_id == "7444B058"
    assert rep.top_frame == "mach_msg2_trap"


def test_parse_crash_report_does_not_treat_the_file_as_whole_json():
    """反钉：整文件**不是**合法 JSON。若实现写成 `json.loads(整份)`，这条会
    以 `JSONDecodeError` 炸——那是勘察 §8.2 实测过的真形态。"""
    with pytest.raises(json.JSONDecodeError):
        json.loads(_ips())
    assert parse_crash_report(_ips()).bundle_id == "com.phaset0.logindemo"


def test_parse_crash_report_refuses_a_malformed_file():
    """坏报告 fail-loud（不返回一个字段全空的 `CrashReport`——那会让
    「解析失败」看起来像「崩了个没信息的崩溃」）。"""
    with pytest.raises(ValueError):
        parse_crash_report("{}")
    with pytest.raises(ValueError):
        parse_crash_report("")


def test_parse_crash_report_tolerates_a_report_without_threads():
    """有些报告（如 watchdog / 内存）没有 `threads`——`top_frame` 空串，
    但**其余字段照给**（缺线程不是「没崩溃」）。"""
    rep = parse_crash_report(_ips(body={"threads": [], "faultingThread": None}))
    assert rep.top_frame == ""
    assert rep.signal == "SIGSEGV"


# ======================================================================
# 3. SyslogCollector
# ======================================================================


def test_syslog_collector_builds_the_simulator_command():
    runner = _Runner(default=_ndjson())
    col = SyslogCollector(runner=runner, udid="UDID-1", process="LoginDemo")
    lines = col.since(seconds=300)
    assert len(lines) == 1
    argv = runner.calls[0]
    assert argv[:3] == ["xcrun", "simctl", "spawn"]
    assert "UDID-1" in argv and "ndjson" in " ".join(argv)
    assert "process == \"LoginDemo\"" in " ".join(argv)


def test_syslog_collector_requires_a_udid():
    """没有 udid 就没有范围——不猜「booted」（多设备时猜错是静默的）。"""
    with pytest.raises(ValueError):
        SyslogCollector(runner=_Runner(), udid="", process="LoginDemo")


# ======================================================================
# 4. CrashCollector
# ======================================================================


def _write_ips(directory: Path, name: str, *, meta=None, body=None) -> Path:
    p = directory / name
    p.write_text(_ips(meta=meta, body=body), encoding="utf-8")
    return p


def test_crash_collector_finds_the_latest_report_for_the_bundle(tmp_path):
    _write_ips(tmp_path, "LoginDemo-2026-10-11-180000.ips",
               meta={"incident_id": "OLD"})
    _write_ips(tmp_path, "LoginDemo-2026-10-11-190000.ips",
               meta={"incident_id": "NEW"})
    # 另一个 App 的报告：必须被过滤掉（按 bundleID，不是按文件名）
    _write_ips(tmp_path, "OtherApp-2026-10-11-200000.ips",
               meta={"bundleID": "com.other.app", "incident_id": "OTHER"})
    col = CrashCollector(reports_dir=tmp_path)
    rep = col.latest("com.phaset0.logindemo")
    assert rep.incident_id == "NEW"


def test_crash_collector_returns_none_when_nothing_crashed(tmp_path):
    """**没有崩溃报告 = 没崩**——这是 `crash_detected` 的否定证据，必须是
    一个干净的 `None`，不是异常（「没崩」是常态，不是错误）。"""
    col = CrashCollector(reports_dir=tmp_path)
    assert col.latest("com.phaset0.logindemo") is None


def test_crash_collector_filters_by_time_window(tmp_path):
    """`since` 是**必需的过滤**：不按时间收窄会把上一次 run 的旧崩溃当成
    这一次的（`crash_detected` 权重 5，误报代价高）。"""
    _write_ips(tmp_path, "LoginDemo-2026-10-11-100000.ips",
               meta={"incident_id": "OLD", "timestamp": "2026-10-11 10:00:00.00 +0800"})
    _write_ips(tmp_path, "LoginDemo-2026-10-11-200000.ips",
               meta={"incident_id": "NEW", "timestamp": "2026-10-11 20:00:00.00 +0800"})
    col = CrashCollector(reports_dir=tmp_path)
    since = datetime(2026, 10, 11, 12, 0, tzinfo=timezone(timedelta(hours=8)))
    assert col.latest("com.phaset0.logindemo", since=since).incident_id == "NEW"
    since2 = datetime(2026, 10, 11, 21, 0, tzinfo=timezone(timedelta(hours=8)))
    assert col.latest("com.phaset0.logindemo", since=since2) is None


def test_crash_collector_skips_a_malformed_report_instead_of_dying(tmp_path):
    """一个坏文件不该让整次采集失败（崩溃报告是**旁路证据**，它的解析失败
    不能让 run 本身失败）——跳过它，其余照常。"""
    (tmp_path / "LoginDemo-broken.ips").write_text("not a report")
    _write_ips(tmp_path, "LoginDemo-2026-10-11-190000.ips",
               meta={"incident_id": "GOOD"})
    col = CrashCollector(reports_dir=tmp_path)
    assert col.latest("com.phaset0.logindemo").incident_id == "GOOD"


def test_crash_collector_on_a_missing_directory_is_none(tmp_path):
    """目录不存在（真机路径 / 干净机器）= 没崩，不是错误。"""
    col = CrashCollector(reports_dir=tmp_path / "nope")
    assert col.latest("com.phaset0.logindemo") is None


def test_crash_collector_prefers_the_newest_when_timestamps_tie(tmp_path):
    _write_ips(tmp_path, "LoginDemo-a.ips", meta={"incident_id": "A",
               "timestamp": "2026-10-11 20:00:00.00 +0800"})
    _write_ips(tmp_path, "LoginDemo-b.ips", meta={"incident_id": "B",
               "timestamp": "2026-10-11 20:00:00.00 +0800"})
    col = CrashCollector(reports_dir=tmp_path)
    # 平手时按文件名字典序取**最后**一个（确定性，不是文件系统顺序）
    assert col.latest("com.phaset0.logindemo").incident_id == "B"


# ======================================================================
# 5. 默认 runner（真实现的唯一落点；本文件不调它）
# ======================================================================


def test_default_runner_raises_when_the_command_exits_nonzero(monkeypatch):
    """⚠️ **A/B 探针 B6 补的洞**：下面那条测试让 `subprocess.run` **抛**
    `CalledProcessError`，于是走的是 `except SubprocessError` 分支——
    `returncode != 0` 那条**从未被执行**。而真实形态恰恰是后者：
    `check=False` 时 `run` **正常返回**一个非零 `returncode`，
    命令挂了不抛异常。

    （`CalledProcessError` 是 `SubprocessError` 的子类，所以两条路都存在。）
    """
    import subprocess

    class _Proc:
        returncode = 3
        stdout = ""
        stderr = "xcrun: error: unable to find utility \"simctl\""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Proc())
    with pytest.raises(RuntimeError) as e:
        default_runner(["xcrun", "simctl", "list"])
    assert "exit 3" in str(e.value)
    assert "simctl" in str(e.value)      # stderr 要进错误消息（可诊断）


def test_default_runner_raises_on_a_failing_command(monkeypatch):
    """采集失败必须 fail-loud（返回空串会让「命令挂了」读成「没有日志」）。"""
    import subprocess
    def _boom(*a, **kw):
        raise subprocess.CalledProcessError(1, "xcrun")
    monkeypatch.setattr(subprocess, "run", _boom)
    with pytest.raises(RuntimeError):
        default_runner(["xcrun", "simctl", "list"])
