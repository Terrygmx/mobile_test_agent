"""Task 2.3 / P2-05：review accept 触发 create_candidate（E5 / 设计 8.1）。

口径（写死在本文件，测试按此钉）：

1. **ACCEPT 是 Candidate 的唯一入口**（E5 / 8.1）：`mta review accept` 在
   改状态之后立刻 `create_candidate`——不靠后台扫描判断（设计 8.1 明文
   「建议在命令里直接触发」）。
2. **REJECT 不经过任何代码路径入库**（矩阵 #10）：REJECT 分支不得在
   experience 库留下任何行。
3. **种子字段缺一即 fail-loud，且先校验后改状态**：追溯链断裂（step 关联
   不到）时 `ReviewError`，且 review **不得**落 ACCEPT——否则 review 卡在
   ACCEPT 却永远建不出 Candidate，二次 accept 又被「不可二次决策」挡住。
4. **重复触发幂等**：同一 review 二次 seeding 不产生第二行（按
   `seed_recovery_review_id` 判定），返回既有 Experience。
5. **trace 事件 `candidate_created`**（设计 11.1）：追加到 P1 Trace
   （infra_events，该库唯一的事件流），带 seed_run_id + experience_id。
6. **P1 行为保留**：不传 experience_store 时 `decide_review` 与 P1 完全
   一致（只改状态、不碰 experience 库）。
7. **评审修订（review_p2_task23）**：ACCEPT 两个消费入口的前置条件合一校验
   且先于状态变更（P3-2）；幂等按种子 review 直查并显式排除 REJECTED
   （P3-3）；`mta review reseed` 补种半状态（P3-1）；失败路径不留空库文件
   （P3-6 惰性构造）。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from agent.review import ReviewError, decide_review, seed_candidate
from cli.main import main
from experience import ExperienceStatus, SQLiteExperienceStore
from tracer.storage import TraceStore


@pytest.fixture()
def chain(tmp_path):
    """最小完整追溯链 + 一条悬空链（E5 的两个方向都要覆盖）。

    布局（对齐 Task 1.1 审计的真实形态）：
      run_e049fb5e → tc1 → step1(username_field, ELEMENT_NOT_FOUND)
        → rec1(LLM, candidate=username_field_v2) → review 1 PENDING
      rec2(step_id=999 悬空) → review 2 PENDING（种子字段不齐的负例）
    """
    trace = TraceStore(tmp_path / "trace.db")
    conn = trace.conn
    conn.execute(
        "INSERT INTO runs (run_id, trace_schema_version, app_bundle_id,"
        " app_build, start_time) VALUES (?,?,?,?,?)",
        ("run_e049fb5e", "0.1", "com.phaset0.logindemo", "1025",
         "2026-10-03T00:00:00Z"))
    conn.execute(
        "INSERT INTO testcase_runs (run_id, testcase_id, status)"
        " VALUES (?,?,?)", ("run_e049fb5e", "tc_login", "RECOVERED"))
    conn.execute(
        "INSERT INTO steps (testcase_run_id, step_index, step_type, target_id,"
        " status, failure_type, failure_phase) VALUES (?,?,?,?,?,?,?)",
        (1, 0, "input", "username_field", "RECOVERED", "ELEMENT_NOT_FOUND",
         "PRE_DISPATCH"))
    conn.executemany(
        "INSERT INTO recoveries (step_id, kind, expected_target,"
        " candidate_target, candidate_type, screen, app_build, result,"
        " accepted) VALUES (?,?,?,?,?,?,?,?,?)",
        [(1, "LLM", "username_field", "username_field_v2", "textfield",
          "LoginView", "1025", "RECOVERED", 1),
         # 悬空：step_id=999 关联不到 steps（P0 遗留写入形态）
         (999, "LLM", "go_search", "go_search_v2", "button", "HomeView",
          "1025", "RECOVERED", 1)])
    conn.executemany(
        "INSERT INTO recovery_reviews (recovery_id, review_status, reviewer)"
        " VALUES (?,?,?)", [(1, "PENDING", None), (2, "PENDING", None)])
    conn.commit()
    exp_db = tmp_path / "experience.db"
    return {
        "trace": trace, "trace_db": tmp_path / "trace.db",
        "exp": SQLiteExperienceStore(exp_db), "exp_db": exp_db,
    }


def _infra_events(exp_ctx, event_type: str) -> list[dict]:
    conn = exp_ctx["trace"].conn
    rows = conn.execute(
        "SELECT run_id, testcase_run_id, event_type, detail_json"
        " FROM infra_events WHERE event_type=?", (event_type,)).fetchall()
    return [dict(r) for r in rows]


# --- 1. ACCEPT → CANDIDATE ---


def test_accept_creates_candidate(chain):
    decide_review(chain["trace"], 1, "ACCEPT", "terry",
                  experience_store=chain["exp"])
    rows = chain["exp"].list()
    assert len(rows) == 1
    e = rows[0]
    assert e.status is ExperienceStatus.CANDIDATE
    assert (e.app_id, e.screen_id, e.target_id) == (
        "com.phaset0.logindemo", "LoginView", "username_field")
    # E5 三件套（可追溯证据）
    assert e.seed_run_id == "run_e049fb5e"
    assert e.seed_step_id == 1
    assert e.seed_recovery_review_id == 1
    assert e.origin == "LLM_ACCEPTED_RECOVERY"
    # 策略来自被接受的恢复候选（candidate_target）
    assert e.strategy.value == "username_field_v2"
    assert e.strategy.type == "accessibility_id"
    assert e.strategy.origin == "experience"
    # 新建 Candidate 尚无「实际被尝试」样本（E11 口径）
    assert (e.sample_count, e.success_count, e.failure_count) == (0, 0, 0)
    assert e.validated_builds == []
    assert e.promoted is False


def test_seeded_state_event_written(chain):
    """create_candidate 的状态时间线首行（reason=SEEDED）——Task 2.1 契约。"""
    decide_review(chain["trace"], 1, "ACCEPT", "terry",
                  experience_store=chain["exp"])
    conn = sqlite3.connect(chain["exp_db"])
    try:
        rows = conn.execute(
            "SELECT from_status, to_status, reason, run_id"
            " FROM experience_state_events").fetchall()
    finally:
        conn.close()
    assert rows == [(None, "CANDIDATE", "SEEDED", "run_e049fb5e")]


# --- 2. REJECT 不入库（矩阵 #10） ---


def test_reject_creates_no_candidate(chain):
    decide_review(chain["trace"], 1, "REJECT", "terry", "LLM 找错元素",
                  experience_store=chain["exp"])
    assert chain["exp"].list() == []
    assert _infra_events(chain, "candidate_created") == []


# --- 3. 缺字段 fail-loud，且先校验后改状态 ---


def test_dangling_chain_fails_loud_before_decision(chain):
    with pytest.raises(ReviewError) as exc:
        decide_review(chain["trace"], 2, "ACCEPT", "terry",
                      experience_store=chain["exp"])
    msg = str(exc.value)
    assert "seed" in msg.lower()
    # 状态未被改动——否则 review 卡在 ACCEPT 且永远建不出 Candidate
    assert chain["trace"].get_review(2)["review_status"] == "PENDING"
    assert chain["exp"].list() == []


# --- 4. 幂等 ---


def test_repeat_seed_is_idempotent(chain):
    decide_review(chain["trace"], 1, "ACCEPT", "terry",
                  experience_store=chain["exp"])
    first = chain["exp"].list()[0]
    again = seed_candidate(chain["trace"], chain["exp"], 1)
    assert again.experience_id == first.experience_id
    assert len(chain["exp"].list()) == 1
    assert len(_infra_events(chain, "candidate_created")) == 1


def test_seed_requires_accept_status(chain):
    """E5：种子只能来自 ACCEPT——PENDING 直接调用 seeding 必须拒绝。"""
    with pytest.raises(ReviewError):
        seed_candidate(chain["trace"], chain["exp"], 1)
    assert chain["exp"].list() == []


# --- 5. trace 事件 candidate_created（设计 11.1） ---


def test_candidate_created_trace_event(chain):
    decide_review(chain["trace"], 1, "ACCEPT", "terry",
                  experience_store=chain["exp"])
    events = _infra_events(chain, "candidate_created")
    assert len(events) == 1
    ev = events[0]
    assert ev["run_id"] == "run_e049fb5e"
    assert ev["testcase_run_id"] == 1
    detail = json.loads(ev["detail_json"])
    assert detail["experience_id"] == chain["exp"].list()[0].experience_id
    assert detail["screen_id"] == "LoginView"
    assert detail["target_id"] == "username_field"
    assert detail["status"] == "CANDIDATE"


# --- 6. P1 行为保留 ---


def test_decide_without_experience_store_keeps_p1_behavior(chain):
    decide_review(chain["trace"], 1, "ACCEPT", "terry")
    assert chain["trace"].get_review(1)["review_status"] == "ACCEPT"
    assert chain["exp"].list() == []


# --- 7. CLI 装配（--exp-db） ---


def test_cli_accept_seeds_candidate(chain, capsys):
    code = main(["review", "accept", "1", "--db", str(chain["trace_db"]),
                 "--exp-db", str(chain["exp_db"]), "--reviewer", "terry"])
    assert code == 0
    out = capsys.readouterr().out
    assert "CANDIDATE" in out
    # 复用同一个库文件（CLI 与测试看到的是同一份 experience 库）
    store = SQLiteExperienceStore(chain["exp_db"])
    rows = store.list()
    assert len(rows) == 1
    assert rows[0].seed_recovery_review_id == 1
    assert chain["trace"].get_review(1)["review_status"] == "ACCEPT"


def test_cli_accept_dangling_chain_exit3(chain, capsys):
    """种子字段不全 → CLI 退出码 3（fail-loud，不静默跳过）。"""
    code = main(["review", "accept", "2", "--db", str(chain["trace_db"]),
                 "--exp-db", str(chain["exp_db"]), "--reviewer", "terry"])
    assert code == 3
    assert "REVIEW ERROR" in capsys.readouterr().out
    assert chain["trace"].get_review(2)["review_status"] == "PENDING"


# --- 8. 评审修订（review_p2_task23）：P3-1 / P3-2 / P3-3 / P3-6 ---


def test_accept_requires_patch_fields_too(chain):
    """P3-2：ACCEPT 的**两个消费入口**前置条件合一校验，且发生在状态变更前。

    曾各自为政：seed 必填集不含 candidate_type，export 必填集含——于是
    candidate_type 缺失时 seed 放行（状态落 ACCEPT、Candidate 入库），紧接着
    export 抛错 → 补丁再也导不出来。现在一次跑完、失败零副作用。
    """
    chain["trace"].conn.execute(
        "UPDATE recoveries SET candidate_type=NULL WHERE id=1")
    chain["trace"].conn.commit()
    with pytest.raises(ReviewError) as exc:
        decide_review(chain["trace"], 1, "ACCEPT", "terry",
                      experience_store=chain["exp"])
    assert "candidate_type" in str(exc.value)
    assert chain["trace"].get_review(1)["review_status"] == "PENDING"
    assert chain["exp"].list() == []


def test_seed_idempotency_excludes_rejected(chain):
    """P3-3：幂等按种子 review **直查**并显式排除 REJECTED。

    判据不再依赖 `lookup` 的过滤（那是「恰好还能读见」的涌现属性）：
    REJECTED 不算已种——人工判过不可用的策略再 ACCEPT 一次就是重新学习。
    """
    decide_review(chain["trace"], 1, "ACCEPT", "terry",
                  experience_store=chain["exp"])
    first = chain["exp"].list()[0]
    again = seed_candidate(chain["trace"], chain["exp"], 1)
    assert again.experience_id == first.experience_id
    assert len(chain["exp"].list()) == 1

    chain["exp"].update_status(first.experience_id,
                               ExperienceStatus.REJECTED, "MANUAL")
    # REJECTED 不入 lookup（设计 7.1 修订），但 list() 审计视角全量可见
    assert chain["exp"].lookup("com.phaset0.logindemo", "LoginView",
                               "username_field") == []
    assert [e.status for e in chain["exp"].list()] == [
        ExperienceStatus.REJECTED]
    second = seed_candidate(chain["trace"], chain["exp"], 1)
    assert second.experience_id != first.experience_id
    assert len(chain["exp"].find_by_seed_review(1)) == 2
    assert sorted(e.status.value for e in chain["exp"].list()) == [
        "CANDIDATE", "REJECTED"]


def test_cli_reseed_completes_half_state(chain, capsys):
    """P3-1：`create_candidate` 曾失败/未触发留下的半状态有产品出口。

    场景即现存真实数据：review 在 Task 2.3 之前已 ACCEPT（无 Candidate）。
    """
    decide_review(chain["trace"], 1, "ACCEPT", "terry")     # P1 路径，不建
    assert chain["exp"].list() == []

    code = main(["review", "reseed", "1", "--db", str(chain["trace_db"]),
                 "--exp-db", str(chain["exp_db"])])
    assert code == 0
    rows = SQLiteExperienceStore(chain["exp_db"]).list()
    assert len(rows) == 1 and rows[0].seed_recovery_review_id == 1

    # 幂等：再补一次不新增
    assert main(["review", "reseed", "1", "--db", str(chain["trace_db"]),
                 "--exp-db", str(chain["exp_db"])]) == 0
    assert len(SQLiteExperienceStore(chain["exp_db"]).list()) == 1


def test_reseed_requires_accept(chain, capsys):
    """reseed 不是绕过 E5 的后门：PENDING 的 review 补不了种。"""
    code = main(["review", "reseed", "1", "--db", str(chain["trace_db"]),
                 "--exp-db", str(chain["exp_db"])])
    assert code == 3
    assert chain["exp"].list() == []


def test_failed_accept_leaves_no_experience_db(chain, tmp_path, capsys):
    """P3-6：校验失败的 accept 不得在磁盘上留下空库文件（惰性构造）。

    旧实现在 `decide_review` 之前就构造 store（触发 migrate 建库），失败
    路径也产生新文件——「失败却产生新文件」违反最小副作用。
    """
    fresh = tmp_path / "never_created.db"
    code = main(["review", "accept", "2", "--db", str(chain["trace_db"]),
                 "--exp-db", str(fresh), "--reviewer", "terry"])
    assert code == 3
    assert not fresh.exists()


def test_accept_creates_experience_db_when_valid(chain, tmp_path):
    """反向：校验通过时惰性构造照常落库（惰性不等于不建）。"""
    fresh = tmp_path / "created.db"
    code = main(["review", "accept", "1", "--db", str(chain["trace_db"]),
                 "--exp-db", str(fresh), "--reviewer", "terry"])
    assert code == 0
    assert fresh.exists()
    assert len(SQLiteExperienceStore(fresh).list()) == 1
