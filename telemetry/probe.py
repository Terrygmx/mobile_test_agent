"""probe.py — 「App 崩了吗」的判定入口（设计 P1 §8.2 的第三条信号）。

`CrashCollector` 回答「有没有崩溃报告」，本模块回答「**这一次 run 期间**
App 崩了吗」——两者的区别是时间窗与等待策略，不是数据源。

## 三条口径（与 `tests/unit/test_crash_probe.py` 的反钉一一对应）

1. **探测失败 → `None`**（不是异常）。崩溃报告是**旁路证据**：探测自己坏了
   不该让整次回归失败。`None` 的语义是「没有证据说它崩了」，调用方据此
   **保持旧行为**（`WDA_FAILURE` / `INFRA_FAILURE`）——不确定不升级成「崩了」。
2. **等待有界**。崩溃报告是**异步**写的（勘察 §8.2 实测：SIGSEGV 后 6~8s
   才落盘），所以要等；但一次崩溃不能让回归挂在无限轮询上，故 `wait_seconds`
   到点就放弃。`wait_seconds=0` = 单次探测（离线/测试路径）。
3. **`since` 下推到 collector**（不在拿到结果之后再筛）——收窄发生在取数处，
   与 `CrashCollector.latest` 的口径同一处，不给「筛漏了」留第二种写法。
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Callable

from telemetry.collectors import CrashReport

__all__ = ["CrashProbe"]


class CrashProbe:
    """「自 `since` 起该 bundle 崩过吗」——有界等待，失败即 `None`。"""

    def __init__(self, *, collector, bundle_id: str,
                 wait_seconds: float = 12.0, poll_seconds: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if not bundle_id:
            raise ValueError(
                "bundle_id 必填——没有范围就没有判定（不猜：拿别的 App 的"
                "崩溃报告当本 App 的，方向恰好是「把环境问题记成 App 缺陷」）")
        self.collector = collector
        self.bundle_id = bundle_id
        self.wait_seconds = max(float(wait_seconds), 0.0)
        self.poll_seconds = max(float(poll_seconds), 0.0)
        # `sleep` 注入是为了**测试不等真实时间**（F13 的精神：判据可离设备验证）
        self._sleep = sleep

    def crashed_since(self, since: datetime) -> CrashReport | None:
        """返回 `since` 之后的第一份崩溃报告；没有 → `None`。

        ⚠️ **等待按「探测次数」算，不按墙钟**：`sleep` 是注入的（测试不睡真实
        时间），墙钟截止条件在注入替身下**永不成立**——第一版就是这么写的，
        测试当场抓到一个 1000 万次的空转。次数预算 = `1 + wait/poll`，
        与墙钟等价（poll 秒一次）且在注入下确定。
        """
        budget = 1 + int(self.wait_seconds / self.poll_seconds) \
            if self.poll_seconds > 0 else 1
        for attempt in range(budget):
            rep = self._ask(since)
            if rep is not None:
                return rep
            if attempt < budget - 1:
                self._sleep(self.poll_seconds)
        return None

    def _ask(self, since: datetime) -> CrashReport | None:
        """一次探测。**任何失败都翻成 `None`**（见模块 docstring 口径 1）。"""
        try:
            return self.collector.latest(self.bundle_id, since=since)
        except Exception:  # noqa: BLE001 — 旁路证据不推翻主结论
            return None
