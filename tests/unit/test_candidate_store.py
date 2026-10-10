"""Task 3.4（P3-12）：`test_candidates` / `test_candidate_rejections` 的表读写。

三块：

1. **迁移 002 与词表对账**：SQL 的 `CHECK` 词表与 `candidates/test_case.py` 的
   Python 常量是**两处字面量**——加值忘改一边会红（手法同
   `test_agent_store.py::test_state_check_constraint_matches_enum`）。
2. **行的读写**（含 upsert 幂等 / append-only / 未决集过滤）。
3. **`testcase_yaml` 的可复核性**：它是给人看的、也是 Task 3.5 直接落盘的文本
   ——必须能被 P1 的 `parse_testcase_dict` 原样读回（否则「库里存着」与
   「落盘能用」是两回事）。
"""
from __future__ import annotations

import re
import sqlite3

import pytest
import yaml

from agents.storage import SQLiteAgentStore
from candidates.test_case import (DRY_RUN_STATUSES, REJECTION_REASONS,
                                  REVIEW_STATUSES, CandidateRejection,
                                  TestCandidate, dump_testcase_yaml)
from testcase.schema import GenerationEvidence, parse_testcase_dict


@pytest.fixture
def store(tmp_path):
    return SQLiteAgentStore(tmp_path / "agent.db")


def _evidence() -> dict:
    return {"coverage_gap": ["LoginView -> HomeView"],
            "bug_history": [], "source_refs": []}


def _candidate(**over) -> dict:
    kw = {"testcase_yaml": "schema_version: '0.2'\nid: c\nname: c\n",
          "generation_evidence": _evidence(), "fingerprint": "abc123",
          "generated_by": "generator_agent", "dry_run_status": "PASS"}
    kw.update(over)
    return kw


# --- 迁移与词表对账 -----------------------------------------------------------


def test_002_creates_both_tables(store):
    conn = store._connect()
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    assert {"test_candidates", "test_candidate_rejections"} <= tables


def _check_values(ddl: str, column: str) -> set[str]:
    block = re.search(
        rf"CHECK\s*\(\s*{column}\s+IN\s*\((.*?)\)\s*\)", ddl, re.S)
    assert block, f"没解析到 {column} 的 CHECK 子句：{ddl!r}"
    return set(re.findall(r"'([^']*)'", block.group(1)))


@pytest.mark.parametrize("column,expected", [
    ("dry_run_status", DRY_RUN_STATUSES),
    ("review_status", REVIEW_STATUSES),
])
def test_candidate_check_vocab_matches_python(store, column, expected):
    """SQL 的 `CHECK (… IN (…))` 与 Python 常量逐值一致（两处字面量）。"""
    conn = store._connect()
    try:
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table'"
            " AND name='test_candidates'").fetchone()[0]
    finally:
        conn.close()
    assert _check_values(ddl, column) == set(expected), \
        f"{column} 的 SQL 词表与 Python 常量漂移"


def test_rejection_check_vocab_matches_python(store):
    conn = store._connect()
    try:
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table'"
            " AND name='test_candidate_rejections'").fetchone()[0]
    finally:
        conn.close()
    assert _check_values(ddl, "reason") == set(REJECTION_REASONS)


def test_002_migration_is_idempotent(tmp_path):
    db = tmp_path / "agent.db"
    SQLiteAgentStore(db)
    SQLiteAgentStore(db)            # 重放：二次不炸
    from agents.storage import AGENT_SCHEMA_VERSION
    assert AGENT_SCHEMA_VERSION == "002_test_candidates"


@pytest.mark.parametrize("column,value", [
    ("dry_run_status", "BOGUS"),
    ("review_status", "BOGUS"),
])
def test_candidate_check_rejects_bad_value(store, column, value):
    """绕过 Python 直接写坏值 → CHECK 兜住（不静默落库）。"""
    with pytest.raises(sqlite3.IntegrityError):
        with store._write_tx() as conn:
            conn.execute(
                f"INSERT INTO test_candidates (candidate_id, testcase_yaml,"
                f" created_at, {column}) VALUES ('c','y','t',?)", (value,))


