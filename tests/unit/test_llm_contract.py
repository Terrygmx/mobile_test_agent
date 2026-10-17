"""Task 4.2：LLM 契约（10.4 分区/脱敏 + 严格解析 + 9.3 校验链，FakeLLM 无网络）。

矩阵子集（§18）：#4 候选多匹配 / #5 类型不符 / #6 不在当前屏 / #7 风险拦截 /
#8 risk_level 被忽略 / #9 非法 JSON / #10 预算不发调用。#2/#15 端到端在
tests/fault_injection/test_llm_matrix.py。

H14/H8：prompt 里不得出现 secret 原值——先脱敏后出进程。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.context import RecoveryContext
from agent.policy import FailurePhase
from agent.recovery import RecoveryEngine
from executor.policy import Idempotency
from llm.budget import BudgetConfig, LLMBudget
from llm.parser import parse_llm_output
from llm.prompt import build_recovery_prompt, redact_ui_tree
from testcase.schema import Risk

PAGE = ("<App><Node name='screen.HomeView' visible='true'/>"
        "<Node name='signin_button'/></App>")
SECRET_PAGE = ("<App><SecureTextField name='pw' value='S3cret!'/>"
               "<TextField value='13812345678'/></App>")


class FakeLLM:
    def __init__(self, script=None):
        self.script = list(script or [])
        self.calls: list[str] = []

    def complete(self, prompt: str, timeout: int = 20) -> str:
        self.calls.append(prompt)
        item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


def _llm_json(action="tap", value="signin_button", conf=0.93, **extra):
    out = {"action": action,
           "target": {"type": "accessibility_id", "value": value},
           "scope": "HomeView", "reason": "renamed", "confidence": conf}
    out.update(extra)
    return json.dumps(out)


class FakeRepo:
    """最小 Repository 契约：elements_of(screen) + resolve(id, build) +
    current_screen 所需的 marker 映射（screen 识别/reconcile 阶段真实可达，
    否则 recon 恒 None——recon 通道的测试会空转）。"""

    def __init__(self, elements):
        self._els = {e.id: e for e in elements}
        self.generated_screens = {
            "HomeView": SimpleNamespace(marker="screen.HomeView"),
            "ProfileView": SimpleNamespace(marker="screen.ProfileView"),
        }
        self.override_screens = {}

    def screen_kind_hint(self, screen_id):
        return "page"

    def elements_of(self, screen):
        return [e for e in self._els.values() if e.screen == screen]

    def resolve(self, ref, *, build):
        rid = ref.id if hasattr(ref, "id") else str(ref)
        if rid not in self._els:
            raise KeyError(f"unknown reference: {rid!r}")
        return self._els[rid]

    def resolve_locator(self, strategy_type, value, *, build, screen=None):
        for el in self._els.values():
            if getattr(el, "strategy_type", "accessibility_id") == strategy_type \
                    and getattr(el, "strategy_value", el.id) == value \
                    and (screen is None or el.screen == screen):
                return el
        raise KeyError(f"unknown locator: {strategy_type}={value!r}")


def _repo():
    return FakeRepo([
        SimpleNamespace(id="signin_button", screen="HomeView", type="button",
                        risk=Risk.LOW),
        SimpleNamespace(id="pay_button", screen="HomeView", type="button",
                        risk=Risk.HIGH),
        SimpleNamespace(id="ghost_button", screen="ProfileView",
                        type="button", risk=Risk.LOW),
    ])


def _ctx(llm_out=None, *, repo=None, guard=None, budget=None,
         find_results=None, **kw):
    """find_results: 按调用序返回（Exception 抛出）；耗尽后重复最后一项。"""
    finds = {"n": 0}

    def find_with(strategies):
        finds["n"] += 1
        idx = min(finds["n"] - 1, len(find_results or [_El()]) - 1)
        item = (find_results or [_El()])[idx]
        if isinstance(item, Exception):
            raise item
        return item

    defaults = dict(
        failure_type="ELEMENT_NOT_FOUND", phase=FailurePhase.PRE_DISPATCH,
        element_id="login_button", screen_id="HomeView", action="tap",
        expected_type="button", strategies=(),
        effective_idempotency=Idempotency.IDEMPOTENT,
        app_build="local", testcase_id="tc_llm",
        find_with=find_with, redispatch=None,
        page_source=lambda: PAGE,
    )
    defaults.update(kw)
    llm = FakeLLM(llm_out or [_llm_json()])
    engine = RecoveryEngine(repo=repo if repo is not None else _repo(),
                            llm=llm, budget=budget or LLMBudget(),
                            guard=guard, sleep=lambda s: None)
    return engine, ctx(defaults), llm


class _El:
    def __init__(self, etype="XCUIElementTypeButton"):
        self._t = etype

    def get_attribute(self, name):
        return self._t if name == "type" else ""


def ctx(defaults):
    return RecoveryContext(**defaults)


# --- 10.4 解析契约 ---


def test_parse_valid_output():
    p = parse_llm_output(_llm_json())
    assert p.valid and p.action == "tap" and p.target_value == "signin_button"
    assert p.confidence == 0.93


def test_parse_invalid_json():
    assert parse_llm_output("not json at all").valid is False


def test_parse_extra_fields_ignored_and_recorded():
    """10.4：额外字段忽略并记录；H4：risk_level 尤其如此。"""
    p = parse_llm_output(_llm_json(risk_level="LOW", huh=1))
    assert p.valid
    assert "risk_level" in p.ignored_fields and "huh" in p.ignored_fields


def test_parse_action_whitelist():
    """10.4：action 仅 tap/input/swipe/back/wait——越权即 invalid。"""
    assert parse_llm_output(_llm_json(action="shell")).valid is False


def test_parse_confidence_bounds():
    assert parse_llm_output(_llm_json(conf=1.5)).valid is False
    assert parse_llm_output(_llm_json(conf="high")).valid is False


# --- 10.4 分区 + 脱敏（H14/H8） ---


def test_prompt_partitions_and_untrusted_marker():
    prompt = build_recovery_prompt(
        goal_element="login_button", goal_action="tap",
        error="ELEMENT_NOT_FOUND", source_subset=[{"id": "signin_button"}],
        page_source=PAGE)
    for part in ("[SYSTEM INSTRUCTIONS]", "[TEST GOAL]", "[SOURCE METADATA]",
                 "[ERROR]", "[UNTRUSTED OBSERVED UI]"):
        assert part in prompt, f"10.4 分区缺失: {part}"
    # 真实分区头带固定下一行（SYSTEM INSTRUCTIONS 里会引用分区名，
    # str.index 会先撞上引用——用带后缀的完整头定位）
    assert prompt.index("[SOURCE METADATA]") < prompt.index(
        "[UNTRUSTED OBSERVED UI]\n以下运行时 UI 树已脱敏"), \
        "不可信区必须在最后"


def test_prompt_no_secret_values_h14():
    """H14/H8：脱敏先于出进程——SecureTextField 值、手机号不入 prompt。"""
    masked = redact_ui_tree(SECRET_PAGE)
    prompt = build_recovery_prompt(
        goal_element="pw", goal_action="tap", error="ELEMENT_NOT_FOUND",
        source_subset=[], page_source=masked)
    assert "S3cret!" not in prompt
    assert "13812345678" not in prompt
    assert "TextField" in prompt and "pw" in prompt, \
        "保留 type/label——过度脱敏会让恢复失效（10.4）"


def test_redaction_leak_forms_p2_1():
    """review_m4_task42 P2-1 探针形态：\b 在下划线/CJK 与数字间不成立——
    「user_138…」「用户138…」「订单NO123456789」都曾泄漏进 prompt。
    修复后全部遮蔽（对 redact 输出直接断言 + 引擎出口双重验证）。"""
    leak_page = ("<App>"
                 "<Node name='user_13812345678'/>"
                 "<Node label='用户13812345678'/>"
                 "<Node value='订单NO123456789'/>"
                 "<Node name='mail_user@example.com'/>"
                 "</App>")
    masked = redact_ui_tree(leak_page)
    for secret in ("13812345678", "NO123456789", "mail_user@example.com"):
        assert secret not in masked, f"泄漏形态未遮蔽: {secret}"

    # 引擎出口（FakeLLM 捕获实文）——防「忘了接 redact」回归
    page_holder = {"page": leak_page}
    engine, c, llm = _ctx(page_source=lambda: page_holder["page"])
    engine.recover(c)
    assert llm.calls
    assert "13812345678" not in llm.calls[0]
    assert "NO123456789" not in llm.calls[0]
    assert "mail_user@example.com" not in llm.calls[0]


def test_recon_channel_redacted_p2_2():
    """review_m4_task42 P2-2：reconciliation 候选派生自运行时页——必须
    来自脱敏页，且 JSON 落在不可信区（[UNTRUSTED OBSERVED UI] 之后）。"""
    # 元素 id 含订单号形态 → recon candidates_in_runtime 若取自原始页
    # 就会带原值；脱敏后应是 masked 形态
    leak_page = ("<App><Node name='screen.HomeView' visible='true'/>"
                 "<Node name='order_NO123456789'/></App>")
    engine, c, llm = _ctx(page_source=lambda: leak_page,
                          refind=lambda: (_ for _ in ())
                          .throw(LookupError("drifted")))
    r = engine.recover(c)
    assert llm.calls
    prompt = llm.calls[0]
    assert "NO123456789" not in prompt, "recon 通道未脱敏"
    assert "runtime reconciliation" in prompt, "recon 必须带 runtime 派生标注"
    # recon JSON 在不可信区头部之后
    assert prompt.index("[UNTRUSTED OBSERVED UI]") < \
        prompt.index("runtime reconciliation")


def test_engine_prompt_sent_is_redacted():
    """端到端：引擎发出的 prompt 无 secret（FakeLLM 捕获的实文断言）。"""
    engine, c, llm = _ctx(page_source=lambda: SECRET_PAGE)
    engine.recover(c)
    assert llm.calls, "LLM 必须被调用"
    assert "S3cret!" not in llm.calls[0]


# --- 9.3 校验链五项（矩阵 #4-#8） ---


def test_chain_count_not_found_matrix4_pre():
    """矩阵 #4 前置：候选不存在 → LLM_TARGET_NOT_FOUND，不执行。"""
    engine, c, llm = _ctx(find_results=[KeyError("gone")])
    r = engine.recover(c)
    assert not r.recovered and r.failure_type == "LLM_TARGET_NOT_FOUND"


