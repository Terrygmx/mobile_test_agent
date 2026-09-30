"""Task 2.6：JUnit XML 输出（8.5）。

`runner.result.junit_status_for` 已钉住映射规则；本模块测的是**序列化**：
RunResult/TestcaseResult → 合法 JUnit XML 文件。CI（Jenkins/GitLab）按
`tests/failures/errors/skipped` 计数与 `<failure>/<error>` 子元素判读。

8.5 硬要求：
  - RECOVERED → `<failure type="RECOVERED_NEEDS_REVIEW">`（CI 必须能看见）；
  - INFRA/ENV/BLOCKED → `<error>`；
  - `system-out` 附 failure_type、恢复摘要、报告链接。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from report.junit import junit_xml, write_junit
from runner.result import TestcaseResult


def _run(*results, run_id="run_t26", suite="smoke"):
    from runner.result import RunResult
    run = RunResult(run_id=run_id, suite=suite)
    for r in results:
        run.add(r)
    return run


def _parse(run, **kw):
    xml_text = junit_xml(run, **kw)
    return ET.fromstring(xml_text)


# --- 基本结构 ---


def test_empty_run_is_valid_xml_with_zero_counts():
    root = _parse(_run())
    ts = root.find("testsuite")
    assert ts is not None
    assert int(ts.get("tests")) == 0
    assert int(ts.get("failures")) == 0


def test_counts_match_junit_status_not_raw_status():
    """tests/failures/errors/skipped 按 8.5 映射后的 JUnit 状态计数——
    RECOVERED 算 failure，不算 pass；ABORTED 算 error。"""
    run = _run(
        TestcaseResult(testcase_id="a", status="PASS"),
        TestcaseResult(testcase_id="b", status="FAIL",
                       failure_type="ELEMENT_NOT_FOUND"),
        TestcaseResult(testcase_id="c", status="RECOVERED",
                       failure_type="ELEMENT_NOT_FOUND"),
        TestcaseResult(testcase_id="d", status="INFRA_FAILURE",
                       failure_type="WDA_FAILURE"),
        TestcaseResult(testcase_id="e", status="SKIPPED"),
    )
    ts = _parse(run).find("testsuite")
    assert int(ts.get("tests")) == 5
    assert int(ts.get("failures")) == 2, "FAIL + RECOVERED 都是 failure"
    assert int(ts.get("errors")) == 1
    assert int(ts.get("skipped")) == 1


# --- 8.5 映射：子元素与 type ---


def test_pass_has_no_child_element():
    root = _parse(_run(TestcaseResult(testcase_id="a", status="PASS")))
    tc = root.find(".//testcase")
    assert tc.get("name") == "a"
    assert list(tc) == []


def test_recovered_is_failure_with_review_type():
    root = _parse(_run(TestcaseResult(
        testcase_id="r", status="RECOVERED",
        failure_type="ELEMENT_NOT_FOUND")))
    tc = root.find(".//testcase")
    failures = tc.findall("failure")
    assert len(failures) == 1
    assert failures[0].get("type") == "RECOVERED_NEEDS_REVIEW"
    # H5：RECOVERED 的 message 必须说清「需人工确认」
    assert "人工确认" in (failures[0].get("message") or "")


def test_fail_and_error_types():
    run = _run(
        TestcaseResult(testcase_id="f", status="FAIL",
                       failure_type="WAIT_TIMEOUT"),
        TestcaseResult(testcase_id="e", status="ENVIRONMENT_FAILURE",
                       failure_type="CLEANUP_FAILED"),
        TestcaseResult(testcase_id="b", status="BLOCKED",
                       failure_type="SECURITY_BLOCKED"),
    )
    by_name = {tc.get("name"): tc for tc in root_iter(run)}
    assert by_name["f"].find("failure").get("type") == "WAIT_TIMEOUT"
    assert by_name["e"].find("error").get("type") == "CLEANUP_FAILED"
    assert by_name["b"].find("error").get("type") == "SECURITY_BLOCKED"


def root_iter(run):
    return _parse(run).iter("testcase")


# --- system-out（8.5） ---


def test_system_out_carries_failure_type_and_recovery_summary():
    run = _run(
        TestcaseResult(testcase_id="a", status="FAIL",
                       failure_type="ELEMENT_NOT_FOUND"),
        TestcaseResult(testcase_id="r", status="RECOVERED",
                       failure_type="ELEMENT_NOT_FOUND",
                       detail={"recovery": "retry once"}),
    )
    so = _parse(run, report_url="file:///out/report.html").find(
        "testsuite/system-out")
    text = (so.text or "") if so is not None else ""
    assert "ELEMENT_NOT_FOUND" in text, "failure_type 必须出现在 system-out"
    assert "retry once" in text, "恢复摘要必须出现在 system-out"
    assert "file:///out/report.html" in text, "报告链接（8.5）"


def test_system_out_omitted_when_nothing_to_report():
    run = _run(TestcaseResult(testcase_id="a", status="PASS"))
    root = _parse(run)
    so = root.find("testsuite/system-out")
    assert so is None or not (so.text or "").strip()


# --- 写文件 ---


def test_write_junit_creates_file(tmp_path):
    run = _run(TestcaseResult(testcase_id="a", status="FAIL",
                              failure_type="WAIT_TIMEOUT"))
    out = tmp_path / "sub" / "report.xml"
    write_junit(run, out)
    root = ET.parse(out).getroot()
    assert root.find(".//testcase").get("name") == "a"


def test_duration_ms_lands_in_time_attribute():
    run = _run(TestcaseResult(testcase_id="a", status="PASS",
                              duration_ms=1500))
    tc = _parse(run).find(".//testcase")
    assert tc.get("time") == "1.500"


def test_unknown_status_still_raises():
    with pytest.raises(ValueError):
        junit_xml(_run(TestcaseResult(testcase_id="x", status="NOPE")))
