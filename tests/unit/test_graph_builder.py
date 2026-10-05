"""Task 5.1 / P2-11：Runtime Graph 构建（设计 12.1 / E12）。

plan step 1 的失败测试清单：
  - 从 fixture trace（含 screen 转移的 steps）聚合出 nodes/transitions；
  - visit_count / first_seen / last_seen；
  - 同一转移合并计数；
  - **输入只有已有 Trace，不做自动探索**（E12）。

fixture 用真 `TraceStore` 写入（不手搓 SQL）——图构建读的是真实列名与真实
detail 形状，替身 SQL 会让「列名写错」这类错误在单测里静默通过。
"""
from __future__ import annotations

import pytest

from graph import (
    GRAPH_SCHEMA_VERSION,
    RUNTIME,
    SOURCE,
    GraphStore,
    TraceStep,
    build_and_store,
    build_runtime_graph,
    is_screen_wait,
    observed_screens,
    read_trace_steps,
)
from tracer.storage import TraceStore

APP = "com.phaset0.logindemo"
BUILD = "1026"


# --- fixture：真 TraceStore 写入一条「登录 → 首页 → 我的」用例 ---------------


def _trace(tmp_path, *, build=BUILD, timed_out_wait: bool = False,
           with_recovery: bool = True, with_screen_wait: bool = True,
           name: str = "trace.db") -> str:
    """写一条真实形状的 trace：wait 屏 + 恢复观测 + Screen.element 目标。

    刻意包含一个 `wait_for LoginView.password_field`（`Screen.element` 形态）：
    它**不该**产生运行时节点（那是元数据声明，见 builder docstring）。
    """
    db = tmp_path / name
    store = TraceStore(db)
    store.start_run("run_1", suite="smoke", app_bundle_id=APP,
                    app_build=build)
    tc = store.start_testcase("run_1", "login_001", attempt=1)

    store.record_step(tc, 0, "launch_app", status="SUCCESS")
    # Screen.element 形态的 wait：不产生运行时节点
    store.record_step(tc, 1, "wait_for", target_id="LoginView.password_field",
                      status="SUCCESS")
    # 恢复观测：引擎 stage screen/FOUND → LoginView
    detail = ({"recovery_kind": "experience",
               "recovered_kind": "RECOVERED_EXPERIENCE",
               "recovery": {"stages": [
                   {"stage": "screen", "outcome": "FOUND", "screen": "LoginView"},
                   {"stage": "experience_candidate", "outcome": "EXECUTE"}]}}
              if with_recovery else None)
    store.record_step(tc, 2, "input", target_id="username_field",
                      status="RECOVERED" if with_recovery else "SUCCESS",
                      detail=detail)
    store.record_step(tc, 3, "tap", target_id="login_button", status="SUCCESS")
    if with_screen_wait:
        store.record_step(tc, 4, "wait_for", target_id="screen:HomeView",
                          status="FAILED" if timed_out_wait else "SUCCESS")
    store.record_step(tc, 5, "tap", target_id="HomeView.go_profile",
                      status="SUCCESS")
    if with_screen_wait:
        store.record_step(tc, 6, "wait_for", target_id="screen:ProfileView",
                          status="SUCCESS")
    store.end_testcase(tc, "PASS")
    store.end_run("run_1", status="PASS", exit_code=0)
    return str(db)


def _steps(*spec) -> list[TraceStep]:
    """按 `(tc_run, idx, type, target, status)` 造纯输入（不落库的单元测试用）。"""
    out = []
    for tc, idx, stype, target, status in spec:
        out.append(TraceStep(testcase_run_id=tc, step_index=idx,
                             step_type=stype, target_id=target,
                             status=status,
                             observed_at=f"2026-10-05T10:00:{idx:02d}Z"))
    return out


# --- ① 聚合出 nodes / transitions -------------------------------------------


def test_builds_nodes_and_transitions_from_trace(tmp_path):
    steps, app_id, build = read_trace_steps(_trace(tmp_path))
    g = build_runtime_graph(steps, app_id=app_id, app_build=build)

    assert app_id == APP and build == BUILD
    # 节点按 screen_id 排序输出（确定性；diff 与落库都靠它稳定）
    assert g.screens == ("HomeView", "LoginView", "ProfileView")
    # 节点与转移都按 key 排序输出（确定性；diff 与落库都靠它稳定）
    assert g.screens == ("HomeView", "LoginView", "ProfileView")
    assert [(t.from_screen, t.to_screen) for t in g.transitions] == [
        ("HomeView", "ProfileView"), ("LoginView", "HomeView")]


