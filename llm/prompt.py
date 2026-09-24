"""Recovery prompt（Stage 8）。输出只允许一个 JSON 对象（设计文档 4.8.2）。"""

PROMPT_TEMPLATE = """你是 iOS UI 自动化测试的元素定位恢复专家。

测试步骤想操作元素 "{expected}"，但运行时找不到（{error}）。

当前页面（XCUITest 树）:
{page_source}

源码元数据中本页面的元素（accessibility id 列表）:
{source_elements}

局部 reconciliation 结果:
{reconciliation}

请从运行时元素中找出与 "{expected}" 语义等价的目标。只返回一个 JSON 对象，格式：
{{
  "target": {{"type": "accessibility_id", "value": "<运行时元素的 accessibility id>"}},
  "scope": "{screen}",
  "reason": "<一句话理由>",
  "confidence": <0.0-1.0>,
  "risk_level": "LOW" | "MEDIUM" | "HIGH"
}}
risk_level 规则：tap 普通按钮/输入框为 LOW；涉及删除/支付/提交为 HIGH。
找不到等价元素时 value 填 null，confidence 0。
"""
