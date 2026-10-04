"""P2 故障注入矩阵 #1/#2/#3/#4/#15（设计 18 节 Demo / Task 2.4）。

编号即设计 16 节的 P2 矩阵行（**不是** P1 的 24 行矩阵——那套在
test_matrix_01_12 / test_matrix_13_24）。Gate M2 就是这五行的自动化：

| # | 场景 | 预期 |
|---|---|---|
| 1 | 同一 build 第二次遇到相同 Locator Failure | `RECOVERED_EXPERIENCE`，LLM calls = 0 |
| 2 | Experience 候选在当前 UI 出现 2 个匹配 | `TARGET_AMBIGUOUS`，Guard BLOCK，不执行 |
| 3 | Experience 的目标 Screen risk 升高 | `RISK_BLOCKED`，Guard BLOCK |
| 4 | 当前 Screen 与记录不符 | MISS，不计失败样本（E11），回落下一候选或 LLM |
| 15 | 断言目标 ID 漂移，Experience 命中 | `RECOVERED_ASSERTION_TARGET`，期望值本身未被修改 |

全部 FakeDriver（无网络无真机）；#1 的真机版（F5 改名重编译）在
`phase0/verify_p2_m2.py`。
"""

from __future__ import annotations

import pytest

from executor.executor import ElementNotFound
from experience import SQLiteExperienceStore
from experience.models import CandidateSeed
from llm.budget import LLMBudget
from repository.loader import LocatorStrategy
from tests.fault_injection.fi_support import (
    El,
    FakeExecutor,
    FakeLLM,
    drift_repo,
    llm_json,
    run_matrix,
)

APP = "com.matrix.app"
HOME_PAGE = "<App><Node name='screen.HomeView' visible='true'/></App>"
PROFILE_PAGE = "<App><Node name='screen.ProfileView' visible='true'/></App>"


class DriftExecutor(FakeExecutor):
    """F5 漂移形态的 Executor：旧名找不到、新名找得到。

    比「按调用序弹脚本」更贴近真机语义——漂移的语义就是「按旧策略找不到、
    按新策略找得到」，与调用次数无关；脚本式替身会随引擎多一次/少一次
    调用而假绿或假红（P1 的矩阵替身吃过的亏）。

    `found_values`：**当前 UI 上真实存在**的策略值集合（默认 `{new_name}`）。
    需要「Experience 旧名与 LLM 新名并存」的行（#4 回落 LLM）把两者都放进
    来——否则 LLM 候选也会找不到，回落断言就测不到东西。
    """

    def __init__(self, *, new_name="signin_button", all_matches=1,
                 found_values=None, **kw):
        super().__init__(**kw)
        self.new_name = new_name
        self.all_matches = all_matches
        self.found_values = set(found_values or {new_name})

    def find(self, strategies):
        self.find_calls += 1
        if any(s.get("value") in self.found_values for s in strategies):
            return El()
        raise ElementNotFound("drifted away")

    def find_all(self, locator):
        self.find_all_calls += 1
        if any(s.get("value") in self.found_values for s in locator):
            return [El() for _ in range(self.all_matches)]
        return []


def _seed(store, *, screen="HomeView", target="login_button",
          value="signin_button", review_id=1):
    """人工 ACCEPT 留下的 Candidate（走真 create_candidate，不绕 E5 闸门）。"""
    return store.create_candidate(CandidateSeed(
        review_id=review_id, recovery_id=review_id,
        seed_run_id="run_seed", seed_step_id=1,
        seed_recovery_review_id=review_id,
        app_id=APP, screen_id=screen, target_id=target,
        strategy=LocatorStrategy(type="accessibility_id", value=value,
                                 origin="experience")))


def _recovery(tmp_path, *, store, llm=None, budget=None):
    from agent.recovery import RecoveryEngine
    return RecoveryEngine(repo=drift_repo(tmp_path), llm=llm, budget=budget,
                          experience_store=store, sleep=lambda s: None)


def _case(row, step):
    return (f'schema_version: "0.2"\nid: p2_{row}\nname: p2 matrix {row}\n'
            f'suite: smoke\ntags: [smoke]\nsteps:\n  - action: launch_app\n'
            f'{step}')


TAP = """\
  - action: tap
    target: HomeView.login_button
    idempotency: IDEMPOTENT
"""

TAP_PAY = """\
  - action: tap
    target: HomeView.pay_button
"""

ASSERT_EXISTS = """\
  - assertion:
      target: HomeView.login_button
      condition: exists
      timeout: 0.05
"""

