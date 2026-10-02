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

__all__ = ["LLM_INVALID_OUTPUT", "ParsedLLMOutput", "parse_llm_output",
           "ALLOWED_ACTIONS"]

LLM_INVALID_OUTPUT = "LLM_INVALID_OUTPUT"

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
