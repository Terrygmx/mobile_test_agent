"""P3 故障注入矩阵 #7–#10（M3；Task 3.4）。

| # | 场景 | 预期 | 约束 |
|---|---|---|---|
| 7 | 候选 Schema/lint 不过 → **拒绝入库** `test_candidates`（fail-loud，不静默丢弃） | 拒绝 + `test_candidate_rejections` 留痕 | F5 |
| 8 | Test Fingerprint 命中已有用例/未决候选 → SKIP **且留痕**（skip 原因可查） | 不重复入库 + `duplicate_of` 指名撞上的是谁 | F8 |
| 9 | Dry Run 在非 sandbox（或 real_device）请求 → **拒绝执行** | 抛 `DryRunRefused`（≠「跑了没过」） | F7 |
| 10 | LLM `INVALID_OUTPUT` 连续 N 次 → 熔断（复用 P1 breaker），不自由发挥 | 停止生成 + `budget.broken` | F4 |

## ⚠️ 编号撞车（沿用 `test_p2_matrix_m2.py` 的登记）

**#7–#10 在 P1 的 24 行矩阵里已被占用**（`test_matrix_01_12.py` 的 LLM 风险 /
非法 JSON / 预算那几行）。本文件的 #7–#10 是 **P3 矩阵**（`docs/phase3-plan.md`
§74-95）的编号，语义完全不同——读编号时先看文件，别看数字。

## 与单测的分工

细粒度判据在 `tests/unit/test_generator_pipeline.py` / `test_dry_run.py`
（夹具可控、覆盖分支）。本文件是**矩阵行**：每行用**真 Repository 语料**跑一遍
端到端，证明「判据在真实数据上成立」，而不是只在造的替身上成立。
"""
from __future__ import annotations

import json

import pytest

from agents.storage import SQLiteAgentStore
from candidates.fingerprint import test_fingerprint as fingerprint_of
from environment.capabilities import DeviceType
from executor.guard import EnvKind
from generator.coverage import CATEGORY_HAPPY_PATH, UncoveredTransition
from generator.dry_run import DryRunRefused
from generator.generator import generate
from graph.models import RUNTIME, RuntimeGraph, ScreenTransition
from llm.budget import LLMBudget
from repository.resolver import Repository
from runner.result import TestcaseResult
from testcase.schema import parse_testcase_dict

APP = "com.phaset0.logindemo"
_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]

# 一条真语料上成立的候选（两段到达 → 声明 `SearchView -> DetailView` 转移）
GOOD_STEPS = [{"action": "launch_app"},
              {"action": "tap", "target": "HomeView.go_search"},
              {"wait_for": {"target": "screen:SearchView", "condition": "active",
                            "timeout": 10}},
              {"action": "tap", "target": "SearchView.search_field"},
              {"wait_for": {"target": "screen:DetailView", "condition": "active",
                            "timeout": 10}}]

GAP = UncoveredTransition(from_screen="SearchView", to_screen="DetailView",
                          trigger="tap:SearchView.search_field",
                          observed_count=9, category=CATEGORY_HAPPY_PATH)

T_GAP = ScreenTransition("SearchView", "DetailView",
                         "tap:SearchView.search_field", source_of=RUNTIME,
                         count=9)


class _LLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def complete(self, prompt, timeout=120):
        self.calls += 1
        item = self.replies.pop(0) if self.replies else "{}"
        if isinstance(item, Exception):
            raise item
        return item


class _Secrets:
    def get(self, key):
        return f"fake-{key}"


class _Knowledge:
    def __init__(self, graph):
        self.graph = graph

    def graph_query(self, app_id):
        return self.graph

    def trace_history(self, filters):
        return []


def _repo():
    return Repository.from_dirs(
        generated_root=str(_ROOT / "repository" / "generated" / "local"))


def _run(tmp_path, *, replies, gaps=(GAP,), cases=(), graph=None, budget=None,
         execute=None, env_kind=EnvKind.SANDBOX,
         device_type=DeviceType.SIMULATOR, store=None):
    store = store or SQLiteAgentStore(tmp_path / "agent.db")
    budget = budget or LLMBudget(max_calls_per_run=10,
                                 breaker_consecutive_failures=3)
    llm = _LLM(list(replies))
    result = generate(
        list(gaps), knowledge=_Knowledge(graph if graph is not None
                                         else RuntimeGraph(
                                             app_id=APP, app_build="1026",
                                             source_of=RUNTIME,
                                             transitions=(T_GAP,))),
        app_id=APP, existing_cases=list(cases), repository=_repo(),
        secrets=_Secrets(), store=store, llm=llm, budget=budget,
        execute=execute or (lambda tc: TestcaseResult(testcase_id=tc.id,
                                                      status="PASS")),
        env_kind=env_kind, device_type=device_type, suite="smoke")
    return result, store, llm, budget