# `disabled`：覆盖定位后 `find` 成功、但元素是 enabled 的 → 断言仍不过。
# 用来构造「Guard 过了、重跑观测到失败」这一格（Guard 不是执行结果，
# 它看不见这种失败）。
ASSERT_DISABLED = """\
  - assertion:
      target: HomeView.login_button
      condition: disabled
      timeout: 0.05
"""


def _exp_runs(tmp_path, experience_id=None):
    import sqlite3
    conn = sqlite3.connect(tmp_path / "experience.db")
    conn.row_factory = sqlite3.Row
    try:
        if experience_id:
            rows = conn.execute(
                "SELECT * FROM experience_runs WHERE experience_id=?"
                " ORDER BY id", (experience_id,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM experience_runs ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _stages(result):
    detail = result.detail
    return (detail.get("recovery_attempted") or
            detail.get("recovery") or {}).get("stages", [])


def _aux_stages(trace, step_type="assert"):
    """aux 步骤的恢复明细在**步骤** detail 里（`_record_aux_recovered` 写
    steps.detail_json），不在用例 detail——用例级 `recovery_kinds` 只留
    机制名与分类标签。"""
    import json
    row = trace.conn.execute(
        "SELECT detail_json FROM steps WHERE step_type=?"
        " ORDER BY id DESC LIMIT 1", (step_type,)).fetchone()
    detail = json.loads(row["detail_json"]) if row and row["detail_json"] else {}
    return (detail.get("recovery") or {}).get("stages", [])


# --- #1：同一 build 第二次 → RECOVERED_EXPERIENCE，LLM calls = 0 ------------


def test_p2_01_experience_hit_skips_llm(tmp_path):
    store = SQLiteExperienceStore(tmp_path / "experience.db")
    exp = _seed(store)
    llm = FakeLLM([llm_json()])
    budget = LLMBudget()
    ex = DriftExecutor(page_source=HOME_PAGE)

    run, trace, _, ex = run_matrix(
        tmp_path, _case("01", TAP), ex=ex, repo=drift_repo(tmp_path),
        recovery=_recovery(tmp_path, store=store, llm=llm, budget=budget),
        bundle_id=APP)

    r = run.results[0]
    assert r.status == "RECOVERED" and run.exit_code == 5
    assert r.detail["recovery_kinds"] == ["experience"]
    assert r.detail["recovered_kinds"] == ["RECOVERED_EXPERIENCE"], \
        "设计 10：明细必须能回答「这次是经验救的还是 LLM 救的」"
    assert llm.calls == [] and budget.calls_used == 0, \
        "Experience 命中时根本不消耗 Budget（设计 3.1 / 矩阵 #1）"
    assert ex.tap_calls == 1, "命中即执行"

    # trace：recoveries 行如实记机制（Task 2.4 给 RECOVERY_KINDS 加了 EXPERIENCE）
    rows = trace.conn.execute(
        "SELECT kind, candidate_target, candidate_type, screen FROM recoveries"
    ).fetchall()
    assert [tuple(x) for x in rows] == [
        ("EXPERIENCE", "signin_button", "button", "HomeView")]
    # 矩阵 #1 的另一半：Experience 恢复不建 review（没有新知识要审）
    assert trace.conn.execute(
        "SELECT COUNT(*) FROM recovery_reviews").fetchone()[0] == 0

    # 4.7 样本落库：step_id 是真 steps.id（不是 YAML 步序）
    steps = trace.conn.execute(
        "SELECT id, step_index, status FROM steps ORDER BY id").fetchall()
    recovered = [s for s in steps if s["status"] == "RECOVERED"]
    assert len(recovered) == 1
    [run_row] = _exp_runs(tmp_path, exp.experience_id)
    assert run_row["result"] == "SUCCESS"
    assert run_row["step_id"] == recovered[0]["id"]
    assert run_row["run_id"] == "run_matrix"
    assert run_row["app_build"] == "local"
    # E7：成功样本追加 validated_builds
    after = store.lookup(APP, "HomeView", "login_button")[0]
    assert (after.sample_count, after.success_count) == (1, 1)
    assert after.validated_builds == ["local"]


def test_p2_01_second_run_uses_store_not_llm(tmp_path):
    """#1 的「第二次」语义：同一 Candidate 连跑两次都走 Experience。

    第一次是 LLM 学来的（本用例直接以人工 ACCEPT 的 Candidate 起手），
    第二次起 LLM 就是零调用——这正是「知识积累」的可观测形状。"""
    store = SQLiteExperienceStore(tmp_path / "experience.db")
    exp = _seed(store)
    llm = FakeLLM([llm_json()])
    recovery = _recovery(tmp_path, store=store, llm=llm, budget=LLMBudget())

    for i in range(2):
        # 每次 run 一个独立目录：run_matrix 会用同一 run_id 起 trace，
        # 同库两次 start_run 会撞 runs.run_id 唯一约束（不是被测行为）。
        sub = tmp_path / f"run{i}"
        sub.mkdir()
        run, _, _, _ = run_matrix(
            sub, _case("01b", TAP), ex=DriftExecutor(page_source=HOME_PAGE),
            repo=drift_repo(sub), recovery=recovery, bundle_id=APP)
        assert run.results[0].status == "RECOVERED"
        assert run.results[0].detail["recovered_kinds"] == [
            "RECOVERED_EXPERIENCE"]

    assert llm.calls == []
    after = store.lookup(APP, "HomeView", "login_button")[0]
    assert (after.sample_count, after.success_count) == (2, 2)
    assert len(_exp_runs(tmp_path, exp.experience_id)) == 2


# --- #2：双匹配 → TARGET_AMBIGUOUS，Guard BLOCK，不执行 --------------------


def test_p2_02_ambiguous_candidate_blocked_not_executed(tmp_path):
    store = SQLiteExperienceStore(tmp_path / "experience.db")
    exp = _seed(store)
    # 不给 LLM：断言「Guard BLOCK 的候选绝不执行」需要 tap 计数只可能来自
    # 被拦的那个候选；挂上 LLM 后它会另行 redispatch 一次，计数失去意义。
    ex = DriftExecutor(page_source=HOME_PAGE, all_matches=2)

    run, trace, _, ex = run_matrix(
        tmp_path, _case("02", TAP), ex=ex, repo=drift_repo(tmp_path),
        recovery=_recovery(tmp_path, store=store),
        bundle_id=APP)

    r = run.results[0]
    assert r.status == "FAIL" and r.failure_type == "ELEMENT_NOT_FOUND"
    assert ex.tap_calls == 0, "Guard BLOCK 的候选绝不执行"
    entry = next(s for s in _stages(r) if s["stage"] == "experience_candidate")
    assert (entry["outcome"], entry["reason"]) == ("BLOCK", "TARGET_AMBIGUOUS")
    assert entry["uniqueness_count"] == 2
    # 4.7 第 2 行：唯一性失败是「用了但错了」→ 计失败样本
    [run_row] = _exp_runs(tmp_path, exp.experience_id)
    assert run_row["result"] == "FAILURE"
    assert run_row["guard_reason"] == "TARGET_AMBIGUOUS"
    assert run_row["uniqueness_count"] == 2


def test_p2_02_not_found_is_failure_sample(tmp_path):
    """#2 的邻居（0 匹配）：同样 BLOCK + 计失败样本，但 reason 是 NOT_FOUND。"""
    store = SQLiteExperienceStore(tmp_path / "experience.db")
    exp = _seed(store, value="never_there")
    ex = DriftExecutor(page_source=HOME_PAGE)

    run, _, _, _ = run_matrix(
        tmp_path, _case("02b", TAP), ex=ex, repo=drift_repo(tmp_path),
        recovery=_recovery(tmp_path, store=store), bundle_id=APP)

    entry = next(s for s in _stages(run.results[0])
                 if s["stage"] == "experience_candidate")
    assert (entry["outcome"], entry["reason"]) == ("BLOCK", "TARGET_NOT_FOUND")
    assert entry["uniqueness_count"] == 0
    [run_row] = _exp_runs(tmp_path, exp.experience_id)
    assert (run_row["result"], run_row["guard_reason"]) == (
        "FAILURE", "TARGET_NOT_FOUND")


# --- #3：目标风险升高 → RISK_BLOCKED，Guard BLOCK --------------------------


def test_p2_03_high_risk_target_blocked(tmp_path):
    """pay_button 在 metadata 里是 HIGH → E2 的 effective_risk = HIGH。

    4.7 第 4 行：风险拦截**不写样本**——「此 Experience 当前不被允许使用」
    不是「用了但错了」。"""
    store = SQLiteExperienceStore(tmp_path / "experience.db")
    exp = _seed(store, target="pay_button", value="pay_button_v2")
    ex = DriftExecutor(new_name="pay_button_v2", page_source=HOME_PAGE)

    run, _, _, ex = run_matrix(
        tmp_path, _case("03", TAP_PAY), ex=ex, repo=drift_repo(tmp_path),
        recovery=_recovery(tmp_path, store=store), bundle_id=APP)

    r = run.results[0]
    assert r.status == "FAIL", "Guard 拦截后回落 LLM（无 LLM）→ 维持原症状"
    assert ex.tap_calls == 0
    entry = next(s for s in _stages(r) if s["stage"] == "experience_candidate")
    assert (entry["outcome"], entry["reason"]) == ("BLOCK", "RISK_BLOCKED")
    assert entry["record_as_sample"] is False
    assert _exp_runs(tmp_path, exp.experience_id) == [], \
        "E11 / 4.7 第 4 行：风险拦截不计样本（不计失败率）"
    after = store.lookup(APP, "HomeView", "pay_button")[0]
    assert (after.sample_count, after.failure_count) == (0, 0)


# --- #4：当前 Screen 与记录不符 → MISS，不计失败样本（E11） -----------------


def test_p2_04_screen_mismatch_miss_not_a_sample(tmp_path):
    store = SQLiteExperienceStore(tmp_path / "experience.db")
    exp = _seed(store, screen="HomeView")
    # 运行时页面是 ProfileView（App 当前在别的屏）
    ex = DriftExecutor(page_source=PROFILE_PAGE)

    run, _, _, ex = run_matrix(
        tmp_path, _case("04", TAP), ex=ex, repo=drift_repo(tmp_path),
        recovery=_recovery(tmp_path, store=store), bundle_id=APP)

    r = run.results[0]
    assert r.status == "FAIL" and r.failure_type == "ELEMENT_NOT_FOUND"
    assert ex.tap_calls == 0
    entry = next(s for s in _stages(r) if s["stage"] == "experience_candidate")
    assert (entry["outcome"], entry["reason"]) == ("MISS", "SCREEN_MISMATCH")
    assert entry["record_as_sample"] is False
    assert _exp_runs(tmp_path, exp.experience_id) == [], \
        "E11：Screen 不符不是「用了但错了」——不写 experience_runs"


def test_p2_04_screen_mismatch_falls_back_to_llm(tmp_path):
    """#4 的后半句「回落下一候选或 LLM」：MISS 之后 LLM 仍能救回来。

    LLM 候选必须落在**当前屏**（ProfileView）才能过 Screen 校验——这正是
    #4 与 #2 的区别：Experience 因屏不符而 MISS，LLM 按当前屏重新给候选。
    """
    store = SQLiteExperienceStore(tmp_path / "experience.db")
    exp = _seed(store, screen="HomeView")
    llm = FakeLLM([llm_json(value="ghost_button")])
    # 当前 UI：ProfileView 的 ghost_button 在（LLM 可命中），
    # HomeView 的 signin_button 不在（Experience 就算不被屏校验拦也找不到）
    ex = DriftExecutor(page_source=PROFILE_PAGE,
                       found_values={"ghost_button"})

    run, _, _, _ = run_matrix(
        tmp_path, _case("04b", TAP), ex=ex, repo=drift_repo(tmp_path),
        recovery=_recovery(tmp_path, store=store, llm=llm,
                           budget=LLMBudget()), bundle_id=APP)

    r = run.results[0]
    assert r.status == "RECOVERED"
    assert r.detail["recovered_kinds"] == ["RECOVERED_LLM"]
    assert len(llm.calls) == 1
    assert _exp_runs(tmp_path, exp.experience_id) == [], "MISS 不因回落 LLM 而记样本"


# --- #15：断言目标漂移 → RECOVERED_ASSERTION_TARGET，期望值不动（E3） -------


def test_p2_15_assertion_target_drift_recovers_as_third_kind(tmp_path):
    store = SQLiteExperienceStore(tmp_path / "experience.db")
    _seed(store)
    ex = DriftExecutor(page_source=HOME_PAGE)

    run, trace, _, ex = run_matrix(
        tmp_path, _case("15", ASSERT_EXISTS), ex=ex,
        repo=drift_repo(tmp_path), recovery=_recovery(tmp_path, store=store),
        bundle_id=APP)

    r = run.results[0]
    assert r.status == "RECOVERED" and run.exit_code == 5
    assert r.detail["recovered_kinds"] == ["RECOVERED_ASSERTION_TARGET"], \
        "断言目标漂移压过机制分类（设计 10 第三类）"
    # E3：只允许恢复**定位**，期望值本身未被修改——断言条件/期望值仍是原样
    assert "assertion_value" not in r.detail, \
        "断言漂移不是值失败：不得改写期望值（E3）"
    steps = trace.conn.execute(
        "SELECT step_type, target_id, status, detail_json FROM steps"
        " ORDER BY id").fetchall()
    assert [s["step_type"] for s in steps] == ["launch_app", "assert"]
    assert steps[-1]["status"] == "RECOVERED"
    # recoveries 行的 kind 仍是**机制**枚举（context=assertion_target 只进
    # 明细，不进机制列——两者语义不同，不得互相冒充）
    assert [x["kind"] for x in trace.conn.execute(
        "SELECT kind FROM recoveries").fetchall()] == ["EXPERIENCE"]


def test_p2_15_aux_hit_records_sample_from_rerun_observation(tmp_path):
    """aux 命中的样本由**重跑观测**决定：重跑过 → SUCCESS，落库。

    引擎侧 aux 无 dispatch 语义（`execution == "not_dispatched"`，结果此刻
    不可观测），但**调用方能观测**——它在覆盖定位后重跑了一次断言。所以
    样本不是「猜的」：`result` 由那次重跑回填（Task 2.4 评审 P2-1）。

    为什么必须记：只经 aux 命中的 Candidate 若 sample_count 恒为 0，就永远
    到不了 4.5 的 min_samples、永远不能 VERIFIED，报告里还会读成「这条经验
    从未被使用」——P2 的「知识积累」目标对 aux 目标整体失效。
    """
    store = SQLiteExperienceStore(tmp_path / "experience.db")
    exp = _seed(store)
    ex = DriftExecutor(page_source=HOME_PAGE)

    run, trace, _, _ = run_matrix(
        tmp_path, _case("15b", ASSERT_EXISTS), ex=ex,
        repo=drift_repo(tmp_path), recovery=_recovery(tmp_path, store=store),
        bundle_id=APP)

    assert run.results[0].status == "RECOVERED"
    # 引擎侧仍如实标 not_dispatched（它确实没执行）
    entry = next(s for s in _aux_stages(trace)
                 if s["stage"] == "experience_candidate")
    assert entry["execution"] == "not_dispatched"
    # 但样本按**重跑观测**落库，且归属 aux 那一步
    steps = trace.conn.execute(
        "SELECT id, step_type, status FROM steps ORDER BY id").fetchall()
    assert [s["step_type"] for s in steps] == ["launch_app", "assert"]
    assert steps[-1]["status"] == "RECOVERED"
    [run_row] = _exp_runs(tmp_path, exp.experience_id)
    assert run_row["result"] == "SUCCESS"
    assert run_row["step_id"] == steps[-1]["id"]
    assert run_row["guard_reason"] is None
    after = store.lookup(APP, "HomeView", "login_button")[0]
    assert (after.sample_count, after.success_count) == (1, 1)
    assert after.validated_builds == ["local"], \
        "E7：成功样本追加 validated_builds——aux 命中同样算验证过这个 build"


def test_p2_15_aux_rerun_failure_records_failure_sample(tmp_path):
    """aux 重跑仍失败 → 样本记 FAILURE（不是「没观测到」，是**观测到失败**）。

    没有这一条，aux 命中就只记成功、不记失败，失败率被系统性低估——4.7 的
    口径是「用了但错了就记账」，而 aux 的「错」恰恰只能由调用方的重跑观测到。

    同时：失败的 aux 步骤必须落 steps 行。此前这条路径直接把异常抛给外层，
    steps 表**一行都没有**——trace 上「这一步没跑过」，排障无从下手（与 M5
    基线实锤的 aux 失败不落库同一个缺口，只是入口更靠后）。
    """
    store = SQLiteExperienceStore(tmp_path / "experience.db")
    exp = _seed(store)
    ex = DriftExecutor(page_source=HOME_PAGE)

    run, trace, _, _ = run_matrix(
        tmp_path, _case("15c", ASSERT_DISABLED), ex=ex,
        repo=drift_repo(tmp_path), recovery=_recovery(tmp_path, store=store),
        bundle_id=APP)

    r = run.results[0]
    assert r.status == "FAIL" and r.failure_type == "ASSERTION_VALUE_MISMATCH"
    steps = trace.conn.execute(
        "SELECT id, step_type, status, detail_json FROM steps ORDER BY id"
    ).fetchall()
    assert [s["step_type"] for s in steps] == ["launch_app", "assert"]
    assert steps[-1]["status"] != "RECOVERED", "重跑没过就不是恢复"
    # P3-3：失败路径也要带 `recovery` 段（与成功路径同形）——只写 error 的话
    # trace 上看不出「试过哪条经验、候选值是什么、Guard 判了什么」。
    import json as _json
    detail = _json.loads(steps[-1]["detail_json"])
    assert detail["recovery_kind"] == "experience"
    assert detail["recovery_context"] == "aux_rerun_failed"
    assert any(s["stage"] == "experience_candidate"
               for s in detail["recovery"]["stages"]), \
        "失败路径的 stages 必须能看到候选判定过程"
    # 但不盖 §10 的分类标签：本步终态是 FAILED，不是「被谁救回」
    assert "recovered_kind" not in detail, \
        "recovered_kind 的语义是「被哪条机制救回」——失败步骤上会污染恢复分类"
    # P3-4（有意口径，钉住）：失败尝试**不进 recoveries 表**
    # （recoveries = 恢复动作记录 + review 种子来源，E5：失败尝试不产种子；
    #   失败痕迹在 experience_runs + steps.detail）
    assert trace.conn.execute(
        "SELECT COUNT(*) FROM recoveries").fetchone()[0] == 0
    [run_row] = _exp_runs(tmp_path, exp.experience_id)
    assert (run_row["result"], run_row["guard_reason"]) == (
        "FAILURE", "AUX_RERUN_FAILED")
    assert run_row["step_id"] == steps[-1]["id"]
    after = store.lookup(APP, "HomeView", "login_button")[0]
    assert (after.sample_count, after.success_count, after.failure_count) == (
        1, 0, 1)
    assert after.validated_builds == [], "E7：失败样本绝不追加 validated_builds"


# --- 空库 / 未接库：P1 行为保留（回归底线） --------------------------------


def test_empty_experience_db_keeps_p1_behavior(tmp_path):
    """P1 行为保留原则：空 experience 库时 Recovery 行为与 P1 完全一致。

    空库 + 无 LLM → 确定性半边穷尽后维持原症状（与 P1 矩阵 #1 同形）。"""
    store = SQLiteExperienceStore(tmp_path / "experience.db")   # 空库
    ex = DriftExecutor(page_source=HOME_PAGE)

    run, _, _, _ = run_matrix(
        tmp_path, _case("empty", TAP), ex=ex, repo=drift_repo(tmp_path),
        recovery=_recovery(tmp_path, store=store), bundle_id=APP)

    r = run.results[0]
    assert r.status == "FAIL" and r.failure_type == "ELEMENT_NOT_FOUND"
    assert run.exit_code == 1
    assert {"stage": "experience", "outcome": "miss"} in _stages(r)
    assert {"stage": "llm", "outcome": "disabled"} in _stages(r)
    assert _exp_runs(tmp_path) == []


def test_no_app_id_reports_incomplete_key(tmp_path):
    """--fake-driver / 未给 --bundle-id：app_id 缺失 → 如实报 incomplete_key，
    不拿 app_build 顶（P1 旧签名的错，Task 2.4 清掉）。"""
    store = SQLiteExperienceStore(tmp_path / "experience.db")
    _seed(store)
    ex = DriftExecutor(page_source=HOME_PAGE)

    run, _, _, _ = run_matrix(
        tmp_path, _case("nokey", TAP), ex=ex, repo=drift_repo(tmp_path),
        recovery=_recovery(tmp_path, store=store), app_id="")

    entry = next(s for s in _stages(run.results[0])
                 if s["stage"] == "experience")
    assert entry == {"stage": "experience", "outcome": "incomplete_key",
                     "missing": "app_id"}


def test_engine_never_consumes_rejected_experience(tmp_path):
    """设计 7.1 修订：REJECTED 不被消费路径看见（人工判过不可用的策略）。"""
    store = SQLiteExperienceStore(tmp_path / "experience.db")
    exp = _seed(store)
    from experience import ExperienceStatus
    store.update_status(exp.experience_id, ExperienceStatus.REJECTED,
                        "MANUAL")
    ex = DriftExecutor(page_source=HOME_PAGE)

    run, _, _, ex = run_matrix(
        tmp_path, _case("rej", TAP), ex=ex, repo=drift_repo(tmp_path),
        recovery=_recovery(tmp_path, store=store), bundle_id=APP)

    entry = next(s for s in _stages(run.results[0])
                 if s["stage"] == "experience")
    assert entry == {"stage": "experience", "outcome": "miss"}
    assert ex.tap_calls == 0
    assert _exp_runs(tmp_path, exp.experience_id) == []
