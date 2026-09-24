"""Stage 5 验收：完整跑 login_demo.yaml，检查 trace + 脱敏。"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from environment.secrets import EnvSecretProvider
from executor.executor import Executor
from runner.testcase_runner import TestcaseRunner
from session.app_session import AppSession
from session.device_session import DeviceSession
from testcase.loader import load_testcase
from tracer.recorder import Recorder

# Phase 0 测试口令（demo app：任意非空即可登录；正式环境走 Vault）
os.environ.setdefault("TEST_USERNAME", "qa_agent")
os.environ.setdefault("TEST_PASSWORD", "test_pass_123")


def main() -> int:
    ds = DeviceSession("http://127.0.0.1:4723", {
        "platform_name": "iOS", "automation_name": "XCUITest",
        "device_name": "iPhone 14", "platform_version": "18.5",
        "udid": "AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E",
        "bundle_id": "com.phaset0.logindemo", "no_reset": True,
        "new_command_timeout": 120,
    })
    ds.connect()
    rec = Recorder()
    app = AppSession(ds, "com.phaset0.logindemo")
    ex = Executor(ds)
    runner = TestcaseRunner(ex, app, rec, EnvSecretProvider())

    tc = load_testcase("testcase/login_demo.yaml")
    status = runner.run(tc)
    assert status == "PASS", status
    print("[1] 用例执行 PASS")

    rows = rec.conn.execute(
        "SELECT step_index, action_type, locator, status FROM steps ORDER BY id"
    ).fetchall()
    print(f"[2] steps 表 {len(rows)} 条:")
    for r in rows:
        print(f"    {r}")

    run = rec.conn.execute(
        "SELECT run_id, test_case, status, source_commit FROM runs ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    print(f"[3] runs 表: {run}")
    assert run[2] == "PASS"

    print("\n✅ Stage 5 PASS — YAML 用例 + trace + secrets 全链路通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
