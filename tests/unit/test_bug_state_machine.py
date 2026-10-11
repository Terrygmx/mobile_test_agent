"""Task 5.6 / P3-25：Bug Candidate 状态机 + 统一审核入口接 `kind="bug"`
（设计 §8.5 / F6 / F13；迁移 005）。

三块，各钉一段：

1. **转换表是纯函数**（F13）：`advance` / `can_transition` / `is_terminal` 离库
   可测；词表与 `diagnosis.models.BUG_STATUSES` 逐值一致（加状态忘改转换表会红）；
2. **非法推进 fail-loud**：终态不可再推进、未知状态、越级跳全部抛
   `BugStateError`（静默拒绝会让调用方以为「已经推进了」）；
3. **统一入口**：`decide_candidate(kind="bug")` 只在 `HUMAN_REVIEW` 上决策，
   ACCEPT→`CONFIRMED` / REJECT→`REJECTED`，且 ACCEPT 落 Evidence Bundle JSON。
"""
from __future__ import annotations

import json

import pytest

from agents.storage import SQLiteAgentStore
from candidates.bug import (BUG_TERMINAL_STATUSES, BUG_TRANSITIONS,
                            BugStateError, advance, can_transition, is_terminal)
from candidates.review import (CandidateReviewError, decide_candidate,
                               export_candidate_patch, export_evidence_bundle)
from diagnosis.models import BUG_STATUSES, EvidenceItem


@pytest.fixture()
def store(tmp_path) -> SQLiteAgentStore:
    return SQLiteAgentStore(tmp_path / "agent.db")


def _save_bug(store, cid="bug_1", *, status=None):
    """落一条 bug 候选；`status` 给了就用原始 SQL 推进（绕过状态机——
    单测要能构造任意起点，正式路径只能经 `decide_bug_candidate`）。"""
    store.save_bug_candidate(
        cid, title="支付页崩溃",
        evidence=[EvidenceItem(kind="crash_detected", detail="进程消失",
                               source="trace:run_1/step_1").model_dump(mode="json")],
        failure_type="APP_CRASH", evidence_score=8,
        affected_tests=["pay_001"])
    if status is not None:
        conn = store._connect()
        try:
            conn.execute("UPDATE bug_candidates SET status=? WHERE"
                         " candidate_id=?", (status, cid))
            conn.commit()
        finally:
            conn.close()
    return store.get_bug_candidate(cid)


# --- 1. 转换表（F13 纯函数） ----------------------------------------------------


def test_transition_table_covers_exactly_the_status_vocabulary():
    """转换表的键集必须 == `BUG_STATUSES`（加状态忘改表会红）。"""
    assert set(BUG_TRANSITIONS) == set(BUG_STATUSES), \
        "转换表的键集与 BUG_STATUSES 漂移了（加状态要同时加表项）"


def test_terminal_statuses_are_the_three_decided_ones():
    assert set(BUG_TERMINAL_STATUSES) == {"CONFIRMED", "REJECTED", "DUPLICATE"}
    for s in BUG_TERMINAL_STATUSES:
        assert BUG_TRANSITIONS[s] == (), f"{s} 是终态，不该有出边"
        assert is_terminal(s)


def test_the_designed_path_advances_step_by_step():
    """设计 §8.5：`DISCOVERED → ANALYZING → CANDIDATE → HUMAN_REVIEW`。"""
    path = ["DISCOVERED", "ANALYZING", "CANDIDATE", "HUMAN_REVIEW"]
    for a, b in zip(path, path[1:]):
        assert can_transition(a, b)
        assert advance(a, b) == b
    for target in ("CONFIRMED", "REJECTED", "DUPLICATE"):
        assert advance("HUMAN_REVIEW", target) == target


def test_illegal_transitions_fail_loud():
    """越级 / 回退 / 终态再推进 / 未知状态 —— 全部 fail-loud。"""
    for frm, to in [("DISCOVERED", "CONFIRMED"),      # 越级
                    ("ANALYZING", "DISCOVERED"),       # 回退
                    ("CONFIRMED", "REJECTED"),         # 终态再推进
                    ("REJECTED", "CONFIRMED"),
                    ("DUPLICATE", "HUMAN_REVIEW"),
                    ("DISCOVERED", "DISCOVERED")]:     # 自环（不在表里）
        assert not can_transition(frm, to), (frm, to)
        with pytest.raises(BugStateError):
            advance(frm, to)
    with pytest.raises(BugStateError):
        advance("NOPE", "CONFIRMED")
    # 终态的报错要带「终态不可再推进」的提示（可读性）
    with pytest.raises(BugStateError, match="终态"):
        advance("CONFIRMED", "REJECTED")


# --- 2. 状态落库（唯一写路径） --------------------------------------------------


