"""Task 4.6 / P3-19：`mta explore` CLI（设计 §7 / §12；M4 的出口）。

判据（plan Task 4.6 Steps）：

1. **F7 启动期拒绝**：production（公共前置）与 **staging**（探索专属前置，
   review_p3_task12 §4 的口径必守）一律拒，且**在写任何东西之前**；
2. **主路径**：sandbox + simulator → observe→frontier→guard→execute→verify
   跑通；撞见图里没有的屏 → `discovery_events` PENDING；**图不变**（E12）；
3. **预算生效**（F11）：`--budget` 文件 → `*_BUDGET_EXCEEDED` **正常退出**
   （矩阵 #11：超限是结论不是错误）；
4. **HIGH+ 被拦**（矩阵 #13 前半）：审计留痕、不执行、循环继续；
5. **前置错误 → exit 3**：预算文件坏 / metadata 读不到 / 设备装配失败。

fixture 用**真 sqlite（trace + agent + graph）+ 真 Repository + 脚本化
Executor**。设备侧只替掉 `DeviceSession` 与 `Executor` **两个类**
（`wire_device_stack` 里它们是**调用时 import**，故 monkeypatch 生效）——
**装配路径本身与生产逐字相同**（`test_cli_run.py` 的既有做法），不新开
「测试专用装配分支」。

⚠️ `Executor` 的替身必须**真被 `wire_device_stack` 构造**：`cli/pipeline.py`
在函数体内 `from executor.executor import Executor`，所以 patch
`executor.executor.Executor` 才能命中（patch `cli.pipeline.Executor` 无效——
那里根本没有这个名字）。
"""
from __future__ import annotations

import json

import pytest

from agents.storage import SQLiteAgentStore
from cli.main import main
from graph import SOURCE, GraphStore, RuntimeGraph, ScreenNode, ScreenTransition
from tracer.storage import TraceStore

APP_FILE = "Demo.swift"

# 声明面：只有 HomeView（**ProfileView 故意不声明**——它就是要被探索撞见的
# 「图里没有的屏」，见 discovery 的判据 `screen not in _known_screens()`）。
_METADATA_SCREENS = ["HomeView"]
# 已确认的发现：HomeView --tap:HomeView.go_profile--> ProfileView
# （graph.db 的声明面；探索的 frontier 就来自这里——metadata 没有导航声明，
#  所以「有出边可探」这件事只能由人工确认过的发现提供）
_SOURCE_TRANSITION = ("HomeView", "ProfileView", "tap:HomeView.go_profile")

_HOME = "<App><Node name='screen.HomeView' visible='true'/></App>"
_PROFILE = "<App><Node name='screen.ProfileView' visible='true'/></App>"


def _element_yaml(elem: str, screen: str, *, risk: str = "LOW") -> str:
    return (f"schema_version: '1.0'\nkind: element\nid: {elem}\n"
            f"screen: {screen}\ntype: button\nstrategies:\n"
            f"- type: accessibility_id\n  value: {elem}\n  origin: source\n"
            f"  source_file: {APP_FILE}\n  source_line: 1\n"
            f"metadata:\n  origin: source\n  risk: {risk}\n")


def _screen_yaml(screen: str) -> str:
    return (f"schema_version: '1.0'\nkind: screen\nid: {screen}\n"
            f"marker: screen.{screen}\nkind_hint: page\n"
            f"metadata:\n  risk: LOW\n  origin: source\n")


def _metadata() -> dict:
    return {"app_version": "debug", "build": "local", "git_commit": "deadbeef",
            "screens": list(_METADATA_SCREENS),
            "screen_elements": [{"name": "HomeView", "elements": [
                {"id": "go_profile", "accessibility_id": "go_profile",
                 "type": "button", "resolution_type": "literal",
                 "source": {"file": APP_FILE, "line": 1}}]}]}


class _StubElement:
    """`Executor` 契约下的最小元素（tap 只需 `click()`）。"""

    def click(self) -> None:
        pass

    def clear(self) -> None:
        pass

    def send_keys(self, value) -> None:
        pass


class _ScriptedExecutor:
    """脚本化 Executor：`page_source` 按 tap 次数推进页面。

    **只实现 Agent 工具层会碰到的方法**（`find` / `tap` / `input` / `swipe` /
    `page_source` / `screenshot`）——多写一个「生产里没有的」方法就等于在测试里
    发明契约。`wire_device_stack` 用 `Executor(ds)` 构造它，故 `__init__` 收
    `ds` 并忽略（真 Executor 也只用它取 driver）。
    """

    def __init__(self, ds, *, pages=None):
        self.ds = ds
        self.pages = list(pages or [_HOME])
        self.taps: list[list] = []
        self._i = 0

    def find(self, locator):
        return _StubElement()

    def find_all(self, locator):
        return [_StubElement()]

    def tap(self, locator) -> None:
        self.taps.append(list(locator))
        self._i = min(self._i + 1, len(self.pages) - 1)

    def input(self, locator, value) -> None:
        pass

    def swipe(self, direction) -> None:
        pass

    def screenshot(self, path) -> None:
        pass

    def page_source(self) -> str:
        return self.pages[self._i]


