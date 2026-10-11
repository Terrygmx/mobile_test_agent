"""Task 5.5（P3-24）：Bug 去重（设计 §8.5 / F8）。

设计 §8.5 原文：`dedup_key(bug) = 同 screen + 同 runtime diff + 同 source change`，
用 F8 的同一套哈希工具。plan 的失败测试要求：「三个不同 testcase 失败但同一根因
→ 合并为一个 Bug Candidate，`affected_tests` 累加而不是生成三条记录」（矩阵 #16
后半）。

七块：

1. **三元组语义**：三个分量各自能分开；`failure_type` / `title` / `evidence` 等
   **不进 key**——这是「同一根因的三种症状合并成一条」的落点。
2. **`runtime_diff` 的规范化**：顺序无关、去重、丢 `UNKNOWN`。
3. **`GraphDiff` → tokens**：真 diff 对象接进来（不是手搓字符串列表）。
4. **合并语义**（`merge_into` 纯函数）：并集 / max / 三态单调 / 保留先到者。
5. **`find_duplicate`**：取最早一条、不按 status 过滤、能排除自己。
6. **落库端到端**：真 `SQLiteAgentStore`，三条用例 → 一条 Bug + `affected_tests`
   累加。
7. **F3 / F8 守卫**：不直连数据源；与 `test_fingerprint` / `screen_fingerprint`
   用**同一个**哈希原语（import 级断言）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agents.storage import SQLiteAgentStore
from diagnosis.deduplicator import (DEDUP_PREFIX, dedup_key, diff_tokens,
                                    find_duplicate, merge_into)
from diagnosis.models import BugCandidateDraft, EvidenceItem
from graph.diff import ADDED, CHANGED, REMOVED, UNKNOWN, DiffEntry, GraphDiff

HOME = "HomeView"
BUILD = "1026"


def _draft(**over) -> BugCandidateDraft:
    kw = dict(title="首页到详情页的转移消失", screen=HOME,
              source_change=BUILD, runtime_diff=["ADDED:HomeView->Detail@tap:go"])
    kw.update(over)
    return BugCandidateDraft(**kw)


# --- ① 三元组语义 -------------------------------------------------------------


def test_the_key_is_stable_for_the_same_triple():
    """同一三元组 → 同一 key（跨进程稳定的前提，F8 的 `stable_hash` 保证）。"""
    assert dedup_key(_draft()) == dedup_key(_draft())
    assert dedup_key(_draft()).startswith(DEDUP_PREFIX + "_")


@pytest.mark.parametrize("field,value", [
    ("screen", "ProfileView"),
    ("source_change", "1030"),
    ("runtime_diff", ["ADDED:HomeView->Other@tap:go"]),
])
def test_each_component_of_the_triple_separates_the_key(field, value):
    """三个分量**各自**都能把 key 分开——缺一个就等于少一道判据。"""
    assert dedup_key(_draft(**{field: value})) != dedup_key(_draft())


def test_the_key_ignores_failure_type():
    """**本任务的核心判据**：同一根因的三种症状必须撞成同一个 key。

    三条 draft 的 `failure_type` 完全不同（这正是同一根因在三台不同用例上的
    样子），但三元组相同 → 一条 Bug。若有人把 `failure_type` 加进 key，这条
    会红，而库里会多出两条本不该存在的 Bug。
    """
    keys = {
        dedup_key(_draft(failure_type="ELEMENT_NOT_FOUND",
                         title="找不到 go 按钮")),
        dedup_key(_draft(failure_type="WAIT_TIMEOUT", title="详情页没出现")),
        dedup_key(_draft(failure_type="ASSERTION_VALUE_MISMATCH",
                         title="标题不对")),
    }
    assert len(keys) == 1, f"三种症状该撞成一个 key，实得 {keys}"


def test_the_key_ignores_presentation_and_evidence_fields():
    """`title` / `severity` / `evidence` / `attribution` / `reproducible` 不进 key。

    它们是「这条 Bug 现在多可信 / 叫什么」，不是「它是什么」——让它们进 key
    会让「同一条 Bug 多了一条证据」变成一条新 Bug。
    """
    base = dedup_key(_draft())
    assert dedup_key(_draft(
        title="完全不同的表述", severity="CRITICAL",
        attribution_hypothesis="APP_DEFECT", evidence_score=9,
        reproducible=True, reproduction_count=3,
        evidence=[EvidenceItem(kind="crash_detected", detail="x",
                               source="trace:run_1")])) == base


def test_the_key_ignores_the_draft_s_own_dedup_key():
    """传入的 `dedup_key` 字段本身不参与计算（它是**产物**不是输入）。

    不防这一手的话，合并时把既有 key 写回 draft 再算一遍会得到双重哈希——
    key 越滚越长且与原始三元组脱钩。
    """
    assert dedup_key(_draft(dedup_key="bug_deadbeef")) == dedup_key(_draft())


def test_whitespace_is_normalised_on_both_sides_of_the_triple():
    """`screen` / `source_change` 两端空白不影响 key（调用方不必自己 strip）。"""
    assert dedup_key(_draft(screen=" HomeView ")) == dedup_key(_draft())
    assert dedup_key(_draft(source_change=" 1026 ")) == dedup_key(_draft())


# --- ② runtime_diff 的规范化 --------------------------------------------------


def test_runtime_diff_order_does_not_matter():
    """⚠️ **口径登记一**：diff 条目的顺序不是语义的一部分。

    `GraphDiff.entries` 的顺序由图的遍历顺序决定——不排序的话「同 runtime diff」
    会退化成「同 diff 且恰好同序」，去重**静默**失效（库里 key 各不相同，
    看起来像「本来就没有重复」）。
    """
    a = _draft(runtime_diff=["ADDED:A->B@tap", "REMOVED:C"])
    b = _draft(runtime_diff=["REMOVED:C", "ADDED:A->B@tap"])
    assert dedup_key(a) == dedup_key(b)


def test_runtime_diff_duplicates_are_collapsed():
    """重复条目（同一差异被两条路径记了两次）不该改变 key。"""
    once = _draft(runtime_diff=["ADDED:A->B@tap"])
    twice = _draft(runtime_diff=["ADDED:A->B@tap", "ADDED:A->B@tap"])
    assert dedup_key(once) == dedup_key(twice)


def test_an_empty_diff_is_a_valid_component():
    """没有差异（比如断言类失败）→ 空分量（**不是**「没有这个分量」）。

    空分量与「有差异」的 key 不同（这一条钉住分量确实参与哈希），而空分量本身
    是**稳定**的（两次空 diff 得同一 key）。

    ⚠️ **边界登记（探针 9 实测）**：`runtime_diff=None` 在 `BugCandidateDraft`
    上是**非法**的（`list[str]`，pydantic 拒绝 `None`；默认 `[]`），所以「跑了
    diff 但没有差异」与「压根没跑 diff」在**本层**无法区分——`diff_tokens(None)`
    也返回 `[]`。也就是说：诊断没给 `--base-build` 时算出的 key，会与「确实
    无差异」的 Bug 相同。
    这**不是**本模块能修的（本层拿不到「跑没跑过 diff」这个事实），故登记给
    调用方：Task 5.6 的 `mta diagnose` 应在**没有 base build 时不落 dedup_key**
    （留 `NULL`），而不是落一个会误合的空分量 key。
    """
    empty = _draft(runtime_diff=[])
    assert dedup_key(empty) != dedup_key(_draft())      # 与「有差异」不同
    assert dedup_key(empty) == dedup_key(_draft(runtime_diff=[]))
    # 空分量 key 也稳定（两次空 diff 得同一 key）
    assert dedup_key(_draft(runtime_diff=[])) == dedup_key(
        _draft(runtime_diff=[]))


def test_diff_tokens_drops_unknown_entries():
    """`UNKNOWN` 是「没判定」，不是「有变化」——不进 tokens。

    不丢的话，「同一根因，只是这次没给 base build」（整份 diff 全是 UNKNOWN）
    会得到另一个 key，于是同一条 Bug 被记两次。
    """
    diff = GraphDiff(entries=(
        DiffEntry(kind=ADDED, from_screen="A", to_screen="B", trigger="tap"),
        DiffEntry(kind=UNKNOWN, screen_id="C"),
    ))
    assert diff_tokens(diff) == ["ADDED:A->B@tap"]


def test_diff_tokens_covers_nodes_and_transitions():
    """节点与转移都能出 token，且 `kind` 参与（同一条边 REMOVED vs CHANGED 不同）。"""
    node = DiffEntry(kind=REMOVED, screen_id="GoneView")
    edge = DiffEntry(kind=CHANGED, from_screen="A", to_screen="B",
                     trigger="tap")
    assert diff_tokens(GraphDiff(entries=(node,))) == ["REMOVED:GoneView"]
    assert diff_tokens(GraphDiff(entries=(edge,))) == ["CHANGED:A->B@tap"]
    assert diff_tokens(None) == []
    assert diff_tokens(GraphDiff(entries=())) == []


def test_the_key_is_built_from_a_real_graph_diff():
    """真 `GraphDiff` 接进来——两个条目顺序不同、含 UNKNOWN 的 diff 得到同一 key。"""
    def _diff(order):
        entries = {ADDED: DiffEntry(kind=ADDED, from_screen="A",
                                    to_screen="B", trigger="tap"),
                   UNKNOWN: DiffEntry(kind=UNKNOWN, screen_id="C")}
        return GraphDiff(entries=tuple(entries[k] for k in order))

    assert dedup_key(_draft(runtime_diff=diff_tokens(_diff([ADDED, UNKNOWN])))) \
        == dedup_key(_draft(runtime_diff=diff_tokens(
            _diff([UNKNOWN, ADDED]))))


# --- ③ 合并语义（纯函数） -----------------------------------------------------


def _existing(**over) -> dict:
    kw = dict(candidate_id="bug_1", title="先到的表述",
              failure_type="ELEMENT_NOT_FOUND", severity="MEDIUM",
              attribution_hypothesis="UNTRIAGED",
              evidence=[{"kind": "crash_detected", "detail": "d", "source": "s"}],
              evidence_score=5, reproducible=None, reproduction_count=0,
              dedup_key="bug_x", affected_tests=["login_001"])
    kw.update(over)
    return kw


def test_merge_unions_affected_tests_in_order():
    """**矩阵 #16 后半的判据**：三个不同用例同一根因 → 一条 Bug，测试名累加。"""
    out = merge_into(_existing(affected_tests=["login_001"]),
                     _draft(affected_tests=["checkout_001", "login_001"]))
    assert out["affected_tests"] == ["login_001", "checkout_001"], \
        "保序并集：先到者在前、重复不叠加"


