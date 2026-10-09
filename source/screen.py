"""current_screen() 运行时判定（设计 13.2，Task 1.4 / P1-03）。

纯函数（H18）：输入 page_source XML 字符串 + Repository，不发网络、不碰设备。

判定规则（13.2）：
  - 收集「repo 已登记 Screen 的 marker 且 visible=true」的节点；
  - 0 个 → status=CURRENT_SCREEN_UNKNOWN；
  - 1 个 → status=FOUND，screen=该 Screen；
  - 多个：若恰有一个 kind_hint ∈ {modal, overlay} → 取它（覆盖层压住底层 page，
    用户看到的是它）；否则 → SCREEN_AMBIGUOUS（含 kind_hint: page 多个）。

marker 匹配严格按 repo 中 ScreenDef.marker（`screen.<Name>`），不无差别扫描
`screen.` 前缀——未登记的 `screen.x` 不是 marker（避免 App 侧误挂的 id 干扰判定）。
`marker_visible()` 是 `wait_for(screen, active)` 的廉价路径支撑（13.2 注）。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from repository.resolver import Repository
from source.hashing import stable_hash

# status 常量（8.2 failure_type 同名语义；FOUND 非失败态）
CURRENT_SCREEN_UNKNOWN = "CURRENT_SCREEN_UNKNOWN"
SCREEN_AMBIGUOUS = "SCREEN_AMBIGUOUS"
FOUND = "FOUND"

# 压底判定：恰一 modal/overlay 时取它（13.2）
TOP_LAYER_HINTS = ("modal", "overlay")


@dataclass(frozen=True)
class ScreenResult:
    """13.2 判定结果；screen 仅在 status=FOUND 时非 None。"""

    status: str  # CURRENT_SCREEN_UNKNOWN / SCREEN_AMBIGUOUS / FOUND
    screen: str | None = None
    visible_markers: tuple[str, ...] = ()  # 调试用：参与判定的 marker 名（screen id）


def _marker_to_screen(repo: Repository) -> dict[str, str]:
    """marker → screen_id 映射（generated 被 override 同名 marker 覆盖）。

    TODO(M3, R8-3)：override 改 marker 后，generated 旧 marker 仍映射同一
    Screen——树中新旧 marker 同时 visible 会把同一 screen 计两次，多 marker
    分支可能误判 AMBIGUOUS。M3 接入 generated 时改为 override marker 取代
    generated marker（旧映射不保留），或 hits 按 screen_id 去重。
    """
    marker_to_screen = {
        s.marker: s_id for s_id, s in repo.generated_screens.items()
    }
    marker_to_screen.update({
        s.marker: s_id for s_id, s in repo.override_screens.items()
    })
    return marker_to_screen


def _visible_marker_nodes(page_source: str, repo: Repository) -> list[tuple[str, ET.Element]]:
    """返回 [(screen_id, node)]：repo 登记的 marker 且 visible=true。"""
    marker_to_screen = _marker_to_screen(repo)
    if not marker_to_screen:
        return []
    root = ET.fromstring(page_source)
    hits: list[tuple[str, ET.Element]] = []
    for el in root.iter():
        name = el.get("name")
        if name is not None and name in marker_to_screen \
                and el.get("visible", "true") != "false":
            hits.append((marker_to_screen[name], el))
    return hits


def current_screen(page_source: str, repo: Repository) -> ScreenResult:
    """13.2 纯函数判定。XML 非法 → ValueError（调用方决定失败分类）。"""
    try:
        hits = _visible_marker_nodes(page_source, repo)
    except ET.ParseError as exc:
        # ElementTree.ParseError 是 ValueError 子类，这里显式转译保持契约清晰
        raise ValueError(f"invalid page_source XML: {exc}") from exc
    if not hits:
        return ScreenResult(CURRENT_SCREEN_UNKNOWN, None, ())
    if len(hits) == 1:
        return ScreenResult(FOUND, hits[0][0], (hits[0][0],))

    # 多 marker：恰一 modal/overlay → 取它；否则 AMBIGUOUS
    top = [s_id for s_id, _ in hits
           if repo.screen_kind_hint(s_id) in TOP_LAYER_HINTS]
    if len(top) == 1:
        return ScreenResult(FOUND, top[0], tuple(s_id for s_id, _ in hits))
    return ScreenResult(SCREEN_AMBIGUOUS, None, tuple(s_id for s_id, _ in hits))


def marker_visible(page_source: str, repo: Repository, screen_id: str) -> bool:
    """`wait_for(screen, active)` 廉价路径（13.2 注）：只做目标 marker 的
    存在性 + visible 检查，省去完整多 marker 判定分支与后续处理。"""
    marker_to_screen = _marker_to_screen(repo)
    root = ET.fromstring(page_source)
    for el in root.iter():
        name = el.get("name")
        if name is not None and marker_to_screen.get(name) == screen_id \
                and el.get("visible", "true") != "false":
            return True
    return False


def screen_fingerprint(page_source: str) -> str | None:
    """当前页面的**结构指纹**（设计 4.1 注 / E8 的观测面，Task 2.4）。

    定义：页面上可见元素的 `name`/`label` 去重排序后的 sha256 前 16 位。

    定位与边界（写给 M3）：
      - 只做**观测**——fingerprint **不是主键的一部分**，变化只触发
        `REVALIDATION_REQUIRED`（E8），绝不驱动清空或拒绝；
      - 不依赖 Repository（纯页面函数）——「当前页面结构是否仍与历史观测
        相似」这句话的主语是页面，不是 metadata；M3 若要把粒度收窄到
        「本屏登记元素的出现集合」，改本函数一处即可，调用方只拿字符串比。
      - 页面不可解析 → `None`（不猜指纹；比对侧对 None 一律记「不可观测」，
        不当成 mismatch——把「没看到」记成「变了」会让 REVALIDATION 误触发）。
    """
    try:
        root = ET.fromstring(page_source)
    except ET.ParseError:
        return None
    names: set[str] = set()
    for el in root.iter():
        if el.get("visible", "true") == "false":
            continue
        for attr in ("name", "label"):
            v = el.get(attr)
            if v:
                names.add(v)
    if not names:
        return None
    # Task 3.3 / P3-11：改调共享哈希原语（F8）。行为逐字不变——原实现即
    # `sha256("\n".join(sorted(names)).encode("utf-8")).hexdigest()[:16]`，
    # `stable_hash` 做的正是同一件事（不排序由调用方负责）。
    return stable_hash(sorted(names))
