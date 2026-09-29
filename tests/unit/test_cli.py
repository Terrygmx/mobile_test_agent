"""Task 1.3（P1-01）mta lint CLI 测试：退出码 + 占位子命令。"""
from __future__ import annotations

import pytest

from cli.main import main


VALID_TC = """\
schema_version: "0.2"
id: t_ok
name: ok
steps:
  - action: launch_app
  - action: tap
    target: login_button
"""

WARNING_TC = """\
schema_version: "0.2"
id: t_warn
name: warn
steps:
  - action: tap
    target: login_button
    idempotency: NON_IDEMPOTENT
"""

UNKNOWN_TARGET_TC = """\
schema_version: "0.2"
id: t_bad
name: bad
steps:
  - action: tap
    target: ghost_button
"""


@pytest.fixture()
def repo_env(tmp_path, monkeypatch):
    """把真实 overrides YAML 摆成 repository/overrides 默认布局并切 cwd。"""
    import shutil
    (tmp_path / "repository").mkdir()
    shutil.copytree("repository/overrides", tmp_path / "repository" / "overrides")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write(tmp_path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content)
    return str(p)


def test_cli_lint_clean_exit_0(repo_env, capsys):
    tc = _write(repo_env, "ok.yaml", VALID_TC)
    assert main(["lint", tc]) == 0
    assert "exit 0" in capsys.readouterr().out


def test_cli_lint_warning_only_exit_0(repo_env, capsys):
    tc = _write(repo_env, "warn.yaml", WARNING_TC)
    assert main(["lint", tc]) == 0
    assert "WARN" in capsys.readouterr().out


def test_cli_lint_error_exit_3(repo_env, capsys):
    tc = _write(repo_env, "bad.yaml", UNKNOWN_TARGET_TC)
    assert main(["lint", tc]) == 3


def test_cli_lint_schema_invalid_exit_3(repo_env):
    tc = _write(repo_env, "broken.yaml", "steps: []\n")
    assert main(["lint", tc]) == 3


# --- R7-1：文件级错误 → exit 3（8.4：exit 1 只留给「存在 FAIL」） ---

def test_cli_lint_missing_file_exit_3(repo_env, capsys):
    assert main(["lint", str(repo_env / "no_such.yaml")]) == 3
    assert "not found" in capsys.readouterr().out


def test_cli_lint_invalid_yaml_exit_3(repo_env, capsys):
    tc = _write(repo_env, "bad_syntax.yaml", "steps: [\n  - {unclosed")
    assert main(["lint", tc]) == 3
    assert "invalid YAML" in capsys.readouterr().out


def test_cli_placeholder_exit_2(capsys):
    assert main(["run"]) == 2
    assert "not implemented" in capsys.readouterr().out
