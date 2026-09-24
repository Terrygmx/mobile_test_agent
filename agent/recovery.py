"""Recovery Engine（Stage 8，设计文档 4.8.3）。

流程：budget → prompt → LLM → 解析 → risk 检查（只 LOW 自动执行）
→ 唯一性校验（0/1/2+）→ 执行 → 记录 recoveries。
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

_JSON = re.compile(r"\{.*\}", re.S)


def recover(expected_id: str, error: Exception, ex: Executor,
            source_metadata: dict, budget: LLMBudget, llm: LLMProvider,
            run_id: str | None = None, recorder: Recorder | None = None) -> dict:
    t0 = time.time()
    result = {"status": None, "llm_target": None, "confidence": 0.0,
              "risk_level": None}

    def done(status: str, accepted: bool = False) -> dict:
        result["status"] = status
        if recorder and run_id:
            recorder.record_recovery(0, "llm", result["llm_target"],
                                     result["confidence"], result["risk_level"] or "NONE",
                                     int((time.time() - t0) * 1000), accepted)
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
        expected=expected_id, error=type(error).__name__, page_source=page[:8000],
        source_elements=json.dumps(source_elements, ensure_ascii=False),
        reconciliation=json.dumps(recon, ensure_ascii=False),
        screen=source_metadata.get("screen", ""),
    )
    try:
        raw = llm.complete(prompt)
    except Exception:
        return done("LLM_PROVIDER_ERROR")

    # 3. 解析结构化输出
    m = _JSON.search(raw)
    if not m:
        return done("LLM_INVALID_OUTPUT")
    try:
        out = json.loads(m.group())
        result["llm_target"] = (out.get("target") or {}).get("value")
        result["confidence"] = float(out.get("confidence", 0))
        result["risk_level"] = out.get("risk_level")
    except Exception:
        return done("LLM_INVALID_OUTPUT")
    if not result["llm_target"]:
        return done("LLM_NO_TARGET")

    # 4. risk 检查：Phase 0 只允许 LOW 自动执行（fail closed）
    if result["risk_level"] != "LOW":
        return done("LLM_RISK_NOT_LOW")

    # 5. 唯一性校验 + 执行
    loc = [{"type": "accessibility_id", "value": result["llm_target"]}]
    try:
        ex.find(loc)
    except ElementNotFound:
        return done("LLM_TARGET_NOT_FOUND")
    except AmbiguousElement:
        return done("LLM_TARGET_AMBIGUOUS")
    ex.tap(loc)
    return done("RECOVERED", accepted=True)
