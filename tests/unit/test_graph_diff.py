"""Task 5.3 / P2-13：Graph Diff 五类判定 + build-to-build（设计 12.2 / 12.3 / E12）。

plan step 1 的失败测试清单：
  - ADDED / REMOVED / CHANGED / NOT_OBSERVED / UNKNOWN 五类判定；
  - **REMOVED 仅当 Source 明确标注且 Runtime 多次确认不存在，默认 NOT_OBSERVED**
    （E12，矩阵 #16）；
  - build-to-build diff 按 app_build 关联。

方向约定：`base` = 参照面（Source 声明 / 旧 build），`new` = 当前面（Runtime 观测 /
新 build）——五类的措辞都是站在「当前面」上说的（ADDED = 当前面出现了参照面没有的）。
"""
from __future__ import annotations

import pytest

from graph import (
    ADDED,
    CHANGED,
    NOT_OBSERVED,
    REMOVED,
    RUNTIME,
    SOURCE,
    UNKNOWN,
    DiffEntry,
    GraphDiff,
    GraphStore,
    RuntimeGraph,
    ScreenNode,
    ScreenTransition,
    diff_graphs,
)
from graph.diff import DEFAULT_REMOVAL_CONFIRMATIONS, transition_key

APP = "com.phaset0.logindemo"


def _graph(*, build="1026", source_of=SOURCE, screens=(), transitions=()):
    return RuntimeGraph(
        app_id=APP, app_build=build, source_of=source_of,
        nodes=tuple(ScreenNode(s, source_of) for s in screens),
        transitions=tuple(ScreenTransition(f, t, trig, source_of)
                          for f, t, trig in transitions))


def _kinds(diff) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for e in diff.entries:
        out.setdefault(e.kind, []).append(e.key)
    return out


# --- ① 节点：ADDED / NOT_OBSERVED / 两面都有则不出条目 -----------------------


def test_node_added_and_not_observed():
    """当前面独有 → ADDED；参照面独有 → **NOT_OBSERVED**（矩阵 #16）。"""
    base = _graph(screens=("LoginView", "SearchView"))          # 声明
    new = _graph(source_of=RUNTIME, screens=("LoginView", "ProfileView"))
    diff = diff_graphs(base, new)

    kinds = _kinds(diff)
    assert kinds.get(ADDED) == ["ProfileView"]
    assert kinds.get(NOT_OBSERVED) == ["SearchView"]
    assert "LoginView" not in [e.key for e in diff.entries], "两面都有不出条目"
    assert diff.counts == {ADDED: 1, REMOVED: 0, CHANGED: 0,
                           NOT_OBSERVED: 1, UNKNOWN: 0}


def test_matrix_16_source_screen_never_covered_is_not_removed():
    """**矩阵 #16**：Source 有、用例从未覆盖 → `NOT_OBSERVED`，**不是** `REMOVED`。

    这是本任务最重要的一条：默认把「没走到」判成「被删了」会让报告满屏假删除。
    """
    base = _graph(screens=("SearchView", "DetailView"))
    new = _graph(source_of=RUNTIME, screens=("LoginView",))
    diff = diff_graphs(base, new)

    assert diff.of_kind(REMOVED) == ()
    assert {e.screen_id for e in diff.of_kind(NOT_OBSERVED)} == {"SearchView",
                                                                "DetailView"}


# --- ② REMOVED 的判据（负证据） --------------------------------------------


def test_removed_requires_enough_absent_confirmations():
    """REMOVED 只在**负证据足够**时给出（设计「Runtime 多次确认不存在」）。

    负证据由调用方显式提供（`absent_confirmations`）——运行时图只记「到达了
    什么」，不记「试过但不在」（Task 5.1 的口径）。**当前无生产者**，所以真实
    数据上 REMOVED 不可达，这正是设计的「默认优先 NOT_OBSERVED」。
    """
    base = _graph(screens=("SearchView",))
    new = _graph(source_of=RUNTIME, screens=("LoginView",))

    below = diff_graphs(base, new, absent_confirmations={
        "SearchView": DEFAULT_REMOVAL_CONFIRMATIONS - 1})
    assert below.of_kind(NOT_OBSERVED)[0].screen_id == "SearchView"
    assert below.of_kind(NOT_OBSERVED)[0].detail["absent_confirmations"] == \
        DEFAULT_REMOVAL_CONFIRMATIONS - 1, "差多少次也要如实报出来"

    at = diff_graphs(base, new, absent_confirmations={
        "SearchView": DEFAULT_REMOVAL_CONFIRMATIONS})
    assert at.of_kind(REMOVED)[0].screen_id == "SearchView"


