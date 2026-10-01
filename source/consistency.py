"""consistency.py — 手写 overrides 与扫描 generated 的一致性 Gate（12.6 /
plan Task 3.1 第 5 步）。

为什么需要：P1-M3 接入 Source Intelligence 前，`repository/overrides/` 是
手写的（M1 起点）。扫描生成的 `generated/` 是**App 真相**——两者必须对齐：

  - generated 有而 overrides 没有 → 该元素未经人工审阅就进用例会失控
    （语义 ID/定位策略未定），必须补 overrides（origin: manual）；
  - overrides 有而 generated 没有 → overrides 指向的元素在 App 里已不存在
    （漂移），用例引用它必然 ELEMENT_NOT_FOUND；
  - dynamic/unknown 的 generated 元素 → **不算缺口**（12.2：无法唯一解析
    的一律不得猜值，人工补齐是预期流程）。

Gate 输出 ADDED / REMOVED / MATCHED 三态（P1 只做可判定的部分，12.6）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from repository.loader import load_elements


@dataclass(frozen=True)
class ConsistencyReport:
    """一致性 Gate 结果。`ok` = 无 REMOVED 且无未审 ADDED（见 check）。"""

    matched: tuple[tuple[str, str], ...] = ()
    added: tuple[tuple[str, str], ...] = ()      # generated 有、overrides 无
    removed: tuple[tuple[str, str], ...] = ()    # overrides 有、generated 无
    # ADDED 里「generated 解析不出 accessibility_id」的元素不计缺口
    unresolvable: tuple[tuple[str, str], ...] = ()
    # REMOVED 里被插值前缀豁免的——**可见但不算失败**（12.2 人工确认精神：
    # 豁免是「前缀匹配疑似插值实例」，漂移必须仍出现在报告里，不能静默。
    # review P2-2：cell_removed 与 cell_alpha 同样满足 startswith("cell_")
    # 曾被一并吞掉）。
    prefix_matched: tuple[tuple[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        """P1 口径：REMOVED 是硬失败（用例会挂）；ADDED 是待办（须人工补齐
        overrides 后重跑）。两者都为空才算过。prefix_matched 不影响 ok
        （插值人工登记是 12.2 预期流程），但**永远列在报告里**供人确认。"""
        return not self.removed and not self.added


def generated_element_ids(metadata: dict) -> set[tuple[str, str]]:
    """12.3 metadata → {(screen, accessibility_id)}。只收**解析出真实值**的
    （literal/constant）——dynamic/unknown 无 accessibility_id，进
    unresolvable。
    """
    ids: set[tuple[str, str]] = set()
    for screen in metadata.get("screen_elements", []):
        name = screen.get("name")
        if not name:
            continue
        for el in screen.get("elements", []):
            a11y = el.get("accessibility_id")
            if a11y:
                ids.add((name, a11y))
    return ids


def check(metadata: dict, overrides_root: str | Path, *,
          strict: bool = False) -> ConsistencyReport:
    """对比 generated metadata 与 overrides 目录。

    `strict=False`（默认）：generated 的 dynamic/unknown 元素（12.2 不猜值）
    不参与 REMOVED 判定——手写 overrides 登记的插值实例（如
    `cell_\\(item.key)` 的 cell_alpha）在 generated 里无 accessibility_id，
    若算 REMOVED 会让 Gate 永远红（那正是 12.2 设计的预期形态，不是漂移）。
    `strict=True`：插值也算缺口（临时排查用）。
    """
    overrides = load_elements(Path(overrides_root) / "elements")
    # loader 的键是 (screen | None, id)——None 兜底为 "" 保证同型可比
    override_ids = {(s or "", i) for s, i in overrides.keys()}
    gen_ids = generated_element_ids(metadata)

    added = gen_ids - override_ids
    removed = override_ids - gen_ids
    prefix_matched: tuple[tuple[str, str], ...] = ()
    if not strict:
        # 宽容模式：generated 的 dynamic/unknown（12.2 不猜值）不判 REMOVED。
        # ① id 完全相同；② 插值前缀覆盖（overrides 登记的 cell_alpha 被
        # generated 的 "cell_" 前缀覆盖）。② 进 prefix_matched 桶——
        # **不算失败但必须可见**（P2-2：cell_removed 也曾一并被吞，
        # 前缀越短盲区越大；漂移静默 = 12.6 Gate 形同虚设）。
        unresolvable_pairs = unresolvable_ids(metadata)
        prefix_matched = tuple(sorted(
            p for p in removed
            if p not in unresolvable_pairs
            and _covered_by_prefix(p, unresolvable_pairs)))
        removed = {p for p in removed
                   if not (p in unresolvable_pairs
                           or _covered_by_prefix(p, unresolvable_pairs))}
    matched = gen_ids & override_ids

    # unresolvable：generated 里没有 accessibility_id 的元素（dynamic/unknown）
    unresolvable = tuple(sorted(unresolvable_ids(metadata)))
    removed_final = tuple(sorted(removed))

    return ConsistencyReport(
        matched=tuple(sorted(matched)),
        added=tuple(sorted(added)),
        removed=removed_final,
        unresolvable=unresolvable,
        prefix_matched=prefix_matched)


def unresolvable_ids(metadata: dict) -> set[tuple[str, str]]:
    """generated 中 dynamic/unknown 元素的 (screen, id)。插值形态的 id 是
    静态前缀（如 "cell_"）——见 SwiftUIVisitor.interpolatedIdPrefix。"""
    out: set[tuple[str, str]] = set()
    for screen in metadata.get("screen_elements", []):
        name = screen.get("name") or ""
        for el in screen.get("elements", []):
            if not el.get("accessibility_id"):
                out.add((name, el.get("id", "")))
    return out


def _covered_by_prefix(pair: tuple[str, str],
                       unresolvable: set[tuple[str, str]]) -> bool:
    """overrides 登记的 (screen, id) 是否被 generated 的插值前缀覆盖。

    `cell_alpha` ∈ (HomeView, "cell_") 前缀 → True（12.2 预期形态：插值
    不猜值，人工登记实例，Gate 不得误报 REMOVED）。
    """
    screen, ident = pair
    for s, prefix in unresolvable:
        if s == screen and prefix and ident.startswith(prefix):
            return True
    return False


def format_report(report: ConsistencyReport) -> str:
    """人读输出（Gate 日志/CI）。"""
    lines = [
        f"consistency: matched={len(report.matched)} "
        f"added={len(report.added)} removed={len(report.removed)} "
        f"unresolvable={len(report.unresolvable)}",
    ]
    if report.added:
        lines.append("  ADDED (generated 有、overrides 无——补 origin: manual):")
        lines += [f"    + {s}.{i}" for s, i in report.added]
    if report.removed:
        lines.append("  REMOVED (overrides 有、generated 无——App 侧已删/改名):")
        lines += [f"    - {s}.{i}" for s, i in report.removed]
    if report.unresolvable:
        lines.append("  UNRESOLVABLE (12.2 不猜值，人工补齐后重跑):")
        lines += [f"    ? {s}.{i}" for s, i in report.unresolvable]
    if report.prefix_matched:
        lines.append("  PREFIX-MATCHED (REMOVED 被插值前缀豁免，不算失败——"
                     "人工确认是否漂移):")
        lines += [f"    ~ {s}.{i}" for s, i in report.prefix_matched]
    lines.append("  verdict: " + ("PASS" if report.ok else "FAIL"))
    return "\n".join(lines)
