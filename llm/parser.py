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

from testcase.schema import AssertionSpec, Postcondition, WaitSpec

__all__ = ["LLM_INVALID_OUTPUT", "MAX_REASON_CHARS", "MAX_STATEMENT_CHARS",
           "ParsedCandidateSteps", "ParsedHypotheses",
           "ParsedLLMOutput", "PlanRationale", "parse_candidate_steps",
           "parse_hypotheses", "parse_llm_output", "parse_plan_reasons",
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


# ---------------------------------------------------------------------------
# Test Generator 契约（设计 §6.1；Task 3.4 / P3-12）
# ---------------------------------------------------------------------------


@dataclass
class ParsedCandidateSteps:
    """`{"steps": [...]}` 的解析结果（生成器产出的**候选步骤序列**）。

    `steps` 是**已收窄**的原始 dict 列表：动作/条件过白名单、形态可识别。
    它们**还没**过 P1 schema——那是 `generator/validator.py::validate_schema`
    的职责（`mta lint` 的同一实现，F5）。这里只做「契约层」的收窄：把模型
    编出来的动作名/条件名挡在进入 schema 之前，好让错误信息说清「这是模型
    编的」而不是抛一坨 pydantic 校验错。

    `valid` = `steps` 非空。**空 steps 视为非法**：设计 §6.1 的产物是「候选
    步骤序列」，一个空数组不是候选（它会在 schema 层被拒，但那时错误是
    「用例没有步骤」——不如在契约层就说「模型没给步骤」）。

    `ignored_fields`：模型多输出的键（`id` / `name` / `risk` / `idempotency`…）
    ——忽略但**必须可见**（H4：模型越权的痕迹不能静默消失）。
    """

    steps: list = field(default_factory=list)
    ignored_fields: dict = field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return bool(self.steps)


# 候选步骤里允许出现的**顶层键**（三种步骤形态的判别键 + 动作步的附属键）。
# 白名单而非黑名单：黑名单会让模型新编的键（如 `"goto"`）静默落进步骤 dict，
# 然后被 `TestCase` 的 `extra="forbid"` 在很远的地方拦下——那时错误信息指向
# 「候选 schema 非法」，人看不出是模型编的键。
_STEP_KEYS = frozenset({
    "action", "target", "value", "direction", "sensitive", "postcondition",
    "wait_for", "assertion",
})
# 附属键里**不许**由模型声明的那两个（H4 / 7.4：风险与幂等由系统推导）
_FORBIDDEN_STEP_KEYS = frozenset({"risk", "idempotency"})


def _target_ok(target) -> bool:
    """`target` 必须是 `"<Screen>.<element>"` 或 `{"type","id"}` 形态。

    字符串形态由 `TargetRef._accept_sugar`（`testcase/schema.py`）解析——这里
    只判「非空 str」或「含 type/id 的 dict」，具体解析留给 schema（单一实现）。
    """
    if isinstance(target, str):
        return bool(target.strip())
    if isinstance(target, dict):
        return (isinstance(target.get("type"), str)
                and isinstance(target.get("id"), str)
                and bool(target["type"].strip()) and bool(target["id"].strip()))
    return False


def _clean_step(raw) -> tuple[dict | None, dict]:
    """单步收窄 → (合法步骤 | None, 被忽略的键)。

    `None` = 这一步不合法（调用方**整条候选作废**，不是「跳过这一步」——
    丢一步会让动作序列变成另一条用例，那是**静默改语义**）。
    """
    ignored: dict = {}
    if not isinstance(raw, dict):
        return None, {"<non-dict step>": raw}
    for k, v in raw.items():
        if k in _FORBIDDEN_STEP_KEYS:
            ignored[k] = v
        elif k not in _STEP_KEYS:
            ignored[k] = v
    known = {k: v for k, v in raw.items() if k in _STEP_KEYS}

    # 形态 1：动作步
    if "action" in known:
        action = known.get("action")
        if not isinstance(action, str):
            return None, ignored
        action = action.strip()
        if action not in ALLOWED_ACTIONS:
            # `launch_app` / `terminate_app` 不在 10.4 的恢复白名单里（那是
            # 恢复动作的白名单），但它们是合法用例动作——生成器侧另有一张表。
            from llm.prompt import GENERATOR_ACTIONS
            if action not in GENERATOR_ACTIONS:
                return None, ignored
        step: dict = {"action": action}
        if "target" in known:
            if not _target_ok(known["target"]):
                return None, ignored
            step["target"] = known["target"]
        if "value" in known:
            if not isinstance(known["value"], str):
                return None, ignored
            step["value"] = known["value"]
        if "direction" in known:
            if known["direction"] not in ("up", "down", "left", "right"):
                return None, ignored
            step["direction"] = known["direction"]
        if "sensitive" in known:
            step["sensitive"] = bool(known["sensitive"])
        if "postcondition" in known:
            post = known["postcondition"]
            if not isinstance(post, dict) or not _target_ok(post.get("target")):
                return None, ignored
            # ⚠️ **嵌套层也要过滤**（review_p3_task34 P2-4）：初版把模型的
            # 原 dict 直接 return，于是 `postcondition` / `wait_for` /
            # `assertion` 里的未知键（含 `risk`）**既不被忽略也不被记进
            # `ignored_fields`**——H4 的「越权声明必须可见」在嵌套层不成立。
            # 拦得住是**巧合**（P1 schema 的 `extra="forbid"` 兜住了），
            # 而「靠下游兜住」正是本仓反复吃亏的形态。过滤面从 schema 的
            # **字段名**派生，不手抄。
            post, sub_ignored = _strip_unknown_spec_keys(post, Postcondition)
            ignored.update({f"postcondition.{k}": v
                            for k, v in sub_ignored.items()})
            step["postcondition"] = post
        return step, ignored

    # 形态 2/3：观测步（wait_for / assertion），二者互斥
    for key in ("wait_for", "assertion"):
        if key in known:
            spec = known[key]
            if not isinstance(spec, dict) or not _target_ok(spec.get("target")):
                return None, ignored
            condition = spec.get("condition")
            from llm.prompt import GENERATOR_CONDITIONS
            if condition not in GENERATOR_CONDITIONS:
                return None, ignored
            spec, sub_ignored = _strip_unknown_spec_keys(
                spec, WaitSpec if key == "wait_for" else AssertionSpec)
            ignored.update({f"{key}.{k}": v for k, v in sub_ignored.items()})
            return {key: spec}, ignored
    return None, ignored


def _strip_unknown_spec_keys(spec: dict, model) -> tuple[dict, dict]:
    """剥掉嵌套 spec 里**不是该 schema 字段**的键 → `(干净 spec, 被剥的键)`。

    `model` 是 P1 schema 的类（`WaitSpec` / `AssertionSpec` /
    `Postcondition`），合法键从 `model.model_fields` **派生**——手抄一份字段名
    必然与 schema 漂移，而漂移方向是「schema 加字段后这里把它当越权剥掉」。
    """
    allowed = set(model.model_fields)
    kept = {k: v for k, v in spec.items() if k in allowed}
    stripped = {k: v for k, v in spec.items() if k not in allowed}
    return kept, stripped


def parse_candidate_steps(raw: str) -> ParsedCandidateSteps:
    """严格解析生成器的输出。**不抛异常**——返回 `valid=False` 的结果，调用方
    按 `LLM_INVALID_OUTPUT` 分流（与 `parse_llm_output` / `parse_plan_reasons`
    同一套纪律：任何类型不符的字段**不采纳**，「尽量解释一下」是给幻觉开后门）。

    **任一步不合法 → 整条作废**（`steps` 清空）：候选是一个动作序列，丢一步
    就是另一条用例（见 `_clean_step`）。
    """
    out = ParsedCandidateSteps()
    if not isinstance(raw, str):
        return out
    obj = _extract_json(raw)
    if not isinstance(obj, dict):
        return out

    extra = {k: v for k, v in obj.items() if k != "steps"}
    if extra:
        out.ignored_fields = extra

    steps = obj.get("steps")
    if not isinstance(steps, list) or not steps:
        return out
    cleaned: list[dict] = []
    ignored: dict = dict(out.ignored_fields)
    for idx, raw_step in enumerate(steps):
        step, step_ignored = _clean_step(raw_step)
        if step is None:
            return out                      # 整条作废
        cleaned.append(step)
        for k, v in step_ignored.items():
            ignored[f"steps[{idx}].{k}"] = v
    out.steps = cleaned
    out.ignored_fields = ignored
    return out


# ---------------------------------------------------------------------------
# Failure Diagnosis 契约（设计 §8.2；Task 5.2 / P3-21）
# ---------------------------------------------------------------------------

# `statement` 的长度上限（与 `MAX_REASON_CHARS` 同款理由：这段文字会被持久化
# 进 `bug_candidates.evidence_json` 并被审核界面渲染）。超长**截断并留 `…`**。
MAX_STATEMENT_CHARS = 300


@dataclass
class ParsedHypotheses:
    """`{"hypotheses": [{"statement": str, "confidence": float}]}` 的解析结果。

    - `hypotheses`：**已清洗**（strip 过、去空、截断到 `MAX_STATEMENT_CHARS`）
      且 confidence 已被夹进 `[0, 1]` 的条目。非法条目**直接丢掉**——LLM 的
      输出不该有能力让 `Hypothesis` 构造失败（与 `PlanRationale.reasons` 同款）。
    - `ignored_fields`：额外字段忽略但可见。
    """

    hypotheses: list[dict] = field(default_factory=list)
    ignored_fields: dict = field(default_factory=dict)


def parse_hypotheses(raw: str) -> ParsedHypotheses:
    """严格解析诊断 LLM 的输出。**不抛异常**——返回尽最大努力的合法子集。

    与 `parse_plan_reasons` 同一套纪律：`raw_decode` 增量提取、额外字段忽略但
    记录、任何类型不符的条目**不采纳**（fail-closed —— 「尽量解释一下」是给
    幻觉开后门）。`confidence` 缺失/非数 → 该条丢弃（不是默认 0.5：编一个
    置信度等于替模型表态）。
    """
    out = ParsedHypotheses()
    if not isinstance(raw, str):
        return out
    obj = _extract_json(raw)
    if not isinstance(obj, dict):
        return out

    known = {"hypotheses"}
    extra = {k: v for k, v in obj.items() if k not in known}
    if extra:
        out.ignored_fields = extra

    items = obj.get("hypotheses")
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        statement = item.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            continue
        cleaned = statement.strip()
        if len(cleaned) > MAX_STATEMENT_CHARS:
            cleaned = cleaned[:MAX_STATEMENT_CHARS] + "…"
        confidence = item.get("confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            continue
        out.hypotheses.append(
            {"statement": cleaned,
             "confidence": min(1.0, max(0.0, float(confidence)))})
    return out
