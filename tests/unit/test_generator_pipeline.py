"""Task 3.4（P3-12）：生成管线（设计 §6.1）。

逐条对应 plan 的判据：

- **矩阵 #7**：lint 不过 → 拒绝入库 + `SCHEMA_INVALID` 留痕（fail-loud）
- **语义校验**：与 Runtime Graph 的**已知观测**矛盾 → 拒绝；
  **未知屏/新元素 → `unknown` 放行**（本次拍板的口径，见 `validator.py`）
- **矩阵 #8**：指纹命中 → SKIP + **`duplicate_of` 可查**
- **矩阵 #10**：LLM `INVALID_OUTPUT` 连续 N 次 → 熔断、停止生成
- **落库**：Dry Run PASS 的候选 → `dry_run_status=PASS` / `review_status=PENDING`
- **复用红线**（plan §2 第 1 条）：`lint` / `test_fingerprint` / `LLMBudget` /
  `coverage_gap` 的「同一函数对象」或 spy 调用断言
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.storage import SQLiteAgentStore
from candidates.fingerprint import test_fingerprint as fingerprint_of
from environment.capabilities import DeviceType
from executor.guard import EnvKind
from generator.coverage import CATEGORY_HAPPY_PATH, UncoveredTransition
from generator.generator import GENERATED_BY, candidate_id_for, generate
from generator.validator import (SEMANTIC_CONSISTENT, SEMANTIC_CONTRADICTED,
                                 SEMANTIC_UNKNOWN, SemanticVerdict,
                                 validate_semantic)
from graph.models import RUNTIME, RuntimeGraph, ScreenTransition
from llm.budget import LLMBudget
from repository.resolver import Repository
from runner.result import TestcaseResult
from testcase.schema import parse_testcase_dict

APP = "com.phaset0.logindemo"

GAP = UncoveredTransition(from_screen="HomeView", to_screen="SearchView",
                          trigger="tap:HomeView.go_search", observed_count=64,
                          category=CATEGORY_HAPPY_PATH)


# --- 候选步骤夹具 -------------------------------------------------------------
#
# ⚠️ **两个到达点才推得出一条转移**：`_arrivals` 给出 `[(屏, 触发)]`，相邻两点
# 才构成 `(from, to, trigger)`。单到达点的候选**起点屏未声明** → 语义校验无从
# 下手 → `unknown`（`validator.py` 的口径）。故凡是测语义/去重的夹具都用**两段**
# 到达。


def _steps_two_arrivals(to_screen: str = "DetailView") -> list:
    """`SearchView -> <to_screen> @ tap:SearchView.search_field` 的候选步骤。"""
    return [{"action": "launch_app"},
            {"action": "tap", "target": "HomeView.go_search"},
            {"wait_for": {"target": "screen:SearchView", "condition": "active",
                          "timeout": 10}},
            {"action": "tap", "target": "SearchView.search_field"},
            {"wait_for": {"target": f"screen:{to_screen}", "condition": "active",
                          "timeout": 10}}]


def _steps_single_arrival(screen: str = "SearchView") -> list:
    return [{"action": "launch_app"},
            {"wait_for": {"target": f"screen:{screen}", "condition": "active",
                          "timeout": 10}}]


# 语义校验用的 gap（与 `_steps_two_arrivals` 声明的转移一致）
GAP2 = UncoveredTransition(from_screen="SearchView", to_screen="DetailView",
                           trigger="tap:SearchView.search_field",
                           observed_count=9, category=CATEGORY_HAPPY_PATH)

# 图里那条与 GAP2 对应的观测（语义校验判 `consistent` 用）
T_GAP2 = ScreenTransition("SearchView", "DetailView",
                          "tap:SearchView.search_field", source_of=RUNTIME,
                          count=9)


# --- 夹具 ---------------------------------------------------------------------


class FakeLLM:
    """按调用序弹脚本（与 `fi_support.FakeLLM` 同款；本文件不 import
    `tests.fault_injection`——那个包不是给单测用的）。"""

    def __init__(self, replies: list):
        self.replies = list(replies)
        self.calls: list[str] = []

    def complete(self, prompt: str, timeout: int = 120) -> str:
        self.calls.append(prompt)
        if not self.replies:
            raise AssertionError("FakeLLM 脚本耗尽——测试给的回复数不够")
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _llm_steps(steps: list) -> str:
    return json.dumps({"steps": steps}, ensure_ascii=False)


def _repo() -> Repository:
    """最小 Repository（元素来自 `repository/generated/local/` 真语料）。

    用仓库自带的 `repository/generated/local`：它是**真数据**（P1 的扫描产物），
    比自己现造一份更能证明「生成器产出的候选能过 `mta lint`」。
    """
    root = Path(__file__).resolve().parents[2]
    return Repository.from_dirs(
        generated_root=str(root / "repository" / "generated" / "local"))


class _Secrets:
    def get(self, key: str) -> str:
        return f"fake-{key}"


class _Knowledge:
    """`KnowledgeSources` 的最小替身（只用 `graph_query`）。"""

    def __init__(self, graph: RuntimeGraph):
        self.graph = graph
        self.calls = 0

    def graph_query(self, app_id: str) -> RuntimeGraph:
        self.calls += 1
        return self.graph

    def trace_history(self, filters: dict) -> list:
        return []


def _graph(*transitions: ScreenTransition) -> RuntimeGraph:
    return RuntimeGraph(app_id=APP, app_build="1026", source_of=RUNTIME,
                        transitions=transitions)


def _execute_pass(tc) -> TestcaseResult:
    return TestcaseResult(testcase_id=tc.id, status="PASS")


def _run(tmp_path, *, gaps=(GAP2,), replies, cases=(), graph=None, budget=None,
         execute=_execute_pass, env_kind=EnvKind.SANDBOX,
         device_type=DeviceType.SIMULATOR, store=None, llm=None):
    store = store or SQLiteAgentStore(tmp_path / "agent.db")
    budget = budget or LLMBudget(max_calls_per_run=10,
                                 breaker_consecutive_failures=3)
    llm = llm or FakeLLM(list(replies))
    knowledge = _Knowledge(graph if graph is not None else _graph(T_GAP2))
    result = generate(
        list(gaps), knowledge=knowledge, app_id=APP,
        existing_cases=list(cases), repository=_repo(),
        secrets=_Secrets(), store=store, llm=llm, budget=budget,
        execute=execute, env_kind=env_kind, device_type=device_type,
        suite="smoke")
    return result, store, llm, budget, knowledge


# --- 正常路径 ----------------------------------------------------------------


def test_happy_path_lands_a_candidate(tmp_path):
    """Dry Run PASS 的候选 → 库中 `dry_run_status=PASS`、`review_status=PENDING`。"""
    result, store, *_ = _run(tmp_path,
                             replies=[_llm_steps(_steps_two_arrivals())])
    assert result.ok and len(result.candidates) == 1
    c = result.candidates[0]
    assert c.review_status == "PENDING" and c.dry_run_status == "PASS"
    assert c.generated_by == GENERATED_BY
    # 落库可回查（不是只活在返回值里）
    row = store.get_candidate(c.candidate_id)
    assert row["testcase_yaml"] and row["fingerprint"]
    assert row["generation_evidence"]["coverage_gap"] == ["SearchView -> DetailView"]


def test_candidate_is_a_p1_testcase_with_provenance(tmp_path):
    """F5：Candidate Test **就是** P1 TestCase + 三个溯源字段。"""
    import yaml

    from candidates.test_case import dump_testcase_yaml
    result, store, *_ = _run(tmp_path,
                             replies=[_llm_steps(_steps_two_arrivals())])
    text = store.get_candidate(result.candidates[0].candidate_id)["testcase_yaml"]
    tc = parse_testcase_dict(yaml.safe_load(text))
    assert tc.status == "CANDIDATE"
    assert tc.generated_by == GENERATED_BY
    assert tc.generation_evidence.coverage_gap == ["SearchView -> DetailView"]
    assert tc.schema_version == "0.2"
    # 再 dump 一次仍是同一份（唯一实现，不漂移）
    assert dump_testcase_yaml(tc) == text


def test_no_gap_does_not_touch_the_graph(tmp_path):
    """空 gap → 不生成、**不读图**（与 `planner.plan` 的空 Plan 同款）。"""
    result, store, llm, budget, knowledge = _run(tmp_path, gaps=(), replies=[])
    assert result.candidates == [] and result.rejections == []
    assert knowledge.calls == 0, "没有 gap 就不该读图（读图 = 全表扫 trace）"
    assert any("没有 coverage gap" in n for n in result.notes)
    assert llm.calls == []


def test_missing_llm_or_budget_is_reported_not_silent(tmp_path):
    """只给 llm 不给 budget → 不调用（与 `planner.plan` 的门槛逐字一致）。"""
    store = SQLiteAgentStore(tmp_path / "agent.db")
    result = generate([GAP2], knowledge=_Knowledge(_graph(T_GAP2)), app_id=APP,
                      existing_cases=[], repository=_repo(),
                      secrets=_Secrets(), store=store,
                      llm=FakeLLM([]), budget=None, execute=_execute_pass,
                      env_kind=EnvKind.SANDBOX,
                      device_type=DeviceType.SIMULATOR)
    assert result.candidates == []
    assert any("未提供 llm+budget" in n for n in result.notes)


# --- 矩阵 #7：Schema/lint 不过 → 拒绝入库 -------------------------------------


def test_lint_error_rejects_and_leaves_a_trace(tmp_path):
    """矩阵 #7：`fail-loud`，不静默丢弃——拒绝理由落在留痕表里。"""
    # `SearchView.nope_button` 不存在 → lint ERROR（unknown_target）
    result, store, *_ = _run(tmp_path, replies=[_llm_steps([
        {"action": "launch_app"},
        {"action": "tap", "target": "SearchView.nope_button"}])])
    assert result.candidates == [], "lint 不过的候选不许入库"
    assert store.list_candidates() == []
    rejs = store.list_rejections(reason="SCHEMA_INVALID")
    assert len(rejs) == 1
    assert rejs[0]["candidate_id"] == candidate_id_for(GAP2)
    assert any(i["code"] == "unknown_target"
               for i in rejs[0]["detail"]["issues"])


