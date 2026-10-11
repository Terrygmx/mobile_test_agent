"""Task 4.5 / P3-18：Discovery Event + 启动环境校验（设计 §7.4/§7.5 / F6 / F7）。

plan Steps 的判据：
1. 非 sandbox / real_device → **拒绝启动**（矩阵 #9 同源断言；explore 侧
   sandbox-only **无例外**——staging 也拒）；
2. 新发现 → `discovery_events` PENDING、**图未变**（探索不自动写图，E12）；
3. ACCEPT → 经 `graph/storage.py::GraphStore` **幂等**登记（evidence 标记
   `discovery`），图可查；REJECT → 图不变。
"""
from __future__ import annotations

import sqlite3

import pytest

from agents.storage import AGENT_SCHEMA_VERSION, SQLiteAgentStore
from candidates.discovery import (DISCOVERY_KINDS, DISCOVERY_REVIEW_STATUSES,
                                  DiscoveryEvent, graph_registration, register)
from graph.models import EVIDENCE_DISCOVERY, RUNTIME, SOURCE, RuntimeGraph, \
    ScreenNode
from graph.storage import GraphStore


@pytest.fixture()
def store(tmp_path) -> SQLiteAgentStore:
    return SQLiteAgentStore(tmp_path / "agent.db")


def _save(store, eid="disc_1", **kw):
    kw.setdefault("kind", "NEW_SCREEN")
    kw.setdefault("detail", {"screen_id": "SettingsView"})
    return store.save_discovery_event(eid, **kw)


# --- 1. 迁移 006 ---------------------------------------------------------------


def test_migration_006_lands_and_version_follows_the_chain(tmp_path):
    db = tmp_path / "agent.db"
    SQLiteAgentStore(db)
    SQLiteAgentStore(db)            # 幂等重放
    assert AGENT_SCHEMA_VERSION == "006_discovery_events"
    conn = SQLiteAgentStore(db)._connect()
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "discovery_events" in tables


def test_ddl_check_vocabularies_match_python_constants(store):
    """SQL CHECK 词表 ↔ Python 常量逐值一致（手法同 001 的
    `test_state_check_constraint_matches_enum`：加值忘改 SQL 会红）。"""
    conn = store._connect()
    try:
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='discovery_events'"
        ).fetchone()[0]
    finally:
        conn.close()
    for kind in DISCOVERY_KINDS:
        assert f"'{kind}'" in ddl, f"{kind} 不在 DDL 的 kind CHECK 里"
    for status in DISCOVERY_REVIEW_STATUSES:
        assert f"'{status}'" in ddl, f"{status} 不在 DDL 的 review_status 里"
    # 反向：DDL 里没有 Python 常量之外的词（漏加常量也要红）
    import re
    kind_check = re.search(r"kind IN \(([^)]*)\)", ddl).group(1)
    assert {s.strip().strip("'") for s in kind_check.split(",")} == \
        set(DISCOVERY_KINDS)


def test_bad_kind_and_status_are_rejected_by_the_ddl(store):
    conn = store._connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO discovery_events (event_id, kind, review_status,"
                " created_at) VALUES ('x','NOT_A_KIND','PENDING','now')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO discovery_events (event_id, kind, review_status,"
                " created_at) VALUES ('y','ANOMALY','MAYBE','now')")
    finally:
        conn.close()


# --- 2. 存储：只写 PENDING、已决不可覆写 ---------------------------------------


def test_save_writes_pending_and_round_trips(store):
    row = _save(store, detail={"screen_id": "SettingsView", "from": "HomeView"},
                exploration_task_id="task_x")
    assert row["review_status"] == "PENDING"
    assert row["kind"] == "NEW_SCREEN"
    assert row["detail"]["screen_id"] == "SettingsView"
    assert row["exploration_task_id"] == "task_x"
    assert row["reviewer"] is None and row["reviewed_at"] is None
    got = store.get_discovery_event("disc_1")
    assert got == row


def test_review_status_is_not_a_save_parameter(store):
    """`review_status` 的唯一写路径是 `decide_discovery_event`（F6）——
    形参删掉而不是加断言：调用方传它会以 `TypeError` fail-loud。"""
    with pytest.raises(TypeError):
        store.save_discovery_event("disc_x", kind="ANOMALY",
                                   review_status="ACCEPT")


