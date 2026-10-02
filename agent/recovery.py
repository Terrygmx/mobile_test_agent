"""Recovery Engine（Stage 8，设计文档 4.8.3）。

流程：budget → prompt → LLM → 解析 → risk 检查（只 LOW 自动执行）
→ 唯一性校验（0/1/2+）→ 执行 → 记录 recoveries。

review 修复：P1-2 action 白名单透传（input 恢复不再误 tap）；
P0-2 step_id 由调用方传入，recoveries 与 steps 真正关联；
P2-6 JSON 正则非贪婪。
"""

from __future__ import annotations

import json
import re
import time

from executor.executor import AmbiguousElement, ElementNotFound, Executor
from llm.budget import LLMBudget
from llm.prompt import PROMPT_TEMPLATE
from llm.provider import LLMProvider
from source.reconciliation import reconcile_local
from source.screen import current_screen
from tracer.recorder import Recorder

def _extract_json(raw: str):
    """从 LLM 输出提取第一个完整 JSON 对象（review P2-6：非贪婪正则会截断嵌套 JSON）。"""
    start = raw.find("{")
    if start == -1:
        return None
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(raw[start:])
        return obj
    except json.JSONDecodeError:
        return None


# review P1-2：只放行白名单动作；input 恢复不允许升级成 tap（fail closed）
ALLOWED_ACTIONS = {"tap": {"tap"}, "input": {"input"}}


def recover(expected_id: str, error: Exception, ex: Executor,
            source_metadata: dict, budget: LLMBudget, llm: LLMProvider,
            step_id: int | None = None, recorder: Recorder | None = None,
            step_action: str = "tap", step_value: str | None = None) -> dict:
    t0 = time.time()
    result = {"status": None, "llm_target": None, "confidence": 0.0,
              "risk_level": None, "action": None}

    def done(status: str, accepted: bool = False) -> dict:
        result["status"] = status
        if recorder is not None:
            rid = recorder.record_recovery(step_id or 0, "llm", result["llm_target"],
                                           result["confidence"], result["risk_level"] or "NONE",
                                           int((time.time() - t0) * 1000), accepted)
            # review R2-3：返回精确行 id，调用方无需用全局 MAX(id) 猜
            result["rec_row_id"] = rid
        return result

    # 1. budget（fail closed）
    if not budget.try_acquire():
        return done("LLM_BUDGET_EXCEEDED")

    page = ex.page_source()
    recon = reconcile_local(expected_id, source_metadata.get("screen", ""),
                            source_metadata, page)

    # 2. prompt + 调用
    source_elements = [e.get("accessibilityId") or e["id"]
                       for e in source_metadata.get("elements", [])]
    prompt = PROMPT_TEMPLATE.format(
        expected=expected_id, action=step_action, error=type(error).__name__,
        page_source=page[:8000],
        source_elements=json.dumps(source_elements, ensure_ascii=False),
        reconciliation=json.dumps(recon, ensure_ascii=False),
        screen=source_metadata.get("screen", ""),
    )
    try:
        raw = llm.complete(prompt)
    except Exception:
        return done("LLM_PROVIDER_ERROR")

    # 3. 解析结构化输出
    out = _extract_json(raw)
    if out is None:
        return done("LLM_INVALID_OUTPUT")
    try:
        result["llm_target"] = (out.get("target") or {}).get("value")
        result["confidence"] = float(out.get("confidence", 0))
        result["risk_level"] = out.get("risk_level")
        result["action"] = out.get("action")
        # review R3-1：解析器与 prompt 契约同步——input_value 是输入内容字段；
        # 旧字段 value 保留为 legacy 兜底（防 LLM 无视新契约回填旧字段）
        result["input_value"] = out.get("input_value")
    except Exception:
        return done("LLM_INVALID_OUTPUT")
    if not result["llm_target"]:
        return done("LLM_NO_TARGET")

    # 4. risk 检查：Phase 0 只允许 LOW 自动执行（fail closed）
    if result["risk_level"] != "LOW":
        return done("LLM_RISK_NOT_LOW")

    # 4.5 review P1-2：动作白名单——LLM 不得改动作类型
    if result["action"] not in ALLOWED_ACTIONS.get(step_action, set()):
        return done("LLM_ACTION_MISMATCH")
    # review R2-2：input 恢复值必须优先用 SecretProvider 解析的原值；
    # LLM 编造的输入内容（幻觉密码等）只在原步骤无值时兜底
    if (result["action"] == "input" and not step_value
            and not (result.get("input_value") or result.get("value"))):
        return done("LLM_NO_VALUE")

    # 5. 唯一性校验 + 执行
    loc = [{"type": "accessibility_id", "value": result["llm_target"]}]
    try:
        ex.find(loc)
    except ElementNotFound:
        return done("LLM_TARGET_NOT_FOUND")
    except AmbiguousElement:
        return done("LLM_TARGET_AMBIGUOUS")
    if result["action"] == "input":
        # review R2-2：原值（secret 解析后）优先，LLM value 仅兜底
        ex.input(loc, step_value or result.get("input_value")
                 or result.get("value") or "")
    else:
        ex.tap(loc)
    return done("RECOVERED", accepted=True)


