"""CapabilityResolver（设计 11.1，Task 2.3 / P1-06）。

reset 策略 × 设备类型 支持矩阵（11.1 表逐行）。P0 的 RESET_MATRIX
（session/app_session.py）是写死的 dict；M2 版本按「用例写抽象名，运行时
由 CapabilityResolver 决定是否可用」的语义重立——策略名与设备类型解耦，
策略不支持时抛错而非静默降级（11.1：fail-loud 优先于「尽量跑」）。

Keychain 注意（11.1 注）：REINSTALL 不一定清 Keychain，认证类状态清理以
RESET_STATE（App 自己清，附录 A4 hook）为准。
"""
from __future__ import annotations

import enum

from session.app_session import UnsupportedResetError


class DeviceType(str, enum.Enum):
    """11.1 设备端别。YAML/CLI 传裸字符串时经 _coerce 归一。"""

    SIMULATOR = "simulator"
    REAL_DEVICE = "real_device"


# 11.1 矩阵。真源在此；session/app_session.py 的 P0 matrix 由 ResetExecutor
# 落动作时消费，两处策略名必须一致（有单测钉住）。
RESET_CAPABILITIES: dict[str, set[DeviceType]] = {
    "RESET_STATE": {DeviceType.SIMULATOR, DeviceType.REAL_DEVICE},  # App hook（A4）
    "RELAUNCH": {DeviceType.SIMULATOR, DeviceType.REAL_DEVICE},
    "TERMINATE": {DeviceType.SIMULATOR, DeviceType.REAL_DEVICE},
    "LOGOUT": {DeviceType.SIMULATOR, DeviceType.REAL_DEVICE},       # App 内 hook（11.1）
    "REINSTALL": {DeviceType.SIMULATOR, DeviceType.REAL_DEVICE},    # 慢；仅全新安装用
    "SNAPSHOT": {DeviceType.SIMULATOR},                             # 真机不支持
}


def _coerce(device: DeviceType | str) -> DeviceType:
    if isinstance(device, DeviceType):
        return device
    text = str(device).strip().lower()
    try:
        return DeviceType(text)
    except ValueError:
        # 未知设备描述按「最受限」处理：查 simulator ∩ real_device 的交集成员
        return DeviceType.REAL_DEVICE  # SNAPSHOT 等在真机侧不支持 → 更早暴露


class CapabilityResolver:
    """策略 × 设备 → 支持判定 / 显式解析。纯查询，不碰设备。"""

    @staticmethod
    def is_supported(strategy: str, device: DeviceType | str) -> bool:
        devices = RESET_CAPABILITIES.get(strategy)
        return devices is not None and _coerce(device) in devices

    @staticmethod
    def resolve(strategy: str, device: DeviceType | str) -> None:
        """不支持 → UnsupportedResetError（不静默降级，11.1 硬约束）。

        报错带策略名与支持矩阵摘要，事后可查；不做「降级成 RELAUNCH」之类
        的替作者做主。
        """
        if CapabilityResolver.is_supported(strategy, device):
            return
        dev = _coerce(device)
        supported = sorted(s for s, devs in RESET_CAPABILITIES.items() if dev in devs)
        raise UnsupportedResetError(
            f"unsupported reset strategy {strategy!r} on {dev.value}: "
            f"supported on {dev.value}: {supported}"
        )
