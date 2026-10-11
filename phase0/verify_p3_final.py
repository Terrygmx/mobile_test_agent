#!/usr/bin/env python3
"""Gate M6-final / P3-31：端到端自主回归闭环演示（设计 §14 P3-E Gate；Task 6.6）。

**闭环链**（设计 §14 原文）：新 Build 端到端跑通
Diff→Plan→补测→执行→探索→诊断→Bug Candidate→人工确认，
**全程无资产被自动修改**（矩阵 #17）+ Subagent 统一工具层（矩阵 #18）。

## 四个部分（前两份在任何设备操作之前）

- **OFF（离线）**：
  G1  F7：`agent benchmark run --env-kind production` **启动即拒**，且不写库
  G2  Benchmark 闭环（设计 §13.2）：`agent benchmark run` v1 → v2 →
      `compare` 无回归；写一份劣化快照 → `compare` → **AGENT_REGRESSION
      exit 1**（供 CI）
  G3  **矩阵 #18（manager 级，in-process）**：三 Subagent 经 TestManager 各自
      产出、预算独立互不挤占、trace 按各自 task_id 归因、连续 Guard 拦截 →
      ESCALATED 且接手包完整
- **CHAIN（链条，gate 自足工程 A，M3 Gate 同框架，离设备）**：
  G4  `mta plan`（git diff → 可解释 Plan，`--json` 取 plan_id）
  G5  `mta generate`（本地 LLM 桩）→ 候选 PENDING + dry_run PASS
  G6  **人工** accept → 落 gate 工程 suites/（唯一被允许的资产写入，行为人
      是人——F2/F6 的「AI 提议 → 人工 Accept → 进正式资产」）
  G7  `mta run`（含新用例，`--fake-driver`）→ PASS（管线把新用例跑起来）
- **DEV（设备半边，gate 自足工程 B + 真模拟器）**：
  G8  `mta explore`（sandbox）→ run 行 PASS、`agent_trace` 归因 explorer
      任务、发现事件 **PENDING 且图未变**（E12/矩阵 #11 同款——自主探索
      不自动改图）
  G9  **漂移法注入真实 bug**（把 `username_field` 改名 `user_field`、重编
      重装、**不重扫**——M5 Gate 实测的口径：漂移的形态必须是「元数据旧、
      App 新」，lint 会拒掉元数据里不存在的 target）→ `mta run`（真）失败 →
      `mta diagnose` → Bug Candidate（复现 3 次 + 多用例佐证 → 11 分过阈值
      → AUTOMATION_DEFECT）→ 人工 accept → CONFIRMED + Evidence Bundle
- **G10 矩阵 #17：正式资产零程序修改**——主仓库 overrides/generated/suites
  逐字节未变；gate 工程 A 的 git 只有**人工 accept** 落的那份 suites 文件；
  gate 工程 B 干净；App 源码已还原（漂移改名撤回 + 重编译重装）
- **G11 全量回归**：`pytest tests` 全绿

产出 `out/p3_final_gate/summary.json` + `docs/p3_demo_record.md` 的素材；
exit 0 = M6 Gate 通过。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")

OUT_DIR = ROOT / "out" / "p3_final_gate"
PY = str(ROOT / ".venv/bin/python")
UDID = os.environ.get("MTA_SIM_UDID", "AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E")
BUNDLE = os.environ.get("MTA_SIM_BUNDLE", "com.phaset0.logindemo")
APP = ROOT / "build/p1/Build/Products/Debug-iphonesimulator/LoginDemo.app"
APP_SOURCE = ROOT / "ios_demo/LoginDemo/LoginDemoApp.swift"

# --- 工程A（链条）：M3 Gate 同款的自足工程常量 ---------------------------------
APP_FILE = "Demo.swift"
SEED_STEPS = [("launch_app", None), ("wait_for", "screen:HomeView"),
              ("tap", "HomeView.go_search"), ("wait_for", "screen:SearchView")]
CANDIDATE_STEPS = json.dumps({
    "steps": [{"action": "tap", "target": "HomeView.go_search"},
              {"wait_for": {"target": "screen:SearchView",
                            "condition": "active", "timeout": 10}}]})

# --- 工程B（设备）：漂移注入 ---------------------------------------------------
_DRIFT_ANCHOR = '                    .accessibilityIdentifier("username_field")'
_DRIFT_RENAME = '                    .accessibilityIdentifier("user_field")'
_DECLARED_SCREENS = ("LoginView", "HomeView", "ProfileView")

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
    e.pop("LLM_API_KEY", None)
    e.update(extra)
    return e


def sh(*args: str, cwd: Path = ROOT, env: dict | None = None,
       timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True,
                          env=env or _env(), cwd=str(cwd), timeout=timeout)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = _env(GIT_AUTHOR_NAME="gate_final", GIT_AUTHOR_EMAIL="final@gate",
               GIT_COMMITTER_NAME="gate_final",
               GIT_COMMITTER_EMAIL="final@gate")
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, env=env, timeout=60)


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.strip().splitlines())


def _probe(url: str, timeout: float) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def preflight() -> bool:
    ok = True
    r = sh("xcrun", "simctl", "list", "devices", "booted")
    ok &= check("PF_simulator_booted", UDID in r.stdout and "Booted" in r.stdout,
                f"UDID={UDID}")
    ok &= check("PF_appium_4723", _probe("http://127.0.0.1:4723/status", 4),
                "Appium status 200")
    ok &= check("PF_app_built", APP.is_dir(), str(APP))
    return ok


def make(target: str) -> bool:
    r = sh("make", target)
    if r.returncode != 0:
        print(f"make {target} failed:\n{r.stdout[-800:]}\n{r.stderr[-400:]}")
    return r.returncode == 0


# --- LLM 桩（M3 Gate 同款） -----------------------------------------------------

class _StubLLM(BaseHTTPRequestHandler):
    CONTENT = "{}"

    def do_POST(self):                       # noqa: N802 — http.server 的约定
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.dumps({"choices": [
            {"message": {"content": type(self).CONTENT}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):               # 静音
        pass


def _serve_llm(content: str) -> tuple[str, HTTPServer]:
    cls = type("_Stub", (_StubLLM,), {"CONTENT": content})
    srv = HTTPServer(("127.0.0.1", 0), cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}/v1/chat/completions", srv


# --- 工程A（链条） --------------------------------------------------------------

def _element_yaml(elem: str, screen: str) -> str:
    return (f"schema_version: '1.0'\nkind: element\nid: {elem}\n"
            f"screen: {screen}\ntype: button\nstrategies:\n"
            f"- type: accessibility_id\n  value: {elem}\n  origin: source\n"
            f"  source_file: {APP_FILE}\n  source_line: 1\n"
            f"metadata:\n  origin: source\n  risk: LOW\n")


def _screen_yaml(screen: str) -> str:
    return (f"schema_version: '1.0'\nkind: screen\nid: {screen}\n"
            f"marker: screen.{screen}\nkind_hint: page\n"
            f"metadata:\n  risk: LOW\n  origin: source\n")


def _metadata_a(git_commit: str) -> dict:
    return {"app_version": "debug", "build": "local", "git_commit": git_commit,
            "parser_version": "0.2.0",
            "screens": ["HomeView", "SearchView"],
            "screen_elements": [
                {"name": "HomeView", "elements": [
                    {"id": "go_search", "accessibility_id": "go_search",
                     "type": "button", "resolution_type": "literal",
                     "source": {"file": APP_FILE, "line": 1}}]},
                {"name": "SearchView", "elements": []}]}


def _scaffold_a(tmp: Path) -> dict:
    """工程A：plan/generate/accept/run--fake-driver 的自足工程（M3 Gate 同款）。"""
    proj = tmp / "proj_a"
    gen = proj / "repository" / "generated" / "local"
    (gen / "elements").mkdir(parents=True)
    (gen / "screens").mkdir(parents=True)
    (gen / "elements" / "HomeView.yaml").write_text(
        _element_yaml("go_search", "HomeView"), encoding="utf-8")
    for screen in ("HomeView", "SearchView"):
        (gen / "screens" / f"{screen}.yaml").write_text(
            _screen_yaml(screen), encoding="utf-8")
    (gen / "source_metadata.json").write_text(
        json.dumps(_metadata_a("PLACEHOLDER"), ensure_ascii=False, indent=2),
        encoding="utf-8")
    # 与工程B 同款 .gitignore：`state/`（gate 自己的库）与 `repository/generated/`
    # 都不是「正式资产」。不写它，G10b 的 git status 会把这两样算成程序改动了
    # 资产——断言要么被迫放宽（等于没有），要么假红。
    (proj / ".gitignore").write_text("state/\nrepository/generated/\n",
                                     encoding="utf-8")
    suites = proj / "suites" / "smoke"
    suites.mkdir(parents=True)
    (suites / "home_001.yaml").write_text(
        'schema_version: "0.2"\nid: home_001\nname: home\nsuite: smoke\n'
        'steps:\n  - action: launch_app\n'
        '  - wait_for:\n      target: screen:HomeView\n'
        '      condition: active\n      timeout: 10\n', encoding="utf-8")
    _git(proj, "init", "-q")
    top = Path(_git(proj, "rev-parse", "--show-toplevel").stdout.strip())
    if top.resolve() != proj.resolve():
        raise RuntimeError(f"gate proj A 仓库根漂移：{top} != {proj}")
    _git(proj, "config", "user.email", "final@gate")
    _git(proj, "config", "user.name", "gate_final")
    (proj / APP_FILE).write_text("// baseline\n", encoding="utf-8")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "baseline")
    (proj / APP_FILE).write_text("// changed\n", encoding="utf-8")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "change")
    head = _git(proj, "rev-parse", "HEAD").stdout.strip()
    # metadata 写盘但**不提交**（M3 Gate 同款：提交会漂 HEAD~1..HEAD 范围）
    (gen / "source_metadata.json").write_text(
        json.dumps(_metadata_a(head), ensure_ascii=False, indent=2),
        encoding="utf-8")
    out = proj / "state"
    out.mkdir()

    # 预置 trace：图上一条 HomeView→SearchView（正式用例只声明到达 HomeView
    # → 这条转移是 coverage gap，G5 才有东西可生成）。
    # ⚠️ 直接经 TraceStore 公共 API 播种，**不是** `mta run`——`--fake-driver`
    # 的 stub 对任何 find 都返回可见元素，动作步不产屏观测（M3 Gate 实测）。
    # 少了这一步，G5 会拿到 `gaps=0` 且**看起来完全正常**。
    from tracer.storage import TraceStore

    tstore = TraceStore(str(out / "trace.db"))
    tstore.start_run("run_seed", suite="smoke", device_type="simulator",
                     app_build="local", app_bundle_id="com.demo")
    tc_run = tstore.start_testcase("run_seed", "seed_001")
    for idx, (step_type, target) in enumerate(SEED_STEPS):
        tstore.record_step(tc_run, idx, step_type, target_id=target,
                           status="SUCCESS")
    tstore.end_testcase(tc_run, "PASS")
    tstore.end_run("run_seed", "PASS", 0)
    return {"proj": proj, "gen": gen, "head": head,
            "suites_root": proj / "suites",
            "agent_db": out / "agent.db", "trace_db": out / "trace.db",
            "exp_db": out / "experience.db",
            "run_trace_db": out / "run_trace.db"}


def mta_generate_a(a: dict, llm_url: str, plan_id: str) -> subprocess:
    # LLM 的注入走环境（provider 读 `LLM_BASE_URL` / `LLM_API_KEY`，M3 Gate 同款）
    return sh(PY, "-m", "cli.main", "generate",
              "--gap-from", plan_id,
              "--repo-root", str(a["proj"]),
              "--generated", str(a["gen"]),
              "--suites-root", str(a["suites_root"]),
              "--db", str(a["trace_db"]),
              "--exp-db", str(a["exp_db"]),
              "--agent-db", str(a["agent_db"]),
              "--bundle-id", "com.demo", "--suite", "smoke",
              "--fake-driver",
              env=_env(LLM_BASE_URL=llm_url, LLM_API_KEY="stub",
                       LLM_MODEL="stub"))


# --- 工程B（设备） --------------------------------------------------------------

def _scaffold_b(tmp: Path) -> dict:
    """工程B：explore + 漂移诊断的自足工程（M5 Gate 同框架）。"""
    proj = tmp / "proj_b"
    gen = proj / "repository" / "generated"
    gen.mkdir(parents=True)
    (proj / "repository" / "overrides").mkdir(parents=True)
    su = proj / "suites" / "gate"
    su.mkdir(parents=True)
    for case_id in ("gate_drift_a", "gate_drift_b"):
        (su / f"{case_id}.yaml").write_text(
            'schema_version: "0.2"\n'
            f"id: {case_id}\nname: {case_id}\nsuite: gate\ntags: [gate]\n"
            'steps:\n  - action: terminate_app\n  - action: launch_app\n'
            '  - action: input\n    target: LoginView.username_field\n'
            '    value: ${TEST_USERNAME}\n'
            '  - action: input\n    target: LoginView.password_field\n'
            '    value: ${TEST_PASSWORD}\n    sensitive: true\n'
            '  - action: tap\n    target: LoginView.login_button\n'
            '    idempotency: IDEMPOTENT\n'
            '  - wait_for:\n      target: screen:HomeView\n'
            '      condition: active\n      timeout: 15\n', encoding="utf-8")
    r = sh(PY, "-m", "cli.main", "repo", "generate", "ios_demo/LoginDemo",
           "--out", str(gen))
    if r.returncode != 0:
        raise RuntimeError(f"repo generate failed: {r.stdout[-400:]}")
    meta = json.loads((gen / "local" / "source_metadata.json").read_text(
        encoding="utf-8"))
    ids = {e.get("id") for s in meta.get("screen_elements", [])
           for e in s.get("elements", [])}
    if "username_field" not in ids or "user_field" in ids:
        raise RuntimeError("工程B generated 必须是改名前的元素集")
    (proj / ".gitignore").write_text("state/\nrepository/generated/\n",
                                     encoding="utf-8")
    _git(proj, "init", "-q")
    _git(proj, "config", "user.email", "final@gate")
    _git(proj, "config", "user.name", "gate_final")
    (proj / "README.md").write_text("gate final b\n", encoding="utf-8")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "baseline")
    out = proj / "state"
    out.mkdir()
    # 小预算：探索是演示不是扫屏。⚠️ `max_llm_calls` **不能给 0**——0 会在
    # step 0 就撞 LLM_BUDGET_EXCEEDED，`explore` 仍然 exit 0、`steps=0
    # `screens=0`，G8a 于是**假绿**（M4 Gate 的 `FRONTIER_EMPTY steps=0`
    # 同款：M4 用的 20，这里照抄）。
    budget = out / "budget.yaml"
    budget.write_text(
        "max_steps: 6\nmax_duration_seconds: 240\nmax_llm_calls: 20\n"
        "max_repeated_state: 3\n", encoding="utf-8")
    return {"proj": proj, "gen": gen / "local", "suites": proj / "suites",
            "build": meta.get("build"),
            "agent_db": out / "agent.db", "trace_db": out / "trace.db",
            "graph_db": out / "graph.db", "exp_db": out / "experience.db",
            "budget": budget}


def _seed_source_face(g: dict) -> None:
    """播种声明面（M4 Gate 同款：evidence=discovery，visit_count=0）。"""
    from graph import SOURCE, GraphStore, RuntimeGraph, ScreenNode
    from graph import ScreenTransition
    from graph.models import EVIDENCE_DISCOVERY

    nodes = tuple(ScreenNode(s, source_of=SOURCE, visit_count=0,
                             evidence=(EVIDENCE_DISCOVERY,))
                  for s in _DECLARED_SCREENS)
    store = GraphStore(g["graph_db"])
    # 两条转移，缺一不可：App 冷启动停在 **LoginView**，只播种
    # HomeView→ProfileView 时 frontier 从 LoginView 出发就是空的，探索会在
    # 第 0 步收场（实测 `outcome=FRONTIER_EMPTY steps=0`——正是 M4 Gate
    # `FRONTIER_EMPTY steps=0` 那个假绿的翻版）。
    store.upsert_graph(RuntimeGraph(
        app_id=BUNDLE, app_build=g["build"], source_of=SOURCE, nodes=nodes,
        transitions=(ScreenTransition("LoginView", "HomeView",
                                      "tap:LoginView.login_button",
                                      source_of=SOURCE, count=0),
                     ScreenTransition("HomeView", "ProfileView",
                                      "tap:HomeView.go_profile",
                                      source_of=SOURCE, count=0))))


def mta_explore_b(g: dict, *extra: str) -> subprocess.CompletedProcess:
    return sh(PY, "-m", "cli.main", "explore",
              "--goal", "探索登录后的未覆盖状态",
              "--budget", str(g["budget"]),
              "--generated", str(g["gen"]),
              "--overrides", str(g["proj"] / "repository/overrides"),
              "--suites-root", str(g["suites"]),
              "--db", str(g["trace_db"]),
              "--graph-db", str(g["graph_db"]),
              "--agent-db", str(g["agent_db"]),
              "--bundle-id", BUNDLE, "--udid", UDID,
              *extra, timeout=600)


def mta_run_suite_b(g: dict) -> subprocess.CompletedProcess:
    return sh(PY, "-m", "cli.main", "run", "--suite", "gate",
              "--suites-root", str(g["suites"]),
              "--generated", str(g["gen"]),
              "--overrides", str(g["proj"] / "repository/overrides"),
              "--db", str(g["trace_db"]), "--exp-db", str(g["exp_db"]),
              "--udid", UDID, "--bundle-id", BUNDLE, timeout=900)


def mta_diagnose_b(g: dict, run_id: str) -> subprocess.CompletedProcess:
    return sh(PY, "-m", "cli.main", "diagnose", "--run-id", run_id,
              "--generated", str(g["gen"]),
              "--overrides", str(g["proj"] / "repository/overrides"),
              "--suites-root", str(g["suites"]),
              "--db", str(g["trace_db"]), "--graph-db", str(g["graph_db"]),
              "--agent-db", str(g["agent_db"]), "--exp-db", str(g["exp_db"]),
              "--bundle-id", BUNDLE, "--udid", UDID, "--no-llm",
              "--repo-root", str(g["proj"]), timeout=900)


# --- 漂移注入 / 还原（M5 Gate 同款） --------------------------------------------

_SOURCE_TOUCHED = False


def inject_drift() -> bool:
    global _SOURCE_TOUCHED
    src = APP_SOURCE.read_text(encoding="utf-8")
    if _DRIFT_ANCHOR not in src:
        print(f"PREFLIGHT ERROR: 锚点不在源码里：{_DRIFT_ANCHOR!r}")
        return False
    APP_SOURCE.write_text(src.replace(_DRIFT_ANCHOR, _DRIFT_RENAME, 1),
                          encoding="utf-8")
    _SOURCE_TOUCHED = True
    if not make("p1-build") or not make("p1-install"):
        return False
    return check("INJECT_drift_renamed",
                 _DRIFT_RENAME in APP_SOURCE.read_text(encoding="utf-8"),
                 "username_field → user_field（不重扫 metadata）")


def restore_source() -> None:
    global _SOURCE_TOUCHED
    if not _SOURCE_TOUCHED:
        return
    r = sh("git", "checkout", "--", str(APP_SOURCE))
    if r.returncode != 0:
        print(f"restore WARN: git checkout 失败：{r.stderr[:200]}")
        return
    if make("p1-build") and make("p1-install"):
        print("restore: 源码已还原 + 重编译重装")
    _SOURCE_TOUCHED = False


def _repo_assets_md5() -> dict:
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


# --- 判据 ---------------------------------------------------------------------

def g1_production_refused(tmp: Path) -> bool:
    """F7：`agent benchmark run --env-kind production` 启动即拒且不写库。"""
    fresh = tmp / "refused.db"
    r = sh(PY, "-m", "cli.main", "agent", "benchmark", "run",
           "--label", "prod", "--agent-db", str(fresh),
           "--env-kind", "production", timeout=120)
    return check("G1_production_refused",
                 r.returncode != 0 and "F7" in (r.stdout + r.stderr)
                 and not fresh.exists(),
                 f"exit={r.returncode} wrote_db={fresh.exists()}")


def g2_benchmark_loop(tmp: Path) -> bool:
    """设计 §13.2 的闭环：run v1 → v2 → compare 无回归；劣化快照 →
    AGENT_REGRESSION exit 1。"""
    db = tmp / "bench.db"
    r1 = sh(PY, "-m", "cli.main", "agent", "benchmark", "run",
            "--label", "v1", "--agent-db", str(db), timeout=300)
    r2 = sh(PY, "-m", "cli.main", "agent", "benchmark", "run",
            "--label", "v2", "--agent-db", str(db), timeout=300)
    ok = check("G2a_benchmark_runs",
               r1.returncode == 0 and r2.returncode == 0,
               f"v1 exit={r1.returncode} v2 exit={r2.returncode}")
    rc = sh(PY, "-m", "cli.main", "agent", "benchmark", "compare",
            "v1", "v2", "--agent-db", str(db), timeout=120)
    ok &= check("G2b_compare_same_ok",
                rc.returncode == 0 and "无回归" in rc.stdout,
                f"exit={rc.returncode}")
    # 写一份劣化快照（diagnosis_accuracy 掉到 0）→ compare 必须判 AGENT_REGRESSION
    from agents.storage import SQLiteAgentStore

    store = SQLiteAgentStore(str(db))
    v3 = store.latest_benchmark_run("v2")
    from agents.benchmark import BenchmarkResult, CaseResult

    degraded = BenchmarkResult(
        cases=tuple(CaseResult(case_id=c.case_id, expected=c.expected,
                               got=c.expected, correct=False)
                    for c in v3.cases),
        diagnosis_accuracy=0.0, llm_path_ok=False,
        llm_calls=v3.llm_calls, duration_ms=1.0)
    store.save_benchmark_run("bench_degraded", label="v3", metrics=degraded)
    rc = sh(PY, "-m", "cli.main", "agent", "benchmark", "compare",
            "v1", "v3", "--agent-db", str(db), timeout=120)
    ok &= check("G2c_agent_regression_detected",
                rc.returncode == 1 and "AGENT_REGRESSION" in rc.stdout,
                f"exit={rc.returncode} "
                f"{(rc.stdout.strip().splitlines() or [''])[0][:80]}")
    return ok


def g3_matrix18_manager(tmp: Path) -> bool:
    """矩阵 #18（manager 级，in-process）：三 Subagent 经 TestManager 协调。"""
    from agents.base import Subagent
    from subagents.diagnosis_agent import DiagnosisAgent
    from subagents.explorer_agent import ExplorerAgent
    from agents.manager import TestManager
    from subagents.planner_agent import PlannerAgent
    from agents.state import EscalationReason
    from agents.storage import SQLiteAgentStore
    from agents.tools import AgentToolkit
    from diagnosis.context import DiagnosisContext
    from explorer.agent import ExplorationOutcome
    from graph.models import RuntimeGraph, TraceStep
    from llm.budget import LLMBudget
    from planner.models import PlannerInput
    from tests.fault_injection.fi_support import drift_repo

    store = SQLiteAgentStore(tmp / "m18.db")

    class _Guard:
        def __init__(self):
            self.checked = 0

        def check(self, ctx):
            self.checked += 1

    class _Ex:
        def find(self, strategies):
            return None

        def tap(self, strategies):
            return {"tapped": True}

        def page_source(self):
            return ("<App><Node name='screen.HomeView' visible='true'/></App>")

    guard = _Guard()
    # **真 Repository**（fi_support.drift_repo 带 screen.HomeView marker）：
    # `get_current_screen` 走的是真 loader 的屏判定，SimpleNamespace 替身会让
    # 这一格测的是一个假货（实测：替身 → AttributeError generated_screens）。
    repo = drift_repo(tmp / "repo_m18")

    def factory(task_id):
        return AgentToolkit(
            guard=guard, executor=_Ex(),
            locator_for=lambda t: [{"type": "accessibility_id",
                                    "value": str(t).split(".")[-1]}],
            repository=repo, agent_db=store, task_id=task_id)

    m = TestManager(agent_db=store, goal="M6 矩阵#18", toolkit_factory=factory)
    planner = m.spawn(PlannerAgent, agent_type="planner")
    explorer = m.spawn(ExplorerAgent, agent_type="explorer")
    diagnosis = m.spawn(DiagnosisAgent, agent_type="diagnosis")

    # 各自产出（薄封装复用 M2/M4/M5 本体）
    plan_result = planner.plan(
        PlannerInput(app_build="local", git_commit="c", changed_files=[]),
        knowledge=_FakeKnowledge(), cases=(), metadata={},
        repository=repo, no_history_reason="trace 库不存在（M6 Gate）")
    from explorer.policy import ProposedAction
    _script = iter([ProposedAction(tool="tap", target="HomeView.login_button"),
                    None])

    exp_result = explorer.explore(
        graph=RuntimeGraph(app_id="b", app_build="1"),
        decider=lambda obs, graph, memory: next(_script, None),
        repository=repo)
    drift = TraceStep(testcase_run_id=1, step_index=1, step_type="input",
                      target_id="username_field", status="FAILED",
                      failure_type="ELEMENT_NOT_FOUND",
                      detail={"recovery_attempted": {
                          "recovery": "no_llm_engine",
                          "stages": [{"stage": "reconcile",
                                      "outcome": "DRIFT"}]}})
    dia_result = diagnosis.analyze(DiagnosisContext(
        run_id="r", app_id="b", app_build="1", steps=(drift,)))

    ok = check("G3a_three_agents_produce",
               plan_result.plan.tasks == []
               and exp_result.outcome is ExplorationOutcome.COMPLETED
               and exp_result.steps == 1
               and len(dia_result.hypotheses) == 1,
               f"plan={len(plan_result.plan.tasks)} tasks, "
               f"explore={exp_result.outcome.value}, "
               f"hyp={len(dia_result.hypotheses)}")

    # 预算独立（矩阵 #18 后半）：三份实例互不相同
    ok &= check("G3b_budgets_independent",
                len({id(planner.llm_budget), id(explorer.llm_budget),
                     id(diagnosis.llm_budget)}) == 3
                and all(isinstance(b, LLMBudget) for b in (
                    planner.llm_budget, explorer.llm_budget,
                    diagnosis.llm_budget)),
                "三份 LLMBudget 实例")

    # trace 按各自 task_id 归因（矩阵 #18 前半）+ 都经同一个 Guard
    traces = {t: store.get_trace(t) for t in (planner.task_id,
                                              explorer.task_id,
                                              diagnosis.task_id)}
    # 只有**调过工具**的 agent 才有 trace 行——planner/diagnosis 是纯计算
    # 薄封装（一个跑 diff、一个跑规则分析），没有工具调用就没有痕。
    # 矩阵 #18 要钉的是**归因**不是行数：每一行都记在自己名下，且
    # **没有哪个 agent 的痕落进别人的 task_id**（共用一个 toolkit 就会）。
    ok &= check("G3c_trace_attributed_per_task",
                bool(traces[explorer.task_id])
                and all(e.task_id == tid for tid, v in traces.items()
                        for e in v)
                and not traces[planner.task_id]
                and not traces[diagnosis.task_id],
                f"trace 行数 {({k: len(v) for k, v in traces.items()})}")
    # 探索脚本只走一步动作，但 guard 被 observe + verify + decide 全程过了一遍
    ok &= check("G3d_guard_is_shared", guard.checked >= 1,
                f"guard.check 调用 {guard.checked} 次（唯一闸门）")

    # 连续 Guard 拦截 → ESCALATED + 接手包完整
    package = m.escalate(explorer, EscalationReason.GUARD_BLOCK_STREAK,
                         detail="连续拦截（Gate 演示）")
    ok &= check("G3e_escalation_package_complete",
                store.get_task(explorer.task_id).state.value == "ESCALATED"
                and package["goal"] == "M6 矩阵#18"
                and package["reason"] == "GUARD_BLOCK_STREAK",
                f"outcome={store.get_task(explorer.task_id).outcome}")
    return ok


