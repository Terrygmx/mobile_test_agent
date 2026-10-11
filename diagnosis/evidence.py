"""evidence.py — Evidence Score（设计 §8.3 / F10 / F13；Task 5.3 / P3-22）。

设计 §8.3 原文：「Evidence Score（**替代单纯 LLM confidence**，权重为初版）」
——权重表六项，真值源在 `agents/policy_config.py::DIAGNOSIS_EVIDENCE_WEIGHTS`
（M1 已建段；校准是改那一处，不是改这里）：

```yaml
crash_detected: 5            reproducible_3_times: 5
source_change_correlated: 3  same_issue_multiple_tests: 3
backend_evidence: 2          llm_hypothesis_only: 1
```

## 三条口径

1. **权重从 `agents/policy_config.py` 读**，不在这里写第二份字面量——设计明说
   权重是初版、「需用真实 Bug 数据校准」，校准只该改一处。
2. **排序由 Score 决定，不由 LLM confidence 决定**（本任务的核心钉子）：
   `rank_hypotheses` 的排序键是 `(-score, statement)`——`confidence`
   **不进排序键**。同证据集下把 confidence 从 0.1 改到 0.99，次序必须一模一样。
3. **`suggested_attribution` 仅当 score ≥ `evidence.attribution_threshold`
   （初版 10）才给出**，否则返回 `None`（= 保持 `UNTRIAGED`，矩阵 #16 前半）。
   注意它叫 **suggested**——F10：系统给的是建议，`failure_attribution`
   只经人工 triage（P1 `TraceStore.triage_testcase`）写。

## 口径登记：「按证据类别求和」而不是「按条目数累加」

`score_evidence` 按**类别**求和（每类最多计一次）。设计 §8.3 的权重表本身就是
「每类一项」的形状（六类六个权重），按类求和是它的字面读法；而按条目累加会
让**同一个事实被数很多遍**——同一个 `crash_detected` 记十条不会让证据更强，
只是重复。更硬的反例是 `same_issue_multiple_tests`（3 分）：若按条目累加，
一个影响 100 个用例的根因会拿到 300 分，分数随「有多少个用例撞上」无界增长，
跨候选之间就再也比不了大小。**去重求和让分数只反映「有哪些类证据」，与生产者
循环了几次无关**——这正是设计 §8.3 想让它回答的问题。
"""
from __future__ import annotations

from agents.policy_config import (ATTRIBUTION_THRESHOLD,
                                  DIAGNOSIS_EVIDENCE_WEIGHTS)
from diagnosis.models import EvidenceItem

__all__ = ["history_corroboration", "rank_hypotheses", "score_evidence",
           "suggested_attribution", "tests_hit_by_failure"]

# 证据类别 → 归因方向。**只有指向明确的类别才在表里**：其余三类说的是
# 「这个失败有多值得追」，不是「谁的责任」，映射不出来就不猜（F10）。
#   - `reproducible_3_times` / `same_issue_multiple_tests`：稳定性证据，无方向；
#   - `llm_hypothesis_only`：LLM 的推断本身，正是 F10 要压到最低权重的东西。
# 方向词取自 P1 的 `ATTRIBUTIONS`（`diagnosis/models.py` 复制 + 一致性测试钉）：
#   - 崩溃 → APP_DEFECT（App 进程没了）；
#   - 漂移 → AUTOMATION_DEFECT（UI 变了，失效的是脚本的定位，不是 App 功能）；
#   - 5xx  → ENVIRONMENT_DEFECT（后端/环境，设计 §8.2 原文明写）。
_KIND_TO_ATTRIBUTION: dict[str, str] = {
    "crash_detected": "APP_DEFECT",
    "source_change_correlated": "AUTOMATION_DEFECT",
    "backend_evidence": "ENVIRONMENT_DEFECT",
}



def tests_hit_by_failure(ctx) -> list[str]:
    """撞上**本次失败类型**的所有 `testcase_id`（现场 + 同类历史，去重排序）。

    这一个集合同时喂两处，它们是同一件事的两种说法（**不各算一遍**）：
    `affected_tests`（这个 Bug 影响哪些用例）与
    `history_corroboration` 的 `same_issue_multiple_tests`（这个 issue 命中
    了几个用例）。早先 `affected_tests` 只取 `ctx.steps[0].testcase_id`
    （**第一个**用例），于是证据说「命中 2 个用例」而 bundle 里只列 1 个——
    人工看 bundle 时对不上。
    """
    own = tuple(getattr(ctx, "steps", ()))
    failure_types = {getattr(s, "failure_type", None) for s in own
                     if getattr(s, "status", None) == "FAILED"
                     and getattr(s, "failure_type", None)}
    if not failure_types:
        return []
    tests = {getattr(s, "testcase_id", "") for s in own
             if getattr(s, "testcase_id", "")}
    for step in tuple(getattr(ctx, "history", ())):
        if getattr(step, "failure_type", None) in failure_types:
            tc = getattr(step, "testcase_id", "")
            if tc:
                tests.add(tc)
    return sorted(tests)