def _steps_json(steps):
    return json.dumps({"steps": steps}, ensure_ascii=False)


# --- #7：Schema/lint 不过 → 拒绝入库（fail-loud） -----------------------------


def test_matrix_7_lint_error_is_rejected_and_recorded(tmp_path):
    """矩阵 #7（F5）：`unknown_target` 是 lint ERROR → 不入库 + 留痕可查。"""
    result, store, *_ = _run(tmp_path, replies=[_steps_json(
        [{"action": "launch_app"},
         {"action": "tap", "target": "SearchView.does_not_exist"}])])
    assert store.list_candidates() == [], "lint ERROR 的候选进了库"
    rejs = store.list_rejections(reason="SCHEMA_INVALID")
    assert len(rejs) == 1
    assert any(i["code"] == "unknown_target"
               for i in rejs[0]["detail"]["issues"])
    assert result.candidates == []


def test_matrix_7_schema_invalid_is_rejected_and_recorded(tmp_path):
    """矩阵 #7：连 P1 schema 都过不了（`input` 缺 value）→ 同样 fail-loud。"""
    result, store, *_ = _run(tmp_path, replies=[_steps_json(
        [{"action": "launch_app"},
         {"action": "input", "target": "SearchView.search_field"}])])
    assert store.list_candidates() == []
    assert len(store.list_rejections(reason="SCHEMA_INVALID")) == 1


def test_matrix_7_rejection_is_never_silent(tmp_path):
    """**不静默丢弃**是矩阵 #7 的全部要点：被拒的条数必须与留痕对得上。"""
    bad = _steps_json([{"action": "launch_app"},
                       {"action": "tap", "target": "SearchView.nope"}])
    gaps = tuple(UncoveredTransition("SearchView", "DetailView",
                                     f"tap:SearchView.f{i}", 1)
                 for i in range(3))
    result, store, *_ = _run(tmp_path, gaps=gaps, replies=[bad] * 3)
    assert len(store.list_rejections()) == 3
    assert len(result.rejections) == 3


# --- #8：指纹命中 → SKIP 且留痕 ----------------------------------------------


def test_matrix_8_fingerprint_hit_on_existing_case_is_skipped(tmp_path):
    """矩阵 #8（F8）：命中**已有正式用例** → SKIP + 指名撞上的是哪一条。"""
    existing = parse_testcase_dict({"schema_version": "0.2", "id": "search_001",
                                    "name": "搜索", "steps": GOOD_STEPS})
    result, store, *_ = _run(tmp_path, cases=[existing],
                             replies=[_steps_json(GOOD_STEPS)])
    assert store.list_candidates() == []
    rejs = store.list_rejections(reason="DUPLICATE")
    assert len(rejs) == 1
    assert rejs[0]["duplicate_of"] == "search_001"
    assert rejs[0]["fingerprint"] == fingerprint_of(existing)


def test_matrix_8_fingerprint_hit_on_pending_candidate_is_skipped(tmp_path):
    """矩阵 #8：命中**未决候选** → 同样 SKIP（人工还没看过的不能重复堆）。"""
    store = SQLiteAgentStore(tmp_path / "agent.db")
    _run(tmp_path, replies=[_steps_json(GOOD_STEPS)], store=store)
    gap2 = UncoveredTransition("SearchView", "DetailView",
                               "tap:SearchView.other_field", 1)
    result, *_ = _run(tmp_path, gaps=(gap2,),
                      replies=[_steps_json(GOOD_STEPS)], store=store)
    assert len(store.list_candidates()) == 1
    rejs = store.list_rejections(reason="DUPLICATE")
    assert len(rejs) == 1
    assert rejs[0]["duplicate_of"].startswith("cand_"), \
        "撞的是候选时必须写候选 id（不是「unknown」）"


