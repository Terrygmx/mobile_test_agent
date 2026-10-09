"""Task 2.3 / P3-07：Planner 编排（设计 §5.2/§5.3）+ 故障注入矩阵 #5/#6。

plan Steps 的三条失败测试：
  1. **矩阵 #5**：LLM 输出修改非同分组排序 → 丢弃 LLM 结果、保留确定性顺序、审计留痕；
  2. **预算耗尽** → `reasons` 降级为规则摘要，Plan 仍产出；
  3. **矩阵 #6**：`changed_files` 为空 → 空 Plan 且**明示**。

fixture 用真 `Repository`（`drift_repo`）+ 真 `TraceStore` + 真 `SQLiteExperienceStore`
——替身 SQL 会让「列名/字段写错」静默通过（Task 2.2 的教训）。
"""
from __future__ import annotations

import json

import pytest

from knowledge import build_knowledge
from llm.budget import LLMBudget
from planner.models import PlannerInput, plan_id_for
from planner.planner import (failure_rate, llm_order_violations, plan,
                             rule_basis)
from planner.prioritizer import RISK_FLOOR
from testcase.schema import Risk, parse_testcase_dict
from tests.fault_injection.fi_support import drift_repo
from tracer.storage import TraceStore

APP = "com.phaset0.logindemo"
BUILD = "local"
APP_FILE = "LoginDemoApp.swift"
OTHER_FILE = "ProfileScreen.swift"

# metadata 里的元素（`HomeView.stale_button` **只在 metadata 里**——Repository 没有它，
# 用来造「进了影响面但打不了分」的用例）。
_FILE_ELEMENTS = {
    APP_FILE: ["HomeView.login_button", "HomeView.pay_button",
               "HomeView.confirm_pay_button", "HomeView.stale_button"],
    OTHER_FILE: ["ProfileView.ghost_button"],
}


# --- fixture ------------------------------------------------------------------


def _metadata() -> dict:
    by_screen: dict[str, list[dict]] = {}
    for file, targets in _FILE_ELEMENTS.items():
        for target in targets:
            screen, elem = target.split(".", 1)
            by_screen.setdefault(screen, []).append(
                {"id": elem, "accessibility_id": elem, "type": "button",
                 "resolution_type": "literal", "source": {"file": file, "line": 1}})
    return {"build": BUILD, "git_commit": "abc1234", "screens": sorted(by_screen),
            "screen_elements": [{"name": s, "elements": els}
                                for s, els in by_screen.items()]}


def _case(case_id: str, target: str):
    return parse_testcase_dict({
        "schema_version": "0.2", "id": case_id, "name": case_id, "suite": "smoke",
        "steps": [{"action": "launch_app"},
                  {"action": "tap", "target": target}]})


def _cases() -> list:
    return [
        _case("login_001", "HomeView.login_button"),        # LOW
        _case("pay_002", "HomeView.confirm_pay_button"),    # CRITICAL → floor 90
        _case("pay_003", "HomeView.confirm_pay_button"),    # CRITICAL → 与 pay_002 同分
        _case("search_004", "HomeView.pay_button"),         # HIGH → floor 70
        _case("profile_005", "ProfileView.ghost_button"),   # 不在影响面内
        _case("stale_006", "HomeView.stale_button"),        # 影响面内但解析不到
    ]


def _trace(tmp_path) -> str:
    """`login_001` 跑 2 次、失败 1 次 → 历史失败率 0.5（其余用例无历史）。"""
    db = tmp_path / "trace.db"
    store = TraceStore(db)
    for run, failed in (("run_1", True), ("run_2", False)):
        store.start_run(run, suite="smoke", app_bundle_id=APP, app_build=BUILD)
        tc = store.start_testcase(run, "login_001", attempt=1)
        store.record_step(tc, 0, "launch_app", status="SUCCESS")
        store.record_step(tc, 1, "tap", target_id="login_button",
                          status="FAILED" if failed else "SUCCESS")
        store.end_testcase(tc, "FAIL" if failed else "PASS",
                           **({"failure_type": "ELEMENT_NOT_FOUND"}
                              if failed else {}))
        store.end_run(run, "FAIL" if failed else "PASS")
    return str(db)


