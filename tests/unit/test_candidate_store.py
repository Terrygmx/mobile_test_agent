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

from agents.storage import AGENT_MIGRATIONS_DIR, SQLiteAgentStore
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


def test_migrations_are_idempotent(tmp_path):
    db = tmp_path / "agent.db"
    SQLiteAgentStore(db)
    SQLiteAgentStore(db)            # 重放：二次不炸
    from agents.storage import AGENT_SCHEMA_VERSION
    # 版本串从**链尾派生**（`latest_version`），故加脚本后这里跟着变——
    # 这正是它该有的行为（加脚本忘改常量也不会撒谎）。
    # 004（M3 Task 3.5）→ 005（M5 Task 5.1）→ 006（M4 Task 4.5）
    # → 007（M6 Task 6.5 的 benchmark_runs；plan 原写 005，已被占用——偏离登记）
    assert AGENT_SCHEMA_VERSION == "007_benchmark_runs"


def test_002_alone_leaves_the_db_readable(tmp_path):
    """**P3-new 的钉子**：只跑 002（`feff5ec` 落盘的形态）建的库，必须能被
    当前 store 打开。

    第一轮修订把 `dry_run_detail_json` 直接加进**已落盘**的 002 → 对「按旧版
    建过库」的人，002 已在 `schema_migrations` 里记「跑过」，新列永远不会出现，
    而 `_candidate_from_row` 会去取它 → 读 `IndexError` / 写 `OperationalError`
    （库直接不可用）。修法是走 003（只向后追加，`graph/migrations/002` 的先例）。

    ⚠️ **DDL 逐字取自 `feff5ec` 的 002**（`git show feff5ec:...`），**不是**
    读当前的 002——若将来有人又把列塞回 002，读当前脚本会让本测试跟着「变对」
    （它建的库就带上那一列了），从而**测不出**升级路径炸掉。这正是「守卫跟着
    实现漂移」的形态：夹具必须**冻结历史形态**。
    """
    db = tmp_path / "agent.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        # feff5ec 的 002：**没有** dry_run_detail_json
        conn.executescript("""
CREATE TABLE IF NOT EXISTS test_candidates (
    candidate_id TEXT PRIMARY KEY,
    testcase_yaml TEXT NOT NULL,
    generated_by TEXT,
    generation_evidence_json TEXT NOT NULL DEFAULT '{}',
    fingerprint TEXT,
    dry_run_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (dry_run_status IN ('PENDING', 'PASS', 'FAIL')),
    review_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (review_status IN ('PENDING', 'ACCEPT', 'REJECT')),
    reviewer TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS test_candidate_rejections (
    rejection_id TEXT PRIMARY KEY,
    candidate_id TEXT,
    reason TEXT NOT NULL
        CHECK (reason IN ('SCHEMA_INVALID', 'SEMANTIC_REJECTED', 'DUPLICATE')),
    fingerprint TEXT,
    duplicate_of TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
""")
        conn.execute("INSERT INTO schema_migrations (version, applied_at)"
                     " VALUES ('002_test_candidates','t')")
        conn.commit()
    finally:
        conn.close()

    # 当前 store 打开 → 自动补跑 003 → 读写都通
    store = SQLiteAgentStore(db)
    row = store.save_candidate("cand_1", **_candidate())
    assert row["dry_run_detail"] == {}, "003 补的列必须存在且默认空"
    assert store.get_candidate("cand_1")["candidate_id"] == "cand_1"


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
    """生成器的去重需要**未决集**——已决的候选不该再挡住新候选。

    ⚠️ 已决行**只能**经 `decide_candidate` 造（review_p3_task35 P2-2）：
    `save_candidate` 不再收 `review_status` 形参，走它造已决行会是 `TypeError`。
    """
    store.save_candidate("cand_a", **_candidate())
    for cid, status in (("cand_b", "ACCEPT"), ("cand_c", "REJECT")):
        store.save_candidate(cid, **_candidate())
        store.decide_candidate(cid, review_status=status, reviewer="op")
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
    # 行模型的**字段快照**——**全键**断言，不是只看一个键（少一个键 = 下游
    # 读到 None 的静默缺字段）。⚠️ 它**不是** `save_candidate(**...)` 的实参
    # 形状（review_p3_task35 P2-4）：审核三列不在该方法的形参里，见
    # `to_store_dict` 的 docstring；`from_store_dict` 是它的逆。
    assert set(c.to_store_dict()) == {
        "candidate_id", "testcase_yaml", "generated_by",
        "generation_evidence", "fingerprint", "dry_run_status",
        "dry_run_detail", "review_status", "review_note", "reviewer",
        "reviewed_at"}
    assert c.to_store_dict()["candidate_id"] == "cand_1"
    # 逆运算的自洽：行 → dict → 行，除 `created_at`（唯一刻意缺席的字段，
    # 归 DB 拥有）外逐字段相等——快照漏字段会在这里红。
    back = TestCandidate.from_store_dict(c.to_store_dict())
    assert back.model_dump(exclude={"created_at"}) == \
        c.model_dump(exclude={"created_at"})
    r = CandidateRejection.from_store_dict(
        store.save_rejection("rej_1", reason="DUPLICATE", duplicate_of="x"))
    assert r.reason == "DUPLICATE" and r.duplicate_of == "x"


