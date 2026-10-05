#!/usr/bin/env python3
"""Gate M4 真机验证（Task 4.2 / P2-10 载体）——设计 18 节步骤 9–10。

  G1. P2 矩阵 #13/#14 pytest 实跑（FakeDriver 那一半，
      tests/fault_injection/test_p2_matrix_m4.py）；
  G2. 漂移现场重建（沿用 Gate M2 的 F5 方法：username_field → user_field
      真实改名重编译重装）+ 首跑 RECOVERED_LLM + accept → Candidate；
  G3. **9.5 人工越权路径 promote**（设计 18 步骤 9）：Candidate 尚无 10
      样本——M3 Gate 已验证样本升级路径，此处走 manual_override（理由
      留痕）→ 写 overrides + git commit；
  G4. 再跑同用例：**exit 0 / PASS / recoveries 空 / LLM 0**——promoted
      策略进 Locator 链，正常 find() 命中，不再进 Recovery（Gate M4 头条）；
  G5. `git revert` promotion commit（设计 18 步骤 10）→ 再跑：Repository
      回退（正常 find 失败）→ **Experience HIT / RECOVERED_EXPERIENCE /
      LLM 0**——Store 历史不受 revert 影响（9.6/E10 的真机形态）；
  G6. P1 行为保留：空库 + --no-llm 同场景仍 FAIL / exit 1。

流程：make repo-generate → 改名 build install 重扫描 → G2 首跑+accept →
G3 promote（proj git 仓库：generated + overrides 都入库）→ G4 run →
G5 revert + run → G6 空库 run → restore（源码还原重编译重装重扫描）。

前置：booted 模拟器、Appium 4723、LLM 网关。产出 out/p2_m4_gate/summary.json；
exit 0 = Gate M4 真机判据全绿。
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

OUT_DIR = ROOT / "out" / "p2_m4_gate"
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
    ok &= check("PF_appium_4723",
                _probe("http://127.0.0.1:4723/status", 4),
                "Appium status 200")
    base = _env()["LLM_BASE_URL"].rstrip("/")
    ok &= check("PF_llm_gateway", _probe(f"{base}/models", 5),
                f"LLM_BASE_URL={_env()['LLM_BASE_URL']}")
    return ok


def g1_matrix_pytest() -> bool:
    base = OUT_DIR / "_pytest_tmp"
    proc = sh(PY, "-m", "pytest",
              "tests/fault_injection/test_p2_matrix_m4.py",
              "-p", "no:cacheprovider", "--basetemp", str(base), timeout=600)
    tail = (proc.stdout.strip().splitlines() or ["<no stdout>"])[-1]
    return check("G1_p2_matrix_13_14", proc.returncode == 0,
                 f"exit={proc.returncode} {tail[:120]}")


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


def rename_and_build(gen_root: Path) -> bool:
    global _SOURCE_TOUCHED
    src = APP_SOURCE.read_text(encoding="utf-8")
    if NEW_ID in src and OLD_ID not in src:
        pass
    elif OLD_ID not in src:
        print(f"PREFLIGHT ERROR: {OLD_ID} 不在源码中")
        return False
    else:
        APP_SOURCE.write_text(src.replace(f'"{OLD_ID}"', f'"{NEW_ID}"'),
                              encoding="utf-8")
        _SOURCE_TOUCHED = True
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
    return NEW_ID in ids and OLD_ID not in ids


def restore() -> None:
    if not _SOURCE_TOUCHED:
        print("restore: 本次未改源码，无需还原")
        return
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
id: drift_p2
name: Gate M4 promotion（username_field 真实改名）
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
      timeout: 20
  - assertion:
      target: ProfileView.logout_button
      condition: exists
      timeout: 5
"""

