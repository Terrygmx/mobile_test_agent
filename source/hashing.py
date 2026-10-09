"""hashing.py — 共享哈希原语（设计 F8；Task 3.3 / P3-11）。

F8 的红线是「**复用 P2 `screen_fingerprint` 机制 / 同一套哈希工具，不新造一套
相似度算法**」。但两处指纹的**输入形状不同**：

| 指纹 | 输入 | 规范化 |
|---|---|---|
| `source/screen.py::screen_fingerprint` | XML `page_source` | 可见元素 `name`/`label` 去重排序 |
| `candidates/fingerprint.py::test_fingerprint` | `TestCase` 的 step 序列 | 步骤 → 规范 token（保留顺序） |

「字面调用同一个函数」不可行，红线落地为**共享同一哈希原语**——即本模块的
`stable_hash`。规范化（排序 / 去重 / 顺序 / 字段编码）**由调用方负责**，本函数
只做「拼接 → sha256 → 前 16 hex」。

**只做精确哈希相等**：不做任何相似度 / 模糊匹配 / 编辑距离（F8 明文禁止）。
"""
from __future__ import annotations

import hashlib
from collections.abc import Iterable

__all__ = ["stable_hash"]


def stable_hash(parts: Iterable[str]) -> str:
    """把一组字符串按**给定顺序**拼接后取 sha256 的前 16 hex（64 bit）。

    - 分隔符固定 `"\\n"`、编码固定 UTF-8 —— 同样输入必得同样输出（跨进程、
      跨机器、跨 Python 版本稳定；不掺时间戳 / 随机盐）。
    - **不排序、不去重**：顺序与集合语义由调用方决定（`screen_fingerprint`
      自己 `sorted(set(...))`；`test_fingerprint` 保留步骤顺序）。
    - 传入**裸 `str`** 会 raise `TypeError`：`str` 也是 `Iterable[str]`，会被
      逐字符迭代成一份「看起来正常、实则把每个字符当独立元素」的指纹——这是
      本仓反复踩的「形式合法、语义等于没有」形态（MEMORY §5.A），故显式
      fail-loud，而不是静默产出错误指纹。
    - 元素非 `str` → 由 `"\\n".join` 自然抛 `TypeError`（不吞、不 `str()` 强转：
      强转会让 `1` 与 `"1"` 撞成同一指纹）。
    """
    if isinstance(parts, str):
        raise TypeError(
            "stable_hash 需要 Iterable[str]（元素序列），不接受裸 str——"
            "裸 str 会被逐字符迭代。请用 [s] / (s,) 包装。")
    payload = "\n".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
