#!/usr/bin/env python3
"""Gate M4 / P3-19：Bounded Exploration 端到端验收（Task 4.6）。

**真机半边**：LoginDemo 装在**真模拟器**上、经**真 Appium 会话**跑一轮
`mta explore`——observe→frontier→guard→execute→verify 全程走生产链路
（`wire_device_stack` + `AgentToolkit`），不注入任何测试替身。

**离线半边**：F7 启动期拒绝，在 `out/p3_m4_gate/gate_env/` 的自足工程里跑
（自己的 git、自己的库），**在任何设备操作之前**。

## 起点：为什么探索前要先把 App 开到 HomeView

设计 §7.1 的第一步是 **Observe**，而 Observe 的前提是「App 在屏上、且那个屏
在 Repository 里登记过」。`mta explore` 现在自己会做 `activate_app`（见
`cli/main.py::cmd_explore` 的 5b），但**只 activate 不够**：LoginDemo 冷启动
落在 **LoginView**，而本 Gate 的声明面（以及大多数真实工程）登记的是登录后的
那几屏——`current_screen` 在 LoginView 上返回 `CURRENT_SCREEN_UNKNOWN`，
frontier 恒空，输出 `outcome=FRONTIER_EMPTY steps=0`，**与「真的探完了」逐字
相同**（2026-10-11 实测踩到）。

所以 Gate 用**生产路径**把 App 开到起点：跑一条 gate 自己的用例
（`gate_login`：terminate → launch → 填表 → 登录 → `wait_for screen:HomeView`）
经 `mta run`。这不是替身——它就是 `mta run`，只不过用例与库都在 Gate 工程里。
副产品是 `trace.db` 有了真实的观测面，`mta explore` 的 runtime 图因此非空。

## 判据（plan Task 4.6 的 Gate + 矩阵 #11–#13 的现场复核）

  G1  矩阵 #1/#9：`--env-kind production` → 启动即拒；**staging 也拒**
      （口径必守 review_p3_task12 §4）；`--device-type real_device` → 拒；
      三者都在**写任何库之前**（连 agent.db 文件都不该建）
  G2  矩阵 #11：`--budget` 四维生效 → `*_BUDGET_EXCEEDED` **正常退出**（exit 0）
  G3  主路径（**真设备**）：`mta explore` 撞见图里没有的屏 → `discovery_events`
      PENDING；**图不变**（E12：登记只在人工 ACCEPT 之后）
  G4  矩阵 #13 前半：HIGH 目标被探索侧闸拦下 → 审计留痕、**不执行**
  G5  矩阵 #12：`max_repeated_state` 触发 → `LOOP_DETECTED` 正常停止
  G6  **现场卫生**（矩阵 #17 的 M4 版）：全程结束后 gate 工程 `git status`
      干净 + 主仓库 `overrides/` / `generated/` / `suites/` 零修改
  G7  全量回归：`pytest tests` 全绿

产出 `out/p3_m4_gate/summary.json`；exit 0 = M4 Gate 通过。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# `read_app_identity`（12.5）**不**自设 DEVELOPER_DIR——子进程要能看见 simctl。
# Gate 全程（含它自己发起的子进程）都需要它，故在这里显式落一次。
os.environ.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")

OUT_DIR = ROOT / "out" / "p3_m4_gate"
PY = str(ROOT / ".venv/bin/python")
UDID = os.environ.get("MTA_SIM_UDID", "AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E")
BUNDLE = os.environ.get("MTA_SIM_BUNDLE", "com.phaset0.logindemo")
APP = ROOT / "build/p1/Build/Products/Debug-iphonesimulator/LoginDemo.app"

# Gate 工程自己的用例：把 App 从冷启动开到 HomeView（见模块 docstring）。
# `terminate_app` 是第一步——不 terminate 的话 `launch_app` 只是 activate，
# 上一轮 Gate 留下的 ProfileView 会被当起点。
GATE_CASE = "gate_login"
_GATE_SUITE_YAML = """schema_version: "0.2"
id: gate_login
name: gate 起点：登录到 HomeView
suite: gate
tags: [gate]
steps:
  - action: terminate_app
  - action: launch_app
  - action: input
    target: LoginView.username_field
    value: ${TEST_USERNAME}
  - action: input
    target: LoginView.password_field
    value: ${TEST_PASSWORD}
    sensitive: true
  - action: tap
    target: LoginView.login_button
    idempotency: IDEMPOTENT
  - wait_for:
      target: screen:HomeView
      condition: active
      timeout: 15