def test_save_refuses_to_overwrite_a_decided_event(store):
    _save(store)
    store.decide_discovery_event("disc_1", review_status="ACCEPT",
                                 reviewer="op")
    with pytest.raises(ValueError, match="已人工决策"):
        _save(store, detail={"screen_id": "Other"})


def test_list_filters_by_kind_and_status(store):
    _save(store, "d1", kind="NEW_SCREEN")
    _save(store, "d2", kind="ANOMALY")
    store.decide_discovery_event("d2", review_status="REJECT", reviewer="op",
                                 note="误报")
    assert {r["event_id"] for r in store.list_discovery_events()} == {"d1", "d2"}
    assert {r["event_id"] for r in store.list_discovery_events(kind="ANOMALY")} \
        == {"d2"}
    assert {r["event_id"]
            for r in store.list_discovery_events(review_status="PENDING")} \
        == {"d1"}


def test_row_model_round_trips(store):
    row = _save(store, detail={"screen_id": "SettingsView"})
    ev = DiscoveryEvent.from_store_dict(row)
    assert ev.event_id == "disc_1" and ev.kind == "NEW_SCREEN"
    assert DiscoveryEvent(**{k: v for k, v in ev.to_store_dict().items()
                             if k != "created_at"}).kind == "NEW_SCREEN"


# --- 3. 图登记：kind → 资产（纯函数，F13） -------------------------------------


def test_graph_registration_maps_kinds_to_graph_assets():
    screen = {"kind": "NEW_SCREEN",
              "detail": {"screen_id": "SettingsView"}}
    assert graph_registration(screen) == ("node", {"screen_id": "SettingsView"})
    trans = {"kind": "NEW_TRANSITION",
             "detail": {"from_screen": "Home", "to_screen": "Settings",
                        "trigger": "tap:gear"}}
    assert graph_registration(trans) == (
        "transition", {"from_screen": "Home", "to_screen": "Settings",
                       "trigger": "tap:gear"})
    # NEW_ELEMENT / ANOMALY 不是图资产 → 不进图（返回 None，不是空 dict）
    assert graph_registration({"kind": "NEW_ELEMENT",
                               "detail": {"screen_id": "S"}}) is None
    assert graph_registration({"kind": "ANOMALY", "detail": {}}) is None


def test_graph_registration_needs_the_identifying_fields():
    """缺识别字段是**上游写错了**（发事件时就该带上），fail-loud 而不是
    静默登记一个无名节点。"""
    with pytest.raises(ValueError, match="screen_id"):
        graph_registration({"kind": "NEW_SCREEN", "detail": {}})
    with pytest.raises(ValueError, match="from_screen"):
        graph_registration({"kind": "NEW_TRANSITION",
                            "detail": {"to_screen": "Settings"}})
    with pytest.raises(ValueError, match="to_screen"):
        graph_registration({"kind": "NEW_TRANSITION",
                            "detail": {"from_screen": "Home"}})


# --- 4. ACCEPT → 图幂等登记；REJECT → 图不变 ------------------------------------


def test_register_is_idempotent_and_marks_the_discovery_evidence(tmp_path):
    gs = GraphStore(tmp_path / "graph.db")
    ev = {"kind": "NEW_SCREEN", "detail": {"screen_id": "SettingsView"}}
    assert register(gs, ev, app_id="demo", app_build="1026") is True
    # 幂等：重复登记不重复计数，第二次返回 False
    assert register(gs, ev, app_id="demo", app_build="1026") is False
    g = gs.load_graph("demo", "1026", SOURCE)
    assert g.screens == ("SettingsView",)
    node = g.node("SettingsView")
    assert node.evidence == (EVIDENCE_DISCOVERY,)
    assert node.visit_count == 0, "发现不是访问——visit_count 保持 0"


def test_register_returns_none_for_kinds_that_are_not_graph_assets(tmp_path):
    gs = GraphStore(tmp_path / "graph.db")
    assert register(gs, {"kind": "ANOMALY", "detail": {}}, app_id="demo",
                    app_build="1026") is None
    assert gs.load_graph("demo", "1026", SOURCE).is_empty


