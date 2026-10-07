"""Task 5.5：KnowledgeSources 四查询接口集成测试（设计 19）。

设计原文：「P2 不实现规划能力，但为 P3 准备四类可查询的知识」——
experience_lookup / graph_query / impact_of / trace_history。本测试用
**真组件**集成（真 SQLite Experience Store、真 TraceStore 产生的 trace、
真 parse 的用例），验证四查询是既有模块的薄委派而不是第四套口径
（review_p2_task54 建议动作 2：直接以 impact 的两个原语为底）。

集成而非单测：四个查询的价值在「组合后的口径一致」——单测各自模块
已经覆盖，这里钉的是接缝。
"""
from __future__ import annotations

import pytest

from experience import SQLiteExperienceStore
from experience.knowledge import KnowledgeSources, P2KnowledgeSources
from experience.models import CandidateSeed, ExperienceRun, ExperienceStatus
from repository.loader import LocatorStrategy
from testcase.schema import parse_testcase_dict

APP = "com.matrix.app"


def _seed(store: SQLiteExperienceStore, *, target: str, review_id: int,
          status: ExperienceStatus = ExperienceStatus.CANDIDATE,
          value: str = "v2"):
    exp = store.create_candidate(CandidateSeed(
        review_id=review_id, recovery_id=review_id,
        seed_run_id="run_seed", seed_step_id=1,
        seed_recovery_review_id=review_id, app_id=APP,
        screen_id="HomeView", target_id=target,
        strategy=LocatorStrategy(type="accessibility_id", value=value,
                                 origin="experience")))
    for i in range(3):
        store.record_run(exp.experience_id, ExperienceRun(
            experience_id=exp.experience_id, run_id=f"r{review_id}_{i}",
            step_id=100 + i, app_build="1026", result="SUCCESS"))
    if status is not ExperienceStatus.CANDIDATE:
        store.update_status(exp.experience_id, status, "THRESHOLD_MET",
                            operator="test")
    return exp


def _case(steps: list[dict], cid: str):
    return parse_testcase_dict({
        "schema_version": "0.2", "id": cid, "name": cid, "suite": "s",
        "steps": steps})


@pytest.fixture()
def trace_db(tmp_path):
    """用真管线（run_matrix 同款装配）产一个带 steps 的 trace。"""
    from tests.fault_injection.fi_support import FakeDS, FakeExecutor, \
        run_matrix
    from tracer.storage import TraceStore

    store = TraceStore(tmp_path / "trace.db")
    # 与生产 `mta run` 同款：app_build 在 start_run 的 bi_fields 里落 runs 行
    # （read_trace_steps 的「两个 build 不许混」按它过滤）。
    store.start_run("run_knowledge", app_bundle_id=APP, app_build="1026")
    sdir = tmp_path / "suites"
    sdir.mkdir(exist_ok=True)
    (sdir / "k.yaml").write_text(
        'schema_version: "0.2"\nid: k_case\nname: k\nsuite: smoke\n'
        'steps:\n  - action: launch_app\n'
        "  - wait_for:\n      target: screen:HomeView\n"
        "      condition: active\n      timeout: 5\n"
        "  - action: tap\n    target: HomeView.login_button\n"
        "    idempotency: IDEMPOTENT\n",
        encoding="utf-8")
    from cli.pipeline import SessionPipeline, PipelineDeps
    from executor.guard import EnvKind, Guard
    from runner.lifecycle import Lifecycle
    from runner.runner import StepRunner

    pipe = SessionPipeline(suites_root=sdir, store=store, recovery=None,
                           app_id=APP, app_build="1026")
    pipe.deps = PipelineDeps(env=None, repo=None)
    pipe._step_runner = StepRunner(FakeExecutor(), FakeDS(),
                                   Guard(EnvKind.SANDBOX))
    pipe._lifecycle = Lifecycle(store=store)
    cases = pipe.discover()
    run = pipe.run_all(cases, run_id="run_knowledge")
    assert run.results and run.results[0].status == "PASS"
    return tmp_path / "trace.db"


