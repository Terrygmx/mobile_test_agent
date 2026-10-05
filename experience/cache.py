"""cache.py — 进程内 Recovery Cache（设计 7.3；Task 3.3 / P2-08）。

设计原文：「Recovery Cache（**运行时加速层，不是权威数据**）」——它只缓存
「某失败签名下有哪些可用候选」（ranker 的输入集），命中省掉的是 Store 的
一次磁盘 lookup；候选被尝试时的 Guard 判定、样本记账（4.7）、状态推进
**全部照旧**。权威永远在 Store：缓存里的 Experience 是落库快照的副本，
进程重启即空，不参与任何持久化语义。

## E1 延伸（设计 7.3 第一条）：缓存不允许绕过安全校验

这条红线做进 **API 形状**而不是注释：`get` 返回裸 `list[Experience]`，
缓存里不存在「这条已验证 / 可直接执行」的标记——`RecoveryCache` 没有
任何方法会产出 EXECUTE 或「跳过 Guard」的许可，「绕过」在类型上不可
表达。消费契约（引擎接线时生效）：命中 → 候选列表照常逐条喂
`experience_runtime_guard` → 判定、记账与未命中路径完全同一条代码。

## 失效策略：`app_build` 变化不整体清空

cache_key 的五元组**不含 app_build**（设计 7.3 公式原样）——新 build
不触发失效风暴，某条 Experience 在新 build 下还能不能用，交给
validated_builds（E7）语义自然消化。⚠️ **当前 Guard 链没有任何 build
检查步骤**（review_p2_task33 P3-2 定档挂账）：实际行为是「不检查、
EXECUTE 照常、成功后追 build 记账」（E7）——docstring 曾把「Guard
BLOCK 记失败样本」当成既有机制，那是错的（照做会在每次 build 变化后
给每条 Experience 首用记失败样本，污染 E6 滑动窗口）。裁决机制三选一
已挂账到 plan Task 4.3 接线前置，拍板前**不得**按旧措辞自行实现。
`clear()` 是显式运维动作，没有自动钩子。

## 对 7.3 公式的两处登记细化

1. `None` 段落映射为空串——f-string 的字面 `'None'` 会与真值为 `'None'`
   的段撞车；失败签名的缺段（如屏指纹读不到）用 `''` 表达。
2. 段内含 `'|'` 直接 `ValueError`——管道是键分隔符，混进段里会让两个
   不同签名静默撞出同一个 key（宁可 fail-loud）。
"""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict

from experience.models import Experience

__all__ = ["cache_key", "RecoveryCache"]

_DEFAULT_CAPACITY = 128


def cache_key(app_id: str | None, screen_id: str | None,
              target_id: str | None, failure_type: str | None,
              screen_fingerprint: str | None) -> str:
    """设计 7.3：`sha256("app|screen|target|failure|fingerprint")`。

    两个细化（见模块 docstring）：None → ''；段内含 '|' 拒绝。
    """
    parts = (app_id, screen_id, target_id, failure_type, screen_fingerprint)
    for part in parts:
        if part is not None and "|" in part:
            raise ValueError(
                f"cache_key 组件含分隔符 '|'，会与其他签名撞 key：{part!r}")
    joined = "|".join("" if p is None else p for p in parts)
    return hashlib.sha256(joined.encode()).hexdigest()


class RecoveryCache:
    """进程内 LRU：key → 候选列表（Experience 深拷贝，入出皆拷贝）。

    「入出皆拷贝」的理由：缓存的正本一旦被运行时引用改写（后续引擎
    记账、状态推进都可能碰对象），下一次命中就会拿到被悄悄改过的历史
    ——「不是权威数据」不等于「可以被随意污染」。拷贝成本对几十字节级
    的 Experience 可忽略，换取缓存完整性可推理。

    线程安全：与 ExperienceStore 同款 `threading.Lock`（进程内并发读
    写不撕裂 OrderedDict 的内部链表）；不做跨进程同步——设计 7.2/7.3
    的口径是单进程 Runner 内的加速层，多 Runner 各自持有。
    """

    def __init__(self, capacity: int = _DEFAULT_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        self._capacity = capacity
        self._entries: OrderedDict[str, list[Experience]] = OrderedDict()
        self._lock = threading.Lock()
        # hits/misses 允许锁外读（review_p2_task33 P3-4）：CPython int
        # += 在 GIL 下无撕裂，计数是观测用 approximate 值不是账本——
        # 给它加锁只会白花一次争用，别「好心」加。
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> list[Experience] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return [e.model_copy(deep=True) for e in entry]

    def put(self, key: str, candidates: list[Experience]) -> None:
        if not candidates:
            # 空列表不进缓存：miss 的语义是「没查过/没有候选」，把
            # 「lookup 到空」也缓存会让 Store 侧新增候选后本进程永远
            # 看不见（缓存是加速层，不能反向变成黑洞）。
            return
        with self._lock:
            self._entries[key] = [e.model_copy(deep=True) for e in candidates]
            self._entries.move_to_end(key)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)   # 逐出最久未用

    def clear(self) -> None:
        """显式运维动作（设计 7.3 没有自动失效钩子——app_build 变化
        不整体清空）。"""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
