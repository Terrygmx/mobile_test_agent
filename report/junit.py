"""report.junit — 8.5 JUnit XML 序列化。

映射规则已在 `runner.result.junit_status_for` 钉住并测试；这里只做序列化：
RunResult → 合法 JUnit XML。CI（Jenkins/GitLab）按 testsuite 的
`tests/failures/errors/skipped` 计数 + `<failure>/<error>` 子元素判读。

8.5：`system-out` 附 failure_type、恢复摘要、报告链接。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from runner.result import RunResult, TestcaseResult

__all__ = ["junit_xml", "write_junit"]


def _tc_counts(run: RunResult) -> tuple[int, int, int, int]:
    failures = errors = skipped = 0
    for r in run.results:
        j = r.junit.status
        if j == "failure":
            failures += 1
        elif j == "error":
            errors += 1
        elif j == "skipped":
            skipped += 1
    return len(run.results), failures, errors, skipped


def _system_out_text(run: RunResult, report_url: str | None) -> str | None:
    """8.5：failure_type、恢复摘要、报告链接。全无内容时不输出该节点。

    设计 10（Task 2.4）：恢复摘要里加一层分类（RECOVERED_LLM /
    RECOVERED_EXPERIENCE / RECOVERED_ASSERTION_TARGET）——CI 的 system-out
    是排障第一现场，「这次是 LLM 救的还是经验救的」在那里就要看得见。
    """
    lines: list[str] = []
    for r in run.results:
        if r.status == "PASS":
            continue
        line = f"[{r.testcase_id}] status={r.status}"
        if r.failure_type:
            line += f" failure_type={r.failure_type}"
        if r.failure_phase:
            line += f" phase={r.failure_phase}"
        labels = r.detail.get("recovered_kinds")
        if labels:
            line += f" recovered_kind={','.join(labels)}"
        lines.append(line)
        recovery = r.detail.get("recovery")
        if recovery:
            lines.append(f"[{r.testcase_id}] recovery: {recovery}")
    if report_url:
        lines.append(f"report: {report_url}")
    return "\n".join(lines) if lines else None


def junit_xml(run: RunResult, report_url: str | None = None) -> str:
    """RunResult → JUnit XML 字符串（8.5）。

    未知状态由 `junit_status_for` 在 `r.junit` 里抛 ValueError（H2 fail-loud），
    这里不吞。
    """
    total, failures, errors, skipped = _tc_counts(run)
    suite_name = run.suite or run.run_id
    ts = ET.Element("testsuite", {
        "name": suite_name,
        "tests": str(total),
        "failures": str(failures),
        "errors": str(errors),
        "skipped": str(skipped),
    })
    for r in run.results:
        j = r.junit
        tc = ET.SubElement(ts, "testcase", {
            "name": r.testcase_id,
            "classname": suite_name,
            # JUnit time 是秒；duration_ms 是 int 毫秒
            "time": f"{(r.duration_ms or 0) / 1000:.3f}",
        })
        if j.status == "failure":
            ET.SubElement(tc, "failure", {
                "type": j.type or r.status,
                "message": j.message or r.failure_type or r.status,
            })
        elif j.status == "error":
            ET.SubElement(tc, "error", {
                "type": j.type or r.failure_type or r.status,
                "message": j.message or r.failure_type or r.status,
            })
        elif j.status == "skipped":
            ET.SubElement(tc, "skipped", {
                "message": r.failure_type or r.status,
            })
        # pass：无子元素
    so_text = _system_out_text(run, report_url)
    if so_text:
        so = ET.SubElement(ts, "system-out")
        so.text = so_text
    return ET.tostring(_wrap(ts), encoding="unicode")


def _wrap(ts: ET.Element) -> ET.Element:
    root = ET.Element("testsuites")
    root.append(ts)
    return root


def write_junit(run: RunResult, path: str | Path,
                report_url: str | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(junit_xml(run, report_url=report_url), encoding="utf-8")
    return path
