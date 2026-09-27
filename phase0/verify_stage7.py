"""Stage 7 验收：局部 reconciliation 三种结果 + 不做全 App 扫描。

review R2-0 后 mock 升级为 swift_scan 真实 schema（元素带 screen 字段、
顶层有 screens 列表），并新增 [5] 回归用例：顶层 screen（文件名）与
元素级 screen（struct 名）不一致时 DRIFT 仍必须判出——这正是 R2-0 的断点。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from source.reconciliation import reconcile_local

# 真实 schema：来自 make scan 后 /tmp/swift_scan 的输出形状
METADATA = {
    "screen": "LoginDemoApp",  # 顶层 = 文件名（swift_scan 语义）
    "screens": ["LoginView", "HomeView"],
    "elements": [
        {"id": "login_button", "accessibilityId": "login_button",
         "resolution_type": "literal", "screen": "LoginView"},
        {"id": "username_field", "accessibilityId": "username_field",
         "resolution_type": "literal", "screen": "LoginView"},
        {"id": "home_page", "accessibilityId": "home_page",
         "resolution_type": "literal", "screen": "HomeView"},
        {"id": "dynamic_thing", "accessibilityId": None,
         "resolution_type": "unknown", "screen": "LoginView"},
    ],
}

# 旧 schema 兼容：元素无 screen 字段（早期产物仍可被消费）
OLD_METADATA = {
    "screen": "LoginDemoApp",
    "elements": [
        {"id": "login_button", "accessibilityId": "login_button",
         "resolution_type": "literal"},
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
    r = reconcile_local("login_button", "LoginView", METADATA, PAGE)
    assert r["status"] == "DRIFT", r
    assert "signin_button" in r["candidates_in_runtime"], r
    print(f"    status={r['status']}, candidates={r['candidates_in_runtime']}")

    print("[2] MATCH：username_field 两边都有 ...")
    r = reconcile_local("username_field", "LoginView", METADATA, PAGE)
    assert r["status"] == "MATCH", r
    print(f"    status={r['status']}")

    print("[3] UNKNOWN：两边都没有 ...")
    r = reconcile_local("ghost_thing", "LoginView", METADATA, PAGE)
    assert r["status"] == "UNKNOWN", r
    print(f"    status={r['status']}")

    print("[4] unknown 类型元素不进入 source_ids（不硬猜）...")
    r = reconcile_local("dynamic_thing", "LoginView", METADATA, PAGE)
    assert r["status"] == "UNKNOWN", r
    print("    ok")

    print("[5] R2-0 回归：顶层 screen（文件名）≠ 元素级 screen（struct 名）...")
    # 传入顶层 screen 文件名，元素归属 LoginView —— 走 declared-screens 分支，
    # DRIFT 必须仍能判出（R2-0 断点：此前此场景被过滤成空集判成 UNKNOWN）
    r = reconcile_local("login_button", "LoginDemoApp", METADATA, PAGE)
    assert r["status"] == "DRIFT", r
    # 旧 schema（元素无 screen 字段）也必须继续工作
    r2 = reconcile_local("login_button", "LoginDemoApp", OLD_METADATA, PAGE)
    assert r2["status"] == "DRIFT", r2
    print("    新旧 schema 均判出 DRIFT ✓")

    print("[6] screen 过滤生效：HomeView 元素不污染 LoginView 判断 ...")
    # 用 LoginView 查询 home_page（属 HomeView）：跨 screen 元素必须被过滤出
    # source_ids → 源码/运行时都判无 → UNKNOWN（而非误判 DRIFT/MATCH）
    META_NO_DECL = {**METADATA, "screens": None}
    r = reconcile_local("home_page", "LoginView", META_NO_DECL, PAGE)
    assert r["status"] == "UNKNOWN", r  # home_page 被screen过滤 → 不误判
    # 对照：同元素按其真实归属 HomeView 查询 → 源码有、运行时无 → DRIFT（语义正确）
    r2 = reconcile_local("home_page", "HomeView", META_NO_DECL, PAGE)
    assert r2["status"] == "DRIFT", r2
    print("    跨 screen 候选不串页 ✓（误查他页 UNKNOWN，正查本页 DRIFT）")

    print("\n✅ Stage 7 PASS — 局部 reconciliation（DRIFT/MATCH/UNKNOWN + R2-0 回归）通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
