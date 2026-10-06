#!/usr/bin/env python3
"""Gate M5 / P2 最终验收：设计 18 节 10 步全链演示（Task 5.5）。

步骤映射（设计 18）：

  S1  步骤 1–2：漂移（username_field→user_field 真实改名重编译）→ 首跑
      ELEMENT_NOT_FOUND → LLM → RECOVERED_LLM(exit 5) → 人工 accept →
      Candidate（E5 种子三件套）。
  S2  步骤 3：同库二跑 → RECOVERED_EXPERIENCE / LLM calls=0。
  S3  步骤 4：重复运行 10 次（每次独立 run_id，experience 命中各记一个
      SUCCESS 样本）→ `mta experience verify`（E4：username_field
      LOW/IDEMPOTENT，资格经 Repository 解析）→ VERIFIED(THRESHOLD_MET)。
  S4  步骤 5–6：AMBIGUOUS / RISK 拦截——**判定侧由矩阵 pytest 覆盖**
      （#2/#3，FakeDriver；E13：Guard 是离设备纯函数，真机注入两匹配/
      高风险屏需再造两个 App 变体，边际价值低）。G1 断言之。
  S5  步骤 7：二次漂移（user_field→user_field_gone）→ 两跑经恢复路径记
      失败样本（TARGET_NOT_FOUND，4.7 计失败）→ verify → DEGRADED
      (SLIDING_WINDOW，E6 窗口压总体)。
  S6  步骤 8：还原 App（user_field 回归可用）→ `mta experience revalidate`
      （人工自报证据留痕，M4 定档）→ VERIFIED(REVALIDATED)。
  S7  步骤 9：`mta experience promote` 两段式 → overrides 并入 + git
      commit + promoted 记账 → 再跑 exit 0 / PASS / 零恢复 / 零 LLM
      （正常 find() 命中）。
  S8  步骤 10：`git revert` → Repository 回退 → 再跑 RECOVERED_EXPERIENCE
      / LLM 0 / Store 记账原样（9.6/E10 两条独立事实来源）。
  S9  P1 底线：空 experience 库 + --no-llm 同场景 FAIL / exit 1。
  G1  plan step 2 的 16/16 矩阵全量回归：`pytest tests -q` 全绿
      （P1 24 项 + P2 16 项都在 tests/ 内）。

产出 out/p2_final_gate/summary.json；exit 0 = P2 全链验收通过。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "out" / "p2_final_gate"
BUNDLE = "com.phaset0.logindemo"
UDID = os.environ.get("MTA_SIM_UDID", "AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E")
APP_SOURCE = ROOT / "ios_demo/LoginDemo/LoginDemoApp.swift"
OLD_ID, NEW_ID = "username_field", "user_field"
GONE_ID = "user_field_gone"
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
    e.setdefault("LLM_MODEL", "glm-5-3-flash")
    e.setdefault("LLM_API_KEY", "sk-test")
    e.setdefault("TEST_USERNAME", "qa_agent")
    e.setdefault("TEST_PASSWORD", "qa_pass")
    e["no_proxy"] = e["NO_PROXY"] = "127.0.0.1,localhost,::1"
    e["MTA_SIM_UDID"] = UDID
    return e


def check(name: str, ok: bool, detail: str) -> bool:
    results[name] = {"pass": bool(ok), "detail": detail}
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
    return bool(ok)


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
    base = _env()["LLM_BASE_URL"].rstrip("/")
    ok &= check("PF_llm_gateway", _probe(f"{base}/models", 5),
                f"LLM_BASE_URL={_env()['LLM_BASE_URL']}")
    return ok


def g1_full_pytest() -> bool:
    """plan step 2：16/16 矩阵全量回归（P1 24 项 + P2 16 项都在 tests/）。"""
    base = OUT_DIR / "_pytest_tmp"
    proc = sh(PY, "-m", "pytest", "tests", "-q",
              "-p", "no:cacheprovider", "--basetemp", str(base), timeout=900)
    tail = (proc.stdout.strip().splitlines() or ["<no stdout>"])[-1]
    return check("G1_full_pytest_regression", proc.returncode == 0,
                 f"exit={proc.returncode} {tail[:140]}")


def make(target: str) -> bool:
    r = sh("make", target)
    if r.returncode != 0:
        print(f"make {target} failed:\n{r.stdout[-1200:]}\n{r.stderr[-600:]}")
    return r.returncode == 0


def ensure_original_source() -> None:
    if OLD_ID in APP_SOURCE.read_text(encoding="utf-8"):
        return
    print("ensure_original_source: 检出改名残留 → 还原")
    sh("git", "checkout", "--", str(APP_SOURCE))
    make("p1-build")
    make("p1-install")


_SOURCE_TOUCHED = False


def _rename(src: str, old: str, new: str) -> bool:
    global _SOURCE_TOUCHED
    if f'accessibilityIdentifier("{old}")' not in src:
        return False
    APP_SOURCE.write_text(
        src.replace(f'accessibilityIdentifier("{old}")',
                    f'accessibilityIdentifier("{new}")'), encoding="utf-8")
    _SOURCE_TOUCHED = True
    return True


def build_and_rescan(gen_root: Path, expect_id: str) -> bool:
    if not (make("p1-build") and make("p1-install")):
        return False
    r = sh(PY, "-m", "cli.main", "repo", "generate", "ios_demo/LoginDemo",
           "--out", str(gen_root))
    if r.returncode != 0:
        print(f"repo generate failed: {r.stdout[-400:]}{r.stderr[-200:]}")
        return False
    meta = json.loads((gen_root / "local" / "source_metadata.json")
                      .read_text())
    ids = {e["accessibility_id"] for s in meta["screen_elements"]
           for e in s["elements"] if e.get("accessibility_id")}
    return expect_id in ids


def restore() -> None:
    if not _SOURCE_TOUCHED:
        print("restore: 本次未改源码，无需还原")
        return
    if OLD_ID in APP_SOURCE.read_text(encoding="utf-8"):
        print("restore: 源码已是原样，跳过 checkout")
    else:
        sh("git", "checkout", "--", str(APP_SOURCE))
    if make("p1-build") and make("p1-install"):
        make("repo-generate")
    else:
        print("restore WARN: 还原版重编译/安装失败")


DRIFT_CASE = """\
schema_version: "0.2"
id: drift_final
name: Gate M5 全链演示（username_field 真实改名）
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
      timeout: 20
  - action: tap
    target: HomeView.go_profile
  - wait_for:
      target: screen:ProfileView
      condition: active
      timeout: 20
  - assertion:
      target: ProfileView.logout_button
      condition: exists
      timeout: 5