def test_merge_takes_the_max_reproduction_count():
    """计数是「观察到多少次复现」——合并取 max，不是相加（相加会随命中次数膨胀）。"""
    out = merge_into(_existing(reproduction_count=3),
                     _draft(reproduction_count=1))
    assert out["reproduction_count"] == 3


def test_merge_is_monotone_on_the_three_state_reproducible():
    """⚠️ 三态**单调**：一次跑不出来**不能**抹掉已确认的复现。"""
    assert merge_into(_existing(reproducible=True),
                      _draft(reproducible=False))["reproducible"] is True
    assert merge_into(_existing(reproducible=False),
                      _draft(reproducible=True))["reproducible"] is True
    assert merge_into(_existing(reproducible=None),
                      _draft(reproducible=False))["reproducible"] is False
    assert merge_into(_existing(reproducible=None),
                      _draft(reproducible=None))["reproducible"] is None


def test_merge_takes_the_stronger_severity():
    assert merge_into(_existing(severity="LOW"),
                      _draft(severity="CRITICAL"))["severity"] == "CRITICAL"
    assert merge_into(_existing(severity="HIGH"),
                      _draft(severity="MEDIUM"))["severity"] == "HIGH"


def test_merge_keeps_the_first_comers_presentation():
    """`title` / `attribution` 保留先到者——人工可能已经读过那条表述。"""
    out = merge_into(_existing(title="先到的表述",
                               attribution_hypothesis="UNTRAIGED"
                               if False else "UNTRIAGED"),
                     _draft(title="后来的表述",
                            attribution_hypothesis="APP_DEFECT"))
    assert out["title"] == "先到的表述"
    assert out["attribution_hypothesis"] == "UNTRIAGED"


