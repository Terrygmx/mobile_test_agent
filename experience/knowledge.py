"""knowledge.py — KnowledgeSources 四查询接口（设计 19；Task 5.5）。

设计原文：「P2 不实现规划能力，但为 P3 准备四类可查询的知识」——P3 的
自动探索 / 测试规划 / Subagent 建立在这四个查询之上，**不需要重新设计
数据层**。本模块因此是**纯组合**：每个方法都是既有模块的薄委派，无新
存储、无新查询口径（review_p2_task54 建议动作 2：直接以 impact 的两个
原语 collect_case_refs / ref_to_cases 为底，避免第四种查询口径）。

| 查询 | 底座（唯一真值源） |
|---|---|
| `experience_lookup` | `ExperienceStore.lookup`（消费视角：REJECTED 排除，7.1 修订）+ `rank_experiences`（5.1 消费排序）——与引擎 `try_experiences` 同一条口径 |
| `graph_query` | `graph.builder.read_trace_steps` + `build_runtime_graph`（trace 是运行时图的唯一来源，12.1） |
| `impact_of` | `source.coverage.collect_case_refs` / `ref_to_cases` 原语 + `graph.impact.affected_testcases`（11.3 反向索引） |
| `trace_history` | `graph.builder.read_trace_steps`（trace 的 steps 即历史；不复制第二套读取逻辑） |

只读纪律：本接口**没有任何写方法**——P3 的规划能力消费知识，不生产
知识（写路径仍归 Runner / review accept / promoter 各自的闸门）。
"""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from experience.models import Experience
from experience.ranker import rank_experiences
from graph.builder import build_runtime_graph, read_trace_steps
from graph.impact import affected_testcases, ref_index

if TYPE_CHECKING:
    # RuntimeGraph 真实类型标注（review_p2_task54 P3-3 同族：避免运行时
    # 循环 import，P3 消费方拿到的不是裸 object）。取**定义处**
    # `graph.models`（`graph.diff` 只是转口，review_p3_task11 §0 指出的
    # 无谓耦合：知识层不该为了拿一个模型类型而依赖 diff 模块）。
    from graph.models import RuntimeGraph

__all__ = ["KnowledgeSources", "P2KnowledgeSources"]

# 「调用方没给」的哨兵（review_p3_task11 P2-1）。**不能**用 `None` 兼作默认
# 值：`read_trace_steps(app_build=None)` 的语义是「取库里全部 run」，而
# 「没给」应当落到构造时的 `self._app_build`——两者混用一个值就会让
# `{"app_build": None}`（显式要求不过滤）被静默改写成构造值。
_UNSET = object()


@runtime_checkable
class KnowledgeSources(Protocol):
    """设计 19 的四查询形状（P3 的知识底座）。"""

    def experience_lookup(self, app_id: str, screen_id: str,
                          target_id: str) -> list[Experience]: ...

    def graph_query(self, app_id: str) -> "RuntimeGraph": ...

    def impact_of(self, target_id: str) -> tuple[str, ...]: ...

    def trace_history(self, filters: dict) -> list: ...


