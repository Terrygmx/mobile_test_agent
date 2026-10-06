"""Task 5.4 / P2-14：Impact Analysis + UI Change Report（设计 11.3 / 12.3）。

plan step 1 的失败测试清单：
  - fixture 用例集 + target → 受影响的 testcase **全集**；
  - Report 含 **diff 行**与**影响面**。

设计 11.3 的硬要求：**复用 P1 已建立的 element → testcase 反向索引，不新建第二套**
——本测试用一条「与 `source.coverage.ref_to_cases` 一致」的断言把它钉住（索引的
fold 只许一处实现）。
"""
from __future__ import annotations

import pytest

from graph import (
    ADDED,
    CHANGED,
    NOT_OBSERVED,
    RUNTIME,
    SOURCE,
    UNKNOWN,
    RuntimeGraph,
    ScreenNode,
    ScreenTransition,
    affected_testcases,
    cases_touching_screen,
    diff_graphs,
)
from graph.impact import (
    ImpactRow,
    build_change_report,
    format_report,
    ref_index,
    trigger_element,
)
from source.coverage import ref_to_cases
from testcase.schema import parse_testcase_dict

APP = "com.phaset0.logindemo"


def _case(steps: list[dict], cid: str = "c1") -> object:
    return parse_testcase_dict({
        "schema_version": "0.2", "id": cid, "name": cid, "suite": "s",
        "steps": steps})


def _graph(*, build="1026", source_of=SOURCE, screens=(), transitions=()):
    return RuntimeGraph(
        app_id=APP, app_build=build, source_of=source_of,
        nodes=tuple(ScreenNode(s, source_of) for s in screens),
        transitions=tuple(ScreenTransition(f, t, trig, source_of)
                          for f, t, trig in transitions))


# --- ① 索引：键的形态 + 与 P1 单点一致 ---------------------------------------


def test_index_keys_cover_all_three_reference_shapes():
    """键 = 用例里写的形态：元素限定引用 / 元素短名 / `screen:X` 引用。"""
    cases = [_case([
        {"action": "tap", "target": "LoginView.login_button"},
        {"action": "tap", "target": "go_profile"},          # 短名
        {"wait_for": {"target": "screen:HomeView", "condition": "active",
                            "timeout": 5}},        # 屏引用
    ])]
    idx = ref_index(cases)

    assert idx["LoginView.login_button"] == ("c1",)
    assert idx["go_profile"] == ("c1",)
    assert idx["screen:HomeView"] == ("c1",), "屏引用也要进索引（影响面需要）"


def test_index_is_built_on_the_single_p1_collector():
    """**不新建第二套**：元素维度必须与 `source.coverage.ref_to_cases` 一致。

    （本模块额外保留屏引用，因为 `ref_to_cases` 按 12.7 口径把 screen 滤掉了；
    而一个只 `wait_for screen:X` 的用例同样会因 X 变化而失败。）
    """
    cases = [_case([
        {"action": "tap", "target": "LoginView.login_button"},
        {"action": "tap", "target": "go_profile"},
    ]), _case([{"action": "tap", "target": "go_profile"}], cid="c2")]
    shared = ref_to_cases(cases)
    mine = ref_index(cases)

    for key, value in shared.items():
        assert mine[key] == tuple(sorted(value)), f"{key} 与 P1 索引不一致"


# --- ② affected_testcases（设计 11.3 的签名） --------------------------------


def test_affected_testcases_qualified_target():
    cases = [
        _case([{"action": "tap", "target": "LoginView.login_button"}], "c1"),
        _case([{"action": "tap", "target": "HomeView.go_profile"}], "c2"),
    ]
    idx = ref_index(cases)
    assert affected_testcases("LoginView.login_button", idx) == ("c1",)


