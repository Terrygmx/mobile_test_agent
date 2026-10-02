"""build_diff.py — Build-level Source Diff（12.6 / plan Task 3.3 第 2 步）。

12.6 原文：
    | Build-level | 独立任务：`mta source diff`，不阻塞日常回归 |
                 | 仅限已有用例**实际到达**的 Screen；**不做自动探索** |
    输出 diff：ADDED / REMOVED / RENAMED? / UNCHANGED / UNKNOWN
              （P1 只要求前三类中可判定的部分）

与 consistency.py（3.1）的区别——**两者不是一回事，不能互相顶替**：
consistency 比的是 generated（当前源码扫描）vs overrides（人工登记），
回答「这个 id 有没有人审阅过」；build_diff 比的是**两个 build 的 metadata**
（旧 build vs 新 build），回答「两个 build 之间 App 改了什么」。轴不同。

三条硬纪律：

1. **范围 = 用例实际到达的 Screen**（不做自动探索）。没被任何用例引用的
   页面即使变了也不报——否则改一个无关页面就淹没一堆 ADDED/REMOVED。
   「到达」= 用例里出现 `screen:X` 引用，或引用了 `X.elem` 限定名。
2. **dynamic/unknown 不参与判定**（12.2 无可定位 id，比了也是假漂移），
   但两侧计数与列表都要能看到（不判定 ≠ 不可见）。
3. **RENAMED? 是候选不是结论**。12.7 那行字面就带问号：启发式命中（同屏
   一增一删 + 元素类型相同）才列，**不进 counts、不影响 ok**。两侧各有
   多个候选时一律不猜——宁可报独立的 ADDED/REMOVED，让人自己看。

UNKNOWN：用例引用了任一侧 metadata 都没有的 screen。**不是 REMOVED**——
没扫到 ≠ 被删了，12.2 不得猜值同源纪律。但它必须让人看见，因此 `ok` 为
False（可见 ≠ 阻塞：默认 `fail_on_drift=False` 时 ok 只看 unknown）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from source.coverage import RESOLVED_TYPES, collect_refs, iter_refs

# diff 判定的四类（RENAMED? 刻意不在其中——它是候选，不是判定结论）
DETERMINED = ("ADDED", "REMOVED", "UNCHANGED", "UNKNOWN")


@dataclass(frozen=True)
class SourceDiffReport:
    """Build-level diff 结果。`ok` 默认只看 UNKNOWN（见模块 docstring）。"""

    scope_screens: tuple[str, ...] = ()
    unchanged: tuple[tuple[str, str], ...] = ()
    added: tuple[tuple[str, str], ...] = ()
    removed: tuple[tuple[str, str], ...] = ()
    # 用例引用、但两侧 metadata 都没有的 screen（不是 REMOVED：没扫到≠被删）
    unknown_screens: tuple[str, ...] = ()
    # 启发式改名候选 (screen, old_id, new_id)——带 '?' 语义，不参与判定
    renamed_candidates: tuple[tuple[str, str, str], ...] = ()
    # 两侧 dynamic/unknown 元素数（跳过判定但计数）
    dynamic_skipped: int = 0
    dynamic_old: int = 0
    dynamic_new: int = 0
    # ref → 引用它的用例（triage 用）
    cases_by_ref: dict = field(default_factory=dict)
    fail_on_drift: bool = False

    @property
    def counts(self) -> dict[str, int]:
        out = {"UNCHANGED": len(self.unchanged),
               "REMOVED": len(self.removed),
               "ADDED": len(self.added)}
        if self.unknown_screens:
            out["UNKNOWN"] = len(self.unknown_screens)
        return {k: v for k, v in out.items() if v}

    @property
    def drift(self) -> bool:
        """有无 REMOVED（用例引用得到的元素在新 build 里没了）。"""
        return bool(self.removed)

    @property
    def ok(self) -> bool:
        """12.6「不阻塞日常回归」：默认只看 UNKNOWN。

        `fail_on_drift=True`（CI 想要守门）时 REMOVED 也算不通过。
        """
        if self.unknown_screens:
            return False
        return not (self.fail_on_drift and self.drift)

    def to_dict(self) -> dict:
        return {
            "scope_screens": list(self.scope_screens),
            "counts": self.counts,
            "unchanged": [f"{s}.{i}" for s, i in self.unchanged],
            "added": [f"{s}.{i}" for s, i in self.added],
            "removed": [f"{s}.{i}" for s, i in self.removed],
            "unknown_screens": list(self.unknown_screens),
            "renamed_candidates": [f"{s}:{o}->{n}"
                                   for s, o, n in self.renamed_candidates],
            "dynamic_skipped": self.dynamic_skipped,
            "dynamic_old": self.dynamic_old,
            "dynamic_new": self.dynamic_new,
            "cases_by_ref": self.cases_by_ref,
            "drift": self.drift,
            "ok": self.ok,
        }


def _element_index(metadata: dict) -> dict[str, dict[str, str]]:
    """metadata → {screen: {accessibility_id: type}}，只收**可定位**元素。

    dynamic/unknown（无 accessibility_id）不进索引——它们比出来的差异是
    假的（12.2）。`type` 存元素类型（button/textfield…），RENAMED? 启发式
    要用它：同屏一增一删且类型相同才算疑似改名。
    """
    out: dict[str, dict[str, str]] = {}
    for scr in metadata.get("screen_elements") or []:
        name = scr.get("name") or ""
        bucket = out.setdefault(name, {})
        for el in scr.get("elements") or []:
            a11y = el.get("accessibility_id")
            if a11y and el.get("resolution_type") in RESOLVED_TYPES:
                bucket[a11y] = el.get("type") or ""
    return out


def _dynamic_count(metadata: dict) -> int:
    return sum(1
               for scr in metadata.get("screen_elements") or []
               for el in scr.get("elements") or []
               if not (el.get("accessibility_id")
                       and el.get("resolution_type") in RESOLVED_TYPES))


def reached_screens(cases: Iterable[Any], metadata: dict) -> tuple[str, ...]:
    """用例**实际到达**的 Screen（12.6 范围）。

    三种到达方式：`screen:X` 显式等待、`X.elem` 限定名、以及**全局唯一的
    短名**。短名要查 metadata：唯一时可确定（与 resolver 4.1 同一判据，
    短名不是「猜」）；跨屏同名时无法确定到达哪屏 → **不猜**（12.2），不贡献
    任何屏——宁可少报不可猜报。

    短名按 `old` 侧 metadata 解析：范围是「这批用例会碰到哪些屏」，用旧
    build 解析才稳定（新 build 删掉元素不该让范围缩水，那会让漂移自己消失）。
    """
    names: set[str] = set()
    by_short: dict[str, list[str]] = {}
    for scr in metadata.get("screen_elements") or []:
        screen = scr.get("name") or ""
        for el in scr.get("elements") or []:
            a11y = el.get("accessibility_id")
            if a11y and el.get("resolution_type") in RESOLVED_TYPES:
                by_short.setdefault(a11y, []).append(screen)
    for ref in iter_refs(cases):
        if ref.type == "screen":
            names.add(ref.id)
        elif "." in ref.id:
            names.add(ref.id.partition(".")[0])
        else:
            owners = {s for s in by_short.get(ref.id, ()) if s}
            if len(owners) == 1:      # 唯一 → 确定；0 个/多个 → 不猜
                names |= owners
    return tuple(sorted(names))


def diff_builds(old: dict, new: dict, cases: Iterable[Any], *,
                fail_on_drift: bool = False) -> SourceDiffReport:
    """12.6 Build-level diff 主入口（纯函数）。"""
    cases = list(cases)
    scope = reached_screens(cases, old)
    old_idx, new_idx = _element_index(old), _element_index(new)
    declared = {s for s in (old.get("screens") or []) if s} | {
        s for s in (new.get("screens") or []) if s}

    # 用例引用但两侧都没声明 → UNKNOWN（没扫到 ≠ 被删）
    unknown = tuple(s for s in scope if s not in declared)

    unchanged: list = []
    added: list = []
    removed: list = []
    renamed: list = []
    cases_by_ref: dict[str, set] = {}

    refs = collect_refs(cases)          # [(case_id, "Screen.elem")]
    for screen in scope:
        if screen in unknown:
            continue                    # 不可判定屏不产出 ADDED/REMOVED
        o, n = old_idx.get(screen, {}), new_idx.get(screen, {})
        for ident in sorted(set(o) | set(n)):
            in_o, in_n = ident in o, ident in n
            if in_o and in_n:
                unchanged.append((screen, ident))
            elif in_n:
                added.append((screen, ident))
            else:
                removed.append((screen, ident))
        renamed += _rename_candidates(screen, o, n)

    for cid, ref in refs:
        screen, _, ident = ref.partition(".")
        if screen and ident:
            cases_by_ref.setdefault(f"{screen}.{ident}", set()).add(cid)

    return SourceDiffReport(
        scope_screens=scope,
        unchanged=tuple(unchanged),
        added=tuple(added),
        removed=tuple(removed),
        unknown_screens=unknown,
        renamed_candidates=tuple(renamed),
        dynamic_skipped=_dynamic_count(old) + _dynamic_count(new),
        dynamic_old=_dynamic_count(old),
        dynamic_new=_dynamic_count(new),
        cases_by_ref={k: sorted(v) for k, v in sorted(cases_by_ref.items())},
        fail_on_drift=fail_on_drift)


def _rename_candidates(screen: str, old: dict, new: dict) -> list:
    """同屏「恰好一增一删 + 元素类型相同」→ RENAMED? 候选（**并列**输出）。

    不从 ADDED/REMOVED 里**摘掉**这一对：改名是猜测，被猜的那对元素在事实
    上仍然是「旧 build 有、新 build 无」。摘掉等于让报告只讲猜测、丢掉事实
    （consistency 的 prefix_matched 同款纪律：豁免/推断都要与事实并存）。

    增删数不等 → 一律不猜，各条按独立 ADDED/REMOVED 上报。
    """
    cands = [i for i in new if i not in old]
    dels = [i for i in old if i not in new]
    if len(cands) != 1 or len(dels) != 1:
        return []                      # 歧义 → 不猜
    new_id, old_id = cands[0], dels[0]
    if new.get(new_id) != old.get(old_id):
        return []                      # 类型不同 → 更可能是「删一个加一个」
    return [(screen, old_id, new_id)]


def format_report(report: SourceDiffReport) -> str:
    """人读输出。范围必须打出来——否则人无法判断「没报」是没变还是没比。"""
    lines = [f"source diff scope: {', '.join(report.scope_screens) or '(none)'}",
             f"  counts: {report.counts or '{}'}"]
    for label, refs in (("UNCHANGED", report.unchanged),
                        ("REMOVED", report.removed),
                        ("ADDED", report.added)):
        if refs:
            lines.append(f"  {label}:")
            lines += [f"    {s}.{i}" for s, i in refs]
    if report.unknown_screens:
        lines.append("  UNKNOWN (用例引用但两侧 metadata 都无此 screen——"
                     "没扫到 ≠ 被删，需人工确认):")
        lines += [f"    ? {s}" for s in report.unknown_screens]
    if report.renamed_candidates:
        lines.append("  RENAMED? (启发式候选：同屏一增一删且类型相同——"
                     "**非结论**，请人工确认):")
        lines += [f"    ~ {s}: {o} -> {n}"
                  for s, o, n in report.renamed_candidates]
    lines.append(
        f"  dynamic skipped: {report.dynamic_skipped} "
        f"(old={report.dynamic_old} new={report.dynamic_new}；12.2 不猜值，"
        f"不参与判定)")
    lines.append("  verdict: " + ("PASS" if report.ok else "REVIEW"))
    return "\n".join(lines)