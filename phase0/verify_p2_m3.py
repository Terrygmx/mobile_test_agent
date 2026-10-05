#!/usr/bin/env python3
"""Gate M3 验证（Task 3.4 / P2-06/07/08 载体）——**离线判据**。

plan Task 3.4 step 2：完整状态转换演示 + 矩阵 #5–#12 断言。矩阵 #5–#9/#11
是纯函数/SQLite 语义（E13：判定不依赖设备），#12 是单写者并发验证——全部
可在无设备环境机械断言；真机那一半（Experience 命中链路）归 Gate M2 产物
（out/p2_m2_gate/summary.json）与 M4 的 Promotion/回滚演示。

判据（与 plan Gate M3 一一对应）：

  S1. 完整状态转换：CANDIDATE →VERIFIED(THRESHOLD_MET) → DEGRADED(SLIDING_WINDOW)
      → 显式 revalidate → VERIFIED(REVALIDATED)，事件链完整可复算（4.3）。
  S2. 矩阵 #5：总体 0.95（≥ 门槛）但最近 5 次失败 ≥ 2 → 立即 DEGRADED，
      detail 同时给出 success_rate 与 window_failures（E6 窗口优先）。
  S3. 矩阵 #6：validated_builds=[1024,1025]，build=1026——record_run **不**
      追加 build；Guard+执行成功（EXECUTE）后 record_success_build 才追加
      （E7 记账语义，与引擎接线同一条代码路径的库侧契约）。
  S4. 矩阵 #7：fingerprint 变化 → REVALIDATION_REQUIRED 事件，状态不动、
      不删除、不拒绝（E8）。
  S5. 矩阵 #8/#9（**经真 CLI**）：非幂等 10/10、risk MEDIUM 100% →
      KEEP_CANDIDATE/NOT_ELIGIBLE 不升级；同批 LOW/IDEMPOTENT 10/10 →
      VERIFIED——`mta experience verify` 的 E4 资格**必须**经 Repository
      解析元素 + eligible_for_auto_verification（review_p2_task31 P3-7
      接线前置①的验收项）。
  S6. 矩阵 #11：Candidate 闲置 > max_idle_days → REJECTED(STALE)，证据保留。
  S7. 矩阵 #12：双线程各 record_run 100 次 → 计数和 200 无丢失（E9 单写者：
      threading.Lock + BEGIN IMMEDIATE）。
  S8. CLI 冒烟：list/show/revalidate/sweep 退出码与输出形状。

产出：out/p2_m3_gate/summary.json；exit 0 = Gate M3 离线判据全绿。
"""
from __future__ import annotations

import io
import json
import sys
import threading
import time
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from experience.models import ExperienceStatus  # noqa: E402

OUT_DIR = ROOT / "out" / "p2_m3_gate"

results: dict[str, dict] = {}
_T0 = time.time()


def check(name: str, ok: bool, detail: str) -> bool:
    results[name] = {"pass": bool(ok), "detail": detail}
    print(f"{'PASS' if ok else 'FAIL'} {name}: {detail}")
    return bool(ok)


def _run_cli(argv: list[str]) -> tuple[int, str]:
    from cli.main import main
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


OVERRIDES_YAML = """\
schema_version: "1.0"
kind: element
id: ok_button
screen: GateView
type: button
strategies:
  - {type: accessibility_id, value: ok_btn_v2, origin: manual,
     source_file: X.swift, source_line: 1}
metadata:
  risk: LOW
  idempotency: IDEMPOTENT
  data_class: INTERNAL
---
schema_version: "1.0"
kind: element
id: submit_order
screen: GateView
type: button
strategies:
  - {type: accessibility_id, value: order_btn_v2, origin: manual,
     source_file: X.swift, source_line: 2}
metadata:
  risk: LOW
  idempotency: NON_IDEMPOTENT
  data_class: INTERNAL
---
schema_version: "1.0"
kind: element
id: pay_button
screen: GateView
type: button
strategies:
  - {type: accessibility_id, value: pay_btn_v2, origin: manual,
     source_file: X.swift, source_line: 3}
metadata:
  risk: MEDIUM
  idempotency: IDEMPOTENT
  data_class: INTERNAL
"""


def _fresh_store(db: Path):
    from experience import SQLiteExperienceStore
    return SQLiteExperienceStore(str(db))


