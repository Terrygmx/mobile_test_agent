"""R11-4：H11 implicit wait = 0 的接线测试。

review 结论是「死接线」。复核后修正了原因，但问题本身更深一层：全库所有
Executor 调用点都是 `connect()` 先于 `Executor(ds)`，所以构造期设置其实是
生效的；真正的漏洞是 `DeviceSession.restart_wda()` 会造出**新** driver，
只在 `Executor.__init__` 设一次会在 WDA 自愈后丢失该保证。

因此测试全部打在真实 `DeviceSession.connect` 上（monkeypatch 掉 webdriver.Remote，
不打真设备），而不是测替身自己的行为——测替身只能证明替身符合预期。
"""
from __future__ import annotations

from executor.executor import Executor
from session import device_session as mod


class FakeDriver:
    def __init__(self):
        self.implicit_waits: list[int] = []

    def implicitly_wait(self, seconds):
        self.implicit_waits.append(seconds)

    def get_window_size(self):  # health_check 的探针
        return {"width": 390, "height": 844}

    def quit(self):
        pass


def _patched_remote(monkeypatch) -> list[FakeDriver]:
    made: list[FakeDriver] = []

    def fake_remote(*a, **k):
        d = FakeDriver()
        made.append(d)
        return d

    monkeypatch.setattr(mod.webdriver, "Remote", fake_remote)
    return made


def test_connect_sets_implicit_wait_zero(monkeypatch):
    made = _patched_remote(monkeypatch)
    ds = mod.DeviceSession("http://127.0.0.1:4723", {"platformName": "iOS"})
    ds.connect()
    assert made[0].implicit_waits == [0]


def test_wda_restart_applies_implicit_wait_to_new_driver(monkeypatch):
    """R11-4 核心：自愈重建的 driver 也必须带 0，否则 H11 靠 Appium 默认值侥幸成立。"""
    made = _patched_remote(monkeypatch)
    ds = mod.DeviceSession("http://127.0.0.1:4723", {"platformName": "iOS"})
    ds.connect()
    ds.restart_wda()
    assert len(made) == 2 and made[1] is not made[0], "restart 应造出新 driver"
    assert made[1].implicit_waits == [0]


def test_executor_before_connect_does_not_crash(monkeypatch):
    """Executor 先于 connect 构造不应炸（无 driver 时静默跳过）。"""
    _patched_remote(monkeypatch)
    ds = mod.DeviceSession("http://127.0.0.1:4723", {"platformName": "iOS"})
    ex = Executor(ds)
    assert ex.ds.driver is None
    ds.connect()
    assert ds.driver.implicit_waits == [0]
