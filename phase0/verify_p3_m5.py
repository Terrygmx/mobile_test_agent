#!/usr/bin/env python3
"""Gate M5 / P3-25：Failure Diagnosis 端到端验收（Task 5.6）。

**真机半边**：LoginDemo 装在**真模拟器**上、经**真 Appium 会话**——用**漂移法**
造一次真实失败：把 App 里 `username_field` 改名成 `user_field`（一个真实的源码
改动，也就是定位漂移最典型的根因），**重编重装但不重扫 metadata**（元数据与用例
都还指向旧名字，App 却已经变了——这正是「漂移」的定义），然后跑一条引用
`LoginView.username_field` 的套件：`mta run` 两条用例全 `ELEMENT_NOT_FOUND`，
再 `mta diagnose` 那条 run：规则出假设（漂移 → UI_DRIFT，LLM 零调用）→ 复现
**真的重跑三次** → Evidence Score **11 分过阈值 10** → 归因建议
`AUTOMATION_DEFECT` → Bug 候选落 `HUMAN_REVIEW` → 人工 `candidate review
--kind bug accept` → `CONFIRMED` + Evidence Bundle 落 `out/`。

**离线半边**：F7 启动期拒绝 + 四条口径边界（未知 run → exit 3 / 无
`--base-build` 不写 dedup_key / 纯断言失败 → exit 0 不落候选 / **分数低于阈值
→ 保持 UNTRIAGED**）在自足工程里跑，**在任何设备操作之前**。

## ⚠️ 为什么是漂移法，不是「注入一个崩溃按钮」

底稿与早先版本的本 Gate 都写「注入 `fatalError` 按钮 → App 崩 → WDA 断连 →
`crash_detected` 判据成立」。**这条路被实测证伪**（2026-10-11，本 Gate 的开发
过程）：点一个 `fatalError` 按钮，Appium 把这次 tap 记成 **SUCCESS**，用例
**照常 PASS**——`fatalError` 发生在手势返回之后，WDA 会话不断，`APP_CRASH` /
`WDA_DEAD` 一个都不出现。根因是 **P1 根本没有崩溃判定逻辑**（底稿修订 #6：
`APP_CRASH` 只是 `FAILURE_TYPES` 词表里的一个字符串），所以 `crash_detected`
的第二条判据（infra 事件）在真机上拿不到。

于是 Gate 改用 plan Task 5.6 自己写的**前半句**——「**漂移法**失败 + 注入真实
逻辑 bug」。漂移是 M5 在真机上**唯一可达**的失败形态，也是这条路逼出了下面
三个真缺陷。

⚠️ **漂移的形态必须是「元数据旧、App 新」**：`mta run` 的 lint 会拒掉「元数据里
不存在的 target」（`unknown_target` → exit 3）。所以**不能**先改名再重扫
metadata——那样用例根本跑不起来（那是配置错误，不是漂移）。正确顺序是
**先扫干净源码的 metadata，再改名重编重装，且不重扫**：元数据和用例都指向
`username_field`，App 里已经变成 `user_field`。

## 本 Gate 在开发中抓到的三个真缺陷（都已修 + 单测钉住）

1. **缺陷 A**：`diagnosis/classifier.py` 只读 `detail["recovery"]`，而恢复
   **失败**时（最常见的一种漂移）P2 写的是
   `detail["recovery_attempted"].stages[].outcome == "DRIFT"` → 真机漂移现场
   **一条假设都产不出**（代码与旧单测都绿——单测造的是恢复**成功**的形状）。
2. **缺陷 B**：`cli/main.py::cmd_diagnose` **先算归因、后跑复现**，复现证据
   永远进不了归因的证据集 → 分数 8 但 `attribution_hypothesis` 空，自相矛盾。
   这正是 handover §9「5.6 开工前置①」预见的坑。
3. **缺口 C**：`same_issue_multiple_tests`（权重 3）从 M1 建段起就在权重表里、
   `diagnosis/context.py` 的 docstring 也明写「证据就从这里来」，但**全仓没有
   任何生产者**。后果是漂移现场分数上限只有 3+5=8，**永远过不了阈值 10**——
   F10 的「系统给建议」在真机数据上实际不可达。已补
   `diagnosis/evidence.py::history_corroboration`。

## 判据（plan Task 5.6 的 Gate）

  G1  矩阵 #1/#9：`--env-kind production` → **启动即拒**（含 `F7`），且
      **在写任何库之前**（连 agent.db 文件都不该建）
  G2  口径边界（**离线**，在任何设备操作之前）：
      2a 未知 run → exit 3（不是 traceback、不是静默成功）
      2b **纯断言失败**（无崩溃/无漂移/无 5xx）→ exit 0 且**不落**候选
          （F10 的空证据闸门）
      2c **矩阵 #16 前半**：漂移单独只有 3 分（< 阈值 10）且**无法复现**
          → 候选照落，但 `attribution_hypothesis == "UNTRIAGED"`
          （系统有假设但不替人归因）
  G3  主路径（**真设备**）：漂移 run → `mta diagnose` → `bug_candidates` 一行，
      `status=HUMAN_REVIEW`、**审核三列为空**（系统不替人决策，F10）；
      且没有 `--base-build` → **不写** `dedup_key`（半个三元组会误合并）
  G4  矩阵 #14：规则命中 → **LLM 零调用**（stdout 有「规则命中」）
  G5  矩阵 #16 前半的**反面**：漂移(3) + 复现三次(5) + 多用例佐证(3) = **11**
      ≥ 阈值 10 → `attribution_hypothesis == "AUTOMATION_DEFECT"`。
      ⚠️ 这条同时是「三类证据真的都进了同一个证据集」的钉子——少任何一类
      （复现没跑 / 佐证没生产者）都过不了线
  G6  矩阵 #15：复现**真的重跑了**（`attempts >= 2` 且每次都复现）→
      `reproducible=True`、`reproduction_count>=2`、证据里有
      `reproducible_3_times`
  G7  **人工确认闭环**（F6/F10）：`candidate review --kind bug accept` →
      `CONFIRMED` + `reviewer`/`reviewed_at` 落库 + Evidence Bundle JSON 落
      `out/`（含 `bundle_version`）
  G8  状态机：终态不可再推进（二次决策 fail-loud，状态不被改写）
  G9  **现场卫生**（矩阵 #17 的 M5 版）：结束后主仓库 `overrides/` /
      `generated/` / `suites/` 零修改，**App 源码已还原**（撤回改名 +
      重编译重装，模拟器上是干净 App）
  G10 全量回归：`pytest tests` 全绿

产出 `out/p3_m5_gate/summary.json`；exit 0 = M5 Gate 通过。
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

# `read_app_identity` 不自设 DEVELOPER_DIR——子进程要能看见 simctl。
os.environ.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")

OUT_DIR = ROOT / "out" / "p3_m5_gate"
PY = str(ROOT / ".venv/bin/python")
UDID = os.environ.get("MTA_SIM_UDID", "AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E")
BUNDLE = os.environ.get("MTA_SIM_BUNDLE", "com.phaset0.logindemo")
APP = ROOT / "build/p1/Build/Products/Debug-iphonesimulator/LoginDemo.app"
APP_SOURCE = ROOT / "ios_demo/LoginDemo/LoginDemoApp.swift"

# --- 漂移注入：把一个元素改名 -------------------------------------------------
#
# 一次**真实的源码改动**：`username_field` → `user_field`。这不是伪造的失败，
# 是定位漂移最典型的根因（设计师把元素名改了，自动化脚本还在用旧名字）。
_DRIFT_ANCHOR = '                    .accessibilityIdentifier("username_field")'
_DRIFT_RENAME = '                    .accessibilityIdentifier("user_field")'

# Gate 套件的两条用例。**两条都撞同一个漂移**——这是 `same_issue_multiple_tests`
# 证据（权重 3）的来源：一个改名会打挂所有碰这个元素的用例，那正是「一个根因
# 影响多个用例」。只跑一条的话漂移现场分数上限只有 3+5=8，过不了阈值 10。
GATE_DRIFT_A = "gate_drift_a"
GATE_DRIFT_B = "gate_drift_b"
_GATE_DRIFT_A_YAML = """schema_version: "0.2"
id: gate_drift_a
name: Gate M5 漂移 A：登录（定位 username_field）
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
_GATE_DRIFT_B_YAML = """schema_version: "0.2"
id: gate_drift_b
name: Gate M5 漂移 B：同一个改名元素的另一条路径
suite: gate
tags: [gate]
steps:
  - action: terminate_app
  - action: launch_app
  - action: input
    target: LoginView.username_field
    value: ${TEST_USERNAME}
  - wait_for:
      target: LoginView.login_button
      condition: enabled
      timeout: 5
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
    # ⚠️ 不设 LLM_API_KEY：诊断的 LLM 通路在规则命中时本就不该被碰（G4）。
    e.pop("LLM_API_KEY", None)
    e.update(extra)
    return e


def sh(*args: str, cwd: Path = ROOT, env: dict | None = None,
       timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True,
                          env=env or _env(), cwd=str(cwd), timeout=timeout)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = _env(GIT_AUTHOR_NAME="gate_m5", GIT_AUTHOR_EMAIL="m5@gate",
               GIT_COMMITTER_NAME="gate_m5", GIT_COMMITTER_EMAIL="m5@gate")
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


def make(target: str) -> bool:
    r = sh("make", target)
    if r.returncode != 0:
        print(f"make {target} failed:\n{r.stdout[-1000:]}\n{r.stderr[-500:]}")
    return r.returncode == 0


# --- 漂移注入 / 还原 ----------------------------------------------------------

_SOURCE_TOUCHED = False


def inject_drift() -> bool:
    """把 `username_field` 改名成 `user_field` → 重编译 → 重装。

    ⚠️ **这里刻意不做 `repo generate`**（模块 docstring 的「漂移的形态」）：
    metadata 必须是**改名前**扫的那份。先改名再重扫会让 `mta run` 的 lint 以
    `unknown_target` 拒掉用例（exit 3）——那是配置错误，不是漂移。

    ⚠️ **无条件还原**由 `restore_source()` 在 `finally` 里做：留着一个「定位
    对不上」的 App 在模拟器上，会让下一轮任何 `mta run` 以莫名其妙的方式失败。
    """
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
    after = APP_SOURCE.read_text(encoding="utf-8")
    return check("INJECT_element_renamed",
                 _DRIFT_RENAME in after and "crash_button" not in after,
                 f"{_DRIFT_ANCHOR.strip()} → {_DRIFT_RENAME.strip()}")


def restore_source() -> None:
    """还原源码 + 重编译 + 重装（G9 的判据对象）。"""
    global _SOURCE_TOUCHED
    if not _SOURCE_TOUCHED:
        print("restore: 本次未改源码，无需还原")
        return
    r = sh("git", "checkout", "--", str(APP_SOURCE))
    if r.returncode != 0:
        print(f"restore WARN: git checkout 失败：{r.stderr[:200]}")
        return
    if make("p1-build") and make("p1-install"):
        print("restore: 源码已还原 + 重编译重装")
    else:
        print("restore WARN: 还原版重编译/安装失败（源码已还原，App 未还原）")
    _SOURCE_TOUCHED = False


# --- 自足工程 -----------------------------------------------------------------

def _scaffold(tmp: Path) -> dict:
    """自足工程：自己的 git + suites + 库 + **gate 自己的 generated**。

    ⚠️ generated 由 `repo generate` 从**干净源码**扫出来（不是手写的）——手写
    一份会和真 App 的元素集慢慢脱节，而漂移判据恰恰要求「元数据是旧的但**是准
    的**」。`repo generate` 不带 `--check`：主仓库 `overrides/` 的一致性校验是
    **存量缺口**（`HomeView.cell_` 插值前缀 UNRESOLVABLE），与 Gate 无关，
    不该挡它。

    ⚠️ 这一步**只解析源码、不碰设备**，所以能在离线半边之前安全执行。
    """
    proj = tmp / "proj"
    if proj.exists():
        shutil.rmtree(proj)
    gen = proj / "repository" / "generated"
    gen.mkdir(parents=True)
    (proj / "repository" / "overrides").mkdir(parents=True)
    su = proj / "suites" / "gate"
    su.mkdir(parents=True)
    (su / f"{GATE_DRIFT_A}.yaml").write_text(_GATE_DRIFT_A_YAML,
                                             encoding="utf-8")
    (su / f"{GATE_DRIFT_B}.yaml").write_text(_GATE_DRIFT_B_YAML,
                                             encoding="utf-8")
    r = sh(PY, "-m", "cli.main", "repo", "generate", "ios_demo/LoginDemo",
           "--out", str(gen))
    if r.returncode != 0:
        raise RuntimeError(f"repo generate failed: {r.stdout[-600:]}"
                           f"{r.stderr[-300:]}")
    meta = json.loads((gen / "local" / "source_metadata.json").read_text(
        encoding="utf-8"))
    ids = {e.get("id") for s in meta.get("screen_elements", [])
           for e in s.get("elements", [])}
    if "username_field" not in ids or "user_field" in ids:
        raise RuntimeError(
            "gate generated 必须是改名**前**的元素集：username_field="
            f"{'username_field' in ids} user_field={'user_field' in ids}")

    (proj / ".gitignore").write_text("state/\nrepository/generated/\n",
                                     encoding="utf-8")
    _git(proj, "init", "-q")
    top = Path(_git(proj, "rev-parse", "--show-toplevel").stdout.strip())
    if top.resolve() != proj.resolve():
        raise RuntimeError(f"gate proj 仓库根漂移：{top} != {proj}")
    _git(proj, "config", "user.email", "m5@gate")
    _git(proj, "config", "user.name", "gate_m5")
    (proj / "README.md").write_text("gate m5\n", encoding="utf-8")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "baseline")
    head = _git(proj, "rev-parse", "HEAD").stdout.strip()

    out = proj / "state"
    out.mkdir()
    # ⚠️ `run_id` 占位：G1（production 启动即拒）与 G2a（未知 run）都要走
    # `mta_diagnose`，而它在启动期/前置期就被挡下，**根本读不到 run**——占位值
    # 是什么无关紧要（G2a 甚至另传 `run_nope`）。真正的 run_id 由
    # `device_half` 跑完套件后从 trace 库取出来覆盖。
    return {"proj": proj, "gen": gen / "local", "head": head,
            "suites": proj / "suites", "build": meta.get("build"),
            "run_id": "run_pending",
            "agent_db": out / "agent.db", "trace_db": out / "trace.db",
            "graph_db": out / "graph.db", "exp_db": out / "experience.db"}


def mta_run_suite(g: dict) -> subprocess.CompletedProcess:
    return sh(PY, "-m", "cli.main", "run", "--suite", "gate",
              "--suites-root", str(g["suites"]),
              "--generated", str(g["gen"]),
              "--overrides", str(g["proj"] / "repository/overrides"),
              "--db", str(g["trace_db"]), "--exp-db", str(g["exp_db"]),
              "--udid", UDID, "--bundle-id", BUNDLE, timeout=900)


def mta_diagnose(g: dict, *extra: str,
                 timeout: int = 900) -> subprocess.CompletedProcess:
    return sh(PY, "-m", "cli.main", "diagnose", "--run-id", g["run_id"],
              "--generated", str(g["gen"]),
              "--overrides", str(g["proj"] / "repository/overrides"),
              "--suites-root", str(g["suites"]),
              "--db", str(g["trace_db"]), "--graph-db", str(g["graph_db"]),
              "--agent-db", str(g["agent_db"]), "--exp-db", str(g["exp_db"]),
              "--bundle-id", BUNDLE, "--udid", UDID, "--no-llm", "--repo-root",
              str(g["proj"]), *extra, timeout=timeout)


def _bugs(g: dict) -> list[dict]:
    from agents.storage import SQLiteAgentStore

    if not g["agent_db"].exists():
        return []
    return SQLiteAgentStore(g["agent_db"]).list_bug_candidates()


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.strip().splitlines())


# --- 判据 ---------------------------------------------------------------------

def g1_startup_refusal(g: dict) -> bool:
    """矩阵 #1：production 下自主命令不可用，且**在写任何库之前**。"""
    fresh = g["proj"] / "state/refused_prod.db"
    r = mta_diagnose(g, "--env-kind", "production", "--agent-db", str(fresh),
                     timeout=120)
    wrote = fresh.exists()
    return check("G1_production_refused",
                 r.returncode != 0 and "F7" in (r.stdout + r.stderr)
                 and not wrote,
                 f"exit={r.returncode} wrote_db={wrote} "
                 f"{(r.stdout + r.stderr).strip().splitlines()[:1]}")


