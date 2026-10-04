"""Task 4.2：LLM 恢复端到端故障注入（FakeLLM + FakeDriver，无网络）。

矩阵子集（§18）：#2 locator 漂移 → RECOVERED exit 5；#10 预算/熔断不发
新调用；#15 断言目标漂移 → RECOVERED（kind=assertion_target 上下文）；
#13 反例（on_wait_timeout=true 接通后可恢复）。
"""
from __future__ import annotations

import json
import sqlite3

import yaml

from executor.executor import ElementNotFound
from llm.budget import BudgetConfig, LLMBudget

from tests.fault_injection.fi_support import (
    El as _El,
    FakeExecutor,
    FakeLLM,
    drift_repo as _repo,
    llm_json as _llm_json,
    run_matrix as _run,
    load_case as _case,
)

from tests.fault_injection.fi_support import (
    El as _El,
    FakeExecutor,
    FakeLLM,
    drift_repo as _repo,
    llm_json as _llm_json,
    run_matrix as _run,
    load_case as _case,
)

DRIFT_CASE = """\
schema_version: "0.2"
id: llm_drift_001
name: drift
suite: smoke
tags: [smoke]
steps:
  - action: launch_app
  - action: tap
    target: HomeView.login_button
    idempotency: IDEMPOTENT
"""


def test_fi_02_llm_recovery_recovered_exit5(tmp_path):
    """矩阵 #2：locator 漂移（login_button→signin_button）+ LLM →
    步骤/用例 RECOVERED、exit 5、recoveries 行 + PENDING review。"""
    from agent.recovery import RecoveryEngine

    # find 序：主步骤(旧 id)失败 → settle refind 失败 → LLM 候选命中
    ex = FakeExecutor(find_script=[ElementNotFound("drifted"),
                                   ElementNotFound("drifted"), _El()])
    llm = FakeLLM([_llm_json()])
    recovery = RecoveryEngine(repo=_repo(tmp_path), llm=llm,
                              budget=LLMBudget(), sleep=lambda s: None)
    run, store, _, _ = _run(tmp_path, DRIFT_CASE, ex=ex, repo=_repo(tmp_path),
                      recovery=recovery)
    r = run.results[0]
    assert r.status == "RECOVERED", r.detail
    assert r.detail["recovery_kinds"] == ["llm"]
    assert run.exit_code == 5
    assert ex.tap_calls == 1, "恢复后按候选执行 tap"
    conn = sqlite3.connect(tmp_path / "trace.db")
    kinds = conn.execute("SELECT kind, result FROM recoveries").fetchall()
    assert ("LLM", "RECOVERED") in kinds
    reviews = conn.execute(
        "SELECT review_status FROM recovery_reviews").fetchall()
    assert reviews == [("PENDING",)], "LLM 恢复必须建 PENDING review（9.5）"


def test_fi_02b_review_accept_exports_patch_h15(tmp_path):
    """9.5：accept 导出 overrides 补丁（只落 --out/stdout，不写
    repository/overrides——H15）；reject 需 note。

    P2（Task 2.3）起 accept 同时是 Candidate 的唯一入口：本行给出完整
    追溯链（bundle_id + step 关联），并显式 `--exp-db` 隔离——绝不写仓库的
    out/experience.db（测试污染真实经验库 = 污染数据）。"""
    from agent.recovery import RecoveryEngine
    from cli.main import main

    ex = FakeExecutor(find_script=[ElementNotFound("drifted"),
                                   ElementNotFound("drifted"), _El()])
    recovery = RecoveryEngine(repo=_repo(tmp_path), llm=FakeLLM([_llm_json()]),
                              budget=LLMBudget(), sleep=lambda s: None)
    _run(tmp_path, DRIFT_CASE, ex=ex, repo=_repo(tmp_path), recovery=recovery,
         bundle_id="com.matrix.app")
    out = tmp_path / "patch.yaml"
    exp_db = tmp_path / "experience.db"
    code = main(["review", "list", "--db", str(tmp_path / "trace.db")])
    assert code == 0
    code = main(["review", "accept", "1", "--db", str(tmp_path / "trace.db"),
                 "--reviewer", "tester", "--out", str(out),
                 "--exp-db", str(exp_db)])
    assert code == 0
    patch = out.read_text(encoding="utf-8")
    assert "signin_button" in patch and "login_button" in patch
    assert "origin: manual" in patch
    # H15：补丁只能落 --out 指定文件，repository/overrides 无人碰
    assert not (tmp_path / "repository").exists()
    # P2：ACCEPT 顺带建出 Candidate（种子三件套来自本 run 的真实链）
    import sqlite3
    conn = sqlite3.connect(exp_db)
    try:
        rows = conn.execute(
            "SELECT status, app_id, screen_id, target_id, seed_run_id,"
            " seed_recovery_review_id FROM experiences").fetchall()
    finally:
        conn.close()
    assert rows == [("CANDIDATE", "com.matrix.app", "HomeView",
                     "login_button", "run_matrix", 1)]
    # 二次决策拒绝（已 ACCEPT 不可再动）
    code = main(["review", "reject", "1", "--db", str(tmp_path / "trace.db"),
                 "--note", "double decide"])
    assert code == 3
    # reject 流程：note 必填在 argparse 层拦截（exit 2）；agent 层 guard
    # 是 exit 3（decide_review 直调时）
    import pytest as _pytest
    with _pytest.raises(SystemExit) as ei:
        main(["review", "reject", "1", "--db", str(tmp_path / "trace.db")])
    assert ei.value.code == 2