"""

ALIAS_DOC = """
---
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


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(_env(),
               GIT_AUTHOR_NAME="gate_m5", GIT_AUTHOR_EMAIL="m5@gate",
               GIT_COMMITTER_NAME="gate_m5", GIT_COMMITTER_EMAIL="m5@gate")
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, env=env,
                          timeout=60)


def _scaffold_proj(tmp: Path) -> Path:
    proj = tmp / "proj"
    if proj.exists():
        shutil.rmtree(proj)
    (proj / "suites").mkdir(parents=True)
    (proj / "suites" / "drift_final.yaml").write_text(DRIFT_CASE,
                                                      encoding="utf-8")
    (proj / ".gitignore").write_text("*.db\nstate/\n_pytest/\n",
                                     encoding="utf-8")
    return proj


def _prepare_repo(proj: Path, gen_root: Path) -> Path:
    ov = proj / "repository" / "overrides"
    shutil.copytree(ROOT / "repository" / "overrides", ov)
    lv = ov / "elements" / "LoginView.yaml"
    text = lv.read_text(encoding="utf-8")
    if "id: user_field" not in text:
        lv.write_text(text + ALIAS_DOC, encoding="utf-8")
    gen_dst = proj / "generated"
    shutil.copytree(gen_root, gen_dst)
    # ⚠️ 认 proj 自己的 .git（rev-parse 会穿透到主仓库——M4 Gate 事故）
    if not (proj / ".git").exists():
        _git(proj, "init", "-q")
    toplevel = _git(proj, "rev-parse", "--show-toplevel").stdout.strip()
    if Path(toplevel) != proj:
        raise RuntimeError(f"gate proj 仓库根漂移：{toplevel} != {proj}")
    _git(proj, "config", "user.email", "m5@gate")
    _git(proj, "config", "user.name", "gate_m5")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "baseline: generated + overrides(alias)")
    return ov


