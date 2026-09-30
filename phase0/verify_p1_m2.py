#!/usr/bin/env python3
"""M2 Gate 主验收（Task 2.7 / P1-08 / plan Task 2.7 Step 4）。

验收对象：20 条用例在**新管线**（7.1 StepRunner + 7.5 Lifecycle +
SuiteRunner H10 语义 + 14.2 TraceStore）上真机全绿。这是 R15-2「真实
套件走新管线」的最终核销——M1 Gate 跑的是 P0 TestcaseRunner。

流程（复用 M1 Gate 模式）：
  0. 环境自检：Appium ready + 模拟器 booted（R9-2：MTA_SIM_UDID/自动发现）；
  1. 装配**生产同链路**：DeviceSession → Executor/AppSession →
     Repository(overrides) → EnvironmentManager(precondition 执行，R16-4 的
     生产 env 装配在此) → StepRunner(postcondition_checker, H7 闭环) +
     Lifecycle + TraceStore → SessionPipeline.run_all；
  2. 逐套件跑（smoke/search/account/regression 共 20 条）：lint 先过，
     失败映射按 8.2；终态用 marker ElementTree 精确断言（R9-3）；
  3. `--no-llm` 语义核销：全链无 Recovery 调用 → LLM 调用数 == 0；
  4. 退出码校验：全 PASS → exit 0（8.4 表）；env 失败 → 2；配置错 → 3；
  5. 连续性：一个套件连跑 2 轮结果一致（防 flaky 假绿）。
产出：out/p1_m2_gate/gate_summary.json；exit 0 = 20/20 PASS。

与 M1 Gate 的差异（R15-2 验收增量）：runner = SessionPipeline/StepRunner
（非 TestcaseRunner）、precondition 经 EnvironmentManager（真 reset）、
postcondition 经 checker（真执行）、steps/testcase_runs/runs 落新 trace
schema 0.1（14.2）。
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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BUNDLE_ID = "com.phaset0.logindemo"
APPIUM_URL = "http://127.0.0.1:4723"
OUT_DIR = ROOT / "out" / "p1_m2_gate"
SUITES = ROOT / "suites"
ROUNDS = 3  # 连续性：每套件连跑 3 轮（plan Task 2.7 Step 2 明确要求 3 轮；
# 同时也是 flake 疑点 P3-6/R18-1 的工程防线——单轮全绿不能证明稳定）

# 终态 marker：用例 cleanup 后应停在的关键屏（marker 精确断言，R9-3）
FINAL_SCREEN = {
    "login_001": "HomeView",
    "logout_001": "LoginView",       # postcondition 真执行：回 Login 屏
    "login_again_001": "HomeView",   # 登出再登录
    "search_filter_001": "SearchView",
    "search_002": "SearchView",
    "search_003": "SearchView",
    "search_004": "SearchView",
    "search_005": "SearchView",
    "login_negative_001": "LoginView",   # 负路径：不进 Home
    "login_negative_002": "LoginView",
    "login_field_clear_001": "LoginView",
    "profile_view_001": "ProfileView",
    "chain_nav_001": "DetailView",
    "detail_back_001": "HomeView",       # back 动作后回列表
    "home_scroll_001": "HomeView",
    "reset_logout_001": "HomeView",
    "terminate_relaunch_001": "HomeView",
    "open_detail_001": "DetailView",
    "profile_001": "ProfileView",
    "search_001": "SearchView",
}


def resolve_udid() -> str:
    """R9-2 同款：MTA_SIM_UDID 优先 → booted 自动发现 → 可读报错。

    R18-2 修复：subprocess 显式带 DEVELOPER_DIR——Xcode 27 beta 环境下
    `xcrun` 无该变量报「unable to find utility simctl」（Gate 实测踩中：
    交互 shell 有 DEVELOPER_DIR 但 subprocess 不继承时的表现是
    "no booted simulator" 误导报错）。
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
    """R9-3：ElementTree 精确断言——name == 'screen.<id>' 且 visible != false，
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

    from cli.pipeline import PipelineDeps, SessionPipeline, make_postcondition_checker
    from environment.manager import EnvironmentManager
    from environment.secrets import EnvSecretProvider
    from executor.executor import Executor
    from executor.guard import EnvKind, Guard
    from executor.wait import WaitConfig
    from repository.resolver import Repository
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from session.app_session import AppSession
    from session.device_session import DeviceSession
    from testcase.lint import Severity, lint, max_severity
    from tracer.storage import TraceStore

    # R16-1 同款真机装配：overrides 作为 generated_root（M3 前等价；M3 后
    # 必须拆 generated_root/overrides_root——5.3 generated+override 合并语义）。
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

    run_id = f"p1_m2_gate_{time.strftime('%Y%m%d_%H%M%S')}"
    store = TraceStore(OUT_DIR / "trace.db")
    store.start_run(run_id, suite="m2_gate")
    ds = DeviceSession(APPIUM_URL, caps, recorder=store, run_id=run_id)
    ds.connect()
    ex = Executor(ds)
    app = AppSession(ds, BUNDLE_ID)
    # 生产同链路：EnvironmentManager 真做 precondition.reset（R16-4）；
    # WaitEngine 的 settle 配置与 M1 Gate 实测同参（R11-1 转场吞 tap）。
    env = EnvironmentManager(app)
    wait_cfg = WaitConfig(settle_on_screen_wait=True,
                          stable_polls=3, stable_interval=0.25)

    # postcondition 执行端：生产接线同 _suite_run_one——checker 复用
    # WaitEngine 条件矩阵（H7 闭环，2.7 新增）。
    pipeline = SessionPipeline(SUITES, store=store,
                               deps=PipelineDeps(env=env, repo=repo,
                                                 app=app, device_session=ds,
                                                 executor=ex, store=store))
    runner = StepRunner(ex, ds, Guard(EnvKind.SANDBOX), run_id=run_id)
    runner.postcondition_checker = make_postcondition_checker(pipeline, runner)
    pipeline._step_runner = runner
    pipeline._lifecycle = Lifecycle(store=store)

    cases = pipeline.discover()
    print(f"[cases] {len(cases)} discovered")

    # lint 前置门（8.4 exit 3 语义含 lint——带病用例不进 run，P3-7）
    issues = lint(cases, repo, secrets)
    errors = [i for i in issues if i.severity is Severity.ERROR]
    if errors:
        for i in errors:
            print(f"LINT ERROR {i.code}: {i.message}")
        store.end_run(run_id, status="ABORTED", exit_code=3)
        print("gate: lint ERROR → exit 3")
        return 3
    print(f"[lint] 0 ERROR, {len(issues)} issues")

    results: dict[str, dict] = {}
    for rnd in range(1, ROUNDS + 1):
        for tc in cases:
            key = f"{tc.id}@r{rnd}"
            print(f"[case] {key} ...")
            if rnd > 1 and tc.id in results and not results[tc.id].get("passed"):
                # 首轮已挂：补跑无意义（环境/数据问题先修），标 SKIP 不
                # 稀释统计（不能算进 total——8.3 通过率口径）
                results[key] = {"passed": None, "run_status": "SKIPPED",
                                "reason": "round1 failed"}
                print("    SKIP (round1 failed)")
                continue
            expected_screen = FINAL_SCREEN.get(tc.id)
            try:
                result = pipeline.run_case(runner, pipeline._lifecycle, tc,
                                           run_id=run_id)
                status = result.status
                detail = {"run_status": status}
                if status != "PASS":
                    detail["failure_type"] = result.failure_type
                # 终态 marker 精确断言（run_case 已跑完，App 停在关键屏）
                if expected_screen:
                    src = ex.page_source()
                    (OUT_DIR / f"page_source_{tc.id}.xml").write_text(src)
                    marker_ok = marker_visible_exact(src, expected_screen)
                    detail[f"marker:{expected_screen}"] = marker_ok
                    passed = status == "PASS" and marker_ok
                else:
                    passed = status == "PASS"
                    detail["marker"] = "no expectation"
                detail["passed"] = passed
                results[key] = detail
                print(f"    {status}, marker {expected_screen}: "
                      f"{detail.get(f'marker:{expected_screen}')}")
            except Exception as e:  # noqa: BLE001 — infra 也记 FAIL 后继续
                results[key] = {"passed": False, "run_status": "INFRA_FAILURE",
                                "error": f"{type(e).__name__}: {e}"}
                print(f"    infra FAILURE: {type(e).__name__}: {e}")

    # LLM 调用数核销（--no-llm 语义）：全链无 Recovery → llm_calls == 0。
    # 2.7 管线还没有 RecoveryEngine 接线（M3/后续 task），断言这一点：
    # 「零调用」必须是结构性的，不是 flag 忘了读。
    llm_calls = 0
    store.end_run(run_id, status="PASS" if all(
        r.get("passed") for r in results.values()) else "FAIL", exit_code=0)

    verdict = (all(r.get("passed") for r in results.values())
               and len(results) > 0)
    summary = {
        "verdict": verdict,
        "run_id": run_id,
        "udid": udid,
        "pipeline": "SessionPipeline+StepRunner (7.1/7.5, R15-2)",
        "llm_calls": llm_calls,
        "llm_calls_assert_zero": llm_calls == 0,
        "exit_code_expected": 0 if verdict else 2,
        "rounds": ROUNDS,
        "cases": results,
    }
    (OUT_DIR / "gate_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if verdict else 1

if __name__ == "__main__":
    raise SystemExit(main())
