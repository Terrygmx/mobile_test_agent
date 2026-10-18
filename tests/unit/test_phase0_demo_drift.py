"""Phase 0 demo 漂移段的回归守卫（2026-10-18）。

背景：`run_phase0_demo.py` 在旧 runner 退役（9dd5bf1）后第一次端到端实跑，
表现是「用例 PASS 但没有一条 RECOVERED 步骤」——看起来绿，其实**根本没验到
恢复**。两处退化路径：

1. 漂移目标选了 `login_button`：overrides 里它还带 predicate 策略
   `label == '登录'`，源码改名后照样命中 → DRIFT 永不发生；
2. 基础用例的 precondition 是 REINSTALL，会把 login_demo.yaml 里写死的
   DerivedData 旧包装回去，覆盖掉改名后的包。

这里用 FakeExecutor/FakeLLM 锁住「同一份用例定义必须走完 DRIFT → 候选 →
RECOVERED」，并额外把「带 predicate 兜底的目标不能当漂移目标」这条陷阱
显式钉住。测试不复制恢复引擎逻辑，走 SessionPipeline 全链。
"""
from __future__ import annotations

import json

from executor.executor import ElementNotFound
from llm.budget import LLMBudget
from tests.fault_injection.fi_support import (
    El,
    FakeDS,
    FakeExecutor,
    FakeLLM,
    llm_json,
    run_matrix,
)

APP = "com.phaset0.logindemo"
SCREEN = "LoginView"
DRIFT_ID = "username_field"        # metadata 仍登记的旧 id
RUNTIME_ID = "user_field"          # App 已改名后的运行时 id

LOGIN_PAGE = f"""\
<App>
  <Node name="screen.LoginView" visible="true"/>
  <TextField name="{RUNTIME_ID}" visible="true"/>
  <TextField name="password_field" visible="true"/>
  <Button name="login_button" visible="true"/>
</App>
"""


class LoginDriftExecutor(FakeExecutor):
    """只模拟「旧 id 找不到、`user_field` 找得到」的漂移语义。"""

    def find(self, strategies):
        self.find_calls += 1
        values = {s.get("value") for s in strategies}
        if values & {RUNTIME_ID}:
            return El(etype="XCUIElementTypeTextField")
        raise ElementNotFound(f"drifted {DRIFT_ID}")

    def find_all(self, locator):
        self.find_all_calls += 1
        values = {s.get("value") for s in locator}
        if values & {RUNTIME_ID}:
            return [El(etype="XCUIElementTypeTextField")]
        return []


def _repo(tmp_path):
    """LoginView：username_field（旧）+ user_field（新，恢复候选登记）。"""
    from repository.resolver import Repository
    root = tmp_path / "overrides"
    (root / "elements").mkdir(parents=True, exist_ok=True)
    (root / "screens").mkdir(parents=True, exist_ok=True)
    (root / "elements" / "LoginView.yaml").write_text(
        "schema_version: '1.0'\nkind: element\n"
        f"id: {DRIFT_ID}\nscreen: {SCREEN}\ntype: textfield\nstrategies:\n"
        f"- type: accessibility_id\n  value: {DRIFT_ID}\n  origin: source\n"
        "metadata:\n  risk: LOW\n  idempotency: IDEMPOTENT\n"
        "  data_class: INTERNAL\n"
        "---\n"
        "schema_version: '1.0'\nkind: element\n"
        f"id: {RUNTIME_ID}\nscreen: {SCREEN}\ntype: textfield\nstrategies:\n"
        f"- type: accessibility_id\n  value: {RUNTIME_ID}\n  origin: manual\n"
        "metadata:\n  risk: LOW\n  idempotency: IDEMPOTENT\n"
        "  data_class: INTERNAL\n",
        encoding="utf-8",
    )
    (root / "screens" / "LoginView.yaml").write_text(
        "schema_version: '1.0'\nkind: screen\n"
        f"id: {SCREEN}\nmarker: screen.{SCREEN}\nkind_hint: page\n"
        "metadata:\n  risk: LOW\n  origin: source\n",
        encoding="utf-8",
    )
    return Repository.from_dirs(overrides_root=str(root))


def _drift_case(target: str = f"{SCREEN}.{DRIFT_ID}") -> str:
    return f"""\
schema_version: "0.2"
id: phase0_demo_recovery
name: Phase 0 auto recovery demo
precondition:
  reset: RELAUNCH
steps:
  - action: launch_app
  - action: input
    target: {target}
    value: qa_agent
"""


def _run(tmp_path, case: str, llm_value=RUNTIME_ID):
    from agent.recovery import RecoveryEngine
    repo = _repo(tmp_path)
    llm = FakeLLM([llm_json(value=llm_value, action="input")])
    budget = LLMBudget()
    recovery = RecoveryEngine(repo=repo, llm=llm, budget=budget,
                              sleep=lambda _s: None)
    return run_matrix(tmp_path, case, ex=LoginDriftExecutor(
        page_source=LOGIN_PAGE), repo=repo, recovery=recovery,
        bundle_id=APP)


