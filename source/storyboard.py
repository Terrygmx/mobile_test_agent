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
import xml.parsers.expat
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


class _LineNumberTreeBuilder(ET.TreeBuilder):
    """记录每个 start 标签行号的 TreeBuilder。

    驱动者必须是 `xml.parsers.expat`（其 `CurrentLineNumber` 公开可用）——
    `ET.XMLParser` 不注入底层 expat（Python 3.11 实测 `parser` 属性恒
    None），所以由 expat 直接驱动 TreeBuilder。
    行号 = start 事件触发行；UNKNOWN id 用它组 "file:line"，同文件多个
    unknown 元素因此可区分（review P2-1）。
    """

    def __init__(self, lines: dict[int, int]) -> None:
        super().__init__()
        self._lines = lines
        self._expat: xml.parsers.expat.XMLParserType | None = None

    def start(self, tag, attrs):  # type: ignore[override]
        el = super().start(tag, attrs)
        expat = getattr(self, "_expat", None)
        if expat is not None:
            self._lines[id(el)] = expat.CurrentLineNumber
        return el


def _parse_with_lines(p: Path, lines_by_id: dict[int, int]) -> ET.Element:
    """expat 直接驱动 TreeBuilder 解析文件（行号入 lines_by_id）。"""
    tree_builder = _LineNumberTreeBuilder(lines_by_id)
    expat = xml.parsers.expat.ParserCreate()
    # 显式挂上：TreeBuilder.start 需要读 expat.CurrentLineNumber
    tree_builder._expat = expat            # noqa: SLF001（内部约定）
    for handler in ("StartElementHandler", "EndElementHandler",
                    "CharacterDataHandler"):
        setattr(expat, handler, getattr(tree_builder, {
            "StartElementHandler": "start",
            "EndElementHandler": "end",
            "CharacterDataHandler": "data",
        }[handler]))
    try:
        with open(p, "rb") as fh:
            data = fh.read()
        expat.Parse(data, True)      # 一次性读完全部
    except xml.parsers.expat.ExpatError as e:
        raise ValueError(f"invalid IB XML in {p}: {e}") from None
    return tree_builder.close()


def parse_storyboard(path: str | Path) -> list[StoryboardElement]:
    """解析单个 .xib/.storyboard → 元素列表（无元素返回空 list，不报错）。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"storyboard not found: {p}")

    # 行号捕获：标准 ET.parse 的 Element 没有 _line 属性（自定义 TreeBuilder
    # 才有）——裸 getattr(el, "_line", 0) 恒 0，UNKNOWN id 退化成
    # "file:0" 同文件多个 unknown 不可区分（review P2-1 实锤）。
    lines_by_id: dict[int, int] = {}
    root = _parse_with_lines(p, lines_by_id)

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
        line = lines_by_id.get(id(el), 0)
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
    tag 判定用 ascii lowercase endswith：IB 家族 tag 大小写混用
    （viewController / tableViewController / collectionViewController /
    navigationController / tabBarController / splitViewController /
    pageViewController / glkViewController…）——原来只匹配
    endswith("ViewController") 会漏掉 camelCase 家族（review P3-7）。
    """
    here = current
    tag = el.tag.lower()
    if tag.endswith("viewcontroller") or tag == "viewcontrollerplacement":
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