def mta_run(proj: Path, db: Path, exp_db: Path, *extra: str) -> subprocess.CompletedProcess:
    return sh(PY, "-m", "cli.main", "run", "--case", "drift_final",
              "--suites-root", str(proj / "suites"), "--db", str(db),
              "--udid", UDID, "--bundle-id", BUNDLE,
              "--metadata", str(proj / "generated" / "local"
                                / "source_metadata.json"),
              "--generated", str(proj / "generated" / "local"),
              "--overrides", str(proj / "repository" / "overrides"),
              "--exp-db", str(exp_db),
              *extra, timeout=900)


def _db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def trace_facts(db: Path) -> dict:
    conn = _db(db)
    try:
        run = conn.execute("SELECT exit_code, status, llm_calls FROM runs"
                           " ORDER BY rowid DESC LIMIT 1").fetchone()
        tc = conn.execute("SELECT status, detail_json FROM testcase_runs"
                          " ORDER BY rowid DESC LIMIT 1").fetchone()
        recs = [dict(r) for r in conn.execute(
            "SELECT kind, candidate_target FROM recoveries ORDER BY id")]
        reviews = [dict(r) for r in conn.execute(
            "SELECT id, review_status FROM recovery_reviews ORDER BY id")]
        detail = json.loads(tc["detail_json"]) if tc and tc["detail_json"] \
            else {}
        return {"exit_code": run["exit_code"] if run else None,
                "llm_calls": run["llm_calls"] if run else None,
                "tc_status": tc["status"] if tc else None,
                "recovered_kinds": detail.get("recovered_kinds"),
                "recoveries": recs, "reviews": reviews}
    finally:
        conn.close()


def exp_row(exp_db: Path, exp_id: str) -> dict:
    conn = _db(exp_db)
    try:
        r = conn.execute("SELECT status, sample_count, success_count,"
                         " promoted, promoted_commit FROM experiences"
                         " WHERE experience_id=?", (exp_id,)).fetchone()
        return dict(r) if r else {}
    finally:
        conn.close()


def s1_first_run_accept(proj: Path, exp_db: Path) -> tuple[bool, str | None]:
    """设计 18 步骤 1–2。带环境抖动重试（provider 错误 / 恢复后等待超时）。"""
    db = proj / "state" / "trace_s1.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    f: dict = {}
    r = None
    for attempt in range(1, 5):
        db.unlink(missing_ok=True)
        r = mta_run(proj, db, exp_db)
        f = trace_facts(db)
        print(f"  S1 attempt {attempt}: exit={r.returncode} facts={f}")
        conn = _db(db)
        try:
            provider_errors = conn.execute(
                "SELECT COUNT(*) FROM steps WHERE failure_type="
                "'LLM_PROVIDER_ERROR'").fetchone()[0]
            recovered = conn.execute(
                "SELECT COUNT(*) FROM steps WHERE status='RECOVERED'"
            ).fetchone()[0]
            wait_timeout = conn.execute(
                "SELECT COUNT(*) FROM steps WHERE failure_type="
                "'WAIT_TIMEOUT'").fetchone()[0]
        finally:
            conn.close()
        if provider_errors == 0 and not (recovered and wait_timeout):
            break
        print(f"  S1 attempt {attempt}: 环境抖动 → 重试")
    ok = check(
        "S1_step1_2_llm_recover_and_accept",
        bool(r) and r.returncode == 5 and f["exit_code"] == 5
        and f["tc_status"] == "RECOVERED"
        and f["recovered_kinds"] == ["RECOVERED_LLM"]
        and len(f["reviews"]) == 1
        and f["reviews"][0]["review_status"] == "PENDING",
        f"exit={r.returncode if r else None} kinds={f['recovered_kinds']}")
    if not (ok and f["reviews"]):
        return False, None
    review_id = f["reviews"][0]["id"]
    r = sh(PY, "-m", "cli.main", "review", "accept", str(review_id),
           "--db", str(db), "--exp-db", str(exp_db),
           "--reviewer", "gate_m5", "--out",
           str(proj / "state" / "patch.yaml"))
    conn = _db(exp_db)
    try:
        exps = [dict(x) for x in conn.execute(
            "SELECT experience_id, status FROM experiences").fetchall()]
    finally:
        conn.close()
    ok2 = check("S1_accept_creates_candidate",
                r.returncode == 0 and len(exps) == 1
                and exps[0]["status"] == "CANDIDATE",
                f"exit={r.returncode} experiences={exps}")
    return ok2, (exps[0]["experience_id"] if ok2 else None)


