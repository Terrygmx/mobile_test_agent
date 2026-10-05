"""Task 3.4：`mta experience` CLI 测试（设计 13 节）。

plan step 1 的失败测试清单逐条对应：
  - `list --status` 过滤；
  - `verify` 对全部 Candidate 跑 Verifier 并产出决策报告（不自动执行
    高风险/非幂等升级——E4 资格经 **Repository 解析出的元素**判定，
    review_p2_task31 P3-7 接线前置）；
  - `revalidate` 对 DEGRADED 转状态（3.1 的护栏生效：非 DEGRADED →
    exit 3 + 可操作指引）；
  - `sweep` 输出清理清单（只改状态不删证据）。

E4 单点是本任务的验收核心：`verify` 不得绕过 `eligible_for_auto_verification`
（它是 E4 的唯一实现，review_p2_task31 P3-7），元素解析不到 = 不合格
（fail-closed，与 Guard 的 None 风险同侧）。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from cli.main import main
from experience import SQLiteExperienceStore
from experience.models import CandidateSeed, ExperienceRun, ExperienceStatus
from repository.loader import LocatorStrategy

NOW = datetime.now(timezone.utc)

OVERRIDES_YAML = """\
schema_version: "1.0"
kind: element
id: login_button
screen: VerifyView
type: button
strategies:
  - {type: accessibility_id, value: login_btn_v2, origin: manual,
     source_file: X.swift, source_line: 10}
metadata:
  risk: LOW
  idempotency: IDEMPOTENT
  data_class: INTERNAL
---
schema_version: "1.0"
kind: element
id: order_button
screen: VerifyView
type: button
strategies:
  - {type: accessibility_id, value: order_btn_v2, origin: manual,
     source_file: X.swift, source_line: 20}
metadata:
  risk: LOW
  idempotency: NON_IDEMPOTENT
  data_class: INTERNAL
