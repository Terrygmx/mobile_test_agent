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
    def __init__(self, appium_url: str, capabilities: dict, infra_log: Path | None = None):
        self.appium_url = appium_url
        self.caps = capabilities
        self.infra_log = infra_log or Path("out/infra_events.jsonl")
        self.driver: webdriver.Remote | None = None

    # --- infra event 记录（Stage 5 迁入 SQLite） ---
    def _log_infra(self, event_type: str) -> None:
        self.infra_log.parent.mkdir(exist_ok=True)
        with open(self.infra_log, "a") as f:
            f.write(json.dumps({"event_type": event_type, "timestamp": time.time()}) + "\n")

    # --- 核心生命周期 ---
    def connect(self) -> webdriver.Remote:
        options = XCUITestOptions()
        for k, v in self.caps.items():
            options.set_capability(k, v)
        self.driver = webdriver.Remote(self.appium_url, options=options)
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