class _FakeKnowledge:
    """graph_query 返回空图的最小替身（G3 的 planner 不需要真知识）。"""

    def graph_query(self, app_id):
        from graph.models import RuntimeGraph
        return RuntimeGraph(app_id=app_id, app_build="1")

    def experience_lookup(self, app_id, screen_id, target_id):
        return []

    def trace_history(self, filters):
        return []

    def impact_of(self, target_id):
        return ()


def chain_half(a: dict) -> bool:
    """G4–G7：plan → generate → accept → run（--fake-driver）。"""
    ok = True
    # G4 plan
    plan = sh(PY, "-m", "cli.main", "plan",
              "--repo-root", str(a["proj"]), "--generated", str(a["gen"]),
              "--overrides", str(a["proj"] / "repository/overrides"),
              "--suites-root", str(a["suites_root"]),
              "--db", str(a["trace_db"]), "--exp-db", str(a["exp_db"]),
              "--agent-db", str(a["agent_db"]), "--json", "--no-llm")
    if plan.returncode != 0:
        return check("G4_plan_runs", False,
                     f"exit={plan.returncode} {plan.stderr[-300:]}")
    plan_id = json.loads(plan.stdout)["plan_id"]
    ok &= check("G4_plan_runs", True, f"plan_id={plan_id}")

    # G5 generate（本地 LLM 桩）
    llm_url, srv = _serve_llm(CANDIDATE_STEPS)
    try:
        r = mta_generate_a(a, llm_url, plan_id)
    finally:
        srv.shutdown()
    if r.returncode != 0:
        return check("G5_generate_runs", False,
                     f"exit={r.returncode} {(r.stdout + r.stderr)[-300:]}")
    ok &= check("G5_generate_runs", "gaps=1" in r.stdout,
                f"{[l for l in r.stdout.splitlines() if 'gaps' in l][:1]}")
    from agents.storage import SQLiteAgentStore

    store = SQLiteAgentStore(str(a["agent_db"]))
    cands = store.list_candidates()
    if not cands:
        return check("G6_candidate_pending", False, "库里没有候选")
    cand = cands[0]
    ok &= check("G6_candidate_pending",
                cand["review_status"] == "PENDING"
                and cand["dry_run_status"] == "PASS",
                f"review={cand['review_status']} "
                f"dry_run={cand['dry_run_status']}")

    # G6 人工 accept（F6：落盘 suites/ 的是**人**的动作）
    cid = cand["candidate_id"]
    r = sh(PY, "-m", "cli.main", "candidate", "review", "accept", cid,
           "--kind", "test", "--reviewer", "gate_final",
           "--db", str(a["agent_db"]), "--repo-root", str(a["proj"]),
           "--suites-root", str(a["suites_root"]))
    ok &= check("G6_accept_lands_suite", r.returncode == 0,
                f"exit={r.returncode} "
                f"{(r.stdout.strip().splitlines() or [''])[-1][:90]}")
    landed = list((a["suites_root"] / "smoke").glob(f"{cid}*.yaml"))
    ok &= check("G6b_candidate_yaml_in_suites", bool(landed),
                f"{[p.name for p in landed]}")

    # G7 跑（含新用例，--fake-driver；secrets 走 EnvSecretProvider（env 注入，
    # `_env()` 已给 TEST_USERNAME/TEST_PASSWORD——`mta run` 没有 --secret 旗标）
    case_id = cid.split("_candidate")[0] if "_candidate" in cid else cid
    r = sh(PY, "-m", "cli.main", "run", "--case", case_id,
           "--suites-root", str(a["suites_root"]),
           "--generated", str(a["gen"]),
           "--overrides", str(a["proj"] / "repository/overrides"),
           "--db", str(a["run_trace_db"]), "--fake-driver")
    ok &= check("G7_new_case_runs", r.returncode == 0,
                f"exit={r.returncode} "
                f"{(r.stdout.strip().splitlines() or [''])[-1][:90]}")
    return ok


