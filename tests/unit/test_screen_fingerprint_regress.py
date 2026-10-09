"""`screen_fingerprint` 重构回归闸（Task 3.3 / P3-11，plan Step 1 第三句）。

plan Step 1 原文：「screen_fingerprint 重构前后对同一批 fixture 输出完全一致。」

本文件把**重构前**（原实现 `sha256("\\n".join(sorted(names)).encode("utf-8"))
.hexdigest()[:16]`，用裸 `hashlib` 独立算出）的固定摘要**硬编码**下来。期望值
不是从新实现回抄的——否则重构把两个实现一起改错也照样绿。重构（改调
`source.hashing.stable_hash`）后必须逐字命中这些摘要。

同时钉住「同一套哈希工具」的复用红线（plan §2 第 1 条）：既断言同一对象
（import 级），也钉住**调用**（spy）——review_p3_task32 P3-1 的教训。
"""
from __future__ import annotations

from source.screen import screen_fingerprint

# 重构前用裸 hashlib 独立算出的期望值（见文件 docstring）。
_BEFORE = [
    (
        '<root><el name="login_button" label="登录"/>'
        '<el name="username_field"/></root>',
        "b00ee6cdb98b8128",
    ),
    (
        '<root><el name="a"/><el name="b" visible="false"/></root>',
        "ca978112ca1bbdca",
    ),
    (
        '<root><el name="z"/><el name="y"/><el name="z"/></root>',
        "154fe76ca1499d78",
    ),
]


def test_refactor_preserves_known_digests():
    for xml, expected in _BEFORE:
        assert screen_fingerprint(xml) == expected, xml


def test_unobservable_pages_still_none():
    # visible=false 过滤后无可见 name/label
    assert screen_fingerprint('<root><el visible="false" name="x"/></root>') is None
    # 完全无 name/label
    assert screen_fingerprint("<root><el/></root>") is None
    # 非法 XML → None（不猜指纹）
    assert screen_fingerprint("<not xml") is None


def test_uses_the_same_hash_primitive():
    import source.hashing as sh
    import source.screen as sc
    assert sc.stable_hash is sh.stable_hash


def test_actually_calls_the_shared_primitive(monkeypatch):
    """钉住**调用**：把 `source.screen` 模块级绑定换成 spy，确认真的走它。"""
    import source.screen as sc

    seen = []
    real = sc.stable_hash

    def spy(parts):
        seen.append(list(parts))
        return real(parts)

    monkeypatch.setattr(sc, "stable_hash", spy)
    assert sc.screen_fingerprint('<root><el name="a"/></root>') == \
        real(["a"])
    assert seen == [["a"]], "screen_fingerprint 没把规范化后的名字交给共享原语"