def s2_second_run_experience_hit(proj: Path, exp_db: Path) -> bool:
    """设计 18 步骤 3：Experience HIT / LLM=0。"""
    db = proj / "state" / "trace_s2.db"
    db.unlink(missing_ok=True)
    r = mta_run(proj, db, exp_db)
    f = trace_facts(db)
    print(f"  S2 run: exit={r.returncode} facts={f}")
    return check(
        "S2_step3_experience_hit_zero_llm",
        r.returncode == 5 and f["tc_status"] == "RECOVERED"
        and f["recovered_kinds"] == ["RECOVERED_EXPERIENCE"]
        and f["llm_calls"] == 0,
        f"exit={r.returncode} kinds={f['recovered_kinds']} "
        f"llm={f['llm_calls']}")


def s3_repeat_runs_to_verified(proj: Path, exp_db: Path,
                               exp_id: str) -> bool:
    """设计 18 步骤 4：10 次独立 run（distinct run_id）→ verify → VERIFIED。"""
    all_ok = True
    for i in range(10):
        db = proj / "state" / f"trace_s3_{i}.db"
        db.unlink(missing_ok=True)
        r = mta_run(proj, db, exp_db)
        f = trace_facts(db)
        if not (r.returncode == 5
                and f["recovered_kinds"] == ["RECOVERED_EXPERIENCE"]):
            all_ok = check(f"S3_run_{i}", False,
                           f"exit={r.returncode} facts={f}")
            break
        print(f"  S3 run {i + 1}/10: RECOVERED_EXPERIENCE ✓")
    row = exp_row(exp_db, exp_id)
    ok_samples = check(
        "S3_samples_accumulated",
        row.get("sample_count") == 11 and row.get("success_count") == 11,
        f"samples={row.get('sample_count')}/{row.get('success_count')}"
        "（S2 的 1 次 + 本步 10 次，E11 全记）")
    r = sh(PY, "-m", "cli.main", "experience", "verify",
           "--exp-db", str(exp_db),
           "--overrides", str(proj / "repository" / "overrides"))
    after = exp_row(exp_db, exp_id)
    return all_ok and ok_samples and check(
        "S3_step4_verified_via_threshold",
        r.returncode == 0 and after.get("status") == "VERIFIED",
        f"verify exit={r.returncode} status={after.get('status')}"
        "（E4 资格经 Repository 解析：username_field LOW/IDEMPOTENT）")


def s5_sliding_window_degrade(proj: Path, exp_db: Path,
                              exp_id: str) -> bool:
    """设计 18 步骤 7：二次漂移 → 2 次失败样本 → DEGRADED(SLIDING_WINDOW)。"""
    src = APP_SOURCE.read_text(encoding="utf-8")
    if not _rename(src, NEW_ID, GONE_ID):
        return check("S5_second_drift", False, f"{NEW_ID} 不在源码中")
    if not build_and_rescan(proj / "generated_gone", GONE_ID):
        return check("S5_second_drift", False, "重编译/重扫描失败")
    # generated 换成新扫描产物（overrides 里的别名 user_field 已无人匹配）
    shutil.rmtree(proj / "generated")
    shutil.copytree(proj / "generated_gone", proj / "generated")
    for i in range(2):
        db = proj / "state" / f"trace_s5_{i}.db"
        db.unlink(missing_ok=True)
        r = mta_run(proj, db, exp_db)
        print(f"  S5 fail-run {i + 1}/2: exit={r.returncode}")
    r = sh(PY, "-m", "cli.main", "experience", "verify",
           "--exp-db", str(exp_db),
           "--overrides", str(proj / "repository" / "overrides"))
    after = exp_row(exp_db, exp_id)
    return check(
        "S5_step7_degraded_sliding_window",
        r.returncode == 0 and after.get("status") == "DEGRADED",
        f"verify exit={r.returncode} status={after.get('status')}"
        "（E6：最近 5 次 2 失败 → 降级，压倒总体成功率）")