# ---------------------------------------------------------------------------
# P1 Recovery Engine（设计 9.2 / 20 节，Task 4.1 确定性半边）
#
# 与上方 P0 `recover()` 的关系：P0 函数保留至 Task 4.2——LLM 校验链迁入
# RecoveryEngine 后由引擎接管，P0 verify_stage8 回归路径随之切换（记账见
# p1_schema_review.md）。重构完成前两者并存，但**消费只有一条**：P1 管线
# 只走 RecoveryEngine，P0 函数仅供 P0 verify 脚本回归。
# ---------------------------------------------------------------------------

from agent.context import (          # noqa: E402
    EmptyExperienceStore,
    ExperienceStore,
    RecoveryContext,
    RecoveryResult,
)
from agent.policy import (           # noqa: E402
    RecoveryAction,
    RecoveryConfig,
    admitted_actions,
)

__all__ = ["RunMemo", "RecoveryEngine"]


class RunMemo:
    """9.4 作用域内复用（非学习）：同 run 内 (screen, target_id, app_build)
    的已校验恢复结果，内存态，run 结束即丢弃。**不写 Repository，不跨
    run**（H15）。P1 只存经确定性校验的条目（4.2 起 LLM 校验通过后写入）；
    每次使用仍标 RECOVERED，recoveries.kind='RUN_MEMO'。"""

    def __init__(self) -> None:
        self._memo: dict[tuple[str, str, str], dict] = {}

    def lookup(self, screen: str, target_id: str,
               app_build: str) -> dict | None:
        return self._memo.get((screen, target_id, app_build))

    def save(self, screen: str, target_id: str, app_build: str,
             strategy: dict) -> None:
        self._memo[(screen, target_id, app_build)] = strategy


def _single_element(found) -> object:
    """Executor 契约归一（2.6 实锤：单元素或列表两种替身形态）。"""
    if isinstance(found, (list, tuple)):
        if len(found) != 1:
            raise LookupError(f"expected exactly 1 element, got {len(found)}")
        return found[0]
    return found


