"""Task 2.4 / P2-03+04+05 集成：Recovery 引擎消费真 Experience Store。

口径（写死在本文件，测试按此钉）：

1. **命中即免 LLM**（设计 3.1）：Experience 命中并执行成功 → `kind="experience"`
   / `recovered_kind="RECOVERED_EXPERIENCE"`，且 LLM 调用数为 0、budget 未消耗。
2. **E11 的样本口径**（设计 4.7 表）：只有 `record_as_sample` 的判定才写
   `experience_runs`；Screen 不符/当前屏未知（MISS）与风险拦截（RISK_BLOCKED）
   **不写**——「此刻不适用/不被允许」不是「用了但错了」。
3. **多候选回落**（设计 5.1）：BLOCK 或执行失败 → 换下一个；全部用尽 → 回落
   LLM（`RECOVERED_LLM`）。
4. **执行结果不可观测就不声称成功**：aux（redispatch=None）产出**待定**样本
   （`result=None` + `pending_observation`），由**能观测**的调用方回填
   （`resolve_deferred_sample`）；postcondition 观测不到（UNKNOWN）→ 不写样本、
   不返回 recovered。绝不预置 SUCCESS。
5. **E2 / 接线地雷 ④**：`effective_risk` 必须是 `Risk` 枚举——真实枚举流经全链
   得 EXECUTE；字符串会被 Guard 入口断言当场拦下（不静默降成功率）。
6. **E8 观测面**：`screen_fingerprint_match` 如实记录差异（REVALIDATION 动作在 M3）。
7. **step_id 不在引擎手里**：恢复发生在 `record_step()` 之前，样本 payload
   里没有 step_id，由 trace 写入方补（`experience.store.record_sample_runs`）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.context import RecoveryContext
from agent.recovery import RecoveryEngine, resolve_deferred_sample
from executor.policy import FailurePhase, Idempotency
from experience import SQLiteExperienceStore
from experience.models import CandidateSeed
from experience.store import record_sample_runs
from llm.budget import LLMBudget
from repository.loader import LocatorStrategy
from testcase.schema import Risk
from tests.fault_injection.fi_support import El, FakeLLM, llm_json

PRE = FailurePhase.PRE_DISPATCH
APP = "com.matrix.app"
PAGE = "<App><Node name='screen.HomeView' visible='true'/></App>"
LOGIN_PAGE = "<App><Node name='screen.LoginView' visible='true'/></App>"
OTHER_PAGE = ("<App><Node name='screen.HomeView' visible='true'/>"
              "<Node name='captcha_field' visible='true'/></App>")
# 页面没有任何已登记 marker（屏识别必然失败）——用于钉住「回落登记屏」口径
UNKNOWN_PAGE = "<App><Node name='mystery_widget' visible='true'/></App>"


class _Repo:
    """resolve 按 (screen, risk, type) 表返回；未登记抛 KeyError（=未登记）。"""

    def __init__(self, entries=None, screens=("HomeView",)):
        self.entries = entries or {}
        self.generated_screens = {
            s: SimpleNamespace(marker=f"screen.{s}") for s in screens}
        self.override_screens = {}

    def screen_kind_hint(self, screen):
        return "page"

    def elements_of(self, screen):
        return []

    def resolve(self, ref, *, build):
        rid = ref.id if hasattr(ref, "id") else str(ref)
        if rid not in self.entries:
            raise KeyError(f"unregistered: {rid!r}")
        screen, risk, etype = self.entries[rid]
        return SimpleNamespace(id=rid, screen=screen, risk=risk, type=etype)


def _seed(store, *, screen="HomeView", target="login_button",
          value="signin_button", review_id=1, status=None,
          fingerprint=None):
    """建一条 CANDIDATE（走真 create_candidate，不绕过 E5 种子闸门）。"""
    exp = store.create_candidate(CandidateSeed(
        review_id=review_id, recovery_id=review_id,
        seed_run_id="run_seed", seed_step_id=1,
        seed_recovery_review_id=review_id,
        app_id=APP, screen_id=screen, target_id=target,
        strategy=LocatorStrategy(type="accessibility_id", value=value,
                                 origin="experience")))
    if status is not None or fingerprint is not None:
        import sqlite3
        conn = sqlite3.connect(store._path)
        try:
            if status is not None:
                conn.execute("UPDATE experiences SET status=? WHERE"
                             " experience_id=?", (status, exp.experience_id))
            if fingerprint is not None:
                conn.execute("UPDATE experiences SET last_screen_fingerprint=?"
                             " WHERE experience_id=?",
                             (fingerprint, exp.experience_id))
            conn.commit()
        finally:
            conn.close()
    return exp


@pytest.fixture()
def store(tmp_path):
    return SQLiteExperienceStore(tmp_path / "experience.db")


def _ctx(**kw) -> RecoveryContext:
    """动作步形态的 ctx（redispatch 有值 = 会真的执行）。"""
    dispatched: list = []

    def _default_redispatch(el):
        dispatched.append(el)

    defaults = dict(
        failure_type="ELEMENT_NOT_FOUND", phase=PRE,
        element_id="login_button", screen_id="HomeView", strategies=(),
        action="tap", expected_type="button",
        effective_risk=Risk.LOW,
        effective_idempotency=Idempotency.IDEMPOTENT,
        app_id=APP, app_build="local", run_id="run_now",
        testcase_id="tc1", page_source=lambda: PAGE,
        redispatch=_default_redispatch)
    defaults.update(kw)
    ctx = RecoveryContext(**defaults)
    return ctx, dispatched


def _engine(store, **kw):
    kw.setdefault("repo", None)
    kw.setdefault("sleep", lambda s: None)
    return RecoveryEngine(experience_store=store, **kw)


def _find_all_script(*results):
    """按调用序弹结果的 find_all（Exception 抛出）。"""
    box = {"i": 0}

    def _find_all(locator):
        i = min(box["i"], len(results) - 1)
        box["i"] += 1
        item = results[i]
        if isinstance(item, Exception):
            raise item
        return item
    return _find_all


# --- 1. 命中 + 执行成功 → RECOVERED_EXPERIENCE，LLM 零调用 -----------------


def test_hit_executes_and_never_calls_llm(store):
    _seed(store)
    llm, budget = FakeLLM([llm_json()]), None
    from llm.budget import LLMBudget
    budget = LLMBudget()
    ctx, dispatched = _ctx(find_all=lambda loc: [El()])
    r = _engine(store, llm=llm, budget=budget).recover(ctx)

    assert r.recovered and r.kind == "experience"
    assert r.detail["recovered_kind"] == "RECOVERED_EXPERIENCE"
    assert r.strategy == {"type": "accessibility_id", "value": "signin_button"}
    assert len(dispatched) == 1, "命中即执行（设计 5.1）"
    assert llm.calls == [] and budget.calls_used == 0, \
        "Experience 命中时根本不消耗 Budget（设计 3.1）"
    # 4.7：成功样本（payload 待落库，step_id 由 trace 写入方补）
    [sample] = r.detail["experience_runs"]
    assert sample["result"] == "SUCCESS"
    assert sample["uniqueness_count"] == 1
    assert sample["element_type_match"] is True
    assert sample["effective_risk"] == "LOW"
    assert sample["run_id"] == "run_now" and sample["app_build"] == "local"
    assert "step_id" not in sample


def test_hit_guard_observation_is_recorded_in_stage(store):
    _seed(store)
    ctx, _ = _ctx(find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)
    stages = [s for s in r.detail["stages"]
              if s["stage"] == "experience_candidate"]
    assert stages == [{"stage": "experience_candidate",
                       "experience_id": stages[0]["experience_id"],
                       "status": "CANDIDATE",
                       "outcome": "EXECUTE",
                       "reason": None,
                       "record_as_sample": True,
                       "screen_fingerprint_match": None,
                       "uniqueness_count": 1,
                       "execution": "SUCCESS"}]


# --- 2. Guard BLOCK → 换下一个候选（设计 5.1） -----------------------------


def test_ambiguous_candidate_blocks_and_moves_to_next(store):
    first = _seed(store, value="dup_button", review_id=1)
    second = _seed(store, value="signin_button", review_id=2)
    # Store 按 updated_at DESC 返回 → 先试 second；为让 first 先试，直接构造
    # 顺序（本任务排序是 Store 的 updated_at 简单序，M3 换 ranker）。
    ctx, dispatched = _ctx(find_all=_find_all_script([El(), El()], [El()]))
    r = _engine(store).recover(ctx)

    assert r.recovered and r.kind == "experience"
    assert r.detail["experience_id"] in (first.experience_id,
                                        second.experience_id)
    # 第一个候选记 FAILURE（TARGET_AMBIGUOUS 是「用了但错了」，计失败样本）
    failed = [s for s in r.detail["experience_runs"]
              if s["result"] == "FAILURE"]
    assert len(failed) == 1
    assert failed[0]["guard_reason"] == "TARGET_AMBIGUOUS"
    assert failed[0]["uniqueness_count"] == 2
    assert len(dispatched) == 1, "只有通过 Guard 的那个候选被执行"


def test_type_mismatch_is_failure_sample_and_falls_through(store):
    _seed(store)
    from llm.budget import LLMBudget
    llm = FakeLLM([llm_json()])
    # Guard 的数量观测端返回类型不符的元素
    ctx, dispatched = _ctx(
        find_all=lambda loc: [El("XCUIElementTypeTextField")],
        find_with=lambda sts: El("XCUIElementTypeButton"))
    r = _engine(store, repo=_Repo({"signin_button": ("HomeView", Risk.LOW,
                                                     "button")}),
                llm=llm, budget=LLMBudget()).recover(ctx)

    assert r.recovered and r.kind == "llm"
    assert r.detail["recovered_kind"] == "RECOVERED_LLM"
    assert len(dispatched) == 1, "只有 LLM 候选被执行（Experience 候选没通过）"
    failed = [s for s in r.detail["experience_runs"] if s["result"] == "FAILURE"]
    assert [f["guard_reason"] for f in failed] == ["TYPE_MISMATCH"]
    assert failed[0]["element_type_match"] is False


# --- 3. MISS（Screen 不符）不写样本（E11 / 矩阵 #4） ------------------------


def test_screen_mismatch_miss_writes_no_sample(store):
    """矩阵 #4：Experience 记录的屏 ≠ 运行时当前屏 → MISS，不计失败样本（E11）。

    注意 lookup 的键是**步骤登记的屏**（`ctx.screen_id`）——所以候选查得到，
    随后在 Guard 的 Screen 环被拦下。这正是设计要的形状：查表用主键，
    适用性由 Guard 判（设计 5 节）。
    """
    _seed(store, screen="HomeView")          # 记录在 HomeView
    ctx, dispatched = _ctx(page_source=lambda: LOGIN_PAGE,   # 当前在 LoginView
                           find_all=lambda loc: [El()])
    r = _engine(store,
                repo=_Repo(screens=("HomeView", "LoginView"))).recover(ctx)

    assert not r.recovered
    assert r.detail.get("experience_runs") in (None, [])
    entry = next(s for s in r.detail["stages"]
                 if s["stage"] == "experience_candidate")
    assert entry["outcome"] == "MISS" and entry["reason"] == "SCREEN_MISMATCH"
    assert entry["record_as_sample"] is False
    assert dispatched == [], "MISS 的候选不得执行"


def test_risk_blocked_writes_no_sample(store):
    """4.7 第 4 行：风险拦截 =「此 Experience 当前不被允许使用」→ 不记样本。

    接线地雷 ④ 的反面：这里传的是**真 Risk 枚举**（HIGH），所以 Guard 走到
    了风险分支而不是入口 TypeError。"""
    _seed(store)
    ctx, dispatched = _ctx(effective_risk=Risk.HIGH,
                           find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)

    assert not r.recovered
    assert r.detail.get("experience_runs") in (None, [])
    entry = next(s for s in r.detail["stages"]
                 if s["stage"] == "experience_candidate")
    assert entry["outcome"] == "BLOCK" and entry["reason"] == "RISK_BLOCKED"
    assert entry["record_as_sample"] is False
    assert dispatched == []


def test_string_risk_fails_loud_not_silently_blocked(store):
    """接线地雷 ④ 的正面：字符串 "LOW" 会被 Guard 入口断言当场拦下。

    静默 fail-closed 的版本不炸、不报错，只表现为「成功率莫名偏低」——
    那是最难查的一类 bug，所以 runtime_guard 显式 TypeError。"""
    _seed(store)
    ctx, _ = _ctx(effective_risk="LOW", find_all=lambda loc: [El()])
    with pytest.raises(TypeError, match="must be Risk"):
        _engine(store).recover(ctx)


def test_lookup_key_is_registered_screen_not_current(store):
    """lookup 用**步骤登记的屏**（Experience 主键第二段）作键——运行时当前屏
    不参与查表，它由 Guard 校验。

    这条钉住的是「为什么 SCREEN_MISMATCH 可达」：如果拿当前屏当键，记录的屏
    与当前屏不符时就永远查不到候选，Guard 的 Screen 环变成空转的死代码。
    反过来，若登记屏为空（aux 步解析不出屏）→ 键不全 → 如实报 incomplete_key
    而不是拿当前屏顶上（那会查到别的屏的经验）。"""
    _seed(store, screen="HomeView")
    ctx, _ = _ctx(screen_id="", find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)
    assert not r.recovered
    entry = next(s for s in r.detail["stages"] if s["stage"] == "experience")
    assert entry["outcome"] == "incomplete_key"


# --- 4. 全部候选用尽 → 回落 LLM（设计 5.1） --------------------------------


def test_all_candidates_blocked_falls_back_to_llm(store):
    _seed(store)
    from llm.budget import LLMBudget
    llm = FakeLLM([llm_json()])
    ctx, dispatched = _ctx(find_all=lambda loc: [El(), El()],
                           find_with=lambda sts: El())
    r = _engine(store, repo=_Repo({"signin_button": ("HomeView", Risk.LOW,
                                                     "button")}),
                llm=llm, budget=LLMBudget()).recover(ctx)

    assert r.recovered and r.kind == "llm"
    assert r.detail["recovered_kind"] == "RECOVERED_LLM"
    assert len(llm.calls) == 1, "候选用尽才回落 LLM"
    assert len(dispatched) == 1, "Experience 候选全被拦，只有 LLM 候选执行"
    stages = [s["stage"] for s in r.detail["stages"]]
    assert stages.index("experience") < stages.index("llm")
    assert {"stage": "experience", "outcome": "exhausted",
            "count": 1} in r.detail["stages"]


# --- 5. 非幂等 + postcondition（设计 6） ----------------------------------


def test_non_idempotent_postcondition_confirms_success(store):
    """设计 6：非幂等目标命中后不重试，postcondition 确认成功才记 SUCCESS。"""
    _seed(store)
    ctx, dispatched = _ctx(
        effective_idempotency=Idempotency.NON_IDEMPOTENT,
        has_postcondition=True, postcondition_check=lambda: True,
        find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)

    assert r.recovered and r.kind == "experience"
    [sample] = r.detail["experience_runs"]
    assert sample["result"] == "SUCCESS"
    entry = next(s for s in r.detail["stages"]
                 if s["stage"] == "experience_candidate")
    assert entry["postcondition"] is True


def test_non_idempotent_postcondition_unsatisfied_is_failure(store):
    _seed(store)
    ctx, _ = _ctx(effective_idempotency=Idempotency.NON_IDEMPOTENT,
                  has_postcondition=True, postcondition_check=lambda: False,
                  find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)

    assert not r.recovered
    [sample] = r.detail["experience_runs"]
    assert sample["result"] == "FAILURE"
    assert sample["guard_reason"] == "postcondition_not_satisfied"


def test_unobservable_postcondition_writes_no_sample(store):
    """观测故障（postcondition 抛异常）→ 不写样本、不声称恢复。

    猜一个 SUCCESS 会直接抬高 success_rate 把 Candidate 推向 VERIFIED——
    那是 E4/E11 级别的错误，不是记账瑕疵。"""
    _seed(store)

    def _boom():
        raise RuntimeError("postcondition probe died")

    ctx, _ = _ctx(effective_idempotency=Idempotency.NON_IDEMPOTENT,
                  has_postcondition=True, postcondition_check=_boom,
                  find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)
    assert not r.recovered
    assert r.detail.get("experience_runs") in (None, [])


# --- 6. aux（无 dispatch 语义）与追溯链缺失 --------------------------------


def test_aux_candidate_returns_strategy_with_deferred_sample(store):
    """aux（wait/assert）无 dispatch 语义：候选交调用方覆盖定位后重跑。

    执行结果此刻不可观测 → 产出**待定**样本（`result=None` +
    `pending_observation`），由调用方观测后回填；同时仍返回 strategy（否则
    调用方没东西可覆盖）。

    为什么不能「干脆不写」：只经 aux 命中的 Candidate 会 sample_count 恒为
    0、永远到不了 4.5 的 min_samples，报告上读成「从未被使用」——P2 的
    「知识积累」目标对 aux 目标整体失效（Task 2.4 评审 P2-1）。
    为什么不能「直接写 SUCCESS」：猜错的 SUCCESS 会抬高 success_rate 把
    Candidate 推向 VERIFIED（E4/E11 级别）。
    """
    _seed(store)
    ctx, _ = _ctx(action=None, redispatch=None, find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)

    assert r.recovered and r.kind == "experience"
    assert r.strategy == {"type": "accessibility_id", "value": "signin_button"}
    [sample] = r.detail["experience_runs"]
    assert sample["result"] is None and sample["pending_observation"] is True
    assert sample["guard_reason"] is None, \
        "还没观测到，就不是「用了但错了」——不得预置失败原因"
    entry = next(s for s in r.detail["stages"]
                 if s["stage"] == "experience_candidate")
    assert entry["execution"] == "not_dispatched"


def test_resolve_deferred_sample_fills_observed_outcome(store):
    """回填：调用方观测到成/败 → 待定样本翻成 4.7 的 SUCCESS/FAILURE。

    翻译留在引擎模块（4.7 单点），调用方只说「成没成」。
    """
    _seed(store)
    ctx, _ = _ctx(action=None, redispatch=None, find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)

    assert resolve_deferred_sample(r, succeeded=True) == 1
    assert r.detail["experience_runs"][0]["result"] == "SUCCESS"
    assert "pending_observation" not in r.detail["experience_runs"][0]
    # 幂等：同一结论再回填一次不会重复计数（标记已摘除）
    assert resolve_deferred_sample(r, succeeded=False) == 0


def test_resolve_deferred_sample_failure_keeps_reason(store):
    """观测到失败 → FAILURE + 原因（默认 AUX_RERUN_FAILED，可显式覆盖）。"""
    _seed(store)
    ctx, _ = _ctx(action=None, redispatch=None, find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)

    resolve_deferred_sample(r, succeeded=False, failure_reason="WAIT_TIMEOUT")
    [sample] = r.detail["experience_runs"]
    assert sample["result"] == "FAILURE"
    assert sample["guard_reason"] == "WAIT_TIMEOUT"


def test_resolve_deferred_sample_noop_on_plain_result(store):
    """动作步的样本没有待定标记 → 回填是 no-op（不误改已定的结果）。"""
    _seed(store)
    ctx, _ = _ctx(find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)

    assert r.detail["experience_runs"][0]["result"] == "SUCCESS"
    assert resolve_deferred_sample(r, succeeded=False) == 0
    assert r.detail["experience_runs"][0]["result"] == "SUCCESS"


# --- 6b. 10.1 复检与屏识别回落（评审 P3-1 / P2-2 的口径钉子） --------------


def test_experience_candidate_also_passes_10_1_guard(store):
    """10.1 复检对 Experience 候选同样生效。

    E1 的「同一输入同一结论」不止覆盖 Guard 链的六项判定，也覆盖 10.1：
    **同一候选策略不因「来自 Experience 而非 LLM」就免检**。此前 Experience
    路径不传 `policy_check`，注释还谎称「EXECUTE 后走正常 dispatch、其处自会
    过 Guard」——`StepRunner._dispatch_action` 里**没有任何 Guard**（只有
    `run_step` 对**原步骤的原元素**跑一次），那个兜底并不存在
    （Task 2.4 评审 P3-1 实锤）。
    """
    from executor.guard import BlockedTarget, EnvKind, Guard

    _seed(store, value="signin_button")
    repo = _Repo(entries={"signin_button": ("HomeView", Risk.LOW, "button")})
    guard = Guard(EnvKind.SANDBOX,
                  blocked_targets=(BlockedTarget.parse("element:signin_*"),))
    ctx, dispatched = _ctx(find_all=lambda loc: [El()])
    r = _engine(store, repo=repo, guard=guard).recover(ctx)

    assert not r.recovered and dispatched == [], "10.1 命中的候选绝不执行"
    entry = next(s for s in r.detail["stages"]
                 if s["stage"] == "experience_candidate")
    assert (entry["outcome"], entry["reason"]) == ("BLOCK", "SECURITY_BLOCKED")
    assert entry["record_as_sample"] is False, \
        "4.7：安全拦截不是「用了但错了」——不计样本"


def test_screen_identification_ran_and_failed_is_fail_closed(store):
    """识别**跑过但没结论** → `SCREEN_UNKNOWN → MISS`，候选不执行、不记样本。

    设计 §5/§4.7 的明文规则，此前被 `ctx.screen_id` 回落变成死代码。更要命的
    是那回落让 Experience 路径的屏校验**自比自**（`current_screen` 与
    `exp.screen_id` 都等于 `ctx.screen_id` → 永远相等 → Guard 第一环从不触发），
    于是「页面没有任何已登记 marker」时，只要恰好存在同名唯一元素，候选就会
    在**未确认屏**的情况下执行（Task 2.4 终审 P2-2 的收口）。
    """
    _seed(store)
    ctx, dispatched = _ctx(page_source=lambda: UNKNOWN_PAGE,
                           find_all=lambda loc: [El()])
    r = _engine(store, repo=_Repo()).recover(ctx)

    entry = next(s for s in r.detail["stages"] if s["stage"] == "screen")
    assert entry["outcome"] == "CURRENT_SCREEN_UNKNOWN", "屏确实没识别出来"
    assert not r.recovered and dispatched == [], "证明不了屏就不执行候选"
    cand = next(s for s in r.detail["stages"]
                if s["stage"] == "experience_candidate")
    assert (cand["outcome"], cand["reason"]) == ("MISS", "SCREEN_UNKNOWN")
    assert cand["record_as_sample"] is False, "4.7：SCREEN_UNKNOWN 不计样本"
    assert r.detail.get("experience_runs") in (None, [])


def test_screen_identification_not_run_keeps_the_registered_screen(store):
    """识别**根本没跑**（无 Repository）→ 沿用登记屏先验（P1 行为保留）。

    没有 Repository 就没有 marker 表，「屏识别」这件事无从发生——不是「证明
    失败」而是「无从判定」，任何屏校验都必然空转。此时收紧只会白掉能力而不
    增加安全性，故保留 P1 的登记屏先验。两条口径的边界就是「识别跑没跑」。
    """
    _seed(store)
    ctx, dispatched = _ctx(find_all=lambda loc: [El()])
    r = _engine(store, repo=None).recover(ctx)   # 无 repo → screen_res is None

    assert not any(s["stage"] == "screen" for s in r.detail["stages"]), \
        "前提：识别确实没跑（没有 screen 段）"
    assert r.recovered and len(dispatched) == 1, \
        "沿用登记屏先验 → 照常放行（P1 行为保留）"


def test_missing_run_id_writes_no_sample(store):
    """无追溯链（run_id=None）→ 不落库：样本行没有归属 run 等于伪造证据。

    键**缺席**而不是空列表：`_attach` 的「有才挂」口径对两条路径统一
    （此前「已恢复」路径恒带空 `experience_runs`、「未恢复」路径不带，
    同一件事两种形状）。管线消费侧本来就是
    `(detail or {}).get("experience_runs") or []`，两种都吃。
    """
    _seed(store)
    ctx, dispatched = _ctx(run_id=None, find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)
    assert r.recovered and len(dispatched) == 1
    assert r.detail.get("experience_runs") in (None, [])
    assert {"stage": "experience_sample", "outcome": "no_run_id",
            "experience_id": store.list()[0].experience_id} in r.detail["stages"], \
        "不落库不是静默跳过——留痕（review_p2_task24_final P3-3 同款纪律）"


def test_execution_failure_is_failure_sample(store):
    """通过 Guard 但执行失败 → 写 FAILURE（4.7 第 5 行），并换下一候选。"""
    _seed(store)

    def _boom(el):
        raise RuntimeError("dispatch died mid-tap")

    ctx, _ = _ctx(redispatch=_boom, find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)
    assert not r.recovered
    [sample] = r.detail["experience_runs"]
    assert sample["result"] == "FAILURE"
    assert sample["guard_reason"] == "redispatch:RuntimeError"


# --- 7. E8 观测面：fingerprint 差异如实记录 --------------------------------


def test_fingerprint_difference_is_observed_not_acted_on(store):
    """设计 4.1 注 / E8：fingerprint 变化只是**观测**（REVALIDATION 动作在
    M3），不影响本次命中。"""
    from source.screen import screen_fingerprint

    _seed(store, fingerprint="deadbeefdeadbeef")
    assert screen_fingerprint(PAGE) != "deadbeefdeadbeef"
    ctx, _ = _ctx(find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)

    assert r.recovered, "fingerprint 差异不清空、不拒绝（E8）"
    assert r.detail["screen_fingerprint_match"] is False
    [sample] = r.detail["experience_runs"]
    assert sample["screen_fingerprint"] == screen_fingerprint(PAGE)


def test_fingerprint_match_true_when_same_page(store):
    from source.screen import screen_fingerprint

    _seed(store, fingerprint=screen_fingerprint(PAGE))
    ctx, _ = _ctx(find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)
    assert r.detail["screen_fingerprint_match"] is True


# --- 8. 前置条件不足时如实报出，不猜 --------------------------------------


@pytest.mark.parametrize("override,expected", [
    (dict(page_source=None), "no_page_source"),
    (dict(find_all=None), "no_find_all"),
    (dict(app_id=""), "incomplete_key"),
])
def test_missing_prerequisite_is_reported_not_guessed(store, override,
                                                      expected):
    _seed(store)
    ctx, _ = _ctx(**override)
    if "find_all" not in override:
        ctx.find_all = lambda loc: [El()]
    r = _engine(store).recover(ctx)
    assert not r.recovered
    entry = next(s for s in r.detail["stages"]
                 if s["stage"] == "experience")
    assert entry["outcome"] == expected


def test_empty_store_is_miss_not_no_store(store):
    """「库是空的」与「没接库」在报告上必须可区分。"""
    ctx, _ = _ctx(find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)
    entry = next(s for s in r.detail["stages"]
                 if s["stage"] == "experience")
    assert entry == {"stage": "experience", "outcome": "miss"}


# --- 9. 样本落库（引擎决定写什么，trace 写入方补 steps.id） ----------------


def test_record_sample_runs_persists_and_appends_validated_build(store):
    """设计 4.7 末行 / E7：成功样本追加 validated_builds，失败样本不追加。"""
    exp = _seed(store)
    samples = [
        {"experience_id": exp.experience_id, "run_id": "run_now",
         "app_build": "1026", "result": "SUCCESS", "guard_reason": None,
         "effective_risk": "LOW", "uniqueness_count": 1,
         "element_type_match": True, "screen_fingerprint": "abc",
         "latency_ms": 7},
        {"experience_id": exp.experience_id, "run_id": "run_now",
         "app_build": "1026", "result": "FAILURE",
         "guard_reason": "TARGET_NOT_FOUND", "effective_risk": "LOW",
         "uniqueness_count": 0, "element_type_match": None,
         "screen_fingerprint": "abc", "latency_ms": 9},
    ]
    assert record_sample_runs(store, samples, step_id=42) == 2

    runs = store.get_runs(exp.experience_id)
    assert [(r.step_id, r.result) for r in runs] == [(42, "SUCCESS"),
                                                    (42, "FAILURE")]
    assert runs[1].guard_reason == "TARGET_NOT_FOUND"
    after = store.lookup(APP, "HomeView", "login_button")[0]
    assert (after.sample_count, after.success_count, after.failure_count) == (
        2, 1, 1)
    assert after.validated_builds == ["1026"], \
        "E7：只有成功样本追加 validated_builds"


def test_unfilled_deferred_sample_fails_loud_at_store_boundary(store):
    """待定样本漏回填 → 落库点**指名报错**，不是 pydantic 类型错。

    aux 命中的样本产出时 `result=None`，必须由调用方经
    `resolve_deferred_sample` 回填观测结果。漏了这一步原实现会一路带到
    `ExperienceRun` 的 `Literal` 校验才炸，报错只说「result 不是
    SUCCESS/FAILURE」——看不出是「忘了回填」（review_p2_task24_final P3-2）。
    """
    exp = _seed(store)
    ctx, _ = _ctx(action=None, redispatch=None, find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)
    [pending] = r.detail["experience_runs"]
    assert pending["result"] is None, "前提：aux 产出的是待定样本"

    with pytest.raises(ValueError, match="deferred sample 未回填"):
        record_sample_runs(store, [pending], step_id=1)
    assert store.get_runs(exp.experience_id) == [], "报错后不留半行"

    # 回填后同一条 payload 正常落库
    resolve_deferred_sample(r, succeeded=True)
    assert record_sample_runs(store, [pending], step_id=1) == 1
    assert store.get_runs(exp.experience_id)[0].result == "SUCCESS"


def test_engine_sample_payload_round_trips_through_store(store):
    """端到端形状检查：引擎产出的 payload 能原样被 record_sample_runs 消费。

    （这条把「引擎侧字段名」与「落库侧字段名」钉在一起——两边各写一份
    字段名正是最容易静默漂移的地方。）"""
    exp = _seed(store)
    ctx, _ = _ctx(find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)
    record_sample_runs(store, r.detail["experience_runs"], step_id=1)
    [run] = store.get_runs(exp.experience_id)
    assert run.result == "SUCCESS" and run.run_id == "run_now"
    assert run.element_type_match is True


def test_payload_is_json_serializable(store):
    """payload 会进 `rec.detail`（→ steps.detail_json），必须可序列化。"""
    _seed(store)
    ctx, _ = _ctx(find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)
    json.dumps(r.detail)


# --- 6c. §11.1 的 recovery 期事件（Task 2.4 终审：事件从半接改全接） --------


def _events(r):
    return r.detail.get("experience_events") or []


def _event_types(r):
    return [e["event_type"] for e in _events(r)]


def test_event_names_are_the_design_11_1_vocabulary(store):
    """引擎发的事件名必须**逐个**在 `EXPERIENCE_EVENT_TYPES` 里。

    这条不是「测实现细节」：`record_experience_event` 对不在集合里的名字
    fail-loud 抛错——引擎侧写错一个字，落库那一刻整例崩掉。这里把两侧的
    词汇表钉在一起（与 4.7 样本的字段名同一类保护）。
    """
    from tracer.storage import EXPERIENCE_EVENT_TYPES

    _seed(store)
    ctx, _ = _ctx(find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)

    assert _event_types(r), "命中路径必须发事件"
    unknown = set(_event_types(r)) - EXPERIENCE_EVENT_TYPES
    assert not unknown, f"引擎发了设计词汇表外的事件名：{sorted(unknown)}"


def test_hit_path_emits_lookup_hit_execution(store):
    """命中并执行成功 → lookup / hit / execution 三条，字段对齐 §11.1 示例。"""
    _seed(store)
    ctx, dispatched = _ctx(find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)

    assert r.recovered and len(dispatched) == 1
    assert _event_types(r) == ["experience_lookup", "experience_hit",
                               "experience_execution"]
    lookup = _events(r)[0]["detail"]
    assert lookup == {"app_id": APP, "screen_id": "HomeView",
                      "target_id": "login_button", "candidates": 1}
    hit = _events(r)[1]["detail"]
    assert hit["experience_id"] and hit["status_before"] == "CANDIDATE"
    assert hit["screen_match"] is True and hit["uniqueness_count"] == 1
    assert hit["type_match"] is True and hit["effective_risk"] == "LOW"
    assert hit["build_in_validated_set"] is False, \
        "还没记过成功样本 → 本 build 不在 validated 集合里（§11.1 字段）"
    assert hit["outcome"] == "EXECUTE"
    exe = _events(r)[2]["detail"]
    assert exe["execution"] == "SUCCESS"
    assert exe["result"] == "RECOVERED_EXPERIENCE", "§11.1 示例的 result 口径"


def test_miss_path_emits_lookup_then_miss(store):
    """空库 → lookup（候选 0）+ miss；**不发 hit/execution**（没有候选）。"""
    ctx, _ = _ctx(find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)

    assert not r.recovered
    assert _event_types(r) == ["experience_lookup", "experience_miss"]
    assert _events(r)[0]["detail"]["candidates"] == 0


def test_guard_block_emits_guard_block_event_without_sample(store):
    """Guard BLOCK（0 匹配）→ 有 `experience_guard_block` 事件、样本照记。

    4.7：唯一性失败是「用了但错了」→ 计样本；而**事件与样本不同生共死**——
    只看样本会以为「压根没查过」，trace 上必须看得见「查了、拦了、为什么」。
    """
    _seed(store, value="never_there")
    ctx, _ = _ctx(find_all=lambda loc: [])
    r = _engine(store).recover(ctx)

    assert not r.recovered
    assert _event_types(r) == ["experience_lookup", "experience_hit",
                               "experience_guard_block"]
    assert _events(r)[2]["detail"]["reason"] == "TARGET_NOT_FOUND"
    assert _events(r)[2]["detail"]["record_as_sample"] is True
    [sample] = r.detail["experience_runs"]
    assert sample["result"] == "FAILURE", "唯一性失败是「用了但错了」→ 计样本"


def test_risk_blocked_emits_event_without_sample(store):
    """风险拦截：`record_as_sample=False`（4.7 第 4 行）——事件照发，样本不记。"""
    _seed(store)
    ctx, _ = _ctx(effective_risk=Risk.HIGH, find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)

    assert not r.recovered
    blocked = next(e for e in _events(r)
                   if e["event_type"] == "experience_guard_block")
    assert blocked["detail"]["reason"] == "RISK_BLOCKED"
    assert blocked["detail"]["record_as_sample"] is False
    assert r.detail.get("experience_runs") in (None, [])


def test_aux_execution_event_backfilled_with_observed_outcome(store):
    """aux 的 execution 事件先标待定，调用方回填后反映**观测到的**终态。"""
    _seed(store)
    ctx, _ = _ctx(action=None, redispatch=None, find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)

    exe = next(e for e in _events(r)
               if e["event_type"] == "experience_execution")["detail"]
    assert exe["execution"] == "not_dispatched"
    assert exe["result"] is None and exe["pending_observation"] is True

    resolve_deferred_sample(r, succeeded=False,
                            failure_reason="AUX_RERUN_FAILED")
    assert exe["execution"] == "FAILURE" and exe["result"] is None
    assert exe["reason"] == "AUX_RERUN_FAILED"
    assert "pending_observation" not in exe


def test_no_store_and_incomplete_key_emit_no_events(store):
    """没查库就不发事件——事件是「发生过什么」的记录，不是「想过什么」。"""
    ctx, _ = _ctx(app_id="", find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)
    assert _events(r) == []


def test_events_are_json_serializable(store):
    """事件 payload 会进 `rec.detail`（→ steps.detail_json），必须可序列化。"""
    _seed(store)
    ctx, _ = _ctx(find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)
    json.dumps(r.detail["experience_events"])


# --- 6d. 终态 failure_type 的对称化（终审 P3-1） ---------------------------


def _guard_blocking(pattern="element:signin_*"):
    from executor.guard import BlockedTarget, EnvKind, Guard
    return Guard(EnvKind.SANDBOX,
                 blocked_targets=(BlockedTarget.parse(pattern),))


def test_security_blocked_without_fallback_is_the_terminal_failure_type(store):
    """候选被 10.1 拦且**无回落** → 终态是 `SECURITY_BLOCKED`，不是原症状。

    与 LLM 路径同一映射表（E1：同一候选不因来路不同而结论不同）。报成原症状
    （ELEMENT_NOT_FOUND）会让 CI 把「被策略拦下」读成「元素漂移」——排障方向
    完全错；而 `SECURITY_BLOCKED` 还会让用例走 BLOCKED/exit 4（8.1/8.4）。
    """
    _seed(store, value="signin_button")
    repo = _Repo(entries={"signin_button": ("HomeView", Risk.LOW, "button")})
    ctx, dispatched = _ctx(find_all=lambda loc: [El()])
    r = _engine(store, repo=repo, guard=_guard_blocking()).recover(ctx)

    assert not r.recovered and dispatched == []
    assert r.failure_type == "SECURITY_BLOCKED", \
        "无回落时终态用 Guard 的裁决（与 LLM 路径同一映射表）"


def test_risk_blocked_without_fallback_uses_the_same_mapping(store):
    """风险门控（risk != LOW）同样改写终态——与 LLM 路径同表。"""
    _seed(store)
    ctx, _ = _ctx(effective_risk=Risk.HIGH, find_all=lambda loc: [El()])
    r = _engine(store).recover(ctx)

    assert not r.recovered
    assert r.failure_type == "LLM_RISK_BLOCKED", \
        "值里的 LLM_ 前缀是既有报告契约值，保留不动（改名换值会割裂历史报告）"


def test_ordinary_guard_failure_does_not_rewrite_terminal(store):
    """0 匹配 / 多匹配 / 类型不符是**普通失败**，不改终态（仍原症状）。

    只有安全/风险拦截才是「裁决」。把 NOT_FOUND 也映射成 Guard 结论会让用例
    集体变成 BLOCKED，CI 从此分不清「漂移」与「被策略拦」。
    """
    _seed(store, value="never_there")
    ctx, _ = _ctx(find_all=lambda loc: [])
    r = _engine(store).recover(ctx)

    assert not r.recovered
    assert r.failure_type == "ELEMENT_NOT_FOUND", "维持原症状"


def test_terminal_failure_type_precedence_is_order_independent(store):
    """多条候选各被拦 → 取最严的一条，且**与候选顺序无关**。

    ⚠️ 当前 Guard 顺序下两种 reason **不会同时出现**（风险检查在 10.1 之前，
    而 `effective_risk` 是 ctx 级、对全部候选相同）——所以这条直接测纯函数
    `_terminal_failure_type`，把「次序规则」的契约钉住：将来 risk 若改成
    逐候选判定，结论不会退化成「取决于候选顺序」的静默行为。
    """
    from agent.recovery import RecoveryEngine

    ctx, _ = _ctx()
    cases = [(["SECURITY_BLOCKED", "RISK_BLOCKED"], "SECURITY_BLOCKED"),
             (["RISK_BLOCKED", "SECURITY_BLOCKED"], "SECURITY_BLOCKED"),
             (["RISK_BLOCKED"], "LLM_RISK_BLOCKED"),
             ([], "ELEMENT_NOT_FOUND"),
             (None, "ELEMENT_NOT_FOUND")]
    for blocks, expected in cases:
        assert RecoveryEngine._terminal_failure_type(ctx, blocks) == expected, \
            f"blocks={blocks}"


def test_risk_gate_fires_before_10_1_recheck_with_high_risk(store):
    """`effective_risk != LOW` 时全部候选都在**风险门控**被拦（顺序使然）。

    这是上一条「两种 reason 不会同时出现」的实证：ctx 的风险对每条候选相同，
    且风险检查先于 10.1 复检，所以 SECURITY 那条分支在 HIGH 语境下不可达。
    """
    _seed(store, value="signin_button")
    repo = _Repo(entries={"signin_button": ("HomeView", Risk.LOW, "button")})
    ctx, _ = _ctx(find_all=lambda loc: [El()], effective_risk=Risk.HIGH)
    r = _engine(store, repo=repo, guard=_guard_blocking()).recover(ctx)

    assert not r.recovered
    assert r.failure_type == "LLM_RISK_BLOCKED"
    reasons = [e["detail"]["reason"] for e in _events(r)
               if e["event_type"] == "experience_guard_block"]
    assert reasons == ["RISK_BLOCKED"], "风险门控先于 10.1 复检命中"


def test_llm_fallback_verdict_wins_over_experience_blocks(store):
    """有回落时终态由 LLM 那一次决定——Experience 的拦截不改写它。

    「被拦」是**尝试过的候选**的裁决；LLM 是之后新起的一次尝试，它的结论才是
    最后发生的症状。否则会拿旧的拦截盖掉新的失败，掩盖真正的排障线索。
    """
    _seed(store, value="signin_button")
    repo = _Repo(entries={"signin_button": ("HomeView", Risk.LOW, "button")})
    ctx, _ = _ctx(find_all=lambda loc: [El()])
    eng = _engine(store, repo=repo, guard=_guard_blocking(),
                  llm=FakeLLM(['{"nope": true}']), budget=LLMBudget())
    r = eng.recover(ctx)

    assert not r.recovered
    assert r.failure_type != "SECURITY_BLOCKED", \
        "LLM 的 miss 才是终态（这里 LLM_INVALID_OUTPUT）"
