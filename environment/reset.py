"""ResetExecutor（设计 11 / 附录 A4，Task 2.3 / P1-06）。

把策略名变成动作序列，动作归 AppSession（P0 已有）。与 P0 的差异：
  - 先过 CapabilityResolver（11.1），不支持直接抛，不落到 AppSession 才炸；
  - RESET_STATE / LOGOUT 走 App 内 reset hook（附录 A4：`-UITestReset` 启动
    参数），不是 AppSession 的 reset_state——hook 的入口是「下次 launch 以
    `-UITestReset` 启动」，terminate + relaunch 之间由 AppSession 传参；
  - SNAPSHOT 在 P1 无实现（矩阵里仅 simulator 支持，但实现顺延）——显式
    NotImplementedError，不假装做了。
"""
from __future__ import annotations

from environment.capabilities import CapabilityResolver
from session.app_session import AppSession


class SnapshotNotImplemented(NotImplementedError):
    """SNAPSHOT 在 P1 未实现（矩阵允许，实现顺延）。显式，不静默。"""


class ResetExecutor:
    def __init__(self, app: AppSession, device_type: str = "simulator") -> None:
        self.app = app
        self.device_type = device_type

    def reset(self, strategy: str, **kwargs) -> None:
        """执行 reset。策略×设备不支持 → UnsupportedResetError（先查矩阵）。"""
        CapabilityResolver.resolve(strategy, self.device_type)
        if strategy == "SNAPSHOT":
            raise SnapshotNotImplemented(
                "SNAPSHOT reset is listed in 11.1 but not implemented in P1; "
                "use RESET_STATE/RELAUNCH instead")
        if strategy == "RESET_STATE":
            self.app.terminate()
            self.app.launch(arguments=["-UITestReset"])
            return
        if strategy == "LOGOUT":
            self.app.terminate()
            self.app.launch(arguments=["-UITestLogout"])
            return
        # RELAUNCH / TERMINATE / REINSTALL：P0 AppSession.reset_state 已实现
        self.app.reset_state(strategy, **kwargs)