def test_rejection_check_rejects_bad_reason(store):
    with pytest.raises(sqlite3.IntegrityError):
        with store._write_tx() as conn:
            conn.execute(
                "INSERT INTO test_candidate_rejections (rejection_id, reason,"
                " created_at) VALUES ('r','BOGUS','t')")


# --- 读写 ---------------------------------------------------------------------


def test_save_and_get_candidate(store):
    row = store.save_candidate("cand_1", **_candidate())
    assert row["candidate_id"] == "cand_1"
    assert row["fingerprint"] == "abc123"
    assert row["dry_run_status"] == "PASS"
    assert row["review_status"] == "PENDING"        # 默认值
    assert row["generation_evidence"] == _evidence()
    assert row["created_at"]
    assert store.get_candidate("cand_1") == row
    assert store.get_candidate("nope") is None


def test_save_candidate_is_upsert_and_keeps_first_created_at(store):
    """同 id 重存 → 覆盖内容但**不改** `created_at`（审计事实）。"""
    first = store.save_candidate("cand_1", **_candidate())
    again = store.save_candidate(
        "cand_1", **{**_candidate(), "dry_run_status": "FAIL",
                     "fingerprint": "def456"})
    assert again["dry_run_status"] == "FAIL"
    assert again["fingerprint"] == "def456"
    assert again["created_at"] == first["created_at"], \
        "created_at 是「第一次生成」的审计事实，重存不该改写"
    assert len(store.list_candidates()) == 1, "upsert 不该线性增长"


def test_list_candidates_filters_pending_set(store):
    """生成器的去重需要**未决集**——已决的候选不该再挡住新候选。"""
    store.save_candidate("cand_a", **_candidate())
    store.save_candidate("cand_b", **{**_candidate(), "review_status": "ACCEPT"})
    store.save_candidate("cand_c", **{**_candidate(), "review_status": "REJECT"})
    pending = store.list_candidates(review_status="PENDING")
    assert [c["candidate_id"] for c in pending] == ["cand_a"]
    assert len(store.list_candidates()) == 3


def test_save_and_get_rejection(store):
    row = store.save_rejection("rej_1", reason="DUPLICATE", candidate_id="cand_1",
                               fingerprint="abc", duplicate_of="login_001",
                               detail={"note": "同语义"})
    assert row["duplicate_of"] == "login_001"
    assert row["detail"] == {"note": "同语义"}
    assert store.get_rejection("rej_1") == row


def test_rejection_is_append_only(store):
    """同 id 重放 → `IntegrityError`（不静默覆盖；见 `save_rejection` 的理由）。"""
    store.save_rejection("rej_1", reason="DUPLICATE")
    with pytest.raises(sqlite3.IntegrityError):
        store.save_rejection("rej_1", reason="SCHEMA_INVALID")


def test_list_rejections_filters_reason(store):
    store.save_rejection("rej_1", reason="DUPLICATE")
    store.save_rejection("rej_2", reason="SCHEMA_INVALID")
    assert [r["rejection_id"]
            for r in store.list_rejections(reason="DUPLICATE")] == ["rej_1"]
    assert len(store.list_rejections()) == 2


def test_rejection_allows_null_candidate_id_and_fingerprint(store):
    """矩阵 #7 的边界：Schema 不过时**可能**连 id 与指纹都取不出来。"""
    row = store.save_rejection("rej_x", reason="SCHEMA_INVALID")
    assert row["candidate_id"] is None
    assert row["fingerprint"] is None
    assert row["duplicate_of"] is None


# --- 行模型与 YAML 文本 -------------------------------------------------------