def g2_offline_boundaries(g: dict) -> bool:
    """四条口径边界（**离线**，在任何设备操作之前）。"""
    ok = True
    # 2a 未知 run → 前置错误（不是 traceback、不是静默成功）
    r = sh(PY, "-m", "cli.main", "diagnose", "--run-id", "run_nope",
           "--generated", str(g["gen"]), "--suites-root", str(g["suites"]),
           "--db", str(g["trace_db"]), "--graph-db", str(g["graph_db"]),
           "--agent-db", str(g["agent_db"]), "--exp-db", str(g["exp_db"]),
           "--bundle-id", BUNDLE, "--no-llm", timeout=120)
    out = r.stdout + r.stderr
    ok &= check("G2a_unknown_run_is_preflight",
                r.returncode == 3 and out.startswith("PREFLIGHT ERROR:")
                and "Traceback" not in out,
                f"exit={r.returncode} "
                f"{(out.strip().splitlines() or ['<empty>'])[0][:100]}")

    # 2b 纯断言失败 → 规则给不出结论 → exit 0 且**不落**空候选（F10 空证据闸门）
    off = g["proj"] / "state" / "offline"
    off.mkdir(parents=True, exist_ok=True)
    tdb, adb = off / "trace.db", off / "agent.db"
    from tracer.storage import TraceStore
    store = TraceStore(str(tdb))
    store.start_run("run_assert", suite="smoke", device_type="simulator",
                    app_build="local", app_bundle_id=BUNDLE)
    tc = store.start_testcase("run_assert", "pay_001")
    store.record_step(tc, 0, "tap", target_id="pay_button", status="SUCCESS")
    store.record_step(tc, 1, "assert_text", target_id="PayView.amount",
                      status="FAILED", failure_type="ASSERTION_VALUE_MISMATCH",
                      detail={"expected": "9.9", "actual": "10.0"})
    store.end_testcase(tc, "FAIL")
    store.end_run("run_assert", "FAILED", 1)
    r = sh(PY, "-m", "cli.main", "diagnose", "--run-id", "run_assert",
           "--generated", str(g["gen"]), "--suites-root", str(off / "no_suites"),
           "--db", str(tdb), "--graph-db", str(off / "graph.db"),
           "--agent-db", str(adb), "--exp-db", str(off / "exp.db"),
           "--bundle-id", BUNDLE, "--no-llm", timeout=120)
    from agents.storage import SQLiteAgentStore
    wrote_rows = (SQLiteAgentStore(str(adb)).list_bug_candidates()
                  if adb.exists() else [])
    ok &= check("G2b_assertion_only_writes_nothing",
                r.returncode == 0 and not wrote_rows
                and "无假设产出" in r.stdout,
                f"exit={r.returncode} candidates={len(wrote_rows)}")

    # 2c **矩阵 #16 前半**：漂移单独只有 3 分（< 阈值 10）且无法复现
    #     → 候选照落（有证据就不是空假设），但归因保持 UNTRIAGED。
    #     `--suites-root` 指向一个空目录 → 不复现（没有可重跑的用例）。
    tdb2, adb2 = off / "trace2.db", off / "agent2.db"
    (off / "empty_suites").mkdir(parents=True, exist_ok=True)
    drift_detail = {"recovery_attempted": {
        "recovery": "no_llm_engine",
        "stages": [{"stage": "reconcile", "outcome": "DRIFT",
                    "candidates": ["user_field"]}]}}
    store = TraceStore(str(tdb2))
    store.start_run("run_drift_only", suite="smoke",
                    device_type="simulator", app_build="local",
                    app_bundle_id=BUNDLE)
    tc = store.start_testcase("run_drift_only", "gate_drift_a")
    store.record_step(tc, 2, "input", target_id="LoginView.username_field",
                      status="FAILED", failure_type="ELEMENT_NOT_FOUND",
                      detail=drift_detail)
    store.end_testcase(tc, "FAIL")
    store.end_run("run_drift_only", "FAILED", 1)
    r = sh(PY, "-m", "cli.main", "diagnose", "--run-id", "run_drift_only",
           "--generated", str(g["gen"]),
           "--suites-root", str(off / "empty_suites"),
           "--db", str(tdb2), "--graph-db", str(off / "graph2.db"),
           "--agent-db", str(adb2), "--exp-db", str(off / "exp2.db"),
           "--bundle-id", BUNDLE, "--no-llm", timeout=120)
    rows2 = (SQLiteAgentStore(str(adb2)).list_bug_candidates()
             if adb2.exists() else [])
    if not rows2:
        ok &= check("G2c_below_threshold_stays_untriaged", False,
                    "没有候选行")
    else:
        row = rows2[0]
        kinds = {e["kind"] for e in row["evidence"]}
        ok &= check("G2c_below_threshold_stays_untriaged",
                    row["attribution_hypothesis"] == "UNTRIAGED"
                    and row["evidence_score"] < 10
                    and row["status"] == "HUMAN_REVIEW"
                    and kinds == {"source_change_correlated"},
                    f"score={row['evidence_score']} "
                    f"attribution={row['attribution_hypothesis']} "
                    f"status={row['status']} kinds={sorted(kinds)}")
    return ok


