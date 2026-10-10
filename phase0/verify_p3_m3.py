#!/usr/bin/env python3
"""Gate M3 / P3-13：`mta generate` + 统一审核 + ACCEPT 落盘 端到端验收（Task 3.5）。

**不碰真实仓库**：全部在 `out/p3_m3_gate/gate_env/` 下自建一个**自足工程**
（自己的 `.git`、自己的 `generated/local`、自己的 suites、自己的 trace 库）——
M2 Gate 的 M4 事故（`rev-parse` 穿透到主仓库）在此显式防御：建仓后断言
`--show-toplevel` == 工程根。

判据（plan Task 3.5 的 Gate + 矩阵 #7–#10 的 CLI 级复核）：

  G1  矩阵 #1：`generate --env-kind production` → **启动即拒**（含 `F7`），
      且不留下任何库文件
  G2  矩阵 #9 前置：`--env-kind staging` → exit 3 且**零候选入库**（不部分写）
  G3  主路径：预置 trace（图上一条 HomeView→SearchView 转移，正式用例未覆盖）
      → `mta plan`（取 plan_id）→ `mta generate`（本地 LLM 桩）→
      `test_candidates` 里那条候选 `review_status=PENDING` / `dry_run_status=PASS`
  G4  矩阵 #8：重跑 generate → 不重复入库，`DUPLICATE` 留痕带 `duplicate_of`
  G5  **ACCEPT 落盘**：`mta candidate review accept` → `suites/<suite>/<id>.yaml`
      + git commit + 库状态 ACCEPT；`mta lint` 对新文件通过；`mta run --case`
      能发现它（设计 6.1「不是新路径」）
  G6  REJECT 无 note → exit 2（argparse 的 required 挡在 handler 之前）
  G7  **AE（handover §9 前置 2）**：ACCEPT 后重跑 generate → 该候选的
      `testcase_yaml` **逐字节不变**（不被生成器覆写）
  G8  全量回归：`pytest tests` 全绿

产出 `out/p3_m3_gate/summary.json`；exit 0 = M3 Gate 通过。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "out" / "p3_m3_gate"
PY = str(ROOT / ".venv/bin/python")
APP_FILE = "Demo.swift"

# 图上的转移：HomeView --tap:HomeView.go_search--> SearchView
# （预置 trace 的屏观测序列；`observed_screens` 只认 `wait_for` + `screen:` 前缀
#  + SUCCESS —— 见 `graph/builder.py:100-140`）
SEED_STEPS = [("launch_app", None), ("wait_for", "screen:HomeView"),
              ("tap", "HomeView.go_search"), ("wait_for", "screen:SearchView")]
EXPECTED_GAP = "HomeView -> SearchView"

# 候选步骤（LLM 桩的返回）：走 go_search 到 SearchView
CANDIDATE_STEPS = json.dumps({
    "steps": [{"action": "tap", "target": "HomeView.go_search"},
              {"wait_for": {"target": "screen:SearchView",
                            "condition": "active", "timeout": 10}}]})

results: dict[str, dict] = {}
_T0 = time.time()


def check(name: str, ok: bool, detail: str) -> bool:
    results[name] = {"pass": bool(ok), "detail": detail}
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
    return bool(ok)


def _env(**extra) -> dict:
    e = dict(os.environ, PYTHONPATH=str(ROOT),
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
    env = _env(GIT_AUTHOR_NAME="gate_m3", GIT_AUTHOR_EMAIL="m3@gate",
               GIT_COMMITTER_NAME="gate_m3", GIT_COMMITTER_EMAIL="m3@gate")
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, env=env, timeout=60)


# --- 自足工程 -----------------------------------------------------------------

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


def _metadata(git_commit: str) -> dict:
    return {"app_version": "debug", "build": "local", "git_commit": git_commit,
            "parser_version": "0.2.0",
            "screens": ["HomeView", "SearchView"],
            "screen_elements": [
                {"name": "HomeView", "elements": [
                    {"id": "go_search", "accessibility_id": "go_search",
                     "type": "button", "resolution_type": "literal",
                     "source": {"file": APP_FILE, "line": 1}}]},
                {"name": "SearchView", "elements": []}]}


def _scaffold(tmp: Path) -> dict:
    """建一个**自足**的 gate 工程（自己的 .git + generated + suites + trace）。"""
    proj = tmp / "proj"
    if proj.exists():
        shutil.rmtree(proj)
    gen = proj / "repository" / "generated" / "local"
    (gen / "elements").mkdir(parents=True)
    (gen / "screens").mkdir(parents=True)
    (gen / "elements" / "HomeView.yaml").write_text(
        _element_yaml("go_search", "HomeView"), encoding="utf-8")
    for screen in ("HomeView", "SearchView"):
        (gen / "screens" / f"{screen}.yaml").write_text(
            _screen_yaml(screen), encoding="utf-8")
    (gen / "source_metadata.json").write_text(
        json.dumps(_metadata("PLACEHOLDER"), ensure_ascii=False, indent=2),
        encoding="utf-8")

    # 正式用例：只声明 HomeView 到达 → HomeView→SearchView 是 gap
    suites = proj / "suites" / "smoke"
    suites.mkdir(parents=True)
    (suites / "home_001.yaml").write_text(
        'schema_version: "0.2"\nid: home_001\nname: home\nsuite: smoke\n'
        'steps:\n  - action: launch_app\n'
        '  - wait_for:\n      target: screen:HomeView\n'
        '      condition: active\n      timeout: 10\n', encoding="utf-8")

    # 自己的 .git（P2 M4 事故：rev-parse 会穿透到主仓库）
    _git(proj, "init", "-q")
    top = _git(proj, "rev-parse", "--show-toplevel").stdout.strip()
    if Path(top) != proj:
        raise RuntimeError(f"gate proj 仓库根漂移：{top} != {proj}")
    _git(proj, "config", "user.email", "m3@gate")
    _git(proj, "config", "user.name", "gate_m3")
    (proj / APP_FILE).write_text("// baseline\n", encoding="utf-8")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "baseline")
    # 第二个 commit：`plan` 的改动范围写死是 `HEAD~1..HEAD`（M2 Gate 同款），
    # 单 commit 的仓库会 `fatal: ambiguous argument 'HEAD~1..'` → exit 3。
    (proj / APP_FILE).write_text("// changed\n", encoding="utf-8")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "change")
    head = _git(proj, "rev-parse", "HEAD").stdout.strip()
    # ⚠️ metadata 写盘但**不提交**：它是生成产物，一旦提交就多出一个 commit，
    # `HEAD~1..HEAD` 的范围会漂（M2 Gate 的 M4 事故同族）。
    (gen / "source_metadata.json").write_text(
        json.dumps(_metadata(head), ensure_ascii=False, indent=2),
        encoding="utf-8")

    out = proj / "state"
    out.mkdir()
    g = {"proj": proj, "gen": gen, "head": head,
         "suites_root": proj / "suites",
         "agent_db": out / "agent.db", "trace_db": out / "trace.db",
         "exp_db": out / "experience.db",
         # ⚠️ `mta run` 用**另一个** trace 库：run 不带 `--bundle-id` 时
         # `runs.app_bundle_id` 是空串，与播种的 `com.demo` 同库会凑出两个
         # (app_id, build) 范围 → `read_trace_steps` 多范围 fail-loud，
         # 后面 generate 的 `graph_query` 就炸了（这是**正确**的既有行为：
         # 图必须限定单一范围）。gate 只需证明「P1 能跑落盘的用例」，
         # 不该顺手污染 generate 的图源。
         "run_trace_db": out / "run_trace.db"}

    # 预置 trace：图上一条 HomeView→SearchView（正式用例未覆盖 → 是 gap）
    # ⚠️ 直接经 TraceStore 公共 API 播种，**不是** `mta run`——`--fake-driver`
    # 的 `_StubEx` 对任何 `find` 都返回可见元素，动作步不产屏观测，单次 run
    # 至多一个节点（0 转移）。播种的形态与 `mta run` 产出的**逐列一致**。
    from tracer.storage import TraceStore

    store = TraceStore(str(g["trace_db"]))
    store.start_run("run_seed", suite="smoke", device_type="simulator",
                    app_build="local", app_bundle_id="com.demo")
    tc_run = store.start_testcase("run_seed", "seed_001")
    for idx, (step_type, target) in enumerate(SEED_STEPS):
        store.record_step(tc_run, idx, step_type, target_id=target,
                          status="SUCCESS")
    store.end_testcase(tc_run, "PASS")
    store.end_run("run_seed", "PASS", 0)
    return g


def mta_generate(g: dict, *extra: str, plan_id: str = "plan_gate",
                 env: dict | None = None):
    return sh(PY, "-m", "cli.main", "generate",
              "--gap-from", plan_id,
              "--repo-root", str(g["proj"]),
              "--generated", str(g["gen"]),
              "--suites-root", str(g["suites_root"]),
              "--db", str(g["trace_db"]),
              "--exp-db", str(g["exp_db"]),
              "--agent-db", str(g["agent_db"]),
              "--bundle-id", "com.demo", "--suite", "smoke",
              "--fake-driver", *extra, env=env)


def mta_candidate(g: dict, *extra: str, env: dict | None = None):
    return sh(PY, "-m", "cli.main", "candidate", "review", *extra,
              "--db", str(g["agent_db"]), env=env)


# --- 本地 LLM 桩（不需要真网关） ---------------------------------------------

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
    host, port = srv.server_address
    return f"http://{host}:{port}/v1", srv


# --- 判据 ---------------------------------------------------------------------

def g1_production_refused(g: dict) -> bool:
    r = mta_generate(g, "--env-kind", "production")
    return check("G1_production_refused",
                 r.returncode != 0 and "F7" in (r.stdout + r.stderr)
                 and not g["agent_db"].exists(),
                 f"exit={r.returncode} 含 F7={('F7' in r.stdout + r.stderr)} "
                 f"agent.db 未创建={not g['agent_db'].exists()}")


def g2_staging_refused_before_writing(g: dict) -> bool:
    r = mta_generate(g, "--env-kind", "staging")
    body = r.stdout + r.stderr
    return check("G2_staging_refused",
                 r.returncode == 3 and "sandbox" in body
                 and not g["agent_db"].exists(),
                 f"exit={r.returncode} 点名 sandbox={'sandbox' in body} "
                 f"未写库={not g['agent_db'].exists()}")


def g3_full_chain(g: dict, llm_url: str) -> str | None:
    """plan → generate → 候选入库 PENDING + dry_run PASS。"""
    plan = sh(PY, "-m", "cli.main", "plan",
              "--repo-root", str(g["proj"]), "--generated", str(g["gen"]),
              "--suites-root", str(g["suites_root"]), "--db", str(g["trace_db"]),
              "--exp-db", str(g["exp_db"]), "--agent-db", str(g["agent_db"]),
              "--no-llm", "--json")
    if plan.returncode != 0:
        check("G3a_plan_runs", False, f"exit={plan.returncode} {plan.stderr[:200]}")
        return None
    plan_id = json.loads(plan.stdout)["plan_id"]   # M2 Gate 同款：--json 取 plan_id
    check("G3a_plan_runs", True, f"plan_id={plan_id}")

    r = mta_generate(g, "--gap-from", plan_id,
                     env=_env(LLM_BASE_URL=llm_url, LLM_API_KEY="stub",
                              LLM_MODEL="stub"))
    if r.returncode != 0:
        check("G3b_generate_runs", False, f"exit={r.returncode} "
              f"{(r.stdout + r.stderr)[:300]}")
        return None
    ok = check("G3b_generate_runs", "gaps=1" in r.stdout,
               f"exit=0 gaps 现场重算={('gaps=1' in r.stdout)}")

    from agents.storage import SQLiteAgentStore

    rows = SQLiteAgentStore(g["agent_db"]).list_candidates()
    if len(rows) != 1:
        check("G3c_candidate_landed", False, f"候选数={len(rows)}")
        return None
    row = rows[0]
    import yaml

    from testcase.schema import parse_testcase_dict
    tc = parse_testcase_dict(yaml.safe_load(row["testcase_yaml"]))
    ok &= check("G3c_candidate_landed",
                row["review_status"] == "PENDING"
                and row["dry_run_status"] == "PASS"
                and bool(row["fingerprint"])
                and tc.generation_evidence.coverage_gap == [EXPECTED_GAP],
                f"review={row['review_status']} dry={row['dry_run_status']} "
                f"fp={'有' if row['fingerprint'] else '无'} "
                f"gap={tc.generation_evidence.coverage_gap}")
    return (row["candidate_id"], plan_id) if ok else (None, plan_id)


def g4_duplicate_is_traced(g: dict, llm_url: str, plan_id: str) -> bool:
    env = _env(LLM_BASE_URL=llm_url, LLM_API_KEY="stub", LLM_MODEL="stub")
    r = mta_generate(g, plan_id=plan_id, env=env)
    from agents.storage import SQLiteAgentStore

    store = SQLiteAgentStore(g["agent_db"])
    rejs = store.list_rejections(reason="DUPLICATE")
    return check("G4_duplicate_traced",
                 r.returncode == 0 and len(store.list_candidates()) == 1
                 and len(rejs) == 1 and bool(rejs[0]["duplicate_of"]),
                 f"exit={r.returncode} 候选数={len(store.list_candidates())} "
                 f"DUPLICATE 留痕={len(rejs)} "
                 f"duplicate_of={rejs[0]['duplicate_of'] if rejs else None}")


def g5_accept_lands_and_enters_p1(g: dict, cid: str) -> bool:
    r = mta_candidate(g, "accept", cid, "--suites-root",
                      str(g["suites_root"]), "--generated", str(g["gen"]),
                      "--repo-root", str(g["proj"]), "--reviewer", "gate")
    body = r.stdout + r.stderr
    target = g["suites_root"] / "smoke" / f"{cid}.yaml"
    log = _git(g["proj"], "log", "--oneline").stdout
    from agents.storage import SQLiteAgentStore

    row = SQLiteAgentStore(g["agent_db"]).get_candidate(cid)
    ok = check("G5a_accept_lands", r.returncode == 0 and target.exists()
               and cid in log and row["review_status"] == "ACCEPT"
               and bool(row["reviewer"]),
               f"exit={r.returncode} 文件={target.exists()} commit={cid in log} "
               f"库={row['review_status']} reviewer={row['reviewer']}")
    if not target.exists():
        return ok
    # P1 流程零改造：lint 通过（文件级入口，`mta lint <file>`）+ run 能发现该用例
    lint = sh(PY, "-m", "cli.main", "lint", str(target),
              "--generated", str(g["gen"]))
    run = sh(PY, "-m", "cli.main", "run", "--case", cid,
             "--suites-root", str(g["suites_root"]),
             "--generated", str(g["gen"]), "--fake-driver", "--no-llm",
             "--db", str(g["run_trace_db"]))
    ok &= check("G5b_enters_p1_flow",
                lint.returncode == 0 and run.returncode == 0,
                f"lint exit={lint.returncode}（{lint.stdout.strip()[:80]}）"
                f" run --case exit={run.returncode}")
    return ok


def g6_reject_requires_note(g: dict) -> bool:
    """REJECT 无 `--note` → argparse 报错（exit 2），且**不留任何决策痕迹**。

    ⚠️ 「不留痕迹」原先只写在 docstring 里、`check()` 没实现（round 2 P3-G）：
    gate 的说明文字与它的判据之间没有自动一致性检查，同一句谎可以再撒一次。

    ⚠️ 判据必须是**前后快照对比**，不能是「所有行都还是 PENDING」——本 check 在
    G5a（accept）**之后**跑，那时库里已经有 ACCEPT 行了。「这次失败的 reject
    没改任何东西」的正确形态是：整张候选表的决策四列逐行不变。
    """
    from agents.storage import SQLiteAgentStore

    def snapshot() -> list[tuple]:
        return [(r["candidate_id"], r["review_status"], r["reviewer"],
                 r["reviewed_at"], r["review_note"])
                for r in SQLiteAgentStore(g["agent_db"]).list_candidates()]

    before = snapshot()
    r = mta_candidate(g, "reject", "whatever")
    after = snapshot()
    body = r.stdout + r.stderr
    return check("G6_reject_requires_note",
                 r.returncode == 2 and "--note" in body and before == after,
                 f"exit={r.returncode} 报错点名 --note={'--note' in body} "
                 f"决策四列未动={before == after}（{len(after)} 行）")


def g7_decided_candidate_not_overwritten(g: dict, cid: str, llm_url: str,
                                         plan_id: str) -> bool:
    """AE：ACCEPT 后重跑 generate → 那条候选的 yaml **逐字节不变**。"""
    from agents.storage import SQLiteAgentStore

    store = SQLiteAgentStore(g["agent_db"])
    before = store.get_candidate(cid)["testcase_yaml"]
    r = mta_generate(g, plan_id=plan_id,
                     env=_env(LLM_BASE_URL=llm_url, LLM_API_KEY="stub",
                              LLM_MODEL="stub"))
    after = store.get_candidate(cid)
    return check("G7_decided_not_overwritten",
                 r.returncode == 0 and after["testcase_yaml"] == before
                 and after["review_status"] == "ACCEPT"
                 and "已人工决策" in r.stdout,
                 f"exit={r.returncode} yaml 未变={after['testcase_yaml'] == before} "
                 f"状态={after['review_status']} "
                 f"notes 留痕={'已人工决策' in r.stdout}")


def g8_full_pytest() -> bool:
    r = sh(PY, "-m", "pytest", "tests", "-p", "no:cacheprovider",
           "--basetemp=./out/_p3m3_pt", timeout=1800)
    shutil.rmtree(ROOT / "out" / "_p3m3_pt", ignore_errors=True)
    tail = [ln for ln in r.stdout.splitlines() if "passed" in ln or "failed" in ln]
    return check("G8_full_pytest", r.returncode == 0,
                 tail[-1] if tail else f"exit={r.returncode}")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_DIR / "gate_env"
    tmp.mkdir(parents=True, exist_ok=True)
    g = _scaffold(tmp)
    print(f"gate proj: {g['proj']}  head={g['head'][:8]}")

    ok = g1_production_refused(g)
    ok &= g2_staging_refused_before_writing(g)

    llm_url, srv = _serve_llm(CANDIDATE_STEPS)
    try:
        cid, plan_id = g3_full_chain(g, llm_url)
        if cid is not None:
            ok &= g4_duplicate_is_traced(g, llm_url, plan_id)
            ok &= g5_accept_lands_and_enters_p1(g, cid)
            ok &= g7_decided_candidate_not_overwritten(g, cid, llm_url, plan_id)
        else:
            check("G4_duplicate_traced", False, "G3 未产出候选")
            check("G5a_accept_lands", False, "G3 未产出候选")
            check("G7_decided_not_overwritten", False, "G3 未产出候选")
    finally:
        srv.shutdown()
        srv.server_close()
    ok &= g6_reject_requires_note(g)
    ok &= g8_full_pytest()

    code = 0 if ok else 1
    summary = {"verdict": "PASS" if code == 0 else "FAIL", "gate": "M3",
               "head": g["head"], "expected_gap": EXPECTED_GAP,
               "elapsed_s": round(time.time() - _T0, 1), "results": results}
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"summary: {OUT_DIR / 'summary.json'}")
    print(f"verify_p3_m3: exit {code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
