#!/usr/bin/env python3
"""M4 Gate 真机验证（Task 4.3 / P1-13）：故障矩阵 + 漂移链路 + Gate M4 判据。

设计 §17 Gate M4（判据机械映射，review_m3_task32 P2-2 教训——判据逐条编号
可机械判定）：
  G1. 24 项故障矩阵全过（tests/fault_injection/test_matrix_01_12.py +
      test_matrix_13_24.py，FakeDriver 版 pytest 实跑）；
  G2. 漂移 build → RECOVERED + 退出码 5（矩阵 #2 真机版：真实改名重编译，
      verify_stage9_f5 同款方法）；
  G3. --no-llm 同场景 FAIL（Gate M4 原文「按预期 FAIL」：无 LLM 时本地
      reconcile 只判 DRIFT 不给候选 → 不可恢复 → exit 1）；
  G4. LLM 调用率 ≤ 10%（19 节指标：LLM recovery calls / 已执行 steps；
      12 步用例中 1 步漂移 → 8.3%）。

真机项分工（plan Task 4.3 step 3）：#2 漂移在本脚本真机实跑；#16/#17 WDA
重试链已在 FakeDriver 矩阵覆盖（真机 WDA 进程击杀不可确定性自动化，M2 Gate
已验真机 WDA 会话/重启语义），本脚本不重复。

漂移设计：源码 username_field → user_field（非敏感输入框，LLM 按 prompt
规则确定判 LOW——F5 实测结论；登录钮等提交类可能判 HIGH 会被 fail-closed
拒绝，不能作硬断言）。用例仍写 username_field → ElementNotFound →
reconcile DRIFT（本地只分类不给候选）→ LLM 唯一候选 user_field →
RECOVERED → 后续步骤全部按原 id 正常执行。

流程：
  1. make repo-generate（基线，身份与 HEAD 对齐，G8 不断）；
  2. 改名 → make p1-build（plist 注入 HEAD，工作区改动不影响 commit）→
     make p1-install → 重扫描到临时目录（不带 --check——overrides 手工
     旧 id 悬空正是 12.6 应报的，漂移 run 只需登记新 id 的 metadata）；
  3. G2/G4 真机跑（用例仍引旧 id；--generated 指临时产物）→ 查
     TraceStore 断言；
  4. G3 同场景 --no-llm → FAIL；
  5. git checkout 还原源码 + 重编译重装 + repo-generate（现场还原，可重跑）。

前置：booted 模拟器、Appium 4723、LLM 网关（LLM_BASE_URL，默认内网 15721）。
产出：out/p1_m4_gate/summary.json；exit 0 = Gate M4 真机判据全绿。
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "out" / "p1_m4_gate"
BUNDLE = "com.phaset0.logindemo"
UDID = os.environ.get("MTA_SIM_UDID", "AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E")
APP_SOURCE = ROOT / "ios_demo/LoginDemo/LoginDemoApp.swift"
OLD_ID, NEW_ID = "username_field", "user_field"
PY = str(ROOT / ".venv/bin/python")

results: dict[str, dict] = {}
_T0 = time.time()


def sh(*args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True,
                          env=_env(), cwd=ROOT, timeout=timeout)


def _env() -> dict:
    e = dict(os.environ)
    e.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    e.setdefault("LLM_BASE_URL", "http://127.0.0.1:15721/v1")
    # 网关可用模型随 CC Switch 上游变化（step-5-preview 现走火山 Agentplan
    # 不支持 chat/completions；glm-5-3-flash 实测 200）。可 env 覆盖。
    e.setdefault("LLM_MODEL", "glm-5-3-flash")
    e.setdefault("LLM_API_KEY", "sk-test")  # F5 同款：内网网关不校验 key
    e.setdefault("TEST_USERNAME", "qa_agent")
    e.setdefault("TEST_PASSWORD", "qa_pass")  # 测试环境任意非空即可（App 源 99 行）
    e["MTA_SIM_UDID"] = UDID
    return e


def check(name: str, ok: bool, detail: str) -> bool:
    results[name] = {"pass": bool(ok), "detail": detail}
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
    return bool(ok)


def preflight() -> bool:
    ok = True
    r = sh("xcrun", "simctl", "list", "devices", "booted")
    ok &= check("PF_simulator_booted", UDID in r.stdout and "Booted" in r.stdout,
                f"UDID={UDID}")
    try:
        with urllib.request.urlopen("http://127.0.0.1:4723/status",
                                    timeout=3) as resp:
            appium = resp.status == 200
    except Exception:
        appium = False
    ok &= check("PF_appium_4723", appium, "Appium status 200")
    try:
        base = _env()["LLM_BASE_URL"].rstrip("/")
        with urllib.request.urlopen(f"{base}/models", timeout=4) as resp:
            gw = resp.status == 200
    except Exception:
        gw = False
    ok &= check("PF_llm_gateway", gw, f"LLM_BASE_URL={_env()['LLM_BASE_URL']}")
    return ok


def g1_matrix_pytest() -> bool:
    proc = sh(PY, "-m", "pytest",
              "tests/fault_injection/test_matrix_01_12.py",
              "tests/fault_injection/test_matrix_13_24.py",
              "-p", "no:cacheprovider", "-q", timeout=600)
    tail = (proc.stdout.strip().splitlines() or ["<no stdout>"])[-1]
    return check("G1_matrix_24of24", proc.returncode == 0,
                 f"exit={proc.returncode} {tail[:120]}")


def make(target: str) -> bool:
    r = sh("make", target)
    if r.returncode != 0:
        print(f"make {target} failed:\n{r.stdout[-1200:]}\n{r.stderr[-600:]}")
    return r.returncode == 0


def ensure_original_source() -> None:
    """上次中断可能残留改名态——基线 repo-generate（--check 会因 overrides
    与改名后源码的缺口 fail）必须在原始源码上跑。"""
    if OLD_ID in APP_SOURCE.read_text(encoding="utf-8"):
        return
    print("ensure_original_source: 检出改名残留 → 还原")
    sh("git", "checkout", "--", str(APP_SOURCE))
    make("p1-build")
    make("p1-install")


def rename_and_build(gen_root: Path) -> bool:
    """真实改名 → 注入构建 → 安装 → 重扫描登记新 id。

    重扫描不带 --check：overrides 里手工登记的旧 id 悬空正是 12.6 应报的
    缺口（真实漂移场景语义），漂移 run 只需要一份**登记了新 id** 的
    metadata。用例仍引用旧 id → 运行时 ElementNotFound → reconcile 无候选
    → LLM 提新 id（已登记 → 9.3 全链通过）→ RECOVERED。
    """
    src = APP_SOURCE.read_text(encoding="utf-8")
    if NEW_ID in src and OLD_ID not in src:
        pass  # 上次中断残留的改名形态，直接续跑
    elif OLD_ID not in src:
        print(f"PREFLIGHT ERROR: {OLD_ID} 不在源码中")
        return False
    else:
        APP_SOURCE.write_text(src.replace(f'"{OLD_ID}"', f'"{NEW_ID}"'),
                              encoding="utf-8")
    if not (make("p1-build") and make("p1-install")):
        return False
    r = sh(PY, "-m", "cli.main", "repo", "generate", "ios_demo/LoginDemo",
           "--out", str(gen_root))
    if r.returncode != 0:
        print(f"repo generate failed: {r.stdout[-600:]}{r.stderr[-300:]}")
        return False
    meta = json.loads(
        (gen_root / "local" / "source_metadata.json").read_text())
    ids = {e["accessibility_id"]
           for s in meta["screen_elements"] for e in s["elements"]
           if e.get("accessibility_id")}
    if NEW_ID not in ids or OLD_ID in ids:
        print(f"PREFLIGHT ERROR: 重扫描产物不含 {NEW_ID}（ids 样本："
              f"{sorted(ids)[:8]}）")
        return False
    return True


def restore() -> None:
    """还原源码 + 还原版重装 + 重扫描（保持 G8：App/Metadata 同 commit）。"""
    if OLD_ID in APP_SOURCE.read_text(encoding="utf-8"):
        print("restore: 源码已是原样，跳过 checkout")
    else:
        r = sh("git", "checkout", "--", str(APP_SOURCE))
        if r.returncode != 0:
            print(f"restore WARN: git checkout 失败: {r.stderr[:200]}")
            return
    if make("p1-build") and make("p1-install"):
        make("repo-generate")
    else:
        print("restore WARN: 还原版重编译/安装失败（源码已还原，App 未还原）")


DRIFT_CASE = """\
schema_version: "0.2"
id: drift_001
name: M4 Gate 漂移恢复（username_field 真实改名）
suite: driftgate
tags: [drift]
precondition:
  reset: RELAUNCH
