"""review_p3_task11 P2-2：单写者事务原语（`source/sqlite_tx.write_tx`）。

这条纪律此前有**三份手写实现**，`agents/storage.py` 那份把 `_connect()` 与
`BEGIN IMMEDIATE` 放在保护区间**之外**——一次 `database is locked` 后进程锁
永不释放，该 store 此后所有写永久挂死在 `acquire()` 上（审计留痕整段丢失，
且无自愈机制）。异常路径是原实现的零覆盖区，本文件把它钉成可执行断言。
"""
from __future__ import annotations

import ast
import sqlite3
import threading
from pathlib import Path

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


def test_lock_released_when_close_raises():
    """`close()` 抛异常时也必须释放锁（review_p3_task11_final P3-1）。

    原 `finally` 是「先 `close()` 再 `release()`」且**无保护**——`close()` 一抛，
    锁永不释放（与 P2-2 的「`_connect()` 在保护区间外」是同一失败模式，只差一
    层），而本模块的全部存在意义就是这条不变量。

    ⚠️ 真 sqlite3 走 `sqlite3_close_v2`（延迟关闭、返回 `SQLITE_OK`）**不会抛**
    ——所以只能用替身复现（评审也标注了这条边界）。
    """
    lock = threading.Lock()

    class _BadConn:
        """最小替身：只让 `close()` 抛，其余是 no-op。"""

        def execute(self, *a, **k):        # noqa: D102
            return None

        def commit(self) -> None:          # noqa: D102
            pass

        def rollback(self) -> None:        # noqa: D102
            pass

        def close(self) -> None:           # noqa: D102
            raise sqlite3.OperationalError("close failed")

    with pytest.raises(sqlite3.OperationalError, match="close failed"):
        with write_tx(lock, _BadConn):
            pass
    assert not lock.locked(), "close() 抛异常也必须释放锁"


def test_lock_released_when_body_and_close_both_fail(db):
    """体异常 + `close()` 也抛 → 锁仍必须释放，且事务**已回滚**。

    （`finally` 的语义是 `close()` 的异常会取代正在传播的原异常——本测试只钉
    「锁不泄漏 + 事务不半提交」这两条不变量。真 sqlite3 的 `close()` 不抛，见
    模块 docstring 的诚实边界。）
    """
    lock = threading.Lock()
    real = _conn(db)

    class _BadClose:
        """真连接 + `close()` 抛（事务部分是真 sqlite3）。"""

        def execute(self, *a, **k):        # noqa: D102
            return real.execute(*a, **k)

        def commit(self) -> None:          # noqa: D102
            real.commit()

        def rollback(self) -> None:        # noqa: D102
            real.rollback()

        def close(self) -> None:           # noqa: D102
            raise sqlite3.OperationalError("close failed")

    with pytest.raises(sqlite3.OperationalError, match="close failed"):
        with write_tx(lock, _BadClose) as conn:
            conn.execute("INSERT INTO t (x) VALUES (1)")
            raise ValueError("body failed")
    assert not lock.locked(), "两层清理都失败时锁仍必须释放"
    assert _count(db) == 0, "体异常必须回滚（close 的失败不影响已回滚的事实）"


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


# --- review_p3_task11_final P3-2：三处 store 的连接参数必须一致 ---------------


def test_busy_timeout_is_consistent_across_the_three_stores(tmp_path):
    """三处 store 的 `busy_timeout` 都是 5000ms（行为面）。

    review_p3_task11_final P3-2：`agents/storage.py::_connect` 抄了 experience
    **已修掉**的 `timeout=30` + `PRAGMA busy_timeout=5000`（PRAGMA 后设者胜 →
    `timeout=30` 是**死字面量**，读者会以为等 30s）；`graph/storage.py` 又是
    第三种形态（不传 timeout、不设 PRAGMA）。功能等价，但「哪个才是权威值」
    不能交给阅读顺序。
    """
    from agents.storage import SQLiteAgentStore
    from experience.store import SQLiteExperienceStore
    from graph.storage import GraphStore

    stores = {"experience": SQLiteExperienceStore(tmp_path / "experience.db"),
              "agents": SQLiteAgentStore(tmp_path / "agent.db"),
              "graph": GraphStore(tmp_path / "graph.db")}
    for name, store in stores.items():
        conn = store._connect()
        try:
            value = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        finally:
            conn.close()
        assert value == 5000, f"{name} 的 busy_timeout 是 {value}，不是 5000"


def test_store_connects_use_the_same_timeout():
    """源码面（AST）：三处 store 的 `sqlite3.connect(timeout=…)` 必须一致。

    `PRAGMA busy_timeout` 后设者胜，`timeout=` 只有在**没有** PRAGMA 时才生效
    ——两者写不同值时前者是**死字面量**（读者会以为等 30s）。这个模式在
    `experience/store.py` 被判过一次 P3（注释原文还记着），P3-01 又抄了一遍。

    只认真正的 `timeout=` 实参（注释里提到这个数字不算——评审探针指出
    「`timeout=30` 是死字面量」，但注释里为解释它而提到该数字是合法的）。
    """
    for rel in ("experience/store.py", "agents/storage.py", "graph/storage.py"):
        tree = ast.parse(Path(rel).read_text(encoding="utf-8"), filename=rel)
        connects = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "connect"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "sqlite3"]
        assert len(connects) == 1, f"{rel} 的 sqlite3.connect 调用数不是 1"
        kw = {k.arg: k.value for k in connects[0].keywords}
        assert "timeout" in kw, \
            f"{rel} 的 connect 又没写 timeout（「第三种形态」回来了）"
        assert ast.literal_eval(kw["timeout"]) == 5, \
            f"{rel} 的 connect timeout 是 {ast.literal_eval(kw['timeout'])}"\
            f"，与 PRAGMA busy_timeout=5000 打架（后设者胜 → 前者是死字面量）"
