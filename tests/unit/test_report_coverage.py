"""report.html Source Coverage 报告页测试（12.7 / Task 3.2）。

只测渲染纯函数：报告页必须把五个分桶都显示出来（visible 但不算失败的口径
不能因为「不失败」就从报告里消失），且所有插值文本走 _esc。
"""
from __future__ import annotations

from report.html import render_source_coverage_report, write_source_coverage_report
from source.coverage import CoverageReport


def _report(**kw) -> CoverageReport:
    base: dict = dict(
        total=4, resolved=2, dynamic=1, unknown=0, ambiguous=0,
        missing=1, occurrences=9,
        resolved_refs=(("HomeView", "go_profile"),),
        dynamic_refs=(("HomeView", "cell_alpha"),),
        missing_refs=(("LoginView", "ghost"),),
        screens_total=2, screens_resolved=1,
        screens_missing=("GhostView",),
        cases_by_ref={"HomeView.cell_alpha": ["t1", "t2"],
                      "LoginView.ghost": ["t3"]})
    base.update(kw)
    return CoverageReport(**base)


def test_page_shows_ratio_and_all_buckets():
    html = render_source_coverage_report(_report())
    assert "50.0%" in html                     # coverage
    assert "25.0%" in html                     # dynamic_ratio
    assert "HomeView.cell_alpha" in html
    assert "LoginView.ghost" in html
    assert "GhostView" in html                 # screen 缺口
    for label in ("resolved", "dynamic", "unknown", "ambiguous", "missing"):
        assert label in html


def test_page_shows_which_cases_reference_each_gap():
    """报告要能回答「谁引用了它」——否则人得自己 grep 全套件。"""
    html = render_source_coverage_report(_report())
    assert "t1" in html and "t3" in html


def test_page_escapes_refs():
    html = render_source_coverage_report(
        _report(missing_refs=(("X", "<script>alert(1)</script>"),)))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_empty_report_renders_zero_not_crash():
    html = render_source_coverage_report(CoverageReport())
    assert "0.0%" in html
    assert "no identifier refs" in html or "0/0" in html


def test_write_creates_parent_dirs(tmp_path):
    p = write_source_coverage_report(_report(),
                                     tmp_path / "a" / "b" / "cov.html")
    assert p.exists() and p.read_text(encoding="utf-8").startswith("<!doctype")