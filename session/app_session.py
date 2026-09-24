"""AppSession — App 生命周期，与 WDA 生命周期解耦（Stage 3）。

ponytail: 所有操作直接用 Appium driver 内置 API + simctl install，
不封装 xcrun 管理层；capability matrix 就是一个 dict。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from session.device_session import DeviceSession


def _simctl(*args: str) -> None:
    """调用 simctl，继承 DEVELOPER_DIR（子进程可能没有）。"""
    env = dict(os.environ)
    env.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    subprocess.run(["xcrun", "simctl", *args], check=True, capture_output=True, env=env)

# Reset Capability Matrix（Phase 0 写死版，来自设计文档 4.2.1）
RESET_MATRIX: dict[str, set[str]] = {
    "RELAUNCH": {"simulator", "real_device"},
    "TERMINATE": {"simulator", "real_device"},
    "REINSTALL": {"simulator", "real_device"},
    "LOGOUT": set(),        # 预留：走 App 内测试 Hook，Phase 0 用 REINSTALL 代替
    "SNAPSHOT": {"simulator"},  # 预留，不实现
}

SUPPORTED_STRATEGIES = {"RELAUNCH", "TERMINATE", "REINSTALL"}


class UnsupportedResetError(Exception):
    pass


class AppSession:
    def __init__(self, device_session: DeviceSession, bundle_id: str):
        self.ds = device_session
        self.bundle_id = bundle_id

    def launch(self) -> None:
        self.ds.ensure_alive().activate_app(self.bundle_id)

    def terminate(self) -> None:
        self.ds.ensure_alive().terminate_app(self.bundle_id)

    def reinstall(self, app_path: str) -> None:
        """卸载重装（模拟器）。Appium session 不受影响。"""
        d = self.ds.ensure_alive()
        d.remove_app(self.bundle_id)
        _simctl("install", d.capabilities.get("udid", ""), app_path)
        d.activate_app(self.bundle_id)

    def reset_state(self, strategy: str, **kwargs) -> None:
        """先查 matrix，不支持直接抛错，不静默降级（设计文档硬约束）。"""
        if strategy not in SUPPORTED_STRATEGIES:
            raise UnsupportedResetError(
                f"{strategy}: supported={sorted(SUPPORTED_STRATEGIES)}, "
                f"matrix={ {k: sorted(v) for k, v in RESET_MATRIX.items()} }"
            )
        if strategy == "RELAUNCH":
            self.terminate()
            self.launch()
        elif strategy == "TERMINATE":
            self.terminate()
        elif strategy == "REINSTALL":
            self.reinstall(kwargs["app_path"])
