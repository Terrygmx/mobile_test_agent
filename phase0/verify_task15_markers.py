#!/usr/bin/env python3
"""Task 1.5（P1-04）验证脚本：五屏 mtaScreen() marker 唯一可见 + 导航流子元素可定位。

流程：登录 → Home；切 Search；点 cell_beta → Detail；回 Home 切 Profile；
logout 回 Login。每屏落盘 page_source 到 out/p1_task15/。
判定：每屏 marker 存在且唯一；关键子元素 id 存在。
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
OUT_DIR = os.path.join(ROOT, "out", "p1_task15")

# (场景名, marker id, 必须存在的子元素 id 列表)
SCENES = [
    ("Login", "screen.LoginView", ["username_field", "password_field", "login_button"]),
    ("Home", "screen.HomeView", ["home_page", "welcome_label", "cell_alpha", "cell_beta", "cell_gamma", "go_search", "go_profile"]),
    ("Search", "screen.SearchView", ["search_title", "search_field", "search_results", "cell_beta"]),
    ("Detail", "screen.DetailView", ["detail_title", "detail_item_key"]),
    ("Profile", "screen.ProfileView", ["profile_title", "logout_button"]),
]


def make_driver():
    opts = XCUITestOptions()
    opts.platform_name = "iOS"
    opts.device_name = "iPhone 14"
    opts.udid = UDID
    opts.automation_name = "XCUITest"
    opts.bundle_id = BUNDLE_ID
    opts.no_reset = True
    return webdriver.Remote("http://127.0.0.1:4723", options=opts)


def dump(driver, name, results, marker_id, child_ids):
    src = driver.page_source
    with open(os.path.join(OUT_DIR, f"page_source_{name}.xml"), "w") as f:
        f.write(src)
    scene = {
        "marker_present": marker_id in src,
        "marker_unique": src.count(marker_id) == 1,
        "children": {cid: cid in src for cid in child_ids},
    }
    scene["passed"] = scene["marker_present"] and scene["marker_unique"] and all(scene["children"].values())
    results[name] = scene
    return scene["passed"]


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    results = {}
    driver = make_driver()
    try:
        time.sleep(2)  # 启动动画
        # Login（no_reset 下可能残留登录态：若直接看到 home_page 就跳过登录）
        src = driver.page_source
        if "screen.LoginView" in src:
            # 先留证再登录：登录后 page_source 已切到 Home，不能复用同一次抓取
            dump(driver, "Login", results, "screen.LoginView", SCENES[0][2])
            driver.find_element(AppiumBy.ACCESSIBILITY_ID, "username_field").send_keys("mta")
            driver.find_element(AppiumBy.ACCESSIBILITY_ID, "password_field").send_keys("pw")
            driver.find_element(AppiumBy.ACCESSIBILITY_ID, "login_button").click()
            time.sleep(1.5)
        else:
            results["Login"] = {"skipped": "already logged in", "passed": True}

        # Home
        dump(driver, "Home", results, "screen.HomeView", SCENES[1][2])

        # Search
        driver.find_element(AppiumBy.ACCESSIBILITY_ID, "go_search").click()
        time.sleep(1.5)
        dump(driver, "Search", results, "screen.SearchView", SCENES[2][2])

        # Detail（点 cell_beta）
        driver.find_element(AppiumBy.ACCESSIBILITY_ID, "cell_beta").click()
        time.sleep(1.5)
        dump(driver, "Detail", results, "screen.DetailView", SCENES[3][2])

        # 回 Home → Profile
        driver.back()
        time.sleep(1)
        driver.back()
        time.sleep(1)
        driver.find_element(AppiumBy.ACCESSIBILITY_ID, "go_profile").click()
        time.sleep(1.5)
        dump(driver, "Profile", results, "screen.ProfileView", SCENES[4][2])

        # logout 回 Login
        driver.find_element(AppiumBy.ACCESSIBILITY_ID, "logout_button").click()
        time.sleep(1.5)
        src = driver.page_source
        results["LogoutBackToLogin"] = {"passed": "screen.LoginView" in src}
        with open(os.path.join(OUT_DIR, "page_source_after_logout.xml"), "w") as f:
            f.write(src)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    summary = {"scenes": results, "verdict": all(r.get("passed", False) for r in results.values())}
    with open(os.path.join(OUT_DIR, "task15_summary.json"), "w") as f:
        f.write(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["verdict"] else 1


if __name__ == "__main__":
    sys.exit(main())
