"""CI 门禁：Agent 工具表静态断言（设计 §10.1；Task 1.3 / P3-03，矩阵 #2）。

**这个文件是门禁，不是普通单测**——plan 明文「工具注册表静态断言自 M1 起进 CI
门禁，防止后续任何 PR 给 Agent 加上危险工具」（`phase3-plan.md` 执行注意事项
#2）。所以它只做**静态/结构性**断言，不依赖设备、不依赖 fixture：

| 门禁 | 防的是什么 |
|---|---|
| A 工具全集 = `TOOL_ASSET_TIER` 的键集 | 出现第二份手抄的工具名单 |
| B 注册表键集 = `ALLOWED_TOOLS` | 注册表与全集漂移（能调的工具不在表里，或反之） |
| C 禁用名与全集/注册表**不相交** | 给 Agent 加上危险工具 |
| D `TOOL_ASSET_TIER` 值域无 PRODUCTION / CRITICAL | F2：Agent 不持有改正式资产的工具 |
| E 禁用名在 `agents/` 全包**只出现一次**（且只在 `BANNED_TOOLS` 里） | 在别的模块偷偷注册 / 二次出现 |

**削弱本文件的任何断言都必须先在评审里说清为什么**——它是 F2/F12 唯一的机械
保证（`review_p3_task11` 的「红线测试：8 个禁用名逐一断言不存在」在此正式化）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agents.models import TOOL_ASSET_TIER, AssetTier
from agents.tools import (ALLOWED_TOOLS, BANNED_TOOLS, TOOL_METHODS,
                          AgentToolkit)

# 仓库根（本文件在 tests/unit/ 下）
_ROOT = Path(__file__).resolve().parents[2]
_AGENTS_DIR = _ROOT / "agents"

# 设计 §10.1 末句逐字（8 个名字）——**显式列出**，不从 BANNED_TOOLS 反推：
# 反推的话「有人从 BANNED_TOOLS 里删一个」本文件会跟着变绿。
DESIGN_10_1_BANNED = (
    "delete_testcase",
    "modify_expectation",
    "modify_verified_experience",
    "modify_repository_directly",
    "production_api_call",
    "real_payment",
    "arbitrary_shell",
    "arbitrary_python",
)


# --- A：工具全集只有一个来源 ---------------------------------------------------


def test_allowed_tools_is_derived_from_the_asset_tier_table():
    """`ALLOWED_TOOLS` == `frozenset(TOOL_ASSET_TIER)`（**派生**，不是手抄）。

    手抄一份名单就会漂移：加了 tier 却没进全集（工具调不到）、或进了全集却没
    tier（tier 标注缺失）。
    """
    assert ALLOWED_TOOLS == frozenset(TOOL_ASSET_TIER)
    assert len(ALLOWED_TOOLS) == 22, "设计 §10.1 全集是 22 个"


# --- B：注册表与全集一致 -------------------------------------------------------


def test_registry_key_set_equals_the_allowlist():
    assert set(TOOL_METHODS) == set(ALLOWED_TOOLS)


def test_every_registered_tool_maps_to_a_real_method():
    for tool, method in TOOL_METHODS.items():
        assert callable(getattr(AgentToolkit, method, None)), \
            f"{tool} 映射到不存在的 {method}"


def test_toolkit_exposes_no_dynamic_dispatch():
    """不许有绕过查表的动态入口（F12：Subagent 也没有额外权限）。"""
    for name in ("__getattr__", "__getitem__", "execute", "perform"):
        assert not hasattr(AgentToolkit, name), f"AgentToolkit 不该有 {name}"


# --- C / E：禁用名 -------------------------------------------------------------


def test_banned_set_matches_the_design_verbatim():
    assert set(BANNED_TOOLS) == set(DESIGN_10_1_BANNED)
    assert len(BANNED_TOOLS) == 8


@pytest.mark.parametrize("banned", DESIGN_10_1_BANNED)
def test_banned_tool_is_absent_from_every_tool_surface(banned):
    """8 个禁用名逐一断言**不存在**（矩阵 #2 的静态面）。

    三张面全查：全集、注册表、tier 表。少查一张就等于留一条注册通道。
    """
    assert banned not in ALLOWED_TOOLS
    assert banned not in TOOL_METHODS
    assert banned not in TOOL_ASSET_TIER


def test_asset_tier_values_never_reach_production_or_critical():
    """F2：PRODUCTION / CRITICAL **仅作为枚举值存在**（语义文档），
    不出现在任何 Agent 可调用工具的归属里。"""
    values = set(TOOL_ASSET_TIER.values())
    assert values <= {AssetTier.READ_ONLY, AssetTier.CANDIDATE}
    assert AssetTier.PRODUCTION not in values
    assert AssetTier.CRITICAL not in values


# --- E：禁用名在整个 agents/ 包只出现一次 --------------------------------------


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """所有 docstring 常量节点的 `id()`（排除它们——文档里提到禁用名是合法的）。"""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                out.add(id(body[0].value))
    return out


def _string_constants(path: Path) -> list[str]:
    """文件里**非 docstring** 的字符串常量（含集合/字典字面量里的元素）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docs = _docstring_nodes(tree)
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs]


def test_banned_names_appear_exactly_once_in_the_agents_package():
    """禁用名在 `agents/**/*.py` 里**只出现一次**，且那一次必须在
    `tools.py::BANNED_TOOLS` 里。

    这条防的是「在别的模块偷偷注册」与「同一名字出现两处」（第二处通常意味着
    有人把它当工具名用了）。docstring 里提到禁用名不算——那是文档。
    """
    occurrences: dict[str, list[str]] = {b: [] for b in DESIGN_10_1_BANNED}
    for path in sorted(_AGENTS_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for value in _string_constants(path):
            if value in occurrences:
                occurrences[value].append(str(path.relative_to(_ROOT)))

    bad = {name: where for name, where in occurrences.items()
           if where != ["agents/tools.py"]}
    assert not bad, (
        f"禁用名必须只在 agents/tools.py 出现一次（BANNED_TOOLS 的字面量），"
        f"实际：{bad}")


def test_gate_scan_actually_covers_the_package():
    """门禁自身不能空转：扫到的文件数与字符串常量数要有下界。"""
    files = [p for p in _AGENTS_DIR.rglob("*.py") if "__pycache__" not in p.parts]
    assert len(files) >= 4, f"只扫到 {len(files)} 个 agents/*.py，范围疑似写错"
    total = sum(len(_string_constants(p)) for p in files)
    assert total > 50, f"只扫到 {total} 个字符串常量，判据疑似失效"
    assert (Path("agents/tools.py") in
            [p.relative_to(_ROOT) for p in files]), "tools.py 必须被覆盖到"
