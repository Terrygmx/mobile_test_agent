"""Executor + Locator（Stage 4）。

ponytail: Locator 是 list[dict]，不是类；每个方法 = ensure_alive + 一行 driver 调用。
唯一性校验内嵌在 find：0 → ElementNotFound，>=2 → AmbiguousElement。
"""

from __future__ import annotations

from appium.webdriver.webdriver import WebDriver

from session.device_session import DeviceSession

# Locator: [{"type": "accessibility_id", "value": "login_button"},
#           {"type": "predicate", "value": "label == '登录'"}]
Locator = list[dict]


class ElementNotFound(Exception):
    pass


class AmbiguousElement(Exception):
    pass


class Executor:
    def __init__(self, device_session: DeviceSession):
        self.ds = device_session

    @property
    def driver(self) -> WebDriver:
        return self.ds.driver

    def find(self, locator: Locator):
        """按策略顺序查找。每个 action 前先 ensure_alive（设计文档硬约束）。"""
        self.ds.ensure_alive()
        for strat in locator:
            by = {"accessibility_id": "accessibility id", "predicate": "-ios predicate string"}[
                strat["type"]
            ]
            elements = self.driver.find_elements(by, strat["value"])
            if len(elements) == 1:
                return elements[0]
            if len(elements) > 1:
                raise AmbiguousElement(f"{strat}: {len(elements)} matches, fail closed")
        raise ElementNotFound(f"no element for {locator}")

    def tap(self, locator: Locator) -> None:
        self.find(locator).click()

    def input(self, locator: Locator, value: str) -> None:
        el = self.find(locator)
        el.clear()
        el.send_keys(value)

    def swipe(self, direction: str) -> None:
        self.ds.ensure_alive()
        size = self.driver.get_window_size()
        cx, cy = size["width"] // 2, size["height"] // 2
        offsets = {
            "up": (0, -300), "down": (0, 300), "left": (-300, 0), "right": (300, 0)
        }
        dx, dy = offsets[direction]
        self.driver.swipe(cx, cy, cx + dx, cy + dy, 300)

    def screenshot(self, path: str) -> None:
        self.ds.ensure_alive()
        self.driver.get_screenshot_as_file(path)

    def page_source(self) -> str:
        self.ds.ensure_alive()
        return self.driver.page_source