"""

results: dict[str, dict] = {}
_T0 = time.time()


def check(name: str, ok: bool, detail: str) -> bool:
    results[name] = {"pass": bool(ok), "detail": detail}
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
    return bool(ok)


def _env(**extra) -> dict:
    e = dict(os.environ, PYTHONPATH=str(ROOT),
             DEVELOPER_DIR="/Applications/Xcode.app/Contents/Developer",
             TEST_USERNAME="qa_agent", TEST_PASSWORD="pw123",
             CODEBUDDY_BROKERED_FS_HOOK_ENABLED="0",
             CODEBUDDY_SAFE_DELETE_SANDBOX="0")
    e["no_proxy"] = e["NO_PROXY"] = "127.0.0.1,localhost,::1"
    e.update(extra)
    return e


def sh(*args: str, cwd: Path = ROOT, env: dict | None = None,
       timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True,
                          env=env or _env(), cwd=str(cwd), timeout=timeout)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = _env(GIT_AUTHOR_NAME="gate_m4", GIT_AUTHOR_EMAIL="m4@gate",
               GIT_COMMITTER_NAME="gate_m4", GIT_COMMITTER_EMAIL="m4@gate")
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, env=env, timeout=60)


def _probe(url: str, timeout: float) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def preflight() -> bool:
    """设备半边的前置：模拟器 booted + Appium 在跑 + App 已构建。"""
    ok = True
    r = sh("xcrun", "simctl", "list", "devices", "booted")
    ok &= check("PF_simulator_booted", UDID in r.stdout and "Booted" in r.stdout,
                f"UDID={UDID}")
    ok &= check("PF_appium_4723", _probe("http://127.0.0.1:4723/status", 4),
                "Appium status 200")
    ok &= check("PF_app_built", APP.is_dir(), str(APP))
    return ok


def install_app() -> bool:
    r = sh("xcrun", "simctl", "install", UDID, str(APP))
    return check("PF_app_installed", r.returncode == 0, r.stderr.strip()[:160])


# --- 自足工程 -----------------------------------------------------------------

def _element_yaml(elem: str, screen: str, *, risk: str = "LOW") -> str:
    return (f"schema_version: '1.0'\nkind: element\nid: {elem}\n"
            f"screen: {screen}\ntype: button\nstrategies:\n"
            f"- type: accessibility_id\n  value: {elem}\n  origin: source\n"
            f"  source_file: LoginDemoApp.swift\n  source_line: 1\n"
            f"metadata:\n  origin: source\n  risk: {risk}\n")


def _screen_yaml(screen: str) -> str:
    return (f"schema_version: '1.0'\nkind: screen\nid: {screen}\n"
            f"marker: screen.{screen}\nkind_hint: page\n"
            f"metadata:\n  risk: LOW\n  origin: source\n")


# Gate 的声明面：**LoginDemo 真实存在的那几个元素/屏**（accessibility id 逐个
# 从 `ios_demo/LoginDemo/LoginDemoApp.swift` 抄来）。编造的 id 在真 App 上找
# 不到，`tap` 会以 ELEMENT_NOT_FOUND 收场，「探索撞见新屏」这条判据就永远
# 不成立——Gate 也就失去了意义。
_ELEMENTS = {
    "LoginView": [("username_field", "LOW"), ("password_field", "LOW"),
                  ("login_button", "LOW")],
    "HomeView": [("go_profile", "LOW"), ("go_search", "LOW"),
                 ("home_page", "LOW"), ("pay_button", "HIGH")],
    "ProfileView": [("logout_button", "LOW")],
}
_SCREENS = ("LoginView", "HomeView", "ProfileView")
# 声明面（source 图的节点）只登记 **HomeView**：ProfileView 因此是「图里没有
# 的屏」，正是 G3 要撞见的那个（设计 §7.5 的第一段）。
_DECLARED_SCREENS = ["HomeView"]


def _scaffold(tmp: Path) -> dict:
    """自足工程：自己的 git + generated + suites + 库。

    metadata 的 `build` / `git_commit` **从装好的 App 读**（12.5）：写死会让
    `mta run` 在 Build Identity 闸上 fail-closed（exit 3），Gate 就一步都跑不动。
    """
    from source import build_identity as bi

    proj = tmp / "proj"
    if proj.exists():
        shutil.rmtree(proj)
    gen = proj / "repository" / "generated" / "local"
    (gen / "elements").mkdir(parents=True)
    (gen / "screens").mkdir(parents=True)
    for screen, elems in _ELEMENTS.items():
        (gen / "elements" / f"{screen}.yaml").write_text(
            "---\n".join(_element_yaml(e, screen, risk=r)
                         for e, r in elems), encoding="utf-8")
    for screen in _SCREENS:
        (gen / "screens" / f"{screen}.yaml").write_text(
            _screen_yaml(screen), encoding="utf-8")
    # overrides 空目录：Gate 必须**自足**，不能把主仓库的 overrides 混进来
    # （混进来会让 resolve 的结果取决于主仓库当下长什么样）。
    (proj / "repository" / "overrides").mkdir(parents=True)
    su = proj / "suites" / "gate"
    su.mkdir(parents=True)
    (su / f"{GATE_CASE}.yaml").write_text(_GATE_SUITE_YAML, encoding="utf-8")

    # .gitignore **在 baseline 之前**写好：G6a 的「干净」才是真判据，而不是
    # 「跑完之后补一条 ignore 再宣称干净」。
    (proj / ".gitignore").write_text("state/\nrepository/generated/\n",
                                     encoding="utf-8")
    _git(proj, "init", "-q")
    top = Path(_git(proj, "rev-parse", "--show-toplevel").stdout.strip())
    if top.resolve() != proj.resolve():
        raise RuntimeError(f"gate proj 仓库根漂移：{top} != {proj}")
    _git(proj, "config", "user.email", "m4@gate")
    _git(proj, "config", "user.name", "gate_m4")
    (proj / "README.md").write_text("gate m4\n", encoding="utf-8")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "baseline")
    head = _git(proj, "rev-parse", "HEAD").stdout.strip()

    # ⚠️ **身份读不到也要能把工程建起来**：`_scaffold` 在设备半边**之前**跑，
    #    而 `read_app_identity` 要 simctl 找到那个 booted 设备。没有设备时直接
    #    抛异常会让**离线半边（G1 的三种 F7 拒绝）与 G7 全量回归都不跑**——
    #    而它们恰恰是任何机器上都该跑的那部分（2026-10-11 实测：无设备时
    #    Gate 以一个 traceback 收场，一条判据都没报）。所以这里降级：身份读不到
    #    就写一份占位 metadata，设备半边随后按 `pre_ok` 跳过（`SKIPPED_ENV`）。
    try:
        app = bi.read_app_identity(UDID, BUNDLE)
        identity = {"build": app.build, "git_commit": app.git_commit}
    except Exception as e:  # noqa: BLE001 — 无设备 = 离线半边照跑
        print(f"note: 读不到 App 身份（{type(e).__name__}: {e}）"
              f"→ 用占位 metadata，设备半边将跳过")
        identity = {"build": "local", "git_commit": "unavailable"}
    (gen / "source_metadata.json").write_text(json.dumps(
        {"app_version": "debug", "build": identity["build"],
         "git_commit": identity["git_commit"],
         "screens": _DECLARED_SCREENS,
         "screen_elements": [{"name": "HomeView", "elements": [
             {"id": "go_profile", "accessibility_id": "go_profile",
              "type": "button", "resolution_type": "literal",
              "source": {"file": "LoginDemoApp.swift", "line": 146}}]}]},
        ensure_ascii=False, indent=2), encoding="utf-8")

    out = proj / "state"
    out.mkdir()
    return {"proj": proj, "gen": gen, "head": head, "suites": proj / "suites",
            # build 取自**装好的 App**（读不到时是占位 "local"）——声明面与
            # 观测面都必须用它，不能再硬编码（见 `_seed_source_face` 注）。
            "build": identity["build"],
            "agent_db": out / "agent.db", "trace_db": out / "trace.db",
            "graph_db": out / "graph.db", "exp_db": out / "experience.db"}


def _seed_source_face(g: dict, *transitions: tuple) -> None:
    """播种**声明面**（`graph.db` 的 source 图）。

    metadata 没有导航声明（`graph/builder.py` 的实测结论），所以「有出边可探」
    这件事只能来自**人工确认过的发现**——Gate 直接经 `GraphStore` 播一条，
    与「人工 ACCEPT 之后登记」的库形态**逐列一致**（`visit_count=0` +
    `count=0` + `evidence=discovery`，与 `candidates/discovery.register`
    写出的节点/转移同款——证据词必须是 `EVIDENCE_DISCOVERY`，不是
    `source_declared`：前者是「人工确认的发现」，后者是「源码声明」，
    `graph/models.py` 特意把两者分开记）。
    """
    from graph import SOURCE, GraphStore, RuntimeGraph, ScreenNode, ScreenTransition
    from graph.models import EVIDENCE_DISCOVERY

    nodes = tuple(ScreenNode(s, source_of=SOURCE, visit_count=0,
                             evidence=(EVIDENCE_DISCOVERY,))
                  for s in _DECLARED_SCREENS)
    store = GraphStore(g["graph_db"])
    # 范围必须与 `cmd_explore` 用的一致（`_scaffold` 从装好的 App 读出 build）
    # ——错位会让 `load_graph` 查不到那一面 → frontier 恒空 → G3b 假红。
    store.upsert_graph(RuntimeGraph(
        app_id=BUNDLE, app_build=g["build"], source_of=SOURCE, nodes=nodes,
        transitions=tuple(ScreenTransition(a, b, tr, source_of=SOURCE, count=0)
                          for a, b, tr in transitions)))


def mta_run(g: dict) -> subprocess.CompletedProcess:
    """跑 gate 自己的用例把 App 开到 HomeView（生产路径，见模块 docstring）。"""
    return sh(PY, "-m", "cli.main", "run", "--case", GATE_CASE,
              "--suites-root", str(g["suites"]),
              "--generated", str(g["gen"]),
              "--overrides", str(g["proj"] / "repository/overrides"),
              "--metadata", str(g["gen"] / "source_metadata.json"),
              "--db", str(g["trace_db"]), "--exp-db", str(g["exp_db"]),
              "--udid", UDID, "--bundle-id", BUNDLE, timeout=600)


def _device_home(g: dict, label: str) -> bool:
    """把设备重置到 HomeView（每个设备判据的统一起点）。"""
    r = mta_run(g)
    return check(f"SETUP_{label}_app_at_home", r.returncode == 0,
                 f"exit={r.returncode} "
                 f"{(r.stdout.strip().splitlines() or ['<empty>'])[-1][:80]}")


def mta_explore(g: dict, *extra: str, env: dict | None = None,
                timeout: int = 600) -> subprocess.CompletedProcess:
    return sh(PY, "-m", "cli.main", "explore",
              "--goal", "探索某页面未覆盖状态",
              "--generated", str(g["gen"]),
              "--overrides", str(g["proj"] / "repository/overrides"),
              "--suites-root", str(g["suites"]),
              "--db", str(g["trace_db"]),
              "--graph-db", str(g["graph_db"]),
              "--agent-db", str(g["agent_db"]),
              "--bundle-id", BUNDLE, "--udid", UDID,
              *extra, env=env, timeout=timeout)


def _events(g: dict) -> list[dict]:
    from agents.storage import SQLiteAgentStore

    if not g["agent_db"].exists():
        return []
    return SQLiteAgentStore(g["agent_db"]).list_discovery_events()


def _tasks(g: dict) -> list[str]:
    from agents.storage import SQLiteAgentStore

    if not g["agent_db"].exists():
        return []
    store = SQLiteAgentStore(g["agent_db"])
    conn = store._connect()
    try:
        return [r["task_id"] for r in
                conn.execute("SELECT task_id FROM agent_tasks").fetchall()]
    finally:
        conn.close()


def _live_screen(g: dict) -> str:
    """当前屏（Gate 自己的只读观测，用来证明「动作没被发出去」）。

    与 `mta explore` 的观测面**同源**：同一个 `current_screen` 纯函数 + 同一份
    Gate 生成的 Repository。这里刻意不复用 `mta explore` 的输出——用被测命令
    自己的报告去证明被测命令没做某事，是自证。
    """
    from cli.pipeline import SessionPipeline, wire_device_stack
    from environment.secrets import EnvSecretProvider
    from source.screen import current_screen
    from repository.resolver import Repository

    repo = Repository.from_dirs(generated_root=str(g["gen"]),
                                overrides_root=None)
    pipeline = SessionPipeline(suites_root=None, secrets=EnvSecretProvider(),
                               app_id=BUNDLE, app_build=g["build"])
    stack = wire_device_stack(pipeline, env_kind="sandbox", run_id="gate_probe",
                              udid=UDID, bundle_id=BUNDLE, repo=repo)
    try:
        return current_screen(stack.runner.ex.page_source(), repo).screen or "?"
    finally:
        stack.close()


# --- 判据 ---------------------------------------------------------------------

def g1_startup_refusals(g: dict) -> bool:
    """矩阵 #1/#9 + 口径必守：production / staging / 真机三种启动期拒绝。

    **在任何设备操作之前**跑——三者都该在装配设备会话之前就退出，所以
    「连 agent.db 都不该建」是这里最强的那条判据。
    """
    ok = True
    for label, extra, needle in (
            ("production", ["--env-kind", "production"], "F7"),
            ("staging", ["--env-kind", "staging"], "F7"),
            ("real_device", ["--device-type", "real_device"], "F7")):
        fresh = g["proj"] / f"state/refused_{label}.db"
        r = mta_explore(g, *extra, "--agent-db", str(fresh), timeout=120)
        wrote = fresh.exists()
        ok &= check(f"G1_{label}_refused",
                    r.returncode != 0 and needle in (r.stdout + r.stderr)
                    and not wrote,
                    f"exit={r.returncode} wrote_db={wrote} "
                    f"{(r.stdout + r.stderr).strip().splitlines()[:1]}")
    return ok


def g2_budget_stops_cleanly(g: dict) -> bool:
    """矩阵 #11：预算耗尽 → `*_BUDGET_EXCEEDED` **正常退出**（exit 0）。"""
    ok = _device_home(g, "G2")
    b = g["proj"] / "state/budget_zero.yaml"
    b.write_text("max_steps: 0\n", encoding="utf-8")
    r = mta_explore(g, "--budget", str(b), timeout=300)
    ok &= check("G2_budget_stops_cleanly",
                r.returncode == 0 and "STEPS_BUDGET_EXCEEDED" in r.stdout,
                f"exit={r.returncode} "
                f"{(r.stdout.strip().splitlines() or ['<empty>'])[-1][:100]}")
    # 坏预算文件 → 前置错误（不是 traceback、不是静默回落），且**在设备之前**
    bad = g["proj"] / "state/budget_bad.yaml"
    bad.write_text("max_steps: true\n", encoding="utf-8")
    fresh = g["proj"] / "state/bad_budget.db"
    r2 = mta_explore(g, "--budget", str(bad), "--agent-db", str(fresh),
                     timeout=120)
    ok &= check("G2_bad_budget_is_preflight",
                r2.returncode == 3 and "PREFLIGHT ERROR" in r2.stdout
                and not fresh.exists(),
                f"exit={r2.returncode} wrote_db={fresh.exists()}")
    return ok


