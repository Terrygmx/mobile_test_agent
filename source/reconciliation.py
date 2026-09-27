"""reconciliation.py — 局部 Source/Runtime 对比（Stage 7）。

ponytail: 纯函数；候选元素直接从 page_source XML 提取同 accessibility id。
review P0-3：metadata 元素带 screen 归属后，这里真正按 screen 过滤候选，
兑现"只 diff 当前 Screen"的性能红线。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET


def reconcile_local(expected_element_id: str, screen: str,
                    source_metadata: dict, runtime_page_source: str) -> dict:
    """只在 locator 失败时调用；只对比当前 screen 的 metadata 子集（性能红线）。

    review R2-0：传入 screen 可能是顶层文件名（swift_scan 语义，不可作过滤键）。
    过滤基准是元素级 screen 与 metadata 声明的 screens 集合：
    1. 传入 screen ∈ declared（调用方给了真实 struct 名）→ 严格按当前 screen 过滤；
    2. 传入 screen ∉ declared（顶层文件名）→ 退化为「元素 screen ∈ declared 全集」，
       保证 DRIFT 仍判得出（此前此处过滤成空集 → 恒 UNKNOWN，回归 [5]）；
    3. 元素无 screen 字段（旧 schema）→ 兜底纳入。
    """
    all_elements = source_metadata.get("elements", [])
    declared = set(source_metadata.get("screens") or ([screen] if screen else []))

    if screen and screen in declared:
        screen_elements = [
            e for e in all_elements
            if e.get("screen") is None or e.get("screen") == screen
        ]
    elif declared:
        screen_elements = [
            e for e in all_elements
            if e.get("screen") is None or e.get("screen") in declared
        ]
    else:
        screen_elements = all_elements

    source_ids = {
        e["accessibilityId"] for e in screen_elements
        if e.get("accessibilityId") and e["resolution_type"] == "literal"
    }

    # runtime 候选：页面里所有节点的 name 属性（accessibility id 落在 name 上）
    runtime_ids = {
        el.get("name") for el in ET.fromstring(runtime_page_source).iter()
        if el.get("name")
    }

    expected_in_source = expected_element_id in source_ids
    expected_in_runtime = expected_element_id in runtime_ids

    if expected_in_source and not expected_in_runtime:
        status = "DRIFT"  # 源码认为有，运行时没有 → 元素被改名/删除
    elif expected_in_runtime:
        status = "MATCH"
    else:
        status = "UNKNOWN"  # 源码 metadata 里也没有（可能 screen 判断错了）

    return {
        "status": status,
        "expected": expected_element_id,
        "screen": screen,
        "candidates_in_runtime": sorted(
            rid for rid in runtime_ids
            if rid and rid not in source_ids and not rid.startswith(("XCUI", "wd"))
        ),
    }