def test_chain_count_ambiguous_matrix4():
    engine, c, llm = _ctx(find_results=[[_El(), _El()]])
    r = engine.recover(c)
    assert not r.recovered and r.failure_type == "LLM_TARGET_AMBIGUOUS"


def test_chain_type_mismatch_matrix5():
    engine, c, llm = _ctx(find_results=[_El("XCUIElementTypeTextField")])
    r = engine.recover(c)
    assert not r.recovered and r.failure_type == "LLM_TARGET_TYPE_MISMATCH"


def test_chain_screen_mismatch_matrix6():
    """候选登记在别的屏（ghost_button 在 ProfileView）→ 拒绝。"""
    engine, c, llm = _ctx(llm_out=[_llm_json(value="ghost_button")])
    r = engine.recover(c)
    assert not r.recovered and r.failure_type == "LLM_TARGET_SCREEN_MISMATCH"


def test_chain_unregistered_candidate_fail_closed():
    """候选不在 metadata → 无法证明属于当前屏 → fail-closed 拒绝。"""
    engine, c, llm = _ctx(llm_out=[_llm_json(value="not_registered")])
    r = engine.recover(c)
    assert not r.recovered and r.failure_type == "LLM_TARGET_SCREEN_MISMATCH"


def test_chain_locator_value_resolves_to_registered_element():
    """候选给 accessibility 值，元素 id 可与值不同（search_back/return）。"""
    repo = _repo()
    repo._els["search_back"] = SimpleNamespace(
        id="search_back", screen="HomeView", type="button", risk=Risk.LOW,
        strategy_type="accessibility_id", strategy_value="return")
    engine, c, llm = _ctx(
        llm_out=[_llm_json(value="return")], repo=repo,
        element_id="lst_recovery_search_back")
    r = engine.recover(c)
    assert r.recovered and r.kind == "llm"
    assert r.detail["candidate"] == {
        "type": "accessibility_id", "value": "return"}
    validate = [s for s in r.detail["stages"]
                if s.get("stage") == "validate"]
    assert validate[-1]["outcome"] == "EXECUTE"