@pytest.fixture()
def env(tmp_path):
    """一次规划所需的全部真实依赖（trace 库只建一次——`runs.run_id` 是主键）。"""
    from experience import SQLiteExperienceStore

    cases = _cases()
    repo = drift_repo(tmp_path)
    store = SQLiteExperienceStore(tmp_path / "experience.db")
    trace_db = _trace(tmp_path)
    ks = build_knowledge(experience_store=store, trace_db=trace_db,
                         cases=cases, app_build=BUILD)
    return {"cases": cases, "repo": repo, "knowledge": ks,
            "experience_store": store, "trace_db": trace_db,
            "metadata": _metadata(), "tmp_path": tmp_path}


def _rebuild_knowledge(env) -> None:
    """用例集变了之后重装配（F3 单一入口：仍然只经 `build_knowledge`）。"""
    env["knowledge"] = build_knowledge(
        experience_store=env["experience_store"], trace_db=env["trace_db"],
        cases=env["cases"], app_build=BUILD)


def _input(changed=APP_FILE) -> PlannerInput:
    return PlannerInput(app_build=BUILD, git_commit="abc1234",
                        changed_files=[changed] if changed else [])


def _plan(env, *, changed=APP_FILE, llm=None, budget=None, base_metadata=None,
          knowledge=None):
    return plan(_input(changed), knowledge=knowledge or env["knowledge"],
                cases=env["cases"], metadata=env["metadata"],
                repository=env["repo"], llm=llm, budget=budget,
                base_metadata=base_metadata)


class _FakeLLM:
    """替身只复刻 `complete(prompt, timeout=...)` 这一条契约。"""

    def __init__(self, payload):
        self.payload = payload
        self.calls: list[str] = []

    def complete(self, prompt: str, timeout: int = 120) -> str:
        self.calls.append(prompt)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class _CountingKnowledge:
    """只统计 `trace_history` 被调了几次（小观察 2：应当**一次**）。"""

    def __init__(self, inner):
        self.inner = inner
        self.trace_calls = 0

    def impact_of(self, target_id):
        return self.inner.impact_of(target_id)

    def trace_history(self, filters):
        self.trace_calls += 1
        return self.inner.trace_history(filters)


def _payload(order, reasons=None, **extra):
    return json.dumps({"reasons": reasons or {}, "order": list(order)}, **extra)


# --- 确定性排序（无 LLM） ------------------------------------------------------


def test_ranks_affected_cases_by_deterministic_score(env):
    """候选集 = 影响面内用例；顺序 = 分数降序、同分按 id 字典序。"""
    result = _plan(env)
    assert [t.testcase_id for t in result.tasks] == [
        "pay_002", "pay_003", "search_004", "login_001"]
    assert [t.priority for t in result.tasks] == [90, 90, 70, 55]
    assert all(t.source == "existing" for t in result.tasks)


def test_unaffected_case_is_not_in_the_plan_but_is_announced(env):
    """口径 1：未受影响的用例不进 Plan——但**说出来**（不是静默丢弃）。"""
    result = _plan(env)
    assert "profile_005" not in [t.testcase_id for t in result.tasks]
    assert any("不在本次改动的影响面内" in n for n in result.notes), result.notes


def test_rule_reasons_are_traceable_and_non_blank(env):
    """F13：`reasons` 是**代码生成**的依据，不是「LLM 觉得」。"""
    task = next(t for t in _plan(env).tasks if t.testcase_id == "search_004")
    joined = "\n".join(task.reasons)
    assert "impact: 命中改动影响面" in joined
    assert "history: 历史失败率 0.00" in joined
    assert "risk: HIGH（floor 70）" in joined
    assert "score: 70" in joined
    assert all(r.strip() for r in task.reasons), "空串会被 TestPlanTask 拒掉"


def test_plan_id_is_content_addressed(env):
    """同一输入 → 同一 id（重跑幂等）；改动不同 → 不同 id（不互相覆盖）。"""
    a = _plan(env).plan.plan_id
    b = _plan(env).plan.plan_id
    c = _plan(env, changed=OTHER_FILE).plan.plan_id
    assert a == b == plan_id_for(BUILD, "abc1234", [APP_FILE])
    assert a != c
    assert _plan(env, changed=OTHER_FILE).plan.plan_id == plan_id_for(
        BUILD, "abc1234", [OTHER_FILE])


