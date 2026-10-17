"""Phase 0 端到端 Demo（Stage 9）——当前唯一执行管线版。

场景：`login_demo.yaml` 的元素在运行时已改名。流程：

  tap(login_button) → ElementNotFound → Local Reconciliation 判 DRIFT
  → RecoveryEngine（真 LLM 或 --stub）→ 唯一性/类型/风险校验
  → 重发动作 → 用例 RECOVERED

需要环境变量：LLM_API_KEY（+ 可选 LLM_BASE_URL / LLM_MODEL）。
无 LLM key 时用 `--stub` 跑通全流程。
"""

import argparse
import json
import os
import sys
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


DRIFT_CASE = """\
schema_version: "0.2"
id: phase0_demo_recovery
name: Phase 0 auto recovery demo
precondition:
  reset: RELAUNCH
steps:
  - action: input
    target: LoginView.login_button
    value: ${TEST_USERNAME}
"""


class StubLLM(LLMProvider):
    """返回新 parser 契约；候选必须是运行时真实存在的 username_field。"""

    def complete(self, prompt, timeout=30):
        return json.dumps({
            "action": "input",
            "target": {"type": "accessibility_id", "value": "username_field"},
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

    run_id = "phase0_demo"
    store = TraceStore(ROOT / "out/trace.db")
    store.start_run(run_id, suite="phase0_demo")
    app = AppSession(ds, BUNDLE_ID)
    ex = Executor(ds)
    repo = Repository.from_dirs(
        generated_root=str(ROOT / "repository/generated/local"),
        overrides_root=str(ROOT / "repository/overrides"))
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

    print("\n[2] Recovery 场景：运行时 login_button 已被改名 ...")
    case_path = _write_demo_case(ROOT / "out/phase0_demo_recovery.yaml")
    drift = load_testcase(case_path)
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