def test_an_empty_review_status_is_not_silently_read_as_pending(store):
    """`review_status` 读侧不许有默认值（round 2 P2-E）。

    `row.get("review_status") or "PENDING"` 会把库里的空串兜成 `PENDING`，
    而 `decide_candidate` 的门槛正是 `review_status != "PENDING"` 就拒绝——
    兜一次等于**开了一条重新决策的旁路**，绕过 note 必填 / 不可二次决策。
    这与 P2-2「`review_status` 的唯一写路径」是同一条纪律的读出侧。

    双闸：空串/词表外的值 → `ValidationError`（字段收进
    `Literal[*REVIEW_STATUSES]`）；缺键 → `KeyError`。

    `dry_run_status` 刻意**仍**用 `or "PENDING"`（它不是审核状态，没有第二道
    门槛依赖它）——两列宽严不同是刻意的，不是漏改。
    """
    from pydantic import ValidationError

    row = store.save_candidate("cand_empty", **_candidate())
    row["review_status"] = ""
    with pytest.raises(ValidationError):
        TestCandidate.from_store_dict(row)
    row["review_status"] = "MAYBE"          # 词表外的值同样不许过
    with pytest.raises(ValidationError):
        TestCandidate.from_store_dict(row)
    # 缺键 fail-loud（该列在 DDL 里 NOT NULL DEFAULT 'PENDING'，
    # 正常行必然有值——缺了只可能是别处写坏了）
    row2 = store.save_candidate("cand_missing", **_candidate())
    del row2["review_status"]
    with pytest.raises(KeyError):
        TestCandidate.from_store_dict(row2)
    # 而 dry_run_status 为空仍读成 PENDING（刻意保留的宽松）
    ok = store.save_candidate("cand_dry", **_candidate())
    ok["dry_run_status"] = ""
    assert TestCandidate.from_store_dict(ok).dry_run_status == "PENDING"


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


# --- Task 3.5（P3-13）：审核状态机的存储面 + AE 第 1 层闸门 --------------------


def test_decide_candidate_stamps_review_fields(store):
    """`decide_candidate` 是 `review_status` 的**唯一写路径**（3.5 前
    `save_candidate` 的 upsert 只写不更新它）——reviewer / reviewed_at /
    review_note（迁移 004）一次落齐。"""
    store.save_candidate("cand_1", **_candidate())
    store.decide_candidate("cand_1", review_status="REJECT",
                           reviewer="terry", note="与既有用例语义重复")
    row = store.get_candidate("cand_1")
    assert row["review_status"] == "REJECT"
    assert row["reviewer"] == "terry"
    assert row["review_note"] == "与既有用例语义重复"
    assert row["reviewed_at"], "reviewed_at 由 store 盖章（_now 单一来源）"
    # 没给 note 的决策 → review_note 为 None（NULL = 无理由，不是空串）
    store.save_candidate("cand_2", **_candidate())
    store.decide_candidate("cand_2", review_status="ACCEPT", reviewer="op")
    assert store.get_candidate("cand_2")["review_note"] is None


def test_save_candidate_refuses_overwriting_a_decided_candidate(store):
    """AE 第 1 层（handover §9 前置 2）：已人工决策的候选**不可被 upsert 覆写**。

    探针 AE 实测的缺陷形态：upsert 的 `ON CONFLICT` 不含 `review_status` →
    生成器重跑同 gap 会改写已 ACCEPT 候选的 `testcase_yaml`，而 `review_status`
    仍是 ACCEPT → 落盘文件与库内容分叉。已决行经生成器写路径**必须 fail-loud**
    （第 2 层——generate 预过滤——在 `test_generator_pipeline.py`）。
    """
    store.save_candidate("cand_1", **_candidate())
    store.decide_candidate("cand_1", review_status="ACCEPT", reviewer="op")
    with pytest.raises(ValueError, match="已人工决策"):
        store.save_candidate("cand_1", **{
            **_candidate(), "testcase_yaml": "schema_version: '0.2'\nid: x\n"})
    # 库内容原封不动（不是「改了一半」）
    assert store.get_candidate("cand_1")["testcase_yaml"] == \
        _candidate()["testcase_yaml"]
    # PENDING 行的 upsert 幂等语义**不变**（闸门只挡已决行）
    store.save_candidate("cand_2", **_candidate())
    again = store.save_candidate("cand_2", **{
        **_candidate(), "dry_run_status": "FAIL"})
    assert again["dry_run_status"] == "FAIL"
