#!/usr/bin/env python3
"""P1-03 Spike 验证脚本：验证 mtaScreen() marker 方案在 Tab/Nav/Sheet 三场景的可行性。

判定标准（设计 13.3）：
  ① 每个 mtaScreen() marker 在 page_source 中唯一可见；
  ② 同屏子元素 accessibilityIdentifier 不被容器折叠（仍可通过 id 定位）。
留档：page_source 全文写 out/spike/，汇总写 out/spike/spike_summary.json。

用法：
  前置：Appium 127.0.0.1:4723 ready；模拟器 booted；LoginDemo 已装（含 SpikeScreen.swift）。
  .venv/bin/python phase0/spike_screen.py
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appium import webdriver
from appium.options.ios import XCUITestOptions
from appium.webdriver.common.appiumby import AppiumBy

UDID = "AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E"
BUNDLE_ID = "com.phaset0.logindemo"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "out", "spike")

# (场景名, marker id, 必须唯一可 find 的子元素 id 列表)
SCENES = [
    ("TabView", "screen.SpikeTab", ["tab_inner_title", "tab_inner_button"]),
    ("NavigationStack", "screen.SpikeNavDetail", ["nav_detail_text"]),
    ("Sheet", "screen.SpikeSheet", ["sheet_inner_text"]),
]

# 切 tab 多级定位：SwiftUI tabItem 的 accessibilityIdentifier 不暴露进 XCUITest 树
# （实测 page_source 中 TabBar 按钮只暴露 label name / SF Symbol 图名），故
# accessibility id → label name → TabBar 内按钮序号 三级 fallback。
TAB_STRATEGIES = {
    "NavigationStack": [
        (AppiumBy.ACCESSIBILITY_ID, "spike_tab_nav"),
        (AppiumBy.NAME, "Nav"),
        (AppiumBy.IOS_CLASS_CHAIN, "**/XCUIElementTypeTabBar/XCUIElementTypeButton[2]"),
    ],
    "Sheet": [
        (AppiumBy.ACCESSIBILITY_ID, "spike_tab_sheet"),
        (AppiumBy.NAME, "Sheet"),
        (AppiumBy.IOS_CLASS_CHAIN, "**/XCUIElementTypeTabBar/XCUIElementTypeButton[3]"),
    ],
}


def make_driver():
    opts = XCUITestOptions()
    opts.platform_name = "iOS"
    opts.device_name = "iPhone 14"
    opts.udid = UDID
    opts.automation_name = "XCUITest"
    opts.bundle_id = BUNDLE_ID
    opts.no_reset = True
    opts.set_capability("appium:processArguments", {"args": ["-UITestSpikeScreen"], "env": {}})
    return webdriver.Remote("http://127.0.0.1:4723", options=opts)


def drive_to_scene(driver, scene_name):
    """切换到目标场景：Nav/Sheet 要点对应 tab；Sheet 还需打开 sheet。

    返回 (True, None) 或 (False, 最后一次失败原因)。逐级 fallback，失败不抛出，
    由 main 落盘 page_source 留证。
    """
    if scene_name == "TabView":
        return True, None
    last_error = "no strategy defined"
    for by, value in TAB_STRATEGIES.get(scene_name, []):
        try:
            driver.find_element(by, value).click()
            time.sleep(1)  # tab 切换动画
            if scene_name == "Sheet":
                driver.find_element(AppiumBy.ACCESSIBILITY_ID, "sheet_open_button").click()
                time.sleep(1)  # sheet 弹出动画
            return True, None
        except Exception as exc:  # noqa: BLE001 — 逐级 fallback，末级失败才上报
            last_error = f"{getattr(by, 'name', by)!s}={value!r}: {exc.__class__.__name__}"
    return False, last_error


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    results = {}

    for scene_name, marker_id, child_ids in SCENES:
        driver = make_driver()
        src = ""
        nav_error = None
        try:
            nav_ok, nav_error = drive_to_scene(driver, scene_name)
            src = driver.page_source
        finally:
            try:
                driver.quit()
            except Exception:  # noqa: BLE001 — quit 失败不吞掉主流程结果
                pass

        # 切换失败也落盘 page_source——证据优先，Nav/Sheet 场景不再裸奔
        with open(os.path.join(OUT_DIR, f"page_source_{scene_name}.xml"), "w") as f:
            f.write(src)

        if not nav_ok:
            results[scene_name] = {
                "passed": False,
                "error": f"drive_to_scene failed: {nav_error}",
                "marker_present": None,
                "marker_unique": None,
                "children": {},
            }
            continue

        scene = {
            "marker_present": marker_id in src,
            "marker_unique": src.count(marker_id) == 1,
            "children": {cid: src.count(cid) == 1 for cid in child_ids},
        }
        scene["passed"] = (
            scene["marker_present"]
            and scene["marker_unique"]
            and all(scene["children"].values())
        )
        results[scene_name] = scene

    summary = {"scenes": results, "verdict": all(r["passed"] for r in results.values())}
    with open(os.path.join(OUT_DIR, "spike_summary.json"), "w") as f:
        f.write(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["verdict"] else 1


if __name__ == "__main__":
    sys.exit(main())
