"""Phase 0 端到端 Demo（Stage 9）——设计文档第 5 节步骤 7 的真实版。

场景：login_demo.yaml 用例找 login_button，但"源码已被改名"（用不存在元素模拟
源码 metadata 仍是旧的）。流程：
  tap(login_button) → ElementNotFound → reconcile_local → DRIFT
  → recover()（真 LLM）→ 唯一性校验 → tap → PASS

需要环境变量：LLM_API_KEY（+ 可选 LLM_BASE_URL / LLM_MODEL）。
无 LLM key 时用 --stub 跑通全流程（stub 返回 signin_button 等价目标）。
"""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # review P2-1：项目根，非上级

from agent.recovery import recover
from environment.secrets import EnvSecretProvider
from executor.executor import AmbiguousElement, ElementNotFound, Executor
from llm.budget import LLMBudget
from llm.provider import LLMProvider
from runner.testcase_runner import TestFailure, TestcaseRunner
from session.app_session import AppSession
from session.device_session import DeviceSession
from source.metadata import build_metadata
from source.reconciliation import reconcile_local
from testcase.loader import load_testcase
from tracer.recorder import Recorder

ROOT = Path(__file__).resolve().parent

# Phase 0 测试口令：默认值只提示来源，不内嵌明文（review C5/P2）
os.environ.setdefault("TEST_USERNAME", "qa_agent")
if "TEST_PASSWORD" not in os.environ:
    print("⚠ 未设置 TEST_PASSWORD，用 demo 默认（仅 Phase 0 演示用）")
    os.environ.setdefault("TEST_PASSWORD", "demo-only-not-a-secret")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stub", action="store_true", help="用 stub LLM 替代真实 API")
    args = ap.parse_args()

    ds = DeviceSession("http://127.0.0.1:4723", {
        "platform_name": "iOS", "automation_name": "XCUITest",
        "device_name": "iPhone 14", "platform_version": "18.5",
        "udid": "AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E",
        "bundle_id": "com.phaset0.logindemo", "no_reset": True,
        "new_command_timeout": 120,
    })
    ds.connect()
    rec = Recorder(ROOT / "out/trace.db")
    app = AppSession(ds, "com.phaset0.logindemo")
    ex = Executor(ds)
    # recovery 依赖（review R2-5：demo 主入口启用自动恢复路径）
    meta = build_metadata(str(ROOT / "ios_demo/LoginDemo/LoginDemoApp.swift"),
                          ROOT / "out/source_metadata.json")
    llm = StubLLM() if args.stub else (
        LLMProvider() if os.environ.get("LLM_API_KEY") else None)
    recovery_ctx = ({"metadata": meta, "budget": LLMBudget(max_calls_per_run=3),
                     "llm": llm} if llm else None)
    if recovery_ctx is None:
        print("⚠ 未配置 LLM_API_KEY，自动恢复不启用（只演示基础链路）")
    runner = TestcaseRunner(ex, app, rec, EnvSecretProvider(),
                            recovery_context=recovery_ctx)
    run_id = rec.start_run("phase0_demo")
    # review R2-4：infra 事件挂到本 run 的 SQLite（run_id 存在后补挂）
    ds.attach_recorder(rec, run_id)

    print("=== Phase 0 端到端 Demo ===")
    print("[1] 正常运行 login_demo.yaml ...")
    tc = load_testcase(ROOT / "testcase/login_demo.yaml")
    try:
        runner.run(tc)
        # 已登录（上次运行残留）→ 回登录页重来
        app.reset_state("RELAUNCH")
        runner.run(tc)
        print("    PASS")
    except (TestFailure, AssertionError):
        print("    （登录页流程异常，继续 recovery 场景）")

    print("\n[2] Recovery 场景：源码 metadata 仍是 login_button，运行时已被改名 ...")
    # 回登录页（上面用例 PASS 后 App 停在首页，等价元素不在当前页面）
    app.reset_state("RELAUNCH")
    print(f"    source metadata: {[e['id'] for e in meta['elements']]}")

    # 自动恢复路径（R2-5）：跑一个 target 不存在的 input 步骤，
    # runner 内部 reconcile → recover → RECOVERED → 用例继续
    from testcase.loader import Step, TestCase
    tc2 = TestCase(id="phase0_demo_recovery", name="auto recovery demo", steps=[
        Step(action="input", target="ghost_field_drift", value="${TEST_USERNAME}"),
    ])
    try:
        runner.run(tc2)
        print("    用例 PASS（runner 自动恢复生效，R2-1 闭环）")
    except Exception as e:
        print(f"    用例未恢复: {type(e).__name__}")

    # 手动编排路径保留（展示 reconcile 语义给 LLM 的输入）
    expected = "login_button"
    err = ElementNotFound(f"accessibility id == {expected}")
    page = ex.page_source()
    recon = reconcile_local(expected, "LoginView", meta, page)
    print(f"    reconciliation: {recon['status']}, candidates={recon['candidates_in_runtime'][:5]}")

    if args.stub:
        pass  # llm 已在上方按 --stub 构造
    elif llm is None:
        print("    ✗ 未配置 LLM_API_KEY，无法演示手动 recovery")
        return 2
    assert llm is not None  # 窄化类型（上方已排除 None）

    r = recover(expected, err, ex, meta, LLMBudget(max_calls_per_run=3), llm,
                recorder=rec)
    print(f"    recovery: {json.dumps(r, ensure_ascii=False)}")

    # R2-4 验证：infra_events 表应有本 run 的事件（若期间发生 WDA restart）
    infra_rows = rec.conn.execute(
        "SELECT COUNT(*) FROM infra_events WHERE run_id=?", (run_id,)).fetchone()[0]
    print(f"    infra_events(本 run): {infra_rows} 条")

    rec.end_run(run_id, "PASS" if r["status"] == "RECOVERED" else "FAIL")  # review F8：status 枚举收敛到 PASS/FAIL/INFRA_FAILURE
    print(f"\n[3] run {run_id} 状态: {rec.conn.execute('SELECT status FROM runs WHERE run_id=?', (run_id,)).fetchone()[0]}")

    if r["status"] == "RECOVERED":
        print("\n✅ 端到端 Recovery 全链路通过（DRIFT → LLM → 唯一性校验 → 执行）")
        return 0
    print(f"\n✗ recovery 未成功: {r['status']}")
    return 1


class StubLLM(LLMProvider):
    def complete(self, prompt, timeout=30):
        # review R3-2：按 prompt 中的原动作回显——否则 input 步骤恢复时
        # stub 返回 tap 必然 LLM_ACTION_MISMATCH，--stub 模式的自动恢复段静默失败
        import re as _re
        m = _re.search(r'执行 (tap|input)', prompt)
        action = m.group(1) if m else "tap"
        return json.dumps({
            "target": {"type": "accessibility_id", "value": "username_field"},
            "action": action,
            "input_value": None,
            "scope": "LoginDemoApp", "reason": "stub: 等价元素",
            "confidence": 0.9, "risk_level": "LOW",
        })


if __name__ == "__main__":
    sys.exit(main())
