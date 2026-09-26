"""reconciliation.py — 局部 Source/Runtime 对比（Stage 7）。

ponytail: 纯函数；候选元素直接从 page_source XML 提取同 accessibility id。
review P0-3：metadata 元素带 screen 归属后，这里真正按 screen 过滤候选，
兑现"只 diff 当前 Screen"的性能红线。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET


def reconcile_local(expected_element_id: str, screen: str,
                    source_metadata: dict, runtime_page_source: str) -> dict:
    """只在 locator 失败时调用；只对比当前 screen 的 metadata 子集（性能红线）。"""
    all_elements = source_metadata.get("elements", [])

    # 当前 screen 的元素子集；screen 未知（空）时退化为全量，不静默丢信息
    if screen:
        screen_elements = [
            e for e in all_elements
            if e.get("screen") in (None, screen)  # 无归属的旧 metadata 元素也算在内
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