def test_history_is_read_once_not_per_candidate(env):
    """小观察 2：`trace_history` **一次读全量**再按 `testcase_id` 分组。

    `trace_history(filters)` 每次调用都会 `read_trace_steps` 读**全库**再在 Python 里
    过滤，所以「在候选循环里逐用例调用」是 O(候选数 × 全库步骤数)。本 Plan 有 4 个候选
    ——按旧写法会调 4 次。
    """
    counting = _CountingKnowledge(env["knowledge"])
    result = _plan(env, knowledge=counting)
    assert len(result.tasks) == 4
    assert counting.trace_calls == 1, f"读了 {counting.trace_calls} 次全量 trace"


# --- 矩阵 #6：空改动 ----------------------------------------------------------


def test_empty_changed_files_yields_empty_plan_with_reason(env):
    result = _plan(env, changed=None)
    assert result.plan.tasks == []
    assert any("changed_files 为空" in n for n in result.notes), result.notes
    assert result.plan.plan_id  # 空 Plan 也是一个确定的 Plan


def test_changed_file_without_targets_lists_the_unmatched_paths(env):
    """改动非空但映射不到元素 → 空 Plan，且**列出未匹配的文件**。"""
    result = _plan(env, changed="NotInMetadata.swift")
    assert result.plan.tasks == []
    assert any("NotInMetadata.swift" in n for n in result.notes), result.notes


def test_targets_without_cases_yields_empty_plan(env):
    """改动命中元素但没有用例引用它们 → 空 Plan + 明示。"""
    env["metadata"]["screen_elements"].append(
        {"name": "OrphanView", "elements": [
            {"id": "orphan", "accessibility_id": "orphan", "type": "button",
             "resolution_type": "literal",
             "source": {"file": "Orphan.swift", "line": 1}}]})
    result = _plan(env, changed="Orphan.swift")
    assert result.plan.tasks == []
    assert any("没有用例引用" in n for n in result.notes), result.notes


# --- 矩阵 #5：LLM 只许同分重排 ------------------------------------------------


def test_llm_same_score_swap_is_applied(env):
    """同分互换是**设计允许**的 tie-break（§5.2），必须真的生效。"""
    llm = _FakeLLM(_payload(["pay_003", "pay_002", "search_004", "login_001"]))
    result = _plan(env, llm=llm, budget=LLMBudget(max_calls_per_run=1))
    assert [t.testcase_id for t in result.tasks][:2] == ["pay_003", "pay_002"]
    assert result.audit == ()
    assert len(llm.calls) == 1, "整份 Plan 只发一次 LLM 调用"


def test_llm_cross_score_reorder_is_rejected_and_audited(env):
    """**矩阵 #5**：把 70 分提到 90 分之前 → 丢弃重排、保留确定性顺序、审计留痕。"""
    llm = _FakeLLM(_payload(["search_004", "pay_002", "pay_003", "login_001"]))
    result = _plan(env, llm=llm, budget=LLMBudget(max_calls_per_run=1))
    assert [t.testcase_id for t in result.tasks] == [
        "pay_002", "pay_003", "search_004", "login_001"], "确定性顺序必须保留"
    kinds = [a["kind"] for a in result.audit]
    assert "llm_order_rejected" in kinds, result.audit
    rejected = next(a for a in result.audit if a["kind"] == "llm_order_rejected")
    assert rejected["violations"][0]["moved_up"] == "search_004"
    assert rejected["violations"][0]["displaced"] == "pay_002"
    assert rejected["violations"][0]["moved_up_score"] == 70
    assert any("跨分重排" in n for n in result.notes), result.notes


def test_llm_non_permutation_is_rejected(env):
    """漏一个 id → 不是排列 → 整条顺序作废（确定性顺序保留）。"""
    llm = _FakeLLM(_payload(["pay_003", "pay_002"]))
    result = _plan(env, llm=llm, budget=LLMBudget(max_calls_per_run=1))
    assert [t.testcase_id for t in result.tasks] == [
        "pay_002", "pay_003", "search_004", "login_001"]
    rejected = next(a for a in result.audit if a["kind"] == "llm_order_rejected")
    assert rejected["violations"][0]["kind"] == "not_a_permutation"
    assert rejected["violations"][0]["missing"] == ["login_001", "search_004"]


