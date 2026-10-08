"""sqlite_tx.py — SQLite 单写者事务的**唯一实现**（E9；review_p3_task11 P2-2）。

三个库（`experience.db` / `agent.db` / `graph.db`）按「写者边界」分家，但
**写纪律是同一条**：进程锁串行化 + `BEGIN IMMEDIATE`（跨进程忙等）+
「任何早退路径都不得泄漏事务或锁」。这条纪律此前有三份手写实现：

| 位置 | 形态 | 缺陷 |
|---|---|---|
| `experience/store.py` | `@contextmanager` | `_connect()` 在 `try` 之外（锁泄漏的窄口） |
| `agents/storage.py` | 手写 `_Tx` 类 | **`_connect()` 与 `BEGIN IMMEDIATE` 都在 `try` 之外** → 一次 `database is locked` 后进程锁永不释放，该 store 此后所有写**永久挂死** |
| `graph/storage.py` | `with conn:` | 无进程锁、无 `BEGIN IMMEDIATE` |

同一概念三份实现必然漂移——第三份就是漏掉纪律的那份。本模块把它收敛成
一处，三处 store 只保留一个两行的 `_write_tx()` 转口。

## 为什么 `_connect()` 也必须在保护区间内

`lock.acquire()` 之后到 `finally` 之间的**任何**异常都必须走到 `release()`。
`_connect()` 会真的抛（路径不存在、fd 用尽、`sqlite3.OperationalError`），
而 `BEGIN IMMEDIATE` 更会（`database is locked`）——两者只要有一个在
`try` 之外，锁就永远不还。`acquire()` 自身留在 `try` 之外是有意的：它没拿到
锁时不该去 release。

## 为什么回滚要吞 `sqlite3.Error`

`BEGIN IMMEDIATE` 失败时事务根本没起来，此时 `ROLLBACK` 会抛
`cannot rollback - no transaction is active`——那是**清理阶段的正常噪声**，
不该盖住真正的失败原因（原异常会被 `raise` 重新抛出）。
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Callable, Iterator

__all__ = ["write_tx"]


@contextmanager
def write_tx(lock: threading.Lock,
             connect: Callable[[], sqlite3.Connection]
             ) -> Iterator[sqlite3.Connection]:
    """单写者写事务：`with write_tx(self._write_lock, self._connect) as conn:`。

    退出时——成功 `commit`、异常 `ROLLBACK` 后**原样重抛**——并**必然**
    `close()` + `release()`。连接由本函数创建与关闭；调用方只负责在 `with`
    体内执行 SQL，**不要**自己 commit/close。
    """
    lock.acquire()
    conn: sqlite3.Connection | None = None
    try:
        conn = connect()
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        if conn is not None:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        raise
    finally:
        if conn is not None:
            conn.close()
        lock.release()