def g3_diagnose_drift_run(g: dict) -> bool:
    """主路径（**真设备**）：漂移 run → 候选落 `HUMAN_REVIEW`、审核三列为空。"""
    r = mta_diagnose(g)
    print(f"  G3 stdout:\n{_indent(r.stdout)}")
    if r.returncode != 0:
        print(f"  G3 stderr:\n{_indent(r.stderr[-800:])}")
    ok = check("G3a_diagnose_runs", r.returncode == 0, f"exit={r.returncode}")
    rows = _bugs(g)
    if not rows:
        return check("G3b_candidate_at_human_review", False, "库里没有 bug 候选")
    row = rows[0]
    ok &= check("G3b_candidate_at_human_review",
                row["status"] == "HUMAN_REVIEW"
                and row["reviewer"] is None and row["reviewed_at"] is None
                and row["review_decision"] is None,
                f"status={row['status']} reviewer={row['reviewer']} "
                f"decision={row['review_decision']}")
    ok &= check("G3c_failure_type_is_the_drift",
                row["failure_type"] == "ELEMENT_NOT_FOUND",
                f"failure_type={row['failure_type']}")
    g["candidate_id"] = row["candidate_id"]
    # 口径登记：没有 --base-build → 不写 dedup_key（半个三元组会误合并）
    ok &= check("G3d_no_base_build_no_dedup_key", row["dedup_key"] is None,
                f"dedup_key={row['dedup_key']}")
    return ok