def test_register_does_not_touch_the_runtime_face(tmp_path):
    """发现进的是**声明面**（人确认过 = 已知资产，E12：运行时面只来自 trace）。
    在运行时面登记会让「观测」与「确认」混成一体，设计 12.2 的 diff 失去意义。"""
    gs = GraphStore(tmp_path / "graph.db")
    gs.upsert_graph(RuntimeGraph(
        app_id="demo", app_build="1026", source_of=RUNTIME,
        nodes=(ScreenNode("HomeView", source_of=RUNTIME,
                          evidence=("wait_screen",)),)))
    register(gs, {"kind": "NEW_SCREEN", "detail": {"screen_id": "Settings"}},
             app_id="demo", app_build="1026")
    assert gs.load_graph("demo", "1026", RUNTIME).screens == ("HomeView",)
    assert gs.load_graph("demo", "1026", SOURCE).screens == ("Settings",)


def test_register_transition_is_idempotent(tmp_path):
    gs = GraphStore(tmp_path / "graph.db")
    ev = {"kind": "NEW_TRANSITION",
          "detail": {"from_screen": "Home", "to_screen": "Settings",
                     "trigger": "tap:gear"}}
    assert register(gs, ev, app_id="demo", app_build="1026") is True
    assert register(gs, ev, app_id="demo", app_build="1026") is False
    g = gs.load_graph("demo", "1026", SOURCE)
    assert len(g.transitions) == 1
    assert g.transitions[0].from_screen == "Home"
    assert g.transitions[0].count == 0, "发现不是走过——count 保持 0"


# --- 5. 统一审核入口（F6：同一 `decide_candidate`） -----------------------------


def _review(store, eid, decision, *, graph_store=None, note=None):
    from candidates.review import decide_candidate
    return decide_candidate(store, "discovery", eid, decision, "op", note,
                            graph_store=graph_store)


def test_accept_registers_into_the_graph_and_reject_leaves_it_alone(
        store, tmp_path):
    gs = GraphStore(tmp_path / "graph.db")
    _save(store, "d_acc", detail={"screen_id": "SettingsView"})
    _save(store, "d_rej", detail={"screen_id": "GhostView"})

    row, sha = _review(store, "d_acc", "ACCEPT", graph_store=gs)
    assert row["review_status"] == "ACCEPT" and sha is None
    assert gs.load_graph("", "", SOURCE).screens == ("SettingsView",)

    row, sha = _review(store, "d_rej", "REJECT", graph_store=gs, note="误报")
    assert row["review_status"] == "REJECT" and sha is None
    assert gs.load_graph("", "", SOURCE).screens == ("SettingsView",), \
        "REJECT 不得改图"


def test_discovery_review_needs_no_git_or_testcase_inputs(store, tmp_path):
    """与 test 候选不同：发现事件的 ACCEPT 不落盘、不 commit——所以不需要
    `repo_root` / `repository` / `secrets`（那些是 test 分支的输入）。"""
    gs = GraphStore(tmp_path / "graph.db")
    _save(store)
    row, _ = _review(store, "disc_1", "ACCEPT", graph_store=gs)
    assert row["review_status"] == "ACCEPT"


def test_test_kind_accept_still_requires_its_inputs(store):
    """反向闸门：test 分支缺输入仍 fail-loud（把必填改可选不能变成静默）。"""
    from candidates.review import CandidateReviewError, decide_candidate
    store.save_candidate("c1", testcase_yaml="id: c1\nsuite: smoke\nsteps: []",
                         generation_evidence={}, fingerprint="fp")
    with pytest.raises(CandidateReviewError, match="repo_root"):
        decide_candidate(store, "test", "c1", "ACCEPT", "op")


def test_accept_needs_a_graph_store(store):
    from candidates.review import CandidateReviewError
    _save(store)
    with pytest.raises(CandidateReviewError, match="graph_store"):
        _review(store, "disc_1", "ACCEPT")


def test_reject_needs_a_note(store):
    from candidates.review import CandidateReviewError
    _save(store)
    with pytest.raises(CandidateReviewError, match="note"):
        _review(store, "disc_1", "REJECT")


def test_already_decided_event_is_refused(store, tmp_path):
    from candidates.review import CandidateReviewError
    gs = GraphStore(tmp_path / "graph.db")
    _save(store)
    _review(store, "disc_1", "ACCEPT", graph_store=gs)
    with pytest.raises(CandidateReviewError, match="不可二次决策"):
        _review(store, "disc_1", "ACCEPT", graph_store=gs)


# --- 6. 启动期环境校验（矩阵 #9 同源；F7 / 设计 §7.4） -------------------------


def _check(env_kind, device_type, *, enabled=True):
    from agents.policy_config import PolicyConfig
    from cli.main import _check_exploration_env
    policy = PolicyConfig.model_validate(
        {"autonomous": {"enabled": enabled}})
    return _check_exploration_env(policy, env_kind=env_kind,
                                  device_type=device_type, command="explore")