def g3_real_exploration(g: dict) -> bool:
    """主路径（**真设备 + 真 Appium**）：撞见图里没有的屏 → PENDING 事件，图不变。"""
    from graph import SOURCE, GraphStore

    ok = _device_home(g, "G3")
    _seed_source_face(g, ("HomeView", "ProfileView", "tap:HomeView.go_profile"))
    before = GraphStore(g["graph_db"]).load_graph(BUNDLE, "local", SOURCE)
    r = mta_explore(g)
    print(f"  G3 stdout:\n{_indent(r.stdout)}")
    if r.returncode != 0:
        print(f"  G3 stderr:\n{_indent(r.stderr[-800:])}")
    ok &= check("G3a_explore_runs", r.returncode == 0, f"exit={r.returncode}")

    events = _events(g)
    kinds = [e["kind"] for e in events]
    screens = [(e.get("detail") or {}).get("screen_id") for e in events]
    pv = [e for e in events
          if (e.get("detail") or {}).get("screen_id") == "ProfileView"]
    ok &= check("G3b_discovery_recorded",
                "NEW_SCREEN" in kinds and "ProfileView" in screens
                and bool(pv)
                and all(e["review_status"] == "PENDING" for e in pv),
                f"events={list(zip(kinds, screens))} "
                f"review={[e['review_status'] for e in events]}")

    after = GraphStore(g["graph_db"]).load_graph(BUNDLE, "local", SOURCE)
    ok &= check("G3c_graph_untouched",
                after.nodes == before.nodes and after.transitions == before.transitions
                and "ProfileView" not in after.screens,
                f"screens={list(after.screens)} transitions={len(after.transitions)}")
    return ok


