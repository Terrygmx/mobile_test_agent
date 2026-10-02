"""cli 增补测试：`mta source diff`（12.6 Build-level diff / Task 3.3）。

覆盖：路径缺失 fail-loud（exit 3，不伪装成「无差异」）、默认不阻塞
（REMOVED 非空仍 exit 0）、--fail-on-drift 才非零、UNKNOWN 恒非零、
JSON 落盘、范围由 suites 决定（换一个 suites 根 → scope 变）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli.main import main


def _meta(path: Path, screens: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "screens": list(screens),
        "screen_elements": [{"name": n,
                             "elements": [{"id": i, "accessibility_id": i,
                                           "resolution_type": "literal",
                                           "type": "button"}
                                          for i in ids]}
                            for n, ids in screens.items()],
    }), encoding="utf-8")


def _case(root: Path, body: str) -> None:
    d = root / "smoke"
    d.mkdir(parents=True, exist_ok=True)
    (d / "a.yaml").write_text(body, encoding="utf-8")


def test_diff_removed_is_informational_by_default(tmp_path, capsys):
    _meta(tmp_path / "old.json", {"HomeView": ["gone"]})
    _meta(tmp_path / "new.json", {"HomeView": []})
    _case(tmp_path / "suites",
          'schema_version: "0.2"\nid: a\nname: a\nsuite: smoke\n'
          'steps:\n  - action: tap\n    target: HomeView.gone\n')
    code = main(["source", "diff", "--old", str(tmp_path / "old.json"),
                 "--new", str(tmp_path / "new.json"),
                 "--suites-root", str(tmp_path / "suites"),
                 "--json", str(tmp_path / "d.json")])
    out = capsys.readouterr().out
    assert code == 0                       # 12.6 不阻塞日常回归
    assert "REMOVED" in out and "HomeView.gone" in out
    assert json.loads((tmp_path / "d.json").read_text())["drift"] is True


def test_fail_on_drift_opts_into_nonzero(tmp_path, capsys):
    _meta(tmp_path / "old.json", {"HomeView": ["gone"]})
    _meta(tmp_path / "new.json", {"HomeView": []})
    _case(tmp_path / "suites",
          'schema_version: "0.2"\nid: a\nname: a\nsuite: smoke\n'
          'steps:\n  - action: tap\n    target: HomeView.gone\n')
    code = main(["source", "diff", "--old", str(tmp_path / "old.json"),
                 "--new", str(tmp_path / "new.json"),
                 "--suites-root", str(tmp_path / "suites"),
                 "--fail-on-drift"])
    assert code == 1                       # 1 = 判定不通过（8.4：非配置错误）


def test_unknown_screen_is_always_nonzero(tmp_path, capsys):
    """UNKNOWN（引用了没有的屏）不因「不阻塞」而放过——那是待人工确认项。"""
    _meta(tmp_path / "old.json", {"HomeView": ["x"]})
    _meta(tmp_path / "new.json", {"HomeView": ["x"]})
    _case(tmp_path / "suites",
          'schema_version: "0.2"\nid: a\nname: a\nsuite: smoke\n'
          'steps:\n  - wait_for:\n      target: screen:GhostView\n'
          '      condition: active\n')
    code = main(["source", "diff", "--old", str(tmp_path / "old.json"),
                 "--new", str(tmp_path / "new.json"),
                 "--suites-root", str(tmp_path / "suites")])
    assert code == 1
    assert "UNKNOWN" in capsys.readouterr().out


def test_missing_old_metadata_fails_loud(tmp_path, capsys):
    _meta(tmp_path / "new.json", {"A": ["x"]})
    code = main(["source", "diff", "--old", str(tmp_path / "nope.json"),
                 "--new", str(tmp_path / "new.json"),
                 "--suites-root", str(tmp_path)])
    assert code == 3
    assert "nope.json" in capsys.readouterr().out


def test_missing_suites_root_fails_loud(tmp_path, capsys):
    _meta(tmp_path / "old.json", {"A": ["x"]})
    _meta(tmp_path / "new.json", {"A": ["x"]})
    code = main(["source", "diff", "--old", str(tmp_path / "old.json"),
                 "--new", str(tmp_path / "new.json"),
                 "--suites-root", str(tmp_path / "no_such")])
    assert code == 3
    assert "suites" in capsys.readouterr().out


def test_old_is_required_flag(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main(["source", "diff", "--new", str(tmp_path / "x.json")])
    assert e.value.code == 2              # argparse 缺必填参数


def test_scope_follows_suites_root(tmp_path, capsys):
    """范围跟着 suites 走：换一个用例根，scope 与结论都变（12.6 的
    「仅限用例实际到达」不是装饰）。"""
    _meta(tmp_path / "old.json", {"A": ["x"], "B": ["y"]})
    _meta(tmp_path / "new.json", {"A": ["x"], "B": []})
    _case(tmp_path / "only_a",
          'schema_version: "0.2"\nid: a\nname: a\nsuite: smoke\n'
          'steps:\n  - action: tap\n    target: A.x\n')
    _case(tmp_path / "also_b",
          'schema_version: "0.2"\nid: b\nname: b\nsuite: smoke\n'
          'steps:\n  - action: tap\n    target: B.y\n')
    main(["source", "diff", "--old", str(tmp_path / "old.json"),
          "--new", str(tmp_path / "new.json"),
          "--suites-root", str(tmp_path / "only_a"),
          "--json", str(tmp_path / "a.json")])
    assert json.loads((tmp_path / "a.json").read_text())["removed"] == []
    main(["source", "diff", "--old", str(tmp_path / "old.json"),
          "--new", str(tmp_path / "new.json"),
          "--suites-root", str(tmp_path / "also_b"),
          "--json", str(tmp_path / "b.json")])
    assert json.loads((tmp_path / "b.json").read_text())["removed"] == ["B.y"]