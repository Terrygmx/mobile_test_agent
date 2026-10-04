"""Task 1.2 / P2-02：Experience schema 迁移测试（plan step 2）。

覆盖：幂等（重复 migrate 无副作用）、四表 + 索引存在性、独立库文件
（out/experience.db 与 trace.db 分离）、CHECK 约束兜底、旧代码开新库
fail-loud、graph 三表不在本链（plan 关键决策第 2 条）。
"""
from __future__ import annotations

import sqlite3

import pytest

from experience.schema_migrations import (
    EXPERIENCE_SCHEMA_VERSION,
    current_version,
    migrate,
)


@pytest.fixture()
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "experience.db")
    yield c
    c.close()


EXP_TABLES = {"experiences", "experience_runs", "experience_state_events",
              "promotion_proposals"}
GRAPH_TABLES = {"screen_nodes", "screen_transitions", "graph_diffs"}


def test_migrate_creates_four_tables_and_index(conn):
    version = migrate(conn)
    assert version == EXPERIENCE_SCHEMA_VERSION
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXP_TABLES <= tables
    idx = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_experiences_lookup" in idx


def test_migrate_is_idempotent(conn):
    migrate(conn)
    conn.execute(
        "INSERT INTO experiences (experience_id, app_id, screen_id,"
        " target_id, strategy_json, origin, status, seed_run_id,"
        " seed_step_id, seed_recovery_review_id)"
        " VALUES ('e1','app','LoginView','t','{}','LLM_ACCEPTED_RECOVERY',"
        " 'CANDIDATE','r1',1,7)")
    conn.commit()
    # 重复 migrate：无副作用（数据保留、版本不变、不报错）
    assert migrate(conn) == EXPERIENCE_SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM experiences").fetchone()[0] == 1
    # schema_migrations 无重复行
    n = conn.execute(
        "SELECT COUNT(*) FROM schema_migrations WHERE version LIKE '002%'"
    ).fetchone()[0]
    assert n == 1


def test_migrate_records_version_chain(conn):
    migrate(conn)
    rows = [r[0] for r in conn.execute(
        "SELECT version FROM schema_migrations ORDER BY rowid")]
    # 版本链记**脚本名**（幂等判定的基准是「哪个脚本跑过」）；
    # current_version 返回链尾。汇总常量从链尾**派生**（P3-3：加
    # 003_*.sql 忘 bump 常量也不会撒谎）。
    assert rows == ["002_experience_schema"]
    assert current_version(conn) == "002_experience_schema"
    assert EXPERIENCE_SCHEMA_VERSION == "002_experience_schema"


def test_current_version_empty_db(conn):
    assert current_version(conn) is None


def test_check_constraints_guard_bad_status(conn):
    """状态四值 / origin 单值 / result 两值——DB 层兜底（4.3/4.7）。"""
    migrate(conn)
    base = ("INSERT INTO experiences (experience_id, app_id, screen_id,"
            " target_id, strategy_json, origin, status, seed_run_id,"
            " seed_step_id, seed_recovery_review_id)"
            " VALUES ('e','a','s','t','{}',?,'CANDIDATE','r',1,7)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(base, ("SOME_OTHER_ORIGIN",))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO experiences (experience_id, app_id,"
                     " screen_id, target_id, strategy_json, origin, status,"
                     " seed_run_id, seed_step_id, seed_recovery_review_id)"
                     " VALUES ('e2','a','s','t','{}',"
                     "'LLM_ACCEPTED_RECOVERY','PENDING','r',1,7)")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO experience_runs (experience_id, run_id, step_id,"
            " app_build, result) VALUES ('e','r',1,'1','GUARD_MISS')")
    conn.commit()


def test_graph_tables_not_in_this_chain(conn):
    """plan Task 1.2 关键决策：graph 三表拆 M5、schema 归 graph 侧——
    experience 002 链不得提前冻结它们。"""
    migrate(conn)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert not (GRAPH_TABLES & tables)


def test_garbage_version_row_fails_loud(conn):
    """P3-5：schema_migrations 里手工插的垃圾行 → 幂等判定静默失真，
    必须 fail-loud 而不是跳过。"""
    migrate(conn)
    conn.execute("INSERT INTO schema_migrations (version, applied_at)"
                 " VALUES ('garbage-no-seq', '2026-10-04T00:00:00Z')")
    conn.commit()
    with pytest.raises(RuntimeError, match="无法解析的版本行"):
        migrate(conn)


def test_older_build_opening_newer_db_fails_loud(tmp_path):
    """迁移链只向后追加：应用脚本数 > 本 build 已知数 → 拒绝续写。"""
    db = tmp_path / "exp.db"
    c1 = sqlite3.connect(db)
    migrate(c1)
    # 模拟「未来版本」先写进链
    c1.execute("INSERT INTO schema_migrations (version, applied_at)"
               " VALUES ('003_future', '2026-10-04T00:00:00Z')")
    c1.commit()
    c1.close()
    c2 = sqlite3.connect(db)
    with pytest.raises(RuntimeError, match="newer than this build"):
        migrate(c2)
    c2.close()
