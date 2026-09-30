"""Task 2.3（P1-06）Environment Manager 单测（设计 11 / 11.1 / 11.2）。

口径：
  - CapabilityResolver 矩阵按 11.1 表逐行：RESET_STATE/RELAUNCH/TERMINATE/LOGOUT/
    REINSTALL 双端支持，SNAPSHOT 仅 simulator；
  - 不支持 → `UnsupportedResetError`（**不静默降级**——fail-loud 优先于「尽量跑」）；
  - cleanup 失败 → `CleanupError`（H10：cleanup 失败 → ENVIRONMENT_FAILURE，
    默认 ABORT_SUITE——不能带着脏环境跑下一条）；
  - lint 阶段拦截不支持的策略（11.2）：`mta lint` 预检报错，不运行到一半才炸
    （R5-3 的 `reset: RESET_STATEE` typo 就此在 lint 关口拦截）。
"""
from __future__ import annotations

import pytest

from environment.capabilities import CapabilityResolver, DeviceType
from environment.manager import CleanupError, EnvironmentManager
from environment.reset import ResetExecutor
from repository.resolver import LintIssue, Repository, Severity
from testcase.schema import TestCase

EnvironmentManager.__test__ = False  # type: ignore[attr-defined]
ResetExecutor.__test__ = False  # type: ignore[attr-defined]
CleanupError.__test__ = False  # type: ignore[attr-defined]


# --- CapabilityResolver：11.1 表逐行 ---

@pytest.mark.parametrize("strategy", ["RESET_STATE", "RELAUNCH", "TERMINATE",
                                      "LOGOUT", "REINSTALL"])
def test_matrix_all_but_snapshot_support_both_devices(strategy):
    assert CapabilityResolver.is_supported(strategy, DeviceType.SIMULATOR)
    assert CapabilityResolver.is_supported(strategy, DeviceType.REAL_DEVICE)


def test_snapshot_simulator_only():
    assert CapabilityResolver.is_supported("SNAPSHOT", DeviceType.SIMULATOR)
    assert not CapabilityResolver.is_supported("SNAPSHOT", DeviceType.REAL_DEVICE)


def test_unknown_strategy_is_unsupported_everywhere():
    """typo（RESET_STATEE）与未知策略同罪：哪里都不支持，不猜。"""
    for dev in (DeviceType.SIMULATOR, DeviceType.REAL_DEVICE):
        assert not CapabilityResolver.is_supported("RESET_STATEE", dev)
        assert not CapabilityResolver.is_supported("SOMETHING_ELSE", dev)


@pytest.mark.parametrize("strategy,dev", [
    ("SNAPSHOT", DeviceType.REAL_DEVICE),
    ("RESET_STATEE", DeviceType.SIMULATOR),
    ("NOPE", DeviceType.SIMULATOR),
])
def test_resolver_raises_on_unsupported_no_silent_degradation(strategy, dev):
    with pytest.raises(Exception) as ei:
        CapabilityResolver.resolve(strategy, dev)
    assert "unsupported" in str(ei.value).lower()
    assert strategy in str(ei.value)  # 报错要带策略名，事后可查


def test_resolver_accepts_string_device_type():
    """YAML 里写 'simulator'/'real_device' 字符串也要能过。"""
    assert CapabilityResolver.is_supported("RELAUNCH", "simulator")


# --- ResetExecutor：动作分派 ---

class FakeApp:
    def __init__(self):
        self.calls: list[tuple] = []

    def reset_state(self, strategy, **kw):
        self.calls.append((strategy, kw))

    def launch(self, arguments=None):
        self.calls.append(("launch", {"arguments": arguments}))

    def terminate(self):
        self.calls.append(("terminate", {}))


def _manager(app, cleanup_calls=None) -> EnvironmentManager:
    return EnvironmentManager(app,  # type: ignore[arg-type]
                              on_cleanup=lambda: cleanup_calls.append(1)
                              if cleanup_calls is not None else None)


def test_reset_delegates_with_strategy():
    app = FakeApp()
    _manager(app).reset("RELAUNCH")
    assert app.calls == [("RELAUNCH", {})]


# --- RESET_STATE / LOGOUT 走 hook 启动参数（附录 A4） ---

def test_reset_state_uses_hook_arguments():
    """RESET_STATE = terminate + 以 -UITestReset 重启（App 自己清，11.1 注）。"""
    app = FakeApp()
    _manager(app).reset("RESET_STATE")
    assert app.calls == [("terminate", {}), ("launch", {"arguments": ["-UITestReset"]})]