def _install_fake_device(monkeypatch, pages, *, tap_error=None):
    """把 `DeviceSession` / `Executor` 换成替身（装配路径不动）。"""
    import executor.executor as ex_mod
    import session.device_session as ds_mod

    made: list[_ScriptedExecutor] = []
    launched: list[str] = []          # `activate_app` 的调用记录（见下）

    class _FakeDriver:
        def quit(self):
            pass

        def implicitly_wait(self, seconds):
            pass

        def activate_app(self, bundle_id):
            # `cmd_explore` 的「把 App 带到前台」那一步（5b）走的就是这里——
            # 真机实测：不 activate 时 `page_source` 是 springboard，
            # `current_screen` 恒 UNKNOWN → frontier 恒空（见该处注释）。
            launched.append(bundle_id)

    class _FakeDS:
        def __init__(self, url, caps):
            self.url, self.caps, self.driver = url, caps, None

        def connect(self):
            self.driver = _FakeDriver()
            return self.driver

        def ensure_alive(self):
            return self.driver

    def _make(ds):
        ex = _ScriptedExecutor(ds, pages=pages)
        made.append(ex)
        return ex

    monkeypatch.setattr(ds_mod, "DeviceSession", _FakeDS)
    monkeypatch.setattr(ds_mod, "resolve_local_caps",
                        lambda udid, bundle_id: {"udid": udid,
                                                 "bundleId": bundle_id})
    monkeypatch.setattr(ex_mod, "Executor", _make)
    return made, launched