@pytest.fixture()
def knowledge(tmp_path, trace_db):
    store = SQLiteExperienceStore(tmp_path / "experience.db")
    _seed(store, target="login_button", review_id=1,
          status=ExperienceStatus.VERIFIED)
    _seed(store, target="login_button", review_id=2)          # CANDIDATE
    _seed(store, target="old_button", review_id=3)            # 之后 REJECT
    ks = P2KnowledgeSources(experience_store=store, trace_db=trace_db,
                            app_build="1026",
                            cases=[
                                _case([{"action": "tap", "target":
                                        "HomeView.login_button",
                                        "idempotency": "IDEMPOTENT"}],
                                      "tc_login"),
                                _case([{"action": "tap", "target":
                                        "ProfileView.logout_button"}],
                                      "tc_logout"),
                            ])
    store.update_status(ks.experience_lookup(
        APP, "HomeView", "old_button")[0].experience_id,
        ExperienceStatus.REJECTED, "MANUAL_REJECT")
    return ks, store


def test_protocol_conformance(knowledge):
    ks, _ = knowledge
    assert isinstance(ks, KnowledgeSources), "设计 19 的 Protocol 形状"


def test_experience_lookup_returns_consumption_view(knowledge):
    """消费视角：lookup（REJECTED 排除）+ ranker 排序（VERIFIED > CANDIDATE）
    ——查询口径与引擎的 try_experiences 一致，不是第四种排序。"""
    ks, store = knowledge
    hits = ks.experience_lookup(APP, "HomeView", "login_button")
    assert [e.status for e in hits] == [ExperienceStatus.VERIFIED,
                                        ExperienceStatus.CANDIDATE]
    assert ks.experience_lookup(APP, "HomeView", "old_button") == [], \
        "REJECTED 不进消费视角（设计 7.1 修订）"


def test_graph_query_builds_runtime_graph_from_real_trace(knowledge):
    ks, _ = knowledge
    graph = ks.graph_query(APP)
    assert graph.app_id == APP
    assert any(n.screen_id == "HomeView" for n in graph.nodes), \
        "trace 里的屏观测进图"


def test_graph_query_rejects_unknown_app(knowledge):
    ks, _ = knowledge
    with pytest.raises(ValueError, match="app_id"):
        ks.graph_query("com.other.app")


def test_impact_of_uses_ref_index_primitives(knowledge):
    ks, _ = knowledge
    assert ks.impact_of("HomeView.login_button") == ("tc_login",)
    assert ks.impact_of("ProfileView.logout_button") == ("tc_logout",)
    assert ks.impact_of("ghost_button") == (), "索引只答「谁引用了什么」"


def test_trace_history_reads_steps_with_filters(knowledge):
    ks, _ = knowledge
    steps = ks.trace_history({})
    assert steps, "历史非空（真 trace 产出的 steps）"
    limited = ks.trace_history({"limit": 2})
    assert len(limited) == 2
    with pytest.raises(ValueError, match="未知过滤键"):
        ks.trace_history({"bogus": 1})


def test_uninitialized_trace_db_is_domain_error(tmp_path):
    """P3-2（review_p2_task55）：指向未初始化的空文件 → 可诊断的域错误，
    不是裸 `sqlite3.OperationalError: no such table`（P3 规划能力会远程
    消费本接口）。"""
    from experience.knowledge import P2KnowledgeSources

    empty = tmp_path / "empty_trace.db"
    empty.write_bytes(b"")
    ks = P2KnowledgeSources(experience_store=SQLiteExperienceStore(
        str(tmp_path / "experience.db")), trace_db=str(empty), cases=[])
    with pytest.raises(ValueError, match="trace 库不可读"):
        ks.graph_query(APP)
    with pytest.raises(ValueError, match="trace 库不可读"):
        ks.trace_history({})
