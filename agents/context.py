"""context.py — Scoped Agent Context（设计 §9.3 / F3；Task 6.3 / P3-28）。

设计 §9.3 原文（两参签名）：

```python
def build_agent_context(task: AgentTask, knowledge: KnowledgeSources) -> AgentContext:
    # 只检索当前任务相关的子集，全部经由 P2 的 KnowledgeSources：
    #   current_screen       -> knowledge.graph_query(scope=current_screen)
    #   relevant_experience  -> knowledge.experience_lookup(app_id, screen_id, target_id)
    #   relevant_history     -> knowledge.trace_history(filters={screen: current_screen})
    #   relevant_source      -> source_metadata 的当前 Screen 子集
    # 不做"把全部 Source + 全部 Trace 塞进去"的全文 RAG。
```

## 两处签名偏离（登记）

1. **两参 → 显式 kwargs**：`graph_query` 的真签名要 `app_id`（P2 实现里
   app_id 是图的范围第一段，fail-loud 不猜）、`experience_lookup` 要
   `screen_id`/`target_id`——设计的两参签名把它们藏进 task 拿不到。显式传入
   而不是从 goal 字符串里**猜**。
2. **`relevant_history` 不做屏级过滤**（⚠️ **用户拍板 2026-10-11**）：trace 的
   `steps` 表不记 screen（P1 schema 如此），`trace_history` 对 `screen_id`
   过滤键 **fail-loud**（`experience/knowledge.py` 明文「静默接受一个过滤不了
   的键，等于把『想过滤没过滤』伪装成『结果恰好都对』」）。故用可用键
   （`app_build` + `limit`）取最近步骤；屏级收窄等数据源补齐——handover
   「待拍板三项」之一**保持登记，不闭**。

四字段全部经 `KnowledgeSources` 四方法（F3）——不直接 SQL、不新建平行检索
接口。`impact_of` 是 planner 的检索面，本 builder 的四个字段**不消费**它
（少调是设计，不是遗漏）。

F13：纯函数（对 knowledge 的查询是读，不写任何状态），离设备可测。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = ["AgentContext", "build_agent_context"]

# relevant_history 的缺省条数——「相关历史」是决策参考不是全量回放；
# 上限先取初版（与探索 loop window 同量级），校准后回填。
DEFAULT_HISTORY_LIMIT = 20


@dataclass(frozen=True)
class AgentContext:
    """一次任务检索出的**相关子集**（快照——frozen，决策依据事后不可改）。

    `current_screen` 是图里该屏的子集（`Screen` 对象列表，通常 0 或 1 个——
    新屏不在图里是常态）；`relevant_source` 是 source_metadata 里该屏的
    元素列表（dict）；`relevant_history` 是 trace 的最近步骤。
    """

    app_id: str
    current_screen: tuple = ()
    relevant_experience: tuple = ()
    relevant_history: tuple = ()
    relevant_source: tuple = ()
    meta: dict[str, Any] = field(default_factory=dict)


def build_agent_context(task, knowledge, *, app_id: str = "",
                        current_screen: str | None = None,
                        target_id: str | None = None,
                        app_build: str | None = None,
                        source_metadata: Mapping | None = None,
                        history_limit: int = DEFAULT_HISTORY_LIMIT
                        ) -> AgentContext:
    """只检索当前任务相关子集（§9.3），全部经 `KnowledgeSources`（F3）。

    `app_id` 必填——`graph_query` 的范围第一段，缺了不猜（fail-loud）。
    `current_screen` / `target_id` 缺省 None（任务刚开始还没观测到屏）：
    屏级检索（graph 子集 / experience）跳过而不是拿 None 去瞎查。
    """
    if not app_id:
        raise ValueError("app_id 必填——graph_query/experience_lookup 的范围"
                         "第一段（不给不猜，设计的两参签名不可实现，偏离登记"
                         "见模块 docstring）")

    # ① current_screen：图里该屏的子集（没有 → 空元组，新屏是常态不是错误）。
    #    `ScreenNode` 的屏字段是 `screen_id`（`graph/models.py`）；别的图替身
    #    可能叫 `id`——两者都认，但不猜第三种。
    screens: tuple = ()
    if current_screen:
        graph = knowledge.graph_query(app_id)
        nodes = getattr(graph, "nodes", None)
        if nodes is None:                      # Mapping 形态的图替身兜底
            nodes = (getattr(graph, "current_screen", None),)
        screens = tuple(
            s for s in (nodes or ())
            if s is not None and current_screen in (
                getattr(s, "screen_id", None), getattr(s, "id", None)))

    # ② relevant_experience：屏 + 元素都在才查（缺一是瞎查）。
    experiences: tuple = ()
    if current_screen and target_id:
        experiences = tuple(knowledge.experience_lookup(
            app_id, current_screen, target_id))

    # ③ relevant_history：可用键取最近 N 条。⚠️ **不传 screen_id**（用户拍板
    #    的登记偏离——数据源没有屏信息，真实现对它是 fail-loud）。
    filters: dict[str, Any] = {"limit": max(int(history_limit), 0)}
    if app_build:
        filters["app_build"] = app_build
    history = tuple(knowledge.trace_history(filters))

    # ④ relevant_source：注入的 source_metadata 的当前屏子集（本地切片，不查库）。
    source: tuple = ()
    if current_screen and source_metadata:
        for entry in (source_metadata.get("screen_elements") or ()):
            if entry.get("name") == current_screen:
                source = tuple(entry.get("elements") or ())
                break

    return AgentContext(app_id=app_id, current_screen=screens,
                        relevant_experience=experiences,
                        relevant_history=history, relevant_source=source)