def test_chain_risk_blocked_matrix7():
    engine, c, llm = _ctx(llm_out=[_llm_json(value="pay_button")])
    r = engine.recover(c)
    assert not r.recovered and r.failure_type == "LLM_RISK_BLOCKED"


def test_chain_llm_risk_level_never_lowers_risk_matrix8():
    """矩阵 #8：LLM 返回 risk_level: LOW 但候选元素是 HIGH → 仍拦截。"""
    engine, c, llm = _ctx(llm_out=[_llm_json(value="pay_button",
                                             risk_level="LOW")])
    r = engine.recover(c)
    assert not r.recovered and r.failure_type == "LLM_RISK_BLOCKED"
    stages = [s for s in r.detail["stages"] if s.get("stage") == "llm"
              and s.get("outcome") == "ignored_fields"]
    assert stages, "risk_level 被忽略必须留痕（H4）"


def test_chain_guard_violation_security_blocked():
    """9.3-4 后半：候选 risk LOW 但 Guard 拦（blocked_targets 等）→
    SECURITY_BLOCKED（10.1：Guard 不受 LLM 输出影响）。"""
    from executor.guard import GuardContext, GuardViolation
    from testcase.schema import Risk

    class AlwaysBlock:
        def check(self, gctx):
            raise GuardViolation("blocked by policy", Risk.LOW, gctx)

    engine, c, llm = _ctx(guard=AlwaysBlock())
    r = engine.recover(c)
    assert not r.recovered and r.failure_type == "SECURITY_BLOCKED"


