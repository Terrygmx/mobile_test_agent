"""Task 5.1 / P3-20：`bug_candidates` 表的读写（迁移 005；设计 §11）。

三件事，各钉一块：
1. **迁移 005 落表且幂等**，版本串跟着链尾走（`004_review_note` → `005_bug_candidates`）；
2. **SQL CHECK 词表 ↔ Python 常量逐值一致**（手法同 001 的
   `test_state_check_constraint_matches_enum`：加值忘改 SQL 会红）；
3. **写路径的两道闸门**：`status` 不是 `save_bug_candidate` 的形参（绕过状态机
   的旁路在构造上不可达）；已推进的行不可被诊断重跑覆写（AE 同款）。
"""
from __future__ import annotations

import re
import sqlite3

import pytest

from agents.storage import AGENT_SCHEMA_VERSION, SQLiteAgentStore
from candidates.bug import BugCandidate
from diagnosis.models import (ATTRIBUTIONS, BUG_SEVERITIES, BUG_STATUSES,
                              EvidenceItem)


@pytest.fixture()
def store(tmp_path) -> SQLiteAgentStore:
    return SQLiteAgentStore(tmp_path / "agent.db")


def _evidence() -> list[dict]:
    return [EvidenceItem(kind="crash_detected", detail="进程消失",
                         source="trace:run_1/step_1").model_dump(mode="json")]


def _save(store, cid="bug_1", **kw):
    return store.save_bug_candidate(cid, title="支付页崩溃",
                                    evidence=_evidence(),
                                    failure_type="APP_CRASH", **kw)


# --- 迁移 -----------------------------------------------------------------------


def test_migrations_are_idempotent_and_version_follows_the_chain(tmp_path):
    db = tmp_path / "agent.db"
    SQLiteAgentStore(db)
    SQLiteAgentStore(db)            # 重放：二次不炸
    # 版本串从**链尾派生**（`latest_version`），故加 005 后这里跟着变——
    # 这正是它该有的行为（加脚本忘改常量也不会撒谎）。
    assert AGENT_SCHEMA_VERSION == "005_bug_candidates"


def test_bug_candidates_table_exists(store):
    conn = store._connect()
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert "bug_candidates" in tables


def test_ddl_check_vocabularies_match_python_constants(store):
    """迁移 005 的三个 CHECK 词表与 `diagnosis/models.py` 的常量逐值一致。

    词表有**两处字面量**（SQL 的 CHECK 与 Python 常量）。加第 8 个状态忘改
    SQL → `save_bug_candidate` 会抛 `IntegrityError`（loud，不会静默坏数据），
    但 loud 失败也该在 CI 里更早被发现。
    """
    conn = store._connect()
    try:
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table'"
            " AND name='bug_candidates'").fetchone()[0]
    finally:
        conn.close()

    def values(column: str) -> set[str]:
        block = re.search(
            rf"CHECK\s*\(\s*{column}\s+IN\s*\((.*?)\)\s*\)", ddl, re.S)
        assert block, f"没解析到 {column} 的 CHECK 子句：{ddl!r}"
        return set(re.findall(r"'([^']*)'", block.group(1)))

    assert values("status") == set(BUG_STATUSES), "status 词表漂移"
    assert values("severity") == set(BUG_SEVERITIES), "severity 词表漂移"
    assert values("attribution_hypothesis") == set(ATTRIBUTIONS), \
        "attribution 词表漂移"


def test_status_check_rejects_a_bad_value_written_by_raw_sql(store):
    with pytest.raises(sqlite3.IntegrityError):
        with store._write_tx() as conn:
            conn.execute(
                "INSERT INTO bug_candidates (candidate_id, title,"
                " failure_type, status, created_at)"
                " VALUES ('x','t','','BOGUS','now')")
    assert not store._write_lock.locked(), "CHECK 失败同样不得泄漏锁"
    assert store.get_bug_candidate("x") is None, "坏值没有落库"


# --- 写路径 ---------------------------------------------------------------------


