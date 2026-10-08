"""仓库卫生守护（review_p2_task52 P3-3 的固化）。

## 为什么要有这个文件

「同一文件里同名顶层定义」是 Python 的**静默遮蔽**：后者覆盖前者，无警告、
无报错，`def test_` 数与 pytest 收集数对不上只是它的一种表现。

本项目已**三次**踩到同一个根因（「`cat >>` 追加被重复执行」）：

1. `review_p2_task43` P3-3：`test_report_experience_metrics.py` 56 行逐行重复块；
2. `review_p2_task51` P3-1：`test_cli_run.py` 新增测试各定义两次 + 存量 3 个
   `test_resolve_app_build_*`；
3. 同一次评审又抓到 `test_graph_builder.py` 的 3 个钉子测试各两次。

前两次都是**手工跑一遍**就宣布「固化」——`aadb8b3` 的提交信息写着「固化体检」，
而仓库里当时**没有任何**这样的测试（评审实测 grep 零命中）。这个文件就是把那句
话变成「一条会自动失败的守护」。

## 覆盖面

扫**仓库自己的 Python**（`tests/` 与各源码包），排除生成物与第三方目录
（`out/` 每次 run 会生成上千个文件、`build/` 是编译中间产物）。判据只针对
**顶层** `def`：类内同名方法、嵌套函数、`if TYPE_CHECKING` 分支里的重定义都是
合法形态，不在本守护范围内。
"""
from __future__ import annotations

import ast
import collections
from pathlib import Path

# 仓库根（本文件在 tests/unit/ 下）
ROOT = Path(__file__).resolve().parents[2]

# 不扫的目录：生成物 / 中间产物 / 第三方 / 工具目录
_SKIP_DIRS = {"out", "build", "__pycache__", ".git", ".venv", "venv",
              "node_modules", ".workbuddy-ai", ".mypy_cache", ".pytest_cache"}

# 源码包（只扫这些顶层目录下的 .py，避免误入 out/ 的工作副本）
_SOURCE_DIRS = ("tests", "agent", "cli", "environment", "executor",
                "experience", "graph", "llm", "report", "repository",
                "runner", "session", "source", "testcase", "tracer")


def _iter_python_files():
    for name in _SOURCE_DIRS:
        base = ROOT / name
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if _SKIP_DIRS & set(path.parts):
                continue
            yield path
    for path in ROOT.glob("*.py"):        # 根级脚本（conftest / run_*_demo）
        yield path