# 与 Gate M2 同一款别名文档：LLM 候选 user_field 经人工确认落 overrides
# （含风险声明；无此声明源扫描 risk=None → Guard fail-closed）。
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
               GIT_AUTHOR_NAME="gate_m4", GIT_AUTHOR_EMAIL="m4@gate",
               GIT_COMMITTER_NAME="gate_m4", GIT_COMMITTER_EMAIL="m4@gate")
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, env=env,
                          timeout=60)


def _scaffold_proj(tmp: Path) -> Path:
    """proj = git 仓库根：generated + overrides 入库（promote/revert 的
    操作对象），trace/exp/state 不入库。"""
    proj = tmp / "proj"
    (proj / "suites").mkdir(parents=True, exist_ok=True)
    (proj / "suites" / "drift_p2.yaml").write_text(DRIFT_CASE,
                                                   encoding="utf-8")
    (proj / ".gitignore").write_text("*.db\nstate/\n_pytest/\n",
                                     encoding="utf-8")
    return proj


def _prepare_repo(proj: Path, gen_root: Path) -> Path:
    """overrides 副本 + user_field 别名（H15：工具只导出，合入是本 Gate 的
    人工 ACCEPT 动作本身）；generated 复制进 proj 并首次提交。"""
    ov = proj / "repository" / "overrides"
    if ov.exists():
        shutil.rmtree(ov)
    shutil.copytree(ROOT / "repository" / "overrides", ov)
    lv = ov / "elements" / "LoginView.yaml"
    text = lv.read_text(encoding="utf-8")
    if "id: user_field" not in text:
        lv.write_text(text + ALIAS_DOC, encoding="utf-8")
    gen_dst = proj / "generated"
    if gen_dst.exists():
        shutil.rmtree(gen_dst)
    shutil.copytree(gen_root, gen_dst)
    # ⚠️ 必须认 **proj 自己的 .git**，不能拿 rev-parse 判断——proj 在主
    # 仓库工作树里（out/），rev-parse 会穿透到上层主仓库，随后 add -A
    # 就把改名源码提交进主仓库了（M4 Gate 首跑实锤，reset --hard 才救回）。
    if not (proj / ".git").exists():
        _git(proj, "init", "-q")
    toplevel = _git(proj, "rev-parse", "--show-toplevel").stdout.strip()
    if Path(toplevel) != proj:
        raise RuntimeError(
            f"gate proj 仓库根漂移：{toplevel} != {proj}——中止，"
            f"绝不操作主仓库")
    _git(proj, "config", "user.email", "m4@gate")
    _git(proj, "config", "user.name", "gate_m4")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-qm", "baseline: generated + overrides(alias)")
    return ov


def mta_run(proj: Path, db: Path, exp_db: Path, ov: Path,
            *extra: str) -> subprocess.CompletedProcess:
    return sh(PY, "-m", "cli.main", "run", "--case", "drift_p2",
              "--suites-root", str(proj / "suites"), "--db", str(db),
              "--udid", UDID, "--bundle-id", BUNDLE,
              "--metadata", str(proj / "generated" / "local"
                                / "source_metadata.json"),
              "--generated", str(proj / "generated" / "local"),
              "--overrides", str(ov),
              "--exp-db", str(exp_db),
              *extra, timeout=900)


def _db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def trace_facts(db: Path) -> dict:
    conn = _db(db)
    try:
        run = conn.execute(
            "SELECT run_id, exit_code, status, llm_calls FROM runs"
            " ORDER BY rowid DESC LIMIT 1").fetchone()
        tc = conn.execute(
            "SELECT status, detail_json FROM testcase_runs"
            " ORDER BY rowid DESC LIMIT 1").fetchone()
        recoveries = [dict(r) for r in conn.execute(
            "SELECT kind, candidate_target FROM recoveries ORDER BY id"
        ).fetchall()]
        detail = {}
        if tc and tc["detail_json"]:
            try:
                detail = json.loads(tc["detail_json"])
            except Exception:
                detail = {}
        reviews = [dict(r) for r in conn.execute(
            "SELECT id, review_status FROM recovery_reviews ORDER BY id"
        ).fetchall()]
        return {"exit_code": run["exit_code"] if run else None,
                "llm_calls": run["llm_calls"] if run else None,
                "tc_status": tc["status"] if tc else None,
                "recovered_kinds": detail.get("recovered_kinds"),
                "recoveries": recoveries, "reviews": reviews}
    finally:
        conn.close()


