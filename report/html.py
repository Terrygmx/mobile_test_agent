"""report.html — 14.5 报告首页。

纯函数（H18）：RunResult + 汇总数据 → HTML 字符串。不碰设备、不读库——
数据由 `mta run` 侧收集后传入。P1 输出自包含静态 HTML（无 JS 依赖），
用例页/截图/UI 树链接留待 Task 2.7 真实套件跑通后补。
"""
from __future__ import annotations

import html as _html
from pathlib import Path

from runner.result import RunResult
from source.coverage import CoverageReport

__all__ = ["render_run_report", "write_run_report",
           "render_source_coverage_report", "write_source_coverage_report"]


def _fmt_duration(ms: int | None) -> str:
    if ms is None:
        return "N/A"
    if ms < 60_000:
        return f"{ms / 1000:.1f}s"
    m, s = divmod(ms // 1000, 60)
    return f"{m}:{s:02d}"


def _rate(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "N/A" if numerator == 0 else "0.0%"
    return f"{numerator / denominator * 100:.1f}%"


def _esc(text: str) -> str:
    return _html.escape(str(text))


def render_run_report(
    run: RunResult,
    *,
    llm_calls: int = 0,
    executed_steps: int = 0,
    wda_restarts: int = 0,
    duration_ms: int | None = None,
    report_url: str | None = None,
    unexecuted: list[str] | None = None,
    abort_reason: str | None = None,
    llm_broken: bool = False,
    experience_metrics=None,
) -> str:
    """14.5 首页。字段：Total / PASS / RECOVERED / FAIL / INFRA / ENV /
    BLOCKED、LLM 调用数、耗时、LLM Invocation Rate、WDA 重启次数。

    Invocation Rate = LLM recovery calls / 已执行 steps（14.7 指标表）。

    `experience_metrics`（Task 4.3 / 设计 17）：`report.experience_metrics`
    的 `ExperienceMetrics`，由调用方采集后注入——本模块保持「纯函数、不读库」
    （H18），采集归 `collect_experience_metrics`。
    """
    s = run.summary
    counts = s.counts
    rows: list[str] = []

    def card(label: str, value: object, cls: str = "") -> str:
        return (f'<div class="card {cls}"><div class="num">{value}</div>'
                f'<div class="label">{_esc(label)}</div></div>')

    cards = "".join([
        card("Total", s.total, "c-total"),
        card("PASS", counts.get("PASS", 0), "c-pass"),
        # 14.5 加粗句：RECOVERED 单独一栏（并进 PASS 会掩盖「需人工确认」）
        card("RECOVERED", counts.get("RECOVERED", 0), "c-recovered"),
        card("FAIL", counts.get("FAIL", 0), "c-fail"),
        card("INFRA", counts.get("INFRA_FAILURE", 0), "c-infra"),
        card("ENV", counts.get("ENVIRONMENT_FAILURE", 0), "c-env"),
        card("BLOCKED", counts.get("BLOCKED", 0), "c-blocked"),
        card("LLM 调用数", llm_calls, "c-llm"),
        card("LLM Invocation Rate", _rate(llm_calls, executed_steps),
             "c-rate"),
        card("WDA 重启次数", wda_restarts, "c-wda"),
        card("耗时", _fmt_duration(duration_ms), "c-dur"),
    ])

    for r in run.results:
        j = r.junit
        badge_cls = {"pass": "st-pass", "failure": "st-fail",
                     "error": "st-error", "skipped": "st-skip"}[j.status]
        # 设计 10（Task 2.4）：恢复分类进明细列——聚合卡片与退出码不变
        # （RECOVERED 仍是独立一栏、仍 ≠ PASS），只是能一眼看出「LLM 救的
        # 还是经验救的」。非恢复用例留空，不写 N/A（那一栏本来就无意义）。
        labels = r.detail.get("recovered_kinds") or []
        rows.append(
            f"<tr><td>{_esc(r.testcase_id)}</td>"
            f'<td><span class="badge {badge_cls}">{_esc(r.status)}</span></td>'
            f"<td>{_esc(','.join(labels))}</td>"
            f"<td>{_esc(r.failure_type or '')}</td>"
            f"<td>{_esc(r.failure_phase or '')}</td>"
            f"<td>{_esc(r.failure_attribution)}</td>"
            f"<td>{_esc(r.cleanup_status or '')}</td>"
            f"<td>{(r.duration_ms or 0) / 1000:.3f}</td></tr>")

    breaker_html = ""
    if llm_broken:
        # 10.5 明文「报告首页告警」：熔断必须一眼可见（RECOVERED 同款纪律
        # ——CI 判读者只看首页）
        breaker_html = ('<section class="breaker-alert">'
                        '<strong>LLM 熔断已触发（10.5）</strong>——连续失败达'
                        "阈值，本 run 后续不再调用；相关步骤按 "
                        "LLM_BUDGET_EXCEEDED 记录。</section>")

    unexec_html = ""
    if unexecuted:
        items = "".join(f"<li>{_esc(t)}</li>" for t in unexecuted)
        reason = f" — {_esc(abort_reason)}" if abort_reason else ""
        unexec_html = (f'<section class="unexecuted"><h2>未执行用例'
                       f'（套件中止{reason}）</h2><ul>{items}</ul></section>')

    metrics_html = ""
    if experience_metrics is not None:
        from report.experience_metrics import (
            render_experience_metrics_section,
        )
        metrics_html = render_experience_metrics_section(experience_metrics)

    title = f"mta run 报告 — {run.run_id}"
    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>{_esc(title)}</title>
<style>
body {{ font-family: -apple-system, "PingFang SC", sans-serif;
       margin: 24px; color: #1a1a1a; }}
.cards {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 24px; }}
.card {{ border: 1px solid #ddd; border-radius: 8px; padding: 10px 16px;
        min-width: 84px; text-align: center; }}
.card .num {{ font-size: 22px; font-weight: 600; }}
.card .label {{ font-size: 12px; color: #666; margin-top: 2px; }}
.breaker-alert {{ border: 1px solid #d33; background: #fdeaea;
             padding: 10px 16px; border-radius: 8px; margin-bottom: 20px;
             color: #8a1f1f; }}
.c-recovered {{ border-color: #e6a817; background: #fff8e6; }}
.c-fail {{ border-color: #d33; background: #fdeaea; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left;
         font-size: 13px; }}
.badge {{ padding: 2px 8px; border-radius: 4px; font-size: 12px; }}
.st-pass {{ background: #d4edda; }}
.st-fail {{ background: #f5c6cb; }}
.st-error {{ background: #fdd8b5; }}
.st-skip {{ background: #e2e3e5; }}
.unexecuted {{ margin-top: 24px; color: #a15c00; }}
.exp-metrics {{ margin-top: 28px; }}
.exp-metrics h2 {{ font-size: 17px; }}
.exp-metrics .note {{ color: #666; font-size: 12px; line-height: 1.6; }}
.c-exp-verified {{ border-color: #2f855a; background: #eaf7ef; }}
.c-exp-degraded {{ border-color: #e6a817; background: #fff8e6; }}
.c-exp-rejected {{ border-color: #999; background: #f2f2f2; }}
</style></head><body>
<h1>{_esc(title)}</h1>
<p>run_id: <code>{_esc(run.run_id)}</code>
 | exit code: <code>{run.exit_code}</code>{_esc(f" | {report_url}" if report_url else "")}</p>
<div class="cards">{cards}</div>
{breaker_html}
<table><thead><tr>
<th>用例</th><th>状态</th><th>恢复分类</th><th>failure_type</th><th>phase</th>
<th>attribution</th><th>cleanup</th><th>耗时(s)</th>
</tr></thead><tbody>
{''.join(rows) or '<tr><td colspan="8">（无用例执行）</td></tr>'}
</tbody></table>
{unexec_html}
{metrics_html}
</body></html>"""


def write_run_report(run: RunResult, path: str | Path, **kw) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_run_report(run, **kw), encoding="utf-8")
    return path


# --- Source Coverage 报告页（12.7 / Task 3.2） ---------------------------

_COV_STYLE = """
body { font-family: -apple-system, "PingFang SC", sans-serif;
       margin: 24px; color: #1a1a1a; }
.cards { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 20px; }
.card { border: 1px solid #ddd; border-radius: 8px; padding: 10px 16px;
        min-width: 96px; text-align: center; }
.card .num { font-size: 22px; font-weight: 600; }
.card .label { font-size: 12px; color: #666; margin-top: 2px; }
.c-cov { border-color: #2b6cb0; background: #ebf4ff; }
.c-gap { border-color: #e6a817; background: #fff8e6; }
table { border-collapse: collapse; width: 100%; margin-bottom: 20px; }
th, td { border: 1px solid #ddd; padding: 5px 9px; text-align: left;
         font-size: 13px; }
th { background: #f6f6f6; }
code { font-family: ui-monospace, Menlo, monospace; }
.bucket { margin-top: 18px; }
.bucket h2 { font-size: 15px; margin: 8px 0 4px; }
.note { color: #666; font-size: 12px; }
"""


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _bucket_rows(report: CoverageReport, refs) -> str:
    """一个分桶的表格行：ref + 引用它的用例 id。"""
    rows = []
    for screen, ident in refs:
        key = f"{screen}.{ident}"
        cases = ", ".join(report.cases_by_ref.get(key, ())) or "—"
        rows.append(f"<tr><td><code>{_esc(key)}</code></td>"
                    f"<td>{_esc(cases)}</td></tr>")
    return "".join(rows) or '<tr><td colspan="2">（空）</td></tr>'


def render_source_coverage_report(report: CoverageReport) -> str:
    """12.7 覆盖率报告页（14.5 报告体系的 Source Coverage 段）。

    五个分桶全部渲染——dynamic/unknown/ambiguous 虽然**不算失败**，但从
    报告里删掉就等于把「12.2 解析不出来」藏起来（consistency 的
    prefix_matched 同款纪律：不失败 ≠ 不显示）。
    """
    buckets = (
        ("resolved", report.resolved, report.resolved_refs,
         report.coverage,
         "被 metadata 解析（literal/constant）——运行时可定位"),
        ("dynamic", report.dynamic, report.dynamic_refs,
         report.dynamic_ratio,
         "12.2 插值元素：扫描器只给静态前缀，不猜值（人工登记实例）"),
        ("unknown", report.unknown, report.unknown_refs,
         report.unknown_ratio, "12.2 无法唯一解析"),
        ("ambiguous", report.ambiguous, report.ambiguous_refs,
         report.ambiguous_ratio, "短名跨屏同名：用例需加 Screen. 限定"),
        ("missing", report.missing, report.missing_refs,
         report.missing_ratio, "metadata 无此 id（漂移或从未登记）"),
    )
    sections = "".join(
        f'<div class="bucket"><h2>{name} = {count} （{_pct(ratio)}）</h2>'
        f'<p class="note">{_esc(note)}</p>'
        f'<table><thead><tr><th>identifier</th><th>引用用例</th></tr></thead>'
        f'<tbody>{_bucket_rows(report, refs)}</tbody></table></div>'
        for name, count, refs, ratio, note in buckets)

    screen_block = ""
    if report.screens_total:
        missing = ", ".join(report.screens_missing) or "（无）"
        screen_block = (
            f'<div class="bucket"><h2>screen marker coverage = '
            f'{_pct(report.screen_coverage)} '
            f'（{report.screens_resolved}/{report.screens_total}）</h2>'
            f'<p class="note">未被 metadata 顶层 screens 声明：</p>'
            f'<table><thead><tr><th>screen</th><th>引用用例</th></tr></thead>'
            f'<tbody><tr><td><code>{_esc(missing)}</code></td><td>—</td>'
            f'</tr></tbody></table></div>')

    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>mta source coverage</title>
<style>{_COV_STYLE}</style></head><body>
<h1>Source Coverage 报告（12.7）</h1>
<p class="note">Identifier Coverage = 用例引用的 identifier 中被 metadata 正确
解析（literal+constant）的数量 / 用例引用的 identifier 总数。
分母为去重后的 identifier 集合；共 {report.occurrences} 次引用。
screen marker 走独立指标（不混进 identifier 分母）。</p>
<div class="cards">
<div class="card c-cov"><div class="num">{_pct(report.coverage)}</div>
<div class="label">coverage ({report.resolved}/{report.total})</div></div>
<div class="card"><div class="num">{_pct(report.dynamic_ratio)}</div>
<div class="label">dynamic_ratio</div></div>
<div class="card"><div class="num">{_pct(report.unknown_ratio)}</div>
<div class="label">unknown_ratio</div></div>
<div class="card"><div class="num">{_pct(report.screen_coverage)}</div>
<div class="label">screen_coverage</div></div>
</div>
{sections}
{screen_block}
</body></html>"""


def write_source_coverage_report(report: CoverageReport,
                                path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_source_coverage_report(report), encoding="utf-8")
    return path