def test_schema_invalid_steps_reject_before_lint(tmp_path):
    """步骤形态过不了 P1 schema（`input` 缺 value）→ 同样留痕、不入库。"""
    result, store, *_ = _run(tmp_path, replies=[_llm_steps([
        {"action": "launch_app"},
        {"action": "input", "target": "SearchView.search_field"}])])
    assert result.candidates == []
    rejs = store.list_rejections(reason="SCHEMA_INVALID")
    assert len(rejs) == 1 and "error" in rejs[0]["detail"]


# --- 语义校验 -----------------------------------------------------------------


def test_contradiction_with_known_observation_is_rejected(tmp_path):
    """图对同一 (from, trigger) 有观测，且落点**不含**候选声明的 to → 拒绝。"""
    graph = _graph(ScreenTransition("SearchView", "ProfileView",
                                    "tap:SearchView.search_field",
                                    source_of=RUNTIME, count=9))
    result, store, *_ = _run(
        tmp_path, graph=graph,
        replies=[_llm_steps(_steps_two_arrivals("DetailView"))])
    assert result.candidates == []
    rejs = store.list_rejections(reason="SEMANTIC_REJECTED")
    assert len(rejs) == 1
    assert "SearchView->ProfileView@tap:SearchView.search_field" in \
        rejs[0]["detail"]["observed"]