def test_merge_keeps_the_existing_evidence_and_score_ceiling():
    """证据不因合并消失；分数取 max（合并不会让已有证据打折）。"""
    out = merge_into(_existing(evidence_score=5),
                     _draft(evidence_score=9))
    assert out["evidence_score"] == 9
    assert out["evidence"] == _existing()["evidence"], "既有证据原样保留"


def test_merge_output_is_the_save_signature_shape():
    """返回的是 `save_bug_candidate(**…)` 的实参形状——**不含** `status`。

    `save_bug_candidate` 不收 `status`（状态推进的唯一入口是 Task 5.6 的状态机）；
    若本函数返回里带上它，调用方会 `TypeError`——那正是「旁路被删掉而不是加
    断言」的形态。
    """
    out = merge_into(_existing(), _draft())
    assert "status" not in out
    assert set(out) == {"title", "failure_type", "severity",
                        "attribution_hypothesis", "evidence", "evidence_score",
                        "reproducible", "reproduction_count", "dedup_key",
                        "affected_tests"}
    import inspect

    from agents.storage import SQLiteAgentStore as _S
    params = set(inspect.signature(_S.save_bug_candidate).parameters)
    assert set(out) <= params, f"合并产物有 save 不收的键：{set(out) - params}"


# --- ④ find_duplicate ---------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return SQLiteAgentStore(tmp_path / "agent.db")