def _seed(store, *, target_id: str, screen_id: str = "GateView",
          review_id: int = 7):
    from experience.models import CandidateSeed
    from repository.loader import LocatorStrategy
    return store.create_candidate(CandidateSeed(
        review_id=review_id, recovery_id=review_id,
        seed_run_id="run_seed", seed_step_id=12,
        seed_recovery_review_id=review_id, app_id="com.gate",
        screen_id=screen_id, target_id=target_id,
        strategy=LocatorStrategy(type="accessibility_id", value="v2",
                                 origin="experience")))


def _record(store, exp_id: str, results_list: list[str]) -> None:
    from experience.models import ExperienceRun
    for i, res in enumerate(results_list):
        store.record_run(exp_id, ExperienceRun(
            experience_id=exp_id, run_id=f"run_{i}", step_id=100 + i,
            app_build="1026", result=res))


def s1_full_transition(db: Path) -> bool:
    store = _fresh_store(db)
    exp = _seed(store, target_id="ok_button")
    eid = exp.experience_id
    _record(store, eid, ["SUCCESS"] * 10)
    from experience import apply_outcome, evaluate, revalidate
    from experience.models import ExperienceStatus, VerificationPolicy

    runs = store.get_runs(eid)
    out1 = evaluate(store.get_experience(eid), runs, VerificationPolicy(),
                    auto_verify_eligible=True)
    apply_outcome(store, store.get_experience(eid), out1,
                  operator="gate", run_id="run_gate1")
    st1 = store.get_experience(eid).status

    _record(store, eid, ["FAILURE", "FAILURE"] )
    runs = store.get_runs(eid)
    out2 = evaluate(store.get_experience(eid), runs, VerificationPolicy(),
                    auto_verify_eligible=True)
    apply_outcome(store, store.get_experience(eid), out2,
                  operator="gate", run_id="run_gate2")
    st2 = store.get_experience(eid).status

    revalidate(store, store.get_experience(eid), fingerprint="fp_recheck",
               operator="gate_operator")
    exp_final = store.get_experience(eid)
    events = store.get_state_events(eid)
    chain = [(e.from_status.value if e.from_status else None,
              e.to_status.value, e.reason) for e in events]
    ok = check(
        "S1_full_state_transition",
        out1.reason == "THRESHOLD_MET" and st1 is ExperienceStatus.VERIFIED
        and out2.reason == "SLIDING_WINDOW" and st2 is ExperienceStatus.DEGRADED
        and exp_final.status is ExperienceStatus.VERIFIED
        and exp_final.last_screen_fingerprint == "fp_recheck"
        and chain == [(None, "CANDIDATE", "SEEDED"),
                      ("CANDIDATE", "VERIFIED", "THRESHOLD_MET"),
                      ("VERIFIED", "DEGRADED", "SLIDING_WINDOW"),
                      ("DEGRADED", "VERIFIED", "REVALIDATED")],
        f"chain={chain}")
    return ok


def s2_matrix5_sliding_window(db: Path) -> bool:
    """矩阵 #5 的自洽数字（review_p2_task31：设计原文 100 次 98 成功与
    「最近 5 次 3 失败」自相矛盾——用 19/20=0.95 才真的考到「窗口优先」）。"""
    store = _fresh_store(db)
    exp = _seed(store, target_id="ok_button", review_id=8)
    store.update_status(exp.experience_id, ExperienceStatus.VERIFIED,
                        "THRESHOLD_MET", operator="gate")
    tail = ["FAILURE", "FAILURE", "FAILURE", "SUCCESS", "SUCCESS"]
    _record(store, exp.experience_id, ["SUCCESS"] * 95 + tail)
    from experience import evaluate
    from experience.models import VerificationPolicy

    out = evaluate(store.get_experience(exp.experience_id),
                   store.get_runs(exp.experience_id), VerificationPolicy(),
                   auto_verify_eligible=True)
    return check(
        "S2_matrix5_window_overrides_rate",
        out.decision.value == "DEGRADE" and out.reason == "SLIDING_WINDOW"
        and out.detail["success_rate"] == 0.97
        and out.detail["window_failures"] == 3,
        f"decision={out.decision.value}/{out.reason} "
        f"rate={out.detail['success_rate']} "
        f"window_failures={out.detail['window_failures']}")