def test_unknown_transition_passes_to_dry_run(tmp_path):
    """📌 **口径**：图对 (from, trigger) **零观测** → 放行（未知屏/新元素）。"""
    result, store, *_ = _run(
        tmp_path, graph=_graph(),        # 空图：所有路径都是「未知」
        replies=[_llm_steps(_steps_two_arrivals())])
    assert len(result.candidates) == 1, "未知路径必须放行交给 Dry Run"
    assert store.list_rejections() == []


def test_single_arrival_candidate_is_unknown_not_consistent(tmp_path):
    """⚠️ 只声明一个到达点 → 推不出转移 → `unknown`（**不是** `consistent`）。

    `consistent` 的判词是「声明的转移全部有观测支持」——在**空集**上恒真，
    于是「只声明了一个屏」的候选会得到一句空的「校验通过」，审计上看不出
    它其实**没被校验过**。
    """
    tc = parse_testcase_dict({"schema_version": "0.2", "id": "c", "name": "c",
                              "steps": _steps_single_arrival()})
    v = validate_semantic(tc, graph=_graph(T_GAP2))
    assert v.status == SEMANTIC_UNKNOWN
    assert "到达点" in v.reason


def test_contradiction_is_not_masked_by_an_earlier_unknown_transition():
    """⚠️ **P1-1 的钉子**：矛盾转移排在零观测转移**之后**，仍必须被拒。

    初版逐条短路（遇零观测即 `return unknown`），结论取决于 `sorted(declared)`
    的**字典序**——把第一条转移的屏名从 `HomeView` 换成 `ZHomeView`（两者都
    零观测），后面那条**已知矛盾**的转移就整个不被检查。真语料 20 例里 13 例
    有声明转移、`sorted-first` 全是 `HomeView` 打头，即真实形态天然命中。
    """
    def _tc(first_screen: str) -> TestCase:
        return parse_testcase_dict({
            "schema_version": "0.2", "id": "c", "name": "c", "steps": [
                {"action": "launch_app"},
                {"wait_for": {"target": f"screen:{first_screen}",
                              "condition": "active", "timeout": 5}},
                {"action": "tap", "target": f"{first_screen}.go"},
                {"wait_for": {"target": "screen:MidView", "condition": "active",
                              "timeout": 5}},
                {"action": "tap", "target": "MidView.x"},
                {"wait_for": {"target": "screen:ProfileView",
                              "condition": "active", "timeout": 5}}]})

    # 图里 MidView --tap:MidView.x--> DetailView（**不是** ProfileView）
    graph = _graph(ScreenTransition("MidView", "DetailView", "tap:MidView.x",
                                    source_of=RUNTIME, count=4))
    # `ZHomeView` 字典序在 `MidView` **之后**……两种排法都要拦得住
    for first in ("HomeView", "ZHomeView"):
        v = validate_semantic(_tc(first), graph=graph)
        assert v.status == SEMANTIC_CONTRADICTED, (
            f"first={first}：矛盾被前面的零观测转移掩蔽了（P1-1 回归）")
        # `observed` 装的是**图里的实测落点**（拒绝时要让人看到图说什么）
        assert "MidView->DetailView@tap:MidView.x" in v.observed
        assert "MidView->ProfileView@tap:MidView.x" in v.reason


