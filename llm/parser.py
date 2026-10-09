"""parser.py — LLM 输出严格解析（10.4 / H4 / Task 4.2）。

唯一允许的输出是一个 JSON 对象（10.4 契约）：
  {"action", "target": {"type", "value"}, "scope", "reason", "confidence"}

规则：
- 解析失败 / 缺必填字段 / 字段类型错 → `LLM_INVALID_OUTPUT`（fail-closed，
  任何「尽量解释一下」都是给幻觉开后门）；
- **额外字段忽略并记录**（`ignored_fields`）——`risk_level` 尤其如此（H4）：
  风险来自 metadata + Guard，不由 LLM 声明。LLM 返回 risk_level: LOW
  绝不能降低拦截（矩阵 #8 的反面就是这条纪律）；
- action 白名单：tap / input / swipe / back / wait（10.4，无任意代码执行）。

H18：纯函数，离设备离网络。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

__all__ = ["LLM_INVALID_OUTPUT", "MAX_REASON_CHARS", "ParsedLLMOutput",
           "PlanRationale", "parse_llm_output", "parse_plan_reasons",
           "ALLOWED_ACTIONS"]

LLM_INVALID_OUTPUT = "LLM_INVALID_OUTPUT"

# `reasons` 单条的长度上限。存在的理由：这段文字会被**持久化**（`test_plans.tasks_json`）
# 并被 CLI / 报告渲染，一个失控的长回复会把它们撑坏。超长**截断并留 `…` 标记**——不是
# 静默丢弃（丢了等于「LLM 没解释」，截断是「解释被截了」，后者看得见）。
MAX_REASON_CHARS = 200

# 10.4：允许的 action（恢复执行端仍受「与原动作一致」白名单二次约束——
# 这里是 LLM 契约层，两层各自 fail-closed）
ALLOWED_ACTIONS = {"tap", "input", "swipe", "back", "wait"}

_REQUIRED = ("action", "target", "confidence")


@dataclass
class ParsedLLMOutput:
    action: str | None = None
    target_type: str | None = None
    target_value: str | None = None
    scope: str | None = None
    reason: str | None = None
    confidence: float | None = None
    # 额外字段：忽略但必须可见（H4 审计——LLM 越权的痕迹不能静默消失）
    ignored_fields: dict = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        """必填三件套齐且合法（confidence 越界会留 None → invalid）。"""
        return (self.action is not None and self.target_value is not None
                and self.confidence is not None)


def _extract_json(raw: str):
    """提取第一个完整 JSON 对象（P0 P2-6 教训：非贪婪正则会截断嵌套 JSON，
    用 raw_decode 增量解码）。"""
    start = raw.find("{")
    if start == -1:
        return None
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(raw[start:])
        return obj
    except json.JSONDecodeError:
        return None


def parse_llm_output(raw: str) -> ParsedLLMOutput:
    """严格解析。不合法的字段组合**不抛异常**——返回 valid=False 的结果，
    调用方据 `ignored_fields` 记录、按 LLM_INVALID_OUTPUT 分流。"""
    out = ParsedLLMOutput()
    if not isinstance(raw, str):
        return out
    obj = _extract_json(raw)
    if not isinstance(obj, dict):
        return out

    known = {"action", "target", "scope", "reason", "confidence"}
    extra = {k: v for k, v in obj.items() if k not in known}
    if extra:
        # H4：额外字段一律忽略——尤其 risk_level。记录 = 留痕，不是采纳。
        out.ignored_fields = extra

    action = obj.get("action")
    if isinstance(action, str) and action in ALLOWED_ACTIONS:
        out.action = action

    target = obj.get("target")
    if isinstance(target, dict):
        ttype = target.get("type")
        tvalue = target.get("value")
        if isinstance(ttype, str) and ttype:
            out.target_type = ttype
        if isinstance(tvalue, str) and tvalue:
            out.target_value = tvalue

    scope = obj.get("scope")
    if isinstance(scope, str) and scope:
        out.scope = scope
    reason = obj.get("reason")
    if isinstance(reason, str) and reason:
        out.reason = reason
    conf = obj.get("confidence")
    if isinstance(conf, (int, float)) and not isinstance(conf, bool) \
            and 0.0 <= float(conf) <= 1.0:
        out.confidence = float(conf)

    for name in _REQUIRED:
        if getattr(out, name if name != "target" else "target_value") is None:
            # 必填缺失（action 非白名单 / target.value 缺失 / confidence 缺失）
            return out
    return out


# ---------------------------------------------------------------------------
# Planner 解释层契约（设计 §5.2 末句；Task 2.3 / P3-07）
# ---------------------------------------------------------------------------


@dataclass
class PlanRationale:
    """`{"reasons": {case_id: str}, "order": [case_id, …]}` 的解析结果。

    **两个字段各自独立降级**（不是「一个坏就全丢」）：解释写坏了不该连顺序也丢，
    反过来也一样——它们由 `planner/planner.py` 分别消费（`reasons` 进 Plan，
    `order` 只影响同分次序）。

    - `reasons`：**已清洗**（strip 过、去空、截断到 `MAX_REASON_CHARS`）。
      非法条目**直接丢掉**而不是保留原文：`TestPlanTask.reasons` 的校验要求
      「非空且元素非空白」，留着坏值会让整个 Plan 构造失败——**LLM 的输出不该有能力
      让 Plan 建不出来**。
    - `order`：`None` = 没有可用的顺序（缺字段 / 非列表 / 有重复 / 空 / 含非 str）。
      「不完整或重复的排列」一律作废：调用方要拿它跟确定性顺序做**集合比对**，
      半个排列会让「谁被漏了」变成一个需要猜的问题。
    - `ignored_fields`：额外字段忽略但可见（与 `ParsedLLMOutput` 同款纪律）。
    """

    reasons: dict[str, str] = field(default_factory=dict)
    order: tuple[str, ...] | None = None
    ignored_fields: dict = field(default_factory=dict)


def _clean_reason(text) -> str | None:
    """单条解释的清洗：非 str / 空白 → `None`（丢弃）；超长 → 截断 + `…`。"""
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_REASON_CHARS:
        return cleaned[:MAX_REASON_CHARS - 1] + "…"
    return cleaned


def parse_plan_reasons(raw: str) -> PlanRationale:
    """严格解析 planner 解释层的输出。**不抛异常**——返回尽最大努力的合法子集，
    调用方按「有没有可用内容」决定降级（`planner/planner.py` 的规则摘要兜底）。

    与 `parse_llm_output` 同一套纪律：`raw_decode` 增量提取、额外字段忽略但记录、
    任何类型不符的字段**不采纳**（fail-closed —— 「尽量解释一下」是给幻觉开后门）。
    """
    out = PlanRationale()
    if not isinstance(raw, str):
        return out
    obj = _extract_json(raw)
    if not isinstance(obj, dict):
        return out

    known = {"reasons", "order"}
    extra = {k: v for k, v in obj.items() if k not in known}
    if extra:
        out.ignored_fields = extra

    reasons = obj.get("reasons")
    if isinstance(reasons, dict):
        for case_id, text in reasons.items():
            if not isinstance(case_id, str) or not case_id.strip():
                continue
            cleaned = _clean_reason(text)
            if cleaned is not None:
                out.reasons[case_id.strip()] = cleaned

    order = obj.get("order")
    if isinstance(order, list) and order:
        if all(isinstance(i, str) and i.strip() for i in order):
            ids = tuple(i.strip() for i in order)
            if len(set(ids)) == len(ids):        # 重复 → 不是排列，作废
                out.order = ids
    return out
