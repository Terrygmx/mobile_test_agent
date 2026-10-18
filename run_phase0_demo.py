"""Phase 0 端到端 Demo（Stage 9）——当前唯一执行管线版。

场景（2026-10-18 修订，与 `phase0/verify_p3_final.py` 的漂移法同款）：
把 `username_field` 在 App 源码里改名 `user_field` 后重编译重装，而
metadata 仍只认识旧 id。此时再跑登录用例的第一步就会：

  input(username_field) → ElementNotFound → Local Reconciliation 判 DRIFT
  → RecoveryEngine（真 LLM 或 --stub）→ 唯一性/类型/风险校验
  → 重发动作 → 用例 RECOVERED

漂移前的源码改名/重编译请自行执行（改 ios_demo/LoginDemo/LoginDemoApp.swift
的 `username_field` → `user_field`，再 `make p1-build` + `make p1-install`）；
本脚本只负责「App 已是新、metadata 仍是旧」这一段，并在退出时报告是否需要
还原源码。

需要环境变量：LLM_API_KEY（+ 可选 LLM_BASE_URL / LLM_MODEL）。
无 LLM key 时用 `--stub` 跑通全流程（stub 候选 = 运行时的 `user_field`）。
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from agent.recovery import RecoveryEngine
from cli.pipeline import PipelineDeps, SessionPipeline
from environment.manager import EnvironmentManager
from environment.secrets import EnvSecretProvider
from executor.executor import Executor
from executor.guard import EnvKind, Guard
from llm.budget import LLMBudget
from llm.provider import LLMProvider
from repository.resolver import Repository
from runner.lifecycle import Lifecycle
from runner.runner import StepRunner
from session.app_session import AppSession
from session.device_session import DeviceSession
from testcase.loader import load_testcase
from tracer.storage import TraceStore

BUNDLE_ID = "com.phaset0.logindemo"
APPIUM_URL = "http://127.0.0.1:4723"
UDID = os.environ.get("MTA_SIM_UDID", "AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E")

# Phase 0 测试口令：默认值只提示来源，不内嵌明文（review C5/P2）
os.environ.setdefault("TEST_USERNAME", "qa_agent")
if "TEST_PASSWORD" not in os.environ:
    print("⚠ 未设置 TEST_PASSWORD，用 demo 默认（仅 Phase 0 演示用）")
    os.environ.setdefault("TEST_PASSWORD", "demo-only-not-a-secret")


# 漂移目标 = 登录页用户名输入框（App 里已改名为 user_field，metadata 仍写
# username_field）。用 `tap login_button` 当漂移目标不成立：overrides 里它还
# 带 predicate 策略 `label == '登录'`，改名后仍能命中，DRIFT 根本不会发生。
DRIFT_TARGET = "LoginView.username_field"
RUNTIME_ID = "user_field"

DRIFT_CASE = f"""\
schema_version: "0.2"
id: phase0_demo_recovery
name: Phase 0 auto recovery demo
precondition:
  reset: RELAUNCH
steps:
  - action: launch_app
  - action: input
    target: {DRIFT_TARGET}
    value: ${{TEST_USERNAME}}
"""

# P1_APP：`make p1-build` 的产物。漂移段必须**重装它**——基础用例的
# precondition 是 REINSTALL，会用 login_demo.yaml 里写死的 DerivedData 路径
# 覆盖掉改名后的包（2026-10-18 实测：漂移段因此退化成普通 SUCCESS）。
P1_APP = ROOT / "build/p1/Build/Products/Debug-iphonesimulator/LoginDemo.app"

# H15：恢复候选只登记在本 run 的 overrides 副本里，绝不写正式
# repository/overrides（工具只导出补丁，合入是人的动作）。
CANDIDATE_DOC = f"""
---
schema_version: "1.0"
kind: element
id: {RUNTIME_ID}
screen: LoginView
type: textfield
strategies:
  - {{type: accessibility_id, value: {RUNTIME_ID}, origin: manual,
     source_file: LoginDemoApp.swift, source_line: 66}}
metadata:
  risk: LOW
  idempotency: IDEMPOTENT
  data_class: INTERNAL