def test_logout_uses_hook_arguments():
    app = FakeApp()
    _manager(app).reset("LOGOUT")
    assert app.calls == [("terminate", {}), ("launch", {"arguments": ["-UITestLogout"]})]


def test_snapshot_raises_not_implemented():
    """SNAPSHOT 矩阵允许（simulator）但 P1 未实现——显式报错不假装做了。"""
    app = FakeApp()
    with pytest.raises(NotImplementedError):
        _manager(app).reset("SNAPSHOT")


# --- AppSession.launch 参数路径 ---

class _FakeDriver:
    def __init__(self):
        self.capabilities: dict = {"udid": "UDID-123"}
        self.activated: list[str] = []

    def activate_app(self, bundle_id):
        self.activated.append(bundle_id)


class _FakeDS:
    def __init__(self, driver):
        self.driver = driver

    def ensure_alive(self):
        return self.driver


def test_app_session_launch_without_args_activates(monkeypatch):
    from session.app_session import AppSession
    d = _FakeDriver()
    app = AppSession(_FakeDS(d), "com.phaset0.logindemo")
    monkeypatch.setattr("session.app_session._simctl", lambda *a: (_ for _ in ()).throw(AssertionError("no simctl call")))
    app.launch()
    assert d.activated == ["com.phaset0.logindemo"]


def test_app_session_launch_with_args_uses_simctl(monkeypatch):
    from session.app_session import AppSession
    d = _FakeDriver()
    app = AppSession(_FakeDS(d), "com.phaset0.logindemo")
    seen: list[tuple] = []
    monkeypatch.setattr("session.app_session._simctl", lambda *a: seen.append(a))
    app.launch(arguments=["-UITestReset"])
    assert not d.activated
    assert seen == [("launch", "UDID-123", "com.phaset0.logindemo", "-UITestReset")]


def test_app_session_launch_with_args_missing_udid_raises():
    from session.app_session import AppSession, InfraError
    d = _FakeDriver()
    d.capabilities = {}
    app = AppSession(_FakeDS(d), "com.phaset0.logindemo")
    with pytest.raises(InfraError):
        app.launch(arguments=["-UITestReset"])


def test_reset_unsupported_raises():
    app = FakeApp()
    with pytest.raises(Exception):
        _manager(app).reset("SNAPSHOT")  # 默认 simulator 之外的设备时


def test_cleanup_runs_and_swallows_normal_path():
    """cleanup 正常路径：执行回调，不抛。"""
    ran = []
    m = EnvironmentManager(FakeApp(), on_cleanup=lambda: ran.append(1))  # type: ignore[arg-type]
    m.cleanup()
    assert ran == [1]


def test_cleanup_failure_raises_cleanup_error():
    def broken():
        raise RuntimeError("disk full")

    m = EnvironmentManager(FakeApp(), on_cleanup=broken)  # type: ignore[arg-type]
    with pytest.raises(CleanupError):
        m.cleanup()


# --- lint 集成：11.2 预检拦截 ---

def _tc_with_reset(reset_value) -> TestCase:
    return TestCase(
        schema_version="0.2", id="t", name="t",
        precondition={"reset": reset_value}, steps=[],
    )


def test_lint_rejects_unsupported_reset_strategy():
    from testcase.lint import lint
    issues = lint([_tc_with_reset("RESET_STATEE")],
                  Repository(), _FakeSecrets(), device_type="simulator")
    assert any(i.code == "unsupported_reset" and i.severity is Severity.ERROR
               for i in issues)


def test_lint_accepts_supported_strategy():
    from testcase.lint import lint
    issues = lint([_tc_with_reset("RELAUNCH")],
                  Repository(), _FakeSecrets(), device_type="simulator")
    assert not any(i.code == "unsupported_reset" for i in issues)


def test_lint_snapshot_fails_on_real_device_only():
    from testcase.lint import lint
    ok_sim = lint([_tc_with_reset("SNAPSHOT")],
                  Repository(), _FakeSecrets(), device_type="simulator")
    bad_real = lint([_tc_with_reset("SNAPSHOT")],
                    Repository(), _FakeSecrets(), device_type="real_device")
    assert not any(i.code == "unsupported_reset" for i in ok_sim)
    assert any(i.code == "unsupported_reset" for i in bad_real)


class _FakeSecrets:
    def get(self, key):
        return "v"
