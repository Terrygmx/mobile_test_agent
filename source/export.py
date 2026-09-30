"""export.py — 12.3 metadata → Repository loader 可吃的 YAML（5.1 目录布局）。

为什么需要：`mta-source-scan` 产出 `source_metadata.json`（12.3，App 真相），
但 Repository 只吃 `elements/*.yaml` + `screens/*.yaml`（5.1）。本模块做
桥接：

  - generated 一律 `origin: source`（区别于手写 overrides 的 `origin: manual）；
  - **只导出 literal/constant**（dynamic/unknown 无 accessibility_id 可定位，
    导出会造出假 ID——12.2 不得猜值，宁缺毋滥）；
  - 落盘到 `generated/<build>/`（12.4），与 metadata.json 同址；
  - H16：generated 不手改，只能由 `mta repo generate` 重新生成（覆盖写）。

屏幕导出策略：Screen 名取元素实际 container_type（12.1 归属）；marker
由 `mta repo generate` 侧从 `screens` 顶层名（mtaScreen marker 声明的
screen 名）补齐——IB/SwiftUI 的 container struct 名与 marker 名可能不同
（如 SpikeTabScreen struct vs SpikeTab marker），两者都要可解析。
"""
from __future__ import annotations

from pathlib import Path

import yaml

# 元素 type 白名单（5.2 ElementDef）。**必须与 repository.loader.ELEMENT_TYPES
# 一致**——export 侧放行一个 loader 不认的 type，生成的文件会当批加载失败
# （测试实锤：list/toggle）。以 loader 为单一真值源 import，杜绝白名单漂移。
from repository.loader import ELEMENT_TYPES

_VALID_TYPES = set(ELEMENT_TYPES)
_TYPE_FALLBACK = "other"


def _normalize_type(t: str | None) -> str:
    """scanner 的 type 可能超出 loader 白名单（如 SwiftUI 的 list/toggle）——
    归一到白名单或 other，别让 generated 因类型表漂移整批加载失败。"""
    if t in _VALID_TYPES:
        return t or _TYPE_FALLBACK
    return _TYPE_FALLBACK


def export_generated(metadata: dict, out_dir: str | Path) -> dict[str, int]:
    """12.3 metadata → generated/<build>/{elements,screens}/*.yaml。

    返回 {"elements": n, "screens": n}（n = **去重后**导出数）。已存在文件
    覆盖写（H16：本目录的唯一写者是本命令）。
    """
    out = Path(out_dir)
    elements_dir = out / "elements"
    screens_dir = out / "screens"
    elements_dir.mkdir(parents=True, exist_ok=True)
    screens_dir.mkdir(parents=True, exist_ok=True)

    # --- 元素：按 screen 分组，一个 screen 一个文件（多文档 YAML）---
    # id 去重（同 screen 同 id 多次出现：scanner 对重复赋值的覆盖已在
    # Swift 侧做，这里防御一次；后写覆盖与 UIKit 语义一致）
    by_screen: dict[str, dict[str, dict]] = {}
    for screen in metadata.get("screen_elements", []):
        name = screen.get("name")
        if not name:
            continue
        bucket = by_screen.setdefault(name, {})
        for el in screen.get("elements", []):
            a11y = el.get("accessibility_id")
            if not a11y:
                continue  # 12.2：不导出 dynamic/unknown
            bucket[a11y] = {
                "schema_version": "1.0",
                "kind": "element",
                "id": a11y,
                "screen": name,
                "type": _normalize_type(el.get("type")),
                "strategies": [{
                    "type": "accessibility_id",
                    "value": a11y,
                    "origin": "source",   # 扫描产物，与手写 manual 区分
                    "source_file": el.get("source", {}).get("file"),
                    "source_line": el.get("source", {}).get("line"),
                }],
                "metadata": _element_metadata(el),
            }

    element_count = 0
    for name, bucket in sorted(by_screen.items()):
        _write_yaml(elements_dir / f"{name}.yaml",
                    [bucket[k] for k in sorted(bucket)])
        element_count += len(bucket)

    # --- 屏幕：元素 container 名 + 顶层 screens 名（marker 声明名）---
    screen_names: set[str] = set(by_screen)
    screen_names.update(n for n in metadata.get("screens", []) if n)
    for name in sorted(screen_names):
        _write_yaml(screens_dir / f"{name}.yaml", [{
            "schema_version": "1.0",
            "kind": "screen",
            "id": name,
            "marker": f"screen.{name}",
            "kind_hint": "page",
            "metadata": {"risk": "LOW", "origin": "source"},
        }])

    return {"elements": element_count, "screens": len(screen_names)}


def _element_metadata(el: dict) -> dict:
    """元素 metadata：risk/idempotency 留空由 policy 推导（7.4）——generated
    只提供 source 事实，人的策略判断在 overrides。"""
    return {"origin": "source"}


def _write_yaml(path: Path, docs: list[dict]) -> None:
    """多文档 YAML（loader 用 safe_load_all 读）；空列表不写文件。"""
    if not docs:
        return
    text = yaml.safe_dump_all(docs, allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8")