def test_removal_threshold_must_be_positive():
    with pytest.raises(ValueError, match="removal_min_confirmations"):
        diff_graphs(_graph(screens=("A",)), _graph(source_of=RUNTIME),
                    removal_min_confirmations=0)


# --- ③ 转移：ADDED / NOT_OBSERVED / CHANGED --------------------------------


def test_transition_added_and_not_observed():
    base = _graph(transitions=[("A", "B", "tap:go")])
    new = _graph(source_of=RUNTIME, transitions=[("A", "C", "tap:other")])
    diff = diff_graphs(base, new)

    added = diff.of_kind(ADDED)[0]
    assert (added.from_screen, added.to_screen, added.trigger) == \
        ("A", "C", "tap:other")
    no = diff.of_kind(NOT_OBSERVED)[0]
    assert (no.from_screen, no.to_screen, no.trigger) == ("A", "B", "tap:go")


def test_changed_is_same_trigger_different_target():
    """设计 12.2：`CHANGED` = **同一触发条件、目标 Screen 变化**。

    连接键是 `(from_screen, trigger)`——同一起点、同一动作、目标变了。
    """
    base = _graph(transitions=[("LoginView", "HomeView", "tap:login_button")])
    new = _graph(source_of=RUNTIME,
                 transitions=[("LoginView", "LoginError", "tap:login_button")])
    diff = diff_graphs(base, new)

    ch = diff.of_kind(CHANGED)[0]
    assert (ch.from_screen, ch.to_screen, ch.trigger) == \
        ("LoginView", "LoginError", "tap:login_button")
    assert ch.detail["was_to"] == "HomeView", "原目标要留痕（报告靠它解释变化）"
    assert "原目标: HomeView" in ch.render()


def test_same_screens_different_trigger_is_not_changed():
    """同一对屏但触发条件不同 → 不是 CHANGED（那是两条不同的转移）。"""
    base = _graph(transitions=[("A", "B", "tap:x")])
    new = _graph(source_of=RUNTIME, transitions=[("A", "B", "tap:y")])
    diff = diff_graphs(base, new)

    assert diff.of_kind(CHANGED) == ()
    assert diff.of_kind(ADDED)[0].trigger == "tap:y"
    assert diff.of_kind(NOT_OBSERVED)[0].trigger == "tap:x"


def test_transition_removed_uses_transition_key():
    """转移的负证据键 = `from->to@trigger`（与 `DiffEntry.key` 同域）。

    当前面必须**非空**（P2-1 之后空当前面判 UNKNOWN，走不到转移判定）——给一个
    别的屏即可。
    """
    base = _graph(transitions=[("A", "B", "tap:go")])
    new = _graph(source_of=RUNTIME, screens=("Z",))
    key = transition_key("A", "B", "tap:go")

    assert diff_graphs(base, new).of_kind(NOT_OBSERVED)[0].key == key
    removed = diff_graphs(base, new, absent_confirmations={key: 5})
    assert removed.of_kind(REMOVED)[0].key == key


# --- ④ UNKNOWN：参照面缺失/为空 --------------------------------------------