def test_unknown_is_reported_only_when_nothing_contradicts():
    """反向：**全部**转移零观测 → `unknown`（P1-1 的修订不能把放行也堵死）。"""
    tc = parse_testcase_dict({
        "schema_version": "0.2", "id": "c", "name": "c", "steps": [
            {"action": "launch_app"},
            {"wait_for": {"target": "screen:HomeView", "condition": "active",
                          "timeout": 5}},
            {"action": "tap", "target": "HomeView.go_search"},
            {"wait_for": {"target": "screen:SearchView", "condition": "active",
                          "timeout": 5}}]})
    v = validate_semantic(tc, graph=_graph(T_GAP2))     # 图里没有 HomeView 的观测
    assert v.status == SEMANTIC_UNKNOWN


def test_validate_semantic_rejects_source_graph():
    """源图转移恒空 → 静默接受它会让判据**恒判 consistent**（与 coverage_gap 同款）。"""
    from graph.models import SOURCE
    tc = parse_testcase_dict({"schema_version": "0.2", "id": "c", "name": "c",
                              "steps": _steps_two_arrivals()})
    src = RuntimeGraph(app_id=APP, app_build="1026", source_of=SOURCE)
    with pytest.raises(ValueError):
        validate_semantic(tc, graph=src)


# --- 矩阵 #8：指纹去重 --------------------------------------------------------


def test_duplicate_of_existing_case_is_skipped_with_pointer(tmp_path):
    """矩阵 #8 + handover §9 前置 #1：SKIP 必须带上**撞的是哪一条**。"""
    existing = parse_testcase_dict({
        "schema_version": "0.2", "id": "search_001", "name": "搜索",
        "steps": _steps_two_arrivals()})
    result, store, *_ = _run(
        tmp_path, cases=[existing],
        replies=[_llm_steps(_steps_two_arrivals())])
    assert result.candidates == [], "撞指纹的候选必须 SKIP（不重复入库）"
    rejs = store.list_rejections(reason="DUPLICATE")
    assert len(rejs) == 1
    assert rejs[0]["duplicate_of"] == "search_001", \
        "SKIP 留痕必须指名撞上的是哪一条（否则人工看不见被跳过的是什么）"
    assert rejs[0]["fingerprint"] == fingerprint_of(existing)


def test_duplicate_within_the_same_batch_is_skipped(tmp_path):
    """同批次内的重复也要挡：第一条入库后立刻进基准。"""
    two = (GAP2, UncoveredTransition(from_screen="SearchView",
                                     to_screen="DetailView",
                                     trigger="tap:SearchView.other_field",
                                     observed_count=1))
    result, store, *_ = _run(tmp_path, gaps=two,
                             graph=_graph(T_GAP2),
                             replies=[_llm_steps(_steps_two_arrivals()),
                                      _llm_steps(_steps_two_arrivals())])
    assert len(result.candidates) == 1
    rejs = store.list_rejections(reason="DUPLICATE")
    assert len(rejs) == 1
    assert rejs[0]["duplicate_of"] == result.candidates[0].candidate_id


def test_pending_candidate_blocks_a_new_one(tmp_path):
    """未决候选也进去重基准（已决的不挡——Task 3.5 的审核会消费它们）。"""
    store = SQLiteAgentStore(tmp_path / "agent.db")
    _run(tmp_path, replies=[_llm_steps(_steps_two_arrivals())], store=store)
    # 第二条 gap（不同 id）产出**同一条用例** → 撞上未决候选
    gap2 = UncoveredTransition(from_screen="SearchView", to_screen="DetailView",
                               trigger="tap:SearchView.other_field",
                               observed_count=1)
    result, *_ = _run(tmp_path, gaps=(gap2,),
                      replies=[_llm_steps(_steps_two_arrivals())], store=store)
    assert result.candidates == []
    rejs = store.list_rejections(reason="DUPLICATE")
    assert len(rejs) == 1 and rejs[0]["duplicate_of"].startswith("cand_")


def test_accepted_candidate_does_not_block(tmp_path):
    """已 ACCEPT 的候选不再挡住新候选（`review_status='PENDING'` 才进基准）。"""
    store = SQLiteAgentStore(tmp_path / "agent.db")
    _run(tmp_path, replies=[_llm_steps(_steps_two_arrivals())], store=store)
    with store._write_tx() as conn:
        conn.execute("UPDATE test_candidates SET review_status='ACCEPT'")
    gap2 = UncoveredTransition(from_screen="SearchView", to_screen="DetailView",
                               trigger="tap:SearchView.other_field",
                               observed_count=1)
    result, *_ = _run(tmp_path, gaps=(gap2,),
                      replies=[_llm_steps(_steps_two_arrivals())], store=store)
    assert len(result.candidates) == 1
    assert store.list_rejections(reason="DUPLICATE") == []


