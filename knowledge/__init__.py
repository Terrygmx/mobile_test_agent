"""knowledge — P3 的知识装配入口（F3；Task 2.2 / P3-06）。

设计 F3：「**知识检索复用 P2 `KnowledgeSources` Protocol**
（`experience_lookup / graph_query / impact_of / trace_history`），**不新建平行的
检索接口**」。

所以本包**只有一个工厂函数**：把 P2 的既有数据源（experience store / trace 库 /
用例集合）组合成一个满足该 Protocol 的实例。检索逻辑本体仍在
`experience/knowledge.py::P2KnowledgeSources`——这里不新增方法、不新口径。
"""
from knowledge.retrieval import build_knowledge

__all__ = ["build_knowledge"]
