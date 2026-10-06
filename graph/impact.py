"""impact.py — Impact Analysis + UI Change Report（设计 11.3 / 12.3；Task 5.4 / P2-14）。

设计 11.3 的原文（签名与一句注释）：

```python
def affected_testcases(target_id: str, repo_index) -> list[str]:
    # 直接查询 P1 `mta lint` 已建立的 element → testcase 反向索引，不新建第二套。
```

## 「不新建第二套」是怎么落实的

P1 的反向索引原语在 `source/coverage.py`：

- `collect_case_refs(cases)` —— **唯一的引用收集点**（Task 5.4 统一：此前
  `collect_refs` 与 `iter_refs` 各写一遍 for 循环）；
- `ref_to_cases(cases)` —— 索引 fold 的**唯一实现**（`source/build_diff.py` 与
  本模块共用；build_diff 只按 12.6 口径取限定引用）。

本模块**只**在这两个原语之上做「查」与「拼」，不重新遍历用例、不重新解析引用。

⚠️ 本模块额外需要**屏引用**（`wait_for screen:X`），而 `ref_to_cases` 按
`collect_refs` 的口径把 screen 滤掉了（它进 screen_coverage 的分母）。屏引用对
影响面是必须的——一个只 `wait_for screen:X`、不碰 X 上任何元素的用例**同样**会因
X 的变化而失败。所以这里用 `collect_case_refs`（原语）自己建一份含屏的索引，
键写成 `screen:X` 以区别于元素引用。

## 影响面的判定规则（都写在这里，不散在调用方）

- **节点类差异**（屏 X）→ 引用 X 的用例：`screen:X` 引用 ∪ `X.*` 元素引用。
- **转移类差异**（`from → to`，trigger T）→ 涉及两屏的用例 ∪ 执行 T 那个元素的
  用例（trigger 形如 `tap:login_button`，元素是 `login_button`）——移除了/改了目标
  的导航，正是「执行了这个动作」的用例会失败。
- **UNKNOWN 行**的差异：影响面照算，但在报告里标**候选**（判定未定，不是「确定
  受影响」），并把 `reason` 一并给出。

## 不做的事

- **不做模糊匹配**：`target_id` 只认精确键与 `.<target_id>` 后缀（裸名形态）。
  不做子串/前缀模糊——那会把 `login_button_2` 算进 `login_button` 的影响面。
- **不做元素解析**：短名是否属于某屏由 `_index(metadata)` 决定（覆盖率那边的事）；
  影响面回答的是「谁引用了这个名字」。
"""
from __future__ import annotations

from dataclasses import dataclass

from graph.diff import UNKNOWN, DiffEntry, GraphDiff
from source.coverage import collect_case_refs

__all__ = [
    "SCREEN_KEY_PREFIX",
    "ref_index",
    "affected_testcases",
    "cases_touching_screen",
    "trigger_element",
    "ImpactRow",
    "ImpactReport",
    "build_change_report",
    "format_report",
]

SCREEN_KEY_PREFIX = "screen:"


def ref_index(cases) -> dict[str, tuple[str, ...]]:
    """引用键 → 引用它的用例 id（排序）。

    键的形态与用例里写的一致：

    - 元素限定引用 `Screen.elem`
    - 元素短名引用 `elem`（裸名）
    - 屏引用 `screen:X`（`wait_for screen:X` 的形态）

    **只遍历一次**，走 `collect_case_refs` 原语（不重新实现引用分派）。
    """
    out: dict[str, set[str]] = {}
    for cid, ref in collect_case_refs(cases):
        key = f"{SCREEN_KEY_PREFIX}{ref.id}" if ref.type == "screen" else ref.id
        out.setdefault(key, set()).add(cid)
    return {k: tuple(sorted(v)) for k, v in sorted(out.items())}


def affected_testcases(target_id: str, index) -> tuple[str, ...]:
    """设计 11.3：查 element → testcase 反向索引。

    接受 `Screen.elem`（限定）与 `elem`（裸名）两种形态：裸名匹配**任意屏上**
    同名元素的引用——用例作者常写短名，而它指的就是那个元素。只认精确与
    `.<target_id>` 后缀，不做模糊匹配。
    """
    hit: set[str] = set()
    for key, cases in index.items():
        # 正向：限定查询命中限定引用；反向：裸名查询覆盖任意屏上的同名引用
        if key == target_id or key.endswith("." + target_id):
            hit.update(cases)
        # 反向的另一半：**限定查询也覆盖裸名引用**（`HomeView.go_profile` 变了，
        # 而用例只写了 `go_profile`）。影响面是「回归范围选择」——**多包含一个
        # 用例的成本远低于漏掉一个会失败的用例**（12.2 同源：宁可可见，不可静默）。
        elif target_id.endswith("." + key):
            hit.update(cases)
    return tuple(sorted(hit))


def cases_touching_screen(screen_id: str, index) -> tuple[str, ...]:
    """引用该屏的用例：`screen:X` 引用 ∪ `X.<elem>` 元素引用。"""
    hit: set[str] = set(index.get(f"{SCREEN_KEY_PREFIX}{screen_id}", ()))
    prefix = screen_id + "."
    for key, cases in index.items():
        if key.startswith(prefix):
            hit.update(cases)
    return tuple(sorted(hit))