def test_phase0_drift_step_is_recovered_not_silently_pass(tmp_path):
    """同一份漂移用例定义：必须有 RECOVERED 步骤，且 reconcile 判 DRIFT。

    这是 demo 退化时唯一会红的断言：退化形态是「用例 PASS + 0 条 RECOVERED」，
    只断言 status 会把它当成通过。
    """
    run, trace, _, ex = _run(tmp_path / "drift", _drift_case())
    result = run.results[0]
    assert result.status == "RECOVERED", result.status
    assert result.detail["recovered_kinds"] == ["RECOVERED_LLM"]

    rows = trace.conn.execute(
        "SELECT status, detail_json FROM steps WHERE status='RECOVERED'"
    ).fetchall()
    assert len(rows) == 1, "漂移段没有产生 RECOVERED 步骤（恢复没被验到）"
    stages = json.loads(rows[0]["detail_json"])["recovery"]["stages"]
    reconcile = [s for s in stages if s.get("stage") == "reconcile"]
    assert len(reconcile) == 1 and reconcile[0]["outcome"] == "DRIFT"
    assert any(s.get("stage") == "llm" and s.get("outcome") == "candidate"
               and s.get("target", {}).get("value") == RUNTIME_ID
               for s in stages)
    # 恢复必须真的重发了动作（不是只落了一条好看的 trace）
    assert ex.input_calls == 1


def test_phase0_drift_target_must_not_be_a_predicate_fallback(tmp_path):
    """退化路径①：拿 login_button 当漂移目标时 predicate 仍命中 → 不漂移。

    守卫的是「demo 漂移目标的选择」：只要目标元素还带 predicate 兜底策略，
    源码改名就不会产生 ELEMENT_NOT_FOUND。这里用同样的带 predicate 元素，
    断言跑完是 PASS 且**没有** RECOVERED——把这条陷阱显式记录下来。
    """
    from cli.pipeline import PipelineDeps, SessionPipeline
    from executor.guard import EnvKind, Guard
    from repository.resolver import Repository
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner
    from tracer.storage import TraceStore

    root = tmp_path / "pred"
    (root / "elements").mkdir(parents=True, exist_ok=True)
    (root / "screens").mkdir(parents=True, exist_ok=True)
    (root / "elements" / "LoginView.yaml").write_text(
        "schema_version: '1.0'\nkind: element\n"
        "id: login_button\nscreen: LoginView\ntype: button\nstrategies:\n"
        "- type: accessibility_id\n  value: login_button\n  origin: source\n"
        "- type: predicate\n  value: \"label == '登录'\"\n  origin: manual\n"
        "metadata:\n  risk: MEDIUM\n  idempotency: IDEMPOTENT\n"
        "  data_class: PUBLIC\n",
        encoding="utf-8")
    (root / "screens" / "LoginView.yaml").write_text(
        "schema_version: '1.0'\nkind: screen\nid: LoginView\n"
        "marker: screen.LoginView\nkind_hint: page\n"
        "metadata:\n  risk: LOW\n  origin: source\n", encoding="utf-8")
    repo = Repository.from_dirs(overrides_root=str(root))

    class PredicateHitExecutor(FakeExecutor):
        """accessibility_id 找不到，但 predicate 命中（真机改名后的同款行为）。"""

        def find(self, strategies):
            self.find_calls += 1
            for s in strategies:
                if s.get("type") == "predicate":
                    return El()
            raise ElementNotFound("renamed")

    store = TraceStore(tmp_path / "trace.db")
    store.start_run("run_matrix", app_bundle_id=APP)
    sdir = tmp_path / "suites"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "matrix.yaml").write_text(
        'schema_version: "0.2"\nid: c\nname: c\nsteps:\n'
        '  - action: tap\n    target: LoginView.login_button\n',
        encoding="utf-8")
    pipe = SessionPipeline(suites_root=sdir, store=store, app_id=APP)
    pipe.deps = PipelineDeps(repo=repo)
    runner = StepRunner(PredicateHitExecutor(page_source=LOGIN_PAGE),
                        FakeDS(), Guard(EnvKind.SANDBOX))
    pipe._step_runner = runner
    pipe._lifecycle = Lifecycle(store=store)
    run = pipe.run_all(pipe.discover(), run_id="run_matrix")

    assert run.results[0].status == "PASS"
    assert store.conn.execute(
        "SELECT COUNT(*) FROM steps WHERE status='RECOVERED'"
    ).fetchone()[0] == 0, \
        "带 predicate 兜底的目标不该被用于漂移演示（DRIFT 不会发生）"