steps:
  - action: launch_app
  - wait_for:
      target: LoginView.password_field
      condition: visible
      timeout: 10
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
      timeout: 10
  - action: tap
    target: HomeView.go_profile
  - wait_for:
      target: screen:ProfileView
      condition: active
      timeout: 10
  - assertion:
      target: ProfileView.logout_button
      condition: exists
      timeout: 5
  - action: tap
    target: ProfileView.logout_button
    idempotency: NON_IDEMPOTENT
    postcondition:
      target: screen:LoginView
      condition: active
      timeout: 10
  - wait_for:
      target: screen:LoginView
      condition: active
      timeout: 10
  - assertion:
      target: LoginView.password_field
      condition: exists
      timeout: 5
"""


def _scaffold() -> Path:
    tmp = OUT_DIR / "drift_env"
    (tmp / "suites").mkdir(parents=True, exist_ok=True)
    (tmp / "suites" / "drift_001.yaml").write_text(DRIFT_CASE, encoding="utf-8")
    return tmp


DRIFT_ALIAS_DOC = """
---
# M4 Gate：模拟 9.5 review-accept 产物——LLM 候选 user_field 经人工确认后
# 落 overrides（含风险声明）。rename 漂移的恢复闭环 = 人的确认（risk 声明）
# + LLM 桥接运行时命名；无此声明则源扫描元素 risk=None → LLM_RISK_BLOCKED
# （candidate_risk_allowed 的 fail-closed 纪律，设计 9.3 第 4 行）。
schema_version: "1.0"
kind: element
id: user_field
screen: LoginView
type: textfield
strategies:
  - {type: accessibility_id, value: user_field, origin: manual,
     source_file: LoginDemoApp.swift, source_line: 66}