def test_chain_low_confidence_matrix_pre():
    engine, c, llm = _ctx(llm_out=[_llm_json(conf=0.5)])
    r = engine.recover(c)
    assert not r.recovered and r.failure_type == "LLM_LOW_CONFIDENCE"


def test_chain_invalid_output_matrix9():
    """矩阵 #9：非法 JSON → LLM_INVALID_OUTPUT。"""
    engine, c, llm = _ctx(llm_out=["I think it is signin_button, trust me"])
    r = engine.recover(c)
    assert not r.recovered and r.failure_type == "LLM_INVALID_OUTPUT"


def test_chain_action_mismatch_is_contract_violation():
    """10.4：action 必须与原动作一致（tap 步骤不允许 LLM 改 input）。"""
    engine, c, llm = _ctx(llm_out=[_llm_json(action="input")])
    r = engine.recover(c)
    assert not r.recovered and r.failure_type == "LLM_INVALID_OUTPUT"


# --- RECOVERED 主路径 + memo + budget ---


def test_chain_recovered_executes_and_saves_memo():
    """矩阵 #2 引擎半边：候选全链通过 → 执行 → RECOVERED(kind=llm) +
    ⑦ memo 保存（同 run 同漂移免重复调用）。"""
    dispatched: list = []
    engine, c, llm = _ctx(redispatch=lambda el: dispatched.append(el))
    r = engine.recover(c)
    assert r.recovered and r.kind == "llm"
    assert r.strategy == {"type": "accessibility_id",
                          "value": "signin_button"}
    assert len(dispatched) == 1, "动作步候选必须被执行"
    memo = engine.run_memo.lookup("HomeView", "login_button", "local")
    assert memo == r.strategy, "⑦：LLM 校验通过后写 RUN_MEMO"
    assert r.detail["screen"] == "HomeView"
    assert r.detail["candidate_type"] == "button"


def test_chain_budget_exhausted_no_api_call_matrix10():
    """矩阵 #10：budget=0 → LLM_BUDGET_EXCEEDED 且**未发起** API 调用。"""
    engine, c, llm = _ctx(budget=LLMBudget(max_calls_per_run=0))
    r = engine.recover(c)
    assert not r.recovered and r.failure_type == "LLM_BUDGET_EXCEEDED"
    assert llm.calls == [], "不得发起任何 API 调用"


def test_chain_breaker_stops_api_calls():
    """熔断后引擎不再发调用（连续失败累计跨恢复尝试）。"""
    budget = LLMBudget(config=BudgetConfig(breaker_consecutive_failures=2))
    engine, c, llm = _ctx(llm_out=["garbage", "garbage", _llm_json()],
                          budget=budget)
    assert not engine.recover(c).recovered   # invalid #1
    assert not engine.recover(c).recovered   # invalid #2 → 熔断
    r = engine.recover(c)                    # 第 3 次本可成功
    assert not r.recovered and r.failure_type == "LLM_BUDGET_EXCEEDED"
    assert len(llm.calls) == 2, "熔断后不再发起 API 调用"


def test_chain_provider_error():
    engine, c, llm = _ctx(llm_out=[RuntimeError("boom")])
    r = engine.recover(c)
    assert not r.recovered and r.failure_type == "LLM_PROVIDER_ERROR"