def history_corroboration(ctx) -> list[EvidenceItem]:
    """`same_issue_multiple_tests`（权重 3）的**唯一生产者**。

    ⚠️ **M5 Gate 2026-10-11 补上**：这个权重从 M1 建段起就在
    `DIAGNOSIS_EVIDENCE_WEIGHTS` 里，`diagnosis/context.py` 的 docstring 也
    明写「矩阵 #16 的 `same_issue_multiple_tests` 证据就从这里来」——但**全仓
    没有任何代码产出它**。后果是真机上最常见的失败形态（UI 漂移）分数上限
    只有 `source_change_correlated`(3) + `reproducible_3_times`(5) = **8**，
    永远过不了 `ATTRIBUTION_THRESHOLD`(10)：`suggested_attribution` 在真机
    数据上**不可达**，F10 的「系统给建议」退化成了「系统永远不给建议」。
    这是一条「看起来正常」的缺口——单测全绿（它们手工拼证据列表），只有
    端到端跑一次真机才发现没有生产者。

    判据：**同一个 `failure_type`** 命中了 ≥ 2 个不同的 `testcase_id`
    （现场 + 历史合起来算）。「同一个 issue」由 failure_type 定义，不用
    `ctx.history` 已经过滤过当作前提——`DiagnosisContext` 也能被直接构造
    （单测/别的调用方），生产者自己声明前提才不会拿「历史里有别的失败」
    给当前结论加分。

    「多个用例」是这条证据的全部含义，故两道反钉子都钉住**不给**的情形：
    只失败过一个用例、以及历史里只有同一个用例的重跑（后者是
    `reproducible_3_times` 的地盘，两个都给会重复记同一件事）。
    """
    tests = tests_hit_by_failure(ctx)
    if len(tests) < 2:
        return []
    own = tuple(getattr(ctx, "steps", ()))
    failure_type = next(
        (getattr(s, "failure_type", None) for s in own
         if getattr(s, "status", None) == "FAILED"
         and getattr(s, "failure_type", None)), None)
    return [EvidenceItem(
        kind="same_issue_multiple_tests",
        detail=f"同一失败类型 {failure_type} 命中 {len(tests)} 个用例："
               f"{', '.join(tests)}",
        source=f"trace_history:{getattr(ctx, 'run_id', '')}")]

def score_evidence(evidence, *, weights=None) -> int:
    """证据分：**按类别**求和（每类最多计一次，见模块 docstring 的口径登记）。

    `weights` 可注入（policy 覆盖 / 单测），缺省取真值源
    `DIAGNOSIS_EVIDENCE_WEIGHTS`。未知类别 **fail-loud**——静默加 0 分会让
    「证据提交了、其实一分没加」溜过去（`EvidenceItem.kind` 已在构造时挡了
    一道，这里是第二道：注入的 weights 可能不认识这个 kind）。
    """
    table = DIAGNOSIS_EVIDENCE_WEIGHTS if weights is None else weights
    kinds = {e.kind for e in evidence}
    unknown = sorted(kinds - set(table))
    if unknown:
        raise ValueError(
            f"证据类别不在权重表里：{unknown}（权重表键集 {sorted(table)}）"
            f"——静默加 0 分会让「证据提交了、其实一分没加」溜过去")
    return sum(int(table[k]) for k in sorted(kinds))


def suggested_attribution(hypothesis, *, weights=None,
                          threshold: int | None = None) -> str | None:
    """设计 §8.3 / 矩阵 #16 前半：**分数过阈值才给归因建议**。

    返回归因词（`ATTRIBUTIONS` 之一）或 `None`（= 保持 `UNTRIAGED`）。

    两道闸门，缺一不可：

    1. **分数够**：`score_evidence(hypothesis.evidence) ≥ threshold`
       （初版 10）。分数不够 → `None`，即使方向很明确——这正是矩阵 #16
       前半的口径（例如只有一条 `crash_detected` 得 5 分：崩溃是真的，但
       「没复现过一次」时系统仍不替你归因）。
    2. **方向明**：证据里至少有一类落在 `_KIND_TO_ATTRIBUTION` 里。方向由
       **权重最高的那类映射证据**决定（并列时按类别名排序取定）——用权重
       排序而不是写死一条 if 链：权重表是校准点，方向优先级不该是第二处
       需要同步维护的事实。

    分数在**函数内部**现算（不接收调用方传来的 score）：同一个假设在两处
    各算一次，就可能用上不同的 weights，得到一个自相矛盾的结论。

    ⚠️ 这是**建议**，不是结论（F10）：调用方把它填进
    `BugCandidateDraft.attribution_hypothesis` 一类的字段时，它仍是
    hypothesis；`failure_attribution` 只经人工 triage 写。
    """
    table = DIAGNOSIS_EVIDENCE_WEIGHTS if weights is None else weights
    limit = ATTRIBUTION_THRESHOLD if threshold is None else int(threshold)
    if score_evidence(hypothesis.evidence, weights=weights) < limit:
        return None
    mapped = {e.kind for e in hypothesis.evidence} & set(_KIND_TO_ATTRIBUTION)
    if not mapped:
        # 分数够高但方向不明（全是稳定性/LLM 证据）——仍然不猜（F10）。
        return None
    strongest = max(sorted(mapped), key=lambda k: int(table[k]))
    return _KIND_TO_ATTRIBUTION[strongest]


def rank_hypotheses(hypotheses, *, weights=None) -> list[tuple]:
    """按 **Evidence Score** 降序排（同分按 `statement` 字典序稳定）。

    ⚠️ **`confidence` 不进排序键**（本任务的核心钉子）：设计 §8.3 的标题就是
    「Evidence Score（**替代单纯 LLM confidence**）」。排序键里带上它，这条
    就破了——同证据集下 confidence 从 0.1 改到 0.99，返回次序必须一模一样。

    返回 `[(hypothesis, score), …]`：分数与假设同行，调用方不必重算（重算会
    引入第二个求值时机，两处可能用不同的 weights）。
    """
    scored = [(h, score_evidence(h.evidence, weights=weights))
              for h in hypotheses]
    return sorted(scored, key=lambda pair: (-pair[1], pair[0].statement))
