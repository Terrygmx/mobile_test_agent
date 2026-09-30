"""metadata.py — 组装 source_metadata.json（设计 12.3 / P1-09）。

职责边界：Swift 侧 `mta-source-scan`（SwiftSyntax 两遍扫描）产出 12.3 的
`screens/screen_elements`；这里只做**项目级编排与 build 元信息**：

  1. 多文件扫描（一次进程扫全部输入——常量表要跨文件，见 ConstantTable）；
  2. 12.3 顶层字段补齐（app_version / build / git_commit / parser_version /
     generated_at）；
  3. 落盘 `repository/generated/<build>/source_metadata.json`（H16：generated
     不手改，只能由本流程重新生成）。

hard 约束（12.2）：SwiftSyntax 不做类型检查也不做跨模块符号解析——无法唯一
解析的元素一律 dynamic/unknown（**不得猜值**），人工在 overrides 补齐。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

# 与 Package.swift / 源码一起更新；Schedule 与 source_metadata.json 的
# parser_version 联动（消费方据此判断 schema 兼容性）
PARSER_VERSION = "0.2.0"

# 扫描器二进制：优先 SwiftPM release 产物，退回 debug（本地开发）。
# 构建：cd source/swift_scan && swift build -c release
_SCAN_PKG = Path(__file__).parent / "swift_scan"
OBJC_SCAN = str(Path(__file__).parent / "objc_scan.py")


class ScanError(RuntimeError):
    """扫描失败（工具缺失/构建过期/子进程非零退出）。fail-loud：调用方不得
    把「没扫成」当成「扫出来零元素」——那会把构建问题伪装成覆盖率 0。"""


def _scan_binary() -> Path:
    """release 优先、debug 兜底。候选在**函数内**构造（模块级 tuple 会在
    import 时求值，monkeypatch _SCAN_PKG 就失效——测试实锤）。"""
    for p in (_SCAN_PKG / ".build" / "release" / "mta-source-scan",
              _SCAN_PKG / ".build" / "debug" / "mta-source-scan"):
        if p.exists():
            return p
    raise ScanError(
        f"mta-source-scan binary not found under {_SCAN_PKG}/.build/ "
        f"(release|debug); build it: cd source/swift_scan && swift build")


def _git_commit() -> str:
    """review P2-5 同款保护：非 git 目录不崩，返回 unknown。"""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def scan_files(files: list[str | Path]) -> dict:
    """项目级扫描：一次进程扫全部输入（12.2 常量表跨文件——常数定义与被引用
    不在同一文件时必须能解析）。

    返回 mta-source-scan 的原始输出（12.3 形状：screens + screen_elements）。
    ObjC 文件（.m/.h）旁路到 Phase 1 的 objc_scan.py（正则版，独立进程）。
    """
    if not files:
        raise ScanError("scan_files: no input files")
    paths = [str(f) for f in files]
    swift = [p for p in paths if not p.endswith(
        (".m", ".h", ".xib", ".storyboard"))]
    objc = [p for p in paths if p.endswith((".m", ".h"))]
    ib = [p for p in paths if p.endswith((".xib", ".storyboard"))]

    out: dict = {"screens": [], "screen_elements": []}
    if swift:
        proc = subprocess.run(
            [str(_scan_binary()), *swift],
            capture_output=True, text=True)
        if proc.returncode != 0:
            raise ScanError(
                f"mta-source-scan failed (exit {proc.returncode}): "
                f"{proc.stderr.strip()[:400]}")
        if not proc.stdout.strip():
            raise ScanError("mta-source-scan produced empty output")
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise ScanError(f"scanner output is not JSON: {e}") from None
    if objc:
        # objc_scan.py 逐个文件跑（Phase 1 形态），结果合入同一 schema
        proc = subprocess.run(
            ["/usr/bin/python3", OBJC_SCAN, *objc],
            capture_output=True, text=True)
        if proc.returncode != 0:
            raise ScanError(
                f"objc_scan failed (exit {proc.returncode}): "
                f"{proc.stderr.strip()[:400]}")
        objc_out = json.loads(proc.stdout or '{"screen_elements": []}')
        # objc_scan 顶层 screen 名 → 归一到 screen_elements（schema 对齐）
        for scr in objc_out.get("screen_elements", []):
            out.setdefault("screen_elements", []).append(scr)
        out["screens"] = sorted(
            set(out.get("screens", []))
            | {s["name"] for s in objc_out.get("screen_elements", [])})
    if ib:
        # XIB/Storyboard 走 XML 解析（12.1）——element 归属 IB ViewController
        # customClass；转成与 Swift 侧同 schema 的 screen_elements 条目。
        from source.storyboard import scan_ib_files

        ib_elements = scan_ib_files(ib)
        by_container: dict[str, list[dict]] = {}
        for e in ib_elements:
            key = e.container_type or "UNKNOWN"
            by_container.setdefault(key, []).append({
                "id": e.id,
                "type": e.element_type,
                "accessibility_id": e.accessibility_id,
                "resolution_type": e.resolution_type,
                "container_type": e.container_type,
                "label": e.label,
                "source": {"file": e.source_file, "line": e.line},
            })
        for name, els in by_container.items():
            out.setdefault("screen_elements", []).append(
                {"name": name, "elements": els})
            out.setdefault("screens", []).append(name)
    return out


def build_metadata(files: list[str | Path], out_path: str | Path, *,
                   app_version: str = "debug", build: str = "local") -> dict:
    """扫描 + 组装 12.3 metadata 并落盘。

    `out_path` 通常为 `repository/generated/<build>/source_metadata.json`
    （12.4 构建产物布局；H16：只能重新生成，不得手改）。
    """
    raw = scan_files(files)
    meta = {
        "app_version": app_version,
        "build": build,
        "git_commit": _git_commit(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "parser_version": PARSER_VERSION,
        **raw,
    }
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    return meta