def test_exploration_refuses_every_non_sandbox_env():
    """设计 §7.4：探索只能 sandbox——**staging 也不例外**（口径必守，
    review_p3_task12 §4）。这条与 `_check_autonomous_env` 的「sandbox 或
    staging」文案不同，是刻意的。"""
    for env in ("staging", "production", "SANDBOX ", "bogus", None):
        with pytest.raises(SystemExit) as e:
            _check(env, "simulator")
        assert "F7" in str(e.value)
    assert _check("sandbox", "simulator") is None


def test_exploration_refuses_real_devices():
    """设计 §7.4：真机只跑已审核用例，不跑自由探索（矩阵 #9 同源）。"""
    for device in ("real_device", "real-device", "REAL_DEVICE"):
        with pytest.raises(SystemExit) as e:
            _check("sandbox", device)
        assert "模拟器" in str(e.value)
    with pytest.raises(SystemExit):
        _check("sandbox", "bogus_device")


def test_exploration_refusal_is_startup_not_runtime():
    """F7：**启动时**拒绝（`SystemExit`），不是跑一半才拦——所以判据是纯函数、
    零副作用，且唯一出口是 `SystemExit`（`ValueError` 会被 `except Exception`
    兜底的调用方吞掉，review_p3_task12 P3-4 同款）。"""
    with pytest.raises(SystemExit):
        _check("staging", "simulator")
    try:
        _check("staging", "simulator")
    except SystemExit:
        pass
    except Exception as e:                     # pragma: no cover - 反例保护
        pytest.fail(f"安全闸门只许 SystemExit 一个出口，实得 {type(e).__name__}")


def test_the_two_layers_are_separate_and_both_apply():
    """两层是**与**的关系、各管各的判据（不是重复实现）：

    - `_check_autonomous_env`（M1，自主命令公共前置）管总开关与 production；
    - `_check_exploration_env`（本任务）管 sandbox-only 与 simulator。

    ⚠️ 把总开关塞进本函数会让「explore 为什么拒」的判据散成两处，而公共前置
    对 `plan`/`generate`/`diagnose` 已经在管它了——本任务**不复制**那条判据，
    只补探索特有的两条。本用例钉住这个分工。
    """
    from cli.main import _check_autonomous_env
    from agents.policy_config import PolicyConfig

    off = PolicyConfig.model_validate({"autonomous": {"enabled": False}})
    with pytest.raises(SystemExit, match="autonomous.enabled"):
        _check_autonomous_env(off, env_kind="sandbox", command="explore")
    # 本函数**不管**总开关：enabled=False 但 env/device 合法 → 放行
    # （在 CLI 里它排在公共前置之后，那时 enabled 已经过了）
    assert _check("sandbox", "simulator", enabled=False) is None


# --- 7. 探索主循环接线：新屏 → 发现事件，图不变（E12 / 设计 §7.5 前两段） --------


def _explorer(tmp_path, *, pages, discovery_enabled=True, graph_screens=(),
              store=None, task_id="task_exp"):
    from agents.tools import AgentToolkit
    from executor.guard import EnvKind, Guard
    from explorer.agent import Explorer
    from explorer.policy import ProposedAction
    from tests.fault_injection.fi_support import FakeExecutor, drift_repo

    class _PagedExec(FakeExecutor):
        def __init__(self):
            super().__init__()
            self._pages = list(pages)
            self._i = 0

        def tap(self, strategies):
            self.tap_calls += 1
            self._i = min(self._i + 1, len(self._pages) - 1)

        def page_source(self):
            return self._pages[self._i]

    repo = drift_repo(tmp_path)
    if store is None:
        store = SQLiteAgentStore(tmp_path / "agent.db")
        store.create_task(task_id, agent_type="explorer", goal="explore")
    ex = _PagedExec()
    toolkit = AgentToolkit(
        guard=Guard(EnvKind.SANDBOX), executor=ex,
        locator_for=lambda t: [{"type": "accessibility_id", "value": t}],
        repository=repo, agent_db=store, task_id=task_id)
    graph = RuntimeGraph(
        app_id="demo", app_build="1026", source_of=SOURCE,
        nodes=tuple(ScreenNode(s, source_of=SOURCE,
                               evidence=("source_declared",))
                    for s in graph_screens))
    calls = {"n": 0}

    def decider(observation, g, visited):
        calls["n"] += 1
        return (ProposedAction(tool="tap", target="login_button")
                if calls["n"] <= 2 else None)

    explorer = Explorer(toolkit=toolkit, graph=graph, agent_db=store,
                        task_id=task_id, decider=decider, repository=repo,
                        discovery_enabled=discovery_enabled)
    return explorer, store


