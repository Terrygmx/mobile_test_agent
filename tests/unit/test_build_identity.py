"""Task 3.3 收口（12.5 Build Identity / P1-10 前半）：App 与 metadata 的
build 关联校验。

H18 边界：evaluate / metadata_identity / gate_run_start 的判定逻辑全部
离设备（read 注入）；真机读取（simctl）只验 subprocess 边界的契约——
真机端到端语义由 G8 / M4 矩阵 #21 验收。
"""
from __future__ import annotations

import json
import plistlib
import sqlite3

import pytest

from source.build_identity import (
    AppIdentity,
    BuildIdentityError,
    Verdict,
    evaluate,
    gate_run_start,
    metadata_identity,
    read_app_identity,
    resolve_booted_udid,
    override_allowed,
)

# --- evaluate：纯判定（G8 fail-closed） ---


def test_evaluate_match():
    v = evaluate(AppIdentity("a76d449", "local"),
                 AppIdentity("a76d449", "local"))
    assert v == Verdict(matched=True, mismatches=())


def test_evaluate_commit_mismatch():
    v = evaluate(AppIdentity("a76d449", "local"),
                 AppIdentity("0067363", "local"))
    assert not v.matched
    assert "git_commit" in v.mismatches
    assert "build" not in v.mismatches


def test_evaluate_build_mismatch():
    v = evaluate(AppIdentity("a76d449", "local"),
                 AppIdentity("a76d449", "staging"))
    assert not v.matched
    assert "build" in v.mismatches


def test_app_missing_injection_fail_closed():
    """G8：App 没注入 MTA_* 键 = 不可关联 ≠ 一致，必须 fail closed。"""
    v = evaluate(AppIdentity(None, None), AppIdentity("a76d449", "local"))
    assert not v.matched
    assert "app_missing_injection" in v.mismatches


def test_metadata_missing_identity_fail_closed():
    v = evaluate(AppIdentity("a76d449", "local"), AppIdentity(None, "local"))
    assert not v.matched
    assert "metadata_missing_identity" in v.mismatches


def test_both_sides_missing_is_not_match():
    """两边都没身份不是「一致」——静默放行等于绕过关联性（12.2 同源）。"""
    assert not evaluate(AppIdentity(None, None),
                        AppIdentity(None, None)).matched


# --- metadata_identity：12.3 顶层扁平键 ---


def test_metadata_identity_reads_flat_keys(tmp_path):
    p = tmp_path / "source_metadata.json"
    p.write_text(json.dumps({"build": "local", "git_commit": "a76d449",
                             "screens": [], "screen_elements": []}),
                 encoding="utf-8")
    assert metadata_identity(p) == AppIdentity("a76d449", "local")


def test_metadata_identity_missing_file_is_fail_loud(tmp_path):
    with pytest.raises(BuildIdentityError, match="metadata"):
        metadata_identity(tmp_path / "nope.json")


# --- read_app_identity：simctl 边界契约 ---


def _fake_container(tmp_path, keys: dict):
    container = tmp_path / "LoginDemo.app"
    container.mkdir()
    info = {"CFBundleIdentifier": "com.phaset0.logindemo"}
    info.update(keys)
    (container / "Info.plist").write_bytes(
        plistlib.dumps(info, fmt=plistlib.FMT_BINARY))
    return container


def test_read_app_identity_parses_injected_keys(tmp_path, monkeypatch):
    container = _fake_container(tmp_path, {"MTA_GIT_COMMIT": "a76d449",
                                           "MTA_BUILD_ID": "local"})

    class P:
        def __init__(self):
            self.returncode = 0
            self.stdout = str(container) + "\n"
            self.stderr = ""

    monkeypatch.setattr("source.build_identity.subprocess.run",
                        lambda *a, **k: P())
    assert read_app_identity("UDID", "com.phaset0.logindemo") == \
        AppIdentity("a76d449", "local")


def test_read_app_identity_uninjected_app_yields_none_keys(tmp_path,
                                                           monkeypatch):
    """键缺失不是读取错误——错误语义留给 evaluate（未注入的旧 App 要能被
    读出来再判 fail-closed，而不是在读取层就抛异常吞掉现场）。"""
    container = _fake_container(tmp_path, {})

    class P:
        returncode = 0
        stdout = str(container) + "\n"
        stderr = ""

    monkeypatch.setattr("source.build_identity.subprocess.run",
                        lambda *a, **k: P())
    ident = read_app_identity("UDID", "com.phaset0.logindemo")
    assert ident.git_commit is None and ident.build is None


def test_read_app_identity_app_not_installed_raises(tmp_path, monkeypatch):
    class P:
        returncode = 62
        stdout = ""
        stderr = "App not installed"

    monkeypatch.setattr("source.build_identity.subprocess.run",
                        lambda *a, **k: P())
    with pytest.raises(BuildIdentityError, match="not installed"):
        read_app_identity("UDID", "com.phaset0.logindemo")


