"""Task 2.2 / P3-06：`priority_score` 纯函数 + `planner/risk.py` 转发。

plan Steps 的判据：评分公式纯函数化（F13）；**CRITICAL 用例无论 impact/history 多低
都进顶部区间**；`planner/risk.py` 与 P1 对同一输入同结果（**import 级断言 + 行为
断言**）。
"""
from __future__ import annotations

import pytest

from executor.policy import effective_risk as p1_effective_risk
from planner.prioritizer import (HISTORY_WEIGHT, IMPACT_WEIGHT, RISK_FLOOR,
                                 TestCaseMeta, order_key, priority_score)
from planner.risk import case_risk
from testcase.schema import Risk
from tests.fault_injection.fi_support import drift_repo


def _meta(case_id="login_001", elements=()) -> TestCaseMeta:
    return TestCaseMeta(testcase_id=case_id, element_ids=tuple(elements))


# --- 公式 ---------------------------------------------------------------------


def test_impact_hit_contributes_its_weight():
    assert priority_score(_meta(), {"login_001"}, 0.0, Risk.LOW) == IMPACT_WEIGHT
    assert priority_score(_meta(), {"other"}, 0.0, Risk.LOW) == 0
    assert priority_score(_meta(), set(), 0.0, Risk.LOW) == 0


def test_history_contributes_rate_times_its_weight():
    assert priority_score(_meta(), set(), 0.5, Risk.LOW) == HISTORY_WEIGHT * 0.5
    assert priority_score(_meta(), set(), 1.0, Risk.LOW) == HISTORY_WEIGHT
    assert priority_score(_meta(), {"login_001"}, 0.5,
                          Risk.LOW) == IMPACT_WEIGHT + HISTORY_WEIGHT * 0.5


def test_is_a_pure_function():
    """F13：同输入必同输出，且**不依赖调用顺序/时间**。"""
    args = (_meta(), {"login_001"}, 0.25, Risk.LOW)
    first = priority_score(*args)
    for _ in range(5):
        assert priority_score(*args) == first


def test_returns_an_int_within_the_design_range():
    """设计 §5.1：`priority: int  # 0-100`。"""
    for risk in Risk:
        for hit in (set(), {"login_001"}):
            for rate in (0.0, 0.5, 1.0):
                v = priority_score(_meta(), hit, rate, risk)
                assert isinstance(v, int) and 0 <= v <= 100, (risk, hit, rate, v)


@pytest.mark.parametrize("risk,floor", sorted(RISK_FLOOR.items(),
                                              key=lambda kv: kv[0].value))
def test_risk_floor_lifts_regardless_of_impact_and_history(risk, floor):
    """设计 §5.3：「CRITICAL/HIGH 强制高优先级，**不管改动大小**」。

    最小输入（不在受影响集合、历史失败率 0）也必须进**顶部区间**。
    """
    assert priority_score(_meta(), set(), 0.0, risk) >= floor


def test_critical_always_outranks_high():
    """CRITICAL 的 floor(90) 严格高于 base 上限(40+30=70)，所以它**必**高于 HIGH。"""
    worst_critical = priority_score(_meta(), set(), 0.0, Risk.CRITICAL)
    best_non_critical = max(
        priority_score(_meta(), {"login_001"}, 1.0, r)
        for r in (Risk.LOW, Risk.MEDIUM, Risk.HIGH))
    assert worst_critical > best_non_critical


def test_high_ties_with_the_top_non_high_case_by_arithmetic():
    """⚠️ **已知的算术后果**：HIGH 的 70 与「impact 命中 + 历史满」的 70 **打平**。

    base 上限恰好是 40+30=70，所以 HIGH 的 floor 落在同一个点上。plan 的判据是
    「进**顶部区间**」（≥70），不是「严格高于所有非 HIGH」——平局由
    `order_key` 的 `testcase_id` 字典序裁决。这条断言把这个事实钉住（将来调参时
    它会红，提醒改的人是有意为之还是碰巧）。
    """
    high_floor = priority_score(_meta(), set(), 0.0, Risk.HIGH)
    top_low = priority_score(_meta(), {"login_001"}, 1.0, Risk.LOW)
    assert high_floor == top_low == 70