class P2KnowledgeSources:
    """KnowledgeSources 的 P2 实现：组合现有 store / graph / impact / trace。

    `cases`：已解析的用例对象（`pipeline.discover()` 或
    `parse_testcase_dict` 的产物）——`impact_of` 的反向索引数据源。
    `app_build`：`graph_query` / `trace_history` 的 trace 范围（两个 build
    不许混进一张图/一段历史，read_trace_steps 的判定在起作用）。
    """

    def __init__(self, *, experience_store, trace_db, cases=(),
                 app_build: str | None = None) -> None:
        self._store = experience_store
        self._trace_db = trace_db
        self._cases = list(cases)
        self._app_build = app_build

    def experience_lookup(self, app_id: str, screen_id: str,
                          target_id: str) -> list[Experience]:
        return rank_experiences(
            self._store.lookup(app_id, screen_id, target_id))

    def graph_query(self, app_id: str) -> "RuntimeGraph":
        steps, trace_app_id, _build = self._read_steps()
        if trace_app_id != app_id:
            # 设计 19 的签名按 app_id 查——trace 里是另一个 app 时不猜
            # 「也许你要的就是这份」，fail-loud（与 read_trace_steps 的
            # 「两个范围不许混」同一条纪律）。
            raise ValueError(
                f"trace 的 app_id 是 {trace_app_id!r}，与查询的 "
                f"{app_id!r} 不一致——graph_query 不跨 app 混图")
        return build_runtime_graph(steps, app_id=app_id)

    def _read_steps(self, app_build=_UNSET) -> tuple[list, str, str]:
        """read_trace_steps 的薄封装：把裸 sqlite 异常转成可诊断的域错误
        （review_p2_task55 P3-2——P3 规划能力会远程消费本接口，「库没
        初始化」不该以 `no such table` 的形态漏出去）。

        `app_build` 缺省（`_UNSET`）= 用构造时的 `self._app_build`；显式传入
        则**按次覆盖**（review_p3_task11 P2-1：早先本方法写死构造值，于是
        `trace_history` 里算出的 `app_build` 成了死变量——单 build 库「恰好
        返回正确数据」掩盖了问题，双 build 库则报「用 app_build= 过滤」而
        调用方正是这么做的）。传 `None` = 显式要求不过滤（原语语义）。
        """
        build = self._app_build if app_build is _UNSET else app_build
        try:
            return read_trace_steps(self._trace_db, app_build=build)
        except sqlite3.OperationalError as e:
            raise ValueError(
                f"trace 库不可读（未初始化或无表？）：{self._trace_db}"
                f"——{e}") from e

    def impact_of(self, target_id: str) -> tuple[str, ...]:
        return affected_testcases(target_id, ref_index(self._cases))

    def trace_history(self, filters: dict) -> list:
        """trace 的 steps 即历史（按 filters 过滤）。

        **过滤键**（Task 2.2 / P3-06 从 `{app_build, limit}` 扩到五个）：

        | 键 | 语义 | 值 |
        |---|---|---|
        | `app_build` | 范围（SQL 层，`_read_steps`） | `str` 或 `None`（None = 不过滤） |
        | `testcase_id` | 该用例的历史（`testcase_runs.testcase_id`） | 同上 |
        | `failure_type` | 该失败分类的历史（`steps.failure_type`） | 同上 |
        | `limit` | 取**最后** N 条 | 非负 `int` |
        | `screen_id` | ⚠️ **不可用**——见下 | — |

        **顺序语义**：其余过滤键**先**应用、`limit` **最后**——`limit=10` +
        `failure_type=APP_CRASH` 是「最近 10 条崩溃步骤」，不是「最近 10 条步骤里
        恰好崩溃的那些」。既有调用只传 `app_build`/`limit`，顺序变化对它们无影响。

        ⚠️ **`screen_id` 是 fail-loud 而不是静默放过**：trace 里**没有屏信息**——
        `steps` 表不记 screen，`target_id` 是**解析后的裸元素 id**（不含屏），
        `detail_json` 也没有（`runner._record` 的 payload 只有
        step_index/action_type/status/error/latency_ms）。**静默接受一个过滤不了的键，
        等于把「想过滤没过滤」伪装成「结果恰好都对」**——那正是本方法上面那条
        未知键检查要防的事，同一个道理不能对自己网开一面。
        数据源缺口与两条候选修法登记在 `docs/p3_data_audit.md`（Task 2.2 记录）。
        """
        unknown = set(filters) - {"app_build", "screen_id", "testcase_id",
                                  "failure_type", "limit"}
        if unknown:
            # 过滤键是接口契约的一部分：静默忽略拼错的键会把「想过滤没
            # 过滤」伪装成「结果恰好都对」。
            raise ValueError(f"未知过滤键: {sorted(unknown)}（支持: "
                             f"app_build, testcase_id, failure_type, limit；"
                             f"screen_id 见 docstring 的说明）")
        if "screen_id" in filters:
            raise ValueError(
                "screen_id 过滤暂不可用：trace 里没有屏信息（steps 表不记 screen，"
                "target_id 是解析后的裸元素 id，detail_json 也没有）。"
                "**不静默返回未过滤结果**——要么先按 testcase_id 缩小范围，"
                "要么等数据源补齐（见 docs/p3_data_audit.md 的 Task 2.2 记录）")
        app_build = filters.get("app_build", _UNSET)
        # 值也过闸门（与下面 limit 同款纪律）：非 str/None 的值会绑进 SQL 的
        # 等值比较、**一条也匹配不到**——「过滤生效了，只是库里没有」与
        # 「过滤参数是坏的」在结果上长得一样。
        if app_build is not _UNSET and not (
                app_build is None or isinstance(app_build, str)):
            raise ValueError(
                f"app_build 必须是 str 或 None，got {app_build!r}")
        steps, _app_id, _build = self._read_steps(app_build)
        # 逐字段过滤（`None` = 不过滤，与 app_build 同款语义；非 str → fail-loud）
        for key, attr in (("testcase_id", "testcase_id"),
                          ("failure_type", "failure_type")):
            value = filters.get(key, _UNSET)
            if value is _UNSET or value is None:
                continue
            if not isinstance(value, str):
                raise ValueError(f"{key} 必须是 str 或 None，got {value!r}")
            steps = [s for s in steps if getattr(s, attr) == value]
        limit = filters.get("limit")
        if limit is not None:
            if not isinstance(limit, int) or limit < 0:
                raise ValueError(f"limit 必须是非负 int，got {limit!r}")
            steps = steps[-limit:] if limit else []
        return steps