class RecoveryEngine:
    """9.2 流水线。P1 交付确定性半边：决策表 → postcondition → settle →
    当前屏识别 → Local Reconciliation → ExperienceStore（恒空）→ LLM 钩子
    （4.2 之前恒 None）。引擎不持有 Executor/LLM——设备交互经 ctx 注入，
    LLM 调用点同样只在引擎（9.1：禁止 Executor 内 call_llm）。"""

    def __init__(self, config: RecoveryConfig | None = None,
                 repo=None,
                 experience_store: ExperienceStore | None = None,
                 run_memo: RunMemo | None = None,
                 llm=None,
                 sleep=time.sleep) -> None:
        self.config = config or RecoveryConfig()
        self.repo = repo
        self.experience_store = experience_store or EmptyExperienceStore()
        self.run_memo = run_memo or RunMemo()
        self.llm = llm            # Task 4.2：LLMProvider（含 budget 校验链）
        self._sleep = sleep

    def recover(self, ctx: RecoveryContext) -> RecoveryResult:
        allowed = admitted_actions(
            ctx.failure_type, ctx.phase, ctx.effective_idempotency,
            ctx.has_postcondition, self.config)
        if allowed is None:
            # 不可恢复 ≠ 失败被吞：维持原症状，detail 说明为什么没进流水线
            return RecoveryResult(
                recovered=False, failure_type=ctx.failure_type,
                detail={"recovery": "not_admitted"})

        stages: list[dict] = []

        # --- POSTCONDITION_CHECK（H7：非幂等 POST_DISPATCH 的唯一出口；
        #     幂等动作也先查——postcondition 成立即无需重发，矩阵 #18） ---
        if RecoveryAction.POSTCONDITION_CHECK in allowed:
            if ctx.postcondition_check is None:
                stages.append({"stage": "postcondition",
                               "outcome": "no_checker_injected"})
            else:
                try:
                    ok = bool(ctx.postcondition_check())
                except Exception as e:  # noqa: BLE001 — 观测故障不当判定
                    ok = None
                    stages.append({"stage": "postcondition",
                                   "outcome": "checker_error",
                                   "error": f"{type(e).__name__}: {e}"})
                else:
                    stages.append({"stage": "postcondition",
                                   "outcome": bool(ok)})
                if ok is True:
                    # 动作已生效，不重发（H7：未再次点击）
                    return RecoveryResult(
                        recovered=True, kind="postcondition",
                        detail={"stages": stages})
                if ok is False:
                    return RecoveryResult(
                        recovered=False,
                        failure_type="ACTION_OUTCOME_UNKNOWN",
                        detail={"stages": stages,
                                "recovery": "postcondition_not_satisfied"})

        # --- SETTLE_RETRY（9.2 第 5 步：有界 re-find + 重发，默认 1 次） ---
        if RecoveryAction.SETTLE_RETRY in allowed and ctx.refind is not None:
            for attempt in range(1, self.config.settle_max_attempts + 1):
                self._sleep(self.config.settle_wait_s)
                try:
                    element = _single_element(ctx.refind())
                except Exception as e:  # noqa: BLE001 — settle 失败进下一阶段
                    stages.append({"stage": "settle", "attempt": attempt,
                                   "outcome": f"{type(e).__name__}"})
                    break
                if ctx.redispatch is None:
                    stages.append({"stage": "settle",
                                   "outcome": "no_redispatch_callable"})
                    break
                ctx.redispatch(element)
                return RecoveryResult(
                    recovered=True, kind="settle_retry",
                    detail={"stages": stages, "settle_attempt": attempt})

        # --- LOCAL_RECONCILE（9.2 第 6-7 步：当前屏识别 + Source 子集对比）---
        recon: dict | None = None
        if RecoveryAction.LOCAL_RECONCILE in allowed \
                and ctx.page_source is not None and self.repo is not None:
            try:
                page = ctx.page_source()
                screen = current_screen(page, self.repo)
            except Exception as e:  # noqa: BLE001 — page_source/解析故障
                stages.append({"stage": "screen",
                               "outcome": f"{type(e).__name__}"})
                screen = None
            else:
                stages.append({"stage": "screen", "outcome": screen.status,
                               "screen": screen.screen})
                if screen.status == "FOUND" and ctx.source_metadata:
                    recon = reconcile_local(
                        ctx.element_id or "", screen.screen,
                        ctx.source_metadata, page)
                    stages.append({"stage": "reconcile",
                                   "outcome": recon["status"],
                                   "candidates": recon["candidates_in_runtime"]})
                # 确定性候选自动执行是设计 stretch（9.2 可选）——P1 不自动
                # 执行运行时候选：候选元素无 metadata，risk 未知按最高处理
                # （agent/risk.candidate_risk_allowed）。候选留给 4.2 LLM。

        # --- RUN_MEMO（9.4：同 run 复用，reconciliation 后、LLM 前） ---
        if RecoveryAction.RUN_MEMO in allowed and ctx.element_id:
            memo = self.run_memo.lookup(
                ctx.screen_id or "", ctx.element_id, ctx.app_build)
            if memo is not None and ctx.refind is not None:
                stages.append({"stage": "run_memo", "outcome": "hit"})
                try:
                    element = _single_element(ctx.refind())
                    if ctx.redispatch is not None:
                        ctx.redispatch(element)
                        return RecoveryResult(
                            recovered=True, kind="run_memo",
                            detail={"stages": stages})
                except Exception as e:  # noqa: BLE001
                    stages.append({"stage": "run_memo",
                                   "outcome": f"{type(e).__name__}"})
            elif memo is not None:
                stages.append({"stage": "run_memo", "outcome": "hit"})

        # --- ExperienceStore（20 节预留位：reconciliation 后、LLM 前；P1 恒 []） ---
        if ctx.element_id:
            exp = self.experience_store.lookup(
                ctx.app_build, ctx.screen_id or "", ctx.element_id)
            stages.append({"stage": "experience",
                           "outcome": len(exp) if exp else "empty"})

        # --- LLM（4.2 之前恒 None：到此为止，如实报告未恢复） ---
        if self.llm is None:
            stages.append({"stage": "llm", "outcome": "disabled"})
            return RecoveryResult(
                recovered=False, failure_type=ctx.failure_type,
                detail={"stages": stages, "recovery": "no_llm_engine"})
        # Task 4.2：LLM 调用 + 9.3 五项候选校验链在此接续。
        stages.append({"stage": "llm", "outcome": "not_implemented"})
        return RecoveryResult(
            recovered=False, failure_type=ctx.failure_type,
            detail={"stages": stages, "recovery": "no_llm_engine"})