def test_row_models_roundtrip(store):
    c = TestCandidate.from_store_dict(store.save_candidate("cand_1",
                                                           **_candidate()))
    assert c.candidate_id == "cand_1"
    assert c.generation_evidence == GenerationEvidence(**_evidence())
    # P3-2：`to_store_dict` 是 Task 3.5 落盘要复用的形状——**全键**断言，
    # 不是只看一个键（少一个键 = store 收到 None 的静默缺字段）。
    assert set(c.to_store_dict()) == {
        "candidate_id", "testcase_yaml", "generated_by",
        "generation_evidence", "fingerprint", "dry_run_status",
        "dry_run_detail", "review_status", "reviewer", "reviewed_at"}
    assert c.to_store_dict()["candidate_id"] == "cand_1"
    r = CandidateRejection.from_store_dict(
        store.save_rejection("rej_1", reason="DUPLICATE", duplicate_of="x"))
    assert r.reason == "DUPLICATE" and r.duplicate_of == "x"


def test_row_models_reject_unknown_fields():
    """`extra="forbid"`（与 `agents/models.py` / `planner/models.py` 同款）。"""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TestCandidate(candidate_id="c", testcase_yaml="y",
                      generation_evidence=_evidence(), bogus=1)


def test_dump_testcase_yaml_roundtrips_through_p1_parser():
    """`testcase_yaml` 必须是 **P1 能原样读回**的文本（Task 3.5 直接落盘）。

    不满足这条的话，「库里存着候选」与「ACCEPT 时能落成正式用例」是两回事
    ——而设计 §6.1 的出口正是「走 P1 Repository/CI 流程，不是新路径」。
    """
    from candidates.test_case import dump_testcase_yaml as dump
    tc = parse_testcase_dict({
        "schema_version": "0.2", "id": "cand_x", "name": "搜索空结果",
        "status": "CANDIDATE", "generated_by": "generator_agent",
        "generation_evidence": _evidence(),
        "steps": [{"action": "launch_app"},
                  {"wait_for": {"target": "screen:HomeView",
                                "condition": "active", "timeout": 10}}]})
    text = dump(tc)
    back = parse_testcase_dict(yaml.safe_load(text))
    assert back.model_dump() == tc.model_dump()
    # 中文不转义（给人看的文本，不是 \uXXXX）
    assert "搜索空结果" in text


# --- 评审修订（review_p3_task34 P2-1 / P2-2） ---------------------------------


def test_save_candidate_refuses_a_missing_fingerprint(store):
    """P2-1：入库的候选**必有指纹**——`None` → fail-loud。

    迁移 002 的 docstring 早就声称有这道闸门，实际当时并不存在（评审实锤）。
    为什么必须炸：`generator.generate` 建去重基准时写的是
    `if cand.get("fingerprint")`，一个 `NULL` 指纹的候选会被**静默跳过** →
    它**永久不参与去重** → 同一条用例可以被反复生成入库，而每一步都
    「看起来正常」。
    """
    with pytest.raises(ValueError, match="fingerprint"):
        store.save_candidate("cand_1", **{**_candidate(), "fingerprint": None})
    with pytest.raises(ValueError, match="fingerprint"):
        store.save_candidate("cand_1", **{**_candidate(), "fingerprint": ""})


def test_dry_run_detail_roundtrips_through_the_row_model(store):
    """P2-2：`dry_run_detail` 落库并可读回（`TestCandidate` 两侧都要带）。"""
    row = store.save_candidate("cand_1", **{
        **_candidate(), "dry_run_status": "FAIL",
        "dry_run_detail": {"failure_type": "WAIT_TIMEOUT", "attempt": 2}})
    assert row["dry_run_detail"] == {"failure_type": "WAIT_TIMEOUT",
                                     "attempt": 2}
    c = TestCandidate.from_store_dict(row)
    assert c.dry_run_detail["failure_type"] == "WAIT_TIMEOUT"
    assert c.to_store_dict()["dry_run_detail"] == c.dry_run_detail
    # 默认空：没跑过 Dry Run 的候选不是「跑失败了」
    assert store.save_candidate("cand_2", **_candidate())[
        "dry_run_detail"] == {}
