"""Task 2.1 / P2-03：SQLiteExperienceStore 测试（设计 7.1 / E5 / E7 / E9 / 11.2）。

plan step 1 失败测试清单：
  - create_candidate 种子三件套校验（E5）；
  - lookup 按 (app_id, screen_id, target_id) 返回列表；
  - record_run 追加且 sample_count == success + failure 恒成立；
  - update_status 写 experience_state_events（from/to/reason/operator）；
  - validated_builds 只在 record_success_build() 追加（E7）；
  - runs 数据不物理删除（DEGRADED/REJECTED 后保留，11.2）。
外加：E9 单写者（并发写线程全量落账 + 读者并发安全）、round-trip。

H18：全部离设备、离网络（SQLite 临时库）。
"""
from __future__ import annotations

import threading

import pytest

from experience.models import (
    CandidateSeed,
    ExperienceRun,
    ExperienceStatus,
)
from experience.store import SQLiteExperienceStore, EmptyExperienceStore
from repository.loader import LocatorStrategy


def _seed(**kw) -> CandidateSeed:
    d = dict(
        review_id=7, recovery_id=1,
        seed_run_id="run_a", seed_step_id=12, seed_recovery_review_id=7,
        app_id="com.phaset0.logindemo", screen_id="LoginView",
        target_id="username_field",
        strategy=LocatorStrategy(type="accessibility_id",
                                 value="username_field_v2",
                                 origin="manual"),
        app_build="1025", reviewer="alice",
    )
    d.update(kw)
    return CandidateSeed(**d)


def _run(experience_id: str, result: str = "SUCCESS", run_id: str = "run_b",
         **kw) -> ExperienceRun:
    d = dict(experience_id=experience_id, run_id=run_id, step_id=33,
             app_build="1026", result=result)
    d.update(kw)
    return ExperienceRun(**d)


@pytest.fixture()
def store(tmp_path):
    return SQLiteExperienceStore(tmp_path / "exp.db")


# --- E5：create_candidate 三件套校验 -------------------------------------------


def test_create_candidate_roundtrip(store):
    e = store.create_candidate(_seed())
    assert e.experience_id.startswith("exp_")
    assert e.status is ExperienceStatus.CANDIDATE
    assert e.sample_count == 0 and e.validated_builds == []
    assert e.strategy.value == "username_field_v2"
    # lookup 按 (app_id, screen_id, target_id) 返回列表
    got = store.lookup("com.phaset0.logindemo", "LoginView",
                       "username_field")
    assert [x.experience_id for x in got] == [e.experience_id]
    assert got[0].strategy.value == "username_field_v2"  # 策略 round-trip
    # list() 同样可见
    assert [x.experience_id for x in store.list()] == [e.experience_id]


def test_create_candidate_rejects_incomplete_triplet(store):
    """E5：Store 写入前二次校验（模型层之外的闸）。"""
    with pytest.raises(ValueError, match="E5|incomplete seed triplet"):
        store.create_candidate(
            _seed(seed_run_id="", seed_step_id=0,
                  seed_recovery_review_id=0))


def test_same_key_two_candidates_coexist(store):
    """同 (app,screen,target) 的两次 ACCEPT（不同候选策略）各自成行。"""
    e1 = store.create_candidate(_seed())
    e2 = store.create_candidate(
        _seed(review_id=8, recovery_id=2, seed_recovery_review_id=8,
              seed_run_id="run_z", seed_step_id=44,
              strategy=LocatorStrategy(type="accessibility_id",
                                       value="user_login_field",
                                       origin="manual")))
    got = store.lookup("com.phaset0.logindemo", "LoginView",
                       "username_field")
    assert {x.experience_id for x in got} == {e1.experience_id,
                                              e2.experience_id}


# --- record_run：追加 + 统计一致性 ----------------------------------------------


