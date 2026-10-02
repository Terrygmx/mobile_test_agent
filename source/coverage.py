"""coverage.py — Source Coverage 指标（设计 12.7 / plan Task 3.2）。

12.7 原文：
    Identifier Coverage = 用例引用的 identifier 中被 metadata 正确解析
                          (literal+constant) 的数量 / 用例引用的 identifier 总数
    同时输出 dynamic_ratio、unknown_ratio

纯函数（H18 同款纪律）：输入 12.3 metadata + 已解析的 TestCase 列表，输出
CoverageReport。不碰设备、不读库、不猜值。

分桶口径（12.7 只给公式不给分桶，这里定档并写死理由）：

  resolved   metadata 里 resolution_type ∈ {literal, constant} 且解析出
             accessibility_id，与引用逐字匹配 —— 12.2 唯一「可定位」的形态，
             也正是 export_generated 唯一导出的形态，覆盖率与可执行性同源；
  dynamic    引用命中 12.2 插值元素的静态前缀（`cell_alpha` 命中 `cell_`）。
             **不算覆盖**：12.2 不得猜值，运行时靠人工登记的实例 locator，
             扫描器给不出可定位 id。单列是为了「不可覆盖」可见，不是失败；
  unknown    命中 resolution_type=unknown 的元素，同上不算覆盖；
  ambiguous  短名引用（无 `Screen.` 限定）在多个 Screen 各有一个解析值 ——
             lint 会报 AMBIGUOUS，运行时也会 AmbiguousReferenceError，
             与「已覆盖」本质不同，单列；
  missing    metadata 里根本没有该 identifier（漂移 / 从未登记）。

分母 = 上述五桶之和（**不含 screen 引用**）：screen marker 有独立指标
screen_coverage —— 混进同一个分母会让「页面没登记」和「页面元素没登记」
互相掩盖。screen_coverage 同样只认 metadata 顶层 `screens`（marker 声明名，
见 source/export.py：container struct 名不作数）。

去重：覆盖率按 **identifier 集合** 计（同一 id 被 20 条用例引用仍算 1），
否则增删用例会凭空改变指标；原始引用次数保留在 occurrences 供人看。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# 12.2 里「解析出真实值」的两档。与 source.export 的导出判据同源——改了
# 这里必须同步 export（否则「算覆盖但导不出」，指标骗人）。
RESOLVED_TYPES = frozenset({"literal", "constant"})


@dataclass(frozen=True)
class CoverageReport:
    """12.7 指标结果。ratio 字段是 0..1 的浮点，展示层负责乘 100。"""

    total: int = 0
    resolved: int = 0
    dynamic: int = 0
    unknown: int = 0
    ambiguous: int = 0
    missing: int = 0
    occurrences: int = 0

    # 每个桶的具体 (screen, id)（ambiguous 桶记命中的候选 screen 列表）
    resolved_refs: tuple[tuple[str, str], ...] = ()
    dynamic_refs: tuple[tuple[str, str], ...] = ()
    unknown_refs: tuple[tuple[str, str], ...] = ()
    ambiguous_refs: tuple[tuple[str, str], ...] = ()
    missing_refs: tuple[tuple[str, str], ...] = ()

    # screen marker 维度（独立分母，见模块 docstring）
    screens_total: int = 0
    screens_resolved: int = 0
    screens_missing: tuple[str, ...] = ()

    # 引用它的用例 id（**全桶填充**，不只 resolved/missing——HTML 的
    # _bucket_rows 对每个桶都查「谁引用了它」，只填两桶会让 dynamic/
    # ambiguous/missing 的用例列全空。review P3-4）
    cases_by_ref: dict = field(default_factory=dict)

    # --- 指标 ---

    @property
    def coverage(self) -> float:
        """Identifier Coverage。分母 0 → 0.0（不是 1.0：没有用例引用时
        「覆盖率满分」是误导，见 test_empty_inputs_are_zero_not_crash）。"""
        return self.resolved / self.total if self.total else 0.0

    @property
    def dynamic_ratio(self) -> float:
        return self.dynamic / self.total if self.total else 0.0

    @property
    def unknown_ratio(self) -> float:
        return self.unknown / self.total if self.total else 0.0

    @property
    def ambiguous_ratio(self) -> float:
        return self.ambiguous / self.total if self.total else 0.0

    @property
    def missing_ratio(self) -> float:
        return self.missing / self.total if self.total else 0.0

    @property
    def screen_coverage(self) -> float:
        return (self.screens_resolved / self.screens_total
                if self.screens_total else 0.0)

    def to_dict(self) -> dict:
        """JSON 形态（CLI --json / HTML 报告 / Gate summary 共用一份）。"""
        return {
            "total": self.total,
            "resolved": self.resolved,
            "coverage": self.coverage,
            "occurrences": self.occurrences,
            "dynamic": self.dynamic, "dynamic_ratio": self.dynamic_ratio,
            "unknown": self.unknown, "unknown_ratio": self.unknown_ratio,
            "ambiguous": self.ambiguous, "ambiguous_ratio": self.ambiguous_ratio,
            "missing": self.missing, "missing_ratio": self.missing_ratio,
            "resolved_refs": [f"{s}.{i}" for s, i in self.resolved_refs],
            "dynamic_refs": [f"{s}.{i}" for s, i in self.dynamic_refs],
            "unknown_refs": [f"{s}.{i}" for s, i in self.unknown_refs],
            "ambiguous_refs": [f"{s}.{i}" for s, i in self.ambiguous_refs],
            "missing_refs": [f"{s}.{i}" for s, i in self.missing_refs],
            "screens_total": self.screens_total,
            "screens_resolved": self.screens_resolved,
            "screen_coverage": self.screen_coverage,
            "screens_missing": list(self.screens_missing),
            # gap→用例映射：JSON 消费方（CI triage / 脚本）据此知道该找谁，
            # 不能只有 HTML 有（review P3-4）
            "cases_by_ref": self.cases_by_ref,
        }


def _index(metadata: dict) -> dict:
    """metadata → 查表（by_pair / by_short / prefixes）。

    by_pair key = 限定引用 (screen, id)；by_short = id → [(screen, id), ...]
    （多个即 ambiguous）；prefixes = (screen, 静态前缀, rt) 列表（12.2
    dynamic/unknown 只留前缀）。
    """
    resolved: dict[tuple[str, str], str] = {}
    prefixes: list[tuple[str, str, str]] = []   # (screen, 静态前缀, rt)
    for scr in metadata.get("screen_elements") or []:
        name = scr.get("name") or ""
        for el in scr.get("elements") or []:
            rt = el.get("resolution_type")
            a11y = el.get("accessibility_id")
            if rt in RESOLVED_TYPES and a11y:
                resolved[(name, a11y)] = rt
            elif el.get("id"):
                prefixes.append((name, el.get("id"), rt))
    by_short: dict[str, list] = {}
    for (screen, a11y) in resolved:
        by_short.setdefault(a11y, []).append((screen, a11y))
    return {"by_pair": resolved, "by_short": by_short, "prefixes": prefixes}


def collect_refs(cases: Iterable[Any]) -> list[tuple[str, str]]:
    """TestCase 列表 → [(case_id, 原始 ref 字符串)]，按出现顺序、去 screen。

    三种步骤形态（6.3）+ postcondition 都算引用。**用 hasattr 分派**而不是
    `step.action`：WaitStep/AssertionStep 没有 action 字段，直接摸会
    AttributeError（skill 坑 17 同源——那里是被 except 静默吞掉）。
    """
    out: list[tuple[str, str]] = []
    for tc in cases:
        cid = getattr(tc, "id", "?")
        for step in getattr(tc, "steps", []) or []:
            for ref in _step_refs(step):
                if ref.type == "screen":
                    continue        # screen 走 screen_coverage，不进分母
                out.append((cid, ref.id))
    return out


def _step_refs(step: Any) -> list:
    refs = []
    target = getattr(step, "target", None)
    if target is not None:
        refs.append(target)
    spec = getattr(step, "wait_for", None) or getattr(step, "assertion", None)
    if spec is not None and getattr(spec, "target", None) is not None:
        refs.append(spec.target)
    post = getattr(step, "postcondition", None)
    if post is not None and getattr(post, "target", None) is not None:
        refs.append(post.target)
    return refs


def compute_coverage(metadata: dict, cases: Iterable[Any]) -> CoverageReport:
    """12.7 主入口。纯函数：metadata（12.3）+ cases → CoverageReport。"""
    cases = list(cases)
    refs = collect_refs(cases)
    idx = _index(metadata)

    resolved: dict = {}
    dynamic: dict = {}
    unknown: dict = {}
    ambiguous: dict = {}
    missing: dict = {}
    cases_by_ref: dict[tuple[str, str], set] = {}

    for cid, ref in refs:
        # 「有没有限定名」必须用 `"." in ref` 判，不能靠 partition 的返回值
        # 真值——`"go_profile".partition(".")` 是 ("go_profile", "", "")，
        # 短名会被当成 screen=go_profile 的限定名（实锤：短名全部落 missing）
        if "." in ref:
            screen, _, ident = ref.partition(".")
            bucket_pair = (screen, ident)
            if bucket_pair in idx["by_pair"]:
                hit = ("resolved", bucket_pair)
            else:
                hit = (_prefix_hit(idx["prefixes"], screen, ident)
                       or ("missing", bucket_pair))
        else:
            ident = ref
            candidates = idx["by_short"].get(ident, [])
            if len(candidates) == 1:
                hit = ("resolved", candidates[0])
            elif len(candidates) > 1:
                # 短名跨屏同名：记歧义，并把候选 screen 拼进 pair 便于定位
                hit = ("ambiguous", (",".join(sorted(s for s, _ in candidates)),
                                     ident))
            else:
                hit = (_prefix_hit(idx["prefixes"], None, ident)
                       or ("missing", ("", ident)))
        bucket = hit[0]
        pair = hit[1]
        {"resolved": resolved, "dynamic": dynamic, "unknown": unknown,
         "ambiguous": ambiguous, "missing": missing}[bucket][pair] = True
        cases_by_ref.setdefault(pair, set()).add(cid)

    screens = {s for s in (metadata.get("screens") or []) if s}
    # screen 引用从 TargetRef.type 判（不是字符串前缀——schema 已把
    # "screen:X" 解析成 type=screen/id=X，再去前缀是重复实现）
    screen_refs = sorted({r.id for r in _all_typed_refs(cases)
                          if r.type == "screen"})
    screens_missing = tuple(s for s in screen_refs if s not in screens)

    return CoverageReport(
        total=len(resolved) + len(dynamic) + len(unknown)
        + len(ambiguous) + len(missing),
        resolved=len(resolved),
        dynamic=len(dynamic),
        unknown=len(unknown),
        ambiguous=len(ambiguous),
        missing=len(missing),
        occurrences=len(refs),
        resolved_refs=_uniq(resolved),
        dynamic_refs=_uniq(dynamic),
        unknown_refs=_uniq(unknown),
        ambiguous_refs=_uniq(ambiguous),
        missing_refs=_uniq(missing),
        screens_total=len(screen_refs),
        screens_resolved=len(screen_refs) - len(screens_missing),
        screens_missing=screens_missing,
        cases_by_ref={f"{s}.{i}": sorted(v)
                      for (s, i), v in sorted(cases_by_ref.items())},
    )


def _all_typed_refs(cases: Iterable[Any]) -> list:
    """全部 TargetRef（含 screen）。collect_refs 的无过滤版。"""
    out: list = []
    for tc in cases:
        for step in getattr(tc, "steps", []) or []:
            out += _step_refs(step)
    return out


def _prefix_hit(prefixes: list, screen: str | None, ident: str):
    """ident 是否命中某元素的静态前缀（12.2 插值形态）。返回桶或 None。

    `screen=None` 表示短名引用——跨屏找任一命中即可（短名本来就分不清屏）。
    返回的 pair 记**引用侧**的 (screen, ident)（screen 未知时留空），不是
    元素侧的 (screen, prefix)——报告要回答「用例引用了什么没解析出来」，
    展示前缀只会让人再去反查（review P2-1 同款：报告字段要是人能直接用的
    事实，不是内部中间量）。
    """
    for prefix_screen, prefix, rt in prefixes:
        if screen is not None and prefix_screen != screen:
            continue
        if prefix and ident.startswith(prefix):
            return ("dynamic" if rt == "dynamic" else "unknown",
                    (screen or prefix_screen, ident))
    return None


def _uniq(pairs) -> tuple:
    """去重且排序（dict 的键 / list 都吃；同一 id 引用多次只算一次，
    见模块 docstring）。"""
    return tuple(sorted(set(pairs)))


def format_report(report: CoverageReport) -> str:
    """人读输出（CLI / Gate 日志）。空输入也必须出一行（8.2：观测手段不得
    让「没有数据」看起来像「跑过了」。"""
    lines = [
        f"coverage: {report.coverage * 100:.1f}% "
        f"({report.resolved}/{report.total} identifiers resolved; "
        f"{report.occurrences} refs in cases)",
        f"  dynamic={report.dynamic} ({report.dynamic_ratio * 100:.1f}%) "
        f"unknown={report.unknown} ({report.unknown_ratio * 100:.1f}%) "
        f"ambiguous={report.ambiguous} "
        f"({report.ambiguous_ratio * 100:.1f}%) "
        f"missing={report.missing} ({report.missing_ratio * 100:.1f}%)",
    ]
    for label, refs, note in (
        ("DYNAMIC", report.dynamic_refs, "12.2 插值，不猜值——人工登记实例"),
        ("UNKNOWN", report.unknown_refs, "12.2 无法解析"),
        ("AMBIGUOUS", report.ambiguous_refs, "短名跨屏同名——加 Screen. 限定"),
        ("MISSING", report.missing_refs, "metadata 无此 id（漂移或未登记）"),
    ):
        if refs:
            lines.append(f"  {label} ({note}):")
            lines += [f"    {s}.{i}" for s, i in refs]
    if report.screens_total:
        lines.append(
            f"  screen_coverage: {report.screen_coverage * 100:.1f}% "
            f"({report.screens_resolved}/{report.screens_total})")
        if report.screens_missing:
            lines.append("    MISSING SCREEN: "
                         + ", ".join(report.screens_missing))
    return "\n".join(lines)