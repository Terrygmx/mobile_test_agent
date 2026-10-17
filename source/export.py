"""export.py — 12.3 metadata → Repository loader 可吃的 YAML（5.1 目录布局）。

为什么需要：`mta-source-scan` 产出 `source_metadata.json`（12.3，App 真相），
但 Repository 只吃 `elements/*.yaml` + `screens/*.yaml`（5.1）。本模块做
桥接：

  - generated 一律 `origin: source`（区别于手写 overrides 的 `origin: manual）；
  - **只导出 literal/constant**（dynamic/unknown 无 accessibility_id 可定位，
    导出会造出假 ID——12.2 不得猜值，宁缺毋滥）；
  - 落盘到 `generated/<build>/`（12.4），与 metadata.json 同址；
  - H16：generated 不手改，只能由 `mta repo generate` 重新生成（覆盖写）；
  - 同一 build 重新生成时，清理本批未产出的旧 `elements/screens/*.yaml`，
    避免源码里已改名/删除的 screen 继续以 stale 定义参与合并。

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

    返回导出数与 stale 清理数。已存在文件覆盖写（H16：本目录的唯一写者是
    本命令）。清理范围**严格限制**在目标 build 的 `elements/`、`screens/`
    顶层 `*.yaml`：非 YAML、子目录和 build 外的文件都不动。
    """
    out = Path(out_dir)
    elements_dir = out / "elements"
    screens_dir = out / "screens"

    # --- 元素：按 screen 分组，一个 screen 一个文件（多文档 YAML）---
    # id 去重（同 screen 同 id 多次出现：scanner 对重复赋值的覆盖已在
    # Swift 侧做，这里防御一次；后写覆盖与 UIKit 语义一致）
    by_screen: dict[str, dict[str, dict]] = {}
    for screen in metadata.get("screen_elements", []):
        name = screen.get("name")
        if not name:
            continue
        _validate_export_name(name, kind="screen")
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
                "metadata": _element_metadata(),
            }

    element_files: dict[Path, list[dict]] = {}
    for name, bucket in sorted(by_screen.items()):
        if bucket:
            _add_export_file(
                element_files, _yaml_target(elements_dir, name),
                [bucket[k] for k in sorted(bucket)],
                kind="element", name=name,
            )

    # --- 屏幕：**只从顶层 screens（mtaScreen marker 声明名）生成** ---
    # 教训（review P3-6）：元素 container struct 名不是 marker 名——
    # SpikeTabScreen(struct) vs SpikeTab(.mtaScreen 声明) 导出的 screen
    # 条目 marker 永远匹配不到真机，属猜值（12.2 同源纪律）。container 名
    # 仅用于元素归属，screen 定义必须来自 marker 声明。
    screen_names: set[str] = set()
    for name in metadata.get("screens", []):
        if not name:
            continue
        _validate_export_name(name, kind="screen")
        screen_names.add(name)
    screen_files: dict[Path, list[dict]] = {}
    for name in sorted(screen_names):
        _add_export_file(screen_files, _yaml_target(screens_dir, name), [{
            "schema_version": "1.0",
            "kind": "screen",
            "id": name,
            "marker": f"screen.{name}",
            "kind_hint": "page",
            "metadata": {"risk": "LOW", "origin": "source"},
        }], kind="screen", name=name)

    # 先完成全部写入，再删 stale。导出中途失败时旧产物仍在，不会出现
    # 「一半新、一半旧、缺口还被提前清掉」的更难诊断状态。
    _prepare_output_dir(out, elements_dir)
    _prepare_output_dir(out, screens_dir)
    for path, docs in sorted(element_files.items()):
        _write_yaml(path, docs)
    for path, docs in sorted(screen_files.items()):
        _write_yaml(path, docs)

    removed_elements = _prune_stale_yaml(elements_dir, set(element_files))
    removed_screens = _prune_stale_yaml(screens_dir, set(screen_files))
    return {
        "elements": sum(len(docs) for docs in element_files.values()),
        "screens": len(screen_names),
        "removed_elements": removed_elements,
        "removed_screens": removed_screens,
    }


def _validate_export_name(name: object, *, kind: str) -> None:
    """Screen 名会直接成为 YAML 文件名，拒绝任何可越出目标目录的值。"""
    if (not isinstance(name, str) or not name or name in {".", ".."}
            or "/" in name or "\\" in name or "\x00" in name):
        raise ValueError(
            f"invalid {kind} name for generated YAML: {name!r}"
        )


def _yaml_target(directory: Path, name: str) -> Path:
    """返回目录内的 YAML 目标；即使目录已存在为 symlink 也不得越界。"""
    _validate_export_name(name, kind="screen")
    target = directory / f"{name}.yaml"
    root = directory.resolve()
    try:
        target.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"generated YAML target escapes {directory}: {target}"
        ) from exc
    return target


def _add_export_file(
    files: dict[Path, list[dict]], path: Path, docs: list[dict], *,
    kind: str, name: str,
) -> None:
    """同一目录内 screen 名映射到同一文件时必须报错，不能静默后写覆盖。"""
    previous = next(
        (existing for existing in files
         if str(existing).casefold() == str(path).casefold()),
        None,
    )
    if previous is not None:
        previous_name = previous.stem
        raise ValueError(
            f"generated {kind} YAML filename collision: "
            f"{previous_name!r} and {name!r} both map to {path.name!r}"
        )
    files[path] = docs


def _prepare_output_dir(root: Path, directory: Path) -> None:
    """创建 elements/screens；拒绝通过目录 symlink 写到目标 build 之外。"""
    directory.mkdir(parents=True, exist_ok=True)
    # 先比较未解析的路径，再比较解析后的路径。只做后者时 root 与 directory
    # 会一起经 symlink 解析，`elements -> outside` 反而得到两个相同路径。
    expected = root.resolve() / directory.name
    if directory.resolve() != expected:
        raise ValueError(
            f"generated directory escapes build root {root}: {directory}"
        )


def _prune_stale_yaml(directory: Path, expected: set[Path]) -> int:
    """删除本批未产出的顶层 YAML；保留非 YAML、子目录和 symlink 目标。"""
    removed = 0
    for path in sorted(directory.glob("*.yaml")):
        if path in expected or not (path.is_file() or path.is_symlink()):
            continue
        path.unlink()
        removed += 1
    return removed


def _element_metadata() -> dict:
    """元素 metadata：risk/idempotency 留空由 policy 推导（7.4）——generated
    只提供 source 事实，人的策略判断在 overrides。无参数（P3-7：el 未用）。"""
    return {"origin": "source"}


def _write_yaml(path: Path, docs: list[dict]) -> None:
    """多文档 YAML（loader 用 safe_load_all 读）；空列表不写文件。"""
    if not docs:
        return
    text = yaml.safe_dump_all(docs, allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8")
