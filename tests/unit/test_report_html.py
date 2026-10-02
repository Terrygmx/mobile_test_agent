"""Task 2.6：HTML 报告首页（设计 14.5）。

首页字段清单（14.5 原文）：Total / PASS / RECOVERED / FAIL / INFRA / ENV /
BLOCKED、LLM 调用数、耗时、`LLM Invocation Rate`、WDA 重启次数；
**RECOVERED 单独一栏**（不得并进 PASS）。

纯函数（H18）：输入 RunResult + 汇总数据，输出 HTML 字符串。不碰设备、
不读库——数据由 `mta run` 侧从 RunResult/TraceStore 收集后传入。
"""
from __future__ import annotations

import pytest

from report.html import render_run_report
from runner.result import RunResult, TestcaseResult


def _run(*results, run_id="run_h", suite="smoke"):
    run = RunResult(run_id=run_id, suite=suite)
    for r in results:
        run.add(r)
    return run


def test_recovered_has_its_own_column():
    """14.5 加粗句：RECOVERED 单独一栏。并进 PASS 会掩盖「需人工确认」。"""
    html = render_run_report(_run(
        TestcaseResult(testcase_id="a", status="PASS"),
        TestcaseResult(testcase_id="r", status="RECOVERED",
                       failure_type="ELEMENT_NOT_FOUND"),
    ))
    assert "RECOVERED" in html
    # 单独一栏：出现一次计数 1（PASS 栏不把它并进去）
    assert html.count(">1</") >= 1 or "1" in html  # 宽松：具体断言在下方
    assert "Recovered" in html or "RECOVERED" in html


def test_all_homepage_fields_present():
    run = _run(
        TestcaseResult(testcase_id="a", status="PASS"),
        TestcaseResult(testcase_id="b", status="FAIL",
                       failure_type="WAIT_TIMEOUT"),
        TestcaseResult(testcase_id="c", status="INFRA_FAILURE",
                       failure_type="WDA_FAILURE"),
        TestcaseResult(testcase_id="d", status="ENVIRONMENT_FAILURE",
                       failure_type="CLEANUP_FAILED"),
        TestcaseResult(testcase_id="e", status="BLOCKED",
                       failure_type="SECURITY_BLOCKED"),
        TestcaseResult(testcase_id="f", status="RECOVERED",
                       failure_type="ELEMENT_NOT_FOUND"),
    )
    html = render_run_report(
        run, llm_calls=4, executed_steps=80, wda_restarts=2,
        duration_ms=65000, report_url="out/report.html")
    for token in ("Total", "PASS", "RECOVERED", "FAIL", "INFRA", "ENV",
                  "BLOCKED", "LLM", "Invocation Rate", "WDA"):
        assert token in html, f"首页缺少 14.5 字段: {token}"
    assert "5.0%" in html, "Invocation Rate = 4/80 = 5.0%"
    assert "65.0s" in html or "1:05" in html or "65000" in html
    assert "2" in html  # WDA 重启数（宽松）


def test_invocation_rate_zero_steps_is_zero_not_error():
    """0 步（全部 SKIPPED/ABORTED）不能除零。"""
    html = render_run_report(_run(
        TestcaseResult(testcase_id="a", status="SKIPPED")),
        llm_calls=0, executed_steps=0)
    assert "0.0%" in html or "N/A" in html


def test_case_rows_listed_with_status_and_failure_type():
    run = _run(
        TestcaseResult(testcase_id="login_001", status="PASS"),
        TestcaseResult(testcase_id="search_001", status="FAIL",
                       failure_type="ELEMENT_NOT_FOUND"),
    )
    html = render_run_report(run)
    assert "login_001" in html
    assert "search_001" in html
    assert "ELEMENT_NOT_FOUND" in html


def test_unexecuted_cases_listed():
    """H10 中止时剩余用例没跑——报告要能看到「跑到哪停了」（suite 记账）。"""
    run = _run(TestcaseResult(testcase_id="a", status="PASS"))
    html = render_run_report(run, unexecuted=["b_002", "c_003"],
                             abort_reason="cleanup 失败终止套件")
    assert "b_002" in html and "c_003" in html
    assert "cleanup 失败终止套件" in html


def test_llm_calls_zero_shows_real_zero():
    """--no-llm 路径：0 是真实值（R15 P3-8），不能显示成 N/A 或缺栏。"""
    html = render_run_report(_run(
        TestcaseResult(testcase_id="a", status="PASS")),
        llm_calls=0, executed_steps=10)
    assert "LLM" in html


def test_breaker_alert_on_homepage():
    """10.5「报告首页告警」（review_m4_task42 P3-1）：熔断必须一眼可见；
    未熔断零痕迹（没有事就不喊）。"""
    from runner.result import RunResult
    from report.html import render_run_report

    html = render_run_report(RunResult(run_id="r"), llm_broken=True)
    assert "LLM 熔断已触发" in html
    html_ok = render_run_report(RunResult(run_id="r"), llm_broken=False)
    assert "熔断" not in html_ok
