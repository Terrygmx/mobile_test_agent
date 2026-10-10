"""generator.py — 生成管线编排（设计 §6.1；Task 3.4 / P3-12）。

设计 §6.1 的主流程，逐段落地在本包 + `candidates/`：

```
Coverage Gap（coverage.py） + Bug History + Requirement
        ↓
LLM 生成候选步骤序列（llm/prompt.py::build_generation_prompt +
                      llm/parser.py::parse_candidate_steps）
        ↓
Schema Validation（validator.py::validate_schema → mta lint 同一实现，F5）
        ↓
Semantic Validation（validator.py::validate_semantic）
        ↓
Duplicate Detection（candidates/fingerprint.py::test_fingerprint，F8）
        ↓
Dry Run（dry_run.py::run_candidate，仅 sandbox，F7）
        ↓
test_candidates（candidates/test_case.py + agents/storage.py）
```

## 三条不变量

1. **每一步都留痕，且落点是可查的**：入库的进 `test_candidates`，被拒/SKIP 的
   进 `test_candidate_rejections`（带 `reason`）。矩阵 #7/#8 的「可查」由此成立。
2. **LLM 失败不改判据**（F4）：预算与熔断**全部复用** `llm/budget.py::LLMBudget`
   （P1 的同一个对象，不新写断路器）；熔断后**停止生成并如实报告**，不换模型、
   不重试、不「自由发挥」（矩阵 #10）。
3. **依赖全部注入、方向单向**（`generator → candidates → agents`）：本模块
   **不** import `agents` / `cli` / `sqlite3`——store 与 execute 都是参数。
   `planner/planner.py` 同款（那条边界有 AST 守卫钉住）。

## 一处刻意的「不报错」

**没有 gap 时不做任何事**（返回空结果 + 一条 note），**且不读图**——与
`planner.plan` 的「空 changed_files → 空 Plan 并明示」同款。空不是错误
（设计矩阵 #6 的同一态度），但必须**说出来**，否则「这次没有缺口」与
「生成器没跑起来」长得一样。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable
from uuid import uuid4

from pydantic import ValidationError

from candidates.fingerprint import test_fingerprint
from candidates.test_case import (REJECTION_REASONS, TestCandidate,
                                  dump_testcase_yaml)
from generator.coverage import UncoveredTransition
from generator.dry_run import DryRunResult, run_candidate
from generator.validator import (SEMANTIC_CONTRADICTED, schema_errors,
                                 validate_schema, validate_semantic)
from llm.parser import parse_candidate_steps
from source.hashing import stable_hash
from testcase.schema import GenerationEvidence, TestCase

__all__ = ["GENERATED_BY", "GenerationResult", "candidate_id_for",
           "generate", "name_for"]

# 候选的 `generated_by`（设计 §6.2 示例逐字：`generator_agent`）
GENERATED_BY = "generator_agent"


@dataclass
class GenerationResult:
    """一次生成的结果。

    `candidates` 与 `rejections` 是**并列**的两个产物，不是一个的成功/失败
    两半——两者都要给人工看（拒绝的进审计、入库的进审核队列）。`notes` 是
    过程叙述（预算耗尽 / 熔断 / 空 gap / 契约非法），供 CLI 与 Gate 输出。
    """

    candidates: list[TestCandidate] = field(default_factory=list)
    rejections: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    llm_calls: int = 0
    broken: bool = False

    @property
    def ok(self) -> bool:
        """**不是**「有没有候选」——空候选是完全正常的结果（缺口本来就可能
        是 0，或全部候选都被去重挡下）。这里只表示「没有非预期中断」。
        """
        return not self.broken


def _slug(text: str) -> str:
    """屏名/trigger → id 片段（只留字母数字与下划线，其余压成 `_`）。

    `LoginView` → `LoginView`；`tap:LoginView.login_button` →
    `tap_LoginView_login_button`。目的是**可读**（设计 §6.2 的
    `search_candidate_empty_result` 就是给人读的），不是可逆编码。
    """
    out = []
    for ch in text:
        out.append(ch if (ch.isalnum() or ch == "_") else "_")
    return "".join(out).strip("_")


def candidate_id_for(gap: UncoveredTransition) -> str:
    """gap → 候选 id（**内容寻址**，与 `planner.models.plan_id_for` 同一取向）。

    为什么内容寻址而不是 uuid：`save_candidate` 是 upsert（`candidate_id` 是
    主键），同一批 gap 重跑生成器必须得到**同一批 id**，否则库随每次调用线性
    增长，且「这条候选是哪次生成的」无法回答（与 `plan_id_for` 的理由逐条相同）。

    哈希片段复用 `source/hashing.py::stable_hash`（F8 的「同一套哈希工具」，
    与 `test_fingerprint` / `screen_fingerprint` 共用同一个原语）。
    """
    base = f"{gap.from_screen}_{gap.to_screen}_{gap.trigger}"
    digest = stable_hash(["candidate", gap.from_screen, gap.to_screen,
                          gap.trigger])
    return f"cand_{_slug(base)}_{digest[:8]}"


def name_for(gap: UncoveredTransition) -> str:
    """候选的人类可读名字（`testcase.name` 必填）。"""
    return f"{gap.from_screen} 到 {gap.to_screen} 的{gap.category}路径"


def _existing_fingerprints(cases: Iterable[TestCase]) -> dict[str, str]:
    """`fingerprint → testcase_id`（正式 suites 的全量）。

    ⚠️ **同指纹多例时保留第一条**（`setdefault`）：真语料**已知**有两对同指纹
    （`candidates/fingerprint.py` 的边界段，`precondition` 不入指纹的代价）。
    留第一条是确定的（`discover()` 的返回有序），而「留哪条」只影响
    `duplicate_of` 里写哪个 id——**不影响是否 SKIP**。
    """
    out: dict[str, str] = {}
    for tc in cases or ():
        out.setdefault(test_fingerprint(tc), tc.id)
    return out


def generate(gaps: Iterable[UncoveredTransition], *,
             knowledge, app_id: str,
             existing_cases: Iterable[TestCase],
             repository, secrets, store,
             llm, budget,
             execute: Callable[[TestCase], Any],
             env_kind, device_type,
             suite: str | None = None,
             requirement: str = "",
             bug_history: list[dict] | None = None,
             generated_by: str = GENERATED_BY) -> GenerationResult:
    """编排（设计 §6.1）。**每段判据都不在这里重写**——全部转调上面的模块。

    | 参数 | 说明 |
    |---|---|
    | `gaps` | `coverage_gap(...)` 的产物（已按 (from,to,trigger) 排序 → 生成顺序确定） |
    | `knowledge` | `KnowledgeSources`（**只能**由 `knowledge.build_knowledge` 装配，F3）；只用 `graph_query` 与 `trace_history` |
    | `app_id` | `graph_query` 的作用域（与 `P2KnowledgeSources` 的 trace app_id 必须一致，否则它 fail-loud） |
    | `existing_cases` | 正式 suites 的用例（去重的「已有用例」一侧） |
    | `repository` / `secrets` | 传给 `lint`（F5：同一套 schema 校验） |
    | `store` | `SQLiteAgentStore`（写 `test_candidates` / `test_candidate_rejections`） |
    | `llm` / `budget` | **两个都给**才调 LLM（与 `planner.plan` 同款门槛）；`budget` 是 P1 的 `LLMBudget` |
    | `execute` | Dry Run 的执行体（真栈或 Fake，见 `dry_run.py`） |
    | `env_kind` / `device_type` | Dry Run 的闸门输入（矩阵 #9） |
    """
    result = GenerationResult()
    gap_list = list(gaps)
    if not gap_list:
        result.notes.append(
            "没有 coverage gap → 不生成候选（空不是错误，与矩阵 #6 的空 Plan 同款）")
        return result

    if llm is None or budget is None:
        # 与 `planner.plan` 的门槛逐字一致：**两个都给**才调用。只给一个时
        # 静默造预算对象会让「这次调用有没有被记账」有两个来源。
        result.notes.append(
            "未提供 llm+budget → 不生成候选（生成必须有 LLM；"
            "确定性半边无法凭空产出步骤序列）")
        return result

    # 图只读一次（F3 的单一入口）。放在这里而不是每轮循环里：`graph_query`
    # 会读整个 trace 库，逐 gap 调用是 N 次全表扫描。
    graph = knowledge.graph_query(app_id)

    fp_index = _existing_fingerprints(existing_cases)
    for cand in store.list_candidates(review_status="PENDING"):
        if cand.get("fingerprint"):
            fp_index.setdefault(cand["fingerprint"], cand["candidate_id"])
    result.notes.append(
        f"去重基准：正式用例 {len(list(existing_cases or ()))} 条 + "
        f"未决候选 {sum(1 for c in store.list_candidates(review_status='PENDING'))} 条")

    for gap in gap_list:
        # --- F4：预算与熔断（P1 的同一个对象，不新写） ---
        if not budget.try_acquire():
            reason = ("LLM 熔断（连续 INVALID_OUTPUT 达阈值）"
                      if budget.broken else "LLM 预算耗尽")
            result.notes.append(
                f"{reason} → 停止生成（矩阵 #10：不重试、不换模型、不自由发挥）；"
                f"已生成 {len(result.candidates)} 条候选")
            result.broken = budget.broken
            break

        # --- LLM → 契约层解析 ---
        from llm.prompt import build_generation_prompt
        prompt = build_generation_prompt(gaps=[_gap_dict(gap)],
                                         requirement=requirement,
                                         bug_history=bug_history,
                                         suite=suite or "")
        try:
            raw = llm.complete(prompt)
        except Exception as e:  # noqa: BLE001 — 网络/网关错误也是「这次调用没成功」
            budget.record_failure()
            result.notes.append(
                f"{candidate_id_for(gap)}: LLM 调用失败（{type(e).__name__}: {e}）"
                f"→ 本条跳过，连续失败计数 +1")
            continue
        result.llm_calls += 1

        parsed = parse_candidate_steps(raw)
        if not parsed.valid:
            # 契约层非法 = 10.4 的 LLM_INVALID_OUTPUT（fail-closed）。**不建
            # 候选、不写留痕表**：留痕表的三个 reason 都是「候选被拒」，而这里
            # 压根没有候选。它的可见性是 notes + `budget.broken`（矩阵 #10）。
            budget.record_failure()
            result.notes.append(
                f"{candidate_id_for(gap)}: LLM 输出不合契约"
                f"（LLM_INVALID_OUTPUT）→ 本条跳过"
                + ("（连续失败已达阈值 → 熔断）" if budget.broken else ""))
            continue
        budget.record_success()

        candidate_id = candidate_id_for(gap)
        evidence = GenerationEvidence(
            coverage_gap=[f"{gap.from_screen} -> {gap.to_screen}"],
            bug_history=[f"{h.get('testcase_id')}: {h.get('failure_type')}"
                         for h in (bug_history or [])],
            source_refs=[])
        yaml_text = None
        try:
            tc = _build_candidate(candidate_id, gap, parsed.steps, evidence,
                                  suite=suite, generated_by=generated_by)
            yaml_text = dump_testcase_yaml(tc)
        except (ValidationError, ValueError) as e:
            _reject(store, result, candidate_id=candidate_id,
                    reason="SCHEMA_INVALID",
                    detail={"error": f"{type(e).__name__}: {e}",
                            "steps": parsed.steps},
                    fingerprint=None)
            continue

        # --- 矩阵 #7：lint（F5，同一实现）---
        issues = validate_schema(tc, repository=repository, secrets=secrets)
        errors = schema_errors(issues)
        if errors:
            _reject(store, result, candidate_id=candidate_id,
                    reason="SCHEMA_INVALID",
                    detail={"issues": [{"code": i.code, "message": i.message,
                                        "step_index": i.step_index}
                                       for i in errors]},
                    fingerprint=None)
            continue

        # --- 语义校验（validator.py 的口径：与**已知观测**矛盾才拒）---
        verdict = validate_semantic(tc, graph=graph)
        if verdict.status == SEMANTIC_CONTRADICTED:
            _reject(store, result, candidate_id=candidate_id,
                    reason="SEMANTIC_REJECTED",
                    detail={"reason": verdict.reason,
                            "observed": list(verdict.observed)},
                    fingerprint=None)
            continue

        # --- 矩阵 #8：指纹去重（F8，精确哈希）---
        fp = test_fingerprint(tc)
        hit = fp_index.get(fp)
        if hit is not None:
            _reject(store, result, candidate_id=candidate_id,
                    reason="DUPLICATE",
                    detail={"note": f"指纹与 {hit} 相同（同语义不同命名也算重复）"},
                    fingerprint=fp, duplicate_of=hit)
            continue

        # --- 矩阵 #9：Dry Run（闸门在 run_candidate 内部，非 sandbox 会抛）---
        dry = run_candidate(tc, env_kind=env_kind, device_type=device_type,
                            execute=execute)
        row = store.save_candidate(
            candidate_id, testcase_yaml=yaml_text,
            generation_evidence=evidence.model_dump(mode="json"),
            generated_by=generated_by, fingerprint=fp,
            dry_run_status=dry.status)
        result.candidates.append(TestCandidate.from_store_dict(row))
        # 本条已入库 → 立刻进去重基准（同批次内的重复候选也要被挡）
        fp_index.setdefault(fp, candidate_id)

    # ⚠️ **收尾必须重读 `budget.broken`**：熔断可能发生在**最后一次**迭代的
    # `record_failure()` 里——那时循环已到末尾，`if not try_acquire()` 那一支
    # 再也轮不到，`result.broken` 会停在 False，而预算其实已经熔断。
    # 「熔断了但报告说没熔断」正是本仓最防的那类「看起来正常」。
    result.broken = budget.broken
    result.notes.append(
        f"生成完成：入库 {len(result.candidates)} 条候选，"
        f"拒绝/SKIP {len(result.rejections)} 条，LLM 调用 {result.llm_calls} 次"
        + ("（**已熔断**）" if result.broken else ""))
    return result


def _gap_dict(gap: UncoveredTransition) -> dict:
    return {"from_screen": gap.from_screen, "to_screen": gap.to_screen,
            "trigger": gap.trigger, "category": gap.category,
            "observed_count": gap.observed_count}


def _build_candidate(candidate_id: str, gap: UncoveredTransition, steps,
                     evidence: GenerationEvidence, *, suite: str | None,
                     generated_by: str) -> TestCase:
    """步骤 dict 列表 → Candidate `TestCase`（F5：**就是** P1 TestCase）。

    `status="CANDIDATE"` 与三个溯源字段是设计 §6.2 的全部新增面；其余与正式
    用例逐字相同（`schema_version="0.2"`）。
    """
    data = {"schema_version": "0.2",
            "id": candidate_id,
            "name": name_for(gap),
            "status": "CANDIDATE",
            "generated_by": generated_by,
            "generation_evidence": evidence.model_dump(mode="json"),
            "steps": steps}
    if suite:
        data["suite"] = suite
    return TestCase.model_validate(data)


def _reject(store, result: GenerationResult, *, candidate_id: str, reason: str,
            detail: dict, fingerprint: str | None,
            duplicate_of: str | None = None) -> None:
    """写一条拒绝/SKIP 留痕（矩阵 #7/#8 的「可查」）。

    `rejection_id` 用 uuid（不是内容寻址）：同一个候选在同一次生成里被拒两次
    是**两件事**（比如先 lint 挡下、修好后撞指纹），合并成一行会让审计看不全
    ——`save_rejection` 因此是 append-only（见 `agents/storage.py`）。
    """
    assert reason in REJECTION_REASONS, reason
    row = store.save_rejection(
        f"rej_{uuid4().hex}", reason=reason, candidate_id=candidate_id,
        fingerprint=fingerprint, duplicate_of=duplicate_of, detail=detail)
    result.rejections.append(row)