def s6_revalidate(proj: Path, exp_db: Path, exp_id: str) -> bool:
    """设计 18 步骤 8：App 还原 → 显式重验证 → VERIFIED(REVALIDATED)。"""
    src = APP_SOURCE.read_text(encoding="utf-8")
    if not _rename(src, GONE_ID, NEW_ID):
        return check("S6_restore_app", False, f"{GONE_ID} 不在源码中")
    if not build_and_rescan(proj / "generated_restored", NEW_ID):
        return check("S6_restore_app", False, "重编译/重扫描失败")
    shutil.rmtree(proj / "generated")
    shutil.copytree(proj / "generated_restored", proj / "generated")
    r = sh(PY, "-m", "cli.main", "experience", "revalidate", exp_id,
           "--exp-db", str(exp_db), "--fingerprint",
           "gate-final-revalidate-observed", "--operator", "gate_m5")
    after = exp_row(exp_db, exp_id)
    return check(
        "S6_step8_revalidated",
        r.returncode == 0 and after.get("status") == "VERIFIED",
        f"revalidate exit={r.returncode} status={after.get('status')}"
        "（显式重验证：人工自报证据留痕，M4 定档口径）")


def s7_promote_and_normal_find(proj: Path, exp_db: Path,
                               exp_id: str) -> tuple[bool, str]:
    """设计 18 步骤 9：promote → overrides 并入 + commit → 正常 find 命中。"""
    r = sh(PY, "-m", "cli.main", "experience", "promote", exp_id,
           "--exp-db", str(exp_db), "--operator", "gate_m5")
    m = re.search(r"prop_[0-9a-f]{12}", r.stdout)
    if r.returncode != 0 or not m:
        check("S7_promote_generate", False,
              f"exit={r.returncode} {r.stdout[-200:]}")
        return False, ""
    r = sh(PY, "-m", "cli.main", "experience", "promote", exp_id,
           "--exp-db", str(exp_db), "--approve", m.group(0),
           "--repo-root", str(proj), "--operator", "gate_m5")
    m_sha = re.search(r"[0-9a-f]{40}", r.stdout)
    sha = m_sha.group(0) if m_sha else ""
    ov_file = (proj / "repository" / "overrides" / "elements"
               / "LoginView.yaml")
    ok1 = check(
        "S7_step9_promoted",
        r.returncode == 0 and sha and "origin: experience" in
        ov_file.read_text(encoding="utf-8")
        and exp_row(exp_db, exp_id).get("promoted") == 1,
        f"exit={r.returncode} sha={sha[:10]}")
    if not ok1:
        return False, ""
    db = proj / "state" / "trace_s7.db"
    db.unlink(missing_ok=True)
    r = mta_run(proj, db, exp_db)
    f = trace_facts(db)
    ok2 = check(
        "S7_normal_find_no_recovery",
        r.returncode == 0 and f["tc_status"] == "PASS"
        and f["recoveries"] == [] and f["llm_calls"] == 0,
        f"exit={r.returncode} tc={f['tc_status']} llm={f['llm_calls']}"
        "（promoted 策略进 Locator 链，不再进 Recovery）")
    return ok2, sha


def s8_revert(proj: Path, exp_db: Path, exp_id: str, sha: str) -> bool:
    """设计 18 步骤 10：git revert → Repository 回退、Store 历史原样。"""
    r = _git(proj, "revert", "--no-edit", sha)
    if r.returncode != 0:
        return check("S8_git_revert", False, r.stderr[:200])
    ov_file = (proj / "repository" / "overrides" / "elements"
               / "LoginView.yaml")
    reverted = "origin: experience" not in ov_file.read_text(
        encoding="utf-8")
    db = proj / "state" / "trace_s8.db"
    db.unlink(missing_ok=True)
    r = mta_run(proj, db, exp_db)
    f = trace_facts(db)
    row = exp_row(exp_db, exp_id)
    return check(
        "S8_step10_revert_store_intact",
        reverted and r.returncode == 5
        and f["recovered_kinds"] == ["RECOVERED_EXPERIENCE"]
        and f["llm_calls"] == 0
        and row.get("promoted") == 1 and row.get("promoted_commit") == sha,
        f"reverted={reverted} exit={r.returncode} "
        f"kinds={f['recovered_kinds']} promoted={row.get('promoted')}"
        "（Store 记账是 revert 改不掉的事实；revert 后每 run 走恢复路径"
        "是预期稳态）")


