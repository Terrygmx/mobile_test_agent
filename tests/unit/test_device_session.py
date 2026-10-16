"""session.device_session.resolve_local_caps 双路线测试（Task 2.7 扩真机）。

模拟器走 simctl（Booted 匹配）；物理设备 simctl 永远匹配不到，必须
fallthrough 到 devicectl（2026-10-15 乐速通真机接入：此前物理 UDID 会被
误判「不在 booted 设备中」而 fail-loud）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from session.device_session import InfraError, resolve_local_caps


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch(monkeypatch, simctl_stdout="", devicectl_stdout="",
           devicectl_rc=0, sdk_stdout="27.2\n"):
    """按命令行分派：simctl → 模拟器 JSON；devicectl → 物理设备 JSON；
    xcrun --show-sdk-version → SDK 版本（默认与设备同版本 = 不钳制，
    保持旧断言的 27.2 期望）。"""
    def fake_run(cmd, **kw):
        if "simctl" in cmd:
            return _Proc(0, simctl_stdout)
        if "devicectl" in cmd:
            return _Proc(devicectl_rc, devicectl_stdout)
        if "--show-sdk-version" in cmd:
            return _Proc(0, sdk_stdout)
        raise AssertionError(f"未预期的命令: {cmd}")
    monkeypatch.setattr("subprocess.run", fake_run)


_SIMCTL_BOOTED = json.dumps({
    "devices": {
        "com.apple.CoreSimulator.SimRuntime.iOS-18-5": [
            {"udid": "SIM-UDID", "state": "Booted", "name": "iPhone 16 Pro"}]
    }
})

_DEVICECTL_PHYSICAL = json.dumps({
    "result": {"deviceProperties": {
        "name": "“GuoMingxin”的 iPhone",
        "osVersionNumber": 27.2,
    }}
})


def test_simulator_route_unchanged(monkeypatch):
    """模拟器行为逐字保留：simctl Booted 匹配即用它的 name/runtime。"""
    _patch(monkeypatch, simctl_stdout=_SIMCTL_BOOTED)
    caps = resolve_local_caps("SIM-UDID", "com.x")
    assert caps["deviceName"] == "iPhone 16 Pro"
    assert caps["platformVersion"] == "18.5"
    assert caps["udid"] == "SIM-UDID"
    assert caps["bundleId"] == "com.x"


def test_physical_route_via_devicectl(monkeypatch):
    """simctl 匹配不到物理 UDID → fallthrough devicectl（name + osVersion）。"""
    _patch(monkeypatch, simctl_stdout='{"devices": {}}',
           devicectl_stdout=_DEVICECTL_PHYSICAL)
    caps = resolve_local_caps("00008120-0018499A1450C01E", "com.etc.nfcPushOnline")
    assert caps["deviceName"] == "“GuoMingxin”的 iPhone"
    assert caps["platformVersion"] == "27.2"
    assert caps["udid"] == "00008120-0018499A1450C01E"


def test_neither_route_raises_with_both_hints(monkeypatch):
    """两条都查不到 → InfraError，且报错要同时提示两种可能（不猜设备）。"""
    _patch(monkeypatch, simctl_stdout='{"devices": {}}',
           devicectl_stdout="", devicectl_rc=1)
    with pytest.raises(InfraError, match="physical 设备"):
        resolve_local_caps("NOPE", "com.x")


def test_simctl_booted_but_other_udid_falls_through(monkeypatch):
    """有 booted 模拟器但不是目标 UDID → 仍应尝试物理路线（不误判）。"""
    _patch(monkeypatch, simctl_stdout=_SIMCTL_BOOTED,
           devicectl_stdout=_DEVICECTL_PHYSICAL)
    caps = resolve_local_caps("00008120-0018499A1450C01E", "com.x")
    assert caps["deviceName"] == "“GuoMingxin”的 iPhone"


def test_devicectl_missing_fields_raises(monkeypatch):
    """devicectl 返回了但缺 name/version → 不猜，报错。"""
    _patch(monkeypatch, simctl_stdout='{"devices": {}}',
           devicectl_stdout=json.dumps({"result": {"deviceProperties": {}}}))
    with pytest.raises(InfraError):
        resolve_local_caps("00008120-0018499A1450C01E", "com.x")


# --- 部署目标钳制（设备 OS > 本地 SDK）与 WDA 签名 caps ---

class _ProcSDK(_Proc):
    pass


def _patch_with_sdk(monkeypatch, sdk_stdout, devicectl_stdout):
    def fake_run(cmd, **kw):
        if "simctl" in cmd:
            return _Proc(0, '{"devices": {}}')
        if "devicectl" in cmd:
            return _Proc(0, devicectl_stdout)
        if "--show-sdk-version" in cmd:
            return _Proc(0, sdk_stdout)
        raise AssertionError(f"未预期的命令: {cmd}")
    monkeypatch.setattr("subprocess.run", fake_run)


def test_platform_version_clamped_to_sdk(monkeypatch):
    """27.2 设备 + 27.0 SDK → 压到 27.0（Appium 拿它当 WDA 部署目标，
    不压则 xcodebuild code 65：部署目标超出 SDK 支持范围）。"""
    _patch_with_sdk(monkeypatch, "27.0\n", _DEVICECTL_PHYSICAL)
    caps = resolve_local_caps("00008120-0018499A1450C01E", "com.x")
    assert caps["platformVersion"] == "27.0"
    assert caps["deviceName"] == "“GuoMingxin”的 iPhone"


def test_platform_version_kept_when_below_sdk(monkeypatch):
    """设备 OS 低于 SDK → 原样（不无谓压低）。"""
    _patch_with_sdk(monkeypatch, "27.0\n", json.dumps({
        "result": {"deviceProperties": {
            "name": "旧手机", "osVersionNumber": 18.5}}}) )
    caps = resolve_local_caps("00008120-0018499A1450C01E", "com.x")
    assert caps["platformVersion"] == "18.5"


def test_platform_version_kept_when_sdk_unknown(monkeypatch):
    """SDK 版本取不到 → 不猜，原样交给 xcodebuild 报错。"""
    def fake_run(cmd, **kw):
        if "simctl" in cmd:
            return _Proc(0, '{"devices": {}}')
        if "devicectl" in cmd:
            return _Proc(0, _DEVICECTL_PHYSICAL)
        return _Proc(1, "")  # xcrun --show-sdk-version 失败
    monkeypatch.setattr("subprocess.run", fake_run)
    caps = resolve_local_caps("00008120-0018499A1450C01E", "com.x")
    assert caps["platformVersion"] == "27.2"


def test_wda_signing_caps_from_env(monkeypatch):
    """给了 MTA_XCODE_ORG_ID → 注入 appium:xcodeOrgId/SigningId
    （WDA 工程无 team，不给则 code 65：requires a development team）。"""
    _patch_with_sdk(monkeypatch, "27.0\n", _DEVICECTL_PHYSICAL)
    monkeypatch.setenv("MTA_XCODE_ORG_ID", "TSVS7C28BU")
    caps = resolve_local_caps("00008120-0018499A1450C01E", "com.etc.nfcPushOnline")
    assert caps["appium:xcodeOrgId"] == "TSVS7C28BU"
    assert caps["appium:xcodeSigningId"] == "Apple Development"


def test_wda_signing_caps_absent_without_env(monkeypatch):
    """没给 org id → 不加签名 caps（模拟器路线不需要，保持 caps 最小）。"""
    _patch_with_sdk(monkeypatch, "27.0\n", _DEVICECTL_PHYSICAL)
    monkeypatch.delenv("MTA_XCODE_ORG_ID", raising=False)
    caps = resolve_local_caps("00008120-0018499A1450C01E", "com.x")
    assert "appium:xcodeOrgId" not in caps


def test_prebuilt_wda_caps_from_env(monkeypatch):
    """MTA_WDA_DERIVED_DATA → usePrebuiltWDA + derivedDataPath（设备 OS 高于
    本地 SDK 时 Appium 自建 WDA 必挂 code 65，走预建路线）。路径解析成
    **绝对**——Appium 在自己的 CWD（WDA 包目录）里跑 xcodebuild，相对路径
    会解析到别处、找不到预建产物（实测仍报 65）。"""
    _patch_with_sdk(monkeypatch, "27.0\n", _DEVICECTL_PHYSICAL)
    monkeypatch.setenv("MTA_WDA_DERIVED_DATA", "out/wda-ddata")
    caps = resolve_local_caps("00008120-0018499A1450C01E", "com.x")
    assert caps["appium:usePrebuiltWDA"] is True
    assert caps["appium:derivedDataPath"] == str(Path("out/wda-ddata").resolve())
    assert Path(caps["appium:derivedDataPath"]).is_absolute()


def test_prebuilt_wda_caps_absent_without_env(monkeypatch):
    """没给 → 不加（Appium 自建 WDA 的默认行为不变）。"""
    _patch_with_sdk(monkeypatch, "27.0\n", _DEVICECTL_PHYSICAL)
    monkeypatch.delenv("MTA_WDA_DERIVED_DATA", raising=False)
    monkeypatch.delenv("MTA_WDA_PREBUILT_APP", raising=False)
    caps = resolve_local_caps("00008120-0018499A1450C01E", "com.x")
    assert "appium:usePrebuiltWDA" not in caps


def test_preinstalled_wda_caps_from_env(monkeypatch):
    """MTA_WDA_PREBUILT_APP → usePreinstalledWDA + prebuiltWDAPath（绝对路径）。
    iOS 27+ 唯一可靠路线：全程不调 xcodebuild，Appium 直接拉起预装 WDA；
    devicectl 兜底在 27+ 被 Appium 禁用（runner 进前台 → background 失败）。"""
    _patch_with_sdk(monkeypatch, "27.0\n", _DEVICECTL_PHYSICAL)
    monkeypatch.setenv("MTA_WDA_PREBUILT_APP", "out/wda-ddata/Build/Products/"
                       "Debug-iphoneos/WebDriverAgentRunner-Runner.app")
    caps = resolve_local_caps("00008120-0018499A1450C01E", "com.x")
    assert caps["appium:usePreinstalledWDA"] is True
    assert caps["appium:prebuiltWDAPath"] == str(
        Path("out/wda-ddata/Build/Products/Debug-iphoneos/"
             "WebDriverAgentRunner-Runner.app").resolve())


def test_preinstalled_wda_caps_absent_without_env(monkeypatch):
    """没给 → 不加（不影响 Appium 自建 WDA 的默认行为）。"""
    _patch_with_sdk(monkeypatch, "27.0\n", _DEVICECTL_PHYSICAL)
    monkeypatch.delenv("MTA_WDA_PREBUILT_APP", raising=False)
    caps = resolve_local_caps("00008120-0018499A1450C01E", "com.x")
    assert "appium:usePreinstalledWDA" not in caps
    assert "appium:prebuiltWDAPath" not in caps


def test_both_wda_routes_raise(monkeypatch):
    """两条 WDA 路线同时给 → InfraError。它们是互斥启动策略，Appium 只认先
    命中的那条、另一个的路径被静默忽略——留个没人执行的配置不如报错。"""
    _patch_with_sdk(monkeypatch, "27.0\n", _DEVICECTL_PHYSICAL)
    monkeypatch.setenv("MTA_WDA_PREBUILT_APP", "out/wda-ddata/Build/Products/"
                       "Debug-iphoneos/WebDriverAgentRunner-Runner.app")
    monkeypatch.setenv("MTA_WDA_DERIVED_DATA", "out/wda-ddata")
    with pytest.raises(InfraError, match="只能给一个"):
        resolve_local_caps("00008120-0018499A1450C01E", "com.x")