def g4_rule_hit_skips_llm(g: dict) -> bool:
    """矩阵 #14：漂移场景规则命中 → LLM 零调用。"""
    r = mta_diagnose(g)
    out = r.stdout
    return check("G4_rule_hit_no_llm", r.returncode == 0 and "规则命中" in out,
                 f"exit={r.returncode} "
                 f"{[l for l in out.splitlines() if '规则命中' in l][:1]}")


def g5_score_crosses_threshold(g: dict) -> bool:
    """矩阵 #16 前半的**反面**：漂移 + 复现 + 多用例 → 11 分 → 给归因建议。

    ⚠️ handover §9 的「5.6 开工前置①」：漂移单独只值 3 分、复现值 5 分，
    合起来 8 仍然 < 阈值 10——**必须**有第三类证据才过线。这道判据因此同时钉住
    三件事：① 复现证据真的进了同一个证据集（缺陷 B 的回归钉子）；②
    `same_issue_multiple_tests` 真的有生产者（缺口 C 的回归钉子）；③
    方向由 `source_change_correlated` 映射到 `AUTOMATION_DEFECT`
    （UI 变了，失效的是脚本的定位，不是 App 功能）。
    """
    rows = _bugs(g)
    if not rows:
        return check("G5_attribution_suggested", False, "库里没有 bug 候选")
    row = rows[0]
    kinds = {e["kind"] for e in row["evidence"]}
    return check("G5_attribution_suggested",
                 row["attribution_hypothesis"] == "AUTOMATION_DEFECT"
                 and row["evidence_score"] >= 10
                 and {"source_change_correlated", "reproducible_3_times",
                      "same_issue_multiple_tests"} <= kinds,
                 f"attribution={row['attribution_hypothesis']} "
                 f"score={row['evidence_score']} kinds={sorted(kinds)}")


