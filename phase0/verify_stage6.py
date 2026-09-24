"""Stage 6 验收：SwiftSyntax 扫描 LoginDemoApp.swift → source_metadata.json。

验收点：
1. login_button / username_field 等 literal 元素被提取，含行号
2. 构造一个非字面量 identifier 的文件，验证 resolution_type: unknown（不硬猜）
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from source.metadata import build_metadata

SWIFT_SRC = Path(__file__).resolve().parent.parent / "ios_demo/LoginDemo/LoginDemoApp.swift"


def main() -> int:
    # 先编译扫描器（幂等）
    toolchain = "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr"
    subprocess.run(
        ["swiftc", "-o", "/tmp/swift_scan", "main.swift",
         "-I", f"{toolchain}/lib/swift/host", "-L", f"{toolchain}/lib/swift/host",
         "-Xlinker", "-rpath", "-Xlinker", f"{toolchain}/lib/swift/host"],
        check=True, cwd=Path(__file__).resolve().parent.parent / "source",
    )

    out = Path("out/source_metadata.json")
    meta = build_metadata(str(SWIFT_SRC), out)
    print(f"[1] metadata.json 生成: {out}, commit={meta['git_commit']}")

    ids = {e["id"]: e for e in meta["elements"]}
    for expected in ["login_button", "username_field", "password_field", "home_page"]:
        assert expected in ids, f"缺 {expected}: {list(ids)}"
        assert ids[expected]["resolution_type"] == "literal"
        assert ids[expected]["source"]["line"] > 0
    print(f"[2] literal 元素 {len(ids)} 个，行号齐全")

    print("[3] 非字面量 → unknown ...")
    with tempfile.NamedTemporaryFile("w", suffix=".swift", delete=False) as f:
        f.write('import SwiftUI\nstruct T: View {\nlet dynId = "x"\nvar body: some View {\n'
                'Text("hi").accessibilityIdentifier(dynId)\n}\n}\n')
        dyn_path = f.name
    dyn_meta = build_metadata(dyn_path, Path(tempfile.mktemp()))
    unknown = [e for e in dyn_meta["elements"] if e["resolution_type"] == "unknown"]
    assert unknown, f"应识别出 unknown 元素: {dyn_meta}"
    assert unknown[0]["accessibilityId"] is None
    print(f"    unknown 元素 line={unknown[0]['source']['line']}, 不硬猜值 ✓")

    print("\n✅ Stage 6 PASS — SwiftSyntax Source Intelligence 链路通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
