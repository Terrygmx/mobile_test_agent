"""Stage 5 验收：完整跑 login_demo.yaml，检查 trace + 脱敏。

执行与落库都走当前唯一管线：SessionPipeline + StepRunner + Lifecycle +
TraceStore。Stage 5 的验收对象仍是「登录用例通过、步骤可见、敏感值不落盘」，
不再维护 P0 TestcaseRunner/Recorder 的第二套语义。
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cli.pipeline import PipelineDeps, SessionPipeline
from environment.manager import EnvironmentManager
from environment.secrets import EnvSecretProvider
from executor.executor import Executor
from executor.guard import EnvKind, Guard
from repository.resolver import Repository
from runner.lifecycle import Lifecycle
from runner.runner import StepRunner
from session.app_session import AppSession
from session.device_session import DeviceSession
from testcase.loader import load_testcase
from tracer.storage import TraceStore

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
    run_id = f"stage5_{time.strftime('%Y%m%d_%H%M%S')}"
    store = TraceStore("out/trace.db")
    store.start_run(run_id, suite="stage5")
    app = AppSession(ds, "com.phaset0.logindemo")
    ex = Executor(ds)
    repo = Repository.from_dirs(
        generated_root="repository/generated/local",
        overrides_root="repository/overrides")
    secrets = EnvSecretProvider()
    pipeline = SessionPipeline(
        None, store=store, secrets=secrets,
        deps=PipelineDeps(env=EnvironmentManager(app), repo=repo, app=app,
                          device_session=ds, executor=ex, store=store))
    runner = StepRunner(ex, ds, Guard(EnvKind.SANDBOX), run_id=run_id)
    lifecycle = Lifecycle(store=store)

    tc = load_testcase("testcase/login_demo.yaml")
    result = pipeline.run_case(runner, lifecycle, tc, run_id=run_id)
    assert result.status == "PASS", result.status
    print("[1] 用例执行 PASS")

    rows = store.conn.execute(
        "SELECT s.step_index, s.step_type, s.target_id, s.status"
        " FROM steps s JOIN testcase_runs tc ON tc.id=s.testcase_run_id"
        " WHERE tc.run_id=? ORDER BY s.id", (run_id,)
    ).fetchall()
    print(f"[2] steps 表 {len(rows)} 条:")
    for r in rows:
        print(f"    {r}")
    assert rows, "新管线没有写入 steps"
    detail_rows = store.conn.execute(
        "SELECT detail_json FROM steps s JOIN testcase_runs tc"
        " ON tc.id=s.testcase_run_id WHERE tc.run_id=?", (run_id,)
    ).fetchall()
    assert all("{{SECRET_" not in (r[0] or "") for r in detail_rows), \
        "secret 占位符不得原样落 trace"

    run = store.conn.execute(
        "SELECT run_id, status FROM runs WHERE run_id=?", (run_id,)
    ).fetchone()
    print(f"[3] runs 表: {run}")
    assert run[1] == "PASS"
    store.end_run(run_id, status="PASS", exit_code=0)

    print("\n✅ Stage 5 PASS — YAML 用例 + trace + secrets 全链路通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