# --- AE 第 2 层（handover §9 前置 2）：已决候选不被覆写 -----------------------


def test_generate_skips_a_decided_candidate_and_keeps_its_yaml(tmp_path):
    """**同一条 gap** 重跑 → 已人工决策的候选**逐字节不变**，且不烧 LLM 预算。

    与 `test_accepted_candidate_does_not_block` 的关键差别：那条换了一条 gap
    （新 id），**测不到覆写**；本条的 gap 完全相同 → `candidate_id_for(gap)`
    算出的 id 相同 → 走 upsert 的 `ON CONFLICT` 分支，正是探针 AE 的缺陷形态
    （改 `testcase_yaml` 而 `review_status` 保持 ACCEPT → 落盘文件与库内容分叉）。

    两层分别可测：本测试钉**第 2 层**（generate 预过滤，`notes` 留痕 + 不调
    LLM）；第 1 层（storage 闸门 fail-loud）由
    `test_candidate_store.py::test_save_candidate_refuses_overwriting_a_decided_candidate`
    钉住。删掉第 2 层 → 第 1 层的 `ValueError` 冒到 `generate` 外 → 本测试红；
    删掉第 1 层 → 上面那条红。
    """
    store = SQLiteAgentStore(tmp_path / "agent.db")
    first, *_ = _run(tmp_path, replies=[_llm_steps(_steps_two_arrivals())],
                     store=store)
    cid = first.candidates[0].candidate_id
    original_yaml = store.get_candidate(cid)["testcase_yaml"]
    store.decide_candidate(cid, review_status="ACCEPT", reviewer="op")

    # 同 gap 重跑，LLM 这次会给出**不同**的步骤——若被覆写，yaml 必变
    result, _, llm, budget, _ = _run(
        tmp_path, replies=[_llm_steps(_steps_single_arrival())], store=store)
    assert result.candidates == [], "已决候选不该再被当成新候选入库"
    assert store.get_candidate(cid)["testcase_yaml"] == original_yaml, \
        "已 ACCEPT 候选的 testcase_yaml 被覆写了（AE）"
    assert store.get_candidate(cid)["review_status"] == "ACCEPT"
    assert llm.calls == [], "已决 gap 不该消耗 LLM 调用"
    assert budget.calls_used == 0, "已决 gap 不该消耗预算"
    assert any("已人工决策" in n and cid in n for n in result.notes), \
        f"跳过必须在 notes 里可见（不静默）：{result.notes}"


def test_generate_skips_a_rejected_candidate_too(tmp_path):
    """REJECT 同样跳过——已决是「人工给过结论」，与 ACCEPT/REJECT 无关。"""
    store = SQLiteAgentStore(tmp_path / "agent.db")
    first, *_ = _run(tmp_path, replies=[_llm_steps(_steps_two_arrivals())],
                     store=store)
    cid = first.candidates[0].candidate_id
    store.decide_candidate(cid, review_status="REJECT", reviewer="op",
                           note="与既有用例重复")
    result, _, llm, *_ = _run(tmp_path,
                              replies=[_llm_steps(_steps_two_arrivals())],
                              store=store)
    assert result.candidates == [] and llm.calls == []
    assert store.get_candidate(cid)["review_note"] == "与既有用例重复"
    assert any("REJECT" in n for n in result.notes)


# --- 矩阵 #10：INVALID_OUTPUT 熔断 --------------------------------------------


def test_invalid_output_trips_the_breaker_and_stops(tmp_path):
    """连续 N 次非法输出 → 熔断、**停止生成**（不重试、不自由发挥）。"""
    gaps = tuple(UncoveredTransition(from_screen="A", to_screen=f"B{i}",
                                     trigger=f"tap:A.g{i}", observed_count=1)
                 for i in range(6))
    result, store, llm, budget, _ = _run(
        tmp_path, gaps=gaps,
        replies=["not json at all"] * 6,
        budget=LLMBudget(max_calls_per_run=100, breaker_consecutive_failures=3))
    assert result.broken and budget.broken
    assert result.candidates == [] and store.list_candidates() == []
    assert len(llm.calls) == 3, "熔断后不许再调 LLM（第 4 次都不该发生）"
    assert any("熔断" in n for n in result.notes)