def trigger_element(trigger: str | None) -> str | None:
    """`tap:login_button` → `login_button`（触发动作的元素 id）。

    trigger 的形状是 `<step_type>:<target_id>`（`graph/builder` 定义）。取不到
    （空 trigger / 没有冒号）→ `None`（例如 launch_app 这类无目标动作）。
    """
    if not trigger:
        return None
    _, sep, tail = trigger.partition(":")
    return (tail or None) if sep else None


@dataclass(frozen=True)
class ImpactRow:
    """一条差异 + 它的影响面。"""

    entry: DiffEntry
    cases: tuple[str, ...] = ()

    @property
    def provisional(self) -> bool:
        """UNKNOWN 行的影响面只是**候选**（判定未定，不是「确定受影响」）。"""
        return self.entry.kind == UNKNOWN

    def render(self) -> str:
        mark = " (候选)" if self.provisional else ""
        cases = ", ".join(self.cases) or "—"
        return f"{self.entry.render()}{mark}\n        影响用例: {cases}"


@dataclass(frozen=True)
class ImpactReport:
    """UI Change Report = diff × 影响用例（设计 12.3）。"""

    app_id: str = ""
    base_build: str = ""
    build: str = ""
    rows: tuple[ImpactRow, ...] = ()
    all_cases: tuple[str, ...] = ()          # 全部用例 id（回归范围的全集）
    affected_cases: tuple[str, ...] = ()
    unmapped_cases: tuple[str, ...] = ()     # 没引用任何 element/screen → 无法判定
    reason: str | None = None

    @property
    def total_cases(self) -> int:
        """回归范围的全集大小（= 传进来的用例数，含无法判定的那些）。"""
        return len(self.all_cases) if self.all_cases else len(self.unmapped_cases)

    @property
    def affected_count(self) -> int:
        return len(self.affected_cases)

    @property
    def unaffected_cases(self) -> tuple[str, ...]:
        """没被任何一条差异命中的用例（= 本轮不必回归的那一拨）。"""
        hit = set(self.affected_cases)
        return tuple(c for c in self.all_cases if c not in hit)

    def summary(self) -> dict:
        return {"app_id": self.app_id, "base_build": self.base_build,
                "build": self.build, "rows": len(self.rows),
                "affected_cases": list(self.affected_cases),
                "affected_count": self.affected_count,
                "total_cases": self.total_cases,
                "unaffected_cases": list(self.unaffected_cases),
                "unmapped_cases": list(self.unmapped_cases),
                "reason": self.reason}


def build_change_report(diff: GraphDiff, *, cases) -> ImpactReport:
    """**纯函数**：`GraphDiff` × 用例集 → UI Change Report。

    `cases` 是 TestCase 列表（`SessionPipeline.discover()` 的产物）。影响面只由
    **用例里的引用**决定——不做设备访问、不读 metadata（解析归覆盖率那边）。
    """
    cases = list(cases)
    index = ref_index(cases)
    cited = {cid for v in index.values() for cid in v}
    unmapped = tuple(sorted(
        {str(getattr(tc, "id", "?")) for tc in cases} - cited))

    rows: list[ImpactRow] = []
    affected: set[str] = set()
    for entry in diff.entries:
        if entry.is_node:
            hit = set(cases_touching_screen(str(entry.screen_id), index))
        else:
            hit = set(cases_touching_screen(entry.from_screen or "", index))
            hit |= set(cases_touching_screen(entry.to_screen or "", index))
            el = trigger_element(entry.trigger)
            if el:
                hit |= set(affected_testcases(el, index))
        affected |= hit
        rows.append(ImpactRow(entry=entry, cases=tuple(sorted(hit))))

    all_ids = tuple(sorted({str(getattr(tc, "id", "?")) for tc in cases}))
    return ImpactReport(
        app_id=diff.app_id, base_build=diff.base_build, build=diff.build,
        rows=tuple(rows), all_cases=all_ids,
        affected_cases=tuple(sorted(affected)), unmapped_cases=unmapped,
        reason=diff.reason)


def format_report(report: ImpactReport) -> str:
    """人读的文本（CLI 与 Gate 脚本共用，避免两处各写一遍格式）。"""
    lines = [f"UI Change Report (12.3): app={report.app_id!r} "
             f"base_build={report.base_build!r} build={report.build!r}"]
    if report.reason:
        lines.append(f"  判定：UNKNOWN —— {report.reason}")
        lines.append("  下面的影响面都是**候选**（差异本身未判定）")
    if not report.rows:
        lines.append("  （无差异——影响面为空）")
    for row in report.rows:
        lines.append("  " + row.render())
    lines.append(f"  受影响用例: {report.affected_count}/{report.total_cases}"
                 + (f" → {', '.join(report.affected_cases)}"
                    if report.affected_cases else ""))
    if report.unaffected_cases:
        lines.append(f"  未受影响（本轮不必回归）: "
                    f"{', '.join(report.unaffected_cases)}")
    if report.unmapped_cases:
        # 这些用例没有被任何引用覆盖 → 无法判定影响面，必须说出来
        # （「没算出来」不能被读成「没受影响」）
        lines.append(f"  无法判定（用例未引用任何 element/screen）: "
                    f"{', '.join(report.unmapped_cases)}")
    return "\n".join(lines)