def g5b_affected_tests_match_the_evidence(g: dict) -> bool:
    """**evidence 与 bundle 必须对得上**：证据说「命中 N 个用例」，
    `affected_tests` 就得列 N 个。

    早先 `affected_tests` 只取 `ctx.steps[0].testcase_id`（**第一个**用例），
    于是证据写「2 个用例」而 bundle 里只列 1 个——人工看 bundle 时对不上。
    """
    rows = _bugs(g)
    if not rows:
        return check("G5b_affected_tests_match", False, "库里没有 bug 候选")
    row = rows[0]
    return check("G5b_affected_tests_match",
                 sorted(row["affected_tests"]) == [GATE_DRIFT_A, GATE_DRIFT_B],
                 f"affected_tests={row['affected_tests']}")


def g6_reproduction_is_real(g: dict) -> bool:
    """矩阵 #15：复现**真的重跑了**，且每次都复现（不是「无法确认」）。"""
    rows = _bugs(g)
    if not rows:
        return check("G6_reproduced", False, "库里没有 bug 候选")
    row = rows[0]
    return check("G6_reproduced",
                 row["reproducible"] is True and row["reproduction_count"] >= 2,
                 f"reproducible={row['reproducible']} "
                 f"count={row['reproduction_count']}")


def g7_human_confirmation(g: dict) -> bool:
    """F6/F10 的闭环：`HUMAN_REVIEW` --accept--> `CONFIRMED` + Bundle。"""
    cid = g.get("candidate_id")
    if not cid:
        return check("G7_human_confirm", False, "没有候选 id（G3 未产出）")
    bundle_dir = OUT_DIR / "bundles"
    r = sh(PY, "-m", "cli.main", "candidate", "review", "accept", cid,
           "--kind", "bug", "--reviewer", "gate_m5", "--db",
           str(g["agent_db"]), "--bundle-dir", str(bundle_dir), timeout=120)
    print(f"  G7 stdout:\n{_indent(r.stdout)}")
    ok = check("G7a_accept_confirms", r.returncode == 0, f"exit={r.returncode}")
    from agents.storage import SQLiteAgentStore
    row = SQLiteAgentStore(g["agent_db"]).get_bug_candidate(cid)
    ok &= check("G7b_review_columns_written",
                row is not None and row["status"] == "CONFIRMED"
                and row["reviewer"] == "gate_m5"
                and row["review_decision"] == "ACCEPT"
                and bool(row["reviewed_at"]),
                f"status={row and row['status']} "
                f"reviewer={row and row['reviewer']} "
                f"reviewed_at={row and row['reviewed_at']}")
    bp = bundle_dir / f"{cid}.json"
    bundle = json.loads(bp.read_text(encoding="utf-8")) if bp.exists() else {}
    ok &= check("G7c_evidence_bundle_written",
                bp.exists() and bundle.get("status") == "CONFIRMED"
                and bundle.get("kind") == "bug"
                and bundle.get("bundle_version") == 1
                and bundle.get("evidence_score", 0) >= 10
                and len(bundle.get("evidence") or []) >= 3
                and sorted(bundle.get("affected_tests") or []) ==
                [GATE_DRIFT_A, GATE_DRIFT_B],
                f"path={bp.name} status={bundle.get('status')} "
                f"score={bundle.get('evidence_score')} "
                f"evidence={len(bundle.get('evidence') or [])} 条 "
                f"affected={bundle.get('affected_tests')}")
    return ok


