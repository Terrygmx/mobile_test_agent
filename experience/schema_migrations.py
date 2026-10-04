"""schema_migrations.py — Experience 侧迁移执行器（P2-02 / Task 1.2）。

复用 tracer 的机制形态（schema_migrations 版本链 + 逐版本幂等应用），
但**独立版本链**：experience 库（默认 out/experience.db）与 trace 库
（out/trace.db）分开演进——可变状态库 vs append-only 流水库（plan
Task 1.2 关键决策），单写者边界（E9）按库划分。

泛化点（plan：graph 侧 M5 直接调用，不挂在 experience 包下）：执行器
只认「目录 + 版本链」，不 import experience 模型——graph/migrations 建
自己的 002 链时零改动复用。
"""
from __future__ import annotations

import re
import sqlite3
import time
from importlib import resources
from pathlib import Path

# 迁移脚本目录（打包内相对本模块；脚本名 <version>_<name>.sql 排序即应用序）
_MIGRATIONS_PKG = "experience.migrations"

_VERSION_RE = re.compile(r"^(\d+)_")


__all__ = ["migrate", "current_version", "EXPERIENCE_SCHEMA_VERSION"]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _available_migrations() -> list[tuple[int, str]]:
    """(序号, 脚本名) 列表，按序号升序。支持包资源与真实目录两种形态。"""
    out: list[tuple[int, str]] = []
    pkg_dir = Path(__file__).parent / "migrations"
    if pkg_dir.is_dir():
        names = [p.name for p in sorted(pkg_dir.iterdir())
                 if p.suffix == ".sql" and _VERSION_RE.match(p.name)]
    else:  # zipapp 等打包形态
        names = [n for n in resources.files(_MIGRATIONS_PKG).iterdir()
                 if n.endswith(".sql") and _VERSION_RE.match(n)]
    for name in names:
        seq = int(_VERSION_RE.match(name).group(1))
        out.append((seq, name))
    return sorted(out)
def _latest_known_version() -> str:
    """汇总版本串 = 最新已知脚本的**脚本名**（review_p2_task12 P3-3：
    从链尾派生，加 003_*.sql 忘改常量也不会让 migrate() 返回值撒谎）。"""
    known = _available_migrations()
    if not known:
        raise RuntimeError("no migration scripts found")
    return known[-1][1].removesuffix(".sql")


EXPERIENCE_SCHEMA_VERSION = _latest_known_version()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone()
    return row is not None


def current_version(conn: sqlite3.Connection) -> str | None:
    """experience 侧版本链当前值；空库 → None。"""
    if not _table_exists(conn, "schema_migrations"):
        return None
    row = conn.execute(
        "SELECT version FROM schema_migrations ORDER BY applied_at DESC,"
        " rowid DESC LIMIT 1").fetchone()
    return row[0] if row else None


def _applied_seq(conn: sqlite3.Connection) -> set[int]:
    """已应用脚本序号集合（按脚本名里的序号，不是版本串——幂等判定的
    基准是「哪个脚本跑过」，不是「版本串长什么样」）。"""
    if not _table_exists(conn, "schema_migrations"):
        return set()
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    out = set()
    for (v,) in rows:
        m = _VERSION_RE.match(v)
        if not m:
            # fail-loud：手工插的垃圾行会让幂等判定静默失真——直接拒绝
            raise RuntimeError(
                f"schema_migrations 有无法解析的版本行: {v!r}")
        out.add(int(m.group(1)))
    return out


def _load_script(name: str) -> str:
    pkg_dir = Path(__file__).parent / "migrations"
    path = pkg_dir / name
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return (resources.files(_MIGRATIONS_PKG).joinpath(name)
            .read_text(encoding="utf-8"))


def migrate(conn: sqlite3.Connection) -> str:
    """把 conn 升到 EXPERIENCE_SCHEMA_VERSION，返回版本串。幂等。

    只向后追加：已应用序号 > 本 build 已知脚本 → 拒绝（旧代码打开新库，
    静默续写会破坏迁移链——与 tracer.migrate 同款 fail-loud）。
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = _applied_seq(conn)
    known = _available_migrations()

    if applied and max(applied) > max((s for s, _ in known), default=0):
        raise RuntimeError(
            f"experience db is newer than this build "
            f"(applied={sorted(applied)}, known={[n for _, n in known]})")

    for seq, name in known:
        if seq in applied:
            continue
        # 脚本自含 IF NOT EXISTS——单脚本内重放安全；跨脚本由版本链保证
        conn.executescript(_load_script(name))
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?,?)",
            (name.removesuffix(".sql"), _now()))
        conn.commit()

    return EXPERIENCE_SCHEMA_VERSION
