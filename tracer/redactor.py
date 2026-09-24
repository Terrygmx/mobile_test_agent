"""Redactor — 在任何数据写入前调用（设计文档硬约束：不允许先落盘后脱敏）。"""

from __future__ import annotations

REDACTED = "***REDACTED***"
SENSITIVE_KEYS = {"password", "token", "secret", "auth", "credential"}


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
        elif k.lower() in SENSITIVE_KEYS:
            out[k] = REDACTED
        else:
            out[k] = v
    return out