# --- override_allowed：12.5 CI 第二开关 ---


def test_override_allowed_outside_ci():
    assert override_allowed(True, {}) is True


def test_override_blocked_by_default():
    assert override_allowed(False, {}) is False


def test_override_in_ci_requires_second_switch():
    assert override_allowed(True, {"CI": "true"}) is False
    assert override_allowed(True, {"CI": "true",
                                   "MTA_ALLOW_METADATA_MISMATCH_CI": "1"}) \
        is True


# --- gate_run_start：编排（read/metadata 注入，离设备） ---


META = {"build": "local", "git_commit": "a76d449"}


def _gate(tmp_path, app_keys, *, allow=False, env=None, meta=None):
    meta_path = tmp_path / "source_metadata.json"
    meta_path.write_text(json.dumps(meta or META), encoding="utf-8")
    app = AppIdentity(app_keys.get("MTA_GIT_COMMIT"),
                      app_keys.get("MTA_BUILD_ID"))
    return gate_run_start(metadata_path=meta_path, udid="UDID",
                          bundle_id="com.phaset0.logindemo", allow=allow,
                          env=env or {}, read=lambda u, b: app)


def test_gate_matched_not_blocked(tmp_path):
    gr = _gate(tmp_path, {"MTA_GIT_COMMIT": "a76d449",
                          "MTA_BUILD_ID": "local"})
    assert not gr.blocked and not gr.mismatch and not gr.override


def test_gate_mismatch_blocked_by_default(tmp_path):
    gr = _gate(tmp_path, {"MTA_GIT_COMMIT": "deadbeef",
                          "MTA_BUILD_ID": "local"})
    assert gr.blocked and gr.mismatch
    assert not gr.override
    assert "git_commit" in gr.mismatches


def test_gate_mismatch_allowed_records_override(tmp_path):
    """12.5：放行必须可见——override=1 进 trace 字段，不是静默继续。"""
    gr = _gate(tmp_path, {"MTA_GIT_COMMIT": "deadbeef",
                          "MTA_BUILD_ID": "local"}, allow=True)
    assert not gr.blocked and gr.mismatch and gr.override


def test_gate_uninjected_app_blocked_even_with_allow_outside_ci(tmp_path):
    """未注入 = 不可关联：G8 fail-closed。放行开关只豁免「可关联但不一致」，
    不豁免「根本没身份」——否则旧 App 永远绕过校验。"""
    gr = _gate(tmp_path, {}, allow=True)
    assert gr.blocked
    assert "app_missing_injection" in gr.mismatches


def test_gate_ci_mismatch_needs_second_switch(tmp_path):
    gr = _gate(tmp_path, {"MTA_GIT_COMMIT": "deadbeef",
                          "MTA_BUILD_ID": "local"},
               allow=True, env={"CI": "true"})
    assert gr.blocked
    gr = _gate(tmp_path, {"MTA_GIT_COMMIT": "deadbeef",
                          "MTA_BUILD_ID": "local"},
               allow=True, env={"CI": "true",
                                "MTA_ALLOW_METADATA_MISMATCH_CI": "1"})
    assert not gr.blocked and gr.override


# --- resolve_booted_udid：subprocess 边界 ---


def test_resolve_booted_udid_none_when_no_device(monkeypatch):
    class P:
        returncode = 0
        stdout = "== Devices ==\n-- iOS 18.5 --\n"
        stderr = ""

    monkeypatch.setattr("source.build_identity.subprocess.run",
                        lambda *a, **k: P())
    assert resolve_booted_udid() is None


def test_resolve_booted_udid_finds_booted(monkeypatch):
    class P:
        returncode = 0
        stdout = ("== Devices ==\n"
                  "    iPhone 14 (ABC123) (Booted)\n"
                  "    iPhone 15 (DEF456) (Shutdown)\n")
        stderr = ""

    monkeypatch.setattr("source.build_identity.subprocess.run",
                        lambda *a, **k: P())
    assert resolve_booted_udid() == "ABC123"


# --- CLI 接线：mta run 真机路径启动前拦截（12.5「运行开始时」） ---


def _run_scaffold(tmp_path):
    suites = tmp_path / "suites"
    suites.mkdir()
    (suites / "a_001.yaml").write_text(
        'schema_version: "0.2"\nid: a_001\nname: x\nsuite: smoke\n'
        "tags: [smoke]\nsteps:\n  - action: launch_app\n", encoding="utf-8")
    meta_path = tmp_path / "source_metadata.json"
    meta_path.write_text(json.dumps(META), encoding="utf-8")
    return suites, meta_path


def _patch_app(monkeypatch, commit, build):
    monkeypatch.setattr("source.build_identity.read_app_identity",
                        lambda u, b: AppIdentity(commit, build))


