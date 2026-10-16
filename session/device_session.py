"""DeviceSession — WDA 生命周期 + health check + 自愈（Stage 2）。

ponytail: health check 用一次轻量 driver 调用实现，不自建 WDA HTTP ping；
restart = quit + reconnect；infra 事件写 JSONL 文件，SQLite 留给 Stage 5 的 trace 模块。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from appium import webdriver
from appium.options.ios import XCUITestOptions


class InfraError(Exception):
    """基础设施故障（WDA/连接层），与测试用例失败严格区分。"""


class DeviceSession:
    def __init__(self, appium_url: str, capabilities: dict, infra_log: Path | None = None,
                 recorder=None, run_id: str | None = None):
        self.appium_url = appium_url
        self.caps = capabilities
        self.infra_log = infra_log or Path("out/infra_events.jsonl")
        self.driver: webdriver.Remote | None = None
        # review P0-2：infra 事件收敛到 SQLite（JSONL 保留为 debug 副本）
        self.recorder = recorder
        self.run_id = run_id

    def attach_recorder(self, recorder, run_id: str) -> None:
        """review R2-4：run_id 在 start_run 之后才存在，允许连接后补挂。"""
        self.recorder = recorder
        self.run_id = run_id

    def _log_infra(self, event_type: str) -> None:
        self.infra_log.parent.mkdir(exist_ok=True)
        with open(self.infra_log, "a") as f:
            f.write(json.dumps({"event_type": event_type, "timestamp": time.time()}) + "\n")
        if self.recorder is not None:
            self.recorder.record_infra(self.run_id, event_type)

    # --- 核心生命周期 ---
    def connect(self) -> webdriver.Remote:
        options = XCUITestOptions()
        for k, v in self.caps.items():
            options.set_capability(k, v)
        self.driver = webdriver.Remote(self.appium_url, options=options)
        # H11：implicit wait 固定为 0。放在这里而不是 Executor 构造期——WDA 重启
        # （restart_wda → connect）会造出**新** driver，只在 Executor.__init__ 设一次
        # 会在自愈后丢失该保证，等于靠 Appium 默认值（恰好也是 0）侥幸成立。
        self.driver.implicitly_wait(0)
        return self.driver

    def health_check(self) -> bool:
        """轻量调用探测 WDA 是否存活。"""
        if self.driver is None:
            return False
        try:
            self.driver.get_window_size()  # 最廉价的 round-trip
            return True
        except Exception:
            return False

    def restart_wda(self) -> None:
        """杀掉当前 session 重建。"""
        self._log_infra("WDA_DEAD")
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass  # 已死，quit 失败是预期
        self.driver = None
        self.connect()
        self._log_infra("WDA_RESTARTED")

    def ensure_alive(self) -> webdriver.Remote:
        """每个 action 前调用。恢复失败抛 InfraError，与测试失败区分。"""
        if not self.health_check():
            self.restart_wda()
            if not self.health_check():
                raise InfraError("WDA restart failed")
        assert self.driver is not None
        return self.driver


def _sdk_version() -> str | None:
    """本地 iPhoneOS SDK 版本（= 部署目标上限）；取不到返回 None（不猜）。"""
    import os
    import subprocess

    env = dict(os.environ)
    env.setdefault("DEVELOPER_DIR",
                   "/Applications/Xcode.app/Contents/Developer")
    try:
        proc = subprocess.run(
            ["xcrun", "--sdk", "iphoneos", "--show-sdk-version"],
            capture_output=True, text=True, env=env, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def _clamp_to_sdk(version: str, sdk: str | None) -> str:
    """设备 OS 高于本地 SDK 时压到 SDK 上限（27.2 设备 + 27.0 SDK 实测）。

    xcodebuild 对「部署目标 > SDK 上限」直接 error（code 65）。Appium 把
    `platformVersion` 同时当 WDA 的 IPHONEOS_DEPLOYMENT_TARGET 用，所以这里
    压掉的既是构建参数也是上报值——低版本构建在高版本设备上正常运行，反向
    则根本编不动。sdk 取不到时原样返回（让 xcodebuild 自己报错，不猜）。
    """
    if not sdk:
        return version

    def _t(v: str) -> tuple[int, ...]:
        try:
            return tuple(int(x) for x in v.split(".")[:3])
        except ValueError:
            return ()
    a, b = _t(version), _t(sdk)
    if not a or not b:
        return version
    return version if a <= b else sdk


def _physical_device_info(udid: str) -> tuple[str, str] | None:
    """devicectl 查物理设备的 (name, os_version)；查不到返回 None。

    与 simctl 路线互补：`simctl list devices` 只认识模拟器，物理设备的
    UDID（8-16 位十六进制段，如 00008120-0018499A1450C01E）在里面永远
    匹配不到——真机接入（2026-10-15 乐速通）必须走 devicectl。
    `-q -j -`：纯 JSON 到 stdout（人读进度走 stderr）。
    """
    import os
    import subprocess

    env = dict(os.environ)
    env.setdefault("DEVELOPER_DIR",
                   "/Applications/Xcode.app/Contents/Developer")
    try:
        proc = subprocess.run(
            ["xcrun", "devicectl", "device", "info", "details",
             "--device", udid, "-q", "-j", "-"],
            capture_output=True, text=True, env=env, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    props = data.get("result", {}).get("deviceProperties", {})
    name = props.get("name")
    version = props.get("osVersionNumber")
    if not name or not version:
        return None
    return str(name), str(version)


def resolve_local_caps(udid: str, bundle_id: str) -> dict:
    """真机装配 caps（Task 2.7 接线 / M4 Gate 前置；2026-10-15 扩物理设备）。

    deviceName / platformVersion 双路线：
    - 模拟器：`simctl list devices -j booted`（udid 必填且必须 Booted）；
    - 物理设备：`devicectl device info details`（simctl 匹配不到时 fallthrough，
      不需要按 UDID 格式猜设备类型——猜错就是把真机当模拟器报错）。
    两条都查不到 → InfraError（上层 fail-loud，不猜设备名/版本号）。
    """
    import os
    import subprocess

    env = dict(os.environ)
    env.setdefault("DEVELOPER_DIR",
                   "/Applications/Xcode.app/Contents/Developer")
    proc = subprocess.run(
        ["xcrun", "simctl", "list", "devices", "-j", "booted"],
        capture_output=True, text=True, env=env, timeout=30)
    if proc.returncode != 0:
        raise InfraError(f"simctl 查询失败: {proc.stderr[:200]}")
    name, runtime = None, None
    devices = json.loads(proc.stdout or "{}").get("devices", {})
    for runtime_key, lst in devices.items():
        for dev in lst:
            if dev.get("udid") == udid and dev.get("state") == "Booted":
                name = dev.get("name")
                # com.apple.CoreSimulator.SimRuntime.iOS-18-5 → 18.5
                runtime = runtime_key.rsplit(".", 1)[-1].replace("iOS-", "")\
                    .replace("-", ".")
                break
        if name:
            break
    if name is None:
        physical = _physical_device_info(udid)
        if physical is not None:
            name, runtime = physical
            # 设备 OS 可能高于本地 SDK（27.2 设备 + 27.0 SDK 实测）——Appium
            # 拿 platformVersion 当 WDA 部署目标，不压会 xcodebuild code 65。
            runtime = _clamp_to_sdk(runtime, _sdk_version())
    if name is None:
        raise InfraError(
            f"UDID {udid} 不在 booted 模拟器，也不是已连接的 physical 设备"
            f"（xcrun devicectl list devices 确认连接与配对状态）")
    # ⚠️ 键名必须是 Appium 的 camelCase。snake_case（platform_version /
    # bundle_id / no_reset / device_name）Appium **不认**：它只提示
    # "platform_version (did you mean 'platformVersion'?)" 然后**静默丢弃**
    # （2026-10-15 真机实测，Appium 3.7.0 + xcuitest 12.13.2）。后果不只是
    # 「没生效」：platformVersion 丢了，Appium 会退回设备真实 OS 版本，
    # 而 driver.js 又用它当 WDA 的 IPHONEOS_DEPLOYMENT_TARGET——设备 OS 高于
    # 本地 SDK 时直接 xcodebuild code 65。Python 客户端会自动给非 W3C 标准键
    # 加 `appium:` 前缀，无需手写。
    caps = {
        "platformName": "iOS",
        "automationName": "XCUITest",
        "deviceName": name,
        "platformVersion": runtime,
        "udid": udid,
        "bundleId": bundle_id,
        "noReset": True,
        # Appium recognized 的既有别名，保持不变（两个拼写都收）。
        "new_command_timeout": 120,
    }
    # WDA 真机签名：WebDriverAgent 工程不带 team，不显式给
    # `appium:xcodeOrgId` 会被 xcodebuild 判「requires a development team」
    # （2026-10-15 实测 code 65）。team 是**被测产品侧**信息（乐速通 =
    # TSVS7C28BU），平台不写死——从环境变量取，缺省不加（模拟器路线不需要）。
    import os as _os
    org = _os.environ.get("MTA_XCODE_ORG_ID")
    if org:
        caps["appium:xcodeOrgId"] = org
        caps["appium:xcodeSigningId"] = _os.environ.get(
            "MTA_XCODE_SIGNING_ID", "Apple Development")
    # 设备 OS 高于本地 SDK 时（27.2 设备 + 27.0 SDK 实测），Appium 会用设备
    # 真实版本拼 IPHONEOS_DEPLOYMENT_TARGET 覆盖 caps 里的值（driver.js 里
    # `opts.platformVersion = 设备版本`），xcodebuild 报 code 65——caps 侧
    # 钳不掉。两条绕开路线（二选一，都不让 Appium 自己编 WDA）：
    # ① usePreinstalledWDA + prebuiltWDAPath：装好 WDA .app 后由 Appium 直接
    #    拉起，全程不碰 xcodebuild。**iOS 27+ 唯一可靠路线**——devicectl 兜底
    #    会把 XCTest runner 推前台导致 "Failed to background test runner"，
    #    Appium 在 27+ 已禁用该兜底（wda-host-ops.js 注释）。
    # ② usePrebuiltWDA + derivedDataPath：让 Appium 跑
    #    `xcodebuild test-without-building`，需要 DerivedData 里有 .xctestrun。
    # 构建命令见 docs/lst_update_playbook.md §3.10。
    # ⚠️ derivedDataPath 必须**绝对路径**：Appium 在自己的 CWD（WDA 包目录）
    # 里执行 xcodebuild，相对路径会解析到别处、找不到预建产物（实测仍报 65）。
    wda_app = _os.environ.get("MTA_WDA_PREBUILT_APP")
    prebuilt = _os.environ.get("MTA_WDA_DERIVED_DATA")
    if wda_app and prebuilt:
        # 两条是**互斥**的 WDA 启动策略（Appium 只认先命中的那条，另一个的路径
        # 被静默忽略）。同时给＝留一个没人执行的配置，报错而不是猜。
        raise InfraError(
            "MTA_WDA_PREBUILT_APP 与 MTA_WDA_DERIVED_DATA 只能给一个："
            "前者是 iOS 27+ 唯一可靠路线（不调 xcodebuild），后者留给设备 OS "
            "≤ 本地 SDK 的机器。见 docs/lst_update_playbook.md §3.10")
    if wda_app:
        caps["appium:usePreinstalledWDA"] = True
        caps["appium:prebuiltWDAPath"] = str(Path(wda_app).resolve())
    if prebuilt:
        caps["appium:usePrebuiltWDA"] = True
        caps["appium:derivedDataPath"] = str(Path(prebuilt).resolve())
    return caps
