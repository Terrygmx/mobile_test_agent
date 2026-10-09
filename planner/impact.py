"""impact.py — `changed_files` → 受影响 target（设计 §5.3 第一段；Task 2.3 / P3-07）。

## 职责：**适配层**，不是索引本体

设计 §5.3 的流程是 `Git Diff → Impact Analysis(P2) → 受影响 Screen/Element/TestCase`。
本模块只做**第一段到第二段的翻译**：

```text
changed_files（git diff，source/git_diff.py）
    → metadata 的文件级归属（screen_elements[].elements[].source.file）
    → target ids（Screen.elem 形态）
    → KnowledgeSources.impact_of(target_id)     ← P2 的反向索引，不重写
```

「element → testcase」的索引本体是 `source/coverage.py::collect_case_refs` +
`graph/impact.py::ref_index` / `affected_testcases`（Task 5.4 已统一为单点）。本模块
**一行索引逻辑都没有**——那正是 F3「不新建平行的检索接口」要防的。

## 文件级归属从哪来（实测 2026-10-09）

`repository/generated/local/source_metadata.json` 的 **27/27** 个元素都带
`source: {"file": …, "line": …}`（Swift 扫描器与 `source/storyboard.py` 都填）。
所以主路径可用；**结构性缺失**时见 `ImpactMappingError` 与 `drift_targets`。

## ⚠️ 偏离登记 1：多了一个 `unmatched_changed_files` 兄弟函数

plan 的 Files 只写了 `changed_targets(changes, metadata) -> tuple[str, ...]`。实现时
发现「**改了的文件一个 target 都没映射到**」这个中间态**必须可见**：它有两种完全不同的
成因（这次改动没碰 UI / metadata 里的路径与 git 给的路径形态不符），而**两者都表现为
「空 Plan」**。按「错误落在看不见那一侧」的纪律补一个只读视图；它与 `changed_targets`
共用同一个私有索引 `_file_index`（**不是第二份实现**）。

## ⚠️ 偏离登记 2：`drift_targets` 需要 `cases`（plan 只写了「回退 drift 面」）

plan 说「metadata 缺文件级归属时回退 `source/build_diff.py diff_builds` 的 drift 面」。
实现时核实：`diff_builds` 的**范围**由 `reached_screens(cases, old)` 决定——**没有
cases 就退化成空 scope、什么都比不出来**，而结果看起来只是「没变化」。所以回退面必须
收 `cases`，且 `cases` 为空时**显式拒绝**（见该函数的 docstring）。这条与 open
question #2 是同一件事：**映射精度损失必须写进 Plan 的 reasons**，由调用方负责。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

__all__ = [
    "ImpactMappingError",
    "changed_targets",
    "drift_targets",
    "metadata_has_file_attribution",
    "unmatched_changed_files",
]


class ImpactMappingError(ValueError):
    """**无法**把 `changed_files` 映射到 target —— 与「映射结果为空」是两回事。

    只在两种情况下抛：

    1. metadata **结构性地**缺文件级归属（一个元素都没有 `source.file`）而确实有
       改动文件要映射；
    2. `drift_targets` 拿不到 `cases`。

    两种都属「我们没能力判定」。静默返回空会让 planner 产出一个**看起来完全正常**的
    空 Plan——正是矩阵 #6 要防的「不编造依据」（对应地，「改动没碰 UI」这种**有依据的
    空**不抛，走 `unmatched_changed_files` 明示）。
    """


# --- 主路径：文件级归属 -------------------------------------------------------


def metadata_has_file_attribution(metadata: Mapping) -> bool:
    """metadata 里**至少一个**元素带 `source.file`。

    调用方用它决定走主路径还是回退面（`drift_targets`）——判据收在这里，避免
    「谁来判断」有第二份实现。
    """
    for screen in metadata.get("screen_elements") or []:
        for el in screen.get("elements") or []:
            if (el.get("source") or {}).get("file"):
                return True
    return False


def _element_name(el: Mapping) -> str:
    """元素在 metadata 里的名字（`impact_of` 是**文本匹配**，不是解析）。

    取 `accessibility_id`，缺失时退回 `id`（dynamic/unknown 元素只有 id）。两者都
    只是「名字」：多收一个名字只会多包含用例（沿用 P2「宁可多包含，不可静默漏掉」），
    不会把不相关的用例拉进来——匹配仍由 `affected_testcases` 的精确/后缀规则裁决。
    """
    return str(el.get("accessibility_id") or el.get("id") or "").strip()


def _file_index(metadata: Mapping) -> dict[str, tuple[str, ...]]:
    """文件 → target ids（`Screen.elem`，去重保序）。**唯一的归属索引**。

    两个公开视图（`changed_targets` / `unmatched_changed_files`）都读它——同一份
    遍历只许有一处实现。
    """
    out: dict[str, list[str]] = {}
    for screen in metadata.get("screen_elements") or []:
        name = str(screen.get("name") or "").strip()
        for el in screen.get("elements") or []:
            file = str((el.get("source") or {}).get("file") or "").strip()
            elem = _element_name(el)
            if not file or not elem:
                continue
            target = f"{name}.{elem}" if name else elem
            bucket = out.setdefault(file, [])
            if target not in bucket:
                bucket.append(target)
    return {k: tuple(v) for k, v in out.items()}


def _paths(changes: Iterable[str]) -> tuple[str, ...]:
    """`changes` → 路径元组。**只收 iterable of str**，单个 `str` 显式拒绝。

    输入就是 `GitChangeSet.changed_files`（重命名已取旧 + 新、去重保序）。

    ⚠️ 单个字符串**必须拒**：`for p in "a.swift"` 会安静地逐字符迭代出 7 个「路径」，
    它们全都匹配不到 → 表现为「空 Plan」而不是「你传错了」（P3-1/P3-2 同款：
    错误落在看不见那一侧）。非 str 的元素同理——绑进 `dict.get` 一条也匹配不到。
    """
    if isinstance(changes, (str, bytes)):
        raise TypeError(
            f"changes 必须是**路径的可迭代**，不是单个字符串（got {changes!r}）"
            f"——传 `GitChangeSet.changed_files`（tuple[str, ...]）")
    out = tuple(changes)
    bad = [p for p in out if not isinstance(p, str)]
    if bad:
        raise TypeError(f"changes 里必须全是 str，got {bad[:3]!r}")
    return out


def changed_targets(changes: Iterable[str], metadata: Mapping) -> tuple[str, ...]:
    """改动文件 → target ids（`Screen.elem`，按 metadata 顺序去重）。

    plan Task 2.3 的签名。**「结果为空」不是错误**（改动可能真的没碰 UI）——调用方用
    `unmatched_changed_files` 把「改了哪些文件但没映射到」明示出来，两者合起来才
    分得清「没碰 UI」与「路径形态不符」。

    metadata 结构性缺 `source.file` → `ImpactMappingError`（见类 docstring）。
    """
    paths = _paths(changes)
    if paths and not metadata_has_file_attribution(metadata):
        raise ImpactMappingError(
            "metadata 里没有任何元素的 `source.file`，无法按文件映射改动"
            "（扫描器版本过旧 / 只走了 ObjC·IB 旁路？）。两条出路：① 重新生成 "
            "metadata（`mta repo generate`）；② 用 `drift_targets(base_metadata, "
            "metadata, cases)` 走 build 间 drift 面（精度损失要写进 Plan 的 reasons）。")
    index = _file_index(metadata)
    out: list[str] = []
    for path in paths:
        for target in index.get(path, ()):
            if target not in out:
                out.append(target)
    return tuple(out)


def unmatched_changed_files(changes: Iterable[str],
                            metadata: Mapping) -> tuple[str, ...]:
    """改了但**没映射到任何元素**的文件（保持 `changes` 的顺序）。

    这个视图存在的理由只有一个：让「空 Plan」的成因**可见**。「这次改动没碰 UI」
    与「metadata 的路径写法与 git 的不一致（相对/绝对、前缀）」在结果上长得一模一样，
    而后者会让 planner 长期产出空 Plan 却没人发现。
    """
    paths = _paths(changes)
    index = _file_index(metadata)
    return tuple(p for p in paths if not index.get(p))


# --- 回退面：build 间 drift（metadata 缺文件级归属时） --------------------------


def drift_targets(base_metadata: Mapping, metadata: Mapping,
                  cases: Iterable[Any]) -> tuple[str, ...]:
    """回退面：用 **build 间元素 drift** 代替文件映射（plan Task 2.3 / open question #2）。

    复用 `source/build_diff.py::diff_builds`（**不重写** build diff），取
    `added ∪ removed` 作为「变了的目标」：

    - 范围是 `reached_screens(cases, base)`——**用例会碰到的屏**，所以 `cases` 是必需
      输入；为空时**显式拒绝**（空 scope 会让 diff 静默给出「没变化」，与「真的没变化」
      分不开——`diff_builds` 的 docstring 也强调「没报」不等于「没变」）；
    - `renamed_candidates` 是**猜测**且不参与判定，而改名的那对元素在事实上仍是
      「旧有新无 / 新有旧无」——它们已在 `added`/`removed` 里（build_diff 的纪律：
      猜测与事实**并存**），所以这里不需要额外处理；
    - ⚠️ **精度损失**：drift 是「build 之间的元素变化」，比「本次 commit 改了哪些文件」
      **宽**（它不知道哪些变化来自这次改动）。调用方**必须**把这一点写进 Plan 的
      `reasons`（open question #2 的原文要求），不能只把它记在日志里。
    """
    from source.build_diff import diff_builds      # 只有回退面才需要它，故按需导入

    case_list = list(cases)
    if not case_list:
        raise ImpactMappingError(
            "drift 面需要 cases：`diff_builds` 的范围由 `reached_screens(cases, base)` "
            "决定，没有用例就没有 scope——比出来的结果是空，而空看起来像「没变化」。")
    report = diff_builds(dict(base_metadata), dict(metadata), case_list)
    targets = {f"{screen}.{ident}"
               for screen, ident in (*report.added, *report.removed)}
    return tuple(sorted(targets))