def test_affected_testcases_bare_name_matches_any_screen():
    """裸名（用例常写的短名）匹配**任意屏上**同名元素的引用。

    用例写 `go_profile`、另一个写 `HomeView.go_profile`——它们指的是同一个元素。
    """
    cases = [
        _case([{"action": "tap", "target": "HomeView.go_profile"}], "c1"),
        _case([{"action": "tap", "target": "go_profile"}], "c2"),
        _case([{"action": "tap", "target": "OtherView.other"}], "c3"),
    ]
    idx = ref_index(cases)
    assert affected_testcases("go_profile", idx) == ("c1", "c2")
    # 限定名查询**也**覆盖裸名引用：影响面是回归范围选择，多包含一个用例的成本
    # 远低于漏掉一个会失败的用例（裸名指向哪一屏由运行时解析决定，事先不知道）
    assert affected_testcases("HomeView.go_profile", idx) == ("c1", "c2")


def test_affected_testcases_is_not_fuzzy():
    """不做模糊匹配：`login_button_2` 不该被 `login_button` 命中。"""
    cases = [_case([{"action": "tap", "target": "LoginView.login_button_2"}])]
    idx = ref_index(cases)
    assert affected_testcases("login_button", idx) == ()


def test_affected_testcases_unknown_target_is_empty():
    cases = [_case([{"action": "tap", "target": "LoginView.login_button"}])]
    assert affected_testcases("nope", ref_index(cases)) == ()


def test_cases_touching_screen():
    """屏的影响面 = `screen:X` 引用 ∪ `X.*` 元素引用。"""
    cases = [
        _case([{"wait_for": {"target": "screen:HomeView", "condition": "active",
                            "timeout": 5}}], "c1"),
        _case([{"action": "tap", "target": "HomeView.go_profile"}], "c2"),
        _case([{"action": "tap", "target": "OtherView.x"}], "c3"),
    ]
    idx = ref_index(cases)
    assert cases_touching_screen("HomeView", idx) == ("c1", "c2")
    assert cases_touching_screen("Nope", idx) == ()


def test_trigger_element():
    assert trigger_element("tap:login_button") == "login_button"
    assert trigger_element("input:username_field") == "username_field"
    assert trigger_element("launch_app:") is None
    assert trigger_element("") is None
    assert trigger_element(None) is None


# --- ③ UI Change Report = diff × 影响面（设计 12.3） ------------------------


def test_report_has_diff_rows_and_impact():
    """plan step 1：Report 含 **diff 行** 与 **影响面**。"""
    base = _graph(screens=("LoginView", "SearchView"))
    new = _graph(source_of=RUNTIME, screens=("LoginView", "ProfileView"))
    diff = diff_graphs(base, new)

    cases = [
        _case([{"wait_for": {"target": "screen:SearchView", "condition": "active",
                            "timeout": 5}}], "c1"),
        _case([{"wait_for": {"target": "screen:ProfileView", "condition": "active",
                            "timeout": 5}}], "c2"),
    ]
    report = build_change_report(diff, cases=cases)

    assert len(report.rows) == 2, "每条 diff 一行"
    kinds_and_cases = {(r.entry.kind, r.cases) for r in report.rows}
    assert (NOT_OBSERVED, ("c1",)) in kinds_and_cases
    assert (ADDED, ("c2",)) in kinds_and_cases
    assert report.affected_cases == ("c1", "c2")


def test_report_transition_change_hits_trigger_and_screens():
    """转移类差异的影响面 = 涉及两屏 ∪ **执行 trigger 那个元素**的用例。"""
    base = _graph(screens=("Z",),
                  transitions=[("LoginView", "HomeView", "tap:login_button")])
    new = _graph(source_of=RUNTIME, screens=("Z",),
                 transitions=[("LoginView", "ProfileView", "tap:login_button")])
    diff = diff_graphs(base, new)

    cases = [
        _case([{"action": "tap", "target": "LoginView.login_button"}], "c1"),
        _case([{"wait_for": {"target": "screen:ProfileView", "condition": "active",
                            "timeout": 5}}], "c2"),
        _case([{"action": "tap", "target": "OtherView.x"}], "c3"),
    ]
    report = build_change_report(diff, cases=cases)
    [row] = report.rows
    assert row.entry.kind == CHANGED
    assert row.cases == ("c1", "c2"), "trigger 元素 + 新目标屏；不相关用例不算"
    assert report.affected_cases == ("c1", "c2")
    assert report.unaffected_cases == ("c3",), "没被任何差异命中"


