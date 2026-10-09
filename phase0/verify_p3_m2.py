#!/usr/bin/env python3
"""Gate M2 / P3-08：`mta plan` 端到端验收（Task 2.4）。

**不碰真实仓库**：全部在 `out/p3_m2_gate/gate_env/` 下自建一个**自足工程**
（自己的 `.git`、自己的 `generated/local`、自己的 suites）——P2 Gate 的 M4 事故
（`rev-parse` 穿透到主仓库）在此显式防御：建仓后断言 `--show-toplevel` == 工程根。

判据（plan Task 2.4 的 Gate 与矩阵 #1/#5/#6）：

  G1  矩阵 #1：`--env-kind production` → **启动即拒**（含 `F7`），且不留下任何库文件
  G2  主路径：`mta plan --json` → 排序正确（**CRITICAL 居顶**）、`reasons` 非空可追溯、
      `git_commit` 是**确定的 40 位 sha**（不是 `HEAD`）
  G3  落库：`agent.db` 的 `test_plans` 能按 `plan_id` 回查，`tasks_json` 与输出一致
  G4  幂等：同输入重跑 → 同一 `plan_id`、**库不增长**（内容寻址 + upsert）
  G5  矩阵 #5：LLM 试图**跨分重排** → 丢弃重排、保留确定性顺序、`audit` 留痕
      （用一个**本地 LLM 桩**驱动，不需要真网关）
  G6  矩阵 #6：`changed_files` 为空 → **空 Plan 并明示原因**
  G7  「无历史」与「读不出来」分界：trace 库不存在 → 按 0.0 计**且每条 reasons 标注**；
      库存在但非 sqlite → **exit 3**（不按无历史兜）
  G8  全量回归：`pytest tests` 全绿

产出 `out/p3_m2_gate/summary.json`；exit 0 = M2 Gate 通过。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "out" / "p3_m2_gate"
PY = str(ROOT / ".venv/bin/python")
CHANGED_SWIFT = "LoginDemoApp.swift"

# 风险：login LOW / pay HIGH / confirm_pay CRITICAL / ghost LOW
# `file` = 12.3 metadata 的**文件级归属**（改动文件 → 元素 → 用例的唯一桥梁）：
# HomeView 的元素归属**被改的那个** Swift 文件，ProfileView 的归属另一个文件
# ——所以 profile_005 不在影响面内（第一版把所有元素都标成改动文件，于是
# profile_005 也进了 Plan，G2a/G5 一起红）。
ELEMENTS = {
    "HomeView": [("login_button", "LOW", CHANGED_SWIFT),
                 ("pay_button", "HIGH", CHANGED_SWIFT),
                 ("confirm_pay_button", "CRITICAL", CHANGED_SWIFT)],
    "ProfileView": [("ghost_button", "LOW", "ProfileScreen.swift")],
}
CASES = {
    "login_001": "HomeView.login_button",
    "pay_002": "HomeView.confirm_pay_button",
    "search_004": "HomeView.pay_button",
    "profile_005": "ProfileView.ghost_button",
}
# 期望：影响面 = {login_001, pay_002, search_004}（profile_005 不在改动文件里）
EXPECTED_ORDER = ["pay_002", "search_004", "login_001"]

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
    env = _env(GIT_AUTHOR_NAME="gate_m2", GIT_AUTHOR_EMAIL="m2@gate",
               GIT_COMMITTER_NAME="gate_m2", GIT_COMMITTER_EMAIL="m2@gate")
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, env=env, timeout=60)


# --- 自足工程 -----------------------------------------------------------------

def _element_yaml(elem: str, screen: str, risk: str) -> str:
    return (f"schema_version: '1.0'\nkind: element\nid: {elem}\n"
            f"screen: {screen}\ntype: button\nstrategies:\n"
            f"- type: accessibility_id\n  value: {elem}\n  origin: source\n"
            f"metadata:\n  origin: source\n  risk: {risk}\n")


def _screen_yaml(screen: str) -> str:
    return (f"schema_version: '1.0'\nkind: screen\nid: {screen}\n"
            f"marker: screen.{screen}\nkind_hint: page\n"
            f"metadata:\n  risk: LOW\n  origin: source\n")


def _metadata(git_commit: str) -> dict:
    rows = []
    for screen, els in ELEMENTS.items():
        rows.append({"name": screen, "elements": [
            {"id": e, "accessibility_id": e, "type": "button",
             "resolution_type": "literal",
             "source": {"file": file, "line": 1}}
            for e, _risk, file in els]})
    return {"app_version": "debug", "build": "local",
            "git_commit": git_commit, "parser_version": "0.2.0",
            "screens": sorted(ELEMENTS), "screen_elements": rows}


def _scaffold(tmp: Path) -> dict:
    """建一个**自足**的 gate 工程（自己的 .git + generated + suites）。"""
    proj = tmp / "proj"
    if proj.exists():
        shutil.rmtree(proj)
    gen = proj / "repository" / "generated" / "local"
    (gen / "elements").mkdir(parents=True)
    (gen / "screens").mkdir(parents=True)
    for screen, els in ELEMENTS.items():
        (gen / "elements" / f"{screen}.yaml").write_text(
            "---\n".join(_element_yaml(e, screen, r) for e, r, _f in els),
            encoding="utf-8")
        (gen / "screens" / f"{screen}.yaml").write_text(_screen_yaml(screen),
                                                        encoding="utf-8")
    (gen / "source_metadata.json").write_text(
        json.dumps(_metadata("PLACEHOLDER"), ensure_ascii=False, indent=2),
        encoding="utf-8")

    suites = proj / "suites" / "smoke"
    suites.mkdir(parents=True)
    for case_id, target in CASES.items():
        (suites / f"{case_id}.yaml").write_text(
            f'schema_version: "0.2"\nid: {case_id}\nname: {case_id}\n'
            f'suite: smoke\nsteps:\n  - action: launch_app\n'
            f'  - action: tap\n    target: {target}\n', encoding="utf-8")

    # 自己的 .git（P2 M4 事故：rev-parse 会穿透到主仓库）
    _git(proj, "init", "-q")
    top = _git(proj, "rev-parse", "--show-toplevel").stdout.strip()
    if Path(top) != proj:
        raise RuntimeError(f"gate proj 仓库根漂移：{top} != {proj}")
    _git(proj, "config", "user.email", "m2@gate")
    _git(proj, "config", "user.name", "gate_m2")
    (proj / CHANGED_SWIFT).write_text("// baseline\n", encoding="utf-8")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "baseline")
    # 「一次真实改动」：改同一个 Swift 文件再提交（12.3 metadata 的文件级归属
    # 指向它 → HEAD~1..HEAD 正好命中）
    (proj / CHANGED_SWIFT).write_text("// changed\n", encoding="utf-8")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "change")
    head = _git(proj, "rev-parse", "HEAD").stdout.strip()
    # ⚠️ metadata 写盘但**不提交**：它是生成产物，而且一旦提交就多出一个 commit，
    # `HEAD~1..HEAD` 会变成「metadata 的改动」而不是「Swift 的改动」（第一版踩了）。
    (gen / "source_metadata.json").write_text(
        json.dumps(_metadata(head), ensure_ascii=False, indent=2),
        encoding="utf-8")

    out = proj / "state"
    out.mkdir()
    return {"proj": proj, "gen": gen, "head": head,
            "agent_db": out / "agent.db", "trace_db": out / "trace.db",
            "exp_db": out / "experience.db"}


def mta_plan(g: dict, *extra: str, env: dict | None = None):
    return sh(PY, "-m", "cli.main", "plan",
              "--repo-root", str(g["proj"]),
              "--generated", str(g["gen"]),
              "--suites-root", str(g["proj"] / "suites"),
              "--db", str(g["trace_db"]),
              "--exp-db", str(g["exp_db"]),
              "--agent-db", str(g["agent_db"]),
              *extra, env=env)


# --- 本地 LLM 桩（矩阵 #5 的驱动，不需要真网关） --------------------------------

class _StubLLM(BaseHTTPRequestHandler):
    """OpenAI 兼容的最小桩：固定返回 `CONTENT`（plan 解释层的 JSON）。"""

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
    r = mta_plan(g, "--env-kind", "production")
    return check("G1_production_refused",
                 r.returncode != 0 and "F7" in (r.stdout + r.stderr)
                 and not g["agent_db"].exists(),
                 f"exit={r.returncode} 含 F7={('F7' in r.stdout + r.stderr)} "
                 f"agent.db 未创建={not g['agent_db'].exists()}")


def g2_ranks_and_reports(g: dict) -> dict | None:
    r = mta_plan(g, "--json")
    if r.returncode != 0:
        check("G2_plan_runs", False, f"exit={r.returncode} {r.stderr[:200]}")
        return None
    payload = json.loads(r.stdout)
    order = [t["testcase_id"] for t in payload["tasks"]]
    ok = check("G2a_ranking", order == EXPECTED_ORDER, f"{order}")
    top = payload["tasks"][0] if payload["tasks"] else {}
    ok &= check("G2b_critical_on_top", top.get("priority") == 90
                and top.get("testcase_id") == "pay_002",
                f"top={top.get('testcase_id')}/{top.get('priority')}")
    ok &= check("G2c_reasons_traceable",
                bool(payload["tasks"]) and all(
                    t["reasons"] and all(x.strip() for x in t["reasons"])
                    for t in payload["tasks"])
                and top.get("reasons", [""])[0].startswith("impact: "),
                f"top reasons={top.get('reasons')}")
    ok &= check("G2d_git_commit_is_a_sha",
                payload["git_commit"] == g["head"]
                and len(payload["git_commit"]) == 40,
                f"git_commit={payload['git_commit']}")
    ok &= check("G2e_range_visible",
                payload["range"]["base"] == "HEAD~1"
                and payload["changed_files"] == [CHANGED_SWIFT],
                f"range={payload['range']} changed={payload['changed_files']}")
    return payload if ok else None


def g3_persisted(g: dict, payload: dict) -> bool:
    conn = sqlite3.connect(g["agent_db"])
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM test_plans WHERE plan_id=?",
                           (payload["plan_id"],)).fetchone()
    finally:
        conn.close()
    if row is None:
        return check("G3_plan_in_db", False, "按 plan_id 查不到行")
    tasks = json.loads(row["tasks_json"])
    return check("G3_plan_in_db",
                 [t["testcase_id"] for t in tasks]
                 == [t["testcase_id"] for t in payload["tasks"]]
                 and row["git_commit"] == payload["git_commit"],
                 f"plan_id={row['plan_id']} tasks={len(tasks)} "
                 f"git_commit={row['git_commit']}")


def g4_idempotent(g: dict, payload: dict) -> bool:
    r = mta_plan(g, "--json")
    again = json.loads(r.stdout)
    conn = sqlite3.connect(g["agent_db"])
    try:
        n = conn.execute("SELECT COUNT(*) FROM test_plans").fetchone()[0]
    finally:
        conn.close()
    return check("G4_rerun_idempotent",
                 r.returncode == 0 and again["plan_id"] == payload["plan_id"]
                 and n == 1,
                 f"plan_id 相同={again['plan_id'] == payload['plan_id']} 行数={n}")


def g5_llm_reorder_rejected(g: dict) -> bool:
    """矩阵 #5：LLM 把 70 分提到 90 分之前 → 丢弃重排 + 保留确定性顺序 + 审计留痕。"""
    content = json.dumps({
        "reasons": {"login_001": "登录用例历史上失败过"},
        "order": ["search_004", "pay_002", "login_001"],   # ← 跨分重排
    })
    url, srv = _serve_llm(content)
    try:
        r = mta_plan(g, "--json", env=_env(LLM_BASE_URL=url, LLM_API_KEY="x",
                                           LLM_MODEL="stub"))
    finally:
        srv.shutdown()
    if r.returncode != 0:
        return check("G5_llm_reorder_rejected", False,
                     f"exit={r.returncode} {r.stderr[:200]}")
    payload = json.loads(r.stdout)
    kinds = [a.get("kind") for a in payload["audit"]]
    order = [t["testcase_id"] for t in payload["tasks"]]
    reasons = {t["testcase_id"]: t["reasons"] for t in payload["tasks"]}
    return check(
        "G5_llm_reorder_rejected",
        order == EXPECTED_ORDER and "llm_order_rejected" in kinds
        and any(x.startswith("llm: ") for x in reasons["login_001"])
        and not any(x.startswith("llm: ") for x in reasons["pay_002"]),
        f"顺序保留={order == EXPECTED_ORDER} audit={kinds} "
        f"解释只落在给过的 id={[x for x in reasons['login_001'] if x.startswith('llm: ')]}")