def g4_guard_block_audited(g: dict) -> bool:
    """矩阵 #13 前半：HIGH 目标被拦 → 审计留痕、**不执行**。

    ⚠️ **必须真跑到设备**：拦下的步**不进 toolkit**，所以「执行了没」的判据在
    trace 的 `guard_result` 两侧——Gate 再独立观测一次当前屏，确认 App 没动。

    `pay_button` 是 Gate 在自己的 generated 里造的 HIGH 元素（LoginDemo 里
    没有 HIGH 元素；`HomeView.pay_button` 是仓库里风险标注习惯的沿用）。
    声明面里**只**放这一条转移，于是「没执行」只可能是被闸拦下的。
    预算压到 3 步：被拦的步会继续循环（拦截是「这步没做成」不是「探索失败」），
    不压预算就会一路空转到 max_steps。
    """
    from agents.storage import SQLiteAgentStore

    ok = _device_home(g, "G4")
    _seed_source_face(g, ("HomeView", "ProfileView", "tap:HomeView.pay_button"))
    b = g["proj"] / "state/budget_g4.yaml"
    b.write_text("max_steps: 3\nmax_duration_seconds: 120\n"
                 "max_llm_calls: 20\nmax_repeated_state: 9\n", encoding="utf-8")
    r = mta_explore(g, "--budget", str(b), timeout=420)
    print(f"  G4 stdout:\n{_indent(r.stdout)}")
    if r.returncode != 0:
        print(f"  G4 stderr:\n{_indent(r.stderr[-800:])}")

    store = SQLiteAgentStore(g["agent_db"])
    blocked, dispatched = [], []
    for tid in _tasks(g):
        for e in store.get_trace(tid):
            if e.decision.get("target") == "HomeView.pay_button":
                (blocked if e.guard_result == "BLOCK" else dispatched).append(e)
    risks = {e.decision.get("risk") for e in blocked}
    on_screen = _live_screen(g)
    return check("G4_guard_block_audited",
                 r.returncode == 0 and blocked and not dispatched
                 and "HIGH" in risks and on_screen == "HomeView",
                 f"exit={r.returncode} blocked={len(blocked)} "
                 f"dispatched={len(dispatched)} risks={sorted(risks)} "
                 f"screen={on_screen}")


