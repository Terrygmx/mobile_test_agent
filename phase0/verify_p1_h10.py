#!/usr/bin/env python3
"""H10 注入验证（Task 2.7 Step 3）：cleanup 失败 → 套件终止 + exit 2。

设计 11.2 / H10：cleanup 失败不是「吞掉」而是升级 ENVIRONMENT_FAILURE，
默认 ABORT_SUITE——不能带着脏环境跑下一条（那是把「环境没复位」伪装成
「下一条用例的 bug」）。8.4：ENVIRONMENT_FAILURE → exit 2（INFRA）。

注入方式：给 run 装配一个第二次 cleanup 抛 CleanupError 的假
EnvironmentManager，跑 5 条 smoke 用例，验证：
  1. 第 2 条 cleanup 失败 → ENVIRONMENT_FAILURE/CLEANUP_FAILED；
  2. 后续用例**不跑**（中止），已跑结果保留（不清掉 = 不伪造「没跑过」）；
  3. exit code = 2（不是 1——环境问题不是用例 FAIL）；
  4. suite abort 的 remaining 明细落在 detail（报告可展示未执行清单）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cli.pipeline import PipelineDeps, SessionPipeline
from environment.manager import CleanupError, EnvironmentManager
from executor.guard import EnvKind, Guard
from runner.lifecycle import Lifecycle
from runner.runner import StepRunner
from tracer.storage import TraceStore


class _StubEx:
    def find(self, strategies):
        return _StubEl()

    def perform(self, action, element, value=None):
        pass


class _StubEl:
    def is_displayed(self):
        return True

    def is_enabled(self):
        return True

    @property
    def text(self):
        return ""

    def get_attribute(self, name):
        return ""


class _StubDS:
    def ensure_alive(self):
        pass

class _FailingEnv(EnvironmentManager):
    """第 fail_after 次 cleanup 抛 CleanupError，其余放行。"""

    def __init__(self, fail_after: int):
        super().__init__(app=object())
        self.fail_after = fail_after
        self.cleanup_calls = 0

    def prepare(self, tc) -> None:
        pass

    def cleanup(self, tc=None) -> None:
        self.cleanup_calls += 1
        if self.cleanup_calls >= self.fail_after:
            raise CleanupError(
                f"H10 注入验证：第 {self.cleanup_calls} 次 cleanup 故意失败")


def main() -> int:
    import tempfile

    store = TraceStore(ROOT / "out" / "h10_injection.db")
    for tbl in ("runs", "testcase_runs", "steps"):
        try:
            store.conn.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    store.conn.commit()

    cases = SessionPipeline(ROOT / "suites" / "smoke").discover()
    print(f"load cases: {[c.id for c in cases]}")

    env = _FailingEnv(fail_after=2)
    pipeline = SessionPipeline(ROOT / "suites", store=store,
                               deps=PipelineDeps(env=env))
    runner = StepRunner(_StubEx(), _StubDS(), Guard(EnvKind.SANDBOX),
                        run_id="h10")
    pipeline._step_runner = runner
    pipeline._lifecycle = Lifecycle(store=store)

    run_id = "h10_injection"
    store.start_run(run_id, suite="smoke")
    run = pipeline.run_all(cases, run_id=run_id)

    ok = True

    # 1. 第 2 条 cleanup 失败 → ENVIRONMENT_FAILURE/CLEANUP_FAILED
    failed = [r for r in run.results if r.status == "ENVIRONMENT_FAILURE"]
    assert len(failed) == 1, f"应恰好 1 条 ENVIRONMENT_FAILURE，got {run.results}"
    f = failed[0]
    print(f"[1] {f.testcase_id}: {f.status}/{f.failure_type} "
          f"cleanup={f.cleanup_status}")
    if f.failure_type != "CLEANUP_FAILED":
        print("    ✗ failure_type 不是 CLEANUP_FAILED")
        ok = False

    # 2. 套件中止：后续用例不跑（3 条 smoke 未执行），已跑结果保留
    print(f"[2] executed={len(run.results)}/{len(cases)} "
          f"remaining={f.detail.get('remaining')}")
    if len(run.results) >= len(cases):
        print("    ✗ 后续用例仍被执行——H10 中止未生效")
        ok = False
    if len(run.results) < 2:
        print("    ✗ 已跑结果被清掉（伪造「没跑过」）")
        ok = False

    # 3. exit code = 2（环境问题，不是 FAIL 的 1）
    code = run.exit_code
    print(f"[3] exit_code={code}")
    if code != 2:
        print(f"    ✗ 期望 2（INFRA），got {code}")
        ok = False

    # 4. trace.db 终态一致（runs 表落 INFRA/exit 2，testcase_runs 落真实结果）
    row = store.conn.execute(
        "SELECT status, exit_code FROM runs WHERE run_id='h10_injection'"
    ).fetchone()
    print(f"[4] runs row: status={row[0]} exit_code={row[1]}")
    if row[0] != "INFRA_FAILURE" or row[1] != 2:
        print("    ✗ trace 终态与退出码不一致")
        ok = False

    print("\nH10 注入验证：" + ("PASS ✅" if ok else "FAIL ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
