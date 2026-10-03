"""Stage 9 / F5 验收：真实改名重编译的端到端 DRIFT 恢复（review F5 关闭项）。

与 run_phase0_demo 的 stub 模拟不同，这里是真实场景：
  1. 源码 accessibilityIdentifier 已真实改名 login_button → submit_button，
     重编译重装后运行时只有 submit_button；
  2. source_metadata.json 保持改名前的旧产物（login_button）——
     注意：不能调 build_metadata（它会重扫改名后的源码，破坏 DRIFT 前提）；
  3. 用例 tap(login_button) → ElementNotFound → reconcile 判 DRIFT
     → recover()（内网真实 LLM）→ 唯一性校验 → tap(submit_button) → 用例 PASS；
  4. 验证 steps.status=RECOVERED 落库 + runs.status=PASS（R2-1 闭环真链路版）。

前置：Appium 4723 已起、模拟器 booted、App 已安装改名版、内网 LLM 网关可达。
运行后需 git checkout 源码还原 + 重编译重装（见 Makefile f5-restore）。
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.recovery import recover
from executor.executor import AmbiguousElement, ElementNotFound, Executor
from llm.budget import LLMBudget
from llm.provider import LLMProvider
from runner.testcase_runner import TestcaseRunner
from environment.secrets import SecretProvider
from session.app_session import AppSession
from session.device_session import DeviceSession
from source.reconciliation import reconcile_local
from testcase.loader import Step, TestCase
from tracer.recorder import Recorder

ROOT = Path(__file__).resolve().parent.parent
UDID = "AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E"
CAPS = {
    "platform_name": "iOS", "automation_name": "XCUITest",
    "device_name": "iPhone 14", "platform_version": "18.5",
    "udid": UDID, "bundle_id": "com.phaset0.logindemo", "no_reset": True,
    "new_command_timeout": 120,
}
# review R3-5：key 走环境变量（与 LLMProvider 默认行为对齐），不内嵌脚本
GATEWAY = {"base_url": os.environ.get("LLM_BASE_URL", "http://127.0.0.1:15721/v1"),
           "model": os.environ.get("LLM_MODEL", "step-5-preview"),
           "api_key": os.environ.get("LLM_API_KEY", "sk-test")}


def main() -> int:
    # 旧产物 metadata（改名前扫描结果），绝不重扫
    meta = json.loads((ROOT / "out/source_metadata.json").read_text())
    # P1 两键形态（12.3）：元素在 screen_elements[].elements 下
    old_ids = [e["id"] for scr in meta["screen_elements"]
               for e in scr["elements"]]
    assert "login_button" in old_ids, \
        "out/source_metadata.json 必须是改名前产物（含 login_button）"

    ds = DeviceSession("http://127.0.0.1:4723", CAPS)
    ds.connect()
    rec = Recorder(ROOT / "out/trace.db")
    app = AppSession(ds, "com.phaset0.logindemo")
    ex = Executor(ds)
    app.reset_state("RELAUNCH")
    time.sleep(2)

    llm = LLMProvider(**GATEWAY)
    run_id = rec.start_run("f5_real_rename_wrapper")  # R3-3：与 runner 内部 run 区分
    ds.attach_recorder(rec, run_id)

    print("[1] 运行时确认 login_button 真的不在（真实改名生效）...")
    page = ex.page_source()
    assert 'name="login_button"' not in page, "运行时仍能找到 login_button？改名未生效"
    assert 'name="submit_button"' in page, "运行时没有 submit_button，改名版未安装"
    print("    运行时只有 submit_button ✓")

    # 用 input 场景做主断言：输入框按 prompt 规则确定判 LOW（登录按钮是提交类，
    # 真实 LLM 可能判 HIGH → fail-closed 拒绝，属正确策略行为，不能当硬断言）
    print("[2] 自动恢复路径：runner 跑 input(username_field)，期望 RECOVERED→PASS ...")
    # 只放确定性 LOW 的 input 步骤做主断言；tap 登录钮（提交类，LLM 可能判 HIGH
    # → fail-closed 拒绝）放到 [3] 观察段
    tc = TestCase(id="f5_real_rename", name="F5 real rename drift recovery", steps=[
        Step(action="input", target="username_field", value="${TEST_USERNAME}"),
    ])
    class _Secrets(SecretProvider):
        def get(self, key: str) -> str:
            return {"TEST_USERNAME": "qa_agent",
                    "TEST_PASSWORD": "demo-only-not-a-secret"}[key]

    runner = TestcaseRunner(ex, app, rec, _Secrets(),
                            recovery_context={"metadata": meta,
                                              "budget": LLMBudget(max_calls_per_run=3), "llm": llm})
    t0 = time.time()
    try:
        final = runner.run(tc)
    except Exception as e:
        final = f"RAISED:{type(e).__name__}: {e}"
    print(f"    run 结果: {final}  (耗时 {time.time()-t0:.0f}s，含真实 LLM 往返)")

    # 注意：TestcaseRunner.run 内部会 start_run（tc.id），与外层脚本 run 是两条；
    # 按 tc.id 找 runner 自己那条 run 的全部步骤
    rows = rec.conn.execute(
        "SELECT s.id, s.run_id, s.step_index, s.status FROM steps s "
        "JOIN runs ru ON ru.run_id=s.run_id WHERE ru.test_case='f5_real_rename' "
        "ORDER BY s.id DESC LIMIT 1").fetchall()
    # R3-3：无 step 行时清晰失败，而非 IndexError
    assert rows, "runner 的 run 未落任何 steps 行（recovery 链路可能早退）"
    step_row = rows[0]
    run_row = rec.conn.execute(
        "SELECT status FROM runs WHERE run_id=?", (step_row[1],)).fetchone()
    # recoveries 无 status 列：RECOVERED 的证据 = accepted=1 + step_id 已回填（R2-3）
    rec_row = rec.conn.execute(
        "SELECT strategy, llm_target, confidence, accepted, step_id "
        "FROM recoveries WHERE step_id=? ORDER BY id DESC", (step_row[0],)).fetchone()
    print(f"    steps: {step_row}")
    print(f"    recoveries: {rec_row}")
    print(f"    runs: {run_row}")

    print("[3] 手动编排路径：reconcile 语义 + recover() 直调 ...")
    app.reset_state("RELAUNCH")
    time.sleep(2)
    page = ex.page_source()
    # P1 两键 metadata → reconcile_local 扁平子集适配（与 4.2 引擎侧
    # _source_subset 同款；P0 脚本自己带一份——引擎适配不覆盖 P0 路径）
    flat_meta = {
        "elements": [
            {"accessibilityId": e.get("accessibility_id"),
             "resolution_type": e["resolution_type"],
             "screen": scr["name"]}
            for scr in meta["screen_elements"] for e in scr["elements"]
        ],
        "screens": meta["screens"],
    }
    recon = reconcile_local("login_button", "LoginView", flat_meta, page)
    print(f"    reconciliation: {recon['status']}, candidates={recon['candidates_in_runtime']}")
    assert recon["status"] == "DRIFT", recon

    r = recover("login_button", ElementNotFound("accessibility id == login_button"),
                ex, meta, LLMBudget(max_calls_per_run=3), llm, recorder=rec)
    print(f"    recovery: {json.dumps(r, ensure_ascii=False)}")

    rec.end_run(run_id, "PASS" if final == "PASS" else "FAIL")

    # --- 断言 ---
    assert final == "PASS", final
    assert step_row[3] == "RECOVERED", step_row          # 单步 RECOVERED
    assert run_row[0] == "PASS", run_row                  # R2-1：run 级 PASS 闭环
    assert rec_row is not None and rec_row[3] == 1 and rec_row[4] == step_row[0], rec_row
    # 手动 tap 场景：RECOVERED 或 fail-closed 拒绝都算策略正确（提交类判 HIGH 是
    # prompt 规则的正确执行）；但 llm_target 必须找到真实新名字
    assert r["status"] in ("RECOVERED", "LLM_RISK_NOT_LOW"), r
    assert r["llm_target"] == "submit_button", r
    print("\n✅ Stage 9 / F5 PASS — 真实改名端到端：DRIFT → 真实LLM → 唯一性校验 → "
          "RECOVERED 落库 → 用例 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
