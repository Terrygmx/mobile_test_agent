"""Stage 3 验收：App 生命周期与 WDA session 解耦。

验收点：reinstall/relaunch 后 Appium session id 不变（WDA 不重建）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phase0.verify_stage2 import CAPS
from session.app_session import AppSession, UnsupportedResetError
from session.device_session import DeviceSession

APP = str(
    Path.home()
    / "Library/Developer/Xcode/DerivedData/LoginDemo-ctemhqejzgzbnqhigvxhleqcmbjj"
    / "Build/Products/Debug-iphonesimulator/LoginDemo.app"
)


def main() -> int:
    ds = DeviceSession("http://127.0.0.1:4723", CAPS)
    ds.connect()
    session_id = ds.driver.session_id
    app = AppSession(ds, "com.phaset0.logindemo")

    print("[1] terminate + launch（RELAUNCH）...")
    app.reset_state("RELAUNCH")
    assert ds.driver.session_id == session_id, "relaunch 不应重建 WDA session"

    print("[2] reinstall ...")
    app.reset_state("REINSTALL", app_path=APP)
    assert ds.driver.session_id == session_id, "reinstall 不应重建 WDA session"
    print("    Appium session 全程稳定:", session_id[:8])

    print("[3] 不支持的策略应抛 UnsupportedResetError ...")
    try:
        app.reset_state("SNAPSHOT")
        raise AssertionError("SNAPSHOT 应被拒绝")
    except UnsupportedResetError as e:
        print(f"    correctly rejected: {e}")

    print("[4] reinstall 后元素仍可定位（App 真的被装回来了）...")
    ds.ensure_alive().find_element("accessibility id", "login_button")

    print("\n✅ Stage 3 PASS — App 生命周期与 WDA session 解耦验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