def test_no_llm_disabled_stage_visible():
    """--no-llm / 未配置：llm 阶段 disabled 如实可见（不是静默无恢复）。"""
    engine = RecoveryEngine(repo=_repo(), sleep=lambda s: None)
    c = RecoveryContext(failure_type="ELEMENT_NOT_FOUND",
                        phase=FailurePhase.PRE_DISPATCH,
                        element_id="login_button", screen_id="HomeView",
                        effective_idempotency=Idempotency.IDEMPOTENT,
                        refind=lambda: (_ for _ in ()).throw(LookupError("x")))
    r = engine.recover(c)
    assert not r.recovered
    assert {"stage": "llm", "outcome": "disabled"} in r.detail["stages"]


# ---------------------------------------------------------------------------
# Planner 解释层契约（设计 §5.2 末句；Task 2.3 / P3-07）
# ---------------------------------------------------------------------------


def test_plan_rationale_prompt_states_the_two_hard_rules():
    """prompt 必须把两条硬约束写出来（对应的**强制**在 parser 与 planner，不靠模型自觉）。"""
    from llm.prompt import build_plan_rationale_prompt

    prompt = build_plan_rationale_prompt(
        app_build="1026", git_commit="abc1234",
        changed_files=["LoginDemoApp.swift"],
        entries=[{"testcase_id": "login_001", "priority": 55,
                  "basis": ["impact: 命中改动影响面", "score: 55"]}])
    assert "[SYSTEM INSTRUCTIONS]" in prompt
    assert '"reasons"' in prompt and '"order"' in prompt
    assert "会被判违规" in prompt, "跨分重排的后果必须写进契约"
    assert "不要引入没有给出的事实" in prompt, "F13：不许编造依据"
    # 可信区里带上真实上下文（用例 id / 分数 / 依据 / 改动文件）
    assert "login_001" in prompt and "priority=55" in prompt
    assert "LoginDemoApp.swift" in prompt and "abc1234" in prompt


def test_plan_rationale_prompt_has_no_untrusted_zone():
    """本 prompt 只喂仓库数据（用例集 / git / metadata）——**没有**运行时观测，
    所以不该出现不可信区；将来若有人往这里塞运行时数据，必须先建区（见函数 docstring）。"""
    from llm.prompt import build_plan_rationale_prompt

    prompt = build_plan_rationale_prompt(
        app_build="b", git_commit="c", changed_files=[],
        entries=[{"testcase_id": "x", "priority": 0, "basis": []}])
    assert "[UNTRUSTED" not in prompt


def test_parse_plan_reasons_happy_path():
    from llm.parser import parse_plan_reasons

    p = parse_plan_reasons(json.dumps({
        "reasons": {"login_001": "  登录失败过  "},
        "order": ["b", "a"]}))
    assert p.reasons == {"login_001": "登录失败过"}, "strip 过"
    assert p.order == ("b", "a")


@pytest.mark.parametrize("raw", ["不是 JSON", "[]", "{}", "", None, 42])
def test_parse_plan_reasons_invalid_is_empty(raw):
    from llm.parser import parse_plan_reasons

    p = parse_plan_reasons(raw)
    assert p.reasons == {} and p.order is None


def test_parse_plan_reasons_drops_bad_entries_keeps_good_ones():
    """**逐条降级**：一条坏解释不该连累别的（否则 LLM 一抖就全没了）。"""
    from llm.parser import parse_plan_reasons

    p = parse_plan_reasons(json.dumps({
        "reasons": {"a": "好", "b": "", "c": "   ", "d": 42, "e": None,
                    "f": "也好"},
        "order": ["a", "a"]}))          # 有重复 → 不是排列 → 作废
    assert p.reasons == {"a": "好", "f": "也好"}
    assert p.order is None


def test_parse_plan_reasons_truncates_long_text_with_a_marker():
    """超长截断**留标记**——截断是「解释被截了」，丢弃是「LLM 没解释」，两者要能区分。"""
    from llm.parser import MAX_REASON_CHARS, parse_plan_reasons

    p = parse_plan_reasons(json.dumps({"reasons": {"a": "x" * 500}}))
    assert len(p.reasons["a"]) == MAX_REASON_CHARS
    assert p.reasons["a"].endswith("…")


@pytest.mark.parametrize("bad_order", [
    ["a", 1], ["a", ""], ["a", "  "], "a", {}, ["a", "a"], [], None])