def g5_loop_detected(g: dict) -> bool:
    """矩阵 #12：重复状态到阈值 → `LOOP_DETECTED` 正常停止。

    构造：声明面播一条 `HomeView --tap:HomeView.home_page--> HomeView`。
    `home_page` 是「首页」那个**静态文本**（LOW、幂等空操作），点它之后
    `screen_fingerprint` 逐字节不变（2026-10-11 实测）——于是到达序列里同一个
    指纹重复出现，`max_repeated_state: 2` 时第二步就该停。
    """
    ok = _device_home(g, "G5")
    _seed_source_face(g, ("HomeView", "HomeView", "tap:HomeView.home_page"))
    b = g["proj"] / "state/budget_loop.yaml"
    b.write_text("max_steps: 20\nmax_duration_seconds: 300\n"
                 "max_llm_calls: 20\nmax_repeated_state: 2\n", encoding="utf-8")
    r = mta_explore(g, "--budget", str(b), timeout=420)
    print(f"  G5 stdout:\n{_indent(r.stdout)}")
    if r.returncode != 0:
        print(f"  G5 stderr:\n{_indent(r.stderr[-800:])}")
    ok &= check("G5_loop_detected",
                r.returncode == 0 and "LOOP_DETECTED" in r.stdout,
                f"exit={r.returncode} "
                f"{(r.stdout.strip().splitlines() or ['<empty>'])[-1][:110]}")
    return ok


