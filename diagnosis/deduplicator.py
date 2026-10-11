"""deduplicator.py — Bug 去重（设计 §8.5 / F8；Task 5.5 / P3-24）。

设计 §8.5 原文：

```python
def dedup_key(bug: BugCandidateDraft) -> str:
    \"\"\"同 screen + 同 runtime diff + 同 source change → 合并，用 F8 的同一套哈希工具。\"\"\"
```

三个分量，**`failure_type` 刻意不在其中**——那正是 §8.5 这张三元组的用意：
同一个根因在三条用例上常常表现为**三种不同的失败**（一条 `ELEMENT_NOT_FOUND`、
一条 `WAIT_TIMEOUT`、一条断言炸），按失败类型去重会把它们记成三个 Bug，而
它们其实是一个。所以去重的判据是「**在同一个屏上、由同一次变更、产生了同一组
运行时差异**」，不是「报错长得一样」。

| 分量 | 来源 | 规范化 |
|---|---|---|
| `screen` | 失败发生的屏 | strip |
| runtime diff | `graph.diff.GraphDiff` → `diff_tokens()` | **丢掉 UNKNOWN**（它是「没判定」不是「有变化」）→ 去重 → **排序** |
| source change | 变更标识（build / commit） | strip |

## ⚠️ 口径登记一：为什么 diff 分量要排序

`GraphDiff.entries` 的顺序由图的遍历顺序决定，而遍历顺序**不是**语义的一部分
——同一组差异换个构建顺序就会得到不同的 entries 序列。不排序的话，「同 runtime
diff」会退化成「同 runtime diff 且恰好同序」，去重时灵时不灵（而且是**静默**
不灵：库里的 dedup_key 各不相同，看起来「本来就没有重复」）。

## ⚠️ 口径登记二：`source_change` 进 key，所以同一症状在不同 build 上是两个 Bug

设计 §8.5 明文要求它进 key。语义上也对：build 变了意味着源码变了，同一个症状
在 1026 与 1030 上可能是「还没修」与「修了但没修干净」两件事，合并会让修复
验证失去判据。代价是**跨 build 的长期跟踪**需要人工合（`affected_tests` 之外
没有跨 build 的关联列）——登记于此，不假装它完整。

## ⚠️ 口径登记三：空 diff 分量 = 「无差异」，**无法**表达「没跑过 diff」

`BugCandidateDraft.runtime_diff` 是 `list[str]`（默认 `[]`，pydantic 拒绝
`None`），`diff_tokens(None)` 也返回 `[]`——所以「跑了 diff，没有差异」与
「压根没跑 diff」在**本层**得到同一个空分量，因而同一个 key。

本层拿不到「跑没跑过 diff」这个事实（那是调用方的知识），故这是**登记给调用方
的边界**：Task 5.6 的 `mta diagnose` 在**没有 `--base-build` 时不应落
`dedup_key`**（留 `NULL`），而不是落一个会把「无 base build 的失败」与「确实
无差异的失败」误合的空分量 key。探针 9 实测了这条（把空分量替换成哨兵值
`"<none>"` 后**全绿**——说明没有测试依赖空/缺的区分，本模块确实不区分）。

## 合并语义（`merge_into`，纯函数）

合并是**只增不减**的：同一条 Bug 被更多用例命中，只会让它更可信。

| 列 | 合并规则 | 理由 |
|---|---|---|
| `affected_tests` | **并集**（保序、去重） | 「三个不同 testcase 失败但同一根因 → 合并为一个 Bug」的落点 |
| `reproduction_count` | 取 **max** | 计数是「观察到多少次复现」，不是「本次跑了多少次」 |
| `reproducible` | 三态**单调**合并：任一 `True` → `True`；否则任一 `False` → `False`；否则 `None` | 一次跑不出来**不能**抹掉已经确认过的复现（那会退化成 flaky 归零） |
| `evidence_score` | 取 **max** | 分数只由证据决定；合并不会让已有证据消失 |
| `severity` | 取**更强**的一档 | 同一条 Bug 被两种视角看到，取更严重的那个 |
| `title` / `attribution_hypothesis` | **保留先到者** | 先到的那条是人工可能已经读过的表述；改写会让「我上次看的是哪条」失去锚点 |

## F3 / F13

本模块是**纯函数 + 一个注入 store 的编排**：不 import `sqlite3` / `tracer` /
`graph.storage` / `agents.storage`（F3 的 AST 守卫扫 `diagnosis/**`），
落库经调用方传进来的 store（`agents/storage.py` 是 agent.db 的唯一写者）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from diagnosis.models import BugCandidateDraft
from source.hashing import stable_hash

__all__ = ["DEDUP_PREFIX", "DedupOutcome", "dedup_key", "diff_tokens",
           "find_duplicate", "merge_into"]

# key 的前缀：让「这是 dedup_key」在库里一眼可辨（与 `cand_` / `plan_` 同款）。
# 也是**格式版本**的锚点——若将来改规范化口径，换前缀即可让新旧 key 不互撞。
DEDUP_PREFIX = "bug"

# 严重度从强到弱（`merge_into` 取更强的一档）。用**列表**而不是 dict 排序，
# 因为档名进 key 的是设计词表（`diagnosis.models.BUG_SEVERITIES`），这里只要
# 「谁更强」一个判据。
_SEVERITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


def diff_tokens(graph_diff) -> list[str]:
    """`GraphDiff` → 规范化后的差异条目身份串。

    ⚠️ **丢掉 `UNKNOWN`**：`GraphDiff.changed` 的判据就是「有**非 UNKNOWN** 的
    差异」（UNKNOWN 是「这一面没有图，没法判」）。把它算进去会让「没跑过 diff」
    与「跑了，确实有这些差异」得到不同的 key——前者把**所有**条目记成 UNKNOWN，
    于是「同一根因、只是这次没给 base build」会变成另一个 Bug。
    这不是「少算了一个差异」，是**没有差异可算**。

    条目身份串直接用 `DiffEntry.key`（节点 = `screen_id`，转移 =
    `from->to@trigger`）——与 `absent_confirmations` 的键同域，不另造一套。
    `kind` 单独带上：`A->B@tap` 被 REMOVED 与被 CHANGED 是两种漂移。
    """
    if graph_diff is None:
        return []
    entries = getattr(graph_diff, "entries", ()) or ()
    tokens = []
    for e in entries:
        kind = getattr(e, "kind", None)
        if kind is None or kind == "UNKNOWN":
            continue
        key = getattr(e, "key", None)
        tokens.append(f"{kind}:{key if key is not None else _fallback_key(e)}")
    return sorted(set(tokens))


def _fallback_key(entry) -> str:
    """没有 `.key` 的鸭子类型条目（单测的替身）→ 从字段现拼。"""
    if getattr(entry, "screen_id", None):
        return str(entry.screen_id)
    return (f"{getattr(entry, 'from_screen', '') or ''}->"
            f"{getattr(entry, 'to_screen', '') or ''}@"
            f"{getattr(entry, 'trigger', '') or ''}")


def dedup_key(draft: BugCandidateDraft) -> str:
    """设计 §8.5 的 `dedup_key`：同 screen + 同 runtime diff + 同 source change。

    ⚠️ **输入里没有 `failure_type`**（模块 docstring 的首段）：调用方即使传了
    一个 `failure_type` 完全不同的 draft，只要三元组相同，拿到的 key 就相同
    ——这正是「同一根因的三种症状合成一条 Bug」的落点，也是本任务最该被钉住的
    判据（`tests/unit/test_bug_dedup.py::test_the_key_ignores_failure_type`）。

    规范化在**本函数内**做（`strip` / `set` / `sorted`），不依赖调用方把
    `draft.runtime_diff` 排好序——两个调用点各排一次就迟早会漂移。
    """
    tokens = sorted({str(t) for t in (draft.runtime_diff or ())})
    parts = ["screen:" + str(draft.screen or "").strip(),
             "diff:" + "|".join(tokens),
             "source:" + str(draft.source_change or "").strip()]
    return f"{DEDUP_PREFIX}_{stable_hash(parts)}"


@dataclass(frozen=True)
class DedupOutcome:
    """一次去重的结论（CLI / Gate 的读取口）。"""

    dedup_key: str
    merged: bool
    candidate_id: str
    duplicate_of: str | None = None
    affected_tests: tuple[str, ...] = ()
    audit: tuple[dict, ...] = field(default_factory=tuple)


def find_duplicate(store, dedup_key_value: str, *,
                   exclude: str | None = None) -> dict | None:
    """在库里找**同 key 的既有行**（取最早的一条 = 该 Bug 的正身）。

    `store.list_bug_candidates(dedup_key=…)` 已按 `created_at DESC` 排序，
    故取列表末尾即最早的一条。`exclude` 用于「重存自己」的场景（同 id 重跑
    不该把自己认成自己的重复）。

    ⚠️ **不按 `status` 过滤**：已 CONFIRMED 的 Bug 再被新用例命中，仍然是
    同一条 Bug（合并 `affected_tests` 比新开一条更有用）。把终态行排除在外
    会让「已确认的 Bug 又被两条用例命中」变成三条记录。
    """
    rows = store.list_bug_candidates(dedup_key=dedup_key_value)
    rows = [r for r in rows if r["candidate_id"] != exclude]
    return rows[-1] if rows else None


def merge_into(existing: dict, draft: BugCandidateDraft) -> dict:
    """`existing` 行 + 新命中的 `draft` → 合并后的**入库字段**（纯函数）。

    规则见模块 docstring 的表。返回的是 `save_bug_candidate(**…)` 的实参形状
    （不含 `status` / `reviewer` / `reviewed_at`——那些不归本函数，且
    `save_bug_candidate` 根本不收 `status`）。
    """
    merged_tests = _union(existing.get("affected_tests") or [],
                          draft.affected_tests or [])
    return {
        # 先到者的表述（模块 docstring 的表：title / attribution 不改写）
        "title": existing.get("title") or draft.title,
        "failure_type": existing.get("failure_type") or draft.failure_type or "",
        "severity": _stronger(existing.get("severity"), draft.severity),
        "attribution_hypothesis": (existing.get("attribution_hypothesis")
                                   or draft.attribution_hypothesis),
        "evidence": existing.get("evidence") or [],
        "evidence_score": max(int(existing.get("evidence_score") or 0),
                              int(draft.evidence_score or 0)),
        "reproducible": _merge_reproducible(existing.get("reproducible"),
                                            draft.reproducible),
        "reproduction_count": max(int(existing.get("reproduction_count") or 0),
                                  int(draft.reproduction_count or 0)),
        "dedup_key": existing.get("dedup_key"),
        "affected_tests": merged_tests,
    }


def _union(a, b) -> list[str]:
    """保序并集（先到者在前）——`affected_tests` 的顺序是「谁先命中」的信息。"""
    out: list[str] = []
    for item in list(a) + list(b):
        if item not in out:
            out.append(item)
    return out


def _stronger(a: str | None, b: str | None) -> str:
    """取更强的一档严重度；两者都空 → `MEDIUM`（`DEFAULT_SEVERITY`）。"""
    for sev in _SEVERITY_ORDER:
        if sev in (a, b):
            return sev
    return "MEDIUM"


def _merge_reproducible(a: bool | None, b: bool | None) -> bool | None:
    """三态**单调**合并：`True` 支配 `False` 支配 `None`。

    「一次跑不出来不能抹掉已经确认过的复现」——反过来的方向（`False` 支配）
    会让复现证据在合并时凭空消失，而这正是矩阵 #16 要防的那类静默降级。
    """
    if a is True or b is True:
        return True
    if a is False or b is False:
        return False
    return None
