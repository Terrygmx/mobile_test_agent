"""loop_detector.py — Loop Detection（设计 7.3 / F8；Task 4.4 / P3-17）。

`is_loop` 是**纯计数函数**：窗口 `window` 条内，`current_fingerprint`
出现 ≥ `max_repeat` 次 → 是循环。指纹来自 P2 `screen_fingerprint`
（F8 同一套哈希），本模块**不做任何哈希、不做相似度**——只做精确相等
计数（红线：禁相似度算法）。

⚠️ 契约：`visited_fingerprints` **含** current（调用方把本次到达记进
visited 之后再问——「我到了这里，算不算循环」）。

⚠️ `current_fingerprint=None`（页面不可解析）→ **不计入窗口、永不判
loop**（open question #3 的默认口径：不计入 + 调用方审计留痕）。为什么
不判 loop：None 只说明「看不懂这个页面」，重复的 None 是解析器的问题，
不是 App 在循环——把 None 计入会让「解析坏了」伪装成「探索完成了」。

窗口/阈值非法 → `ValueError` fail-loud：静默的「不算 loop」会让人以为
检测在跑、其实从没生效。
"""
from __future__ import annotations

__all__ = ["is_loop"]


def is_loop(visited_fingerprints, current_fingerprint, *, window: int,
            max_repeat: int) -> bool:
    """窗口内重复指纹判定（纯函数，F13）。

    `visited_fingerprints`：到目前为止到过的指纹序列（**含** current）；
    `window`：往回看多少条（含本次）；`max_repeat`：窗口内同一指纹出现
    多少次算循环。
    """
    if window <= 0:
        raise ValueError(f"loop 窗口必须为正，got window={window}")
    if max_repeat <= 0:
        raise ValueError(f"max_repeated_state 必须为正，got {max_repeat}")
    if not current_fingerprint:
        return False          # None / 空串：不可解析页不计入（模块 docstring）
    recent = list(visited_fingerprints)[-window:]
    return recent.count(current_fingerprint) >= max_repeat
