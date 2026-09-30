"""Redactor — 在任何数据写入前调用（设计文档硬约束：不允许先落盘后脱敏）。"""

from __future__ import annotations

REDACTED = "***REDACTED***"
# 14.4：字段名命中 password/token/secret/auth/credential/access_token 即脱敏。
# **子串匹配**而非全等：P0 用全等匹配（k.lower() in SENSITIVE_KEYS）导致
# auth_token / user_password / x-api-key 这类复合字段名全部漏网——Task 2.4
# 的 detail_json 断言把它暴露了。规则是「命中即脱敏」，宁可多脱不可漏脱。
SENSITIVE_KEYS = {"password", "token", "secret", "auth", "credential",
                  "access_token"}
# 显式非敏感（防止子串匹配误伤）
# authentic/authentic 有 "auth" 子串但语义是「真实性」，不是凭证
_ALLOW = {"author", "authoring", "authenticity", "authentic"}


def _is_sensitive(key: str) -> bool:
    k = key.lower()
    if k in _ALLOW:
        return False
    return any(s in k for s in SENSITIVE_KEYS)


def redact(data):
    """递归替换敏感字段值（dict 或 list 内的 dict）。"""
    if isinstance(data, list):
        return [redact(x) for x in data]
    if not isinstance(data, dict):
        return data
    out = {}
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            out[k] = redact(v)
        elif _is_sensitive(k):
            out[k] = REDACTED
        else:
            out[k] = v
    return out
