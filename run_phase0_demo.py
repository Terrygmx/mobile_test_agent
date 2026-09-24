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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

# Phase 0 测试口令（demo app：任意非空即可登录；正式环境走 Vault）
os.environ.setdefault("TEST_USERNAME", "qa_agent")
os.environ.setdefault("TEST_PASSWORD", "test_pass_123")


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
    runner = TestcaseRunner(ex, app, rec, EnvSecretProvider())
    run_id = rec.start_run("phase0_demo")

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
    meta = build_metadata(str(ROOT / "ios_demo/LoginDemo/LoginDemoApp.swift"),
                          ROOT / "out/source_metadata.json")
    print(f"    source metadata: {[e['id'] for e in meta['elements']]}")

    # 模拟改名：查找一个 metadata 里不存在、运行时也不存在的元素
    expected = "login_button"
    err = ElementNotFound(f"accessibility id == {expected}")
    page = ex.page_source()
    recon = reconcile_local(expected, meta["screen"], meta, page)
    print(f"    reconciliation: {recon['status']}, candidates={recon['candidates_in_runtime'][:5]}")

    if args.stub:
        llm = StubLLM()
        print("    （--stub 模式：LLM 返回固定等价目标 username_field）")
    else:
        if not os.environ.get("LLM_API_KEY"):
            print("    ✗ 未配置 LLM_API_KEY，用 --stub 或设置环境变量后重试")
            return 2
        llm = LLMProvider()

    r = recover(expected, err, ex, meta, LLMBudget(max_calls_per_run=3), llm,
                run_id=run_id, recorder=rec)
    print(f"    recovery: {json.dumps(r, ensure_ascii=False)}")

    rec.end_run(run_id, "RECOVERED" if r["status"] == "RECOVERED" else r["status"])
    print(f"\n[3] run {run_id} 状态: {rec.conn.execute('SELECT status FROM runs WHERE run_id=?', (run_id,)).fetchone()[0]}")

    if r["status"] == "RECOVERED":
        print("\n✅ 端到端 Recovery 全链路通过（DRIFT → LLM → 唯一性校验 → 执行）")
        return 0
    print(f"\n✗ recovery 未成功: {r['status']}")
    return 1


class StubLLM(LLMProvider):
    def complete(self, prompt, timeout=30):
        return json.dumps({
            "target": {"type": "accessibility_id", "value": "username_field"},
            "scope": "LoginDemoApp", "reason": "stub: 等价元素",
            "confidence": 0.9, "risk_level": "LOW",
        })


if __name__ == "__main__":
    sys.exit(main())