_HOME = "<App><Node name='screen.HomeView' visible='true'/></App>"
# ⚠️ 屏名必须落在 `drift_repo` 登记过的屏里（HomeView / ProfileView /
# DetailView）——`current_screen` 认不出屏时 `screen=None`，而「屏判定不出来」
# 正是**不该**记发现的那一类（没有屏名就没有可登记的东西）。
_NEW = "<App><Node name='screen.ProfileView' visible='true'/></App>"


def test_new_screen_is_recorded_as_a_pending_discovery_event(tmp_path):
    """设计 §7.5 前两段：撞见图里没有的屏 → 落一条 PENDING 事件。"""
    explorer, store = _explorer(tmp_path, pages=[_HOME, _NEW])
    explorer.run()
    events = store.list_discovery_events()
    assert [e["kind"] for e in events] == ["NEW_SCREEN"]
    assert events[0]["detail"]["screen_id"] == "ProfileView"
    assert events[0]["review_status"] == "PENDING"
    assert events[0]["exploration_task_id"] == "task_exp"
    assert events[0]["reviewer"] is None


def test_discovery_recording_does_not_write_the_graph(tmp_path):
    """**E12 / 设计 §7.5**：探索不自动写图——登记图只在人工 ACCEPT 之后。

    （图由调用方注入，本用例断言的是「explorer 全程没碰过任何图库」：
    它连 GraphStore 都没有——`Explorer` 的构造参数里没有那个依赖。）
    """
    import inspect
    from explorer.agent import Explorer
    params = inspect.signature(Explorer.__init__).parameters
    assert "graph_store" not in params, (
        "Explorer 不得持有图库写入通路——发现只落 discovery_events，"
        "写图归 candidates/review.py 的人工 ACCEPT 分支（设计 §7.5）")
    explorer, store = _explorer(tmp_path, pages=[_HOME, _NEW])
    explorer.run()
    assert store.list_discovery_events(), "事件该落库"


def test_known_screens_are_not_recorded(tmp_path):
    """已在图（声明面）里的屏不是发现。"""
    explorer, store = _explorer(tmp_path, pages=[_HOME, _NEW],
                                graph_screens=("HomeView", "ProfileView"))
    explorer.run()
    assert store.list_discovery_events() == []


def test_discovery_is_off_by_default(tmp_path):
    """默认关（Task 4.6 的 `mta explore` 打开）——否则 4.1–4.4 的构造场景
    会把测试噪声记进人工审核队列。"""
    explorer, store = _explorer(tmp_path, pages=[_HOME, _NEW],
                                discovery_enabled=False)
    explorer.run()
    assert store.list_discovery_events() == []


def test_same_screen_across_a_rerun_does_not_duplicate(tmp_path):
    """`event_id` 用 F8 的同一哈希原语（`stable_hash`）——重跑同一次探索
    得到同一个 id，upsert 幂等吸收（不线性增长重复行）。"""
    explorer, store = _explorer(tmp_path, pages=[_HOME, _NEW])
    explorer.run()
    first = store.list_discovery_events()
    assert len(first) == 1
    # 同一 task 重跑（同一个库、同一个 task_id → 同一个 event_id）
    explorer2, _ = _explorer(tmp_path, pages=[_HOME, _NEW], store=store)
    explorer2.run()
    second = store.list_discovery_events()
    assert len(second) == 1, f"重跑不得新增行，实得 {len(second)}"


def test_recording_failure_does_not_break_the_exploration(tmp_path):
    """发现是**副产品**：落库失败如实留痕，但主循环照常收尾（不中断）。"""
    explorer, store = _explorer(tmp_path, pages=[_HOME, _NEW])
    explorer._toolkit.agent_db = None        # 落库通路被打断
    result = explorer.run()
    assert result.outcome.value == "COMPLETED", "发现落库失败不该改变探索结论"
    trace = store.get_trace("task_exp")
    assert any(e.result == "DISCOVERY_FAILED" for e in trace),         "失败要留痕（审计能回答「为什么没记下来」）"
    assert any(e.result == "DISCOVERY_RECORDED" for e in trace) is False