def test_cli_run_mismatch_blocked_exit3(tmp_path, monkeypatch, capsys):
    """matrix #21：App build 与 metadata 不一致 → BUILD_METADATA_MISMATCH、
    未启动用例、exit 3。"""
    from cli.main import main

    suites, meta_path = _run_scaffold(tmp_path)
    _patch_app(monkeypatch, "deadbeef", "local")
    db = tmp_path / "trace.db"
    code = main(["run", "--suite", "smoke", "--suites-root", str(suites),
                 "--db", str(db), "--udid", "UDID",
                 "--bundle-id", "com.phaset0.logindemo",
                 "--metadata", str(meta_path)])
    out = capsys.readouterr().out
    assert code == 3
    assert "BUILD_METADATA_MISMATCH" in out
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT status FROM testcase_runs").fetchall()
    assert rows == [], "拦截必须发生在用例启动前"
    run_row = conn.execute(
        "SELECT status, exit_code, app_git_commit, metadata_git_commit,"
        " metadata_mismatch, metadata_mismatch_override FROM runs"
        ).fetchone()
    assert run_row == ("ABORTED", 3, "deadbeef", "a76d449", 1, 0)


def test_cli_run_allow_records_override_and_proceeds(tmp_path, monkeypatch,
                                                     capsys):
    """放行不是静默：trace 记 metadata_mismatch=1 + override=1，然后按既有
    真机装配语义继续（Task 2.7 接线后真机路径真实装配；单测封闭：stub 掉
    caps 解析与设备连接，装配失败仍为前置配置错误 exit 3）。"""
    from cli.main import main

    suites, meta_path = _run_scaffold(tmp_path)
    _patch_app(monkeypatch, "deadbeef", "local")
    monkeypatch.setattr("session.device_session.resolve_local_caps",
                        lambda udid, bundle_id: {"udid": udid})
    from session.device_session import DeviceSession

    def _no_device(self):
        raise RuntimeError("unit-test: 无设备会话")

    monkeypatch.setattr(DeviceSession, "connect", _no_device)
    db = tmp_path / "trace.db"
    code = main(["run", "--suite", "smoke", "--suites-root", str(suites),
                 "--db", str(db), "--udid", "UDID",
                 "--bundle-id", "com.phaset0.logindemo",
                 "--metadata", str(meta_path), "--allow-metadata-mismatch"])
    out = capsys.readouterr().out
    assert code == 3  # 放行后走到真机装配；装配失败 → 前置 exit 3
    assert "设备会话建立失败" in out
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT metadata_mismatch, metadata_mismatch_override"
        " FROM runs").fetchone()
    assert row == (1, 1)


def test_cli_run_ci_allow_without_second_switch_blocked(tmp_path,
                                                        monkeypatch, capsys):
    from cli.main import main

    suites, meta_path = _run_scaffold(tmp_path)
    _patch_app(monkeypatch, "deadbeef", "local")
    monkeypatch.setenv("CI", "true")
    code = main(["run", "--suite", "smoke", "--suites-root", str(suites),
                 "--db", str(tmp_path / "trace.db"), "--udid", "UDID",
                 "--bundle-id", "com.phaset0.logindemo",
                 "--metadata", str(meta_path), "--allow-metadata-mismatch"])
    out = capsys.readouterr().out
    assert code == 3
    assert "MTA_ALLOW_METADATA_MISMATCH_CI" in out


def test_cli_run_fake_driver_skips_identity_check(tmp_path, monkeypatch,
                                                  capsys):
    """--fake-driver 无设备身份可读，不宣称校验过（H18 边界）——metadata
    缺失也不拦。"""
    from cli.main import main

    suites, _ = _run_scaffold(tmp_path)
    db = tmp_path / "trace.db"
    code = main(["run", "--suite", "smoke", "--suites-root", str(suites),
                 "--db", str(db), "--fake-driver"])
    out = capsys.readouterr().out
    assert code == 0
    assert "BUILD_METADATA_MISMATCH" not in out


def test_cli_run_read_failure_is_preflight_exit3(tmp_path, monkeypatch,
                                                 capsys):
    """App 未安装/读取失败是前置配置错误（fail-loud），不是静默放行。"""
    from cli.main import main

    def _boom(u, b):
        raise BuildIdentityError("App not installed")

    monkeypatch.setattr("source.build_identity.read_app_identity", _boom)
    suites, meta_path = _run_scaffold(tmp_path)
    code = main(["run", "--suite", "smoke", "--suites-root", str(suites),
                 "--db", str(tmp_path / "trace.db"), "--udid", "UDID",
                 "--bundle-id", "com.phaset0.logindemo",
                 "--metadata", str(meta_path)])
    out = capsys.readouterr().out
    assert code == 3
    assert "build identity" in out