@pytest.mark.parametrize("base,new,reason_kw", [
    (None, _graph(source_of=RUNTIME, screens=("A",)), "参照面"),
    (_graph(screens=("A",)), None, "当前面"),
    (_graph(screens=()), _graph(source_of=RUNTIME, screens=("A",)), "参照面为空"),
])
def test_unknown_when_a_side_is_absent_or_empty(base, new, reason_kw):
    """参照面缺失或为空 → 全部 UNKNOWN（**不是** ADDED）。

    理由：参照面为空时，当前面独有的条目既可能是「新增」也可能是「参照缺失」，
    无法判定。这也顺带堵住一个静默误读——多数现成 metadata 没有 `screens`，
    源图本就为空，照常判 ADDED 会把运行时全部算成「未声明」。
    """
    diff = diff_graphs(base, new)

    assert diff.of_kind(ADDED) == (), "参照面为空时不许判 ADDED"
    assert diff.reason and reason_kw in diff.reason
    assert all(e.kind == UNKNOWN for e in diff.entries)
    assert all(e.detail["reason"] == diff.reason for e in diff.entries)
    assert diff.changed is False, "UNKNOWN 是「没判定」，不是「有变化」"


def test_unknown_dedupes_items_listed_by_both_sides():
    """两面都列出同一条目时只报一次（去重按条目身份串）。"""
    base = _graph(screens=("A",))
    new = _graph(source_of=RUNTIME, screens=("A",))
    diff = diff_graphs(base, new)          # 两面都在 → 走正常分支
    assert diff.entries == (), "两面都有 → 无差异"

    unknown = diff_graphs(None, new)
    assert [e.key for e in unknown.entries] == ["A"]


# --- ⑤ 跨范围守卫（review_p2_task52 P3-1 的接线前置） ----------------------


def test_different_app_id_fails_loud():
    a = _graph(screens=("A",))
    b = RuntimeGraph(app_id="com.other", app_build="1026", source_of=RUNTIME,
                     nodes=(ScreenNode("A", RUNTIME),))
    with pytest.raises(ValueError, match="app_id 不同"):
        diff_graphs(a, b)


def test_different_build_requires_explicit_opt_in():
    """跨 build 比较必须显式声明（否则 scope 不重叠会安静地报满屏 NOT_OBSERVED）。"""
    a = _graph(build="1024", screens=("A",))
    b = _graph(build="1025", source_of=RUNTIME, screens=("A",))
    with pytest.raises(ValueError, match="allow_build_change"):
        diff_graphs(a, b)
    diff = diff_graphs(a, b, allow_build_change=True)
    assert diff.base_build == "1024" and diff.build == "1025"


# --- ⑥ build-to-build（设计 12.3） -----------------------------------------


def test_build_to_build_diff():
    """同一面在两个 build 之间比：新屏 ADDED、消失的屏 NOT_OBSERVED、转移 CHANGED。"""
    old = _graph(build="1024", source_of=RUNTIME,
                 screens=("LoginView", "HomeView"),
                 transitions=[("LoginView", "HomeView", "tap:login_button")])
    new = _graph(build="1025", source_of=RUNTIME,
                 screens=("LoginView", "HomeView", "ProfileView"),
                 transitions=[("LoginView", "ProfileView", "tap:login_button")])
    diff = diff_graphs(old, new, allow_build_change=True)

    assert [e.screen_id for e in diff.of_kind(ADDED)] == ["ProfileView"]
    assert diff.of_kind(CHANGED)[0].detail["was_to"] == "HomeView"
    assert diff.of_kind(CHANGED)[0].to_screen == "ProfileView"
    assert diff.summary()["base_build"] == "1024"
    assert diff.summary()["build"] == "1025"
    assert diff.changed is True


# --- DiffEntry / GraphDiff 的值纪律 ----------------------------------------


def test_diff_entry_validates_kind_and_identity():
    with pytest.raises(ValueError, match="未知 diff kind"):
        DiffEntry("BOGUS", screen_id="A")
    with pytest.raises(ValueError, match="必须给出"):
        DiffEntry(ADDED)


def test_diff_entry_detail_must_be_dict():
    """P3-1（review_p2_task54）：builder 恒传 dict，但 dataclass 不强制
    类型——下游直构 `detail=None` 的 CHANGED 行会在 render 的 detail.get
    上 AttributeError。构造入口 fail-loud（类型地雷同族）。"""
    with pytest.raises(TypeError, match="detail must be dict"):
        DiffEntry(CHANGED, from_screen="A", to_screen="B", detail=None)
    with pytest.raises(TypeError, match="detail must be dict"):
        DiffEntry(CHANGED, from_screen="A", to_screen="B", detail="")


