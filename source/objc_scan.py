#!/usr/bin/env python3
"""objc_scan.py — ObjC 源码的 accessibility id 提取（Phase 1 改造）。

真实 App（nfcPushOnline）是 ObjC+UIKit，P0 的 swift_scan 只支持 Swift 链式调用。
ObjC 里的 id 是赋值语句：  any_view.accessibilityIdentifier = @"some_id";
本扫描器用正则提取该模式（ponytail：ObjC 赋值模式简单固定，AST 收益低；
accessibilityIdentifier 是唯一字面量约定，无误判空间）。

输出与 swift_scan 同 schema（elements 列表），供 metadata.py 统一消费。
"""

from __future__ import annotations

import re
import sys

# ObjC 赋值: receiver.accessibilityIdentifier = @"value";
# receiver 可以是 self.xxx / _xxx / view.xxx / [self xxx].xxx
ASSIGN = re.compile(
    r"^.*?(?P<receiver>[\w\.\[\]\s]+?)\.accessibilityIdentifier\s*=\s*"
    r'@(?P<value>"[^"]*"|\w+)'
)

# Xcode 折叠 UTF-16 转义后中文注释无影响；跳过注释行
COMMENT = re.compile(r"^\s*//")


def scan_file(path: str) -> list[dict]:
    elements = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            if COMMENT.match(line):
                continue
            m = ASSIGN.search(line)
            if not m:
                continue
            raw = m.group("value")
            if raw.startswith('"'):
                value = raw.strip('"')
                resolution = "literal"
            else:
                value = None
                resolution = "unknown"  # 变量/表达式，不硬猜（设计文档第 7 节）
            elements.append({
                "id": value or "UNKNOWN",
                "type": "unknown",
                "accessibilityId": value,
                "resolution_type": resolution,
                "source": {"file": path.rsplit("/", 1)[-1], "line": lineno},
            })
    return elements


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: objc_scan.py <file.m> [...]", file=sys.stderr)
        return 2
    out = []
    for p in sys.argv[1:]:
        out.extend(scan_file(p))
    import json
    print(json.dumps({"screen": "objc_sources", "elements": out},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
