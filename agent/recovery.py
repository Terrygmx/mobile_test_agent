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
