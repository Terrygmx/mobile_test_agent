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


def resolve_local_caps(udid: str, bundle_id: str) -> dict:
    """真机装配 caps（Task 2.7 接线 / M4 Gate 前置）。

    device_name / platform_version 从 simctl 解析，不再像 M2/F5 那样在
    脚本里硬编码；udid 必填（无 booted 设备时上层 fail-loud）。
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
        raise InfraError(f"UDID {udid} 不在 booted 设备中")
    return {
        "platform_name": "iOS",
        "automation_name": "XCUITest",
        "device_name": name,
        "platform_version": runtime,
        "udid": udid,
        "bundle_id": bundle_id,
        "no_reset": True,
        "new_command_timeout": 120,
    }