def test_parse_plan_reasons_rejects_unusable_orders(bad_order):
    from llm.parser import parse_plan_reasons

    assert parse_plan_reasons(json.dumps({"order": bad_order})).order is None


def test_parse_plan_reasons_records_extra_fields():
    """额外字段忽略但可见（与 recovery 契约同款：LLM 越权的痕迹不能静默消失）。"""
    from llm.parser import parse_plan_reasons

    p = parse_plan_reasons(json.dumps({"reasons": {}, "priority": {"a": 100}}))
    assert p.ignored_fields == {"priority": {"a": 100}}


def test_parse_plan_reasons_uses_raw_decode_for_nested_json():
    """P0 P2-6 的老坑：非贪婪正则会被嵌套 JSON 截断——用 `raw_decode` 增量解码。"""
    from llm.parser import parse_plan_reasons

    raw = 'prefix {"reasons": {"a": "nested {\\"x\\": 1}"}, "order": ["a"]} tail'
    p = parse_plan_reasons(raw)
    assert p.reasons["a"] == 'nested {"x": 1}'
    assert p.order == ("a",)


# ---------------------------------------------------------------------------
# Test Generator 契约（设计 §6.1；Task 3.4 / P3-12）
# ---------------------------------------------------------------------------


def test_generation_prompt_states_the_three_hard_rules():
    """prompt 必须写出三条硬约束（对应的**强制**在 parser 与 validator，不靠模型自觉）。"""
    from llm.prompt import build_generation_prompt

    prompt = build_generation_prompt(
        gaps=[{"from_screen": "SearchView", "to_screen": "DetailView",
               "trigger": "tap:SearchView.search_field",
               "category": "Happy Path", "observed_count": 9}],
        requirement="搜索无结果也要能返回", suite="smoke",
        bug_history=[{"testcase_id": "search_001",
                      "failure_type": "WAIT_TIMEOUT", "count": 3}])
    assert "[SYSTEM INSTRUCTIONS]" in prompt
    assert '"steps"' in prompt
    assert "不要输出 id / name / suite" in prompt, "id 由系统定（指纹去重靠它）"
    assert "不要声明 risk 或 idempotency" in prompt, "H4 / 7.4"
    assert "H4" in prompt
    # 可信区里带上真实上下文
    assert "SearchView -> DetailView" in prompt
    assert "搜索无结果也要能返回" in prompt and "smoke" in prompt
    assert "search_001" in prompt and "WAIT_TIMEOUT" in prompt


def test_generation_prompt_has_no_untrusted_zone():
    """只喂仓库派生数据（gap / 需求 / 历史）——**没有**运行时观测，故无不可信区。"""
    from llm.prompt import build_generation_prompt

    prompt = build_generation_prompt(
        gaps=[{"from_screen": "A", "to_screen": "B", "trigger": "tap:A.x",
               "category": "Happy Path", "observed_count": 1}])
    assert "[UNTRUSTED" not in prompt


def test_generation_prompt_action_and_condition_vocab_match_schema():
    """prompt 里列的 action/条件词表必须与 schema 的 Literal **同源**。

    两处各写一份 → 模型按 prompt 编出的动作/条件名会走到 schema 才炸，而错误
    信息是 pydantic 的（人看不出是模型编的）。这里钉住「派生自 schema」这件事
    本身（`llm/prompt.py` 用 `get_args(...)` 取，不手抄）。
    """
    from typing import get_args

    from llm.prompt import GENERATOR_ACTIONS, GENERATOR_CONDITIONS
    from testcase.schema import ActionStep, AssertionSpec, WaitSpec

    assert set(GENERATOR_ACTIONS) == set(
        get_args(ActionStep.model_fields["action"].annotation))
    # 条件 = WaitSpec ∪ AssertionSpec（两份 Literal 各自成词表）
    assert set(GENERATOR_CONDITIONS) == set(
        get_args(WaitSpec.model_fields["condition"].annotation)) | set(
        get_args(AssertionSpec.model_fields["condition"].annotation))
    assert "active" in GENERATOR_CONDITIONS, "屏 wait 的 active 必须在内"
    assert "element_count" in GENERATOR_CONDITIONS, "元素断言的条件必须在内"


