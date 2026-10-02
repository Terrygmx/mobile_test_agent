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
        # H11 的主保证在 DeviceSession.connect()（WDA 自愈后新 driver 也会带上）。
        # 这里对「Executor 先于 connect 构造」的用法补一次；无 driver 时静默跳过。
        self.apply_implicit_wait_zero()

    def apply_implicit_wait_zero(self) -> None:
        """H11：Appium implicit wait 固定为 0。

        否则 `not_exists` 类等待会被隐式等待拖慢（每条策略各自等一轮），轮询
        interval 形同虚设。当前无 driver（如未 connect）时跳过，connect 后由
        DeviceSession.connect 兜底设置。
        """
        driver = self.ds.driver
        if driver is not None:
            driver.implicitly_wait(0)

    @property
    def driver(self) -> WebDriver:
        return self.ds.driver

    def find(self, locator: Locator):
        """按策略顺序查找。每个 action 前先 ensure_alive（设计文档硬约束）。

        Phase 1 改造：真实 App 是 ObjC/UIKit，元素无 accessibility id，
        依赖 label（按钮文案）与 class+label 组合定位，故增加 class_chain 策略。
        """
        self.ds.ensure_alive()
        by_map = {
            "accessibility_id": "accessibility id",
            "predicate": "-ios predicate string",
            "class_chain": "-ios class chain",
        }
        for strat in locator:
            by = by_map.get(strat["type"])  # review P1-6：未知策略报清晰错误
            if by is None:
                raise ValueError(f"unknown locator strategy {strat['type']!r}, "
                                 f"supported={sorted(by_map)}")
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

    # --- 已定位元素上的动作（M4：恢复重发 9.2 用） ---
    # 恢复引擎按恢复后策略找到元素再重发——若按原 locator 重找，漂移场景
    # 下原策略必然再失败（M4 Gate 真机实锤：LLM 候选校验全过后死在重发）。
    # ensure_alive 仍每个动作前执行（设计硬约束，与 find 同源）。

    def tap_element(self, element) -> None:
        self.ds.ensure_alive()
        element.click()

    def input_element(self, element, value: str) -> None:
        self.ds.ensure_alive()
        element.clear()
        element.send_keys(value)

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