def _duplicate_top_level_defs(path: Path) -> dict[str, int]:
    """该文件里出现多次的顶层定义名（含 `async def`）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = [n.name for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef))]
    return {k: c for k, c in collections.Counter(names).items() if c > 1}


def test_no_duplicate_top_level_definitions():
    """全仓不得有同名顶层定义（静默遮蔽；本项目已发生三次）。"""
    offenders: list[str] = []
    for path in _iter_python_files():
        try:
            dup = _duplicate_top_level_defs(path)
        except SyntaxError as e:                      # 语法错由 pytest 收集暴露
            offenders.append(f"{path.relative_to(ROOT)}: SyntaxError {e}")
            continue
        if dup:
            rel = path.relative_to(ROOT)
            offenders.append(f"{rel}: {dup}")

    assert not offenders, (
        "发现同名顶层定义（Python 后者静默覆盖前者）：\n  "
        + "\n  ".join(offenders)
        + "\n修法：删掉重复的那一份（保留**后者**——那是实际生效的），"
          "并检查是不是 `cat >>` 追加被重复执行了。")


def test_test_files_collect_count_matches_definitions():
    """`tests/**` 的 `def test_` 数 == 唯一名数（重复定义的快速对账）。

    与上一条同源，但只针对测试文件、只数 `test_` 前缀——它是「追加测试块」这类
    事故的**直接**指标（`--collect-only` 的收集数在 CI 里不易拿到，这里用
    ast 做等价对账）。
    """
    bad: list[str] = []
    for path in (ROOT / "tests").rglob("test_*.py"):
        if _SKIP_DIRS & set(path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = [n.name for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name.startswith("test_")]
        dup = {k: c for k, c in collections.Counter(names).items() if c > 1}
        if dup:
            bad.append(f"{path.relative_to(ROOT)}: {dup}")
    assert not bad, "测试文件有重复定义：\n  " + "\n  ".join(bad)


def test_hygiene_scan_actually_covers_the_repo():
    """守护自身不能是空转：扫到的文件数要有下界。

    「零命中」有两种成因——真的干净，或者**根本没扫到东西**（路径写错、被排除
    规则吃掉）。这条把后者变成失败。
    """
    files = list(_iter_python_files())
    assert len(files) > 80, f"只扫到 {len(files)} 个 .py，扫描范围疑似写错"
    assert any(p.name == "conftest.py" for p in files), "根级 conftest 要覆盖到"
    assert any("tests" in p.parts for p in files), "tests/ 要覆盖到"
    # 反向：生成物目录必须被排除（否则每次 run 都会拖慢甚至误报）
    assert not any("out" in p.parts for p in files), "out/ 不该被扫"


# --- E9 单写者：三处 store 只许有一个写入口 ------------------------------------
#
# review_p3_task11_final 建议动作 5：P2-2 把三份手写事务样板收敛成
# `source/sqlite_tx.write_tx` 之后，需要一条**机械**守卫防止样板被抄回来
# ——而「已修过的模式被复制」在本项目**已经复发过一次**（同一次评审的 P3-2：
# `agents/storage.py` 抄了 experience 已修掉的 `_connect` 形态）。

# 三处 store（按写者边界分家的可变状态库）
_STORE_FILES = ("experience/store.py", "agents/storage.py", "graph/storage.py")

# 算作「写语句」的 SQL 前缀；`SELECT` / `PRAGMA` 不在内（读与连接参数）
_WRITE_SQL_PREFIXES = ("INSERT", "UPDATE", "DELETE", "REPLACE")

# 三处 store 写语句数的下界（防空转：判据写错会变成「零命中」）
_MIN_WRITE_STATEMENTS = 18


def _write_tx_body_ranges(tree: ast.AST) -> list[tuple[int, int]]:
    """`with self._write_tx() as conn:` 语句的行区间。"""
    out: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            call = item.context_expr
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "_write_tx"):
                out.append((node.lineno, node.end_lineno))
    return out


def _write_sql_calls(tree: ast.AST) -> list[ast.Call]:
    """首参是「写 SQL 字符串常量」的 `execute*` 调用。

    只认 `ast.Constant`：隐式字符串拼接在**解析期**已折叠成一条常量（所以
    多行 SQL 照样命中），而 `"… WHERE " + where` 这类动态拼串与 `f"…"`
    会被跳过——避免把读语句或拼接式 SQL 误判成写。
    """
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute)
                and func.attr in ("execute", "executemany", "executescript")):
            continue
        first = node.args[0]
        if (isinstance(first, ast.Constant) and isinstance(first.value, str)
                and first.value.lstrip().upper().startswith(_WRITE_SQL_PREFIXES)):
            out.append(node)
    return out


def test_store_writes_are_inside_the_write_tx_region():
    """三处 store 的写语句必须都在 `with self._write_tx()` 保护区内（E9）。

    写在保护区外就绕过了**进程锁 + `BEGIN IMMEDIATE`**（跨进程串行 + 忙等待），
    而这类错误的后果是「并发写静默丢更新」或「锁泄漏后永久挂死」——本项目两种
    都真发生过。收敛到 `source/sqlite_tx.write_tx` 之后，本守卫防的是**新写一个
    方法时忘了套 `with self._write_tx()`**。
    """
    bad: list[str] = []
    total = 0
    for rel in _STORE_FILES:
        path = ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        regions = _write_tx_body_ranges(tree)
        calls = _write_sql_calls(tree)
        total += len(calls)
        for call in calls:
            if not any(lo <= call.lineno <= hi for lo, hi in regions):
                sql = call.args[0].value.strip().splitlines()[0][:48]
                bad.append(f"{rel}:{call.lineno} → {sql!r}")
    assert total >= _MIN_WRITE_STATEMENTS, (
        f"三处 store 只扫到 {total} 条写语句（下界 {_MIN_WRITE_STATEMENTS}）"
        "，判据疑似失效")
    assert not bad, ("写语句绕过了 _write_tx（无进程锁 / 无 BEGIN IMMEDIATE）：\n  "
                     + "\n  ".join(bad)
                     + "\n修法：把该语句挪进 `with self._write_tx() as conn:`。")


def test_write_tx_boilerplate_is_not_copied_back():
    """三处 `_write_tx` 只做转口，不许再抄「锁 + `BEGIN IMMEDIATE`」样板。

    这条是 review_p3_task11_final 建议动作 5 的正面记录：P2-2 把三份手写样板
    收敛成 `source/sqlite_tx.write_tx`（唯一实现）。本守卫防的是**样板被抄
    回来**——而「已修过的模式被复制」在本项目已复发过一次（同次评审的 P3-2）。
    """
    for rel in _STORE_FILES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "write_tx(self._write_lock, self._connect)" in src, \
            f"{rel} 的 _write_tx 不再走共享实现"
        assert "_write_lock.acquire()" not in src, \
            f"{rel} 又抄回了手写锁样板（应走 source/sqlite_tx.write_tx）"
        assert '"BEGIN IMMEDIATE"' not in src, \
            f"{rel} 又抄回了手写 BEGIN IMMEDIATE（应走 source/sqlite_tx.write_tx）"