"""


@pytest.fixture()
def exp_db(tmp_path) -> str:
    return str(tmp_path / "experience.db")


@pytest.fixture()
def overrides_dir(tmp_path) -> str:
    d = tmp_path / "overrides" / "elements"
    d.mkdir(parents=True)
    (d / "VerifyView.yaml").write_text(OVERRIDES_YAML, encoding="utf-8")
    return str(tmp_path / "overrides")


@pytest.fixture()
def store(exp_db) -> SQLiteExperienceStore:
    return SQLiteExperienceStore(exp_db)


def _seed(store: SQLiteExperienceStore, *, target_id: str = "login_button",
          screen_id: str = "VerifyView") -> str:
    exp = store.create_candidate(CandidateSeed(
        review_id=7, recovery_id=1, seed_run_id="run_seed", seed_step_id=12,
        seed_recovery_review_id=7, app_id="com.x", screen_id=screen_id,
        target_id=target_id,
        strategy=LocatorStrategy(type="accessibility_id", value="v2",
                                 origin="experience")))
    return exp.experience_id


def _runs(store: SQLiteExperienceStore, exp_id: str, results: list[str],
          *, run_prefix: str = "r") -> None:
    for i, res in enumerate(results):
        store.record_run(exp_id, ExperienceRun(
            experience_id=exp_id, run_id=f"{run_prefix}_{i}",
            step_id=100 + i, app_build="1026", result=res))


def _backdate(store: SQLiteExperienceStore, exp_id: str, days: float) -> None:
    stamp = (NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with store._write_tx() as conn:
        conn.execute("UPDATE experiences SET created_at=?, updated_at=?"
                     " WHERE experience_id=?", (stamp, stamp, exp_id))
        conn.execute("UPDATE experience_runs SET created_at=?"
                     " WHERE experience_id=?", (stamp, exp_id))


# --- list --------------------------------------------------------------------


def test_list_shows_all_and_filters_by_status(exp_db, store, capsys):
    a = _seed(store)
    b = _seed(store, target_id="order_button")
    store.update_status(b, ExperienceStatus.REJECTED, "MANUAL_REJECT")

    assert main(["experience", "list", "--exp-db", exp_db]) == 0
    out = capsys.readouterr().out
    assert a in out and b in out, "缺省列出全部状态（含 REJECTED 审计视角）"

    assert main(["experience", "list", "--exp-db", exp_db,
                 "--status", "CANDIDATE"]) == 0
    out = capsys.readouterr().out
    assert a in out and b not in out

    assert main(["experience", "list", "--exp-db", exp_db,
                 "--status", "REJECTED"]) == 0
    out = capsys.readouterr().out
    assert b in out and a not in out


def test_list_empty_db_friendly_message(exp_db, capsys):
    assert main(["experience", "list", "--exp-db", exp_db]) == 0
    assert "no experiences" in capsys.readouterr().out


# --- show --------------------------------------------------------------------


def test_show_prints_detail_runs_and_events(exp_db, store, capsys):
    exp_id = _seed(store)
    _runs(store, exp_id, ["SUCCESS", "FAILURE"])

    assert main(["experience", "show", exp_id, "--exp-db", exp_db]) == 0
    out = capsys.readouterr().out
    assert exp_id in out and "CANDIDATE" in out
    assert "accessibility_id:v2" in out, "strategy 是 show 的核心信息（可直接比对）"
    assert "r_0" in out and "SUCCESS" in out, "样本历史"
    assert "SEEDED" in out, "状态时间线"


def test_show_unknown_id_exit_3(exp_db, capsys):
    assert main(["experience", "show", "exp_nope", "--exp-db", exp_db]) == 3


# --- verify（E4 单点 + 决策报告） ---------------------------------------------


def test_verify_promotes_eligible_candidate(exp_db, store, overrides_dir,
                                            capsys):
    exp_id = _seed(store)
    _runs(store, exp_id, ["SUCCESS"] * 10)      # 10/10，3 个不同 run

    assert main(["experience", "verify", "--exp-db", exp_db,
                 "--overrides", overrides_dir]) == 0
    out = capsys.readouterr().out
    assert "THRESHOLD_MET" in out and "eligible=True" in out
    after = store.list(ExperienceStatus.VERIFIED)
    assert [e.experience_id for e in after] == [exp_id], "决策已落库"
    events = store.get_state_events(exp_id)
    assert events[-1].reason == "THRESHOLD_MET"
    assert events[-1].operator == "cli-verify"


def test_verify_keeps_non_idempotent_even_10_of_10(exp_db, store,
                                                   overrides_dir, capsys):
    """矩阵 #8（CLI 形态）：非幂等目标 10/10 也不升级——E4 资格**必须**
    经 Repository 解析出的元素判定（review_p2_task31 P3-7 验收项）。"""
    exp_id = _seed(store, target_id="order_button")
    _runs(store, exp_id, ["SUCCESS"] * 10)

    assert main(["experience", "verify", "--exp-db", exp_db,
                 "--overrides", overrides_dir]) == 0
    out = capsys.readouterr().out
    assert "NOT_ELIGIBLE" in out, "决策报告要写明「为什么没升级」"
    assert store.list(ExperienceStatus.CANDIDATE)[0].experience_id == exp_id


def test_verify_unresolved_element_fail_closed(exp_db, store, overrides_dir,
                                               capsys):
    """元素解析不到（Repository 无登记）→ 不合格（fail-closed），
    与 Guard 的 None 风险同侧；报告写明原因，不静默。"""
    exp_id = _seed(store, target_id="ghost_button")
    _runs(store, exp_id, ["SUCCESS"] * 10)

    assert main(["experience", "verify", "--exp-db", exp_db,
                 "--overrides", overrides_dir]) == 0
    out = capsys.readouterr().out
    assert "NOT_ELIGIBLE" in out and "ELEMENT_UNRESOLVED" in out
    assert store.list(ExperienceStatus.CANDIDATE)[0].experience_id == exp_id


def test_verify_degrades_verified_on_sliding_window(exp_db, store,
                                                    overrides_dir, capsys):
    """矩阵 #5（CLI 形态）：E6 窗口压倒总体——verify 同时巡检 VERIFIED。"""
    exp_id = _seed(store)
    store.update_status(exp_id, ExperienceStatus.VERIFIED, "THRESHOLD_MET",
                        operator="test")
    results = ["SUCCESS"] * 15 + ["FAILURE", "FAILURE", "SUCCESS",
                                  "FAILURE", "SUCCESS"]
    _runs(store, exp_id, results)               # 总体 80%，尾 5 有 3 失败

    assert main(["experience", "verify", "--exp-db", exp_db,
                 "--overrides", overrides_dir]) == 0
    out = capsys.readouterr().out
    assert "SLIDING_WINDOW" in out
    assert store.list(ExperienceStatus.DEGRADED)[0].experience_id == exp_id


