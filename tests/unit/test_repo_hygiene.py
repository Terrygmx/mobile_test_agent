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