def test_record_run_appends_and_keeps_counters_consistent(store):
    e = store.create_candidate(_seed())
    store.record_run(e.experience_id, _run(e.experience_id, "SUCCESS"))
    store.record_run(e.experience_id, _run(e.experience_id, "SUCCESS",
                                           run_id="run_c"))
    store.record_run(e.experience_id, _run(e.experience_id, "FAILURE",
                                           run_id="run_d"))
    got = store.lookup("com.phaset0.logindemo", "LoginView",
                       "username_field")[0]
    assert (got.sample_count, got.success_count, got.failure_count) == (3, 2, 1)
    assert got.sample_count == got.success_count + got.failure_count
    assert got.success_rate == pytest.approx(2 / 3)
    runs = store.get_runs(e.experience_id)
    assert [r.run_id for r in runs] == ["run_b", "run_c", "run_d"]  # 时序升序


def test_record_run_does_not_touch_validated_builds(store):
    """E7：validated_builds 只在 record_success_build 追加——record_run
    （静态校验路径）不产生 build 验证事实。"""
    e = store.create_candidate(_seed())
    store.record_run(e.experience_id, _run(e.experience_id, "SUCCESS"))
    got = store.lookup("com.phaset0.logindemo", "LoginView",
                       "username_field")[0]
    assert got.validated_builds == []


def test_record_success_build_is_set_semantics(store):
    """E7：只在 record_success_build 追加；重复 build 无操作（幂等）。"""
    e = store.create_candidate(_seed())
    store.record_success_build(e.experience_id, "1025")
    store.record_success_build(e.experience_id, "1025")
    store.record_success_build(e.experience_id, "1026")
    got = store.lookup("com.phaset0.logindemo", "LoginView",
                       "username_field")[0]
    assert got.validated_builds == ["1025", "1026"]


def test_record_run_unknown_experience_fails(store):
    with pytest.raises(LookupError):
        store.record_run("exp_missing", _run("exp_missing"))
    with pytest.raises(LookupError):
        store.record_success_build("exp_missing", "1025")


# --- update_status：事件留痕 + runs 不物理删除（11.2） ---------------------------


def test_update_status_writes_state_event(store):
    e = store.create_candidate(_seed())
    store.record_run(e.experience_id, _run(e.experience_id, "FAILURE"))
    store.update_status(e.experience_id, ExperienceStatus.REJECTED,
                        "SLIDING_WINDOW_DEGRADE", operator="alice",
                        run_id="run_b", app_build="1026")
    got = store.list(ExperienceStatus.REJECTED)
    assert [x.experience_id for x in got] == [e.experience_id]
    # 事件行：from/to/reason/operator
    import sqlite3
    conn = sqlite3.connect(store._path)
    conn.row_factory = sqlite3.Row
    ev = conn.execute(
        "SELECT * FROM experience_state_events WHERE experience_id=?",
        (e.experience_id,)).fetchall()
    conn.close()
    assert len(ev) == 2   # SEEDED + REJECTED
    last = ev[-1]
    assert last["from_status"] == "CANDIDATE"
    assert last["to_status"] == "REJECTED"
    assert last["reason"] == "SLIDING_WINDOW_DEGRADE"
    assert last["operator"] == "alice"
    assert last["run_id"] == "run_b"


def test_update_status_same_status_is_noop_no_event(store):
    e = store.create_candidate(_seed())
    store.update_status(e.experience_id, ExperienceStatus.CANDIDATE,
                        "NO_CHANGE")
    import sqlite3
    conn = sqlite3.connect(store._path)
    n = conn.execute(
        "SELECT COUNT(*) FROM experience_state_events"
        " WHERE to_status='CANDIDATE' AND reason='NO_CHANGE'").fetchone()[0]
    conn.close()
    assert n == 0, "同状态 no-op 不产生事件"


def test_update_status_unknown_experience_fails(store):
    with pytest.raises(LookupError):
        store.update_status("exp_missing", ExperienceStatus.VERIFIED, "x")


