"""export.py 桥接测试（12.3 metadata → Repository loader YAML / 5.1 布局）。

覆盖：
  1. literal/constant 导出为元素；dynamic/unknown **不出**
     （12.2 不得猜值——导出假 ID 比缺口危险）；
  2. strategies 用 origin: source（与手写 manual 区分）；
  3. 产出目录可被 loader 直接消费（round-trip：export → from_dirs →
     resolve 成功）——这是 TODO(M3, R10-5.2) 的核心闭环；
  4. type 归一化（SwiftUI 的 list/toggle → loader 白名单或 other）；
  5. 屏幕双源（元素 container 名 + metadata screens 顶层名）。
"""

from __future__ import annotations

import pytest

from repository.resolver import Repository
from source.export import export_generated


def _meta() -> dict:
    return {
        "screens": ["LoginView", "SpikeTab"],
        "screen_elements": [
            {"name": "LoginView", "elements": [
                {"id": "login_button", "type": "button",
                 "accessibility_id": "login_button",
                 "resolution_type": "literal",
                 "container_type": "LoginView",
                 "source": {"file": "a.swift", "line": 25}},
                {"id": "user_field", "type": "textfield",
                 "accessibility_id": "user_field",
                 "resolution_type": "constant",
                 "container_type": "LoginView",
                 "source": {"file": "a.swift", "line": 30}},
                {"id": "cell_", "type": "cell",
                 "accessibility_id": None,
                 "resolution_type": "dynamic",
                 "container_type": "LoginView",
                 "source": {"file": "a.swift", "line": 40}},
            ]},
        ],
    }


def test_literal_and_constant_exported(tmp_path):
    out = tmp_path / "generated" / "local"
    counts = export_generated(_meta(), out)
    assert counts["elements"] == 2  # dynamic 不导出
    assert (out / "elements" / "LoginView.yaml").exists()
    assert (out / "screens" / "LoginView.yaml").exists()
    # marker 声明名（SpikeTab）也要有 Screen，即使无元素
    assert (out / "screens" / "SpikeTab.yaml").exists()


def test_dynamic_not_exported(tmp_path):
    out = tmp_path / "gen"
    text = (out / "elements" / "LoginView.yaml")
    export_generated(_meta(), out)
    content = text.read_text()
    assert "cell_" not in content


def test_origin_is_source(tmp_path):
    out = tmp_path / "gen"
    export_generated(_meta(), out)
    content = (out / "elements" / "LoginView.yaml").read_text()
    assert "origin: source" in content


def test_round_trip_loader_can_consume(tmp_path):
    """TODO(M3, R10-5.2) 核心闭环：export 产物被 Repository.from_dirs 消费，
    4.1 resolve（限定名 + screen 解析）可用。"""
    from repository.resolver import EffectiveElement

    out = tmp_path / "generated" / "local"
    export_generated(_meta(), out)
    repo = Repository.from_dirs(generated_root=str(out))
    el = repo.resolve("LoginView.login_button", build="local")
    assert isinstance(el, EffectiveElement)
    assert el.strategies[0].value == "login_button"
    assert el.strategies[0].origin == "source"


def test_override_shadows_generated(tmp_path):
    """5.3 合并：同 id 的 override 覆盖 generated 的 accessibility_id，
    且 R6-2 override_shadows_source 告警要出现。"""
    import yaml

    out = tmp_path / "generated" / "local"
    export_generated(_meta(), out)
    ov = tmp_path / "overrides" / "elements"
    ov.mkdir(parents=True)
    (ov / "LoginView.yaml").write_text(yaml.safe_dump_all([{
        "schema_version": "1.0", "kind": "element", "id": "login_button",
        "screen": "LoginView", "type": "button",
        "strategies": [{"type": "accessibility_id",
                        "value": "login_button_v2", "origin": "manual"}],
        "metadata": {"risk": "LOW", "idempotency": "IDEMPOTENT",
                     "data_class": "PUBLIC"},
    }], allow_unicode=True))
    repo = Repository.from_dirs(generated_root=str(out),
                                overrides_root=str(tmp_path / "overrides"))
    el = repo.resolve("LoginView.login_button", build="local")
    assert el.strategies[0].value == "login_button_v2"
    assert any("override_shadows_source" in w for w in el.warnings)


def test_type_normalization(tmp_path):
    """scanner 的 list/toggle 超出 loader 白名单 → 归一 other，不炸加载。"""
    meta = {"screens": ["V"], "screen_elements": [
        {"name": "V", "elements": [
            {"id": "a_list", "type": "list", "accessibility_id": "a_list",
             "resolution_type": "literal", "source": {"file": "x", "line": 1}},
            {"id": "a_toggle", "type": "toggle", "accessibility_id": "a_toggle",
             "resolution_type": "literal", "source": {"file": "x", "line": 2}},
        ]}]}
    out = tmp_path / "gen"
    export_generated(meta, out)
    repo = Repository.from_dirs(generated_root=str(out))
    assert repo.resolve("V.a_list", build="local").type == "other"


def test_duplicate_ids_deduped(tmp_path):
    """同 screen 同 id 多文档 → 去重（loader 会因 duplicate key 报错）。"""
    meta = {"screens": ["V"], "screen_elements": [
        {"name": "V", "elements": [
            {"id": "dup", "type": "button", "accessibility_id": "dup",
             "resolution_type": "literal", "source": {"file": "x", "line": 1}},
            {"id": "dup", "type": "button", "accessibility_id": "dup",
             "resolution_type": "literal", "source": {"file": "x", "line": 9}},
        ]}]}
    out = tmp_path / "gen"
    counts = export_generated(meta, out)
    assert counts["elements"] == 1