def test_contract_valid_but_schema_invalid_resets_the_breaker(tmp_path):
    """契约层合法（`parse_candidate_steps` 过）→ `record_success`。

    即「模型给的步骤长得对，只是过不了 lint」**不算** INVALID_OUTPUT——
    否则一个爱编 target 的模型会把熔断器打穿，而它的输出其实是可解析的。
    """
    budget = LLMBudget(max_calls_per_run=100, breaker_consecutive_failures=2)
    bad_target = _llm_steps([{"action": "launch_app"},
                             {"action": "tap", "target": "SearchView.nope"}])
    gaps = tuple(UncoveredTransition("S", f"D{i}", f"tap:S.g{i}", 1)
                 for i in range(3))
    result, store, llm, budget, _ = _run(tmp_path, gaps=gaps,
                                         replies=[bad_target] * 3,
                                         budget=budget)
    assert not budget.broken, "schema 层拒绝不该触发熔断（契约是合法的）"
    assert len(llm.calls) == 3
    assert len(store.list_rejections(reason="SCHEMA_INVALID")) == 3


def test_budget_exhaustion_stops_generation(tmp_path):
    """预算耗尽（非熔断）→ 停止 + 如实报告，`broken` 保持 False。

    用**真元素**（`repository/generated/local`）与**两段到达**，让前两条候选
    真的能入库——否则这条测试证明不了「预算只放行 N 条」，只证明了「全都
    被别的判据挡下了」。
    """
    def steps(i: int) -> list:
        return [{"action": "launch_app"},
                {"action": "tap", "target": "HomeView.go_search"},
                {"wait_for": {"target": "screen:SearchView",
                              "condition": "active", "timeout": 10}},
                {"action": "tap", "target": f"SearchView.search_field"},
                {"assertion": {"target": "SearchView.search_results",
                               "condition": "element_count", "expected": i,
                               "timeout": 5}}]
    gaps = tuple(UncoveredTransition("SearchView", "DetailView",
                                     f"tap:SearchView.field{i}", 1)
                 for i in range(4))
    result, store, llm, budget, _ = _run(
        tmp_path, gaps=gaps, graph=_graph(),
        replies=[_llm_steps(steps(i)) for i in range(4)],
        budget=LLMBudget(max_calls_per_run=2,
                         breaker_consecutive_failures=5))
    assert len(llm.calls) == 2 and len(result.candidates) == 2
    assert not result.broken
    assert any("预算耗尽" in n for n in result.notes)


def test_llm_provider_error_counts_as_failure(tmp_path):
    """网关/网络错误也是「这次调用没成功」→ `record_failure`（不建候选）。"""
    budget = LLMBudget(max_calls_per_run=10, breaker_consecutive_failures=2)
    result, store, llm, budget, _ = _run(
        tmp_path, gaps=(GAP2, UncoveredTransition("S", "D", "tap:S.g", 1)),
        replies=[RuntimeError("gateway 502"), RuntimeError("gateway 502")],
        budget=budget)
    assert result.broken
    assert result.candidates == [] and result.rejections == []


# --- 矩阵 #9 的端到端：Dry Run 拒绝时不落候选 --------------------------------


@pytest.mark.parametrize("env_kind,device_type", [
    (EnvKind.PRODUCTION, DeviceType.SIMULATOR),
    (EnvKind.STAGING, DeviceType.SIMULATOR),
    (EnvKind.SANDBOX, DeviceType.REAL_DEVICE),
])
def test_refused_dry_run_does_not_land_a_candidate(tmp_path, env_kind,
                                                   device_type):
    """矩阵 #9 的接线面：闸门抛 → 生成器**不落候选**（异常穿出，不静默吞）。

    ⚠️ 这里断言的是「抛」而不是「静默跳过」：Dry Run 被拒是**调用方的配置
    错误**（拿非 sandbox 跑生成），静默跳过会让整批候选无声消失。
    """
    from generator.dry_run import DryRunRefused
    with pytest.raises(DryRunRefused):
        _run(tmp_path, replies=[_llm_steps(_steps_two_arrivals())],
             env_kind=env_kind, device_type=device_type)


def test_dry_run_fail_still_lands_the_candidate(tmp_path):
    """Dry Run **跑了但没过** → 候选照常入库，`dry_run_status=FAIL`。

    与上一条成对：拒绝（没跑）抛异常，失败（跑了没过）入库留痕——
    人工审核要看到「这条候选跑挂了」，那正是它该被看一眼的理由。
    """
    def execute(tc):
        return TestcaseResult(testcase_id=tc.id, status="FAIL",
                              failure_type="ELEMENT_NOT_FOUND")
    result, store, *_ = _run(tmp_path, execute=execute,
                             replies=[_llm_steps(_steps_two_arrivals())])
    assert len(result.candidates) == 1
    assert result.candidates[0].dry_run_status == "FAIL"
    assert store.get_candidate(
        result.candidates[0].candidate_id)["dry_run_status"] == "FAIL"


# --- 复用红线（plan §2 第 1 条） ----------------------------------------------