def test_transition_trigger_is_the_step_right_before_the_observation(tmp_path):
    """触发条件 = 紧邻目标屏观测之前的那个步骤（设计 12.2 的 CHANGED 连接键）。"""
    steps, app_id, build = read_trace_steps(_trace(tmp_path))
    g = build_runtime_graph(steps, app_id=app_id, app_build=build)

    triggers = {(t.from_screen, t.to_screen): t.trigger for t in g.transitions}
    assert triggers[("LoginView", "HomeView")] == "tap:login_button"
    assert triggers[("HomeView", "ProfileView")] == "tap:HomeView.go_profile"


def test_screen_element_target_does_not_create_runtime_node(tmp_path):
    """`Screen.element` 形态的目标**不**产生运行时节点（元数据声明 ≠ 观测）。

    若把它当观测，Runtime Graph 会混进 Source Graph 的信息，设计 12.2 的
    runtime vs source diff 就失去意义——这是本模块刻意排除的一类，钉住它。
    """
    steps, app_id, build = read_trace_steps(_trace(tmp_path))
    g = build_runtime_graph(steps, app_id=app_id, app_build=build)

    assert "password_field" not in g.screens
    assert all(not s.startswith("LoginView.") for s in g.screens)


def test_failed_screen_wait_is_not_reached(tmp_path):
    """`wait_for screen:X` **超时** → X 没到达，不进图（等待失败就是没到）。"""
    steps, app_id, build = read_trace_steps(_trace(tmp_path, timed_out_wait=True))
    g = build_runtime_graph(steps, app_id=app_id, app_build=build)

    assert "HomeView" not in g.screens
    assert g.screens == ("LoginView", "ProfileView"), \
        "ProfileView 仍被观测到（它自己的 wait 成功了）"
    assert [(t.from_screen, t.to_screen) for t in g.transitions] == [
        ("LoginView", "ProfileView")]
    assert [(t.from_screen, t.to_screen) for t in g.transitions] == [
        ("LoginView", "ProfileView")]


def test_no_observation_at_all_yields_empty_graph(tmp_path):
    """没有任何屏观测 → **空图**，不推测（E12 的底线）。

    关掉恢复观测与屏 wait 之后，剩下的只有元素级步骤——「元素找到了」不等于
    「知道在哪个屏」，所以不该产出任何节点。
    """
    steps, app_id, build = read_trace_steps(
        _trace(tmp_path, with_recovery=False, with_screen_wait=False))
    g = build_runtime_graph(steps, app_id=app_id, app_build=build)

    assert g.nodes == () and g.transitions == ()
    assert g.is_empty is True


def test_recovery_observation_survives_without_screen_waits(tmp_path):
    """没有屏 wait 时，恢复期识别到的屏仍能成图（一类证据即可）。"""
    steps, app_id, build = read_trace_steps(
        _trace(tmp_path, with_screen_wait=False))
    g = build_runtime_graph(steps, app_id=app_id, app_build=build)

    assert g.screens == ("LoginView",)
    assert g.node("LoginView").evidence == ("recovery_observed",)


# --- ② visit_count / first_seen / last_seen ---------------------------------


def test_visit_count_and_time_range_merge_across_runs():
    """跨 run 累加访问次数；first/last_seen 取最早/最晚观测时刻。"""
    steps = _steps(
        (1, 0, "wait_for", "screen:HomeView", "SUCCESS"),
        (1, 1, "tap", "go", "SUCCESS"),
        (1, 2, "wait_for", "screen:ProfileView", "SUCCESS"),
        (2, 0, "wait_for", "screen:HomeView", "SUCCESS"),
    )
    g = build_runtime_graph(steps)

    home = g.node("HomeView")
    assert home.visit_count == 2
    assert home.first_seen == "2026-10-05T10:00:00Z"
    assert home.last_seen == "2026-10-05T10:00:00Z"
    profile = g.node("ProfileView")
    assert profile.visit_count == 1
    assert profile.first_seen == "2026-10-05T10:00:02Z"


def test_first_last_seen_take_min_max_when_runs_are_out_of_order():
    """run 顺序不影响结果（按 step_index 排，不按传入顺序）。"""
    steps = _steps(
        (2, 0, "wait_for", "screen:HomeView", "SUCCESS"),
        (1, 0, "wait_for", "screen:HomeView", "SUCCESS"),
    )
    g = build_runtime_graph(steps)
    assert g.node("HomeView").visit_count == 2


# --- ③ 同一转移合并计数 -----------------------------------------------------