def device_half(b: dict) -> bool:
    """G8–G9：explore（真机 sandbox）→ 漂移注入 → run 失败 → 诊断 → 确认。"""
    ok = True
    _seed_source_face(b)

    # G8 explore（真机，sandbox；E12：发现事件 PENDING、图不变）
    from graph import SOURCE, GraphStore

    before = GraphStore(b["graph_db"]).load_graph(BUNDLE, b["build"], SOURCE)
    r = mta_explore_b(b)
    print(f"  explore stdout:\n{_indent(r.stdout[-600:])}")
    if r.returncode != 0:
        print(f"  explore stderr:\n{_indent(r.stderr[-400:])}")
    outcome_line = next((l for l in r.stdout.splitlines() if "outcome" in l), "")
    # ⚠️ exit 0 **完全不足以**说明探索跑过：`LLM_BUDGET_EXCEEDED` /
    # `FRONTIER_EMPTY` 都是 exit 0 + steps=0（本 Gate 与 M4 各踩过一次）。
    # 判据钉在「**真的动过**」：steps ≥ 1 且终态是设计 §7.2/7.3 的**有界停止**
    # （COMPLETED / FRONTIER_EMPTY / LOOP_DETECTED / *_BUDGET_EXCEEDED——
    # §1.2 明写「预算耗尽/检测到 Loop/触达 Screen 边界会自动停止」），
    # 唯独排除 LLM_BUDGET_EXCEEDED：那是**配置错**（max_llm_calls=0），
    # 不是有界停止。
    m = re.search(r"outcome=(\w+)\s+steps=(\d+)\s+screens=(\d+)", outcome_line)
    outcome_name, steps_n, screens_n = (m.group(1), int(m.group(2)),
                                        int(m.group(3))) if m else ("", -1, -1)
    bounded = {"COMPLETED", "FRONTIER_EMPTY", "LOOP_DETECTED",
               "STEPS_BUDGET_EXCEEDED", "DURATION_BUDGET_EXCEEDED"}
    ok &= check("G8a_explore_runs",
                r.returncode == 0 and steps_n >= 1 and screens_n >= 1
                and outcome_name in bounded,
                f"exit={r.returncode} outcome={outcome_name} steps={steps_n} "
                f"screens={screens_n}")
    after = GraphStore(b["graph_db"]).load_graph(BUNDLE, b["build"], SOURCE)
    ok &= check("G8b_graph_unchanged_by_explore",
                before.nodes == after.nodes
                and before.transitions == after.transitions,
                "探索不写图（E12）")
    from agents.storage import SQLiteAgentStore

    store = SQLiteAgentStore(str(b["agent_db"]))
    events = store.list_discovery_events()
    ok &= check("G8c_discoveries_pending",
                all(e["review_status"] == "PENDING" for e in events),
                f"{len(events)} 条发现事件全 PENDING")
    # agent_trace 归因 explorer 任务（矩阵 #18 的真机一环）
    import sqlite3
    conn = sqlite3.connect(str(b["agent_db"]))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT t.task_id, t.agent_type, COUNT(*) n FROM agent_tasks t"
            " JOIN agent_trace tr ON tr.task_id = t.task_id"
            " GROUP BY t.task_id").fetchall()
    finally:
        conn.close()
    ok &= check("G8d_trace_attributed",
                any(r0["agent_type"] == "explorer" and r0["n"] > 0
                    for r0 in rows),
                f"{[dict(r0) for r0 in rows]}")

    # G9 漂移注入 → run 失败 → 诊断 → 人工确认（M5 Gate 同款）
    if not inject_drift():
        return check("G9_drift_injected", False, "注入失败")
    r = mta_run_suite_b(b)
    ok &= check("G9a_drift_run_failed", r.returncode != 0,
                f"exit={r.returncode} "
                f"{[l for l in r.stdout.splitlines() if 'FAIL' in l][:2]}")
    conn = sqlite3.connect(str(b["trace_db"]))
    try:
        run_id = conn.execute(
            "SELECT run_id FROM runs WHERE suite='gate'"
            " ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    finally:
        conn.close()
    r = mta_diagnose_b(b, run_id)
    print(f"  diagnose stdout:\n{_indent(r.stdout[-700:])}")
    ok &= check("G9b_diagnose_runs", r.returncode == 0,
                f"exit={r.returncode}")
    rows = store.list_bug_candidates()
    if not rows:
        return check("G9c_candidate_human_review", False, "没有候选")
    row = rows[0]
    ok &= check("G9c_candidate_human_review",
                row["status"] == "HUMAN_REVIEW"
                and row["evidence_score"] >= 10
                and row["attribution_hypothesis"] == "AUTOMATION_DEFECT",
                f"score={row['evidence_score']} "
                f"attribution={row['attribution_hypothesis']}")
    r = sh(PY, "-m", "cli.main", "candidate", "review", "accept",
           row["candidate_id"], "--kind", "bug", "--reviewer", "gate_final",
           "--db", str(b["agent_db"]), "--bundle-dir", str(OUT_DIR / "bundles"))
    ok &= check("G9d_human_confirms",
                r.returncode == 0
                and store.get_bug_candidate(
                    row["candidate_id"])["status"] == "CONFIRMED",
                f"exit={r.returncode} cid={row['candidate_id']}")
    bundle = OUT_DIR / "bundles" / f"{row['candidate_id']}.json"
    ok &= check("G9e_evidence_bundle",
                bundle.exists() and json.loads(
                    bundle.read_text(encoding="utf-8"))["status"] == "CONFIRMED",
                str(bundle.name))
    return ok


def g10_asset_hygiene(a: dict, b: dict, assets_md5: dict) -> bool:
    """矩阵 #17：正式资产零程序修改。"""
    ok = check("G10a_main_repo_assets_untouched",
               _repo_assets_md5() == assets_md5,
               "主仓库 overrides/generated/suites 逐字节未变")
    status_a = _git(a["proj"], "status", "--porcelain").stdout.strip()
    # 工程A唯一允许的差异 = 人工 accept 落的 suites 文件（行为是人，不是程序）。
    # ⚠️ **允许空**：`mta candidate review accept` 自己会 git commit 落的那份，
    # 所以正常情况下 status 是干净的；只有 accept 未提交时才留下 `?? suites/`。
    # 曾经写成 `status_a and ...`（要求非空），把「干净」判成 FAIL——断言比
    # 意图严一格，同样是假红。
    ok &= check("G10b_gate_a_only_human_accept_write",
                all(l.startswith("?? suites/") for l in status_a.splitlines()),
                f"git status = {status_a[:200]!r}")
    ok &= check("G10c_gate_b_clean",
                _git(b["proj"], "status", "--porcelain").stdout.strip() == "",
                "工程B干净")
    src = APP_SOURCE.read_text(encoding="utf-8")
    ok &= check("G10d_app_source_restored",
                "user_field" not in src and "username_field" in src,
                "漂移改名已撤回")
    return ok


def g11_full_pytest() -> bool:
    proc = sh(PY, "-m", "pytest", "tests", "-p", "no:cacheprovider",
              "--basetemp", str(OUT_DIR / "_pytest_tmp"), timeout=2400)
    tail = (proc.stdout.strip().splitlines() or ["<no stdout>"])[-1]
    return check("G11_full_pytest", proc.returncode == 0, tail[:120])


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_DIR / "gate_env"
    # 重跑前清干净：上一轮的 gate 工程/库留在原地会让「同 run_id 重记」和
    # git status 断言失真（Gate 必须**可重入**，否则第二次跑必然 FAIL，
    # 而 FAIL 与真实缺陷长得一模一样）。
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)

    pre_ok = preflight()
    ok = True
    device_half_verdict = "SKIPPED_ENV"
    b = None
    try:
        assets_md5 = _repo_assets_md5()
        # 离线半边（任何设备操作之前）
        ok = g1_production_refused(tmp)
        ok &= g2_benchmark_loop(tmp)
        ok &= g3_matrix18_manager(tmp)

        # 链条半边（离设备）
        a = _scaffold_a(tmp)
        ok &= chain_half(a)

        # 设备半边
        if pre_ok:
            device_half_verdict = "PASS"
            try:
                b = _scaffold_b(tmp)
                ok &= device_half(b)
            except Exception as e:  # noqa: BLE001 — 如实报
                device_half_verdict = "FAIL"
                ok = check("device_half", False,
                           f"{type(e).__name__}: {e}") and ok
        else:
            check("device_half", False, "preflight 未过 → 设备半边跳过")
            ok = False
    except Exception as e:  # noqa: BLE001
        device_half_verdict = "FAIL"
        ok = check("gate_scaffold", False, f"{type(e).__name__}: {e}")
        a = None
        b = None
    finally:
        restore_source()

    if a is not None and b is not None:
        ok &= g10_asset_hygiene(a, b, assets_md5)
    else:
        ok &= check("G10_asset_hygiene", False, "工程没建起来")
    ok &= g11_full_pytest()

    code = 0 if ok else 1
    summary = {"verdict": "PASS" if code == 0 else "FAIL", "gate": "M6-final",
               "udid": UDID, "bundle": BUNDLE,
               "device_half": device_half_verdict,
               "elapsed_s": round(time.time() - _T0, 1), "results": results}
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"summary: {OUT_DIR / 'summary.json'}")
    print(f"verify_p3_final: exit {code} (device_half={device_half_verdict})")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
