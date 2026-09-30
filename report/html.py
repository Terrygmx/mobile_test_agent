"""report.html — 14.5 报告首页。

纯函数（H18）：RunResult + 汇总数据 → HTML 字符串。不碰设备、不读库——
数据由 `mta run` 侧收集后传入。P1 输出自包含静态 HTML（无 JS 依赖），
用例页/截图/UI 树链接留待 Task 2.7 真实套件跑通后补。
"""
from __future__ import annotations

import html as _html
from pathlib import Path

from runner.result import RunResult

__all__ = ["render_run_report", "write_run_report"]


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
) -> str:
    """14.5 首页。字段：Total / PASS / RECOVERED / FAIL / INFRA / ENV /
    BLOCKED、LLM 调用数、耗时、LLM Invocation Rate、WDA 重启次数。

    Invocation Rate = LLM recovery calls / 已执行 steps（14.7 指标表）。
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
        rows.append(
            f"<tr><td>{_esc(r.testcase_id)}</td>"
            f'<td><span class="badge {badge_cls}">{_esc(r.status)}</span></td>'
            f"<td>{_esc(r.failure_type or '')}</td>"
            f"<td>{_esc(r.failure_phase or '')}</td>"
            f"<td>{_esc(r.failure_attribution)}</td>"
            f"<td>{_esc(r.cleanup_status or '')}</td>"
            f"<td>{(r.duration_ms or 0) / 1000:.3f}</td></tr>")

    unexec_html = ""
    if unexecuted:
        items = "".join(f"<li>{_esc(t)}</li>" for t in unexecuted)
        reason = f" — {_esc(abort_reason)}" if abort_reason else ""
        unexec_html = (f'<section class="unexecuted"><h2>未执行用例'
                       f'（套件中止{reason}）</h2><ul>{items}</ul></section>')

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
</style></head><body>
<h1>{_esc(title)}</h1>
<p>run_id: <code>{_esc(run.run_id)}</code>
 | exit code: <code>{run.exit_code}</code>{_esc(f" | {report_url}" if report_url else "")}</p>
<div class="cards">{cards}</div>
<table><thead><tr>
<th>用例</th><th>状态</th><th>failure_type</th><th>phase</th>
<th>attribution</th><th>cleanup</th><th>耗时(s)</th>
</tr></thead><tbody>
{''.join(rows) or '<tr><td colspan="7">（无用例执行）</td></tr>'}
</tbody></table>
{unexec_html}
</body></html>"""


def write_run_report(run: RunResult, path: str | Path, **kw) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_run_report(run, **kw), encoding="utf-8")
    return path
