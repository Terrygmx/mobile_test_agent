"""Task 2.2 / P2-04：Runtime Guard 测试（设计 5 节 + 4.7 表 + E1 红线）。

三层：
  1. guard_candidate 共享链逐分支（4.7 表 record_as_sample 矩阵逐行）；
  2. E2：effective_risk 走 P1 compute_effective_risk（max 规则，不信任
     LLM 自报）；
  3. **E1 红线（双向）**：同一输入下 LLM 候选校验（agent/recovery._llm_stage）
     与 Experience Guard（experience_runtime_guard）的结论逐位一致——
     防两套规则漂移，这是 P2-04 的存在理由。

H18：FakeDriver/FakeRepo/FakeLLM，离设备离网络。
"""
from __future__ import annotations

import pytest

from experience.models import Experience, ExperienceStatus
from experience.runtime_guard import (
    GUARD_REASON_TO_LLM_FAILURE,
    GuardResult,
    RuntimeContext,
    compute_effective_risk,
    experience_runtime_guard,
    guard_candidate,
)
from executor.policy import Idempotency, Risk
from repository.loader import LocatorStrategy
from testcase.schema import Risk as SchemaRisk

LOW_STRATEGY = LocatorStrategy(type="accessibility_id",
                               value="signin_button", origin="manual")


class FakeEl:
    """get_attribute("type") 可编排的替身元素。"""

    def __init__(self, etype: str = "XCUIElementTypeButton"):
        self._t = etype

    def get_attribute(self, name):
        return self._t if name == "type" else ""


class FakeFindAllExecutor:
    """find_all 按脚本弹元素（Experience Guard 路径用）。"""

    def __init__(self, script):
        self.script = list(script)

    def find_all(self, strategy):
        items = self.script[min(len(self.script) - 1, 0):] or []
        return self.script


def _ctx(screen="HomeView", expected_type="button",
         risk: Risk | None = Risk.LOW) -> RuntimeContext:
    return RuntimeContext(current_screen=screen, expected_type=expected_type,
                          effective_risk=risk)


def _exp(**kw) -> Experience:
    d = dict(
        experience_id="exp_001", app_id="com.demo", screen_id="HomeView",
        target_id="login_button", strategy=LOW_STRATEGY,
        origin="LLM_ACCEPTED_RECOVERY", status=ExperienceStatus.VERIFIED,
        seed_run_id="run_a", seed_step_id=1, seed_recovery_review_id=7,
    )
    d.update(kw)
    return Experience(**d)


def _guard(**kw) -> GuardResult:
    d = dict(current_screen="HomeView", candidate_screen="HomeView",
             find=lambda: [FakeEl()], expected_type="button",
             effective_risk=Risk.LOW)
    d.update(kw)
    return guard_candidate(**d)


# --- 1. 4.7 表逐行（record_as_sample 矩阵） -------------------------------------


def test_screen_unknown_miss_no_sample():
    r = _guard(current_screen=None)
    assert (r.outcome, r.reason, r.record_as_sample) == \
        ("MISS", "SCREEN_UNKNOWN", False)


def test_screen_mismatch_miss_no_sample():
    r = _guard(candidate_screen="ProfileView")
    assert (r.outcome, r.reason, r.record_as_sample) == \
        ("MISS", "SCREEN_MISMATCH", False)


def test_unregistered_candidate_miss_no_sample():
    """候选未登记（candidate_screen=None）= 无法证明属于当前屏 →
    fail-closed MISS（9.3-3 定档）。"""
    r = _guard(candidate_screen=None)
    assert (r.outcome, r.reason, r.record_as_sample) == \
        ("MISS", "TARGET_UNREGISTERED", False)


def test_target_not_found_block_counts_as_failure():
    r = _guard(find=lambda: [])
    assert (r.outcome, r.reason, r.record_as_sample) == \
        ("BLOCK", "TARGET_NOT_FOUND", True)


def test_target_not_found_on_find_exception():
    def boom():
        raise KeyError("gone")
    r = _guard(find=boom)
    assert (r.outcome, r.reason, r.record_as_sample) == \
        ("BLOCK", "TARGET_NOT_FOUND", True)


def test_target_ambiguous_block_counts_as_failure():
    r = _guard(find=lambda: [FakeEl(), FakeEl()])
    assert (r.outcome, r.reason, r.record_as_sample) == \
        ("BLOCK", "TARGET_AMBIGUOUS", True)


def test_type_mismatch_block_counts_as_failure():
    r = _guard(find=lambda: [FakeEl("XCUIElementTypeTextField")])
    assert (r.outcome, r.reason, r.record_as_sample) == \
        ("BLOCK", "TYPE_MISMATCH", True)


def test_type_check_normalized_xcui_prefix():
    """XCUIElementTypeButton ↔ button（9.3-2 定档）。"""
    assert _guard(find=lambda: [FakeEl("XCUIElementTypeButton")]).outcome \
        == "EXECUTE"


