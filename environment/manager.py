"""EnvironmentManager（设计 11，Task 2.3 / P1-06）。

三入口（11 节）：prepare（reset + 前置数据）、cleanup、reset。
P1 范围：reset 走 ResetExecutor（CapabilityResolver 前置）；cleanup 以回调
注入——P1 用例矩阵的 cleanup 只有「回到初始屏」一类动作，尚未定型成
CleanupSpec schema（顺延 0.3），Manager 先立失败语义：

  H10：cleanup 失败 → CleanupError → ENVIRONMENT_FAILURE，默认 ABORT_SUITE。
  不能带着脏环境跑下一条——那是把「环境没复位」伪装成「下一条用例的 bug」。
"""
from __future__ import annotations

from typing import Callable

from environment.reset import ResetExecutor
from session.app_session import AppSession


class CleanupError(Exception):
    """11.2：cleanup 失败。调用方（runner/suite 层）→ ENVIRONMENT_FAILURE。"""


class EnvironmentManager:
    def __init__(
        self,
        app: AppSession,
        on_cleanup: Callable[[], None] | None = None,
        device_type: str = "simulator",
    ) -> None:
        self.app = app
        self.on_cleanup = on_cleanup
        self.resetter = ResetExecutor(app, device_type=device_type)

    # --- 11 节三入口 ---

    def prepare(self, tc) -> None:
        """用例前置：precondition.reset 存在才动环境。

        与 runner 现行为一致（precondition.get("reset")）；EnvSpec 定型（0.3）
        后由 schema 承担枚举校验，这里只管执行。
        """
        reset = (getattr(tc, "precondition", None) or {}).get("reset")
        if reset:
            self.reset(reset)

    def cleanup(self, tc=None) -> None:
        """11.2：普通失败也执行 cleanup；失败 → CleanupError（不静默）。

        `tc` 目前未用（P1 无按用例分化的 cleanup 动作，EnvSpec 顺延 0.3）。
        保留参数是为了 0.3 定型 EnvSpec 后签名不变——不是「忘了删」。
        """
        if self.on_cleanup is None:
            return
        try:
            self.on_cleanup()
        except Exception as e:
            raise CleanupError(
                f"cleanup failed (H10: ENVIRONMENT_FAILURE, ABORT_SUITE): "
                f"{type(e).__name__}: {e}") from e

    def reset(self, strategy: str, **kwargs) -> None:
        self.resetter.reset(strategy, **kwargs)