def test_diff_entry_render_shapes():
    assert DiffEntry(ADDED, screen_id="A").render() == "ADDED         screen A"
    line = DiffEntry(ADDED, from_screen="A", to_screen="B",
                     trigger="tap:go").render()
    assert line == "ADDED         transition A -> B (trigger=tap:go)"
    assert "trigger=-" in DiffEntry(NOT_OBSERVED, from_screen="A", to_screen="B"
                                    ).render(), "空 trigger 渲染成 -"


def test_empty_diff_has_no_entries_and_no_change():
    g = _graph(screens=("A",))
    diff = diff_graphs(g, _graph(source_of=RUNTIME, screens=("A",)))
    assert isinstance(diff, GraphDiff)
    assert diff.entries == () and diff.changed is False
    assert diff.summary()["counts"] == {ADDED: 0, REMOVED: 0, CHANGED: 0,
                                        NOT_OBSERVED: 0, UNKNOWN: 0}


# --- 落库（graph_diffs 表；Task 5.1 建的表，本任务首次消费） ----------------


def test_record_diff_roundtrip_and_replace(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    base = _graph(screens=("SearchView",))
    new = _graph(source_of=RUNTIME, screens=("LoginView",))
    diff = diff_graphs(base, new)
    assert store.record_diff(diff) == 2, "1 条 NOT_OBSERVED + 1 条 ADDED"

    rows = store.load_diff(APP, "1026", "1026")
    assert [(r["kind"], r["screen_id"]) for r in rows] == [
        (ADDED, "LoginView"), (NOT_OBSERVED, "SearchView")], \
        "按 kind 排序，报告呈现稳定"

    # 重跑（更小的差异）→ 整体替换，不累加
    store.record_diff(diff_graphs(_graph(screens=("A",)),
                                 _graph(source_of=RUNTIME, screens=("A",))))
    assert store.load_diff(APP, "1026", "1026") == [], "空差异替换掉旧行"


def test_record_diff_keeps_transition_columns(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    diff = diff_graphs(
        _graph(transitions=[("A", "B", "tap:go")]),
        _graph(source_of=RUNTIME, transitions=[("A", "C", "tap:go")]))
    store.record_diff(diff)

    [row] = store.load_diff(APP, "1026", "1026")
    assert (row["kind"], row["from_screen"], row["to_screen"],
            row["trigger"]) == (CHANGED, "A", "C", "tap:go")
    assert row["detail"]["was_to"] == "B"
    assert row["created_at"], "时间戳要落（报告要显示「什么时候比的」）"


# --- CLI 端到端：`mta graph build / diff / show` ---------------------------


def _trace_db(tmp_path, *, build="1026", name="trace.db") -> str:
    """造一个最小但真实形状的 trace（两个屏观测 + 一条转移）。"""
    from tracer.storage import TraceStore

    db = tmp_path / name
    store = TraceStore(db)
    store.start_run("run_1", suite="smoke", app_bundle_id=APP, app_build=build)
    tc = store.start_testcase("run_1", "login_001", attempt=1)
    store.record_step(tc, 0, "wait_for", target_id="screen:LoginView",
                      status="SUCCESS")
    store.record_step(tc, 1, "tap", target_id="login_button", status="SUCCESS")
    store.record_step(tc, 2, "wait_for", target_id="screen:HomeView",
                      status="SUCCESS")
    store.end_testcase(tc, "PASS")
    store.end_run("run_1", status="PASS", exit_code=0)
    return str(db)


def _metadata_file(tmp_path, *, screens=("LoginView", "HomeView", "SearchView"),
                   build="1026", name="source_metadata.json") -> str:
    import json
    p = tmp_path / name
    p.write_text(json.dumps({
        "build": build, "generated_at": "2026-10-05T10:00:00Z",
        "screens": list(screens)}), encoding="utf-8")
    return str(p)


def _run_graph_cli(capsys, *argv) -> tuple[int, str]:
    from cli.main import main
    code = main(["graph", *argv])
    return code, capsys.readouterr().out


def test_cli_build_then_show_then_diff(tmp_path, capsys):
    """一条链路：build（两面）→ show → diff（矩阵 #16 的 NOT_OBSERVED）。"""
    gdb = str(tmp_path / "graph.db")
    trace = _trace_db(tmp_path)
    meta = _metadata_file(tmp_path)

    code, out = _run_graph_cli(capsys, "build", "--graph-db", gdb,
                               "--bundle-id", APP, "--from-trace", trace,
                               "--from-source", meta)
    assert code == 0
    assert "runtime: 2 节点 / 1 转移" in out
    assert "source: 3 节点 / 0 转移" in out

    code, out = _run_graph_cli(capsys, "show", "--graph-db", gdb,
                               "--bundle-id", APP)
    assert code == 0
    assert "source_of=runtime" in out and "source_of=source" in out
    assert "node LoginView" in out and "LoginView -> HomeView" in out

    code, out = _run_graph_cli(capsys, "diff", "--graph-db", gdb,
                               "--bundle-id", APP, "--build", "1026")
    assert code == 1, "有真实变化（非 UNKNOWN）→ exit 1"
    assert f"NOT_OBSERVED  screen SearchView" in out, "矩阵 #16"
    assert "ADDED         transition LoginView -> HomeView" in out
    assert "REMOVED=0" in out and "CHANGED=0" in out and "UNKNOWN=0" in out


def test_cli_diff_unknown_when_source_declares_nothing(tmp_path, capsys):
    """源面为空（metadata 没声明屏）→ UNKNOWN + exit 0，且**不当成 ADDED**。"""
    gdb = str(tmp_path / "graph.db")
    trace = _trace_db(tmp_path)
    meta = _metadata_file(tmp_path, screens=())

    _run_graph_cli(capsys, "build", "--graph-db", gdb, "--bundle-id", APP,
                   "--from-trace", trace, "--from-source", meta)
    code, out = _run_graph_cli(capsys, "diff", "--graph-db", gdb,
                               "--bundle-id", APP, "--build", "1026")
    assert code == 0, "UNKNOWN 不是「有变化」"
    assert "判定：UNKNOWN" in out and "参照面为空" in out
    assert "ADDED=0" in out


def test_cli_build_warns_when_source_has_no_screens(tmp_path, capsys):
    """空源图必须**说出来**（多数现成 metadata 没有 `screens`，静默会让判读跑偏）。"""
    gdb = str(tmp_path / "graph.db")
    code, out = _run_graph_cli(capsys, "build", "--graph-db", gdb,
                               "--bundle-id", APP,
                               "--from-source", _metadata_file(tmp_path,
                                                               screens=()))
    assert code == 0
    assert "GRAPH WARN" in out and "没有声明任何屏" in out


def test_cli_diff_fails_loud_on_scope_mismatch(tmp_path, capsys):
    """两面 scope 未对齐 → exit 3 并点名另一个 build（review_p2_task52 P3-1）。"""
    gdb = str(tmp_path / "graph.db")
    # 源图建在 1026，运行时图建在 1025
    _run_graph_cli(capsys, "build", "--graph-db", gdb, "--bundle-id", APP,
                   "--from-source", _metadata_file(tmp_path, build="1026"))
    _run_graph_cli(capsys, "build", "--graph-db", gdb, "--bundle-id", APP,
                   "--from-trace", _trace_db(tmp_path, build="1025"))

    code, out = _run_graph_cli(capsys, "diff", "--graph-db", gdb,
                               "--bundle-id", APP, "--build", "1025")
    assert code == 3
    assert "GRAPH ERROR" in out and "scope 未对齐" in out
    assert "1026" in out, "要点名另一面的 build"


def test_cli_diff_build_to_build(tmp_path, capsys):
    """`--base-build` → build-to-build（设计 12.3）。"""
    gdb = str(tmp_path / "graph.db")
    _run_graph_cli(capsys, "build", "--graph-db", gdb, "--bundle-id", APP,
                   "--from-trace", _trace_db(tmp_path, build="1024",
                                             name="t1024.db"))
    _run_graph_cli(capsys, "build", "--graph-db", gdb, "--bundle-id", APP,
                   "--from-trace", _trace_db(tmp_path, build="1025",
                                             name="t1025.db"))
    code, out = _run_graph_cli(capsys, "diff", "--graph-db", gdb,
                               "--bundle-id", APP, "--build", "1025",
                               "--base-build", "1024")
    assert code == 0, "两个 build 的图相同 → 无变化"
    assert "base_build='1024' build='1025'" in out
    assert "ADDED=0, REMOVED=0, CHANGED=0, NOT_OBSERVED=0, UNKNOWN=0" in out


def test_cli_diff_save_persists_to_graph_diffs(tmp_path, capsys):
    """`--save` 落 `graph_diffs`；重跑按范围整体替换（幂等）。"""
    gdb = str(tmp_path / "graph.db")
    _run_graph_cli(capsys, "build", "--graph-db", gdb, "--bundle-id", APP,
                   "--from-trace", _trace_db(tmp_path),
                   "--from-source", _metadata_file(tmp_path))
    code, out = _run_graph_cli(capsys, "diff", "--graph-db", gdb,
                               "--bundle-id", APP, "--build", "1026", "--save")
    assert code == 1 and "已落库 graph_diffs" in out

    store = GraphStore(gdb)
    assert len(store.load_diff(APP, "1026", "1026")) == 2
    _run_graph_cli(capsys, "diff", "--graph-db", gdb, "--bundle-id", APP,
                   "--build", "1026", "--save")
    assert len(store.load_diff(APP, "1026", "1026")) == 2, "替换而非累加"


def test_cli_build_bad_metadata_fails_loud(tmp_path, capsys):
    """坏 metadata → exit 3（不静默产出空图）。"""
    gdb = str(tmp_path / "graph.db")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    code, out = _run_graph_cli(capsys, "build", "--graph-db", gdb,
                               "--bundle-id", APP, "--from-source", str(bad))
    assert code == 3 and "GRAPH ERROR" in out


def test_cli_show_on_empty_db(tmp_path, capsys):
    code, out = _run_graph_cli(capsys, "show", "--graph-db",
                               str(tmp_path / "none.db"))
    assert code == 0 and "库里没有任何图" in out


def test_cli_build_defaults_to_both_sides(tmp_path, capsys, monkeypatch):
    """两个 `--from-*` 都不给 → 两边都建（CLI 的常见用法）。"""
    gdb = str(tmp_path / "graph.db")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "repository/generated/local").mkdir(parents=True)
    import json
    (tmp_path / "repository/generated/local/source_metadata.json").write_text(
        json.dumps({"build": "1026", "screens": ["LoginView"]}),
        encoding="utf-8")
    trace = _trace_db(tmp_path)
    code, out = _run_graph_cli(capsys, "build", "--graph-db", gdb,
                               "--bundle-id", APP, "--db", trace)
    assert code == 0
    assert "runtime:" in out and "source:" in out, "两面都建"


# --- review_p2_task53 的修订钉子（1×P2 + 3×P3） -----------------------------


def test_empty_current_side_is_unknown_like_empty_reference():
    """P2-1：**当前面为空与参照面同等保守**（都判 UNKNOWN）。

    早先只对参照面做保守处理 → 「当前面没有数据」走正常分支，参照面条目全判
    `NOT_OBSERVED` 且 `changed=True`（CLI exit 1）。而「空图不留行」让「没构建过」
    与「构建了但为空」在库里不可区分——**首次使用路径**（只建源图就 diff）会拿到
    一个自信的错误结论：「用例没走到这些屏」，真相是「你还没建运行时图」。

    对称性判据：同一件事（当前面没有数据）不该因为传**空图**还是 `None` 得到相反
    结论——下面两行必须一致。
    """
    base = _graph(screens=("LoginView", "SearchView"))
    empty = _graph(source_of=RUNTIME)          # 非 None 但没有任何节点

    as_empty = diff_graphs(base, empty)
    as_none = diff_graphs(base, None)
    for diff in (as_empty, as_none):
        assert diff.of_kind(NOT_OBSERVED) == (), "空当前面不许判 NOT_OBSERVED"
        assert diff.of_kind(ADDED) == ()
        assert diff.reason and "当前面为空" in diff.reason or "当前面没有图" \
            in diff.reason
        assert diff.changed is False, "没数据 ≠ 有变化（exit 0）"
        assert all(e.kind == UNKNOWN for e in diff.entries)


def test_empty_current_side_still_lists_both_sides_items():
    """UNKNOWN 时两面条目都要列出来（带原因），不是空列表。"""
    diff = diff_graphs(_graph(screens=("A", "B")), _graph(source_of=RUNTIME))
    assert {e.key for e in diff.entries} == {"A", "B"}
    assert all(e.kind == UNKNOWN for e in diff.entries)


def test_multi_target_transition_is_not_silently_dropped():
    """P3-1：同一 `(from, trigger)` 多目标时**一条都不许丢**。

    早先 `_trans_of` 用单值字典，同键第二条**静默覆盖**第一条——探针实测
    `(A,tap:go)→B` 与 `(A,tap:go)→C` 共存时 B 彻底消失，**真删一条转移也发现
    不了**。schema 的唯一键含 `to_screen`，本来就允许共存。
    """
    base = _graph(transitions=[("A", "B", "tap:go"), ("A", "C", "tap:go")])
    new = _graph(source_of=RUNTIME, screens=("Z",))
    diff = diff_graphs(base, new)

    no = {(e.from_screen, e.to_screen, e.trigger)
          for e in diff.of_kind(NOT_OBSERVED)}
    assert no == {("A", "B", "tap:go"), ("A", "C", "tap:go")}, "两条都要报"
    assert diff.of_kind(CHANGED) == (), "多目标不编 CHANGED"
    assert all(e.detail.get("multi_target") for e in diff.of_kind(NOT_OBSERVED))


def test_multi_target_marks_added_side_too():
    """多目标在**当前面**时同理：逐目标 ADDED，不丢。"""
    base = _graph(screens=("Z",))
    new = _graph(source_of=RUNTIME,
                 transitions=[("A", "B", "tap:go"), ("A", "C", "tap:go")])
    diff = diff_graphs(base, new)

    added = {(e.from_screen, e.to_screen) for e in diff.of_kind(ADDED)}
    assert added == {("A", "B"), ("A", "C")}
    assert all(e.detail.get("multi_target") for e in diff.of_kind(ADDED))


def test_single_target_change_is_still_changed():
    """单目标 → 目标变化仍是 `CHANGED`（多目标改造没破坏原语义）。

    两面都带同一个屏 Z，让差异**只**来自转移（否则 Z 会变成节点级 ADDED，
    掩盖本测试的判据）。
    """
    base = _graph(screens=("Z",), transitions=[("A", "B", "tap:go")])
    new = _graph(source_of=RUNTIME, screens=("Z",),
                 transitions=[("A", "C", "tap:go")])
    diff = diff_graphs(base, new)
    assert diff.of_kind(CHANGED)[0].detail["was_to"] == "B"
    assert diff.of_kind(ADDED) == () and diff.of_kind(NOT_OBSERVED) == ()


def test_multi_target_change_is_reported_as_add_and_remove():
    """多目标**且**目标变了 → 逐目标报（不丢、不编 CHANGED）。"""
    base = _graph(screens=("Z",), transitions=[("A", "B", "tap:go")])
    new = _graph(source_of=RUNTIME, screens=("Z",),
                 transitions=[("A", "C", "tap:go"), ("A", "D", "tap:go")])
    diff = diff_graphs(base, new)

    assert diff.of_kind(CHANGED) == ()
    assert {e.to_screen for e in diff.of_kind(ADDED)} == {"C", "D"}
    assert {e.to_screen for e in diff.of_kind(NOT_OBSERVED)} == {"B"}


def test_diff_persists_which_two_sides_were_compared(tmp_path):
    """P3-2：`graph_diffs` 要记住「比的是哪两面」（迁移 002 的两列）。

    否则「源图 vs 运行时」（两面来源不同、build 相同）与「build-to-build」
    （来源相同、build 不同）在库里长得一样，回读时分不清。CLI 打印时是知道的
    ——信息不该在落库时丢掉。
    """
    store = GraphStore(tmp_path / "graph.db")
    # 要有**真实差异**才落行：源声明了 B，运行时没到（→ NOT_OBSERVED）
    src_vs_rt = diff_graphs(_graph(screens=("A", "B")),
                            _graph(source_of=RUNTIME, screens=("A",)))
    store.record_diff(src_vs_rt)
    [row] = store.load_diff(APP, "1026", "1026")
    assert (row["base_source_of"], row["source_of"]) == (SOURCE, RUNTIME)
    assert (row["kind"], row["screen_id"]) == (NOT_OBSERVED, "B")


def test_diff_replace_key_includes_both_sides(tmp_path):
    """两面来源进替换键：同 build 对下的两种比较互不覆盖。"""
    store = GraphStore(tmp_path / "graph.db")
    src_vs_rt = diff_graphs(_graph(screens=("A", "B")),
                            _graph(source_of=RUNTIME, screens=("A",)))
    store.record_diff(src_vs_rt)

    # 同一 (app_id, 1026, 1026) 下的 build-to-build（两面都是 runtime）
    b2b = diff_graphs(_graph(source_of=RUNTIME, screens=("A",)),
                      _graph(source_of=RUNTIME, screens=("A", "B")),
                      allow_build_change=True)
    store.record_diff(b2b)

    assert len(store.load_diff(APP, "1026", "1026")) == 2, "两种比较共存"
    only_b2b = store.load_diff(APP, "1026", "1026", RUNTIME, RUNTIME)
    assert [r["kind"] for r in only_b2b] == [ADDED], "按两面来源可精确过滤"


def test_cli_diff_save_build_to_build(tmp_path, capsys):
    """评审 §4 的「未验证」项：`--save` 在 build-to-build 下落库的内容。"""
    gdb = str(tmp_path / "graph.db")
    _run_graph_cli(capsys, "build", "--graph-db", gdb, "--bundle-id", APP,
                   "--from-trace", _trace_db(tmp_path, build="1024",
                                             name="t1024.db"))
    _run_graph_cli(capsys, "build", "--graph-db", gdb, "--bundle-id", APP,
                   "--from-trace", _trace_db(tmp_path, build="1025",
                                             name="t1025.db"))
    code, out = _run_graph_cli(capsys, "diff", "--graph-db", gdb,
                               "--bundle-id", APP, "--build", "1025",
                               "--base-build", "1024", "--save")
    assert code == 0 and "已落库 graph_diffs：0 行" in out

    # 造一个有真实差异的 build-to-build：1025 的图删掉一个屏
    store = GraphStore(gdb)
    from graph.models import RUNTIME as R, RuntimeGraph, ScreenNode
    store.upsert_graph(RuntimeGraph(
        app_id=APP, app_build="1024", source_of=R,
        nodes=(ScreenNode("LoginView", R, 1), ScreenNode("HomeView", R, 1))))
    store.upsert_graph(RuntimeGraph(
        app_id=APP, app_build="1025", source_of=R,
        nodes=(ScreenNode("LoginView", R, 1),)))
    code, out = _run_graph_cli(capsys, "diff", "--graph-db", gdb,
                               "--bundle-id", APP, "--build", "1025",
                               "--base-build", "1024", "--save")
    assert code == 1
    rows = store.load_diff(APP, "1024", "1025", RUNTIME, RUNTIME)
    assert [(r["kind"], r["screen_id"]) for r in rows] == \
        [(NOT_OBSERVED, "HomeView")], "build-to-build 的差异按两面来源落库"


def test_cli_diff_prints_next_step_when_unknown(tmp_path, capsys):
    """UNKNOWN 时 CLI 要给出下一步（首次使用最容易踩：忘了建运行时图）。"""
    gdb = str(tmp_path / "graph.db")
    _run_graph_cli(capsys, "build", "--graph-db", gdb, "--bundle-id", APP,
                   "--from-source", _metadata_file(tmp_path))
    code, out = _run_graph_cli(capsys, "diff", "--graph-db", gdb,
                               "--bundle-id", APP, "--build", "1026")
    assert code == 0
    assert "判定：UNKNOWN" in out
    assert "下一步" in out and "graph build" in out
