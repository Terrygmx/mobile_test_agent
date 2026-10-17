"""乐速通恢复演练的离线闭环（第三项）。

真机演练在 `suites/lst_recovery/recovery_drift_001.yaml`；这里用与
`fi_support` 同款的 FakeExecutor / FakeLLM 锁住同一语义：

1. 旧定位找不到 → local reconciliation 判 DRIFT；
2. LLM 从真实 UI 树里选 `return` → 重试成功 → 用例 RECOVERED；
3. 人工 ACCEPT 后建 Experience；再跑同一漂移 → RECOVERED_EXPERIENCE，
   LLM 零调用。

测试不复制恢复引擎逻辑，走 `SessionPipeline.run_all` 全链。
"""
from __future__ import annotations

import json
import sqlite3

from executor.executor import ElementNotFound
from experience import SQLiteExperienceStore
from llm.budget import LLMBudget
from tests.fault_injection.fi_support import (
    El,
    FakeExecutor,
    FakeLLM,
    llm_json,
    run_matrix,
)

APP = "com.etc.nfcPushOnline"
SCREEN = "LSTDynamicSearchView"
DRIFT_ID = "lst_recovery_search_back"
REAL_ID = "return"

SEARCH_PAGE = """\
<App>
  <NavigationBar name="LSTDynamicSearchView" visible="true">
    <Button name="return" label="返回" visible="true"/>
    <TextField name="search" visible="true"/>
  </NavigationBar>
</App>
"""

CASE = """\
schema_version: "0.2"
id: lst_recovery_drift_001
name: 乐速通搜索返回定位漂移恢复
suite: lst_recovery
tags: [lst, recovery, fault_injection]
precondition:
  reset: RELAUNCH
steps:
  - action: tap
    target: LSTDynamicSearchView.lst_recovery_search_back
    idempotency: IDEMPOTENT
  - wait_for:
      target: screen:LSTDynamicHomeViewController
      condition: active
      timeout: 5
"""


class SearchDriftExecutor(FakeExecutor):
    """只模拟“旧 id 找不到、`return` 找得到”的漂移语义。

    按调用序弹脚本在恢复链里会因 settle/guard 多一次 find 而假红；这里按
    策略值判定，与真机行为同构。
    """

    def find(self, strategies):
        self.find_calls += 1
        values = {s.get("value") for s in strategies}
        if values & {REAL_ID, "home_search"}:
            return El()
        raise ElementNotFound("drifted search back")

    def find_all(self, locator):
        self.find_all_calls += 1
        values = {s.get("value") for s in locator}
        if values & {REAL_ID, "home_search"}:
            return [El()]
        return []

    def tap_element(self, element):
        self.tap_calls += 1
        # 点击真实返回按钮后，运行时树切回首页。
        self._page = '<App><Node name="home_search" visible="true"/></App>'


def _repo(tmp_path):
    from repository.resolver import Repository
    root = tmp_path / "fault"
    (root / "elements").mkdir(parents=True, exist_ok=True)
    (root / "screens").mkdir(parents=True, exist_ok=True)
    (root / "elements" / "search.yaml").write_text(
        "schema_version: '1.0'\nkind: element\n"
        f"id: {DRIFT_ID}\nscreen: {SCREEN}\ntype: button\nstrategies:\n"
        f"- type: accessibility_id\n  value: {DRIFT_ID}\n  origin: manual\n"
        "metadata:\n  risk: LOW\n  idempotency: IDEMPOTENT\n"
        "  data_class: PUBLIC\n---\n"
        "schema_version: '1.0'\nkind: element\n"
        f"id: search_back\nscreen: {SCREEN}\ntype: button\nstrategies:\n"
        f"- type: accessibility_id\n  value: {REAL_ID}\n  origin: manual\n"
        "metadata:\n  risk: LOW\n  idempotency: IDEMPOTENT\n"
        "  data_class: PUBLIC\n",
        encoding="utf-8",
    )
    (root / "screens" / "search.yaml").write_text(
        "schema_version: '1.0'\nkind: screen\n"
        f"id: {SCREEN}\nmarker: {SCREEN}\nkind_hint: page\n"
        "metadata:\n  risk: LOW\n  origin: manual\n",
        encoding="utf-8",
    )
    (root / "screens" / "home.yaml").write_text(
        "schema_version: '1.0'\nkind: screen\n"
        "id: LSTDynamicHomeViewController\nmarker: home_search\n"
        "kind_hint: page\nmetadata:\n  risk: LOW\n  origin: manual\n",
        encoding="utf-8",
    )
    return Repository.from_dirs(overrides_root=str(root))