def exp_facts(exp_db: Path) -> dict:
    conn = _db(exp_db)
    try:
        exps = [dict(r) for r in conn.execute(
            "SELECT experience_id, status, promoted, promoted_commit,"
            " sample_count FROM experiences ORDER BY rowid").fetchall()]
        return {"experiences": exps}
    finally:
        conn.close()


def g2_first_run_and_accept(proj: Path, exp_db: Path) -> tuple[bool,
                                                               str | None]:
    """G2：首跑 RECOVERED_LLM + accept → Candidate（E5 学习入口）。

    provider 级错误重试（与 Gate M2 同款）：网关后面是推理模型，单次
    completion 偶发越过 budget 超时——只对 LLM_PROVIDER_ERROR 重试，
    校验链拒绝是确定性语义，重试只会掩盖真问题。
    """
    db = proj / "state" / "trace_g2.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.unlink(missing_ok=True)
    f: dict = {}
    r = None
    for attempt in range(1, 5):
        r = mta_run(proj, db, exp_db, proj / "repository" / "overrides")
        f = trace_facts(db)
        print(f"  G2 attempt {attempt}: exit={r.returncode} facts={f}")
        conn = _db(db)
        try:
            provider_errors = conn.execute(
                "SELECT COUNT(*) FROM steps WHERE failure_type="
                "'LLM_PROVIDER_ERROR'").fetchone()[0]
            # 环境级抖动的第二种形态（实测 5 轮 3 次）：恢复已成功，
            # 但恢复后的屏幕跳转等待超时——冷启动模拟器上 go_profile 后
            # ProfileView 偶发不出现。可重试；若 4 轮全失败仍会 FAIL。
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
        print(f"  G2 attempt {attempt}: 环境抖动"
              f"（provider_err={provider_errors} "
              f"recovered={recovered} wait_timeout={wait_timeout}）→ 重试")
        db.unlink(missing_ok=True)
    ok = check(
        "G2_first_run_llm_recovers",
        bool(r) and r.returncode == 5 and f["exit_code"] == 5
        and f["tc_status"] == "RECOVERED"
        and f["recovered_kinds"] == ["RECOVERED_LLM"]
        and len(f["reviews"]) == 1
        and f["reviews"][0]["review_status"] == "PENDING",
        f"exit={r.returncode if r else None} tc={f['tc_status']} "
        f"kinds={f['recovered_kinds']}")
    if not (ok and f["reviews"]):
        return False, None
    review_id = f["reviews"][0]["id"]
    r = sh(PY, "-m", "cli.main", "review", "accept", str(review_id),
           "--db", str(db), "--exp-db", str(exp_db),
           "--reviewer", "gate_m4", "--out",
           str(proj / "state" / "patch.yaml"))
    exps = exp_facts(exp_db)["experiences"]
    ok2 = check(
        "G2_accept_creates_candidate",
        r.returncode == 0 and len(exps) == 1
        and exps[0]["status"] == "CANDIDATE",
        f"exit={r.returncode} experiences={exps}")
    if not ok2:
        return False, None
    return True, exps[0]["experience_id"]