metadata:
  risk: LOW
  idempotency: IDEMPOTENT
  data_class: INTERNAL
"""


def prepare_overrides(tmp: Path) -> Path:
    """overrides 副本 + user_field 别名（H15：绝不写 repository/overrides）。
    每次重建副本——上次的追加残留会让 id 检查跳过而保留坏文档。"""
    import shutil
    ov = tmp / "overrides"
    if ov.exists():
        shutil.rmtree(ov)
    shutil.copytree(ROOT / "repository" / "overrides", ov)
    lv = ov / "elements" / "LoginView.yaml"
    text = lv.read_text(encoding="utf-8")
    if "id: user_field" not in text:
        lv.write_text(text + DRIFT_ALIAS_DOC, encoding="utf-8")
    return ov


def mta_run(db: Path, suites: Path, gen_root: Path, overrides: Path,
            *extra: str) -> subprocess.CompletedProcess:
    return sh(PY, "-m", "cli.main", "run", "--case", "drift_001",
              "--suites-root", str(suites), "--db", str(db),
              "--udid", UDID, "--bundle-id", BUNDLE,
              "--metadata", str(gen_root / "local" / "source_metadata.json"),
              "--generated", str(gen_root / "local"),
              "--overrides", str(overrides),
              *extra, timeout=900)


def _db(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db)
    return conn


def run_facts(db: Path) -> dict:
    conn = _db(db)
    try:
        run = conn.execute(
            "SELECT exit_code, status, llm_enabled, llm_calls FROM runs"
            " ORDER BY rowid DESC LIMIT 1").fetchone()
        tc = conn.execute(
            "SELECT status FROM testcase_runs ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        steps = conn.execute(
            "SELECT COUNT(*) FROM steps s JOIN testcase_runs t"
            " ON s.testcase_run_id = t.id WHERE t.status IS NOT NULL"
        ).fetchone()[0]
        rec_step = conn.execute(
            "SELECT COUNT(*) FROM steps WHERE status='RECOVERED'"
        ).fetchone()[0]
        llm = conn.execute(
            "SELECT kind, candidate_target, effective_risk, result,"
            " reject_reason FROM recoveries WHERE kind='LLM'"
        ).fetchall()
        return {"exit_code": run[0], "run_status": run[1],
                "llm_enabled": run[2], "llm_calls": run[3],
                "tc_status": tc[0] if tc else None, "steps": steps,
                "recovered_steps": rec_step, "llm_rows": llm,
                "provider_errors": conn.execute(
                    "SELECT COUNT(*) FROM steps"
                    " WHERE failure_type='LLM_PROVIDER_ERROR'"
                ).fetchone()[0]}
    finally:
        conn.close()


def g2_g3_g4(gen_root: Path) -> bool:
    tmp = OUT_DIR / "drift_env"
    suites = tmp / "suites"
    for stale in tmp.glob("trace_*.db"):
        stale.unlink()  # 上次中断残留：recoveries 是全库查询，不能串 run
    overrides = prepare_overrides(tmp)

    # --- G2 + G4：漂移 + 真 LLM → RECOVERED / exit 5 / 调用率 ≤10% ---
    # 网关（CC Switch 代理）间歇性 5xx：仅对 LLM_PROVIDER_ERROR 整 run 重试
    # 一次（不重试任何校验链拒绝——那些是确定性语义）。
    db = tmp / "trace_llm.db"
    for attempt in range(1, 3):
        r = mta_run(db, suites, gen_root, overrides)
        print(f"  G2 attempt {attempt} stdout tail:",
              (r.stdout.strip().splitlines() or ["<empty>"])[-3:])
        if r.stderr.strip():
            print("  G2 stderr tail:", r.stderr.strip().splitlines()[-3:])
        f = run_facts(db)
        if f["provider_errors"] == 0:
            break
        print(f"  G2 attempt {attempt}: LLM_PROVIDER_ERROR（网关抖动）→ 重试")
        db.unlink(missing_ok=True)  # recoveries 全库查询，重试换新库
    print(f"  G2 run: exit={r.returncode} facts={f}")
    ok2 = check("G2_drift_recovered_exit5",
                r.returncode == 5 and f["exit_code"] == 5
                and f["tc_status"] == "RECOVERED"
                and f["recovered_steps"] >= 1 and len(f["llm_rows"]) == 1,
                f"proc_exit={r.returncode} run_exit={f['exit_code']} "
                f"tc={f['tc_status']} recovered_steps={f['recovered_steps']} "
                f"llm_rows={f['llm_rows']}")
    if not ok2:
        return False

    rate = (f["llm_calls"] / f["steps"]) if f["steps"] else 1.0
    if not check("G4_llm_rate_le_10pct",
                 rate <= 0.10 and f["llm_calls"] >= 1,
                 f"{f['llm_calls']}/{f['steps']} = {rate:.1%}"
                 "（llm_calls=budget 实数落库，≥1 防 LLM 静默未跑）"):
        return False

    # --- G3：同场景 --no-llm → FAIL / exit 1 / 零 LLM 调用 ---
    db2 = tmp / "trace_nollm.db"
    r2 = mta_run(db2, suites, gen_root, overrides, "--no-llm")
    print("  G3 stdout tail:",
          (r2.stdout.strip().splitlines() or ["<empty>"])[-4:])
    f2 = run_facts(db2)
    print(f"  G3 run: exit={r2.returncode} facts={f2}")
    return check("G3_no_llm_same_drift_fails",
                 r2.returncode == 1 and f2["exit_code"] == 1
                 and f2["tc_status"] == "FAIL"
                 and not f2["llm_enabled"] and f2["llm_calls"] == 0,
                 f"proc_exit={r2.returncode} run_exit={f2['exit_code']} "
                 f"tc={f2['tc_status']} llm_enabled={f2['llm_enabled']} "
                 f"llm_calls={f2['llm_calls']}")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ok = preflight()
    if ok:
        ok = g1_matrix_pytest()
    tmp = _scaffold()
    gen_root = tmp / "generated"
    if ok:
        ensure_original_source()
        ok = check("G0_baseline_and_drift_build",
                   make("repo-generate") and rename_and_build(gen_root),
                   "基线重扫描（G8 对齐 HEAD）→ 真实改名重编译安装 → "
                   "重扫描登记新 id")
    try:
        if ok:
            ok = g2_g3_g4(gen_root)
    finally:
        restore()
    return _finish(0 if ok else 1)


def _finish(code: int) -> int:
    summary = {"verdict": "PASS" if code == 0 else "FAIL",
               "gate": "M4", "udid": UDID,
               "elapsed_s": round(time.time() - _T0, 1),
               "results": results}
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"summary: {OUT_DIR / 'summary.json'}")
    print(f"verify_p1_m4: exit {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