def _recovery(tmp_path, store, llm, budget):
    from agent.recovery import RecoveryEngine
    return RecoveryEngine(
        repo=_repo(tmp_path), llm=llm, budget=budget,
        experience_store=store, sleep=lambda _s: None)


def _run(tmp_path, recovery, ex):
    return run_matrix(
        tmp_path, CASE, ex=ex, repo=_repo(tmp_path), recovery=recovery,
        bundle_id=APP, app_build="local-uat")


def test_lst_recovery_drift_llm_then_experience(tmp_path):
    """同一漂移用例：先 LLM 恢复，ACCEPT 后 Experience 恢复且零 LLM。"""
    from agent.review import decide_review

    store = SQLiteExperienceStore(tmp_path / "experience.db")
    llm = FakeLLM([llm_json(value=REAL_ID)])
    budget = LLMBudget()

    run, trace, _, ex = _run(
        tmp_path / "first", _recovery(tmp_path, store, llm, budget),
        SearchDriftExecutor(page_source=SEARCH_PAGE))
    result = run.results[0]
    assert result.status == "RECOVERED" and run.exit_code == 5
    assert result.detail["recovery_kinds"] == ["llm"]
    assert result.detail["recovered_kinds"] == ["RECOVERED_LLM"]
    assert ex.tap_calls == 1

    step_detail = json.loads(trace.conn.execute(
        "SELECT detail_json FROM steps WHERE status='RECOVERED'"
    ).fetchone()["detail_json"])
    stages = step_detail["recovery"]["stages"]
    reconcile = [s for s in stages if s.get("stage") == "reconcile"]
    assert len(reconcile) == 1 and reconcile[0]["outcome"] == "DRIFT"
    assert REAL_ID in reconcile[0]["candidates"]
    assert any(s.get("stage") == "llm" and s.get("outcome") == "candidate"
               and s.get("target", {}).get("value") == REAL_ID
               for s in stages)

    reviews = trace.list_reviews()
    assert len(reviews) == 1 and reviews[0]["review_status"] == "PENDING"
    review_id = reviews[0]["review_id"]
    assert reviews[0]["candidate_target"] == REAL_ID
    assert reviews[0]["candidate_type"] == "button"

    # 人工 ACCEPT：走真实 E5 入口，不手工伪造 Candidate。
    candidate = decide_review(
        trace, review_id, "ACCEPT", "test", "乐速通恢复演练",
        experience_store=store)
    assert candidate is not None and candidate.target_id == DRIFT_ID
    assert candidate.strategy.value == REAL_ID

    second_llm = FakeLLM([])
    second_budget = LLMBudget()
    second_run, second_trace, _, second_ex = _run(
        tmp_path / "second",
        _recovery(tmp_path, store, second_llm, second_budget),
        SearchDriftExecutor(page_source=SEARCH_PAGE))
    second = second_run.results[0]
    assert second.status == "RECOVERED" and second_run.exit_code == 5
    assert second.detail["recovered_kinds"] == ["RECOVERED_EXPERIENCE"]
    assert second_llm.calls == [] and second_budget.calls_used == 0
    assert second_ex.tap_calls == 1
    assert second_trace.conn.execute(
        "SELECT COUNT(*) FROM recovery_reviews").fetchone()[0] == 0

    # 经验库留下成功样本；同一 (app, screen, target) 只消费一条 Candidate。
    [exp] = store.lookup(APP, SCREEN, DRIFT_ID)
    assert (exp.sample_count, exp.success_count) == (1, 1)
    assert exp.validated_builds == ["local-uat"]