def test_verify_skips_rejected_terminal(exp_db, store, overrides_dir, capsys):
    exp_id = _seed(store)
    store.update_status(exp_id, ExperienceStatus.REJECTED, "MANUAL_REJECT")

    assert main(["experience", "verify", "--exp-db", exp_db,
                 "--overrides", overrides_dir]) == 0
    assert exp_id not in capsys.readouterr().out, "REJECTED 是终态，不巡检"


# --- revalidate（护栏 + 可操作错误） ------------------------------------------


def test_revalidate_degraded_to_verified(exp_db, store, capsys):
    exp_id = _seed(store)
    store.update_status(exp_id, ExperienceStatus.VERIFIED, "THRESHOLD_MET",
                        operator="test")
    store.update_status(exp_id, ExperienceStatus.DEGRADED, "SLIDING_WINDOW",
                        operator="test")

    assert main(["experience", "revalidate", exp_id, "--exp-db", exp_db,
                 "--fingerprint", "fp_after_recheck",
                 "--operator", "alice"]) == 0
    out = capsys.readouterr().out
    assert "REVALIDATED" in out
    events = store.get_state_events(exp_id)
    assert (events[-1].from_status, events[-1].to_status,
            events[-1].reason, events[-1].operator) == (
        "DEGRADED", "VERIFIED", "REVALIDATED", "alice")
    # E8：重验证通过后更新 fingerprint 观测
    assert store.list(ExperienceStatus.VERIFIED)[0] \
        .last_screen_fingerprint == "fp_after_recheck"


def test_revalidate_candidate_gets_actionable_error(exp_db, store, capsys):
    """P2-1 护栏在 CLI 的出口形态：CANDIDATE → exit 3 + 指引走 verify。"""
    exp_id = _seed(store)
    assert main(["experience", "revalidate", exp_id, "--exp-db", exp_db,
                 "--fingerprint", "fp"]) == 3
    out = capsys.readouterr().out
    assert "verify" in out, "错误信息必须给出下一步（CANDIDATE → verify）"


def test_revalidate_rejected_gets_actionable_error(exp_db, store, capsys):
    exp_id = _seed(store)
    store.update_status(exp_id, ExperienceStatus.REJECTED, "MANUAL_REJECT")
    assert main(["experience", "revalidate", exp_id, "--exp-db", exp_db,
                 "--fingerprint", "fp"]) == 3
    assert "ACCEPT" in capsys.readouterr().out, \
        "REJECTED 的下一步是重新 ACCEPT（E5 重新学习）"


def test_revalidate_unknown_id_exit_3(exp_db, capsys):
    assert main(["experience", "revalidate", "exp_nope", "--exp-db", exp_db,
                 "--fingerprint", "fp"]) == 3


# --- sweep --------------------------------------------------------------------


def test_sweep_lists_cleaned_and_preserves_evidence(exp_db, store, capsys):
    stale = _seed(store, target_id="order_button")
    fresh = _seed(store)
    _runs(store, stale, ["SUCCESS"])
    _backdate(store, stale, 200)

    assert main(["experience", "sweep", "--exp-db", exp_db,
                 "--max-idle-days", "90"]) == 0
    out = capsys.readouterr().out
    assert stale in out and fresh not in out
    assert "STALE" in out

    statuses = {e.experience_id: e.status for e in store.list()}
    assert statuses[stale] is ExperienceStatus.REJECTED
    assert statuses[fresh] is ExperienceStatus.CANDIDATE
    # 11.2：只改状态不删证据
    assert len(store.get_runs(stale)) == 1
    conn = sqlite3.connect(exp_db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM experience_state_events"
                         " WHERE experience_id=?", (stale,)).fetchone()[0]
    finally:
        conn.close()
    assert n >= 2, "SEEDED + REJECTED(STALE) 事件链完整"


def test_sweep_nothing_to_clean(exp_db, capsys):
    assert main(["experience", "sweep", "--exp-db", exp_db]) == 0
    assert "no stale" in capsys.readouterr().out