def test_validate_schema_is_the_same_lint_function():
    """F5：「直接复用 mta lint」——**同一函数对象**，不是「结果看起来一致」。"""
    import generator.validator as gv
    import testcase.lint as tl
    assert gv.lint is tl.lint


def test_generator_calls_the_shared_fingerprint(tmp_path, monkeypatch):
    """F8：去重必须**真的调用** `candidates.fingerprint.test_fingerprint`。"""
    import generator.generator as gg
    seen = []
    real = gg.test_fingerprint

    def spy(tc):
        seen.append(tc.id)
        return real(tc)

    monkeypatch.setattr(gg, "test_fingerprint", spy)
    _run(tmp_path, replies=[_llm_steps(_steps_two_arrivals())])
    assert seen, "生成器没真的调用 test_fingerprint（去重判据是空的）"


def test_candidate_id_uses_the_shared_hash_primitive():
    """`candidate_id_for` 复用 `source/hashing.py::stable_hash`（F8 的同一原语）。"""
    import generator.generator as gg
    import source.hashing as sh
    assert gg.stable_hash is sh.stable_hash
    # 内容寻址：同 gap 必得同 id（`save_candidate` 是 upsert，靠这个幂等）
    assert candidate_id_for(GAP2) == candidate_id_for(GAP2)
    other = UncoveredTransition("SearchView", "DetailView",
                                "tap:SearchView.other_field", 1)
    assert candidate_id_for(GAP2) != candidate_id_for(other)


def test_generator_reuses_coverage_trigger_shape():
    """语义校验与覆盖计算共用 `_arrivals` 的推导（同一套逻辑只许一处实现）。"""
    import generator.coverage as gc
    import generator.validator as gv
    assert gv._arrivals is gc._arrivals


def test_budget_is_the_p1_class(tmp_path):
    """F4：熔断**复用** P1 的 `LLMBudget`，不新写断路器。"""
    import llm.budget as lb
    budget = lb.LLMBudget(max_calls_per_run=1, breaker_consecutive_failures=1)
    _run(tmp_path, replies=[_llm_steps(_steps_two_arrivals())], budget=budget)
    assert budget.calls_used == 1


def test_semantic_verdict_statuses_are_distinct():
    """`unknown` 与 `consistent` **都放行**，但在审计里是两件事（不是 bool）。"""
    tc = parse_testcase_dict({"schema_version": "0.2", "id": "c", "name": "c",
                              "steps": _steps_two_arrivals()})
    assert validate_semantic(tc, graph=_graph()).status == SEMANTIC_UNKNOWN
    assert validate_semantic(tc, graph=_graph(T_GAP2)).status == \
        SEMANTIC_CONSISTENT
    assert len({SEMANTIC_UNKNOWN, SEMANTIC_CONSISTENT,
                SEMANTIC_CONTRADICTED}) == 3
    with pytest.raises(ValueError):
        SemanticVerdict(status="maybe")


# --- 评审修订（review_p3_task34）：P1-2 / P2-2 / P3-4 的钉子 --------------------


def test_model_declared_risk_is_ignored_but_visible(tmp_path):
    """H4 / P1-2：模型越权声明 `risk` → **被忽略**（不是被采纳），但**必须可见**。

    初版解析层正确算出 `ignored_fields`，生成器却零消费——`risk: CRITICAL`
    就这么静默消失。忽略它是对的（风险由 metadata + Guard 判），但人工审核
    时看不到「这条候选的 LLM 试图声明过 CRITICAL」，而那正是最该看一眼的信号。
    """
    steps = [{"action": "launch_app", "risk": "CRITICAL",
              "idempotency": "IDEMPOTENT"}]
    steps += _steps_two_arrivals()
    result, store, *_ = _run(tmp_path, replies=[_llm_steps(steps)])
    assert len(result.candidates) == 1, "越权字段不该让候选作废"
    yaml_text = store.get_candidate(result.candidates[0].candidate_id)[
        "testcase_yaml"]
    assert "CRITICAL" not in yaml_text, "模型声明的 risk 必须被剥掉（H4）"
    assert any("越权声明" in n and "CRITICAL" in n for n in result.notes), \
        f"越权声明必须留在 notes 里可见（P1-2 回归）：{result.notes}"


def test_nested_spec_unknown_keys_are_stripped_and_visible(tmp_path):
    """P2-4：嵌套 spec（`wait_for` / `assertion` / `postcondition`）里的未知键
    同样要被剥掉并记进 `ignored_fields`（H4 在嵌套层也成立）。

    初版把模型的**原 dict** 直接 return——拦得住是**巧合**（P1 schema 的
    `extra="forbid"` 兜住），而「靠下游兜住」正是本仓反复吃亏的形态。
    """
    from llm.parser import parse_candidate_steps
    raw = json.dumps({"steps": [
        {"wait_for": {"target": "screen:SearchView", "condition": "active",
                      "risk": "HIGH", "goto": "x"}}]})
    p = parse_candidate_steps(raw)
    assert p.valid
    assert p.steps[0]["wait_for"] == {"target": "screen:SearchView",
                                      "condition": "active"}
    # 键路径带外层步序前缀（`steps[i].`）——与顶层的记录口径一致
    assert p.ignored_fields["steps[0].wait_for.risk"] == "HIGH"
    assert p.ignored_fields["steps[0].wait_for.goto"] == "x"