@pytest.mark.parametrize("bad", [1.5, -0.1, 2, "0.5", None, True])
def test_history_out_of_range_or_wrong_type_fails_loud(bad):
    """失败率越界 → `ValueError`（**不静默夹取**）。

    与 `planner/models.py` 对 `priority ∈ [0,100]` 的处理同一条理由：越界是**统计
    口径的 bug**（分子分母弄反之类），夹到 1 会让「算错了」表现为「历史一直很差」。
    """
    with pytest.raises(ValueError, match="history"):
        priority_score(_meta(), set(), bad, Risk.LOW)


def test_risk_is_not_accepted_as_history():
    """⚠️ `Risk` 是 `int` 枚举 → 只判 `(int, float)` 会把它**当成失败率**。

    `class Risk(int, enum.Enum)`（`testcase/schema.py`）使
    `isinstance(Risk.LOW, (int, float))` 为真。探针（review_p3_task22 P3-1）：
    `priority_score(m, set(), Risk.LOW, Risk.LOW)` 曾返回 **30**——`Risk.LOW` 被读成
    失败率 1.0，白拿满分历史分。闸门必须显式排除它。
    """
    with pytest.raises(ValueError, match="history"):
        priority_score(_meta(), set(), Risk.LOW, Risk.LOW)


@pytest.mark.parametrize("bad", ["CRITICAL", "critical", "HIGH", 4, None])
def test_wrong_risk_type_fails_loud(bad):
    """`risk` 不是 `Risk` → `ValueError`（**不静默不加 floor**）。

    `RISK_FLOOR.get(非 Risk)` 返回 `None` → 走 `if floor is not None` 的另一支 →
    CRITICAL 的 floor **静默失效**：分数偏低、用例在 Plan 里沉底，错误落在
    「看不见」那一侧（与 `planner/risk.py` docstring 里的同一条理由）。
    """
    with pytest.raises(ValueError, match="risk"):
        priority_score(_meta(), set(), 0.0, bad)


def test_valid_risk_still_works_after_the_gate():
    """闸门不能误伤：`Risk` 的四个成员都照常（有 floor 的抬到 floor，没有的不抬）。"""
    for risk in Risk:
        got = priority_score(_meta(), set(), 0.0, risk)
        assert got == RISK_FLOOR.get(risk, 0), risk


@pytest.mark.parametrize("rate,expected",
                         [(0.05, 2), (0.15, 5), (0.25, 8), (0.35, 11),
                          (0.45, 14)])
def test_history_rounding_is_half_up(rate, expected):
    """半分**一律进位**（不是 `round()` 的银行家舍入）。

    `round()` 下 `30×0.15=4.5→4` 而 `30×0.05=1.5→2`——方向随尾数奇偶翻转，
    「失败率的 k 倍」与「得分的 k 倍」对不上（review_p3_task22 小观察 1）。
    """
    assert priority_score(_meta(), set(), rate, Risk.LOW) == expected


def test_impact_accepts_any_iterable():
    """`impact` 是 `Iterable[str]`：非 set 的形态（list / 生成器）也照常工作。

    （实现里对已经是 `set`/`frozenset` 的入参不重建，见 review_p3_task22 小观察 5
    ——这条同时钉住那条短路没有改变语义。）
    """
    assert priority_score(_meta(), ["login_001"], 0.0,
                          Risk.LOW) == IMPACT_WEIGHT
    assert priority_score(_meta(), (i for i in ["login_001"]), 0.0,
                          Risk.LOW) == IMPACT_WEIGHT


def test_risk_floor_is_read_only():
    """`RISK_FLOOR` 是**只读**映射：校准入口只该是常量定义处。

    它是模块级对象且被 `planner/__init__.py` re-export——裸 `dict` 意味着任何调用方
    `planner.RISK_FLOOR[Risk.HIGH] = 0` 都会**全局生效**（review_p3_task22 小观察 4）。
    `MappingProxyType` 对读取透明（`.get` / `.items` / `[]` 不变），只堵写入。
    """
    import planner

    assert planner.RISK_FLOOR is RISK_FLOOR
    assert RISK_FLOOR[Risk.HIGH] == 70
    with pytest.raises(TypeError):
        RISK_FLOOR[Risk.HIGH] = 0


# --- 排序键 -------------------------------------------------------------------


def test_order_key_is_score_desc_then_id_asc():
    """同分次序 = `testcase_id` 字典序（plan Task 2.2 明文）。"""
    entries = [("b_002", 70), ("a_001", 70), ("c_003", 90)]
    ordered = sorted(entries, key=lambda e: order_key(e[1], e[0]))
    assert [e[0] for e in ordered] == ["c_003", "a_001", "b_002"]