def s9_p1_baseline(proj: Path) -> bool:
    db = proj / "state" / "trace_s9.db"
    empty = proj / "state" / "empty_experience.db"
    db.unlink(missing_ok=True)
    empty.unlink(missing_ok=True)
    r = mta_run(proj, db, empty, "--no-llm")
    f = trace_facts(db)
    return check("S9_p1_baseline_empty_store_no_llm",
                 r.returncode == 1 and f["exit_code"] == 1
                 and f["tc_status"] == "FAIL" and f["llm_calls"] == 0,
                 f"exit={r.returncode} tc={f['tc_status']}")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pre_ok = preflight()
    g1_ok = g1_full_pytest()
    tmp = OUT_DIR / "gate_env"
    tmp.mkdir(parents=True, exist_ok=True)
    device_ok = pre_ok
    exp_db = tmp / "proj" / "state" / "experience.db"
    if device_ok:
        if (tmp / "proj").exists():
            shutil.rmtree(tmp / "proj")
        ensure_original_source()
        # 跑批收尾只还原源码不重装——安装的 App 可能仍是上一轮漂移版，
        # 直接 repo-generate 会扫出 ADDED/REMOVED verdict FAIL（实测踩过）。
        # 强制重建重装，让「安装的 App = HEAD 源码」后再做基线重扫描。
        device_ok = check("G0_realigned_build",
                          make("p1-build") and make("p1-install"),
                          "强制重建重装（对齐 HEAD 源码，防跑批漂移残留）")
        gen_root = tmp / "generated_scan"
        if device_ok:
            # 顺序是硬约束（seeder 教训原文：绝不在漂移态调 repo-generate）：
            # 先在**原始源码**上做基线重扫描（步骤 1「不更新 source_metadata」
            # 的前提），再改名 + 重编译 + 重扫描到**临时目录**。
            device_ok = check("G0_baseline_rescan", make("repo-generate"),
                              "基线重扫描（原始源码 → repository/generated）")
        if device_ok:
            if _rename(APP_SOURCE.read_text(encoding="utf-8"),
                       OLD_ID, NEW_ID):
                device_ok = check(
                    "G0_drift_build", build_and_rescan(gen_root, NEW_ID),
                    "真实改名重编译安装 → 重扫描到临时目录（步骤 1，"
                    "仓库 metadata 保持旧名制造漂移）")
            else:
                device_ok = check("G0_drift_build", False,
                                  f"{OLD_ID} 不在源码中")
        try:
            if device_ok:
                proj = _scaffold_proj(tmp)
                ov = _prepare_repo(proj, gen_root)
                device_ok, exp_id = s1_first_run_accept(proj, exp_db)
                if device_ok and exp_id:
                    device_ok = s2_second_run_experience_hit(proj, exp_db)
                if device_ok and exp_id:
                    device_ok = s3_repeat_runs_to_verified(proj, exp_db,
                                                           exp_id)
                if device_ok and exp_id:
                    device_ok = s5_sliding_window_degrade(proj, exp_db,
                                                          exp_id)
                if device_ok and exp_id:
                    device_ok = s6_revalidate(proj, exp_db, exp_id)
                if device_ok and exp_id:
                    device_ok, sha = s7_promote_and_normal_find(proj,
                                                                exp_db,
                                                                exp_id)
                if device_ok and sha:
                    device_ok = s8_revert(proj, exp_db, exp_id, sha)
                if device_ok:
                    device_ok = s9_p1_baseline(proj)
        finally:
            restore()
    ok = pre_ok and g1_ok and device_ok
    device_half = ("PASS" if device_ok
                   else ("SKIPPED_ENV" if not pre_ok else "FAIL"))
    code = 0 if ok else 1
    blocked = [k for k, v in results.items()
               if k.startswith("PF_") and not v["pass"]]
    summary = {"verdict": "PASS" if code == 0 else "FAIL",
               "gate": "M5-final", "udid": UDID,
               "g1_full_pytest": "PASS" if g1_ok else "FAIL",
               "device_half": device_half, "blocked_by": blocked,
               "design18_steps": {
                   "1-2": "S1", "3": "S2", "4": "S3", "5-6": "G1 矩阵 #2/#3",
                   "7": "S5", "8": "S6", "9": "S7", "10": "S8"},
               "elapsed_s": round(time.time() - _T0, 1),
               "results": results}
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"summary: {OUT_DIR / 'summary.json'}")
    print(f"verify_p2_final: exit {code} (device_half={device_half}, "
          f"blocked_by={blocked})")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