def test_llm_order_violations_allows_any_within_group_order():
    """纯函数级：同分任意排列合法，跨分一次下降即违规（段位按**分数**算）。"""
    ordered = [("a", 90), ("b", 70), ("c", 70), ("d", 40)]
    assert llm_order_violations(ordered, ["a", "c", "b", "d"]) == []
    assert llm_order_violations(ordered, ["c", "b", "a", "d"]) != []
    # 三条同分：任意排列都合法（按名次算就会把它们全判成违规）
    same = [("a", 50), ("b", 50), ("c", 50)]
    assert llm_order_violations(same, ["c", "a", "b"]) == []


# --- 降级：LLM 不可用时 Plan 照常产出 -----------------------------------------


def test_budget_exhausted_degrades_to_rule_reasons(env):
    llm = _FakeLLM(_payload(["pay_002", "pay_003", "search_004", "login_001"]))
    result = _plan(env, llm=llm, budget=LLMBudget(max_calls_per_run=0))
    assert llm.calls == [], "预算耗尽后**不得**发起 API 调用"
    assert any("预算耗尽" in n for n in result.notes), result.notes
    assert all(not any(r.startswith("llm: ") for r in t.reasons)
               for t in result.tasks)
    assert len(result.tasks) == 4, "Plan 仍要产出"


def test_llm_provider_failure_degrades_and_counts_toward_the_breaker(env):
    llm = _FakeLLM(RuntimeError("gateway down"))
    budget = LLMBudget(max_calls_per_run=1, breaker_consecutive_failures=1)
    result = _plan(env, llm=llm, budget=budget)
    assert len(result.tasks) == 4
    assert any("LLM 调用失败" in n for n in result.notes), result.notes
    assert budget.broken is True, "provider 故障必须记失败（熔断的口径）"


def test_unparseable_llm_output_degrades(env):
    llm = _FakeLLM("这不是 JSON")
    result = _plan(env, llm=llm, budget=LLMBudget(max_calls_per_run=1))
    assert len(result.tasks) == 4
    assert any("没有可用内容" in n for n in result.notes), result.notes


def test_llm_without_budget_is_never_called(env):
    """只给 `llm` 不给 `budget` → 不调用：静默造预算对象会让记账有两个来源。"""
    llm = _FakeLLM(_payload(["pay_002", "pay_003", "search_004", "login_001"]))
    result = _plan(env, llm=llm, budget=None)
    assert llm.calls == []
    assert len(result.tasks) == 4


# --- LLM 解释的合并与清洗 -----------------------------------------------------


def test_llm_reasons_are_appended_with_provenance(env):
    """LLM 的文字以 `llm: ` 前缀**追加**——数值依据永远在（F13 的可复核性）。"""
    llm = _FakeLLM(_payload(
        ["pay_002", "pay_003", "search_004", "login_001"],
        reasons={"login_001": "登录用例上周失败过，建议优先"}))

    result = _plan(env, llm=llm, budget=LLMBudget(max_calls_per_run=1))
    task = next(t for t in result.tasks if t.testcase_id == "login_001")
    assert task.reasons[-1] == "llm: 登录用例上周失败过，建议优先"
    assert any(r.startswith("score: ") for r in task.reasons)
    other = next(t for t in result.tasks if t.testcase_id == "pay_002")
    assert not any(r.startswith("llm: ") for r in other.reasons)


def test_unknown_llm_ids_are_dropped_and_audited(env):
    """LLM 提到本 Plan 里没有的 id（幻觉 / 串了别的 Plan）→ 丢弃 + 留痕。"""
    llm = _FakeLLM(_payload(
        ["pay_002", "pay_003", "search_004", "login_001"],
        reasons={"ghost_case": "不存在", "login_001": "真实解释"}))
    result = _plan(env, llm=llm, budget=LLMBudget(max_calls_per_run=1))
    assert any(a["kind"] == "llm_unknown_testcase_ids" and
               a["ids"] == ["ghost_case"] for a in result.audit), result.audit
    task = next(t for t in result.tasks if t.testcase_id == "login_001")
    assert any(r == "llm: 真实解释" for r in task.reasons)