def test_save_and_get_round_trip(store):
    row = _save(store, severity="HIGH", evidence_score=7,
                dedup_key="dk_1", affected_tests=["pay_002"])
    assert row["status"] == "DISCOVERED", "save 只写 DISCOVERED"
    assert row["evidence"][0]["kind"] == "crash_detected"
    assert row["evidence_score"] == 7
    assert row["dedup_key"] == "dk_1"
    assert row["affected_tests"] == ["pay_002"]
    assert row["created_at"], "created_at 由 store 盖章"

    got = store.get_bug_candidate("bug_1")
    assert got == row


def test_reproducible_is_three_state(store):
    """`NULL` = 还没试过复现 / `0` = 试过但复现不了 / `1` = 复现了。

    把 `False` 折成 `None`（`or None` 的经典写法）会让「复现不了」被静默
    改写成「还没试过」——那是本仓最防的「看起来正常」。
    """
    assert _save(store, "b_unknown")["reproducible"] is None
    assert _save(store, "b_no", reproducible=False)["reproducible"] is False
    assert _save(store, "b_yes", reproducible=True)["reproducible"] is True

    # 往返也要保持三态（`BugCandidate.from_store_dict` 是同一个坑的第二处）
    for cid, expected in (("b_unknown", None), ("b_no", False),
                          ("b_yes", True)):
        row = store.get_bug_candidate(cid)
        assert BugCandidate.from_store_dict(row).reproducible is expected


def test_save_is_idempotent_but_never_overwrites_a_promoted_row(store):
    _save(store, evidence_score=1)
    assert _save(store, evidence_score=2)["evidence_score"] == 2, \
        "同 id 重存覆盖（诊断重跑同一次失败算同 id）"

    # 推进状态（Task 5.6 的状态机入口；本任务用裸 UPDATE 模拟已推进的行）
    with store._write_tx() as conn:
        conn.execute("UPDATE bug_candidates SET status='HUMAN_REVIEW'"
                     " WHERE candidate_id='bug_1'")
    with pytest.raises(ValueError, match="已推进"):
        _save(store, evidence_score=3)


def test_status_is_not_a_save_parameter(store):
    """`status` 不在形参里（**删掉而不是加断言**）：调用方传它会 `TypeError`
    fail-loud——绕过状态机的旁路在构造上不可达（与 `save_candidate` 的
    `review_status` 同款处置，review_p3_task35 P2-2）。"""
    with pytest.raises(TypeError):
        store.save_bug_candidate("b", title="t", evidence=[], status="CONFIRMED")


def test_blank_title_is_rejected(store):
    with pytest.raises(ValueError, match="title"):
        store.save_bug_candidate("b", title="   ", evidence=[])


# --- 列表与过滤 -----------------------------------------------------------------


def test_list_filters_by_status_and_dedup_key(store):
    _save(store, "b1", dedup_key="dk_1")
    _save(store, "b2", dedup_key="dk_2")
    _save(store, "b3", dedup_key="dk_1")

    assert {r["candidate_id"] for r in store.list_bug_candidates()} == \
        {"b1", "b2", "b3"}
    assert {r["candidate_id"]
            for r in store.list_bug_candidates(status="DISCOVERED")} == \
        {"b1", "b2", "b3"}
    assert store.list_bug_candidates(status="CONFIRMED") == []
    # F8 的合并查询：同 dedup_key 的行要能一次取全（Task 5.5 消费）
    assert {r["candidate_id"]
            for r in store.list_bug_candidates(dedup_key="dk_1")} == \
        {"b1", "b3"}


def test_bug_candidate_row_model_round_trips(store):
    _save(store, "b_rt", severity="CRITICAL", evidence_score=11,
          affected_tests=["a", "b"])
    row = store.get_bug_candidate("b_rt")
    model = BugCandidate.from_store_dict(row)
    assert model.severity == "CRITICAL"
    assert model.evidence_score == 11
    assert model.affected_tests == ["a", "b"]
    assert model.evidence[0].kind == "crash_detected"
    assert model.to_store_dict()["candidate_id"] == "b_rt"