def g6_site_hygiene(g: dict) -> bool:
    """矩阵 #17 的 M4 版：全程结束后现场干净、正式资产零修改。

    判据分两半：① Gate 自足工程的 `git status` 干净（探索不写工程文件；
    `.gitignore` 在 baseline 里就写好了，见 `_scaffold`）；② **主仓库**的
    `overrides/` / `generated/` / `suites/` 与 Gate 开始时逐字节相同
    （md5 快照比对）——探索的副作用只许落在 `out/` 下的库里。
    """
    st = _git(g["proj"], "status", "--porcelain").stdout.strip()
    ok = check("G6a_gate_proj_clean", st == "",
               f"dirty={st.splitlines()[:4]}")
    ok &= check("G6b_repo_assets_untouched",
                _repo_assets_md5() == g["assets_md5"],
                "overrides/ generated/ suites/ 逐字节未变")
    return ok


def _repo_assets_md5() -> dict:
    """主仓库正式资产的 md5 快照（`overrides/` / `generated/` / `suites/`）。"""
    import hashlib

    out: dict[str, str] = {}
    for sub in ("repository/overrides", "repository/generated", "suites"):
        base = ROOT / sub
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file():
                out[str(p.relative_to(ROOT))] = hashlib.md5(
                    p.read_bytes()).hexdigest()
    return out


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.strip().splitlines())