def test_nested_postcondition_unknown_keys_are_stripped():
    """P2-4 的 `postcondition` 分支（与观测步同款，但是另一个 model）。"""
    from llm.parser import parse_candidate_steps
    raw = json.dumps({"steps": [
        {"action": "tap", "target": "HomeView.go_search",
         "postcondition": {"target": "screen:SearchView",
                           "condition": "active", "risk": "HIGH"}}]})
    p = parse_candidate_steps(raw)
    assert p.valid
    assert "risk" not in p.steps[0]["postcondition"]
    assert p.ignored_fields["steps[0].postcondition.risk"] == "HIGH"


def test_dry_run_detail_is_persisted_not_just_the_status(tmp_path):
    """P2-2：`dry_run_status='FAIL'` 旁边必须有**为什么**失败。

    只落一个 FAIL 是「看起来正常」——人工审核看到 FAIL 却不知道失败类型，
    等于把一次设备执行的结论丢掉一半。
    """
    def _fail(tc):
        return TestcaseResult(testcase_id=tc.id, status="FAIL",
                              failure_type="WAIT_TIMEOUT", attempt=2,
                              cleanup_status="OK")

    result, store, *_ = _run(tmp_path, execute=_fail,
                             replies=[_llm_steps(_steps_two_arrivals())])
    row = store.get_candidate(result.candidates[0].candidate_id)
    assert row["dry_run_status"] == "FAIL"
    assert row["dry_run_detail"]["failure_type"] == "WAIT_TIMEOUT"
    assert row["dry_run_detail"]["attempt"] == 2
    assert row["dry_run_detail"]["cleanup_status"] == "OK"


def test_existing_cases_iterable_is_materialized_once(tmp_path):
    """P3-4：`existing_cases` 传一次性可迭代对象时，审计 note 的数字不许撒谎。

    `generate` 要遍历它**两次**（建指纹索引 + 报条数）。传生成器时第二次遍历
    恒为空 → note 写「正式用例 0 条」而实际有 N 条（去重判据本身仍对，因为
    `fp_index` 第一次就建好了）——审计数字撒谎正是「看起来正常」。
    """
    existing = parse_testcase_dict({"schema_version": "0.2", "id": "existing_001",
                                    "name": "x",
                                    "steps": _steps_two_arrivals()})
    # ⚠️ **不能走 `_run`**：那个 helper 自己写了 `list(cases)`，会在
    # `generate` 看到它之前就物化掉——变异探针实测 0 red，即这条测试当时
    # 根本没碰「一次性可迭代」这个形态（`_run` 的 list() 把被测行为洗掉了）。
    # 直接调 `generate` 并传**生成器**。
    from generator.generator import generate
    store = SQLiteAgentStore(tmp_path / "agent.db")
    result = generate(
        [GAP2], knowledge=_Knowledge(_graph(T_GAP2)), app_id=APP,
        existing_cases=(c for c in [existing]),       # 一次性可迭代
        repository=_repo(), secrets=_Secrets(), store=store,
        llm=FakeLLM([_llm_steps(_steps_two_arrivals())]),
        budget=LLMBudget(max_calls_per_run=10, breaker_consecutive_failures=3),
        execute=_execute_pass, env_kind=EnvKind.SANDBOX,
        device_type=DeviceType.SIMULATOR, suite="smoke")
    assert len(store.list_rejections(reason="DUPLICATE")) == 1, "去重仍须生效"
    assert any("正式用例 1 条" in n for n in result.notes), \
        f"去重基准的数字必须如实（P3-4 回归）：{result.notes}"


def test_last_gap_contract_failure_still_reports_broken(tmp_path):
    """P3-4 附注：熔断**恰好发生在最后一条 gap** 时 `result.broken` 仍为真。

    `test_invalid_output_trips_the_breaker_and_stops` 用 6 个 gap / 阈值 3，
    `try_acquire` 那一支会先命中——走不到「最后一次迭代才熔断」这条路径，
    而它恰恰是「收尾重读 `budget.broken`」唯一的用武之地。
    """
    budget = LLMBudget(max_calls_per_run=100, breaker_consecutive_failures=1)
    result, *_ = _run(tmp_path, gaps=(GAP2,), replies=["不是 JSON"],
                      budget=budget)
    assert budget.broken is True
    assert result.broken is True, "最后一次迭代熔断 → 报告必须说熔断了"
    assert any("熔断" in n for n in result.notes)