def test_type_check_skipped_when_expected_empty():
    r = _guard(expected_type=None, find=lambda: [FakeEl("")])
    assert r.outcome == "EXECUTE"


def test_risk_blocked_block_no_sample():
    r = _guard(effective_risk=Risk.HIGH)
    assert (r.outcome, r.reason, r.record_as_sample) == \
        ("BLOCK", "RISK_BLOCKED", False)


def test_risk_unknown_fail_closed_no_sample():
    """None（无可证明的风险）按最高处理——4.2/9.3-4 同源 fail-closed。"""
    r = _guard(effective_risk=None)
    assert (r.outcome, r.reason, r.record_as_sample) == \
        ("BLOCK", "RISK_BLOCKED", False)


def test_all_pass_execute_counts_sample():
    r = _guard()
    assert (r.outcome, r.record_as_sample) == ("EXECUTE", True)


# --- 2. E2：effective_risk 走 P1 max 规则 ---------------------------------------


def test_effective_risk_is_p1_max_rule():
    """E2：Guard 的 risk 输入来自 P1 compute_effective_risk（max(step/
    element/screen/env)），不信任 LLM 自报——step HIGH 压过 element LOW。"""
    risk = compute_effective_risk(step=SchemaRisk.HIGH,
                                  element=SchemaRisk.LOW)
    assert risk is SchemaRisk.HIGH
    r = _guard(effective_risk=risk)
    assert (r.outcome, r.reason) == ("BLOCK", "RISK_BLOCKED")


def test_effective_risk_string_rejected_loudly():
    """review_p2_task22 P3-1 类型地雷：字符串 "LOW" 曾静默拦成
    RISK_BLOCKED（不炸、不报错、只掉成功率）——入口断言使其当场显形。"""
    with pytest.raises(TypeError, match="must be Risk"):
        _guard(effective_risk="LOW")


# --- 3. 10.1 Guard 复检 ----------------------------------------------------------


def test_policy_guard_violation_block_no_sample():
    class AlwaysBlock:
        def check(self, gctx=None):
            raise type("GuardViolation", (Exception,), {})("blocked")

    r = _guard(policy_check=lambda: AlwaysBlock().check())
    assert (r.outcome, r.reason, r.record_as_sample) == \
        ("BLOCK", "SECURITY_BLOCKED", False)


def test_policy_guard_non_guard_violation_reraised():
    def broken():
        raise RuntimeError("wiring bug")
    with pytest.raises(RuntimeError):
        _guard(policy_check=broken)


# --- 4. E1 红线：LLM 候选校验与 Experience Guard 双向一致 ------------------------
# 同一候选/同一屏/同一元素状态，分别走 agent/recovery._llm_stage（FakeLLM）
# 与 experience_runtime_guard——(outcome, reason, record_as_sample) 必须
# 逐位一致（LLM 的 failure_type 由 GUARD_REASON_TO_LLM_FAILURE 映射）。


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.calls: list[str] = []

    def complete(self, prompt, timeout=20):
        self.calls.append(prompt)
        return self.reply


class FakeRepo:
    """resolve 按 (screen, risk) 表返回；未登记抛 KeyError。"""

    def __init__(self, entries):
        self.entries = entries   # {value: (screen, risk, type)}

    def resolve(self, ref, *, build):
        rid = ref.id if hasattr(ref, "id") else str(ref)
        if rid not in self.entries:
            raise KeyError(f"unregistered: {rid!r}")
        screen, risk, etype = self.entries[rid]
        from types import SimpleNamespace
        return SimpleNamespace(id=rid, screen=screen, risk=risk, type=etype)

    def elements_of(self, screen):
        return []


class FakeFindExecutor:
    """find_with/find_all 共用同一脚本游标（两路径喂同一元素状态）。"""

    def __init__(self, script):
        self.script = list(script)
        self.find_calls = 0

    def _next(self):
        item = self.script[min(self.find_calls, len(self.script) - 1)]
        self.find_calls += 1
        if isinstance(item, Exception):
            raise item
        return item

    def find(self, strategies):
        return self._next()

    def find_with(self, strategies):
        return self._next()

    def find_all(self, strategy):
        found = self._next()
        return found if isinstance(found, (list, tuple)) else [found]

    def page_source(self):
        return "<App/>"

    def input(self, strategies, value):
        pass

    def tap(self, strategies):
        pass


def _llm_json(value="signin_button", conf=0.93):
    import json
    return json.dumps({"action": "tap",
                       "target": {"type": "accessibility_id",
                                  "value": value},
                       "scope": "HomeView", "reason": "renamed",
                       "confidence": conf})


