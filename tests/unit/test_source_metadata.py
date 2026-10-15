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


class TestGitCommitSourceRoot:
    """git_commit 必须取自**被测源码仓库**，不是 CWD（2026-10-15 乐速通接入
    实测：源码在外部仓库 ~/Desktop/workspace/ios，走 CWD 会把平台自己的
    commit 写进 metadata → 与 App 注入值永不相同 → G8 fail-closed）。"""

    def test_source_root_passed_to_git(self, tmp_path, monkeypatch):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            class _P:
                returncode = 0
                stdout = "fef2f1e8\n"
                stderr = ""
            return _P()

        monkeypatch.setattr(md.subprocess, "run", fake_run)
        assert md._git_commit(tmp_path / "src") == "fef2f1e8"
        assert seen["cmd"][:3] == ["git", "-C", str(tmp_path / "src")]

    def test_default_no_root_keeps_cwd_semantics(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            class _P:
                returncode = 0
                stdout = "localrepo\n"
                stderr = ""
            return _P()

        monkeypatch.setattr(md.subprocess, "run", fake_run)
        assert md._git_commit() == "localrepo"
        assert seen["cmd"] == ["git", "rev-parse", "--short", "HEAD"]

    def test_build_metadata_threads_source_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(md, "scan_files",
                            lambda files: {"screens": [], "screen_elements": []})
        seen = {}
        real = md._git_commit

        def spy(root=None):
            seen["root"] = root
            return real(root)

        monkeypatch.setattr(md, "_git_commit", spy)
        md.build_metadata(["a.swift"], tmp_path / "m.json",
                          source_root=tmp_path / "app")
        assert seen["root"] == tmp_path / "app"


class TestObjcRoundTrip:
    """objc_scan 子进程输出 ↔ scan_files objc 分支的 schema round-trip。

    2026-10-15 修：objc_scan.main() 曾输出 {"screen", "elements"}（Phase 1
    独立形态），而 metadata.py 读 screen_elements——键名失配使 ObjC 元素
    **静默合并空**（乐速通 614 个 .m 扫出 0 元素）。此处直接跑真子进程，
    钉死真实 round-trip（此前测试全 stub scan_files，所以缺陷能存活）。
    """

    def test_objc_subprocess_output_merges_into_screen_elements(self, tmp_path):
        src = tmp_path / "Demo.m"
        src.write_text(
            "@implementation DemoView\n"
            "- (void)setup {\n"
            '    self.button.accessibilityIdentifier = @"login_button";\n'
            "}\n"
            "@end\n", encoding="utf-8")
        out = md.scan_files([src])
        names = [s["name"] for s in out["screen_elements"]]
        assert "DemoView" in names
        entry = next(s for s in out["screen_elements"] if s["name"] == "DemoView")
        el = entry["elements"][0]
        assert el["id"] == "login_button"
        assert el["accessibility_id"] == "login_button"
        assert el["container_type"] == "DemoView"
        assert el["resolution_type"] == "literal"
        assert "DemoView" in out["screens"]