def test_ignored_llm_fields_are_audited(env):
    """额外字段忽略但**可见**（与 recovery 契约同款纪律）。"""
    llm = _FakeLLM(json.dumps(
        {"reasons": {}, "order": ["pay_002", "pay_003", "search_004", "login_001"],
         "priority": {"pay_002": 100}}))
    result = _plan(env, llm=llm, budget=LLMBudget(max_calls_per_run=1))
    assert any(a["kind"] == "llm_ignored_fields" and a["fields"] == ["priority"]
               for a in result.audit), result.audit
    assert [t.priority for t in result.tasks] == [90, 90, 70, 55], "分数不可被 LLM 改"


# --- 坏引用用例（不猜、不静默丢） ---------------------------------------------


def test_plan_refuses_wrong_shaped_cases(env):
    """传错形态的用例对象 → **报错**，不静默塌成 id `"?"`（与 P3-1 同形）。

    原先 `candidates` 的过滤条件用 `getattr(c, "id", "?")`：所有坏对象塌成同一个
    `"?"`，Plan 里出现一条 id 为 `"?"` 的任务而全程无报错。
    """
    class _NoId:
        steps: list = []

    with pytest.raises(AttributeError, match="id"):
        plan(_input(APP_FILE), knowledge=env["knowledge"], cases=[_NoId()],
             metadata=env["metadata"], repository=env["repo"])


def test_unresolvable_case_is_listed_not_silently_dropped(env):
    """进了影响面但元素解析不到 → 不猜风险（猜 LOW 会让它沉底），**列出来**。"""
    result = _plan(env)
    assert [u.testcase_id for u in result.unrankable] == ["stale_006"]
    assert "UnknownReferenceError" in result.unrankable[0].reason
    assert "stale_006" not in [t.testcase_id for t in result.tasks]
    assert any("无法打分" in n for n in result.notes), result.notes


# --- drift 回退面 -------------------------------------------------------------


def test_drift_fallback_when_metadata_lacks_attribution(env):
    """metadata 缺 `source.file` → 回退 build 间 drift 面，并在 notes 里说明精度损失。

    fixture 造两个 drift：`signin_button`（Repository 里有 → 能打分）与 `gone_button`
    （Repository 里没有 → 打不了分）。前者证明回退面**真的产出了任务**，后者证明坏引用
    被列出来而不是静默消失。
    """
    new_meta = json.loads(json.dumps(env["metadata"]))
    for screen in new_meta["screen_elements"]:
        for el in screen["elements"]:
            el.pop("source", None)
    base_meta = json.loads(json.dumps(new_meta))
    base_meta["screen_elements"][0]["elements"] += [
        {"id": "signin_button", "accessibility_id": "signin_button",
         "type": "button", "resolution_type": "literal"},
        {"id": "gone_button", "accessibility_id": "gone_button",
         "type": "button", "resolution_type": "literal"}]
    env["cases"] += [_case("signin_008", "HomeView.signin_button"),
                     _case("gone_007", "HomeView.gone_button")]
    _rebuild_knowledge(env)

    result = plan(_input(APP_FILE), knowledge=env["knowledge"], cases=env["cases"],
                  metadata=new_meta, repository=env["repo"], base_metadata=base_meta)
    assert any("回退 build 间 drift 面" in n for n in result.notes), result.notes
    assert any("精度损失" in n for n in result.notes), result.notes
    assert [t.testcase_id for t in result.tasks] == ["signin_008"]
    assert [u.testcase_id for u in result.unrankable] == ["gone_007"]
    # **P2-1 的落点**：精度损失必须随 Plan **落库**（`reasons`），不能只在不落库的
    # `notes` 里——否则落库的 Plan 上「影响面精确」与「影响面宽」长得一模一样。
    reasons = result.tasks[0].reasons
    assert reasons[0] == "impact: 命中改动影响面（drift 回退：影响面比本次 commit 宽）"
    assert result.plan.tasks[0].reasons == reasons, "落库的就是这一份"


def test_no_drift_marker_when_the_primary_path_was_used(env):
    """没走回退面时**不加** drift 标记（标记是限定语，不是装饰）。"""
    reasons = _plan(env).tasks[0].reasons
    assert reasons[0] == "impact: 命中改动影响面"
    assert not any("drift" in r for r in reasons)