def g8_terminal_state_is_final(g: dict) -> bool:
    """状态机：终态不可再推进（二次决策 fail-loud，不静默成功）。"""
    cid = g.get("candidate_id")
    if not cid:
        return check("G8_terminal_is_final", False, "没有候选 id")
    r = sh(PY, "-m", "cli.main", "candidate", "review", "reject", cid,
           "--kind", "bug", "--reviewer", "gate_m5", "--note", "二次决策",
           "--db", str(g["agent_db"]), timeout=120)
    out = r.stdout + r.stderr
    ok = check("G8_terminal_is_final",
               r.returncode != 0 and "HUMAN_REVIEW" in out,
               f"exit={r.returncode} "
               f"{(out.strip().splitlines() or ['<empty>'])[-1][:120]}")
    from agents.storage import SQLiteAgentStore
    row = SQLiteAgentStore(g["agent_db"]).get_bug_candidate(cid)
    ok &= check("G8b_status_unchanged", row is not None
                and row["status"] == "CONFIRMED",
                f"status={row and row['status']}（不该被二次决策改写）")
    return ok


def g9_site_hygiene(g: dict) -> bool:
    """矩阵 #17 的 M5 版：现场干净、正式资产零修改、源码已还原。"""
    src = APP_SOURCE.read_text(encoding="utf-8")
    ok = check("G9a_app_source_restored",
               "user_field" not in src and "username_field" in src,
               "注入的改名已撤回（username_field 回来了）")
    ok &= check("G9b_gate_proj_clean",
                _git(g["proj"], "status", "--porcelain").stdout.strip() == "",
                "自足工程 git status 干净")
    ok &= check("G9c_repo_assets_untouched",
                _repo_assets_md5() == g["assets_md5"],
                "overrides/ generated/ suites/ 逐字节未变")
    return ok


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


