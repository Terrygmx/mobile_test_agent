"""Stage 2 验收：WDA 健康检查 + 自动恢复。

1. 连接并确认 health_check=True
2. 人为破坏底层 session（模拟 WDA 死亡）
3. ensure_alive() 应自动 restart 并恢复
4. infra_events.jsonl 应有 WDA_DEAD + WDA_RESTARTED 各一条
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from session.device_session import DeviceSession, InfraError

CAPS = {
    "platform_name": "iOS",
    "automation_name": "XCUITest",
    "device_name": "iPhone 14",
    "platform_version": "18.5",
    "udid": "AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E",
    "bundle_id": "com.phaset0.logindemo",
    "no_reset": True,
    "new_command_timeout": 120,
}


def main() -> int:
    infra_log = Path(tempfile.mkdtemp()) / "infra_events.jsonl"
    ds = DeviceSession("http://127.0.0.1:4723", CAPS, infra_log=infra_log)

    print("[1] connect + health check ...")
    ds.connect()
    assert ds.health_check(), "初始 health_check 失败"
    old_session = ds.driver.session_id
    print(f"    session={old_session}, healthy")

    print("[2] 破坏底层 session（模拟 WDA 死亡）...")
    ds.driver.command_executor._request = lambda *a, **k: (_ for _ in ()).throw(
        ConnectionError("simulated WDA death")
    )
    assert not ds.health_check(), "破坏后 health_check 应为 False"
    print("    health_check correctly False")

    print("[3] ensure_alive 自动恢复 ...")
    ds.ensure_alive()
    new_session = ds.driver.session_id
    assert new_session != old_session, "session 应已重建"
    print(f"    new session={new_session}, healthy")

    print("[4] infra 事件检查 ...")
    events = [line for line in infra_log.read_text().splitlines() if line]
    types = [e.split('"event_type": "')[1].split('"')[0] for e in events]
    assert "WDA_DEAD" in types and "WDA_RESTARTED" in types, types
    print(f"    events={types}")

    # 恢复后真实操作可用（WDA 真的活着，不是假恢复）
    ds.ensure_alive().find_element("accessibility id", "login_button")
    print("\n✅ Stage 2 PASS — WDA health check + 自愈 + infra 事件全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