def test_decide_bug_candidate_writes_status_and_review_columns(store):
    _save_bug(store, "b1", status="HUMAN_REVIEW")
    store.decide_bug_candidate("b1", status="CONFIRMED", reviewer="alice",
                               decision="ACCEPT")
    row = store.get_bug_candidate("b1")
    assert row["status"] == "CONFIRMED"
    assert row["reviewer"] == "alice"
    assert row["review_decision"] == "ACCEPT"
    assert row["reviewed_at"] is not None


def test_system_advance_does_not_stamp_review_columns(store):
    """系统推进（ANALYZING/CANDIDATE，无 `decision`）不写审核三列。

    把系统推进也盖上 `reviewed_at` 会让「人判的」与「系统走的」在库里无法
    区分（F10 要防的正是这个）。
    """
    _save_bug(store, "b2")
    store.decide_bug_candidate("b2", status="ANALYZING")
    row = store.get_bug_candidate("b2")
    assert row["status"] == "ANALYZING"
    assert row["reviewer"] is None and row["review_decision"] is None
    assert row["reviewed_at"] is None


def test_decide_bug_candidate_on_missing_row_fails_loud(store):
    with pytest.raises(ValueError):
        store.decide_bug_candidate("nope", status="ANALYZING")


# --- 3. 统一审核入口（kind="bug"） ---------------------------------------------


def test_bug_is_a_supported_kind(store):
    _save_bug(store, "b3", status="HUMAN_REVIEW")
    row, sha = decide_candidate(store, "bug", "b3", "ACCEPT", "alice")
    assert row["status"] == "CONFIRMED"
    assert sha is None, "bug 的 ACCEPT 不碰 git"


def test_bug_reject_needs_a_note(store):
    _save_bug(store, "b4", status="HUMAN_REVIEW")
    with pytest.raises(CandidateReviewError):
        decide_candidate(store, "bug", "b4", "REJECT", "alice", None)


def test_bug_reject_sets_rejected(store):
    _save_bug(store, "b5", status="HUMAN_REVIEW")
    row, _ = decide_candidate(store, "bug", "b5", "REJECT", "alice",
                              note="环境抖动，非 App 缺陷")
    assert row["status"] == "REJECTED"
    assert row["review_decision"] == "REJECT"


def test_bug_can_only_be_decided_at_human_review(store):
    """`DISCOVERED` 上直接 ACCEPT 必须拒——那会跳过诊断与证据装配。"""
    _save_bug(store, "b6")            # status = DISCOVERED
    with pytest.raises(CandidateReviewError, match="HUMAN_REVIEW"):
        decide_candidate(store, "bug", "b6", "ACCEPT", "alice")
    assert store.get_bug_candidate("b6")["status"] == "DISCOVERED", "状态不得被改"


def test_unknown_bug_candidate_fails_loud(store):
    with pytest.raises(CandidateReviewError):
        decide_candidate(store, "bug", "ghost", "ACCEPT", "alice")


def test_accept_writes_an_evidence_bundle(store, tmp_path):
    _save_bug(store, "b7", status="HUMAN_REVIEW")
    out = tmp_path / "out"
    row, _ = decide_candidate(store, "bug", "b7", "ACCEPT", "alice",
                              bundle_dir=out)
    assert row["status"] == "CONFIRMED"
    bundle = json.loads((out / "b7.json").read_text(encoding="utf-8"))
    assert bundle["kind"] == "bug" and bundle["bundle_version"] == 1
    assert bundle["candidate_id"] == "b7"
    assert bundle["status"] == "CONFIRMED"
    assert bundle["reviewer"] == "alice"
    assert bundle["evidence_score"] == 8
    assert bundle["evidence"][0]["kind"] == "crash_detected"
    assert bundle["affected_tests"] == ["pay_001"]


def test_reject_writes_no_bundle(store, tmp_path):
    _save_bug(store, "b8", status="HUMAN_REVIEW")
    out = tmp_path / "out"
    decide_candidate(store, "bug", "b8", "REJECT", "alice", note="非缺陷",
                     bundle_dir=out)
    assert not out.exists() or not list(out.iterdir())


def test_export_evidence_bundle_is_self_contained(store):
    _save_bug(store, "b9", status="HUMAN_REVIEW")
    decide_candidate(store, "bug", "b9", "ACCEPT", "alice")
    bundle = export_evidence_bundle(store.get_bug_candidate("b9"))
    for key in ("bundle_version", "candidate_id", "title", "severity", "status",
                "evidence", "evidence_score", "reproducible",
                "reproduction_count", "affected_tests", "reviewer",
                "review_decision", "reviewed_at"):
        assert key in bundle, key


def test_export_patch_is_rejected_for_bug(store):
    """bug 候选没有「可落盘用例 YAML」——`--export-patch` 要清晰报错。"""
    _save_bug(store, "b10")
    with pytest.raises(CandidateReviewError):
        export_candidate_patch(store, "bug", "b10")
