"""risk.py — Recovery 侧的风险门控（9.3 第 4 行 + 7.4「仍受风险门控」）。

恢复不是绕过风险的理由：PRE_DISPATCH 恢复「仍受风险门控」（7.4），
LLM/经验候选必须 `effective_risk == LOW` 且 Guard 通过才自动执行（9.3）。

H18：本模块纯函数；Guard 实例的复检在引擎侧完成（设备无关但依赖注入）。
"""
from __future__ import annotations

from testcase.schema import Risk

__all__ = ["candidate_risk_allowed"]


def candidate_risk_allowed(risk: Risk | None) -> bool:
    """恢复候选是否允许自动执行（9.3 第 4 行的确定性部分）。

    只有 LOW 放行。None（候选无 metadata 可依，风险未知）**不放行**——
    「不知道风险」按最高风险处理（12.2/10.1 同源 fail-closed 纪律）。
    MEDIUM/HIGH/CRITICAL → LLM_RISK_BLOCKED / SECURITY_BLOCKED（4.2 消费）。
    """
    return risk is Risk.LOW