def test_runs_survive_degrade_and_reject_11_2(store):
    """11.2：DEGRADED/REJECTED 后 runs **不物理删除**（至少保留 180 天）；
    Store 没有任何 delete 路径。"""
    e = store.create_candidate(_seed())
    for i in range(3):
        store.record_run(e.experience_id,
                         _run(e.experience_id, "FAILURE", run_id=f"r{i}"))
    store.update_status(e.experience_id, ExperienceStatus.DEGRADED,
                        "SLIDING_WINDOW_DEGRADE")
    store.update_status(e.experience_id, ExperienceStatus.REJECTED,
                        "MANUAL")
    assert len(store.get_runs(e.experience_id)) == 3
    assert store.list(ExperienceStatus.REJECTED)[0].sample_count == 3


# --- lookup：REJECTED 不返回（Guard 不该看见不可用策略） -------------------------


def test_lookup_excludes_rejected(store):
    e1 = store.create_candidate(_seed())
    e2 = store.create_candidate(
        _seed(review_id=8, recovery_id=2, seed_recovery_review_id=8,
              seed_run_id="run_z", seed_step_id=44,
              strategy=LocatorStrategy(type="accessibility_id",
                                       value="user_login_field",
                                       origin="manual")))
    store.update_status(e2.experience_id, ExperienceStatus.REJECTED,
                        "MANUAL")
    got = store.lookup("com.phaset0.logindemo", "LoginView",
                       "username_field")
    assert [x.experience_id for x in got] == [e1.experience_id]
    # list() 仍可见（审计不丢）
    assert len(store.list(ExperienceStatus.REJECTED)) == 1


# --- E9：单写者——并发写全量落账、读者并发安全 -----------------------------------


def test_single_writer_concurrent_record_runs(store):
    """E9：N 线程并发 record_run——全部落账且计数恒一致（进程锁 +
    BEGIN IMMEDIATE）。"""
    e = store.create_candidate(_seed())
    errors: list[Exception] = []

    def worker(tid: int) -> None:
        try:
            for i in range(10):
                result = "SUCCESS" if (tid + i) % 3 else "FAILURE"
                store.record_run(e.experience_id, _run(
                    e.experience_id, result, run_id=f"r{tid}_{i}"))
        except Exception as exc:  # pragma: no cover - 失败即测试失败
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    got = store.lookup("com.phaset0.logindemo", "LoginView",
                       "username_field")[0]
    assert got.sample_count == 80
    assert got.sample_count == got.success_count + got.failure_count
    assert len(store.get_runs(e.experience_id)) == 80


def test_readers_are_safe_during_writes(store):
    """E9：Reader（lookup/get_runs/list）与写并发——不炸、不挂。"""
    e = store.create_candidate(_seed())
    stop = threading.Event()
    # CPython list.append 原子（GIL）——多线程 append 无需加锁，别「修」
    errors: list[Exception] = []

    def writer() -> None:
        for i in range(30):
            store.record_run(e.experience_id,
                             _run(e.experience_id, "SUCCESS",
                                  run_id=f"rw{i}"))
        stop.set()

    def reader() -> None:
        while not stop.is_set():
            store.lookup("com.phaset0.logindemo", "LoginView",
                         "username_field")
            store.list()
            store.get_runs(e.experience_id)

    wt = threading.Thread(target=writer)
    rts = [threading.Thread(target=reader) for _ in range(3)]
    wt.start()
    for t in rts:
        t.start()
    wt.join()
    stop.set()
    for t in rts:
        t.join()
    assert not errors


def test_get_runs_limit_takes_most_recent(store):
    e = store.create_candidate(_seed())
    for i in range(5):
        store.record_run(e.experience_id,
                         _run(e.experience_id, "SUCCESS", run_id=f"r{i}"))
    runs = store.get_runs(e.experience_id, limit=2)
    assert [r.run_id for r in runs] == ["r3", "r4"]  # 最近 2 条，仍按时间升序


# --- EmptyExperienceStore：P1 占位行为不变 ---------------------------------------


def test_empty_store_placeholder_unchanged():
    """P1 引擎的调用形态（旧签名）行为不变——Task 2.4 接真 Store 后退役。"""
    empty = EmptyExperienceStore()
    assert empty.lookup("1025", "LoginView", "username_field") == []