def g3_promote_manual(proj: Path, exp_db: Path, exp_id: str) -> tuple[bool,
                                                                      str]:
    """G3：设计 18 步骤 9 的 promote——走 CLI 两段式 + 9.5 人工路径
    （Candidate 尚无 10 样本；样本升级路径已由 Gate M3 验证）。"""
    r = sh(PY, "-m", "cli.main", "experience", "promote", exp_id,
           "--exp-db", str(exp_db), "--manual-override",
           "--reason", "Gate M4：真机演示（漂移恢复经人工确认，9.5）",
           "--operator", "gate_m4")
    out = r.stdout
    if r.returncode != 0 or "PENDING" not in out:
        check("G3_promote_generate", False, f"exit={r.returncode} {out[-200:]}")
        return False, ""
    m = re.search(r"prop_[0-9a-f]{12}", out)
    if not m:
        check("G3_promote_generate", False, f"no proposal id in: {out[:200]}")
        return False, ""
    proposal_id = m.group(0)   # 不能按空格切列——首行是 "experience
    # promote: proposal prop_x …"，split()[1] 取到的是 "promote:"（实测踩过）
    r = sh(PY, "-m", "cli.main", "experience", "promote", exp_id,
           "--exp-db", str(exp_db), "--approve", proposal_id,
           "--repo-root", str(proj), "--operator", "gate_m4")
    m_sha = re.search(r"[0-9a-f]{40}", r.stdout)
    sha = m_sha.group(0) if m_sha else ""   # sha 后紧跟全角"；"无空格，
    # 按空白切列取不到 40 位 token（实测踩过）——直接正则抓。
    ov_file = (proj / "repository" / "overrides" / "elements"
               / "LoginView.yaml")
    exps = exp_facts(exp_db)["experiences"]
    ok = check(
        "G3_promote_writes_overrides_and_commits",
        r.returncode == 0 and "APPROVED" in r.stdout and sha
        and ov_file.exists() and "origin: experience" in
        ov_file.read_text(encoding="utf-8")
        and exps[0]["promoted"] == 1 and exps[0]["promoted_commit"] == sha,
        f"exit={r.returncode} sha={sha[:10]} "
        f"promoted={exps[0]['promoted']}")
    return ok, sha


def g4_promoted_run_normal_find(proj: Path, exp_db: Path) -> bool:
    """G4：promote 后正常 find() 命中——exit 0 / PASS / 无 recovery。"""
    db = proj / "state" / "trace_g4.db"
    db.unlink(missing_ok=True)
    r = mta_run(proj, db, exp_db, proj / "repository" / "overrides")
    f = trace_facts(db)
    print(f"  G4 run: exit={r.returncode} facts={f}")
    return check(
        "G4_promoted_run_passes_without_recovery",
        r.returncode == 0 and f["exit_code"] == 0
        and f["tc_status"] == "PASS" and f["recoveries"] == []
        and f["llm_calls"] == 0,
        f"exit={r.returncode} tc={f['tc_status']} "
        f"recoveries={f['recoveries']} llm={f['llm_calls']}（Gate M4 头条："
        f"正常 Locator，不再进 Recovery）")


def g5_revert_and_rerun(proj: Path, exp_db: Path, sha: str) -> bool:
    """G5：设计 18 步骤 10——revert 后 Repository 回退、Store 历史不受
    影响（recovery 走 Experience HIT 而非 LLM 即为真机形态的证明）。"""
    r = _git(proj, "revert", "--no-edit", sha)
    if r.returncode != 0:
        return check("G5_git_revert", False, r.stderr[:200])
    ov_file = (proj / "repository" / "overrides" / "elements"
               / "LoginView.yaml")
    text = ov_file.read_text(encoding="utf-8")
    reverted = "origin: experience" not in text
    exps = exp_facts(exp_db)["experiences"]

    db = proj / "state" / "trace_g5.db"
    db.unlink(missing_ok=True)
    r = mta_run(proj, db, exp_db, proj / "repository" / "overrides")
    f = trace_facts(db)
    print(f"  G5 run: exit={r.returncode} facts={f}")
    return check(
        "G5_revert_restores_repo_store_intact",
        reverted and r.returncode == 5 and f["exit_code"] == 5
        and f["tc_status"] == "RECOVERED"
        and f["recovered_kinds"] == ["RECOVERED_EXPERIENCE"]
        and f["llm_calls"] == 0
        and exps[0]["promoted"] == 1 and exps[0]["promoted_commit"] == sha,
        f"reverted={reverted} exit={r.returncode} "
        f"kinds={f['recovered_kinds']} llm={f['llm_calls']} "
        f"promoted={exps[0]['promoted']}（Store 记账是 revert 改不掉的事实）"
        "；注：revert 后的每个后续 run 会稳定多付一次失败 find + 走恢复"
        "路径记样本，直到重新 promote——这是预期稳态，不是回归（基线报告"
        "解读用）")


