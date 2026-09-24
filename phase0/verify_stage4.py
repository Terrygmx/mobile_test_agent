"""Stage 4 验收：Executor 全操作 + 唯一性校验 + 失败类型区分。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from executor.executor import AmbiguousElement, ElementNotFound, Executor
from phase0.verify_stage2 import CAPS
from session.device_session import DeviceSession


def main() -> int:
    ds = DeviceSession("http://127.0.0.1:4723", CAPS)
    ds.connect()
    ex = Executor(ds)
    login_btn = [{"type": "accessibility_id", "value": "login_button"}]

    print("[1] input（含自动 clear）...")
    ex.input([{"type": "accessibility_id", "value": "username_field"}], "stage4_user")
    val = ex.find([{"type": "accessibility_id", "value": "username_field"}]).get_attribute("value")
    assert val == "stage4_user", val
    print(f"    value={val}")

    print("[2] tap + screenshot + swipe + page_source ...")
    ex.tap(login_btn)
    ex.screenshot("out/stage4.png")
    ex.swipe("up")
    src = ex.page_source()
    assert "XCUIElementType" in src
    print("    ok")

    print("[3] ElementNotFound（不存在的元素）...")
    try:
        ex.find([{"type": "accessibility_id", "value": "no_such_element_xyz"}])
        raise AssertionError("应抛 ElementNotFound")
    except ElementNotFound:
        print("    ok")

    print("[4] AmbiguousElement（宽 predicate 匹配多个元素）...")
    try:
        ex.find([{"type": "predicate", "value": "visible == true"}])
        raise AssertionError("应抛 AmbiguousElement")
    except AmbiguousElement:
        print("    ok (fail closed)")

    print("\n✅ Stage 4 PASS — Executor + Locator + 唯一性校验全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
