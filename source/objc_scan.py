#!/usr/bin/env python3
"""objc_scan.py — ObjC 源码的 accessibility id 提取（Phase 1 改造）。

真实 App（nfcPushOnline）是 ObjC+UIKit，P0 的 swift_scan 只支持 Swift 链式调用。
ObjC 里的 id 是赋值语句：  any_view.accessibilityIdentifier = @"some_id";
本扫描器用正则提取该模式（ponytail：ObjC 赋值模式简单固定，AST 收益低；
accessibilityIdentifier 是唯一字面量约定，无误判空间）。

输出与 swift_scan 同 schema（elements 列表），供 metadata.py 统一消费。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# ObjC 赋值: receiver.accessibilityIdentifier = @"value";
# receiver 可以是 self.xxx / _xxx / view.xxx / [self xxx].xxx
ASSIGN = re.compile(
    r"^.*?(?P<receiver>[\w\.\[\]\s]+?)\.accessibilityIdentifier\s*=\s*"
    r'@(?P<value>"[^"]*"|\w+)'
)

# @implementation ClassName —— 元素归属的 container（12.3 schema 的
# container_type）。没有（category/文件级代码）退回文件名去后缀。
IMPL = re.compile(r"^\s*@implementation\s+(\w+)")

# Xcode 折叠 UTF-16 转义后中文注释无影响；跳过行注释（review P2-8：块注释另行处理）
LINE_COMMENT = re.compile(r"^\s*//")
# /* ... */ 块注释整体剥离，防止其中的赋值语句误报
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def scan_file(path: str) -> list[dict]:
    """提取单文件全部 `.accessibilityIdentifier = @"..."` 赋值。

    元素 schema 与 swift_scan 对齐（12.3）：`accessibility_id` /
    `container_type` / `id` / `resolution_type` / `source` / `type`。
    """
    elements = []
    container = Path(path).stem
    with open(path, encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            line = BLOCK_COMMENT.sub("", line)
            if LINE_COMMENT.match(line):
                continue
            m_impl = IMPL.match(line)
            if m_impl:
                container = m_impl.group(1)
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
                # review R2-6：unknown 的 id 带位置，与 swift_scan 同步，避免多元素重复
                "id": value or f"UNKNOWN:{Path(path).name}:{lineno}",
                "type": "unknown",
                "accessibility_id": value,
                "container_type": container,
                "resolution_type": resolution,
                "source": {"file": Path(path).name, "line": lineno},
            })
    return elements


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: objc_scan.py <file.m> [...]", file=sys.stderr)
        return 2
    # 输出 schema 必须与 metadata.py scan_files 的 objc 分支**以及** swift_scan
    # 对齐：顶层 `screen_elements`（条目 {name, elements}）。历史上这里输出的是
    # {"screen", "elements"}（Phase 1 独立形态），而 metadata.py 读的是
    # screen_elements——键名失配使 ObjC 分支**静默合并空**（2026-10-15 修：
    # 乐速通 repo generate 扫 614 个 .m 得 0 元素，根因即此；且测试全 stub 掉
    # scan_files，round-trip 无覆盖，缺陷因此存活）。
    by_container: dict[str, list[dict]] = {}
    for p in sys.argv[1:]:
        for e in scan_file(p):
            by_container.setdefault(e["container_type"], []).append(e)
    print(json.dumps({
        "screens": sorted(by_container),
        "screen_elements": [
            {"name": name, "elements": elements}
            for name, elements in sorted(by_container.items())
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