def g6_empty_store_no_llm(proj: Path) -> bool:
    """G6：P1 行为保留——空库 + --no-llm 同场景仍 FAIL / exit 1。"""
    db = proj / "state" / "trace_g6.db"
    empty = proj / "state" / "empty_experience.db"
    db.unlink(missing_ok=True)
    empty.unlink(missing_ok=True)
    r = mta_run(proj, db, empty, proj / "repository" / "overrides",
                "--no-llm")
    f = trace_facts(db)
    print(f"  G6 run: exit={r.returncode} facts={f}")
    return check(
        "G6_empty_store_no_llm_keeps_p1",
        r.returncode == 1 and f["exit_code"] == 1
        and f["tc_status"] == "FAIL" and f["llm_calls"] == 0,
        f"exit={r.returncode} tc={f['tc_status']}")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pre_ok = preflight()
    g1_ok = g1_matrix_pytest()
    tmp = OUT_DIR / "gate_env"
    tmp.mkdir(parents=True, exist_ok=True)
    device_ok = pre_ok
    exp_db = tmp / "proj" / "state" / "experience.db"
    if device_ok:
        # 每轮**全清现场**：上一轮 accept 留下的 Candidate 会让 G2 直接
        # Experience 命中（store 跨 run 生效本身是对的，但 Gate 的 G2
        # 要验证的是「LLM 学习入口」，现场必须是全新的）。
        if tmp.exists():
            shutil.rmtree(tmp / "proj", ignore_errors=True)
        ensure_original_source()
        gen_root = tmp / "generated_scan"
        device_ok = check(
            "G0_drift_build",
            make("repo-generate") and rename_and_build(gen_root),
            "基线重扫描 → 真实改名重编译安装 → 重扫描登记新 id")
        try:
            if device_ok:
                proj = _scaffold_proj(tmp)
                ov = _prepare_repo(proj, gen_root)
                device_ok, exp_id = g2_first_run_and_accept(proj, exp_db)
                if device_ok and exp_id:
                    device_ok, sha = g3_promote_manual(proj, exp_db, exp_id)
                if device_ok and sha:
                    device_ok = g4_promoted_run_normal_find(proj, exp_db)
                if device_ok and sha:
                    device_ok = g5_revert_and_rerun(proj, exp_db, sha)
                if device_ok:
                    device_ok = g6_empty_store_no_llm(proj)
        finally:
            restore()
    ok = pre_ok and g1_ok and device_ok
    if device_ok:
        device_half = "PASS"
    elif not pre_ok:
        device_half = "SKIPPED_ENV"
    else:
        device_half = "FAIL"
    code = 0 if ok else 1
    blocked = [k for k, v in results.items()
               if k.startswith("PF_") and not v["pass"]]
    summary = {"verdict": "PASS" if code == 0 else "FAIL",
               "gate": "M4", "udid": UDID,
               "g1_matrix_pytest": "PASS" if g1_ok else "FAIL",
               "device_half": device_half, "blocked_by": blocked,
               "elapsed_s": round(time.time() - _T0, 1),
               "results": results}
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"summary: {OUT_DIR / 'summary.json'}")
    print(f"verify_p2_m4: exit {code} (device_half={device_half}, "
          f"blocked_by={blocked})")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
