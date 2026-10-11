"""classifier.py — 规则优先的假设引擎（设计 §8.2 / F10；Task 5.2 / P3-21）。

设计 §8.2 原文（规则给不出结论才用 LLM）：

```python
- app crash 检测到        → APP_DEFECT candidate
- API 5xx + UI 正常       → ENVIRONMENT_DEFECT candidate
- Locator 漂移 + Diff 命中 → UI_DRIFT candidate
- 断言值不符              → TEST_ASSERTION_FAILURE（不走 Diagnosis）
```

## ⚠️ 事实修正（底稿修订 #6）：`crash_detected` 是**新增**的判定

**P1 不存在 APP_CRASH 判定**——`APP_CRASH` 只是 `tracer/storage.py::FAILURE_TYPES`
词表里的一个字符串，全仓没有任何检测逻辑（plan 第 31 行实锤）。设计 §8.2 写
「复用 P1 8.2 的 APP_CRASH 判定」，那是一个**不存在的函数**。本任务**新增**
`crash_detected`：复用**词表**与 trace 数据，不是不存在的函数。

## 三条规则各自需要什么（**缺证据就不出结论**）

| 规则 | 判据 | 证据 |
|---|---|---|
| 崩溃 | 现场有 `failure_type=APP_CRASH` 的步骤，**或** infra 事件里 WDA/设备不可用**且**随后有崩溃特征 | `crash_detected` |
| 环境缺陷 | 网络摘要里 5xx **且** 该步的失败分类**不是** UI 相关（UI 正常） | `backend_evidence` |
| UI 漂移 | 失败步骤的 `detail.recovery` 段里 `outcome == "DRIFT"`（P2 reconciliation 的**记录**），**或** `graph_diff` 命中该屏 | `source_change_correlated` |

⚠️ **UI_DRIFT 复用 P2 的 reconciliation 记录，不重算漂移**（plan 明文）：
`agent/recovery.py` 在恢复流水线里已经跑过 `reconcile_local` 并把结论写进
`steps.detail_json` 的 `recovery` 段（`{"outcome": "DRIFT", ...}`）。本模块
**读那条记录**——重算一遍会引入第二个判据源（而且运行时 page_source 在诊断
时早已不在）。

⚠️ **断言值不符不进诊断**（设计 §8.2 末条）：`ASSERTION_VALUE_MISMATCH` 是
P1 的既有结论（H6：直接 FAIL，无 Recovery），本模块**显式返回空列表**——
它不是「规则给不出结论」，是「这个失败类型归 P1，不该走到诊断」。
"""
from __future__ import annotations

from diagnosis.models import EvidenceItem, Hypothesis

__all__ = ["crash_detected", "is_assertion_only_failure",
           "rule_based_hypotheses"]

# 崩溃特征：`failure_type` 词表里的崩溃类 + infra 事件名里的崩溃/不可用类。
_CRASH_FAILURE_TYPES = frozenset({"APP_CRASH"})
_CRASH_INFRA_EVENTS = frozenset({"WDA_DEAD", "DEVICE_UNAVAILABLE"})

# UI 漂移：P2 reconciliation 写在 `steps.detail_json` 里的结论词。
_DRIFT_OUTCOME = "DRIFT"

# 环境缺陷：网络摘要里的服务端错误（5xx）
_SERVER_ERROR_MIN = 500


def _recovery_detail(step) -> dict:
    """步骤的 `detail.recovery` 段（P2 reconciliation 记录的落点）。

    `TraceStep.detail` 是 `detail_json` 的解析产物；`recovery` 段由
    `cli/pipeline.py` 在恢复成功后写入（`{"outcome": …, "candidates": […],
    "kind": …}`）。取不到 → 空 dict（不是错误：没有恢复记录本身就是信息）。
    """
    detail = getattr(step, "detail", None) or {}
    rec = detail.get("recovery")
    return rec if isinstance(rec, dict) else {}


def crash_detected(ctx) -> bool:
    """设计 §8.2 的「app crash 检测到」——**新增判定**（见模块 docstring）。

    判据（任一成立）：
    1. 现场**或**历史里有 `failure_type == "APP_CRASH"` 的步骤；
    2. `infra_events` 里有崩溃/设备不可用事件，**且**现场存在失败步骤
       （infra 事件单独出现不是「App 崩了」——WDA 重启后用例照常跑完是
       基础设施抖动，不是 App 缺陷）。

    返回 bool 而不是 Hypothesis：设计 §8.2 的伪代码把它写成谓词，规则函数
    按谓词组合（`rule_based_hypotheses` 负责把谓词翻成带证据的假设）。
    """
    steps = tuple(getattr(ctx, "steps", ())) + tuple(
        getattr(ctx, "history", ()))
    if any(getattr(s, "failure_type", None) in _CRASH_FAILURE_TYPES
           for s in steps):
        return True
    events = tuple(getattr(ctx, "infra_events", ()))
    crash_event = any(
        (e.get("event_type") if isinstance(e, dict) else None)
        in _CRASH_INFRA_EVENTS for e in events)
    return crash_event and bool(getattr(ctx, "failure_steps", ()))