def test_order_key_is_total_and_deterministic():
    """键是全序（分数 + id），所以排序结果与输入顺序无关。"""
    entries = [("x", 50), ("y", 50), ("z", 10)]
    a = sorted(entries, key=lambda e: order_key(e[1], e[0]))
    b = sorted(reversed(entries), key=lambda e: order_key(e[1], e[0]))
    assert a == b == [("x", 50), ("y", 50), ("z", 10)]


# --- planner/risk.py：转发 P1 -------------------------------------------------


def test_risk_module_forwards_the_p1_function():
    """**import 级断言**：`planner.risk.effective_risk` 就是 P1 那个函数对象。

    行为断言挡不住「有人抄了一份判定逻辑、恰好结果一样」——这条挡得住。
    """
    import planner.risk as mod

    assert mod.effective_risk is p1_effective_risk


def test_case_risk_matches_p1_for_the_same_input(tmp_path):
    """**行为断言**：同一输入下与 P1 同结果（逐元素比对）。"""
    repo = drift_repo(tmp_path)
    for element_id, screen in (("login_button", "HomeView"),
                               ("pay_button", "HomeView"),
                               ("confirm_pay_button", "HomeView")):
        got = case_risk(_meta(elements=[f"{screen}.{element_id}"]), repo,
                        build="local")
        eff = repo.resolve(f"{screen}.{element_id}", build="local")
        assert got is p1_effective_risk(element=eff.risk, element_id=eff.id)


def test_case_risk_takes_the_max_not_the_mean(tmp_path):
    """取 **max**：一个 CRITICAL 元素不该被同用例里的 LOW 元素稀释。"""
    repo = drift_repo(tmp_path)
    mixed = case_risk(
        _meta(elements=["HomeView.login_button", "HomeView.confirm_pay_button"]),
        repo, build="local")
    assert mixed is Risk.CRITICAL
    assert case_risk(_meta(elements=["HomeView.login_button"]), repo,
                     build="local") is Risk.LOW


def test_case_risk_defaults_to_low_without_elements(tmp_path):
    repo = drift_repo(tmp_path)
    assert case_risk(_meta(), repo, build="local") is Risk.LOW


def test_case_risk_refuses_a_wrong_meta_object(tmp_path):
    """传错对象 / 字段名拼错 → **报错**，不静默返回 `LOW`。

    原先用 `getattr(tc_meta, "element_ids", ())` 兜底，把「用例根本没有/拼错了元素
    字段」与「用例确实不涉及任何元素」压成同一个 `Risk.LOW`——而后者是**有意为之**
    的兜底（见上一条测试）。两种语义混在一个 LOW 里，正是本函数 docstring 自己
    写下的「同一个道理不能对自己网开一面」（review_p3_task22 P3-2：探针实测传错
    对象时 `resolve` **根本没被调用**，静默返回 LOW）。
    """
    class _Typo:
        element_id = ("HomeView.login_button",)      # 少了一个 s

    repo = drift_repo(tmp_path)
    with pytest.raises(AttributeError, match="element_ids"):
        case_risk(_Typo(), repo, build="local")


def test_case_risk_does_not_swallow_unresolvable_elements(tmp_path):
    """解析不到的元素**照原样上抛**——不跳过、不当 LOW。

    一个 CRITICAL 元素因漂移而解析失败时，静默按 LOW 计会让它在 Plan 里**沉底**：
    错误的方向恰好是「看不见」那一侧。
    """
    from repository.resolver import UnknownReferenceError

    repo = drift_repo(tmp_path)
    with pytest.raises(UnknownReferenceError):
        case_risk(_meta(elements=["HomeView.ghost_button_typo"]), repo,
                  build="local")


def test_case_meta_is_frozen():
    """frozen dataclass：属性重绑被拒（收窄到具体异常类型，见 review_p3_task22 §五）。

    `FrozenInstanceError` 是 `AttributeError` 的子类——`pytest.raises(Exception)`
    会把「任何异常」都算通过，与「冻结生效」不等义。
    """
    import dataclasses

    meta = _meta()
    with pytest.raises(dataclasses.FrozenInstanceError):
        meta.testcase_id = "other"