"""


def _overrides_with_candidate(path: Path) -> Path:
    """把正式 overrides 复制一份并追加运行时候选（H15：不动正式资产）。"""
    import shutil

    if path.exists():
        shutil.rmtree(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "repository" / "overrides", path)
    lv = path / "elements" / "LoginView.yaml"
    text = lv.read_text(encoding="utf-8")
    if f"id: {RUNTIME_ID}" not in text:
        lv.write_text(text + CANDIDATE_DOC, encoding="utf-8")
    return path


class StubLLM(LLMProvider):
    """返回新 parser 契约；候选必须是运行时真实存在的 user_field。"""

    def complete(self, prompt, timeout=30):
        return json.dumps({
            "action": "input",
            "target": {"type": "accessibility_id", "value": RUNTIME_ID},
            "scope": "LoginView",
            "reason": "stub: 等价输入框",
            "confidence": 0.9,
        })


def _write_demo_case(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DRIFT_CASE, encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stub", action="store_true",
                    help="用 stub LLM 替代真实 API")
    args = ap.parse_args()

    ds = DeviceSession(APPIUM_URL, {
        "platform_name": "iOS", "automation_name": "XCUITest",
        "device_name": "iPhone 14", "platform_version": "18.5",
        "udid": UDID, "bundle_id": BUNDLE_ID, "no_reset": True,
        "new_command_timeout": 120,
    })
    ds.connect()

    run_id = f"phase0_demo_{time.strftime('%Y%m%d_%H%M%S')}"
    store = TraceStore(ROOT / "out/trace.db")
    store.start_run(run_id, suite="phase0_demo")
    app = AppSession(ds, BUNDLE_ID)
    ex = Executor(ds)
    # 漂移段的 Repository 用**副本**（含 user_field 候选）；基础链路仍用正式
    # overrides，避免把 drift 专用登记混进日常资产。
    ov_dir = ROOT / "out" / "phase0_demo_overrides"

    def _repo(overrides: str | None = None) -> Repository:
        return Repository.from_dirs(
            generated_root=str(ROOT / "repository/generated/local"),
            overrides_root=overrides or str(ROOT / "repository/overrides"))

    repo = _repo()
    secrets = EnvSecretProvider()
    llm = StubLLM() if args.stub else (
        LLMProvider() if os.environ.get("LLM_API_KEY") else None)
    budget = LLMBudget(max_calls_per_run=3) if llm is not None else None
    recovery = RecoveryEngine(
        repo=repo, llm=llm, budget=budget, sleep=lambda _s: None) \
        if llm is not None else None
    if recovery is None:
        print("⚠ 未配置 LLM_API_KEY，自动恢复不启用（只演示基础链路）")

    pipeline = SessionPipeline(
        None, store=store, deps=PipelineDeps(
            env=EnvironmentManager(app), repo=repo, app=app,
            device_session=ds, executor=ex, store=store),
        recovery=recovery, secrets=secrets, app_id=BUNDLE_ID)
    runner = StepRunner(ex, ds, Guard(EnvKind.SANDBOX), run_id=run_id)
    if recovery is not None:
        recovery.guard = runner.guard
    lifecycle = Lifecycle(store=store)

    print("=== Phase 0 端到端 Demo ===")
    print("[1] 正常登录流程 ...")
    tc = load_testcase(ROOT / "testcase/login_demo.yaml")
    first = pipeline.run_case(runner, lifecycle, tc, run_id=run_id)
    print(f"    {first.status}")

    print(f"\n[2] Recovery 场景：运行时 {DRIFT_TARGET} 已被改名 ...")
    case_path = _write_demo_case(ROOT / "out/phase0_demo_recovery.yaml")
    drift = load_testcase(case_path)
    # 上一条用例的 REINSTALL 把 DerivedData 里的旧包装回去了；漂移段必须
    # 重新装上改名后的 p1 产物，否则「App 新、metadata 旧」不成立。
    if P1_APP.is_dir():
        subprocess.run(["xcrun", "simctl", "install", UDID, str(P1_APP)],
                       check=False, capture_output=True)
    else:
        print(f"    ⚠ 未找到 {P1_APP}（先跑 make p1-build）——漂移段可能退化成"
              f"普通 SUCCESS")
    # 漂移段用带候选项的 Repository（H15：副本，不动正式 overrides）
    if recovery is not None:
        recovery.repo = _repo(str(_overrides_with_candidate(ov_dir)))
    pipeline.deps.repo = _repo(str(ov_dir)) if ov_dir.exists() else repo
    result = pipeline.run_case(runner, lifecycle, drift, run_id=run_id)
    print(f"    {result.status}")

    rows = store.conn.execute(
        "SELECT s.id, s.status, s.step_type, s.target_id, s.detail_json"
        " FROM steps s JOIN testcase_runs tc ON tc.id=s.testcase_run_id"
        " WHERE tc.run_id=? ORDER BY s.id", (run_id,)
    ).fetchall()
    recovered = [r for r in rows if r["status"] == "RECOVERED"]
    print(f"    steps: {len(rows)} 条，RECOVERED: {len(recovered)} 条")
    for row in recovered:
        detail = json.loads(row["detail_json"] or "{}")
        stages = (detail.get("recovery") or {}).get("stages") or []
        print(f"    recovery stages: "
              f"{[str(s.get('stage')) + ':' + str(s.get('outcome')) for s in stages]}")

    reviews = store.list_reviews()
    infra_rows = store.conn.execute(
        "SELECT COUNT(*) FROM infra_events WHERE run_id=?", (run_id,)
    ).fetchone()[0]
    print(f"    pending reviews: {len(reviews)}；infra_events: {infra_rows} 条")

    ok = result.status == "RECOVERED" and bool(recovered)
    store.end_run(run_id, status="PASS" if ok else "FAIL",
                  exit_code=0 if ok else 1)
    if ok:
        print("\n✅ 端到端 Recovery 全链路通过"
              "（DRIFT → RecoveryEngine → 校验 → 执行）")
        return 0
    print(f"\n✗ recovery 未成功: {result.status} "
          f"{result.failure_type or ''}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