def test_matrix_8_same_semantics_different_names_still_skips(tmp_path):
    """矩阵 #8 的核心语义：**同语义不同命名 → 同指纹 → SKIP**（F8 明文）。"""
    existing = parse_testcase_dict({"schema_version": "0.2",
                                    "id": "search_candidate_xyz",
                                    "name": "完全不同的名字", "suite": "regression",
                                    "tags": ["generated"], "steps": GOOD_STEPS})
    result, store, *_ = _run(tmp_path, cases=[existing],
                             replies=[_steps_json(GOOD_STEPS)])
    assert store.list_candidates() == []
    assert len(store.list_rejections(reason="DUPLICATE")) == 1


# --- #9：非 sandbox / real_device → 拒绝执行 ----------------------------------


@pytest.mark.parametrize("env_kind", [EnvKind.PRODUCTION, EnvKind.STAGING])
def test_matrix_9_non_sandbox_dry_run_is_refused(tmp_path, env_kind):
    """矩阵 #9（F7）：非 sandbox → **拒绝执行**（抛，不落候选）。"""
    with pytest.raises(DryRunRefused) as e:
        _run(tmp_path, replies=[_steps_json(GOOD_STEPS)], env_kind=env_kind)
    assert "F7" in str(e.value)


def test_matrix_9_real_device_dry_run_is_refused(tmp_path):
    """矩阵 #9：真机 → 拒绝（未审核的候选不许打到真机）。"""
    with pytest.raises(DryRunRefused) as e:
        _run(tmp_path, replies=[_steps_json(GOOD_STEPS)],
             device_type=DeviceType.REAL_DEVICE)
    assert "矩阵 #9" in str(e.value)


def test_matrix_9_refusal_leaves_no_candidate_behind(tmp_path):
    """拒绝时**库里不能留下半条候选**（异常穿出前不许先落库）。"""
    store = SQLiteAgentStore(tmp_path / "agent.db")
    with pytest.raises(DryRunRefused):
        _run(tmp_path, replies=[_steps_json(GOOD_STEPS)],
             env_kind=EnvKind.PRODUCTION, store=store)
    assert store.list_candidates() == []
    assert store.list_rejections() == []


# --- #10：INVALID_OUTPUT 连续 N 次 → 熔断 ------------------------------------


def test_matrix_10_breaker_trips_and_generation_stops(tmp_path):
    """矩阵 #10（F4）：连续非法输出 → 熔断、停止、**不自由发挥**。"""
    gaps = tuple(UncoveredTransition("SearchView", "DetailView",
                                     f"tap:SearchView.f{i}", 1)
                 for i in range(6))
    budget = LLMBudget(max_calls_per_run=100, breaker_consecutive_failures=3)
    result, store, llm, budget = _run(tmp_path, gaps=gaps,
                                      replies=["完全不是 JSON"] * 6,
                                      budget=budget)
    assert budget.broken and result.broken
    assert llm.calls == 3, "熔断后仍继续调 LLM = 自由发挥"
    assert store.list_candidates() == []
    assert any("熔断" in n for n in result.notes)


def test_matrix_10_breaker_is_the_p1_instance(tmp_path):
    """F4 的「复用」：熔断状态**就在调用方给的那个 `LLMBudget` 上**，
    生成器没有自己的第二份状态。"""
    budget = LLMBudget(max_calls_per_run=100, breaker_consecutive_failures=2)
    gaps = tuple(UncoveredTransition("SearchView", "DetailView",
                                     f"tap:SearchView.f{i}", 1)
                 for i in range(4))
    _run(tmp_path, gaps=gaps, replies=["nope"] * 4, budget=budget)
    assert budget.broken is True
    # 熔断后 try_acquire 恒 False（P1 的语义，不是生成器加的）
    assert budget.try_acquire() is False


def test_matrix_10_provider_errors_also_trip_it(tmp_path):
    """网关错误与非法输出**同一条计数**（都算「这次调用没成功」）。"""
    budget = LLMBudget(max_calls_per_run=100, breaker_consecutive_failures=2)
    gaps = tuple(UncoveredTransition("SearchView", "DetailView",
                                     f"tap:SearchView.f{i}", 1)
                 for i in range(3))
    result, *_ = _run(tmp_path, gaps=gaps,
                      replies=[RuntimeError("502"), RuntimeError("502")],
                      budget=budget)
    assert budget.broken and result.broken