def s3_matrix6_validated_builds(db: Path) -> bool:
    """矩阵 #6：新 build 不直接信任——库侧契约是「record_run 不动集合，
    完整 Guard+执行成功后 record_success_build 才追加」（E7；引擎在
    EXECUTE 分支调它，Task 2.4 接线）。"""
    store = _fresh_store(db)
    exp = _seed(store, target_id="ok_button", review_id=9)
    eid = exp.experience_id
    store.record_success_build(eid, "1024")
    store.record_success_build(eid, "1025")
    _record(store, eid, ["SUCCESS"])          # build=1026 的普通样本
    after_run = store.get_experience(eid).validated_builds
    store.record_success_build(eid, "1026")   # Guard+执行成功后的追加
    after_success = store.get_experience(eid).validated_builds
    store.record_success_build(eid, "1026")   # E7 集合语义：重复 no-op
    after_dup = store.get_experience(eid).validated_builds
    return check(
        "S3_matrix6_build_not_trusted_until_executed",
        after_run == ["1024", "1025"]
        and after_success == ["1024", "1025", "1026"]
        and after_dup == ["1024", "1025", "1026"],
        f"after_run={after_run} after_success={after_success} "
        f"dup={after_dup}")


def s4_matrix7_fingerprint_change(db: Path) -> bool:
    store = _fresh_store(db)
    exp = _seed(store, target_id="ok_button", review_id=10)
    eid = exp.experience_id
    store.record_screen_fingerprint(eid, "fp_old")
    from experience import (mark_revalidation_required, needs_revalidation)

    changed = needs_revalidation(store.get_experience(eid), "fp_new")
    marked = mark_revalidation_required(store, store.get_experience(eid),
                                        operator="gate")
    again = mark_revalidation_required(store, store.get_experience(eid),
                                       operator="gate")
    exp_after = store.get_experience(eid)
    events = store.get_state_events(eid)
    reasons = [e.reason for e in events]
    return check(
        "S4_matrix7_revalidation_required_no_delete",
        changed is True and marked is True and again is False
        and exp_after.status is ExperienceStatus.CANDIDATE
        and reasons.count("REVALIDATION_REQUIRED") == 1
        and len(store.get_runs(eid)) == 0,
        f"changed={changed} marked={marked} idempotent={again} "
        f"status={exp_after.status.value} events={reasons}")


def s5_matrix8_9_e4_via_cli(db: Path, overrides: Path) -> bool:
    """矩阵 #8/#9 经**真 CLI**：E4 资格经 Repository 解析元素判定。"""
    store = _fresh_store(db)
    ok_exp = _seed(store, target_id="ok_button", review_id=11)
    nonidem = _seed(store, target_id="submit_order", review_id=12)
    medium = _seed(store, target_id="pay_button", review_id=13)
    for eid in (ok_exp.experience_id, nonidem.experience_id,
                medium.experience_id):
        _record(store, eid, ["SUCCESS"] * 10)

    code, out = _run_cli(["experience", "verify", "--exp-db", str(db),
                          "--overrides", str(overrides)])
    statuses = {e.experience_id: e.status
                for e in store.list()}
    return check(
        "S5_matrix8_9_e4_single_point_via_cli",
        code == 0
        and statuses[ok_exp.experience_id] is ExperienceStatus.VERIFIED
        and statuses[nonidem.experience_id] is ExperienceStatus.CANDIDATE
        and statuses[medium.experience_id] is ExperienceStatus.CANDIDATE
        and "NOT_ELIGIBLE" in out and "eligible=False" in out
        and "THRESHOLD_MET" in out and "eligible=True" in out,
        f"exit={code} ok=VERIFIED nonidem/medium=CANDIDATE "
        f"(report: NOT_ELIGIBLE ×2, THRESHOLD_MET ×1)")


def s6_matrix11_stale(db: Path) -> bool:
    store = _fresh_store(db)
    stale = _seed(store, target_id="ok_button", review_id=14)
    fresh = _seed(store, target_id="submit_order", review_id=15)
    _record(store, stale.experience_id, ["SUCCESS"])
    back = (datetime.now(timezone.utc) - timedelta(days=200)) \
        .strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with store._write_tx() as conn:
        conn.execute("UPDATE experiences SET created_at=?, updated_at=?"
                     " WHERE experience_id=?",
                     (back, back, stale.experience_id))
        conn.execute("UPDATE experience_runs SET created_at=?"
                     " WHERE experience_id=?", (back, stale.experience_id))
    from experience import sweep_stale_candidates, StalenessPolicy
    swept = sweep_stale_candidates(store, StalenessPolicy(max_idle_days=90))
    statuses = {e.experience_id: e.status for e in store.list()}
    runs_kept = len(store.get_runs(stale.experience_id))
    return check(
        "S6_matrix11_stale_90d_evidence_kept",
        swept == [stale.experience_id]
        and statuses[stale.experience_id] is ExperienceStatus.REJECTED
        and statuses[fresh.experience_id] is ExperienceStatus.CANDIDATE
        and runs_kept == 1,
        f"swept={swept} runs_kept={runs_kept}")