def g10_full_pytest() -> bool:
    proc = sh(PY, "-m", "pytest", "tests", "-p", "no:cacheprovider",
              "--basetemp", str(OUT_DIR / "_pytest_tmp"), timeout=2400)
    tail = (proc.stdout.strip().splitlines() or ["<no stdout>"])[-1]
    return check("G10_full_pytest", proc.returncode == 0, tail[:120])


# --- 设备半边 ----------------------------------------------------------------

def device_half(g: dict) -> bool:
    """注入漂移 → `mta run` 真的失败 → `mta diagnose` → 人工确认。"""
    ok = True
    # 1. 注入（改源码 → build → install；**不重扫 metadata**，顺序是硬约束）
    if not inject_drift():
        return check("device_half", False, "注入元素改名失败")

    # 2. 跑套件。两条用例撞同一个漂移 → 都是 ELEMENT_NOT_FOUND。
    #    ⚠️ 每条 run 一个 run_id：`mta run` 自造，Gate 从 trace 库里取（权威，
    #    不解析 stdout 文案）。
    r = mta_run_suite(g)
    print(f"  drift run stdout:\n{_indent(r.stdout)}")
    drift_lines = [l for l in r.stdout.splitlines()
                   if "ELEMENT_NOT_FOUND" in l][:2]
    ok &= check("D1_drift_run_failed", r.returncode != 0,
                f"exit={r.returncode} {drift_lines}")
    import sqlite3
    conn = sqlite3.connect(str(g["trace_db"]))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT run_id FROM runs WHERE suite='gate'").fetchall()
    except sqlite3.Error as e:
        return check("D2_run_id_found", False, f"读 runs 表失败：{e}")
    finally:
        conn.close()
    if not rows:
        return check("D2_run_id_found", False, "trace 里没有 run 行")
    g["run_id"] = rows[0]["run_id"]
    ok &= check("D2_run_id_found", True, f"run_id={g['run_id']}")

    # 3. 诊断 + 复现重跑（真设备）
    ok &= g3_diagnose_drift_run(g)
    ok &= g4_rule_hit_skips_llm(g)
    ok &= g6_reproduction_is_real(g)
    ok &= g5_score_crosses_threshold(g)
    ok &= g5b_affected_tests_match_the_evidence(g)
    # 4. 人工确认闭环 + 状态机终态
    ok &= g7_human_confirmation(g)
    ok &= g8_terminal_state_is_final(g)
    return ok


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = OUT_DIR / "gate_env"
    tmp.mkdir(parents=True, exist_ok=True)

    pre_ok = preflight()
    g = None
    device_half_verdict = "SKIPPED_ENV"
    ok = True
    try:
        g = _scaffold(tmp)
        g["assets_md5"] = _repo_assets_md5()
        print(f"gate proj: {g['proj']}  head={g['head'][:8]}")

        # 离线半边（**任何设备操作之前**）
        ok = g1_startup_refusal(g)
        ok &= g2_offline_boundaries(g)

        # 设备半边。⚠️ 跳过时**不算通过**——没有设备的 exit 0 会是一句
        # 「M5 Gate 通过」的假话（与 M4 Gate 同款口径）。
        if pre_ok:
            device_half_verdict = "PASS"
            try:
                ok &= device_half(g)
            except Exception as e:  # noqa: BLE001 — 设备半边炸了要如实报
                device_half_verdict = "FAIL"
                ok = check("device_half", False,
                           f"{type(e).__name__}: {e}") and ok
        else:
            check("device_half", False, "preflight 未过（无 booted 模拟器 / "
                                        "Appium 未起 / App 未构建）→ 设备半边跳过")
            ok = False
    except Exception as e:  # noqa: BLE001 — 基建失败也要走到还原
        device_half_verdict = "FAIL"
        ok = check("gate_scaffold", False, f"{type(e).__name__}: {e}")
        g = None
    finally:
        # ⚠️ **无条件还原**：Gate 中途崩了也要把「定位对不上」的 App 撤掉。
        restore_source()

    if g is not None:
        ok &= g9_site_hygiene(g)
    else:
        ok &= check("G9_site_hygiene", False, "自足工程没建起来，无法验卫生")
    ok &= g10_full_pytest()

    code = 0 if ok else 1
    summary = {"verdict": "PASS" if code == 0 else "FAIL", "gate": "M5",
               "udid": UDID, "bundle": BUNDLE,
               "device_half": device_half_verdict,
               "elapsed_s": round(time.time() - _T0, 1), "results": results}
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"summary: {OUT_DIR / 'summary.json'}")
    print(f"verify_p3_m5: exit {code} (device_half={device_half_verdict})")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