def test_same_transition_merges_count_across_runs():
    """同一 `(from, to, trigger)` 跨 run 合并计数（不是每个 run 一行）。"""
    steps = _steps(
        (1, 0, "wait_for", "screen:HomeView", "SUCCESS"),
        (1, 1, "tap", "login_button", "SUCCESS"),
        (1, 2, "wait_for", "screen:ProfileView", "SUCCESS"),
        (2, 0, "wait_for", "screen:HomeView", "SUCCESS"),
        (2, 1, "tap", "login_button", "SUCCESS"),
        (2, 2, "wait_for", "screen:ProfileView", "SUCCESS"),
    )
    g = build_runtime_graph(steps)

    assert len(g.transitions) == 1
    t = g.transitions[0]
    assert (t.from_screen, t.to_screen, t.trigger) == (
        "HomeView", "ProfileView", "tap:login_button")
    assert t.count == 2


def test_same_screens_different_trigger_are_two_transitions():
    """同一对屏由不同动作触发 = 两条转移（trigger 进唯一键，供 CHANGED 判据用）。"""
    steps = _steps(
        (1, 0, "wait_for", "screen:HomeView", "SUCCESS"),
        (1, 1, "tap", "go_a", "SUCCESS"),
        (1, 2, "wait_for", "screen:ProfileView", "SUCCESS"),
        (2, 0, "wait_for", "screen:HomeView", "SUCCESS"),
        (2, 1, "tap", "go_b", "SUCCESS"),
        (2, 2, "wait_for", "screen:ProfileView", "SUCCESS"),
    )
    g = build_runtime_graph(steps)

    assert len(g.transitions) == 2
    assert {t.trigger for t in g.transitions} == {"tap:go_a", "tap:go_b"}


def test_consecutive_same_screen_does_not_create_self_loop():
    """同一屏连续观测 = 一次访问，不产生自环转移。"""
    steps = _steps(
        (1, 0, "wait_for", "screen:HomeView", "SUCCESS"),
        (1, 1, "tap", "x", "SUCCESS"),
        (1, 2, "wait_for", "screen:HomeView", "SUCCESS"),
    )
    g = build_runtime_graph(steps)

    assert g.node("HomeView").visit_count == 2, "访问次数照实累加"
    assert g.transitions == (), "但不该有 HomeView → HomeView 的自环"


def test_recovery_and_wait_on_the_same_step_dedupe():
    """同一步同时给出两类证据 → 节点只 +1，evidence 记两类。"""
    step = TraceStep(
        testcase_run_id=1, step_index=0, step_type="wait_for",
        target_id="screen:LoginView", status="SUCCESS",
        detail={"recovery": {"stages": [
            {"stage": "screen", "outcome": "FOUND", "screen": "LoginView"}]}},
        observed_at="2026-10-05T10:00:00Z")
    g = build_runtime_graph([step])

    assert len(g.nodes) == 1
    assert g.node("LoginView").visit_count == 2, \
        "两条观测都算访问（证据不同）"
    assert set(g.node("LoginView").evidence) == {"wait_screen",
                                                "recovery_observed"}


# --- 纯谓词 -----------------------------------------------------------------


def test_is_screen_wait_and_observed_screens():
    ok = TraceStep(testcase_run_id=1, step_index=0, step_type="wait_for",
                   target_id="screen:HomeView", status="SUCCESS")
    assert is_screen_wait(ok) is True
    assert observed_screens(ok) == [("HomeView", "wait_screen")]

    failed = TraceStep(testcase_run_id=1, step_index=0, step_type="wait_for",
                       target_id="screen:HomeView", status="FAILED")
    assert is_screen_wait(failed) is False
    assert observed_screens(failed) == []

    element = TraceStep(testcase_run_id=1, step_index=0, step_type="wait_for",
                        target_id="LoginView.password_field",
                        status="SUCCESS")
    assert observed_screens(element) == [], "元素目标不是屏观测"


# --- ④ 落库：独立库 + 独立迁移链 + 幂等替换 ---------------------------------


