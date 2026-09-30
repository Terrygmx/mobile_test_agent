"""Redactor 语义测试（14.4 / H8）。

Task 2.4 的历史：P0 用全等匹配（`k.lower() in SENSITIVE_KEYS`），复合字段名
（auth_token / user_password / x-api-key）全部漏网。14.4 的规则是「字段名命中
password/token/secret/auth/credential/access_token」，即子串命中即脱敏。本文件
钉住该语义，防止有人「优化」回全等匹配。
"""

from __future__ import annotations

import pytest

from tracer.redactor import REDACTED, redact


@pytest.mark.parametrize("key", [
    "password", "token", "secret", "auth", "credential", "access_token",
    # 复合字段名（P0 漏网的就是这一类）
    "auth_token", "user_password", "password_hash", "accessToken",
    "ACCESS_TOKEN", "id_token", "refresh_token", "api_secret",
    "credentials", "authorisation", "authHeader",
])
def test_sensitive_keys_are_redacted(key):
    out = redact({key: "sensitive-value"})
    assert out[key] == REDACTED


@pytest.mark.parametrize("key", [
    "username", "user_id", "author", "authoring", "screen", "target_id",
    "step_index", "latency_ms", "authentic",  # 含 "auth" 但非凭证
])
def test_non_sensitive_keys_are_preserved(key):
    out = redact({key: "value"})
    assert out[key] == "value", f"{key} 不该被脱敏（误伤会让 trace 无法排查）"


def test_known_gap_api_key_not_in_14_4_list():
    """已知缺口（记账项，不静默）：`x-api-key` / `api_key` 是真实凭证名，
    但 14.4 的命中清单只列了 password/token/secret/auth/credential/
    access_token，**不含 api-key**。当前行为是不脱敏。

    不擅自扩清单：14.4 是设计文档，扩它属于改 spec（且 `key` 一词过宽，
    会误伤 `api_keyboard_hint` 之类）。真正要防的是「用例/元素层拿到 api_key
    写进 detail_json」——M3 起约定：凭证只经 secret 引用（`${var}`），
    不允许字面量进 trace。届时回填本测试的期望。
    """
    out = redact({"x-api-key": "AKIA..."})
    assert out["x-api-key"] == "AKIA...", "当前 14.4 清单不含 api-key（见 docstring）"


def test_redaction_is_recursive_through_dict_and_list():
    payload = {
        "name": "ok",
        "password": "hunter2",
        "nested": {"auth_token": "abc", "inner": [{"secret": "s"}]},
        "items": [{"credential": "c"}],
    }
    out = redact(payload)
    assert out["name"] == "ok"
    assert out["password"] == REDACTED
    assert out["nested"]["auth_token"] == REDACTED
    assert out["nested"]["inner"][0]["secret"] == REDACTED
    assert out["items"][0]["credential"] == REDACTED


def test_non_dict_input_passes_through():
    assert redact("plain") == "plain"
    assert redact(42) == 42
    assert redact(None) is None


def test_empty_containers():
    assert redact({}) == {}
    assert redact([]) == []


def test_original_not_mutated():
    """脱敏返回新对象，不改调用方的原 dict（否则调用方手里的凭证也被抹了）。"""
    original = {"password": "hunter2"}
    redact(original)
    assert original["password"] == "hunter2"


def test_value_types_preserved():
    out = redact({"token": 12345, "auth": True, "secret": None})
    assert out["token"] == REDACTED
    assert out["auth"] == REDACTED
    assert out["secret"] == REDACTED