def test_report_marks_unknown_rows_as_provisional():
    """UNKNOWN 行的影响面只是**候选**（差异本身没判定）。"""
    base = _graph(screens=("A",))
    diff = diff_graphs(base, _graph(source_of=RUNTIME))   # 当前面为空 → UNKNOWN
    report = build_change_report(
        diff, cases=[_case([{"wait_for": {"target": "screen:A", "condition": "active",
                            "timeout": 5}}], "c1")])

    assert report.reason
    assert all(r.provisional for r in report.rows)
    assert "候选" in format_report(report)


def test_report_reports_unmapped_cases():
    """没引用任何 element/screen 的用例 → **无法判定**，必须说出来。

    「没算出来」不能被读成「没受影响」——这条与 12.2 的「没扫到 ≠ 不存在」同源。
    """
    base = _graph(screens=("LoginView",))
    new = _graph(source_of=RUNTIME, screens=("LoginView", "ProfileView"))
    diff = diff_graphs(base, new)
    cases = [
        _case([{"wait_for": {"target": "screen:ProfileView", "condition": "active",
                            "timeout": 5}}], "c1"),
        _case([{"action": "launch_app"}], "c2"),        # 没有任何引用
    ]
    report = build_change_report(diff, cases=cases)
    assert report.unmapped_cases == ("c2",)
    assert "无法判定" in format_report(report)
    # P3-3（review_p2_task54）：文本层 unmapped 不再同时出现在「未受影响」
    # ——人读清单去矛盾（summary() 的数据口径不变，消费方可自行相减）。
    text = format_report(report)
    unaffected_line = next((ln for ln in text.splitlines()
                            if "未受影响" in ln), "")
    assert "c2" not in unaffected_line, \
        "无法判定的用例不得同时出现在「未受影响」清单里"


def test_report_empty_diff_has_no_impact():
    g = _graph(screens=("A",))
    diff = diff_graphs(g, _graph(source_of=RUNTIME, screens=("A",)))
    report = build_change_report(diff, cases=[_case(
        [{"wait_for": {"target": "screen:A", "condition": "active",
                            "timeout": 5}}], "c1")])
    assert report.rows == () and report.affected_cases == ()
    assert report.total_cases == 1


def test_report_summary_shape():
    base = _graph(screens=("A", "B"))
    new = _graph(source_of=RUNTIME, screens=("A",))
    report = build_change_report(base and diff_graphs(base, new), cases=[
        _case([{"wait_for": {"target": "screen:B", "condition": "active",
                            "timeout": 5}}], "c1")])
    s = report.summary()
    assert set(s) == {"app_id", "base_build", "build", "rows",
                      "affected_cases", "affected_count", "total_cases",
                      "unaffected_cases", "unmapped_cases", "reason"}
    assert s["affected_count"] == 1 and s["total_cases"] == 1


def test_impact_row_render_shows_both_sides():
    row = ImpactRow(
        entry=diff_graphs(_graph(screens=("A", "B")),
                          _graph(source_of=RUNTIME,
                                 screens=("A",))).of_kind(NOT_OBSERVED)[0],
        cases=("c1", "c2"))
    text = row.render()
    assert "NOT_OBSERVED" in text and "screen B" in text
    assert "c1, c2" in text


# --- ④ CLI 端到端（`mta graph diff --cases`） -------------------------------


