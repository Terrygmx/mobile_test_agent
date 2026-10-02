"""cli 增补测试：`mta source coverage`（12.7 / Task 3.2）。

覆盖：
  1. 子命令存在，产物路径显式（不给就 exit 3，不是静默默认）；
  2. --metadata 缺失 → 退出码 3 + fail-loud（配置错误不伪装成「覆盖率 0」）；
  3. --json 输出 to_dict 全量字段；
  4. 退出码：missing 桶非空 → 3（覆盖率 Gate）；纯 informational 时 0。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli.main import main


def _write_metadata(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "screens": ["LoginView", "HomeView"],
        "screen_elements": [
            {"name": "LoginView", "elements": [
                {"id": "username_field", "accessibility_id": "username_field",
                 "resolution_type": "literal", "type": "textfield"},
            ]},
            {"name": "HomeView", "elements": [
                {"id": "cell_", "accessibility_id": None,
                 "resolution_type": "dynamic", "type": "cell"},
            ]},
        ],
    }), encoding="utf-8")


def _write_suites(root: Path) -> None:
    d = root / "smoke"
    d.mkdir(parents=True, exist_ok=True)
    (d / "login_001.yaml").write_text(
        "schema_version: \"0.2\"\n"
        "id: login_001\nname: 登录\nsuite: smoke\n"
        "steps:\n"
        "  - action: input\n"
        "    target: LoginView.username_field\n    value: x\n"
        "  - action: tap\n    target: HomeView.cell_alpha\n"
        "  - wait_for:\n      target: screen:HomeView\n"
        "      condition: active\n", encoding="utf-8")


def test_coverage_reports_dynamic_bucket(tmp_path, capsys):
    _write_metadata(tmp_path / "meta.json")
    _write_suites(tmp_path / "suites")
    code = main(["source", "coverage",
                 "--metadata", str(tmp_path / "meta.json"),
                 "--suites-root", str(tmp_path / "suites"),
                 "--json", str(tmp_path / "cov.json")])
    out = capsys.readouterr().out
    data = json.loads((tmp_path / "cov.json").read_text())
    assert code == 3                       # dynamic 非空 → Gate 未达标
    assert data["coverage"] == 0.5         # 1/2：literal 命中，dynamic 不算
    assert data["dynamic"] == 1
    assert data["screens_total"] == 1 and data["screens_resolved"] == 1
    assert "coverage: 50.0%" in out
    assert "DYNAMIC" in out


def test_coverage_all_resolved_exits_zero(tmp_path, capsys):
    _write_metadata(tmp_path / "meta.json")
    d = tmp_path / "suites" / "smoke"
    d.mkdir(parents=True)
    (d / "a.yaml").write_text(
        "schema_version: \"0.2\"\nid: a\nname: a\nsuite: smoke\n"
        "steps:\n  - action: input\n"
        "    target: LoginView.username_field\n    value: x\n",
        encoding="utf-8")
    code = main(["source", "coverage",
                 "--metadata", str(tmp_path / "meta.json"),
                 "--suites-root", str(tmp_path / "suites"),
                 "--json", str(tmp_path / "cov.json")])
    assert code == 0
    assert json.loads((tmp_path / "cov.json").read_text())["coverage"] == 1.0


def test_missing_metadata_fails_loud(tmp_path, capsys):
    code = main(["source", "coverage",
                 "--metadata", str(tmp_path / "nope.json"),
                 "--suites-root", str(tmp_path)])
    assert code == 3
    assert "metadata" in capsys.readouterr().out


def test_missing_suites_root_fails_loud(tmp_path, capsys):
    _write_metadata(tmp_path / "meta.json")
    code = main(["source", "coverage",
                 "--metadata", str(tmp_path / "meta.json"),
                 "--suites-root", str(tmp_path / "no_such_dir")])
    assert code == 3
    assert "suites" in capsys.readouterr().out


def test_defaults_are_repo_layout(tmp_path, monkeypatch, capsys):
    """两个路径都有默认值（12.4 repo 布局）——默认值不是「隐式猜」，是文档化
    的约定，且输出必须打印实际用的两个路径，否则报告无法事后复核（坑 10d）。"""
    _write_metadata(tmp_path / "repository" / "generated" / "local"
                    / "source_metadata.json")
    _write_suites(tmp_path / "suites")
    monkeypatch.chdir(tmp_path)
    main(["source", "coverage"])
    out = capsys.readouterr().out
    assert "repository/generated/local/source_metadata.json" in out
    assert "suites" in out


def test_explicit_paths_are_echoed(tmp_path, capsys):
    _write_metadata(tmp_path / "meta.json")
    _write_suites(tmp_path / "suites")
    main(["source", "coverage",
          "--metadata", str(tmp_path / "meta.json"),
          "--suites-root", str(tmp_path / "suites")])
    out = capsys.readouterr().out
    assert str(tmp_path / "meta.json") in out
    assert str(tmp_path / "suites") in out