"""source.metadata 组装层测试（12.3 / P1-09）。

不打 Swift 工具链（Swift 侧由 swift test 覆盖）——这里只验证 Python 编排层：
  1. 多文件一次扫描、profile 字段齐全（12.3 顶层 5 字段）；
  2. scanner 缺失/非零退出/空输出 → ScanError（fail-loud，不把「没扫成」
     伪装成「零元素」）；
  3. 落盘路径创建 + generated 布局（12.4）。
"""

from __future__ import annotations

import json

import pytest

from source import metadata as md


class TestScanFiles:
    def test_scan_binary_selection_prefers_release(self, tmp_path, monkeypatch):
        """有 release 用 release（CI 构建产物），没有则 debug（本地开发）。"""
        fake_pkg = tmp_path / "swift_scan"
        (fake_pkg / ".build" / "release").mkdir(parents=True)
        (fake_pkg / ".build" / "debug").mkdir(parents=True)
        rel = fake_pkg / ".build" / "release" / "mta-source-scan"
        dbg = fake_pkg / ".build" / "debug" / "mta-source-scan"
        rel.touch()
        dbg.touch()
        monkeypatch.setattr(md, "_SCAN_PKG", fake_pkg)
        assert md._scan_binary() == rel

    def test_missing_binary_raises_scan_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(md, "_SCAN_PKG", tmp_path / "nowhere")
        with pytest.raises(md.ScanError, match="not found"):
            md._scan_binary()

    def test_empty_file_list_raises(self):
        with pytest.raises(md.ScanError, match="no input files"):
            md.scan_files([])


class TestBuildMetadata:
    def test_metadata_shape_12_3(self, tmp_path, monkeypatch):
        """12.3 顶层字段齐全 + 扫描原样输出（screens/screen_elements）。"""
        scanned = {
            "screens": ["LoginView"],
            "screen_elements": [
                {"name": "LoginView",
                 "elements": [{"id": "login_button", "type": "button",
                               "accessibility_id": "login_button",
                               "resolution_type": "literal",
                               "container_type": "LoginView",
                               "label": "登录",
                               "source": {"file": "a.swift", "line": 25}}]},
            ],
        }
        monkeypatch.setattr(md, "scan_files", lambda files: scanned)
        out = tmp_path / "generated" / "local" / "source_metadata.json"
        meta = md.build_metadata(["a.swift"], out)
        for key in ("app_version", "build", "git_commit", "generated_at",
                    "parser_version"):
            assert key in meta, f"12.3 requires {key}"
        assert meta["screens"] == ["LoginView"]
        assert meta["screen_elements"][0]["elements"][0]["id"] == "login_button"
        # 落盘内容 = 返回值（不是「返回了但没写」）
        assert json.loads(out.read_text()) == meta

    def test_scanner_failure_propagates_not_zero_elements(self, tmp_path, monkeypatch):
        """fail-loud：scanner 炸了要抛，不得静默产出零元素（那会把构建问题
        伪装成覆盖率 0，与 12.2「不得猜值」同源的纪律）。"""

        def _boom(files):
            raise md.ScanError("mta-source-scan failed (exit 2)")

        monkeypatch.setattr(md, "scan_files", _boom)
        with pytest.raises(md.ScanError):
            md.build_metadata(["a.swift"], tmp_path / "m.json")

    def test_nested_output_dir_created(self, tmp_path, monkeypatch):
        monkeypatch.setattr(md, "scan_files",
                            lambda files: {"screens": [], "screen_elements": []})
        out = tmp_path / "deep" / "nested" / "dir" / "source_metadata.json"
        md.build_metadata(["a.swift"], out)
        assert out.exists()