def test_fi_10_budget_exhausted_no_new_api_calls(tmp_path):
    """矩阵 #10：预算耗尽 → LLM_BUDGET_EXCEEDED 且未发起新 API 调用。"""
    from agent.recovery import RecoveryEngine

    case_b = DRIFT_CASE.replace("llm_drift_001", "llm_drift_002")
    cases_yaml = {"llm_drift_001": DRIFT_CASE, "llm_drift_002": case_b}
    ex = FakeExecutor(find_script=[ElementNotFound("drifted")])
    llm = FakeLLM([_llm_json(), _llm_json()])   # 每用例最多 1 次
    budget = LLMBudget(config=BudgetConfig(max_calls_per_run=1,
                                           max_calls_per_testcase=1,
                                           breaker_consecutive_failures=99))
    recovery = RecoveryEngine(repo=_repo(tmp_path), llm=llm, budget=budget,
                              sleep=lambda s: None)
    from testcase.schema import parse_testcase_dict
    cases = [parse_testcase_dict(yaml.safe_load(v))
             for v in cases_yaml.values()]
    run, store, _, _ = _run(tmp_path, DRIFT_CASE, ex=ex, repo=_repo(tmp_path),
                      recovery=recovery, cases=cases)
    # 用例 1：预算 1 次给了 LLM，但 find 序耗尽后恒 ENF → 校验链拒绝
    assert run.results[0].failure_type == "LLM_TARGET_NOT_FOUND"
    assert run.results[1].failure_type == "LLM_BUDGET_EXCEEDED"
    assert len(llm.calls) == 1, "预算耗尽后不得再发调用"
    assert run.exit_code == 1


def test_fi_15_assertion_target_drift_recovered(tmp_path):
    """矩阵 #15：断言目标漂移 → LLM 候选 → 覆盖重验 → 步骤 RECOVERED
    （kind=assertion_target 上下文）、exit 5。"""
    from agent.recovery import RecoveryEngine

    case = """\
schema_version: "0.2"
id: llm_assert_001
name: assertion drift
suite: smoke
tags: [smoke]
steps:
  - action: launch_app
  - assertion:
      target: HomeView.login_button
      condition: exists
      timeout: 0.2
"""
    # find 序：断言首次 check 轮询（1 次 ENF 即抛 Drift，timeout 0.2 内）→
    # 恢复 refind → LLM 候选命中 → 覆盖重验命中
    ex = FakeExecutor(find_script=[ElementNotFound("drifted"),
                                   ElementNotFound("drifted"), _El(), _El()])
    recovery = RecoveryEngine(repo=_repo(tmp_path), llm=FakeLLM([_llm_json()]),
                              budget=LLMBudget(), sleep=lambda s: None)
    run, store, _, _ = _run(tmp_path, case, ex=ex, repo=_repo(tmp_path),
                      recovery=recovery)
    r = run.results[0]
    assert r.status == "RECOVERED", r.detail
    assert run.exit_code == 5
    conn = sqlite3.connect(tmp_path / "trace.db")
    row = conn.execute(
        "SELECT status, detail_json FROM steps WHERE status='RECOVERED'"
        ).fetchone()
    assert row is not None
    assert "assertion_target" in (row[1] or ""), \
        "矩阵 #15：恢复上下文 kind=assertion_target 可见"
    reviews = conn.execute(
        "SELECT review_status FROM recovery_reviews").fetchall()
    assert reviews == [("PENDING",)]


def test_fi_13on_wait_timeout_recoverable_with_knob(tmp_path):
    """⑥：on_wait_timeout=true 接通——wait 元素目标漂移可经 LLM 恢复；
    默认 false 时矩阵 #13 仍不进恢复（test_wda_and_recovery 已覆盖）。"""
    from agent.policy import RecoveryConfig
    from agent.recovery import RecoveryEngine

    case = """\
schema_version: "0.2"
id: llm_wait_001
name: wait drift
suite: smoke
tags: [smoke]
steps:
  - action: launch_app
  - wait_for:
      target: HomeView.login_button
      condition: visible
      timeout: 0.2
"""
    ex = FakeExecutor(find_script=[ElementNotFound("drifted"),
                                   ElementNotFound("drifted"), _El(), _El()])
    recovery = RecoveryEngine(
        config=RecoveryConfig(on_wait_timeout=True),
        repo=_repo(tmp_path), llm=FakeLLM([_llm_json()]),
        budget=LLMBudget(), sleep=lambda s: None)
    run, store, _, _ = _run(tmp_path, case, ex=ex, repo=_repo(tmp_path),
                      recovery=recovery)
    r = run.results[0]
    assert r.status == "RECOVERED", r.detail
    assert run.exit_code == 5
