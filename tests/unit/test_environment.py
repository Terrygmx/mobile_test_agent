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


class _FakeTC:
    """最小 Testcase 替身：runner.run 只读 .id / .precondition / .steps。"""
    def __init__(self, precondition=None, steps=(), id="tc_env_001"):
        self.id = id
        self.precondition = precondition or {}
        self.steps = list(steps)


class _FakeRecorder:
    def start_run(self, tc_id):
        return 1

    def end_run(self, run_id, status):
        pass


def _runner_stub(app):
    """只关心 reset 分流的最薄 runner（steps 为空 → 不碰 executor）。

    # type: ignore[arg-type] — 测试替身刻意不带真实 Executor/Recorder/Secrets
    （steps 为空，跑不到它们），仅为触发 runner.run 的 reset 分流。
    """
    from runner.testcase_runner import TestcaseRunner
    return TestcaseRunner(
        executor=None, app=app, recorder=_FakeRecorder(),  # type: ignore[arg-type]
        secrets=None)  # type: ignore[arg-type]


# --- R13-1 跨源一致性：lint 放行的策略，执行路径必须可达 ---
# capabilities.py 的注释曾声称「两处策略名必须一致（有单测钉住）」但并无此测试
# ——空头支票。lint 放行 RESET_STATE 而 runner 走 P0 matrix 必炸，正是这条
# 缺口的产物。这里钉住：11.1 矩阵声称支持的策略，ResetExecutor 必须能执行。

_EXECUTABLE_IN_EXECUTOR = {
    "RESET_STATE", "RELAUNCH", "TERMINATE", "LOGOUT", "REINSTALL",
}
# SNAPSHOT 矩阵允许（simulator）但 P1 未实现 → ResetExecutor 显式
# NotImplementedError。刻意不在此集合里，防「悄悄把未实现的写成已实现」。


@pytest.mark.parametrize("strategy", sorted(_EXECUTABLE_IN_EXECUTOR))
def test_matrix_supported_strategy_is_executable(strategy):
    """11.1 声称支持 + ResetExecutor 有实现 → 不抛任何错（fail-loud 反面）。"""
    app = FakeApp()
    _manager(app).reset(strategy)  # 不抛 = 通过


@pytest.mark.parametrize("strategy", sorted(_EXECUTABLE_IN_EXECUTOR))
def test_executor_handles_exactly_the_claimable_set(strategy):
    """执行路径可达集合 ⊆ 矩阵声称支持集合（不得有矩阵外的策略被执行）。"""
    from environment.capabilities import RESET_CAPABILITIES
    assert strategy in RESET_CAPABILITIES


def test_snapshot_in_matrix_but_not_claimed_executable():
    """SNAPSHOT 的诚实边界：矩阵允许、P1 不实现。文档化此差异而非隐藏。"""
    from environment.capabilities import RESET_CAPABILITIES, DeviceType
    assert DeviceType.SIMULATOR in RESET_CAPABILITIES["SNAPSHOT"]
    assert "SNAPSHOT" not in _EXECUTABLE_IN_EXECUTOR


def test_runner_uses_reset_executor_not_p0_matrix():
    """R13-1 根因防线：runner 的 reset 必须经 ResetExecutor，禁止直调
    P0 app.reset_state（P0 SUPPORTED_STRATEGIES 不含 RESET_STATE/LOGOUT，
    直调就是「lint 放行、运行时炸」）。"""
    import inspect
    from runner.testcase_runner import TestcaseRunner
    src = inspect.getsource(TestcaseRunner.run)
    assert "self._reset_executor.reset(" in src, \
        "runner.run must reset via ResetExecutor (R13-1)"
    assert "self.app.reset_state(" not in src, \
        "runner must not call P0 app.reset_state directly (R13-1)"


def test_reset_state_survives_end_to_end_through_runner():
    """R13-1 回归：RESET_STATE 经 runner.run 全链路不抛（原 bug 是运行时炸）。"""
    app = FakeApp()
    runner = _runner_stub(app)
    tc = _FakeTC(precondition={"reset": "RESET_STATE"}, steps=[])
    runner.run(tc)
    assert app.calls == [("terminate", {}),
                         ("launch", {"arguments": ["-UITestReset"]})]


def test_relaunch_still_works_through_runner():
    """接线不能回归既有 RELAUNCH 路径（5 条 smoke 全用 RELAUNCH）。"""
    app = FakeApp()
    runner = _runner_stub(app)
    tc = _FakeTC(precondition={"reset": "RELAUNCH"}, steps=[])
    runner.run(tc)
    assert app.calls == [("RELAUNCH", {"app_path": None})]


def test_reset_state_reinstall_passes_app_path():
    """REINSTALL 仍需 app_path；RESET_STATE 不需要但也不能吞掉参数。"""
    app = FakeApp()
    runner = _runner_stub(app)
    tc = _FakeTC(precondition={"reset": "RESET_STATE", "app_path": "/x.app"},
                 steps=[])
    runner.run(tc)
    assert app.calls == [("terminate", {}),
                         ("launch", {"arguments": ["-UITestReset"]})]


def test_unknown_device_type_raises_rather_than_silent_coerce():
    """R13-5：`real-device` typo 拼写容错归一为真机；真不认识的值必须抛。"""
    from environment.capabilities import CapabilityResolver
    from session.app_session import UnsupportedResetError
    # 容错：连字符/大小写/空格
    assert CapabilityResolver.is_supported("RESET_STATE", "REAL-DEVICE") is True
    assert CapabilityResolver.is_supported("RESET_STATE", " real_device ") is True
    # 不认识：响
    with pytest.raises(UnsupportedResetError):
        CapabilityResolver.is_supported("RESET_STATE", "emulator")


def test_cleanup_tc_param_documented_as_unused():
    """R13-5：cleanup 的 tc 参数保留是为 0.3 EnvSpec 签名稳定，非死代码漏删。"""
    app = FakeApp()
    m = _manager(app)
    m.cleanup(tc=None)  # 不抛：on_cleanup=None 时是 no-op
    m.cleanup()         # 不传也合法


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
