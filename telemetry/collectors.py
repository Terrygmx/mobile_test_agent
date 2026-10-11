"""collectors.py — 设备侧证据采集（设计 §8.1 / P1 §8.2；Batch B）。

## 为什么存在：`crash_detected`（权重 5）今天没有生产者

勘察结论（`docs/p3_data_audit.md` §8.3，全仓 grep 实测）：

- `diagnosis/classifier.py::crash_detected` 的判据之一是「步骤
  `failure_type == "APP_CRASH"`」；
- 全仓 `"APP_CRASH"` 只出现在**测试**里。生产侧唯一相关的落点是
  `cli/pipeline.py` 的 `except InfraError` 分支——它写的是
  `WDA_FAILURE` + status `INFRA_FAILURE`。

即 **App 真崩了，落库的分类是「基础设施失败」**。设计 P1 §8.2 对 `APP_CRASH`
的规定是「通过确定性信号判定（App 运行状态 / Appium session 状态 /
**崩溃日志**），不得当作 `ELEMENT_NOT_FOUND`」——第三条信号本仓从未实现。
本模块补的就是它。

## 两条边界

1. **离设备（F13）**：`SyslogCollector` / `CrashCollector` 吃注入的 `runner`
   （`list[str] -> str`）。真实现 `default_runner` 是**唯一**碰 `subprocess`
   的地方，测试不调它。故本模块在无模拟器、无 Appium 的机器上全绿。
2. **旁路证据不推翻主结论**：采集失败**不能**让 run 本身失败。所以
   `CrashCollector` 跳过坏报告文件、`latest()` 在「目录不存在 / 没崩过」时
   返回 `None`（**「没崩」是常态，不是错误**）；只有**命令本身失败**才
   fail-loud（`default_runner` 抛 `RuntimeError`）——那与「命令成功但没输出」
   是两件事，混起来会让「采集器挂了」读成「App 没报错」。

## 口径：`since` 是必需的时间窗

`crash_detected` 权重 5，误报代价高。不按时间收窄就会把**上一次 run 的旧崩溃**
当成这一次的。故 `latest()` 的 `since` 不是可选优化。
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

__all__ = ["CrashCollector", "CrashReport", "LogLine", "SyslogCollector",
           "default_runner", "parse_crash_report", "parse_ndjson_logs"]

# 采集器的取数口：吃 argv、回原始文本。真实现是 `default_runner`；
# 测试注入替身（F13：本模块不直接 import subprocess 之外的东西）。
Runner = Callable[[list[str]], str]

# 模拟器崩溃报告的宿主落点（勘察 §8.2 实测：在**宿主**目录，不在设备内）。
DEFAULT_CRASH_DIR = Path.home() / "Library" / "Logs" / "DiagnosticReports"


def default_runner(argv: list[str]) -> str:
    """真实现：跑一条命令，回 stdout。

    **失败 fail-loud**（`RuntimeError`）——不返回空串：返回空串会把
    「命令挂了 / 没有 xcrun」读成「App 没有日志」，而后者是一个**证据性结论**
    （`crash_detected` 靠它说「没崩」）。这两件事必须分开。
    """
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        # OSError：可执行文件不存在（真机路径缺 idevicesyslog）；
        # SubprocessError：子进程层的失败。两者都**不是**「App 没日志」。
        raise RuntimeError(f"采集命令起不来：{argv[0]}: {e}") from None
    if proc.returncode != 0:
        raise RuntimeError(
            f"采集命令失败（exit {proc.returncode}）：{' '.join(argv)}"
            f"｜stderr: {proc.stderr.strip()[:400]}")
    return proc.stdout


# ======================================================================
# syslog
# ======================================================================


@dataclass(frozen=True)
class LogLine:
    """一条 syslog 行（只留诊断用得上的字段）。"""

    timestamp: str
    level: str
    subsystem: str
    category: str
    message: str
    process_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"timestamp": self.timestamp, "level": self.level,
                "subsystem": self.subsystem, "category": self.category,
                "message": self.message, "process_id": self.process_id}


def parse_ndjson_logs(text: str, *, strict: bool = False) -> list[LogLine]:
    """解析 `log show --style ndjson` 的输出。

    ⚠️ **必须容错**：`log show` 会往 stdout 掺非 JSON 行（实测有
    `getpwuid_r did not find a match for uid 501`）。**跳过**它们而不是炸
    ——否则一条无关的告警会让整次采集失败。`strict=True` 目前与 `False`
    同行为（保留参数位，供将来「掺了非 JSON 行要报警」的场景）。

    空输出 → 空列表：**「没采到」与「采到零行」是两件事**，本函数只回答
    后者（前者由 `default_runner` 的 fail-loud 回答）。
    """
    out: list[LogLine] = []
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw or not raw.startswith("{"):
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        msg = row.get("eventMessage") or row.get("formatString") or ""
        out.append(LogLine(
            timestamp=str(row.get("timestamp") or ""),
            level=str(row.get("messageType") or "Default"),
            subsystem=str(row.get("subsystem") or ""),
            category=str(row.get("category") or ""),
            message=str(msg),
            process_id=row.get("processID")))
    return out


class SyslogCollector:
    """模拟器 syslog 采集（`xcrun simctl spawn <udid> log show …`）。

    真机（非模拟器）要走 `idevicesyslog`（`libimobiledevice`）——本机未安装，
    故真机路径**不是**「自动回落」，而是显式选择（`runner=` 注入）。
    """

    def __init__(self, *, runner: Runner, udid: str, process: str,
                 style: str = "ndjson") -> None:
        if not udid:
            raise ValueError(
                "udid 必填——没有范围就没有采集（不猜 `booted`：多设备时"
                "猜错是静默的，会把别台设备的日志当成这一台的）")
        if not process:
            raise ValueError("process 必填——全量 syslog 会淹没诊断")
        self.runner = runner
        self.udid = udid
        self.process = process
        self.style = style

    def command(self, *, seconds: int) -> list[str]:
        return ["xcrun", "simctl", "spawn", self.udid, "log", "show",
                "--last", f"{int(seconds)}s", "--style", self.style,
                "--predicate", f'process == "{self.process}"']

    def since(self, *, seconds: int = 300) -> list[LogLine]:
        return parse_ndjson_logs(self.runner(self.command(seconds=seconds)))


# ======================================================================
# 崩溃报告
# ======================================================================


@dataclass(frozen=True)
class CrashReport:
    """一份解析好的崩溃报告（`.ips`）。"""

    path: str
    bundle_id: str
    timestamp: str
    incident_id: str
    exception_type: str
    signal: str
    termination: str
    faulting_thread: int | None
    top_frame: str

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "bundle_id": self.bundle_id,
                "timestamp": self.timestamp, "incident_id": self.incident_id,
                "exception_type": self.exception_type, "signal": self.signal,
                "termination": self.termination,
                "faulting_thread": self.faulting_thread,
                "top_frame": self.top_frame}


def parse_crash_report(text: str) -> CrashReport:
    """解析 `.ips`——**两段式**：第 1 行元数据 JSON，其余是正文 JSON。

    ⚠️ 整文件**不是**合法 JSON（勘察 §8.2 实测：`json.load(整份)` 抛
    `JSONDecodeError`）。按「首行 + 其余」切是唯一正确的读法。

    坏报告 **fail-loud**（`ValueError`）：返回一个字段全空的 `CrashReport`
    会让「解析失败」看起来像「崩了个没信息的崩溃」。
    """
    if not text.strip():
        raise ValueError("空内容不是崩溃报告")
    head, _, rest = text.partition("\n")
    try:
        meta = json.loads(head)
        body = json.loads(rest)
    except json.JSONDecodeError as e:
        raise ValueError(f"不是 .ips 两段式格式：{e}") from None
    if not isinstance(meta, dict) or not isinstance(body, dict):
        raise ValueError("崩溃报告的两段都必须是 JSON 对象")

    exc = body.get("exception") or {}
    term = body.get("termination") or {}
    ft = body.get("faultingThread")
    top = ""
    threads = body.get("threads") or []
    if isinstance(ft, int) and 0 <= ft < len(threads):
        frames = (threads[ft] or {}).get("frames") or []
        if frames:
            top = str((frames[0] or {}).get("symbol") or "")
    return CrashReport(
        path="", bundle_id=str(meta.get("bundleID") or ""),
        timestamp=str(meta.get("timestamp") or ""),
        incident_id=str(meta.get("incident_id") or ""),
        exception_type=str(exc.get("type") or ""),
        signal=str(exc.get("signal") or ""),
        termination=str(term.get("indicator") or ""),
        faulting_thread=ft if isinstance(ft, int) else None,
        top_frame=top)


class CrashCollector:
    """崩溃报告采集（宿主 `~/Library/Logs/DiagnosticReports/*.ips`）。"""

    def __init__(self, *, reports_dir: str | Path = DEFAULT_CRASH_DIR) -> None:
        self.reports_dir = Path(reports_dir)

    def _candidates(self) -> list[Path]:
        if not self.reports_dir.is_dir():
            return []
        return sorted(self.reports_dir.glob("*.ips"))

    def latest(self, bundle_id: str, *,
               since: datetime | None = None) -> CrashReport | None:
        """该 bundle 最近一份崩溃报告；没有 → `None`。

        - **按 `bundleID` 过滤，不按文件名**（文件名是 App 显示名，可改）；
        - `since` 是**必需的时间窗**（见模块 docstring 的口径）；
        - **坏报告跳过**，不让一个坏文件废掉整次采集。
        """
        best: CrashReport | None = None
        best_key: tuple[str, str] | None = None
        for path in self._candidates():
            try:
                rep = replace(parse_crash_report(path.read_text(encoding="utf-8")),
                              path=str(path))
            except (ValueError, OSError, UnicodeDecodeError):
                continue
            if rep.bundle_id != bundle_id:
                continue
            if since is not None and not _is_after(rep.timestamp, since):
                continue
            # 平手时按文件名字典序取最后一个（确定性，不依赖文件系统顺序）
            key = (rep.timestamp, path.name)
            if best_key is None or key > best_key:
                best, best_key = rep, key
        return best          # `path` 在解析时已回填（见上）


def _is_after(timestamp: str, since: datetime) -> bool:
    """`"2026-10-11 18:31:26.00 +0800"` 是否晚于 `since`。

    解析失败 → **False**（收窄掉而不是放进来）：无法判断时间的报告不该被
    当成「这一次的崩溃」（权重 5，误报代价高）。
    """
    try:
        ts = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S.%f %z")
    except (ValueError, TypeError):
        return False
    return ts >= since