def _suites_dir(tmp_path, name="suites") -> str:
    d = tmp_path / name
    d.mkdir()
    (d / "login_001.yaml").write_text(
        'schema_version: "0.2"\nid: login_001\nname: 登录\nsuite: smoke\n'
        "steps:\n  - action: tap\n    target: LoginView.login_button\n"
        '  - wait_for:\n      target: "screen:HomeView"\n'
        "      condition: active\n      timeout: 10\n",
        encoding="utf-8")
    (d / "profile_001.yaml").write_text(
        'schema_version: "0.2"\nid: profile_001\nname: 我的\nsuite: smoke\n'
        "steps:\n  - action: tap\n    target: HomeView.go_profile\n",
        encoding="utf-8")
    return str(d)


def _metadata_file(tmp_path, *, screens=("LoginView", "HomeView", "SearchView"),
                   build="1026") -> str:
    import json
    p = tmp_path / "source_metadata.json"
    p.write_text(json.dumps({"build": build, "screens": list(screens)}),
                 encoding="utf-8")
    return str(p)


def _run(*argv) -> tuple[int, str]:
    import io
    from contextlib import redirect_stdout
    from cli.main import main
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(["graph", *argv])
    return code, buf.getvalue()


def test_cli_graph_diff_with_cases_prints_ui_change_report(tmp_path):
    """设计 12.3：`graph diff --cases` 给出 UI Change Report（diff × 影响面）。"""
    gdb = str(tmp_path / "graph.db")
    meta = _metadata_file(tmp_path)
    suites = _suites_dir(tmp_path)

    code, out = _run("build", "--graph-db", gdb, "--bundle-id", APP,
                     "--from-source", meta)
    assert code == 0
    # 只建源图（运行时图没有）→ UNKNOWN（5.3 的 P2-1 修复）
    code, out = _run("diff", "--graph-db", gdb, "--bundle-id", APP,
                     "--build", "1026", "--cases", suites)
    assert code == 0
    assert "UI Change Report" in out
    assert "判定：UNKNOWN" in out and "候选" in out


def test_cli_graph_diff_cases_missing_dir_is_exit_3(tmp_path):
    """用例目录读不到 → exit 3（不能把配置问题伪装成「影响面为空」）。"""
    gdb = str(tmp_path / "graph.db")
    _run("build", "--graph-db", gdb, "--bundle-id", APP, "--from-source",
         _metadata_file(tmp_path))
    code, out = _run("diff", "--graph-db", gdb, "--bundle-id", APP,
                     "--build", "1026", "--cases", str(tmp_path / "nope"))
    assert code == 3 and "GRAPH ERROR" in out


def test_cli_graph_diff_cases_end_to_end_with_runtime_graph(tmp_path):
    """端到端：两面都建 → 真实差异 → 影响面指向具体用例。"""
    from tracer.storage import TraceStore

    gdb = str(tmp_path / "graph.db")
    suites = _suites_dir(tmp_path)
    # 运行时图：只到过 LoginView（没到 HomeView）
    trace = tmp_path / "trace.db"
    store = TraceStore(trace)
    store.start_run("run_1", suite="smoke", app_bundle_id=APP,
                    app_build="1026")
    tc = store.start_testcase("run_1", "login_001", attempt=1)
    store.record_step(tc, 0, "wait_for", target_id="screen:LoginView",
                      status="SUCCESS")
    store.end_testcase(tc, "PASS")
    store.end_run("run_1", status="PASS", exit_code=0)

    _run("build", "--graph-db", gdb, "--bundle-id", APP,
         "--from-trace", str(trace),
         "--from-source", _metadata_file(tmp_path))
    code, out = _run("diff", "--graph-db", gdb, "--bundle-id", APP,
                     "--build", "1026", "--cases", suites)

    assert code == 1, "有真实差异"
    assert "NOT_OBSERVED  screen HomeView" in out
    assert "NOT_OBSERVED  screen SearchView" in out
    assert "UI Change Report" in out
    # HomeView 被 login_001（wait screen:HomeView）与 profile_001（HomeView.go_profile）
    # 引用；SearchView 没人引用 → 影响面只出这两个用例
    assert "受影响用例: 2/2" in out
    assert "profile_001" in out