def _engine_candidate_outcome(repo_entries, find_script, current_screen,
                              candidate="signin_button", conf=0.93,
                              expected_type="button"):
    """走 recovery._llm_stage 全链（FakeLLM），返回 (validate stage,
    failure_type, recovered)。"""
    from agent.context import RecoveryContext
    from agent.recovery import RecoveryEngine
    from executor.policy import FailurePhase, Idempotency
    llm = FakeLLM(_llm_json(value=candidate, conf=conf))
    engine = RecoveryEngine(repo=FakeRepo(repo_entries), llm=llm,
                            budget=None, sleep=lambda s: None)
    ex = FakeFindExecutor(find_script)
    ctx = RecoveryContext(
        failure_type="ELEMENT_NOT_FOUND",
        phase=FailurePhase.PRE_DISPATCH,
        element_id="login_button", screen_id="HomeView", action="tap",
        expected_type=expected_type,
        effective_idempotency=Idempotency.IDEMPOTENT,
        app_build="local", testcase_id="t1",
        find_with=lambda sts: ex.find_with(sts),
        redispatch=None,
        page_source=lambda: "<App><Node name='screen.HomeView'"
                            " visible='true'/></App>")
    r = engine.recover(ctx)
    validate = [s for s in r.detail["stages"]
                if s.get("stage") == "validate"]
    return (validate[-1] if validate else None), r.failure_type, r.recovered


def _experience_guard_outcome(repo_entries, find_script, current_screen,
                              expected_type="button"):
    """同一元素状态走 experience_runtime_guard，返回 (outcome, reason,
    record_as_sample)；repo_entries 为空 = 候选未登记——Experience 按定义
    只会来自已登记策略（无可对照物），返回 None 由测试只断言引擎侧。"""
    if not repo_entries:
        return None
    from types import SimpleNamespace
    screen, risk, _ = repo_entries["signin_button"]
    exp = _exp(screen_id=screen)
    # risk 来自 Repository 登记元素（E2 同源）
    ctx = RuntimeContext(current_screen=current_screen,
                         expected_type=expected_type, effective_risk=risk)
    r = experience_runtime_guard(exp, ctx, FakeFindExecutor(find_script))
    return r.outcome, r.reason, r.record_as_sample


LLM_FAILURE_BY_REASON = {v: k for k, v in GUARD_REASON_TO_LLM_FAILURE.items()}


@pytest.mark.parametrize(
    "repo_entries,find_script,current_screen,expected_reason",
    [
        # 全过 → EXECUTE
        ({"signin_button": ("HomeView", SchemaRisk.LOW, "button")},
         [FakeEl()], "HomeView", None),
        # 数量 0
        ({"signin_button": ("HomeView", SchemaRisk.LOW, "button")},
         [KeyError("gone")], "HomeView", "TARGET_NOT_FOUND"),
        # 数量 ≥2
        ({"signin_button": ("HomeView", SchemaRisk.LOW, "button")},
         [[FakeEl(), FakeEl()]], "HomeView", "TARGET_AMBIGUOUS"),
        # 类型不符
        ({"signin_button": ("HomeView", SchemaRisk.LOW, "button")},
         [FakeEl("XCUIElementTypeTextField")], "HomeView", "TYPE_MISMATCH"),
        # 屏不符（登记在别的屏）
        ({"signin_button": ("ProfileView", SchemaRisk.LOW, "button")},
         [FakeEl()], "HomeView", "SCREEN_MISMATCH"),
        # 未登记（fail-closed）
        ({}, [FakeEl()], "HomeView", "TARGET_UNREGISTERED"),
        # 风险拦截（候选登记 risk=HIGH）
        ({"signin_button": ("HomeView", SchemaRisk.HIGH, "button")},
         [FakeEl()], "HomeView", "RISK_BLOCKED"),
    ],
)
def test_e1_red_line_llm_and_experience_agree(
        repo_entries, find_script, current_screen, expected_reason):
    """E1 红线：同一输入下两套路径结论逐位一致（outcome/reason/sample）。"""
    g = _experience_guard_outcome(repo_entries, find_script, current_screen)
    stage, llm_failure, recovered = _engine_candidate_outcome(
        repo_entries, find_script, current_screen)

    if g is None:
        # 未登记候选：无 Experience 可对照物（Experience 按定义来自已
        # 登记策略）——只断言引擎侧 fail-closed 映射
        assert expected_reason == "TARGET_UNREGISTERED"
        assert llm_failure == GUARD_REASON_TO_LLM_FAILURE[expected_reason]
        assert recovered is False
        return
    g_out, g_reason, g_sample = g
    assert g_reason == expected_reason
    assert (g_out, g_reason, g_sample) == \
        (stage["outcome"], stage["reason"], stage["record_as_sample"]), \
        "E1 红线：Guard 与 LLM 校验链的 validate 段必须逐位一致"
    # LLM 的 failure_type = 映射表（EXECUTE 时 recovered=True）
    if expected_reason is None:
        assert recovered is True
    else:
        assert llm_failure == GUARD_REASON_TO_LLM_FAILURE[expected_reason]
        assert recovered is False