def _save(store, cid, **over):
    kw = dict(title="t", failure_type="ELEMENT_NOT_FOUND", evidence=[],
              dedup_key="bug_same", affected_tests=[cid])
    kw.update(over)
    return store.save_bug_candidate(cid, **kw)


def test_find_duplicate_returns_the_earliest_row(store):
    """同 key 多行时取**最早**的一条 = 该 Bug 的正身（后到者是 DUPLICATE）。"""
    _save(store, "bug_a")
    _save(store, "bug_b")
    assert find_duplicate(store, "bug_same")["candidate_id"] == "bug_a"
    assert find_duplicate(store, "bug_none") is None


def test_find_duplicate_does_not_filter_by_status(store):
    """已 CONFIRMED 的 Bug 再被命中仍是同一条（不按 status 过滤）。"""
    _save(store, "bug_a")
    # 直接改状态模拟人工已确认（`status` 的唯一写路径归 Task 5.6 的状态机）
    with store._write_tx() as conn:
        conn.execute("UPDATE bug_candidates SET status='CONFIRMED'"
                     " WHERE candidate_id='bug_a'")
    assert find_duplicate(store, "bug_same")["candidate_id"] == "bug_a"


def test_find_duplicate_can_exclude_itself(store):
    """重存自己时不该把自己认成自己的重复。"""
    _save(store, "bug_a")
    assert find_duplicate(store, "bug_same", exclude="bug_a") is None


# --- ⑤ 落库端到端：三条用例 → 一条 Bug ----------------------------------------


def test_three_failing_testcases_become_one_bug_with_accumulated_tests(store):
    """**矩阵 #16 后半的端到端**（plan 的失败测试原文）。

    三条**不同用例**（`failure_type` 也各不相同）在同一屏、同一次变更下失败，
    运行时差异相同 → 一条 Bug，`affected_tests` 累加。
    """
    diff = ["ADDED:HomeView->ErrorView@tap:go"]
    for tc, ftype in (("login_001", "ELEMENT_NOT_FOUND"),
                      ("checkout_001", "WAIT_TIMEOUT"),
                      ("search_001", "ASSERTION_VALUE_MISMATCH")):
        draft = _draft(title=f"{tc} 失败", failure_type=ftype,
                       runtime_diff=diff, affected_tests=[tc])
        key = dedup_key(draft)
        dup = find_duplicate(store, key)
        if dup is None:
            _save(store, "bug_" + key[-8:], dedup_key=key,
                  affected_tests=[tc])
        else:
            _save(store, dup["candidate_id"], **merge_into(dup, draft))

    rows = store.list_bug_candidates()
    assert len(rows) == 1, f"三条用例同一根因该只有一条 Bug，实得 {len(rows)}"
    assert rows[0]["affected_tests"] == ["login_001", "checkout_001",
                                         "search_001"]


def test_a_different_screen_is_a_different_bug(store):
    """同一次变更、同一失败类型，但**不同屏** → 两条 Bug（三元组分开）。"""
    a = _draft(screen="HomeView", affected_tests=["login_001"])
    b = _draft(screen="ProfileView", affected_tests=["login_001"])
    _save(store, "bug_a", dedup_key=dedup_key(a))
    _save(store, "bug_b", dedup_key=dedup_key(b))
    assert len(store.list_bug_candidates()) == 2


# --- ⑥ F3 / F8 守卫 -----------------------------------------------------------


def test_the_deduplicator_does_not_touch_data_sources_directly():
    """F3：`diagnosis/deduplicator.py` 不直连数据源（store 是注入的）。"""
    path = Path(__file__).resolve().parents[2] / "diagnosis" / "deduplicator.py"
    forbidden = ("sqlite3", "tracer", "graph.storage", "experience.store",
                 "source.sqlite_tx", "agents.storage")
    bad: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        mods: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.append(node.module)
        elif isinstance(node, ast.Import):
            mods.extend(a.name for a in node.names)
        bad += [m for m in mods
                if any(m == f or m.startswith(f + ".") for f in forbidden)]
    assert not bad, f"diagnosis/deduplicator.py 直连数据源（F3）：{sorted(bad)}"


def test_the_dedup_key_shares_the_f8_hash_primitive():
    """F8：与 `test_fingerprint` / `screen_fingerprint` 用**同一个**哈希原语。

    「同一个函数对象」是这条断言的判据——不是「结果看起来一致」（那样两个各写
    各的 sha256 也能过）。
    """
    import diagnosis.deduplicator as dd
    from candidates import fingerprint as fp
    from source import hashing
    from source import screen as screen_mod
    assert dd.stable_hash is hashing.stable_hash
    assert fp.stable_hash is hashing.stable_hash
    assert getattr(screen_mod, "stable_hash", None) is hashing.stable_hash