def s7_matrix12_concurrent_writers(db: Path) -> bool:
    """矩阵 #12：双线程各 record_run 100 次 → 200 无丢失（E9 单写者）。"""
    store = _fresh_store(db)
    exp = _seed(store, target_id="ok_button", review_id=16)
    eid = exp.experience_id
    from experience.models import ExperienceRun

    errors: list[str] = []

    def worker(prefix: str) -> None:
        try:
            for i in range(100):
                store.record_run(eid, ExperienceRun(
                    experience_id=eid, run_id=f"{prefix}_{i}",
                    step_id=i + 1, app_build="1026", result="SUCCESS"))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")

    t1 = threading.Thread(target=worker, args=("t1",))
    t2 = threading.Thread(target=worker, args=("t2",))
    t1.start(); t2.start()
    t1.join(); t2.join()
    exp_after = store.get_experience(eid)
    run_rows = len(store.get_runs(eid))
    return check(
        "S7_matrix12_single_writer_no_lost_update",
        not errors and run_rows == 200 and exp_after.sample_count == 200
        and exp_after.success_count == 200,
        f"rows={run_rows} sample_count={exp_after.sample_count} "
        f"errors={errors[:2]}")


def s8_cli_smoke(db: Path) -> bool:
    code_l, out_l = _run_cli(["experience", "list", "--exp-db", str(db)])
    eid = store_first_id(db)
    code_s, out_s = _run_cli(["experience", "show", eid,
                              "--exp-db", str(db)])
    code_r, out_r = _run_cli(["experience", "revalidate", eid,
                              "--exp-db", str(db), "--fingerprint", "fp"])
    return check(
        "S8_cli_smoke_exit_codes",
        code_l == 0 and code_s == 0 and code_r == 3
        and eid in out_l and "state_events" in out_s
        and "下一步" in out_r,
        f"list={code_l} show={code_s} revalidate_on_candidate={code_r}"
        "（3=护栏出口，指引走 verify）")


def store_first_id(db: Path) -> str:
    conn = sqlite3_connect(db)
    try:
        row = conn.execute("SELECT experience_id FROM experiences"
                           " ORDER BY rowid LIMIT 1").fetchone()
        return row[0]
    finally:
        conn.close()


def sqlite3_connect(db: Path):
    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    work = OUT_DIR / "gate_env"
    work.mkdir(parents=True, exist_ok=True)
    overrides = work / "overrides" / "elements"
    overrides.mkdir(parents=True, exist_ok=True)
    (overrides / "GateView.yaml").write_text(OVERRIDES_YAML,
                                             encoding="utf-8")

    ok = True
    # 每个判据独立临时库——失败互不传染，summary 可逐项复核
    for name, fn in (("s1", s1_full_transition), ("s2", s2_matrix5_sliding_window),
                     ("s3", s3_matrix6_validated_builds),
                     ("s4", s4_matrix7_fingerprint_change),
                     ("s6", s6_matrix11_stale),
                     ("s7", s7_matrix12_concurrent_writers)):
        db = work / f"{name}.db"
        db.unlink(missing_ok=True)
        ok &= fn(db)
    db5 = work / "s5.db"
    db5.unlink(missing_ok=True)
    ok &= s5_matrix8_9_e4_via_cli(db5, work / "overrides")
    ok &= s8_cli_smoke(db5)

    code = 0 if ok else 1
    summary = {"verdict": "PASS" if ok else "FAIL", "gate": "M3",
               "offline": True,
               "matrix": ["#5", "#6", "#7", "#8", "#9", "#11", "#12"],
               "elapsed_s": round(time.time() - _T0, 1),
               "results": results}
    (OUT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"summary: {OUT_DIR / 'summary.json'}")
    print(f"verify_p2_m3: exit {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
