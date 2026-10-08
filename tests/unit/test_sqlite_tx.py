"""review_p3_task11 P2-2：单写者事务原语（`source/sqlite_tx.write_tx`）。

这条纪律此前有**三份手写实现**，`agents/storage.py` 那份把 `_connect()` 与
`BEGIN IMMEDIATE` 放在保护区间**之外**——一次 `database is locked` 后进程锁
永不释放，该 store 此后所有写永久挂死在 `acquire()` 上（审计留痕整段丢失，
且无自愈机制）。异常路径是原实现的零覆盖区，本文件把它钉成可执行断言。
"""
from __future__ import annotations

import sqlite3
import threading

import pytest

from source.sqlite_tx import write_tx


def _conn(db, *, busy_ms: int = 50) -> sqlite3.Connection:
    c = sqlite3.connect(str(db))
    c.execute(f"PRAGMA busy_timeout={busy_ms}")
    return c


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "tx.db"
    c = sqlite3.connect(str(path))
    c.execute("CREATE TABLE t (x INTEGER)")
    c.commit()
    c.close()
    return path


def _count(db) -> int:
    c = sqlite3.connect(str(db))
    try:
        return c.execute("SELECT count(*) FROM t").fetchone()[0]
    finally:
        c.close()


def test_commit_on_success_and_lock_released(db):
    lock = threading.Lock()
    with write_tx(lock, lambda: _conn(db)) as conn:
        conn.execute("INSERT INTO t (x) VALUES (1)")
    assert not lock.locked()
    assert _count(db) == 1


def test_rollback_on_body_exception_and_lock_released(db):
    lock = threading.Lock()
    with pytest.raises(ValueError, match="body failed"):
        with write_tx(lock, lambda: _conn(db)) as conn:
            conn.execute("INSERT INTO t (x) VALUES (1)")
            raise ValueError("body failed")
    assert not lock.locked(), "异常路径必须释放锁"
    assert _count(db) == 0, "异常路径必须回滚"


def test_lock_released_when_connect_raises(db):
    """`_connect()` 抛异常时也必须释放锁（原实现在 `try` 之外）。"""
    lock = threading.Lock()

    def boom():
        raise RuntimeError("connect failed")

    with pytest.raises(RuntimeError, match="connect failed"):
        with write_tx(lock, boom):
            pass                            # pragma: no cover — 进不来
    assert not lock.locked()


def test_lock_released_when_begin_immediate_fails(db):
    """`BEGIN IMMEDIATE` 失败（锁冲突）时也必须释放锁。

    这是评审 A/B 探针的场景：另一条连接持 `BEGIN EXCLUSIVE`。
    """
    lock = threading.Lock()
    blocker = sqlite3.connect(str(db), isolation_level=None)
    try:
        blocker.execute("BEGIN EXCLUSIVE")
        with pytest.raises(sqlite3.OperationalError):
            with write_tx(lock, lambda: _conn(db)):
                pass                        # pragma: no cover — 进不来
        assert not lock.locked()
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    # 冲突解除后必须还能正常写（锁没被吃掉）
    with write_tx(lock, lambda: _conn(db)) as conn:
        conn.execute("INSERT INTO t (x) VALUES (7)")
    assert _count(db) == 1


def test_write_tx_serializes_concurrent_writers(db):
    """两个线程各写 50 次 → 100 个互不相同的值（进程锁真的在串行化）。"""
    lock = threading.Lock()

    def worker(n: int) -> None:
        for i in range(50):
            with write_tx(lock, lambda: _conn(db)) as conn:
                conn.execute("INSERT INTO t (x) VALUES (?)", (n * 100 + i,))

    threads = [threading.Thread(target=worker, args=(n,)) for n in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    c = sqlite3.connect(str(db))
    try:
        rows = [r[0] for r in c.execute("SELECT x FROM t")]
    finally:
        c.close()
    assert len(rows) == 100 and len(set(rows)) == 100, "既不丢也不重"