def g6_empty_diff(g: dict) -> bool:
    """矩阵 #6：base == head → 空 Plan 并**明示**原因。"""
    same = g["gen"].parent / "same"
    same.mkdir(exist_ok=True)
    (same / "source_metadata.json").write_text(
        json.dumps(_metadata(g["head"]), ensure_ascii=False), encoding="utf-8")
    r = mta_plan(g, "--base-build", "same", "--json")
    if r.returncode != 0:
        return check("G6_empty_diff", False, f"exit={r.returncode} {r.stderr[:200]}")
    payload = json.loads(r.stdout)
    return check("G6_empty_diff",
                 payload["tasks"] == []
                 and any("changed_files 为空" in n for n in payload["notes"]),
                 f"tasks={len(payload['tasks'])} notes={payload['notes'][:1]}")


def g7_history_boundary(g: dict) -> bool:
    """「没有历史」按 0.0 计**且每条标注**；「库存在但读不了」→ exit 3。"""
    r = mta_plan(g, "--json")
    payload = json.loads(r.stdout)
    marked = all(any("（无历史数据：trace 库尚不存在）" in x
                     for x in t["reasons"]) for t in payload["tasks"])
    g["trace_db"].write_text("这不是 sqlite 库", encoding="utf-8")
    bad = mta_plan(g, "--json")
    g["trace_db"].unlink()
    return check("G7_history_boundary",
                 marked and bad.returncode == 3
                 and "读 trace 历史失败" in bad.stdout,
                 f"无历史已标注={marked} 坏库 exit={bad.returncode}")


def g8_full_pytest() -> bool:
    r = sh(PY, "-m", "pytest", "tests", "-p", "no:cacheprovider",
           "--basetemp=./out/_p3m2_pt", timeout=1800)
    shutil.rmtree(ROOT / "out" / "_p3m2_pt", ignore_errors=True)
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
    payload = g2_ranks_and_reports(g)
    if payload is not None:
        ok &= g3_persisted(g, payload)
        ok &= g4_idempotent(g, payload)
    else:
        check("G3_plan_in_db", False, "G2 未产出 payload")
        check("G4_rerun_idempotent", False, "G2 未产出 payload")
    ok &= g5_llm_reorder_rejected(g)
    ok &= g6_empty_diff(g)
    ok &= g7_history_boundary(g)
    ok &= g8_full_pytest()

    code = 0 if ok else 1
    summary = {"verdict": "PASS" if code == 0 else "FAIL", "gate": "M2",
               "head": g["head"], "expected_order": EXPECTED_ORDER,
               "elapsed_s": round(time.time() - _T0, 1), "results": results}
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"summary: {OUT_DIR / 'summary.json'}")
    print(f"verify_p3_m2: exit {code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