def is_assertion_only_failure(ctx) -> bool:
    """设计 §8.2 末条：断言值不符 → P1 的既有结论，**不进诊断流程**。

    只在**全部**失败步骤都是断言值不符时返回 True——混了别的失败类型说明
    这次失败不止一个症状，断言那一条不构成「整个失败归 P1」的理由。
    """
    failed = tuple(getattr(ctx, "failure_steps", ()))
    if not failed:
        return False
    return all(getattr(s, "failure_type", None) == "ASSERTION_VALUE_MISMATCH"
               for s in failed)


def _has_drift_record(ctx) -> bool:
    steps = tuple(getattr(ctx, "steps", ())) + tuple(
        getattr(ctx, "history", ()))
    return any(_recovery_detail(s).get("outcome") == _DRIFT_OUTCOME
               for s in steps)


def _has_ui_diff_hit(ctx) -> bool:
    """`graph_diff` 里有没有**非 UNKNOWN** 的差异（UNKNOWN 是「没判定」）。

    复用 `graph.diff` 的判据（`GraphDiff.changed`），不重算。
    """
    diff = getattr(ctx, "graph_diff", None)
    return bool(diff is not None and getattr(diff, "changed", False))


def _server_error(ctx) -> bool:
    summary = getattr(ctx, "network_summary", None) or {}
    for key in ("server_errors_5xx", "status_5xx", "count_5xx"):
        value = summary.get(key)
        if isinstance(value, int) and value >= 1:
            return True
    status = summary.get("status_code")
    if isinstance(status, int) and status >= _SERVER_ERROR_MIN:
        return True
    codes = summary.get("status_codes")
    if isinstance(codes, (list, tuple)):
        return any(isinstance(c, int) and c >= _SERVER_ERROR_MIN for c in codes)
    return False


def rule_based_hypotheses(ctx) -> list[Hypothesis]:
    """设计 §8.2：先过确定性规则，**给不出结论才轮到 LLM**。

    返回的假设全部 `origin="rule_based"`，每条都带**结构化证据**（F10 第一道
    防线：没有证据的假设在 `Hypothesis` 那一层就构造不出来）。

    三条规则的**优先级**（同时命中时都产出，不是互斥——一次失败可以有多个
    可并存的解释，人工审核看全部）：
    1. 崩溃 → `APP_DEFECT`（最强：App 进程没了，别的解释都次要）；
    2. 5xx + 非 UI 失败 → `ENVIRONMENT_DEFECT`；
    3. 漂移记录 / diff 命中 → `UI_DRIFT`。

    ⚠️ **本函数不做归因判定**（F10）：它产出的是「假设 + 证据」，不是
    `failure_attribution`。把假设抬成结论的是 Task 5.3 的证据分（要过阈值）
    与**人工** triage。
    """
    if is_assertion_only_failure(ctx):
        # 设计 §8.2 末条：P1 的既有结论，不进诊断——**显式空列表**而不是
        # 「没规则命中」，调用方（analyzer）据此跳过 LLM（没有可诊断的内容）。
        return []

    out: list[Hypothesis] = []
    failed = tuple(getattr(ctx, "failure_steps", ()))

    if crash_detected(ctx):
        src = _first_crash_source(ctx)
        out.append(Hypothesis(
            statement="App 在执行过程中崩溃（进程消失）",
            confidence=0.9,
            origin="rule_based",
            evidence=[EvidenceItem(
                kind="crash_detected",
                detail="现场/历史存在 APP_CRASH 失败分类或崩溃类 infra 事件",
                source=src)]))

    if _server_error(ctx) and not _has_drift_record(ctx) and not any(
            getattr(s, "failure_type", None) in ("ELEMENT_NOT_FOUND",
                                                 "AMBIGUOUS_ELEMENT")
            for s in failed):
        out.append(Hypothesis(
            statement="服务端 5xx，UI 侧未见异常——环境缺陷",
            confidence=0.7,
            origin="rule_based",
            evidence=[EvidenceItem(
                kind="backend_evidence",
                detail=f"network_summary 报告 5xx："
                       f"{getattr(ctx, 'network_summary', {})}",
                source=f"network:{ctx.run_id}")]))

    if _has_drift_record(ctx) or _has_ui_diff_hit(ctx):
        src = ("trace:recovery_record" if _has_drift_record(ctx)
               else f"graph_diff:{getattr(ctx, 'app_build', '')}")
        out.append(Hypothesis(
            statement="定位失效由 UI 结构漂移引起（元素改名/移动/删除）",
            confidence=0.8,
            origin="rule_based",
            evidence=[EvidenceItem(
                kind="source_change_correlated",
                detail="P2 reconciliation 记录 outcome=DRIFT 或 graph_diff "
                       "命中非 UNKNOWN 差异",
                source=src)]))

    return out


def _first_crash_source(ctx) -> str:
    for step in tuple(getattr(ctx, "steps", ())) + tuple(
            getattr(ctx, "history", ())):
        if getattr(step, "failure_type", None) in _CRASH_FAILURE_TYPES:
            return f"trace:tc_run_{getattr(step, 'testcase_run_id', '?')}/" \
                   f"step_{getattr(step, 'step_index', '?')}"
    return f"infra_events:{ctx.run_id}"