def test_graph_store_migrates_its_own_chain(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    assert GRAPH_SCHEMA_VERSION == "001_graph_schema"
    conn = store._connect()
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        version = conn.execute(
            "SELECT version FROM schema_migrations").fetchone()[0]
    finally:
        conn.close()
    assert {"screen_nodes", "screen_transitions", "graph_diffs"} <= tables
    assert version == GRAPH_SCHEMA_VERSION


def test_upsert_then_load_roundtrip(tmp_path):
    trace = _trace(tmp_path)
    gdb = tmp_path / "graph.db"
    g = build_and_store(trace, gdb)

    back = GraphStore(gdb).load_graph(APP, BUILD, RUNTIME)
    assert back.screens == g.screens
    assert [(t.from_screen, t.to_screen, t.count) for t in back.transitions] == [
        (t.from_screen, t.to_screen, t.count) for t in g.transitions]
    assert back.node("LoginView").evidence == ("recovery_observed",)
    assert back.node("HomeView").evidence == ("wait_screen",)


def test_upsert_is_replace_not_accumulate(tmp_path):
    """重跑 build 必须得到同一张图（幂等）——累加会让 visit_count 翻倍。

    权威输入是 trace 全量（steps 表已累积所有 run），所以「重读全量 → 替换」
    才是正确语义；累加还需要「这次增量是哪几个 run」才能去重，而图里没有这个
    信息。
    """
    trace = _trace(tmp_path)
    gdb = tmp_path / "graph.db"
    first = build_and_store(trace, gdb)
    second = build_and_store(trace, gdb)

    back = GraphStore(gdb).load_graph(APP, BUILD, RUNTIME)
    assert back.node("HomeView").visit_count == \
        first.node("HomeView").visit_count == second.node("HomeView").visit_count
    assert len(back.nodes) == len(first.nodes)


def test_load_scopes_are_separated_by_build(tmp_path):
    """不同 build 的图各存各的（diff 按 build 关联的前提）。"""
    gdb = tmp_path / "graph.db"
    build_and_store(_trace(tmp_path, build="1024", name="t1024.db"), gdb)
    build_and_store(_trace(tmp_path, build="1025", name="t1025.db"), gdb)

    store = GraphStore(gdb)
    expect = ("HomeView", "LoginView", "ProfileView")
    assert store.load_graph(APP, "1024").screens == expect
    assert store.load_graph(APP, "1025").screens == expect
    assert (APP, "1024", RUNTIME) in store.list_scopes()
    assert (APP, "1025", RUNTIME) in store.list_scopes()


def test_load_graph_for_unknown_scope_is_empty(tmp_path):
    store = GraphStore(tmp_path / "graph.db")
    g = store.load_graph(APP, "9999")
    assert g.is_empty and g.screens == ()


def test_source_scope_is_untouched_by_runtime_writes(tmp_path):
    """runtime 写入不碰 `source_of='source'` 的行（Task 5.2 的声明面）。"""
    gdb = tmp_path / "graph.db"
    store = GraphStore(gdb)
    conn = store._connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO screen_nodes (app_id, app_build, screen_id,"
                " source_of, visit_count, evidence) VALUES (?,?,?,?,?,?)",
                (APP, BUILD, "DeclaredView", SOURCE, 0, "source_declared"))
    finally:
        conn.close()

    build_and_store(_trace(tmp_path), gdb)

    declared = store.load_graph(APP, BUILD, SOURCE)
    assert declared.screens == ("DeclaredView",)
    assert "DeclaredView" not in store.load_graph(APP, BUILD, RUNTIME).screens


# --- read_trace_steps 的范围与时间 ------------------------------------------


def test_read_trace_steps_carries_testcase_run_time(tmp_path):
    steps, _, _ = read_trace_steps(_trace(tmp_path))
    assert steps, "fixture 有步骤"
    assert all(s.observed_at for s in steps), \
        "时间取所属 testcase_run（steps 表本身没有时间戳）"
    assert [s.step_index for s in steps] == list(range(7))


def test_read_trace_steps_fails_loud_on_mixed_builds(tmp_path):
    """同一 trace 里混了多个 build → fail-loud，不挑一个。

    两个 build 的屏混进同一张图会让 diff 的 base/build 语义失效——这是
    「静默产出错误结论」的场景，必须炸。
    """
    db = tmp_path / "trace.db"
    store = TraceStore(db)
    for run_id, build in (("run_a", "1024"), ("run_b", "1025")):
        store.start_run(run_id, app_bundle_id=APP, app_build=build)
        tc = store.start_testcase(run_id, "login_001")
        store.record_step(tc, 0, "wait_for", target_id="screen:HomeView",
                          status="SUCCESS")
        store.end_testcase(tc, "PASS")
        store.end_run(run_id, status="PASS", exit_code=0)

    with pytest.raises(ValueError, match="多个 \\(app_id, app_build\\)"):
        read_trace_steps(db)
    # 显式限定 build 就能读
    steps, _, build = read_trace_steps(db, app_build="1025")
    assert build == "1025" and len(steps) == 1


def test_read_trace_steps_on_empty_db(tmp_path):
    db = tmp_path / "trace.db"
    TraceStore(db)          # 建库但不写 run
    steps, app_id, build = read_trace_steps(db)
    assert steps == [] and app_id == "" and build == ""


# --- 摘要（报告/CLI 用） ----------------------------------------------------


def test_summary_counts_without_recomputing(tmp_path):
    steps, app_id, build = read_trace_steps(_trace(tmp_path))
    g = build_runtime_graph(steps, app_id=app_id, app_build=build)
    s = g.summary()

    assert s["nodes"] == 3 and s["transitions"] == 2
    assert s["visits"] == 3
    assert s["screens"] == ["HomeView", "LoginView", "ProfileView"]
    assert s["app_build"] == BUILD
