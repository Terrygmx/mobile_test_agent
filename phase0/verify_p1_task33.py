#!/usr/bin/env python3
"""Task 3.3 第 3 步真机验证（12.5 Build Identity / G8 / M4 矩阵 #21 预演）。

plan Task 3.3 step 3：「改源码不重编 metadata → `mta run` 启动即退出码 3」。
本脚本把验证固化成可重跑脚本 + 落盘产物（verify_p1_task23.py 同款纪律：
commit message 里的文字不算证据）。

验证项（每项独立证据，落 out/p1_task33_verify/summary.json）：
  V1 注入构建可达：make p1-build（HEAD 写入 Info.plist）+ simctl install，
     simctl 侧读回 MTA_GIT_COMMIT == git HEAD（App 侧身份可关联）；
  V2 一致路径：metadata 与 App 同 commit → identity 校验通过 → 走到既有
     「真机组件未装配」exit 3，runs.metadata_mismatch=0；
  V3 拦截路径（矩阵 #21）：metadata 副本 build 改 staging（H16：generated
     不手改，用 --metadata 指副本）→ BUILD_METADATA_MISMATCH、未启动用例、
     exit 3，runs.metadata_mismatch=1 / override=0；
  V4 放行路径：V3 + --allow-metadata-mismatch → 校验放行（走到组件未装配），
     runs.metadata_mismatch=1 / override=1（G8 放行必须留痕）。

不依赖 Appium（校验发生在会话建立之前）。运行前置：
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
  python phase0/verify_p1_task33.py   # 内部自行 build + install + 生成 metadata
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "out" / "p1_task33_verify"
BUNDLE = "com.phaset0.logindemo"

results: dict[str, dict] = {}


def _env() -> dict:
    import os
    e = dict(os.environ)
    e.setdefault("DEVELOPER_DIR",
                 "/Applications/Xcode.app/Contents/Developer")
    # 凭据自给（verify_p1_m3.py G3 同款）：lint 先于 identity gate，缺
    # secret 会在 lint 就 exit 3，永远到不了被测的拦截路径。
    e.setdefault("TEST_USERNAME", "admin")
    e.setdefault("TEST_PASSWORD", "secret123")
    return e


def sh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, env=_env())


def booted_udid() -> str:
    r = sh("xcrun", "simctl", "list", "devices", "booted")
    for line in r.stdout.splitlines():
        if "(" in line and "Booted" in line:
            return line.split("(")[1].split(")")[0]
    raise SystemExit("no booted simulator; run `xcrun simctl boot <udid>`")


def git_head() -> str:
    r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                       capture_output=True, text=True, cwd=ROOT)
    return r.stdout.strip()


def mta_run(db: Path, *extra: str) -> subprocess.CompletedProcess:
    return sh(sys.executable, "-m", "cli.main", "run", "--case", "login_001",
              "--suites-root", "suites", "--db", str(db),
              "--bundle-id", BUNDLE, *extra)


def run_row(db: Path) -> tuple:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT status, exit_code, app_git_commit, metadata_git_commit,"
            " metadata_mismatch, metadata_mismatch_override"
            " FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    finally:
        conn.close()


def tc_rows(db: Path) -> list:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT status FROM testcase_runs").fetchall()
    finally:
        conn.close()


def v1_injected_build(udid: str) -> bool:
    """p1-build + install + 读回注入键 == HEAD。"""
    r = sh("make", "p1-build")
    if r.returncode != 0:
        results["V1_injected_build"] = {
            "pass": False, "detail": f"make p1-build failed: {r.stderr[-400:]}"}
        return False
    app = ROOT / "build/p1/Build/Products/Debug-iphonesimulator/LoginDemo.app"
    r = sh("xcrun", "simctl", "install", udid, str(app))
    if r.returncode != 0:
        results["V1_injected_build"] = {
            "pass": False, "detail": f"simctl install failed: {r.stderr[-400:]}"}
        return False
    from source.build_identity import read_app_identity
    ident = read_app_identity(udid, BUNDLE)
    ok = ident.git_commit == git_head() and ident.build == "local"
    results["V1_injected_build"] = {
        "pass": ok,
        "detail": f"app identity={ident.git_commit}/{ident.build}, "
                  f"HEAD={git_head()}"}
    return ok


def v2_matched_path(udid: str, db: Path) -> bool:
    """一致路径：identity 通过 → 组件未装配 exit 3，metadata_mismatch=0。"""
    gen = sh(sys.executable, "-m", "cli.main", "repo", "generate",
             "ios_demo/LoginDemo")
    if gen.returncode != 0:
        results["V2_matched_path"] = {
            "pass": False, "detail": f"repo generate failed: {gen.stderr[-400:]}"}
        return False
    r = mta_run(db, "--udid", udid)
    out = r.stdout + r.stderr
    row = run_row(db)
    ok = (r.returncode == 3 and "未装配" in out
          and row[0] == "ABORTED" and row[4] == 0 and row[5] == 0
          and row[2] == git_head())
    results["V2_matched_path"] = {
        "pass": ok, "detail": f"exit={r.returncode} row={row}"}
    return ok


def v3_mismatch_blocked(udid: str, db: Path, meta_copy: Path) -> bool:
    """矩阵 #21：metadata 与 App build 不一致 → 拦截、未启动用例、exit 3。"""
    r = mta_run(db, "--udid", udid, "--metadata", str(meta_copy))
    out = r.stdout + r.stderr
    row = run_row(db)
    tcs = tc_rows(db)
    ok = (r.returncode == 3 and "BUILD_METADATA_MISMATCH" in out
          and row[0] == "ABORTED" and row[4] == 1 and row[5] == 0
          and tcs == [])
    results["V3_mismatch_blocked"] = {
        "pass": ok, "detail": f"exit={r.returncode} row={row} tcs={tcs}"}
    return ok


