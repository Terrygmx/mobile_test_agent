#!/usr/bin/env python3
"""M1 Gate 验收脚本（Task 1.6 / P1-04）：5 条 schema 0.2 用例在最小 Executor 上全绿。

流程（复用 P0 verify_stage 脚本模式）：
  1. 环境自检：Appium ready、模拟器 booted（R9-2：MTA_SIM_UDID / 自动发现）；
  2. 建链：DeviceSession → Executor/AppSession → Repository(from_dirs overrides)
     → TestcaseRunner(repository=repo)（target 走 4.1 语义解析）；
  3. 逐条 load_testcase（strict 0.2）→ lint 先过 → runner.run；
  4. 结果判定：SQLite runs 表状态 + ElementTree 精确断言终态 marker
     （R9-3：name 属性精确匹配 + visible 属性过滤，非裸子串）。
产出：out/p1_m1_gate/gate_summary.json；exit 0 = 5/5 PASS。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executor.wait import WaitConfig
from repository.resolver import Repository
from runner.testcase_runner import TestFailure, TestcaseRunner
from testcase.loader import load_testcase
from testcase.lint import lint, max_severity
from testcase.schema import TestCase  # noqa: F401  strict 模型加载由 loader 分发

BUNDLE_ID = "com.phaset0.logindemo"
APPIUM_URL = "http://127.0.0.1:4723"
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "out" / "p1_m1_gate"
SUITES = ROOT / "suites" / "smoke"

CASES = ["login_001", "logout_001", "search_001", "open_detail_001", "profile_001"]


def resolve_udid() -> str:
    """R9-2 同款：MTA_SIM_UDID 优先 → booted 自动发现 → 可读报错。

    R18-2 修复（与 verify_p1_m2 同）：subprocess 显式带 DEVELOPER_DIR——
    Xcode 27 beta 下 `xcrun` 无该变量报「unable to find utility simctl」，
    表现为误导性的 "no booted simulator"。
    """
    env = os.environ.get("MTA_SIM_UDID")
    if env:
        return env
    child_env = dict(os.environ)
    child_env.setdefault(
        "DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    out = subprocess.run(
        ["xcrun", "simctl", "list", "devices", "booted"],
        capture_output=True, text=True, env=child_env,
    ).stdout
    booted = re.findall(r"\(([0-9A-Fa-f-]{36})\)", out)
    if not booted:
        raise SystemExit(
            "no booted simulator: xcrun simctl boot <UDID> or set MTA_SIM_UDID")
    return booted[0]


def appium_ready() -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"{APPIUM_URL}/status", timeout=3) as r:
            return b"ready" in r.read()
    except Exception:
        return False


def marker_visible_exact(page_source: str, screen_id: str) -> bool:
    """R9-3：ElementTree 精确断言——name 属性 == 'screen.<id>' 且 visible != false，
    不用裸子串（screen.LoginView 不会误匹配 screen.LoginView2）。"""
    try:
        root = ET.fromstring(page_source)
    except ET.ParseError:
        return False
    for el in root.iter():
        if el.get("name") == f"screen.{screen_id}" and el.get("visible", "true") != "false":
            return True
    return False


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not appium_ready():
        print(f"Appium not ready at {APPIUM_URL}; start: appium --port 4723")
        return 2
    udid = resolve_udid()
    print(f"[env] appium ready, udid={udid}")

    from environment.secrets import EnvSecretProvider
    from executor.executor import Executor
    from repository.resolver import Severity
    from runner.testcase_runner import TestFailure  # noqa: F401
    from session.app_session import AppSession
    from session.device_session import DeviceSession
    from tracer.recorder import Recorder
    # TODO(M3, R10-5.2)：overrides 目录当前被当作 generated_root 传入——M1
    # overrides-only 下功能等价；M3 接入 generated 后这里必须改为
    # generated_root=<generated目录> + overrides_root=<overrides目录>，
    # generated 与 override 的解析语义不同（5.3 mode 合并）。
    repo = Repository.from_dirs(generated_root=str(ROOT / "repository" / "overrides"))
    secrets = EnvSecretProvider()

    caps = {
        "platform_name": "iOS",
        "automation_name": "XCUITest",
        "device_name": "iPhone 14",
        "udid": udid,
        "bundle_id": BUNDLE_ID,
        "no_reset": True,
        "new_command_timeout": 120,
    }
    rec = Recorder(OUT_DIR / "trace.db")
    # R11-1：3 轮实测 `wait_for screen active → tap` 会被 SwiftUI 转场吞掉 tap
    # （HomeView 成立后紧跟的 go_profile tap 丢失，ProfileView 永不出现，2/3 轮挂）。
    # settle_on_screen_wait=True 让 active 之后补树静止判定——M3 gesture-ready
    # 信号落地后应能关掉，届时恢复 7.3 的纯廉价路径。
    # stable_polls=3 × stable_interval=0.25 ≈ 0.5s 下限：SwiftUI push/pop 转场实测
    # 0.35~0.45s，默认的 2×0.2 偏紧。
    wait_cfg = WaitConfig(settle_on_screen_wait=True,
                          stable_polls=3, stable_interval=0.25)
    # R10-5.3：run_id 带时间戳，避免 trace.db 跨次运行累积后按 run_id 查出多批
    run_id = f"p1_m1_gate_{time.strftime('%Y%m%d_%H%M%S')}"
    ds = DeviceSession(APPIUM_URL, caps, recorder=rec, run_id=run_id)
    ds.connect()
    ex = Executor(ds)
    app = AppSession(ds, BUNDLE_ID)
    runner = TestcaseRunner(ex, app, rec, secrets, repository=repo, wait_config=wait_cfg)

    results: dict[str, dict] = {}
    for case_id in CASES:
        path = SUITES / f"{case_id}.yaml"
        print(f"[case] {case_id} ...")
        tc = load_testcase(path)  # strict 0.2 分发
        issues = lint([tc], repo, secrets)
        # R10-5.1：Severity enum 用 is 比较（R7-3 同款，不做 stringly 比较）
        errors = [i for i in issues if i.severity is Severity.ERROR]
        if errors:
            results[case_id] = {"passed": False, "stage": "lint",
                                "errors": [f"{i.code}: {i.message}" for i in errors]}
            print(f"    lint FAILED ({len(errors)} errors)")
            continue
        try:
            runner.run(tc)
            status = "PASS"
        except TestFailure as e:
            status = "FAIL"
            results[case_id] = {"passed": False, "stage": "run", "error": str(e)}
            print(f"    run FAILED: {e}")
            continue
        except Exception as e:  # noqa: BLE001 — InfraError 等也记 FAIL 后继续下一用例
            status = "INFRA_FAILURE"
            results[case_id] = {"passed": False, "stage": "run",
                                "error": f"{type(e).__name__}: {e}"}
            print(f"    infra FAILURE: {type(e).__name__}: {e}")
            continue

        # 终态 marker 精确断言（用例 cleanup 后应停在的关键屏）
        final_screen = {"login_001": "HomeView", "logout_001": "LoginView",
                        "search_001": "SearchView", "open_detail_001": "DetailView",
                        "profile_001": "ProfileView"}[case_id]
        src = ex.page_source()
        (OUT_DIR / f"page_source_{case_id}.xml").write_text(src)
        marker_ok = marker_visible_exact(src, final_screen)
        results[case_id] = {"passed": status == "PASS" and marker_ok,
                            "run_status": status,
                            f"marker:{final_screen}": marker_ok}
        print(f"    {status}, marker {final_screen}: {marker_ok}")

    verdict = all(r.get("passed") for r in results.values()) and len(results) == len(CASES)
    summary = {"verdict": verdict, "cases": results}
    (OUT_DIR / "gate_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
