#!/usr/bin/env python3
"""Task 2.3 / R13-4：环境管理（11.1 矩阵 + A4 hook）端到端验证脚本。

R13-3 指出的缺口：Task 2.3 的设备验证只在 commit message 里留了文字，
`out/` 下无产物，无法事后复核。本脚本把验证固化成可重跑的脚本 + 落盘产物。

验证项（每项独立证据，落 out/p1_task23/verify_summary.json）：
  V1 RESET_STATE hook：App 写 marker → -UITestReset 启动 → marker 清空
  V2 LOGOUT hook：-UITestLogout 启动 → 日志出现 MTA_LOGOUT_HOOK
  V3 语义差异：LOGOUT 不清 UserDefaults marker，RESET_STATE 清（11.1 粒度差）
  V4 lint 11.2 预检：RESET_STATE 放行 / typo / SNAPSHOT-on-real_device 拦截
  V5 Python 侧矩阵：11.1 声称支持 × ResetExecutor 可执行（跨源一致性）

运行前置：
  export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
  xcodebuild ... -configuration Debug build && 安装到模拟器
  python phase0/verify_p1_task23.py

注意：marker 必须由 **App 自己**写（-UITestWriteMarker）。simctl spawn
defaults write 写的是设备级 domain（data/Library/Preferences/），与 App
容器内 domain（container/Library/Preferences/）不是同一份文件——用它做
写入端会得出「hook 没生效」的错误结论（Task 2.3 曾踩过）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

OUT_DIR = Path("out/p1_task23")
BUNDLE = "com.phaset0.logindemo"
SIMCTL = ["xcrun", "simctl"]

results: dict[str, dict] = {}


def _env() -> dict:
    return {"DEVELOPER_DIR": "/Applications/Xcode.app/Contents/Developer",
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin"}


def sh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, env=_env())


def booted_udid() -> str:
    r = sh("xcrun", "simctl", "list", "devices", "booted")
    for line in r.stdout.splitlines():
        if "(" in line and "Booted" in line:
            return line.split("(")[1].split(")")[0]
    raise SystemExit("no booted simulator; run `xcrun simctl boot <udid>`")


def app_plist(udid: str) -> Path | None:
    r = sh(*SIMCTL, "get_app_container", udid, BUNDLE, "data")
    if r.returncode != 0:
        return None
    return Path(r.stdout.strip()) / "Library/Preferences" / f"{BUNDLE}.plist"


def launch(udid: str, *args: str) -> None:
    sh(*SIMCTL, "terminate", udid, BUNDLE)
    time.sleep(0.6)
    sh(*SIMCTL, "launch", udid, BUNDLE, *args)
    time.sleep(2.5)


def quiesce(udid: str) -> None:
    """终止 App 让 UserDefaults flush 到磁盘。

    必须读盘前先 quiesce：App 运行时 `set()` 只在内存，plist 要等进程
    退出才落盘。R13-4 首次跑 V1 FAIL 就是漏了这一步（探针 bug，非实现 bug）。
    """
    sh(*SIMCTL, "terminate", udid, BUNDLE)
    time.sleep(1.0)


def read_marker(udid: str):
    p = app_plist(udid)
    if p is None or not p.exists():
        return None
    r = sh("plutil", "-extract", "MTA_TEST_MARKER", "raw", "-o", "-", str(p))
    return r.stdout.strip() if r.returncode == 0 else None


def log_has(udid: str, needle: str, last: str = "30s") -> bool:
    # 进程名 = PRODUCT_NAME（LoginDemo），不是 bundle id 末段（logindemo）
    r = sh(*SIMCTL, "spawn", udid, "log", "show", "--last", last,
           "--predicate", 'process == "LoginDemo"')
    return needle in r.stdout


def record(name: str, passed: bool, detail: str) -> None:
    results[name] = {"passed": passed, "detail": detail}
    print(f"{'PASS' if passed else 'FAIL'}  {name}: {detail}")


def main() -> int:
    udid = booted_udid()
    print(f"booted simulator: {udid}\n")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # V1: RESET_STATE 清 marker
    launch(udid, "-UITestWriteMarker")
    quiesce(udid)
    before = read_marker(udid)
    launch(udid, "-UITestReset")
    quiesce(udid)
    after_reset = read_marker(udid)
    record("V1_reset_state_clears_marker",
           before == "BEFORE_RESET" and after_reset is None,
           f"marker before={before!r} after -UITestReset={after_reset!r}")

    # V2: LOGOUT hook 执行
    launch(udid, "-UITestLogout")
    record("V2_logout_hook_executed", log_has(udid, "MTA_LOGOUT_HOOK"),
           "log contains MTA_LOGOUT_HOOK after -UITestLogout launch")

    # V3: 粒度差异——LOGOUT 保留 marker，RESET_STATE 清掉
    launch(udid, "-UITestWriteMarker")
    quiesce(udid)
    pre = read_marker(udid)
    launch(udid, "-UITestLogout")
    quiesce(udid)
    after_logout = read_marker(udid)
    record("V3_logout_preserves_state_reset_state_clears",
           pre == "BEFORE_RESET" and after_logout == "BEFORE_RESET",
           f"LOGOUT keeps marker={after_logout!r} (RESET_STATE cleared it in V1)")

    # V4/V5: Python 侧（lint 11.2 + 跨源一致性），不需要设备
    sys.path.insert(0, ".")
    try:
        from environment.capabilities import CapabilityResolver, RESET_CAPABILITIES
        from environment.reset import ResetExecutor
        from repository.resolver import Repository
        from environment.secrets import EnvSecretProvider
        from testcase.lint import lint

        base = {"schema_version": "0.2", "name": "t",
                "steps": [{"action": "launch_app"}]}
        repo, secrets = Repository(), EnvSecretProvider()

        def reset_issues(reset: str, device: str | None):
            tc = {**base, "id": f"probe_{reset.lower()}",
                  "precondition": {"reset": reset}}
            return [i for i in lint([tc], repo, secrets, device_type=device)
                    if i.code == "unsupported_reset"]

        record("V4_lint_allows_reset_state", not reset_issues("RESET_STATE", "simulator"),
               "RESET_STATE on simulator passes 11.2 preflight")
        record("V4_lint_blocks_typo", bool(reset_issues("RESET_STATEE", None)),
               "typo 'RESET_STATEE' blocked by lint (R5-3 defense)")
        record("V4_lint_blocks_snapshot_on_real_device",
               bool(reset_issues("SNAPSHOT", "real_device")),
               "SNAPSHOT on real_device blocked (11.1 sim-only)")

        # V5 跨源一致性：矩阵声称支持的策略在两端都要能被 ResolveExecutor 接受
        # （SNAPSHOT 矩阵允许但 P1 不实现 → ResetExecutor 显式 NotImplementedError，
        #  这里只验「不会因 matrix 未知而抛 UnsupportedResetError」）
        from session.app_session import UnsupportedResetError

        class _App:
            def reset_state(self, s, **kw): pass
            def launch(self, arguments=None): pass
            def terminate(self): pass

        mismatches = []
        for strategy, devices in RESET_CAPABILITIES.items():
            for dev in devices:
                try:
                    # type: ignore[arg-type] — _App 是探针替身，只验矩阵↔执行
                    # 路径的一致性，不需要真实 AppSession
                    ResetExecutor(_App(), device_type=dev.value).reset(strategy)  # type: ignore[arg-type]
                except UnsupportedResetError as e:
                    mismatches.append(f"{strategy}/{dev.value}: {e}")
                except NotImplementedError:
                    pass  # SNAPSHOT：矩阵允许、实现顺延，显式记账
        record("V5_no_matrix_vs_executor_mismatch", not mismatches,
               "no UnsupportedResetError for matrix-claimed pairs"
               + (f"; mismatches={mismatches}" if mismatches else ""))
    except Exception as e:
        record("V4_V5_python_probes", False, f"{type(e).__name__}: {e}")

    summary = {
        "udid": udid,
        "bundle": BUNDLE,
        "all_passed": all(v["passed"] for v in results.values()),
        "results": results,
    }
    (OUT_DIR / "verify_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\n{'ALL PASS' if summary['all_passed'] else 'SOME FAILED'} "
          f"→ {OUT_DIR / 'verify_summary.json'}")
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())