def v4_allow_records_override(udid: str, db: Path, meta_copy: Path) -> bool:
    """放行必须留痕：override=1，然后走到既有「组件未装配」前置。"""
    r = mta_run(db, "--udid", udid, "--metadata", str(meta_copy),
                "--allow-metadata-mismatch")
    out = r.stdout + r.stderr
    row = run_row(db)
    ok = (r.returncode == 3 and "未装配" in out
          and row[4] == 1 and row[5] == 1)
    results["V4_allow_records_override"] = {
        "pass": ok, "detail": f"exit={r.returncode} row={row}"}
    return ok


def main() -> int:
    import os
    os.environ.setdefault("DEVELOPER_DIR",
                          "/Applications/Xcode.app/Contents/Developer")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    udid = booted_udid()
    print(f"[env] udid={udid}")

    ok = v1_injected_build(udid)
    if not ok:
        return _finish(1)

    db = OUT_DIR / "trace.db"
    if db.exists():
        db.unlink()
    if not v2_matched_path(udid, db):
        return _finish(1)

    # metadata 副本（H16：generated 不手改——漂移用 --metadata 指副本注入）
    src = ROOT / "repository/generated/local/source_metadata.json"
    meta_copy = OUT_DIR / "metadata_staging.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    data["build"] = "staging"
    meta_copy.write_text(json.dumps(data, ensure_ascii=False),
                         encoding="utf-8")
    if not v3_mismatch_blocked(udid, db, meta_copy):
        return _finish(1)
    if not v4_allow_records_override(udid, db, meta_copy):
        return _finish(1)
    return _finish(0)


def _finish(code: int) -> int:
    summary = OUT_DIR / "summary.json"
    summary.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    for k, v in results.items():
        print(f"{'PASS' if v['pass'] else 'FAIL'} {k}: {v['detail']}")
    print(f"summary: {summary}")
    print(f"verify_p1_task33: exit {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