def test_missing_attribution_without_base_metadata_fails_loud(env):
    """没有 base_metadata 可回退 → 照原样上抛（不是「空 Plan」）。"""
    from planner.impact import ImpactMappingError

    new_meta = json.loads(json.dumps(env["metadata"]))
    for screen in new_meta["screen_elements"]:
        for el in screen["elements"]:
            el.pop("source", None)
    with pytest.raises(ImpactMappingError, match="source.file"):
        plan(_input(APP_FILE), knowledge=env["knowledge"], cases=env["cases"],
             metadata=new_meta, repository=env["repo"])


# --- 纯函数级 -----------------------------------------------------------------


def test_failure_rate_counts_runs_not_steps():
    """按**运行**统计：分母 = distinct run，分子 = 有 ≥1 FAILED 步骤的 run。"""
    class _Step:
        def __init__(self, run, status):
            self.testcase_run_id, self.status = run, status

    steps = [_Step(1, "FAILED"), _Step(1, "RECOVERED"),
             _Step(2, "SUCCESS"), _Step(3, "SUCCESS")]
    assert failure_rate(steps) == pytest.approx(1 / 3)
    assert failure_rate([]) == 0.0, "空历史 = 没有失败证据，不是「一直失败」"


def test_failure_rate_refuses_wrong_shaped_steps():
    """入参直接取属性、**不用 `getattr` 兜底**（P3-1：同形第三次）。

    探针实测（修复前）：两个缺 `testcase_run_id` 的 FAILED 步骤 → **1.0**（两个 run 塌成
    一个）；缺 `status` → **0.0**。失败率占 30 分权重——塌成 0 让有失败史的用例**沉底**、
    塌成 1 让没失败史的用例白拿 30 分，两侧都是「看不见」那一侧。
    """
    class _NoRunId:
        status = "FAILED"

    class _NoStatus:
        testcase_run_id = 1

    with pytest.raises(AttributeError):
        failure_rate([_NoRunId(), _NoRunId()])
    with pytest.raises(AttributeError):
        failure_rate([_NoStatus()])


def test_llm_violations_are_capped_and_marked_truncated():
    """`_MAX_VIOLATIONS` 的截断是**真分支**（纯函数直接喂 21 条违规）。"""
    from planner.planner import _MAX_VIOLATIONS

    n = _MAX_VIOLATIONS + 5
    ordered = [(f"c{i:03d}", 100 - i) for i in range(n)]      # 每条分数都不同
    proposed = [cid for cid, _ in reversed(ordered)]          # 全反序 → 每一步都违规
    out = llm_order_violations(ordered, proposed)
    assert len(out) == _MAX_VIOLATIONS + 1
    assert out[-1] == {"kind": "truncated"}, out[-1]


def test_rule_basis_states_every_input():
    basis = rule_basis(hit=True, rate=0.25, risk=Risk.CRITICAL, score=90)
    assert basis == ["impact: 命中改动影响面", "history: 历史失败率 0.25",
                     "risk: CRITICAL（floor 90）", "score: 90"]
    no_floor = rule_basis(hit=False, rate=0.0, risk=Risk.LOW, score=0)
    assert no_floor[2] == "risk: LOW", "没有 floor 的等级不写 floor"
    assert RISK_FLOOR.get(Risk.LOW) is None


def test_rule_basis_marks_the_drift_fallback_on_the_impact_line():
    """drift 标记**附着在它限定的那一行**上（不是每条 reasons 追加一整句）。"""
    plain = rule_basis(hit=True, rate=0.0, risk=Risk.LOW, score=40)
    drift = rule_basis(hit=True, rate=0.0, risk=Risk.LOW, score=40, drift=True)
    assert drift[0].startswith(plain[0]) and drift[0] != plain[0]
    assert "drift 回退" in drift[0]
    assert drift[1:] == plain[1:], "只影响 impact 那一行"


def test_plan_result_tasks_is_a_view_of_the_plan(env):
    """`PlanResult.tasks` 是 `plan.tasks` 的**只读视图**（CLI 渲染用）。

    （旧名 `test_plan_result_exposes_tasks` 的 docstring 是这句，函数体却只构造了
    `Unrankable`——名实不符，review_p3_task23 小观察 3。`Unrankable` 本身由
    `test_unresolvable_case_is_listed_not_silently_dropped` 覆盖。）
    """
    result = _plan(env)
    assert isinstance(result.tasks, tuple)
    assert result.tasks == tuple(result.plan.tasks)
    assert result.tasks and result.tasks[0] is result.plan.tasks[0]