@pytest.fixture()
def proj(tmp_path):
    """自洽的 explore 工程：metadata + Repository + 空 trace + 声明面图。"""
    gen = tmp_path / "repository" / "generated" / "local"
    (gen / "elements").mkdir(parents=True)
    (gen / "screens").mkdir(parents=True)
    (gen / "elements" / "HomeView.yaml").write_text(
        _element_yaml("go_profile", "HomeView"), encoding="utf-8")
    (gen / "elements" / "HomeView.yaml").write_text(
        _element_yaml("go_profile", "HomeView")
        + "---\n" + _element_yaml("pay_button", "HomeView", risk="HIGH"),
        encoding="utf-8")
    for screen in ("HomeView", "ProfileView"):
        (gen / "screens" / f"{screen}.yaml").write_text(
            _screen_yaml(screen), encoding="utf-8")
    (gen / "source_metadata.json").write_text(
        json.dumps(_metadata(), ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "out"
    out.mkdir()
    graph_db = out / "graph.db"
    store = GraphStore(graph_db)
    store.upsert_graph(RuntimeGraph(
        app_id="com.demo", app_build="local", source_of=SOURCE,
        nodes=(ScreenNode("HomeView", source_of=SOURCE, visit_count=0,
                          evidence=("source_declared",)),),
        transitions=(ScreenTransition(*_SOURCE_TRANSITION, source_of=SOURCE,
                                      count=0),)))

    return {"tmp": tmp_path, "gen": gen, "graph_db": graph_db,
            "trace_db": out / "trace.db", "agent_db": out / "agent.db",
            "suites_root": tmp_path / "suites"}


def _argv(proj, *extra) -> list[str]:
    return ["explore", "--goal", "探索某页面未覆盖状态",
            "--generated", str(proj["gen"]),
            "--suites-root", str(proj["suites_root"]),
            "--db", str(proj["trace_db"]),
            "--graph-db", str(proj["graph_db"]),
            "--agent-db", str(proj["agent_db"]),
            "--bundle-id", "com.demo", "--udid", "UDID-1", *extra]


# --- 1. 注册与参数 ------------------------------------------------------------


def test_explore_is_registered_and_goal_is_required():
    """`--goal` 必填（它进 `agent_tasks.goal`，是审计的读法）。"""
    from cli.main import build_parser

    args = build_parser().parse_args(
        ["explore", "--goal", "g", "--agent-db", "/tmp/x.db"])
    assert args.command == "explore" and args.goal == "g"
    assert args.env_kind == "sandbox" and args.device_type == "simulator"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["explore"])


# --- 2. F7 启动期拒绝（矩阵 #9 同源；口径必守 review_p3_task12 §4） -------------


def test_production_is_refused_before_anything_is_written(proj):
    """矩阵 #1：production 下自主命令**启动即拒**，连库文件都不该建。"""
    fresh = proj["tmp"] / "untouched.db"
    with pytest.raises(SystemExit) as e:
        main(_argv(proj, "--env-kind", "production", "--agent-db", str(fresh)))
    assert "F7" in str(e.value)
    assert not fresh.exists(), "前置在写任何东西之前"


def test_staging_is_refused_too(proj):
    """**口径必守**（review_p3_task12 §4）：`explore` 在 staging **也拒**。

    `_check_autonomous_env` 的拒绝文案对 plan/generate 准确（它们确实允许
    staging），对 explore 不准确——设计 §7.4 要求探索 sandbox-only 且**不能
    有例外**。若只过公共前置，staging 会被放行而文案还写着「sandbox 或
    staging」——**文案与行为打架**。
    """
    with pytest.raises(SystemExit) as e:
        main(_argv(proj, "--env-kind", "staging"))
    msg = str(e.value)
    assert "F7" in msg and "sandbox" in msg
    assert not proj["agent_db"].exists(), "staging 拒绝也必须发生在写库之前"


def test_real_device_is_refused(proj):
    """设计 §7.4：真机只运行**已审核**的用例，不跑自由探索。"""
    with pytest.raises(SystemExit) as e:
        main(_argv(proj, "--device-type", "real_device"))
    assert "F7" in str(e.value)
    assert not proj["agent_db"].exists()


def test_the_two_preflight_layers_are_both_called_in_order():
    """两层前置**都在** `cmd_explore` 里、且公共前置在前（F7 的语义）。

    AST 判定（不吃 docstring 的字面亏）：两个调用节点都要有，且
    `_check_autonomous_env` 的行号小于 `_check_exploration_env`。
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path("cli/main.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "cmd_explore")
    lines = {}
    for call in ast.walk(fn):
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id in ("_check_autonomous_env",
                                     "_check_exploration_env")):
            lines[call.func.id] = call.lineno
    assert set(lines) == {"_check_autonomous_env", "_check_exploration_env"}, \
        f"cmd_explore 必须过两层前置，实得 {sorted(lines)}"
    assert lines["_check_autonomous_env"] < lines["_check_exploration_env"], \
        "公共前置（总开关 + production）必须排在探索专属前置之前"


# --- 3. 主路径：observe → frontier → execute → discovery ----------------------


def test_explore_reaches_a_new_screen_and_records_a_discovery_event(
        proj, monkeypatch, capsys):
    """设计 §7.5 前两段：撞见图里没有的屏 → PENDING 事件，**不写图**。

    屏的「已知」判据是**声明面**（`explorer.agent._known_screens`）——
    ProfileView 不在声明面里（metadata 与 graph.db 都只声明 HomeView），
    所以它是**发现**。
    """
    _made, _launched = _install_fake_device(monkeypatch, [_HOME, _PROFILE])
    assert main(_argv(proj)) == 0
    out = capsys.readouterr().out
    assert "outcome=COMPLETED" in out or "outcome=FRONTIER_EMPTY" in out

    events = SQLiteAgentStore(proj["agent_db"]).list_discovery_events()
    assert [e["kind"] for e in events] == ["NEW_SCREEN"]
    assert events[0]["detail"]["screen_id"] == "ProfileView"
    assert events[0]["review_status"] == "PENDING"
    assert events[0]["exploration_task_id"].startswith("exp_")
    assert "discovery:" in out


def test_discovery_does_not_touch_the_graph(proj, monkeypatch):
    """E12 / 设计 §7.5：探索**不自动写图**——登记只在人工 ACCEPT 之后。

    断言的是「声明面逐字未变」：把库读回来与播种时的那一条比对。
    """
    before = GraphStore(proj["graph_db"]).load_graph("com.demo", "local", SOURCE)
    _made, _launched = _install_fake_device(monkeypatch, [_HOME, _PROFILE])
    assert main(_argv(proj)) == 0
    after = GraphStore(proj["graph_db"]).load_graph("com.demo", "local", SOURCE)
    assert [(n.screen_id, n.evidence) for n in after.nodes] == \
        [(n.screen_id, n.evidence) for n in before.nodes]
    assert after.transitions == before.transitions
    assert "ProfileView" not in after.screens, "探索不得把新屏写进图"


def test_the_exploration_task_is_auditable(proj, monkeypatch):
    """审计根基：`agent_tasks.goal` + `agent_trace` 每步留痕（F1）。"""
    _made, _launched = _install_fake_device(monkeypatch, [_HOME, _PROFILE])
    assert main(_argv(proj)) == 0
    store = SQLiteAgentStore(proj["agent_db"])
    tasks = [t for t in [store.get_task(i) for i in
                         [r for r in _task_ids(store)]]]
    assert len(tasks) == 1
    task = tasks[0]
    assert task.agent_type == "explorer"
    assert task.goal == "探索某页面未覆盖状态"
    assert task.outcome is not None and task.state.value == "IDLE"
    trace = store.get_trace(task.task_id)
    assert trace, "每步都要落 agent_trace"
    assert any(e.decision.get("tool") == "tap" for e in trace)


def _task_ids(store):
    conn = store._connect()
    try:
        return [r["task_id"] for r in
                conn.execute("SELECT task_id FROM agent_tasks").fetchall()]
    finally:
        conn.close()


# --- 4. 预算（F11 / 矩阵 #11） -------------------------------------------------


def test_budget_is_enforced_and_is_not_an_error(proj, monkeypatch, capsys,
                                                tmp_path):
    """矩阵 #11：预算耗尽 → `*_BUDGET_EXCEEDED` **正常退出**（exit 0）。

    「超限即停、不自动放宽重试」是**结论**，不是失败——退出码留给前置错误。
    """
    b = tmp_path / "budget.yaml"
    b.write_text("max_steps: 0\nmax_duration_seconds: 600\n"
                 "max_llm_calls: 20\nmax_repeated_state: 3\n",
                 encoding="utf-8")
    _made, _launched = _install_fake_device(monkeypatch, [_HOME, _PROFILE])
    assert main(_argv(proj, "--budget", str(b))) == 0
    out = capsys.readouterr().out
    assert "outcome=STEPS_BUDGET_EXCEEDED" in out
    assert "steps=0" in out


def test_budget_file_is_the_same_shape_as_the_policy_section(proj, monkeypatch,
                                                             tmp_path):
    """`--budget` 是**同结构覆盖**：只写一维、其余回落该段默认值。"""
    b = tmp_path / "budget.yaml"
    b.write_text("max_steps: 1\n", encoding="utf-8")
    _made, _launched = _install_fake_device(monkeypatch, [_HOME, _PROFILE])
    assert main(_argv(proj, "--budget", str(b))) == 0
    store = SQLiteAgentStore(proj["agent_db"])
    task = store.get_task(_task_ids(store)[0])
    assert task.constraints["budget"]["max_steps"] == 1
    assert task.constraints["budget"]["max_llm_calls"] == 20


def test_a_broken_budget_file_is_a_preflight_error(proj, capsys, tmp_path):
    """坏预算文件 → exit 3（前置错误），**不是** traceback、也不是静默回落。"""
    b = tmp_path / "budget.yaml"
    b.write_text("max_steps: true\n", encoding="utf-8")
    assert main(_argv(proj, "--budget", str(b))) == 3
    out = capsys.readouterr().out
    assert out.startswith("PREFLIGHT ERROR:")
    assert not proj["agent_db"].exists(), "前置在写任何东西之前"


# --- 5. HIGH+ 拦截（矩阵 #13 前半的 CLI 端） -----------------------------------


def test_high_risk_action_is_blocked_and_audited(proj, monkeypatch, capsys):
    """矩阵 #13 前半：HIGH 目标被探索侧闸拦下——**不执行**、审计留痕、
    循环继续（拦截是「这步没做成」，不是「探索失败」）。

    声明面里**只**放 `tap:HomeView.pay_button`（HIGH）——它是唯一的 frontier
    项，故「没执行」只可能是被闸门拦下的（若同时留着 LOW 的 `go_profile`，
    它会先跑掉，断言就测不到拦截了）。
    """
    store = GraphStore(proj["graph_db"])
    g = store.load_graph("com.demo", "local", SOURCE)
    store.upsert_graph(RuntimeGraph(
        app_id=g.app_id, app_build=g.app_build, source_of=SOURCE,
        nodes=g.nodes,
        transitions=(ScreenTransition(
            "HomeView", "ProfileView", "tap:HomeView.pay_button",
            source_of=SOURCE, count=0),)))

    made, _launched = _install_fake_device(monkeypatch, [_HOME, _PROFILE])
    assert main(_argv(proj)) == 0
    assert made[0].taps == [], "HIGH 目标不得被探索自动执行"
    out = capsys.readouterr().out
    assert "outcome=" in out
    agent = SQLiteAgentStore(proj["agent_db"])
    trace = agent.get_trace(_task_ids(agent)[0])
    blocked = [e for e in trace if e.guard_result == "BLOCK"]
    assert blocked, "拦截必须留痕（F1：不静默跳过）"
    assert blocked[0].decision["risk"] == "HIGH"


# --- 6. 前置错误面 -------------------------------------------------------------


def test_unreadable_metadata_is_a_preflight_error(proj, capsys):
    """metadata 读不到 → exit 3（不是 traceback）。"""
    proj["gen"].joinpath("source_metadata.json").unlink()
    # 声明面在 graph.db 里已有 → 走不到 metadata；先清掉它，逼出 metadata 路径
    GraphStore(proj["graph_db"]).upsert_graph(RuntimeGraph(
        app_id="com.demo", app_build="local", source_of=SOURCE))
    assert main(_argv(proj)) == 3
    assert capsys.readouterr().out.startswith("PREFLIGHT ERROR:")


def test_device_assembly_failure_is_a_preflight_error(proj, monkeypatch,
                                                      capsys):
    """设备会话建立失败 → exit 3（真机路径的既有契约）。"""
    import session.device_session as ds_mod

    class _Boom:
        def __init__(self, url, caps):
            raise RuntimeError("appium 没起")

    monkeypatch.setattr(ds_mod, "DeviceSession", _Boom)
    monkeypatch.setattr(ds_mod, "resolve_local_caps",
                        lambda udid, bundle_id: {})
    assert main(_argv(proj)) == 3
    assert "设备会话建立失败" in capsys.readouterr().out


def test_explore_without_bundle_id_is_a_preflight_error(proj, capsys):
    """真机/模拟器会话需要 `--bundle-id`（与 `mta run` 同款前置）。"""
    argv = [a for a in _argv(proj)]
    i = argv.index("--bundle-id")
    del argv[i:i + 2]
    assert main(argv) == 3
    assert "--bundle-id" in capsys.readouterr().out


# --- 7. trigger → 动作（F13 纯函数） ------------------------------------------


def test_action_for_parses_the_graph_trigger_vocabulary():
    """`trigger` 的形状是 `<step_type>:<target_id>`（graph/builder 定义）——
    **整串不能当 target**（`frontier` 的缺省 action_for 那样做会解析失败）。"""
    from cli.main import _explore_action_for
    from explorer.policy import ProposedAction
    from graph.models import ScreenTransition

    def t(trigger):
        return ScreenTransition("HomeView", "ProfileView", trigger,
                                source_of=SOURCE)

    assert _explore_action_for(t("tap:HomeView.go_profile")) == \
        ProposedAction(tool="tap", target="HomeView.go_profile")
    assert _explore_action_for(t("input:HomeView.field")) == \
        ProposedAction(tool="input", target="HomeView.field")
    assert _explore_action_for(t("swipe:up")) == \
        ProposedAction(tool="swipe", direction="up")
    assert _explore_action_for(t("back:")) == ProposedAction(tool="back")


def test_action_for_qualifies_a_bare_element_id_with_its_screen():
    """trigger 里只写了元素短名时，必须补成 `Screen.elem` 限定名。

    不补的后果：`Repository.resolve` 的短名规则要求**全局唯一**，跨屏同名
    元素会抛 `AmbiguousReferenceError`——而这条路径上它是**运行期**才炸
    （decider 提议 → toolkit 定位），不是构造期。转移两端都在手里，补限定名
    是零成本的确定性做法。

    ⚠️ 这条测试是 **A/B 探针补出来的**：探针把 `if "." not in target:` 改成
    `if False:`（即永不补限定名）时，21 条测试**全绿**——`action_for` 的这个
    分支此前一行都没被覆盖。
    """
    from cli.main import _explore_action_for
    from explorer.policy import ProposedAction
    from graph.models import ScreenTransition

    t = ScreenTransition("HomeView", "ProfileView", "tap:go_profile",
                         source_of=SOURCE)
    assert _explore_action_for(t) == ProposedAction(
        tool="tap", target="HomeView.go_profile")
    # 已经是限定名 → 原样透传（不重复加前缀）
    t2 = ScreenTransition("HomeView", "ProfileView", "tap:ProfileView.logout_button",
                          source_of=SOURCE)
    assert _explore_action_for(t2) == ProposedAction(
        tool="tap", target="ProfileView.logout_button")


def test_action_for_returns_none_for_non_replayable_triggers():
    """`wait_for` / `assert_*` / 无前缀的 trigger 翻不成动作 → `None`。

    `None` 是 decider 的**跳过**语义（不是结束，见 `explorer/frontier.py`）：
    「再 wait 一次」不驱动 App 走任何新路，翻成动作会让探索空转。
    """
    from cli.main import _explore_action_for
    from graph.models import ScreenTransition

    def t(trigger):
        return ScreenTransition("A", "B", trigger, source_of=SOURCE)

    for trigger in ("wait_for:screen:B", "assert_exists:B.x", "launch_app:",
                    "", "go_profile"):
        assert _explore_action_for(t(trigger)) is None, trigger


# --- 8. 图前提自检 -------------------------------------------------------------


def test_seeded_source_face_really_has_the_unexplored_transition(proj):
    """夹具自检：没有它，下面「探到了新屏」的断言可能因为**frontier 恒空**
    而假绿（FRONTIER_EMPTY 与「探索成功」在输出上长得像）。"""
    from explorer.frontier import select_frontier

    source = GraphStore(proj["graph_db"]).load_graph("com.demo", "local", SOURCE)
    runtime = RuntimeGraph(app_id="com.demo", app_build="local",
                           source_of="runtime")
    items = select_frontier(source, runtime, visited=[], current_screen="HomeView")
    assert [(i.transition.to_screen, i.unexplored) for i in items] == \
        [("ProfileView", True)]


# --- 9. 「把 App 带到前台」（2026-10-11 M4 Gate 实测补的回归） -----------------
#
# 三个缺陷都是在 M4 Gate 的真机跑上现形的，共同形态是**「输出看起来正常」**：
# 探索一步没走却报 FRONTIER_EMPTY、重跑时报出上一轮的发现、`mta run` 的行
# 悄悄丢了 build。三条都补了钉子，避免修完又漂回去。


def test_explore_brings_the_app_to_the_front(proj, monkeypatch, capsys):
    """`cmd_explore` 必须先把 App 带到前台再 Observe（设计 §7.1 的第一步）。

    不做的后果在真机上实测到了：冷模拟器上 `page_source` 是 **springboard**
    （App 根本没起），`current_screen` 恒 `CURRENT_SCREEN_UNKNOWN` → frontier
    恒空 → 输出 `outcome=FRONTIER_EMPTY steps=0`，**与「真的探完了」逐字相同**。
    """
    _made, launched = _install_fake_device(monkeypatch, [_HOME, _PROFILE])
    assert main(_argv(proj)) == 0
    assert launched == ["com.demo"], "探索前必须把 App 激活到前台"


def test_explore_reports_only_its_own_discoveries(proj, monkeypatch, capsys):
    """输出里的 `discoveries=` 与 `discovery:` 行只算**本次任务**。

    整库计数会让同一个 agent.db 上的后一轮探索报出**别人**的发现——M4 Gate
    现场实测：G4/G5 两轮各自打印了 G3 那条 `disc_0731f877…`，而它们各自根本
    没撞见新屏。审计输出必须是「这一轮做了什么」，不是「这个库里有什么」。
    """
    store = SQLiteAgentStore(proj["agent_db"])
    store.create_task("exp_other", agent_type="explorer", goal="上一轮")
    store.save_discovery_event("disc_foreign", kind="NEW_SCREEN",
                               detail={"screen_id": "SomeOtherView"},
                               exploration_task_id="exp_other")

    _made, _launched = _install_fake_device(monkeypatch, [_HOME, _PROFILE])
    assert main(_argv(proj)) == 0
    out = capsys.readouterr().out
    assert "discoveries=1" in out, out
    assert "disc_foreign" not in out, "不得打印别的任务的事件"
    # 库里有两条（别人的 + 本次的），证明是**输出**收窄而不是数据被删
    assert len(store.list_discovery_events()) == 2


def test_a_new_screen_in_the_graph_is_not_a_discovery(proj, monkeypatch):
    """`_known_screens` 认的是**声明面**：图里已有的屏不算发现。

    与上面那条配对——「发现」的判据是「图里没有」，不是「这一轮第一次见」。
    """
    store = GraphStore(proj["graph_db"])
    g = store.load_graph("com.demo", "local", SOURCE)
    store.upsert_graph(RuntimeGraph(
        app_id=g.app_id, app_build=g.app_build, source_of=SOURCE,
        nodes=g.nodes + (ScreenNode("ProfileView", source_of=SOURCE,
                                    visit_count=0,
                                    evidence=("source_declared",)),),
        transitions=g.transitions))
    _made, _launched = _install_fake_device(monkeypatch, [_HOME, _PROFILE])
    assert main(_argv(proj)) == 0
    assert SQLiteAgentStore(proj["agent_db"]).list_discovery_events() == []


def test_the_run_row_is_closed_on_the_success_path(proj, monkeypatch):
    """成功路径也必须给 `runs` 行收口——**这条此前是漏的**。

    不写终态留下的 `status='RUNNING'` 会被下一次 `TraceStore.__init__` 的
    残留清扫扫成 **FAIL**（那是给「进程异常中断」的兜底），于是一次成功的
    探索在库里显示为 FAIL。M4 Gate 现场实测：同库的 explore 行全是
    `status='FAIL' exit_code=None`。

    与 `cmd_generate` 的 review_p3_task35 P3-8 是同一个缺陷——那条修在
    `generate` 上，`explore` 写的时候又漏了一遍。
    """
    import sqlite3

    _made, _launched = _install_fake_device(monkeypatch, [_HOME, _PROFILE])
    assert main(_argv(proj)) == 0

    row = sqlite3.connect(proj["trace_db"]).execute(
        "SELECT status, end_time, exit_code FROM runs ORDER BY rowid DESC"
        " LIMIT 1").fetchone()
    assert row[0] == "PASS", f"成功探索的 run 行必须收口，got {row[0]!r}"
    assert row[1] is not None, "end_time 必须落（RUNNING 残留会被扫成 FAIL）"
    assert row[2] == 0


def test_a_budget_stop_still_closes_the_run_row(proj, monkeypatch, tmp_path):
    """预算耗尽（`STEPS_BUDGET_EXCEEDED`）是**结论**，run 行同样是 PASS。

    F11/矩阵 #11：预算停止不是失败——落 FAIL 会让「预算生效」在报表上
    长得像「探索挂了」。
    """
    import sqlite3

    b = tmp_path / "budget0.yaml"
    b.write_text("max_steps: 0\n", encoding="utf-8")
    _made, _launched = _install_fake_device(monkeypatch, [_HOME])
    assert main(_argv(proj, "--budget", str(b))) == 0
    row = sqlite3.connect(proj["trace_db"]).execute(
        "SELECT status, exit_code FROM runs ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert row == ("PASS", 0)


# --- 10. 对抗性评审补的收口（2026-10-11，两段 review 的第一段） ----------------
#
# 六个洞，共同形态是「**失败路径上的收口不完整**」：异常/前置错误时留下了
# 半成品状态（僵尸任务行、泄漏的会话、多余的文件、对不上的范围），而正常
# 路径看不出来。逐条与实现对齐（探针 P10–P15 各钉一条）。


def test_the_budget_dimension_the_model_cannot_hold_is_a_preflight_error(
        proj, capsys, tmp_path):
    """`max_repeated_state: 0` 必须在**启动期**挡住，不能跑到一半才炸。

    `ExplorationPolicy` 只约束 `ge=0`，而「阈值必须为正」住在
    `explorer.loop_detector.is_loop`——它**在探索跑到第一步之后**才抛。
    不前置的话，这个「坏预算文件」会先建库、建任务行、起设备、拉起 App，
    然后才以「探索失败」收场：正是「坏文件是前置错误、不该留下半张库」
    要防的形态（且 exit 3 的归因也从「前置」漂成「运行失败」）。
    """
    b = tmp_path / "mrs0.yaml"
    b.write_text("max_repeated_state: 0\n", encoding="utf-8")
    assert main(_argv(proj, "--budget", str(b))) == 3
    out = capsys.readouterr().out
    assert out.startswith("PREFLIGHT ERROR:") and "max_repeated_state" in out
    assert not proj["agent_db"].exists(), "前置在写任何东西之前"
    assert not proj["trace_db"].exists(), "连 runs 行都不该建"


def test_a_corrupt_trace_file_is_a_preflight_error_not_a_traceback(proj, capsys,
                                                                   tmp_path):
    """`--db` 指到「不是 sqlite 库」的文件 → exit 3，不是 traceback。

    `_trace_has_runs` 按契约**不吞** `sqlite3.DatabaseError`（那是「库读不了」
    与「还没跑过」的分界），所以探测**本身**也必须在那层 try 里——
    `cmd_generate` 是把两个调用包进同一个 try 的，explore 曾把探测裸奔在外。
    """
    bad = tmp_path / "not_a_db.txt"
    bad.write_text("这不是 sqlite 库\n", encoding="utf-8")
    argv = [a for a in _argv(proj)]
    argv[argv.index("--db") + 1] = str(bad)
    assert main(argv) == 3
    out = capsys.readouterr().out
    assert out.startswith("PREFLIGHT ERROR:") and "Traceback" not in out


def test_a_stale_trace_scope_is_a_preflight_error(proj, capsys):
    """trace 里只有**一个陈旧范围**（build 与本次不同）→ exit 3，不静默混用。

    `read_trace_steps` 的 fail-loud 只在「同一个库里混了多个范围」时响；
    「库里只有一个**别的** build」不会响——此时运行时图的 `transition_key`
    全来自旧范围，`select_frontier` 会把**已经走过的边**当成没走过
    （novelty 误判、重走已知路径），而这在输出上看不出来。
    """
    TraceStore(str(proj["trace_db"])).start_run(
        "run_old", suite="smoke", device_type="simulator",
        app_build="v2", app_bundle_id="com.demo")
    assert main(_argv(proj)) == 3
    out = capsys.readouterr().out
    assert out.startswith("PREFLIGHT ERROR:") and "v2" in out and "local" in out


def test_a_pure_preflight_error_does_not_create_the_agent_db(proj, capsys):
    """前置错误**不得**留下新 agent.db——`SQLiteAgentStore.__init__` 会建库。

    它早先排在 `app_id` 校验之前，于是「拿不到 app_id」这种纯前置错误也会
    在磁盘上留一个带 schema 的库文件。本仓纪律：一次失败的读不该产生新文件
    （`_trace_has_runs` / `_collect_metrics_if_any` 同款）。
    """
    argv = [a for a in _argv(proj)]
    i = argv.index("--bundle-id")
    del argv[i:i + 2]
    assert main(argv) == 3
    assert "拿不到 app_id" in capsys.readouterr().out
    assert not proj["agent_db"].exists(), "前置错误不该建库"


def test_an_exploration_failure_closes_the_task_row(proj, monkeypatch, capsys):
    """`Explorer.run()` 中途抛错 → `agent_tasks` 不能停在 `EXECUTING`。

    `run()` 一开始置 `EXECUTING`、只在**正常**返回时写 `IDLE`。异常路径若
    不收口，审计根上就挂着一个永久的「还在跑」任务（M6 的消费者会读它）。
    """
    # 设备桩照常装（`ds_mod` + `ex_mod` 都打），再把 Executor 换成「观测即炸」
    # 的版本——炸点在 `Explorer._observe` 的第一步，即主循环**已经进入**
    # `EXECUTING` 之后。
    made, _launched = _install_fake_device(monkeypatch, [_HOME])

    import executor.executor as ex_mod

    class _BoomEx(_ScriptedExecutor):
        def page_source(self):
            raise RuntimeError("page_source 炸了")

    def _make(ds):
        ex = _BoomEx(ds, pages=[_HOME])
        made.append(ex)
        return ex

    monkeypatch.setattr(ex_mod, "Executor", _make)
    assert main(_argv(proj)) == 3
    out = capsys.readouterr().out
    assert "探索失败" in out
    store = SQLiteAgentStore(proj["agent_db"])
    task = store.get_task(_task_ids(store)[0])
    assert task.state.value != "EXECUTING", "异常路径必须收口任务行"
    assert task.outcome == "EXPLORATION_FAILED"


def test_app_launch_failure_closes_the_device_session(proj, monkeypatch, capsys):
    """「App 起不来」路径必须 `stack.close()`——否则泄漏 Appium 会话。

    `wire_device_stack` 已经 `connect()` 成功（会话是活的），而 `finally`
    只覆盖它后面的主循环。早返回不显式 close 就是 P2-3 那个泄漏在被抬高一
    层的调用点上重犯。
    """
    import session.device_session as ds_mod

    closed: list[str] = []

    class _FakeDriver:
        def quit(self):
            closed.append("quit")

        def implicitly_wait(self, seconds):
            pass

        def activate_app(self, bundle_id):
            raise RuntimeError("activate_app 失败")

    class _FakeDS:
        def __init__(self, url, caps):
            self.url, self.caps, self.driver = url, caps, None

        def connect(self):
            self.driver = _FakeDriver()
            return self.driver

        def ensure_alive(self):
            return self.driver

    import executor.executor as ex_mod

    monkeypatch.setattr(ds_mod, "DeviceSession", _FakeDS)
    monkeypatch.setattr(ds_mod, "resolve_local_caps",
                        lambda udid, bundle_id: {"udid": udid,
                                                 "bundleId": bundle_id})
    monkeypatch.setattr(ex_mod, "Executor",
                        lambda ds: _ScriptedExecutor(ds, pages=[_HOME]))
    assert main(_argv(proj)) == 3
    assert "App 起不来" in capsys.readouterr().out
    assert closed == ["quit"], "起不来也必须收掉已建立的设备会话"


def test_action_for_refuses_an_empty_from_screen():
    """`from_screen` 为空 → 返回 `None`，不产出 `".elem"`。

    `".elem"` 会被 `Repository.resolve` 拆成 screen="" + elem，而
    `AgentToolkit.call` 对 `ValueError` 子类**故意原样重抛**——它会从
    `Explorer.run()` 冒出去打死整场探索。`ScreenTransition` 自身拒绝空
    `from_screen`（`__post_init__`），所以当前构造器产不出这种值；这条是
    **契约兜底**（纯函数不制造自己都知道解析不了的值），用桩对象喂入。
    """
    from cli.main import _explore_action_for

    class _T:
        trigger = "tap:go_profile"
        from_screen = ""

    assert _explore_action_for(_T()) is None
