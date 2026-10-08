"""CI 门禁：Agent 工具表静态断言（设计 §10.1；Task 1.3 / P3-03，矩阵 #2）。

**这个文件是门禁，不是普通单测**——plan 明文「工具注册表静态断言自 M1 起进 CI
门禁，防止后续任何 PR 给 Agent 加上危险工具」（`phase3-plan.md` 执行注意事项
#2）。所以它只做**静态/结构性**断言，不依赖设备、不依赖 fixture：

| 门禁 | 防的是什么 |
|---|---|
| A 工具全集 = `TOOL_ASSET_TIER` 的键集 | 出现第二份手抄的工具名单 |
| B 注册表键集 = `ALLOWED_TOOLS` | 注册表与全集漂移（能调的工具不在表里，或反之） |
| C 禁用名与**两张独立面**不相交 | 给 Agent 加上危险工具 |
| D `TOOL_ASSET_TIER` 值域无 PRODUCTION / CRITICAL | F2：Agent 不持有改正式资产的工具 |
| E 禁用名在 `agents/` 全包**只出现一次**（且只在 `BANNED_TOOLS` 里） | 在别的模块偷偷注册 / 二次出现 |
| **G `GUARDED_TOOLS` = 设计 §10.1「执行（仍经 Guard）」组逐字** | **让 Guard 不再跑**（F1 的落点） |

⚠️ 门禁 **G** 是 review_p3_task13 P2-1 补的，值得记一句它为什么必须有：`GUARDED_TOOLS`
决定 `AgentToolkit.call` 里 `if tool in GUARDED_TOOLS` 那一行**是否构造 `GuardContext`
并调 `guard.check`**——也就是说「**Guard 到底跑不跑**」完全由它决定。在补 G 之前，
把 `assert_text` 换成只读的 `get_logs`（**等长、仍是全集的子集**）后
**全量 1412 条一条都不红**：防住了「给 Agent 加上危险工具」，却没防住「让 Guard 不再
跑」。它与禁用名一样，**从设计原文逐字重打**，不从代码反推。

**削弱本文件的任何断言都必须先在评审里说清为什么**——它是 F2/F12 唯一的机械
保证（`review_p3_task11` 的「红线测试：8 个禁用名逐一断言不存在」在此正式化）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agents.models import TOOL_ASSET_TIER, AssetTier
from agents.tools import (ALLOWED_TOOLS, BANNED_TOOLS, GUARDED_TOOLS,
                          TOOL_METHODS, AgentToolkit)

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

# 设计 §10.1 中间组「执行（仍经 Guard）」逐字（10 个）——同样**显式列出**：
# 从 `GUARDED_TOOLS` 反推的话，「有人从这张表里换掉一个」本文件会跟着变绿。
DESIGN_10_1_GUARDED = (
    "tap",
    "input",
    "swipe",
    "back",
    "wait",
    "assert_exists",
    "assert_text",
    "screenshot",
    "run_testcase",
    "run_candidate_test",
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

    两张**独立**面：全集（`ALLOWED_TOOLS`）与注册表（`TOOL_METHODS`）。
    ⚠️ `banned not in TOOL_ASSET_TIER` **不是**第三张独立面——`ALLOWED_TOOLS`
    由它派生、且门禁 A 已钉住两者相等，所以那一条是同一断言的重复（便宜，
    留着当冗余；但别把它说成「三张面」，review_p3_task13 小观察指出过）。
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


# --- G：Guard 到底跑不跑（review_p3_task13 P2-1） -----------------------------


def test_guarded_set_matches_the_design_group_verbatim():
    """`GUARDED_TOOLS` == 设计 §10.1 中间组逐字（**门禁 G**）。

    这张表决定 `AgentToolkit.call` 是否调 `guard.check`——**「Guard 到底跑不跑」
    完全由它决定**。补 G 之前，把 `assert_text` 换成只读的 `get_logs`（等长、
    仍是全集子集）后**全量 1412 条全绿**：防住了「加危险工具」，没防住「让
    Guard 不再跑」。所以它必须与设计原文**逐字对账**，不从代码反推。
    """
    assert set(GUARDED_TOOLS) == set(DESIGN_10_1_GUARDED)
    assert len(GUARDED_TOOLS) == 10


def test_guarded_tools_are_allowed_and_not_banned():
    """Guard 名单不能跑出工具全集，也不能与被禁名相交。"""
    assert GUARDED_TOOLS <= ALLOWED_TOOLS
    assert not (GUARDED_TOOLS & BANNED_TOOLS)


def test_every_guarded_tool_actually_calls_the_guard():
    """**行为面**：Guard 名单里的每个工具都必须真的触发 `guard.check`。

    静态对账（门禁 G）挡的是「名单被改」；这条挡的是「`call` 里的
    `if tool in GUARDED_TOOLS` 被挪走/短路」——两者缺一，Guard 都可能不跑。
    """
    from executor.guard import EnvKind, Guard

    seen: list[str] = []

    class _Rec(Guard):
        def check(self, ctx):
            seen.append(ctx.action)
            return super().check(ctx)

    tk = AgentToolkit(guard=_Rec(EnvKind.SANDBOX))
    for tool in sorted(GUARDED_TOOLS):
        seen.clear()
        try:
            tk.call(tool)
        except Exception:                # noqa: BLE001 — 依赖缺失/未接线都会抛，
            pass                         # 但 Guard 已经在最前面跑过了
        assert seen == [tool], f"{tool} 没过 Guard"


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
    """门禁自身不能空转：扫到的文件数与字符串常量数要有下界。

    下界按**实测值**贴住（不是拍脑袋的宽松值）——太松只能抓「范围写成空」，
    抓不到「漏掉一个文件」（review_p3_task13 小观察）。实测 5 文件 / 393 个常量。
    """
    files = [p for p in _AGENTS_DIR.rglob("*.py") if "__pycache__" not in p.parts]
    assert len(files) >= 5, f"只扫到 {len(files)} 个 agents/*.py，范围疑似写错"
    total = sum(len(_string_constants(p)) for p in files)
    assert total > 300, f"只扫到 {total} 个字符串常量，判据疑似失效"
    covered = [p.relative_to(_ROOT) for p in files]
    for must in ("agents/tools.py", "agents/models.py", "agents/storage.py",
                 "agents/policy_config.py", "agents/__init__.py"):
        assert Path(must) in covered, f"{must} 必须被覆盖到"