def test_parse_candidate_steps_happy_path():
    from llm.parser import parse_candidate_steps

    p = parse_candidate_steps(json.dumps({"steps": [
        {"action": "launch_app"},
        {"action": "tap", "target": "HomeView.go_search"},
        {"wait_for": {"target": "screen:SearchView", "condition": "active",
                      "timeout": 10}},
        {"assertion": {"target": "SearchView.search_results",
                       "condition": "element_count", "expected": 0,
                       "timeout": 5}}]}))
    assert p.valid and len(p.steps) == 4
    assert p.steps[1] == {"action": "tap", "target": "HomeView.go_search"}
    assert p.ignored_fields == {}


@pytest.mark.parametrize("raw", ["不是 JSON", "[]", "{}", "", None, 42,
                                 '{"steps": []}', '{"steps": "tap"}',
                                 '{"steps": [{"action": "teleport"}]}',
                                 '{"steps": [{"nope": 1}]}',
                                 '{"steps": [{"action": "tap", "target": 42}]}',
                                 '{"steps": [{"wait_for": {"target": "screen:X",'
                                 ' "condition": "maybe"}}]}',
                                 '{"steps": [{"wait_for": {}}]}'])
def test_parse_candidate_steps_invalid_is_empty(raw):
    """任何不合法的形态 → `valid=False`（**不抛异常**，与另两个契约同款）。"""
    from llm.parser import parse_candidate_steps

    assert not parse_candidate_steps(raw).valid


def test_parse_candidate_steps_is_all_or_nothing():
    """**任一步不合法 → 整条作废**：候选是一个动作序列，丢一步就是另一条用例。"""
    from llm.parser import parse_candidate_steps

    p = parse_candidate_steps(json.dumps({"steps": [
        {"action": "launch_app"},
        {"action": "teleport"}]}))
    assert p.steps == [] and not p.valid


def test_parse_candidate_steps_records_ignored_fields():
    """H4：模型越权声明的 `risk` / `idempotency` 与自编的 `id` 必须**可见**。"""
    from llm.parser import parse_candidate_steps

    p = parse_candidate_steps(json.dumps({
        "steps": [{"action": "tap", "target": "HomeView.go_search",
                   "risk": "LOW", "idempotency": "IDEMPOTENT",
                   "goto": "next"}],
        "id": "model_invented_id", "name": "模型编的名字"}))
    assert p.valid
    assert p.steps == [{"action": "tap", "target": "HomeView.go_search"}]
    assert p.ignored_fields["id"] == "model_invented_id"
    assert p.ignored_fields["steps[0].risk"] == "LOW"
    assert p.ignored_fields["steps[0].idempotency"] == "IDEMPOTENT"
    assert p.ignored_fields["steps[0].goto"] == "next"


def test_parse_candidate_steps_uses_raw_decode_for_nested_json():
    """P0 P2-6 的老坑：非贪婪正则会截断嵌套 JSON（与另两个契约同款）。"""
    from llm.parser import parse_candidate_steps

    raw = ('前言 {"steps": [{"action": "tap", "target": "A.b",'
           ' "postcondition": {"target": "screen:B", "condition": "active"}}]}'
           ' 后记')
    p = parse_candidate_steps(raw)
    assert p.valid
    assert p.steps[0]["postcondition"]["target"] == "screen:B"


def test_contract_layer_defers_precise_rules_to_p1_schema():
    """分层：契约层只挡「模型编的词」，**精确约束归 P1 schema**（F5）。

    `wait_for screen:X condition=visible` —— `visible` 在 `WaitSpec` 的词表里
    （对**元素**成立），但屏语义下只有 `active` 合法。契约层放行它、由
    `validate_schema` 的 lint 拒掉：这是**刻意**的（在这里重写一遍屏/元素
    条件矩阵，就是第二份 schema 校验器）。
    """
    from llm.parser import parse_candidate_steps

    p = parse_candidate_steps(json.dumps({"steps": [
        {"wait_for": {"target": "screen:X", "condition": "visible"}}]}))
    assert p.valid, "契约层不该重写 schema 的屏/元素条件矩阵"


def test_parse_candidate_steps_accepts_launch_and_terminate():
    """`launch_app` / `terminate_app` 不在 10.4 的**恢复**白名单里，
    但它们是合法**用例**动作（生成器另有一张表）。"""
    from llm.parser import parse_candidate_steps

    p = parse_candidate_steps(json.dumps({"steps": [
        {"action": "launch_app"}, {"action": "terminate_app"}]}))
    assert p.valid and [s["action"] for s in p.steps] == ["launch_app",
                                                          "terminate_app"]
