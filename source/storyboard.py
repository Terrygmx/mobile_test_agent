"""storyboard.py — XIB/Storyboard XML 解析（设计 12.1）。

12.1 的工具分工：Swift 源码走 SwiftSyntax（mta-source-scan），**Storyboard /
XIB 走 XML 解析**——Xcode 的 Interface Builder 文件本质是 XML，元素
accessibilityIdentifier 以 `userDefinedRuntimeAttributes` 的
`accessibilityIdentifier` 键值对出现：

    <textField ...>
      <userDefinedRuntimeAttributes>
        <userDefinedRuntimeAttribute type="string"
                                     keyPath="accessibilityIdentifier"
                                     value="username_field"/>
      </userDefinedRuntimeAttributes>
    </textField>

**归属**：IB 文件里元素挂在 ViewController 下，容器取 ViewController 的
`customClass`（无则 `id`）——与 SwiftVisitor 的 struct 名同语义。

**不猜**（12.2 同源纪律）：`value` 缺失/占位（`$(PRODUCT_NAME)` 之类变量）
→ 不产出 literal，标 unknown 让 overrides 补。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

# IB 的可访问性属性键（12.1 只要求 identifier 与 label）
_A11Y_KEYS = ("accessibilityIdentifier", "accessibilityLabel")

# IB 内置控件 tag → 12.3 type（不追求全覆盖，未知 → other）
_IB_ELEMENT_TYPES = {
    "button": "button",
    "textField": "textfield",
    "secureTextField": "securetextfield",
    "label": "text",
    "imageView": "image",
    "switch": "switch",
    "tableView": "list",
    "collectionView": "list",
    "view": "view",
}


@dataclass(frozen=True)
class StoryboardElement:
    """一个从 IB 文件解析出的元素（12.3 元素级子集）。"""

    id: str                       # accessibilityIdentifier 值或占位
    accessibility_id: str | None  # 解析出真实值才非空
    resolution_type: str          # literal / unknown
    container_type: str | None    # ViewController customClass / id
    label: str | None             # accessibilityLabel 值
    element_type: str             # 12.3 type
    line: int                     # XML 行号（IB 文件大，行号是唯一排障锚点）
    source_file: str = ""         # 所在 .xib/.storyboard 文件名（排障锚点）


def _attr_value(el: ET.Element, key: str) -> str | None:
    """从**直接子** `userDefinedRuntimeAttributes` 里取指定 keyPath 的 value。

    只查直接子：IB 里 attributes 挂在控件自身，若用 el.iter() 会把祖先
    节点（view / subviews 容器）的 attributes 也吸进来——父 view 因此
    「获得」子控件的 accessibility_id，输出多出一堆假元素（测试实锤：
    3 元素变 13）。keyPath 匹配精确到 userDefinedRuntimeAttribute 的
    keyPath 属性，不用 type 猜。
    """
    for child in el:
        if child.tag != "userDefinedRuntimeAttributes":
            continue
        for attr in child:
            if attr.tag != "userDefinedRuntimeAttribute":
                continue
            if attr.get("keyPath") == key:
                return attr.get("value")
    return None


def _looks_like_placeholder(value: str | None) -> bool:
    """IB 的 value 可能是构建变量（`$(VAR)`，含中缀 `Ok$(Var)` 也一样）——
    那是运行时值，不猜。空串/纯空白同样不可定位。"""
    if value is None:
        return True
    v = value.strip()
    return (not v) or "$(" in v


def _element_type(tag: str) -> str:
    return _IB_ELEMENT_TYPES.get(tag, "other")


def parse_storyboard(path: str | Path) -> list[StoryboardElement]:
    """解析单个 .xib/.storyboard → 元素列表（无元素返回空 list，不报错）。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"storyboard not found: {p}")

    try:
        root = ET.parse(p).getroot()
    except ET.ParseError as e:
        raise ValueError(f"invalid IB XML in {p}: {e}") from None

    # 容器映射：element（IB 节点）→ 最近祖先 viewController 的 customClass/id。
    # IB 文件里 viewController 用 <viewController customClass="X"> 标记。
    container_of: dict[int, str | None] = {}
    _map_containers(root, None, container_of)

    elements: list[StoryboardElement] = []
    seen: set[int] = set()
    for el in root.iter():
        if id(el) in seen:
            continue
        a11y = _attr_value(el, "accessibilityIdentifier")
        label = _attr_value(el, "accessibilityLabel")
        if a11y is None and label is None:
            continue  # 非可访问性元素，跳过（大量 IB 节点）
        seen.add(id(el))
        line = getattr(el, "_line", 0) or 0
        if _looks_like_placeholder(a11y):
            good_label = None if _looks_like_placeholder(label) else label
            elements.append(StoryboardElement(
                id=f"UNKNOWN:{p.name}:{line}",
                accessibility_id=None,
                resolution_type="unknown",
                container_type=container_of.get(id(el)),
                label=good_label,
                element_type=_element_type(el.tag),
                line=line, source_file=p.name))
        else:
            ident = (a11y or "").strip()
            elements.append(StoryboardElement(
                id=ident,
                accessibility_id=ident,
                resolution_type="literal",
                container_type=container_of.get(id(el)),
                label=label,
                element_type=_element_type(el.tag),
                line=line, source_file=p.name))
    return elements


def _map_containers(el: ET.Element, current: str | None,
                    out: dict[int, str | None]) -> None:
    """预遍历：每个 IB 节点记录其所属 ViewController 名。

    customClass 优先（真类名），无则退回 `id` 属性（IB 内部 id）——沿用
    12.1「以所在 View/ViewController 类型为 container_type」。
    """
    here = current
    tag = el.tag
    if tag.endswith("ViewController") or tag == "viewController":
        here = el.get("customClass") or el.get("id") or "ViewController"
    out[id(el)] = here
    for child in el:
        _map_containers(child, here, out)


def scan_ib_files(paths: list[str | Path]) -> list[StoryboardElement]:
    """多文件扫描（metadata 侧调用）。"""
    out: list[StoryboardElement] = []
    for p in paths:
        out.extend(parse_storyboard(p))
    return out
