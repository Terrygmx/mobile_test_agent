"""Phase 0 Stage 1 — Appium + XCUITest + WDA + Simulator 链路验证。

验收标准（docs/phase0-plan.md Stage 1 / 设计文档目标 1）：
  能通过 Python 对 Simulator 执行 launch / tap / input / screenshot / page_source。

用法：
  .venv/bin/python phase0/smoke_test.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from appium import webdriver
from appium.options.ios import XCUITestOptions
from appium.webdriver.common.appiumby import AppiumBy

APPIUM_URL = "http://127.0.0.1:4723"
BUNDLE_ID = "com.phaset0.logindemo"
DEVICE_NAME = "iPhone 14"
PLATFORM_VERSION = "18.5"
UDID = "AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E"

OUT_DIR = Path(__file__).resolve().parent.parent / "out"
OUT_DIR.mkdir(exist_ok=True)


def build_options() -> XCUITestOptions:
    options = XCUITestOptions()
    options.platform_name = "iOS"
    options.automation_name = "XCUITest"
    options.device_name = DEVICE_NAME
    options.platform_version = PLATFORM_VERSION
    options.udid = UDID
    options.bundle_id = BUNDLE_ID
    options.no_reset = True  # smoke test 不重置 App 状态
    options.new_command_timeout = 120
    return options


def main() -> int:
    print(f"[1] 连接 Appium ({APPIUM_URL}) ...")
    driver = webdriver.Remote(APPIUM_URL, options=build_options())
    print(f"    session id = {driver.session_id}")

    try:
        print("[2] screenshot ...")
        shot = OUT_DIR / "smoke_test.png"
        driver.get_screenshot_as_file(str(shot))
        assert shot.exists() and shot.stat().st_size > 0, "screenshot 文件为空"
        print(f"    -> {shot} ({shot.stat().st_size} bytes)")

        print("[3] page_source ...")
        source = driver.page_source
        print(f"    page_source 长度 = {len(source)}")
        print(f"    前 500 字符:\n{source[:500]}")

        print("[4] tap (username_field) + input ...")
        field = driver.find_element(AppiumBy.ACCESSIBILITY_ID, "username_field")
        field.click()
        field.clear()  # 上次运行可能残留输入（no_reset=True）
        field.send_keys("qa_agent")
        time.sleep(0.5)

        value = field.get_attribute("value")
        print(f"    username_field value = {value!r}")
        assert value == "qa_agent", f"input 未生效: {value!r}"

        print("[5] swipe ...")
        driver.swipe(200, 600, 200, 300, 300)

        print("[6] 验证 login_button 存在（元素定位链路）...")
        login_btn = driver.find_element(AppiumBy.ACCESSIBILITY_ID, "login_button")
        print(f"    login_button visible = {login_btn.get_attribute('visible')}")

        print("\n✅ Stage 1 smoke test PASS — Appium 链路全部打通")
        return 0
    finally:
        driver.quit()


if __name__ == "__main__":
    sys.exit(main())
