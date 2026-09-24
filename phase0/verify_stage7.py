"""Stage 7 验收：局部 reconciliation 三种结果 + 不做全 App 扫描。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from source.reconciliation import reconcile_local

METADATA = {
    "screen": "LoginDemoApp",
    "elements": [
        {"id": "login_button", "accessibilityId": "login_button", "resolution_type": "literal"},
        {"id": "username_field", "accessibilityId": "username_field", "resolution_type": "literal"},
        {"id": "dynamic_thing", "accessibilityId": None, "resolution_type": "unknown"},
    ],
}

PAGE = """<AppiumAUT>
  <XCUIElementTypeApplication name="LoginDemo">
    <XCUIElementTypeTextField name="username_field" label=""/>
    <XCUIElementTypeButton name="signin_button" label="登录"/>
  </XCUIElementTypeApplication>
</AppiumAUT>"""


def main() -> int:
    print("[1] DRIFT：源码有 login_button，运行时没有（被改名 signin_button）...")
    r = reconcile_local("login_button", "LoginDemoApp", METADATA, PAGE)
    assert r["status"] == "DRIFT", r
    assert "signin_button" in r["candidates_in_runtime"], r
    print(f"    status={r['status']}, candidates={r['candidates_in_runtime']}")

    print("[2] MATCH：username_field 两边都有 ...")
    r = reconcile_local("username_field", "LoginDemoApp", METADATA, PAGE)
    assert r["status"] == "MATCH", r
    print(f"    status={r['status']}")

    print("[3] UNKNOWN：两边都没有 ...")
    r = reconcile_local("ghost_thing", "LoginDemoApp", METADATA, PAGE)
    assert r["status"] == "UNKNOWN", r
    print(f"    status={r['status']}")

    print("[4] unknown 类型元素不进入 source_ids（不硬猜）...")
    r = reconcile_local("dynamic_thing", "LoginDemoApp", METADATA, PAGE)
    assert r["status"] == "UNKNOWN", r
    print("    ok")

    print("\n✅ Stage 7 PASS — 局部 reconciliation（DRIFT/MATCH/UNKNOWN）通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
