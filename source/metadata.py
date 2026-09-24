"""metadata.py — 组装 source_metadata.json（Stage 6）。

ponytail: Swift 扫描器输出元素列表，这里只补 build 元信息并落盘。
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

# 与 source/main.swift（swift_scan）一起更新；Phase 0 锁定行为
PARSER_VERSION = "0.1.0"
SCAN_BINARY = "/tmp/swift_scan"  # 由 make scan 构建（见 Makefile）


def build_metadata(swift_file: str, out_path: str | Path) -> dict:
    subprocess.run([SCAN_BINARY, swift_file], check=True, capture_output=True)
    raw = json.loads(subprocess.run([SCAN_BINARY, swift_file],
                                    check=True, capture_output=True, text=True).stdout)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    meta = {
        "app_version": "debug",
        "build": "local",
        "git_commit": commit,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "parser_version": PARSER_VERSION,
        **raw,
    }
    Path(out_path).write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return meta