def g7_full_pytest() -> bool:
    proc = sh(PY, "-m", "pytest", "tests", "-p", "no:cacheprovider",
              "--basetemp", str(OUT_DIR / "_pytest_tmp"), timeout=1800)
    tail = (proc.stdout.strip().splitlines() or ["<no stdout>"])[-1]
    return check("G7_full_pytest", proc.returncode == 0, tail[:120])


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_DIR / "gate_env"
    tmp.mkdir(parents=True, exist_ok=True)

    pre_ok = preflight()
    if pre_ok:
        install_app()

    g = _scaffold(tmp)
    g["assets_md5"] = _repo_assets_md5()
    print(f"gate proj: {g['proj']}  head={g['head'][:8]}")

    # 离线半边（**任何设备操作之前**）：F7 三种拒绝
    ok = g1_startup_refusals(g)

    # 设备半边。⚠️ 跳过时**不算通过**（`ok` 保持 False）——没有设备的
    # `exit 0` 会是一句「M4 Gate 通过」的假话。但跳过**不阻断**离线半边与 G7。
    device_half = "SKIPPED_ENV"
    if pre_ok:
        device_half = "PASS"
        try:
            ok &= g2_budget_stops_cleanly(g)
            ok &= g3_real_exploration(g)
            ok &= g4_guard_block_audited(g)
            ok &= g5_loop_detected(g)
        except Exception as e:  # noqa: BLE001 — 设备半边炸了要如实报
            device_half = "FAIL"
            ok = check("device_half", False, f"{type(e).__name__}: {e}") and ok
    else:
        check("device_half", False, "preflight 未过（无 booted 模拟器 / "
                                    "Appium 未起 / App 未构建）→ 设备半边跳过")
        ok = False

    ok &= g6_site_hygiene(g)
    ok &= g7_full_pytest()

    code = 0 if ok else 1
    summary = {"verdict": "PASS" if code == 0 else "FAIL", "gate": "M4",
               "udid": UDID, "bundle": BUNDLE, "device_half": device_half,
               "elapsed_s": round(time.time() - _T0, 1), "results": results}
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"summary: {OUT_DIR / 'summary.json'}")
    print(f"verify_p3_m4: exit {code} (device_half={device_half})")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
