"""subagents — 三个业务 Subagent 的薄封装（设计 §9.1；Task 6.4 / P3-29）。

## 为什么是独立包而不是 `agents/`

薄封装要同时 import **两**边：`agents.base.Subagent`（框架侧：状态机、
工具层、预算）与 `planner.plan` / `explorer.Explorer` /
`diagnosis.analyzer.analyze`（业务侧本体）。而本仓两条 import 级守卫把
两个方向都封死了，且**都不开豁免**：

| 守卫 | 钉住什么 |
|---|---|
| `test_planner_models.py::test_agents_package_does_not_import_planner` | `agents/**` 不 import `planner.*`（否则成包级环：`planner.planner` 已 import `agents.storage`） |
| `test_knowledge_retrieval.py::test_planner_package_does_not_touch_data_sources_directly` | `planner/**` 不 import `agents`（`agents` 是数据源前缀之一，F3） |

于是壳只能落在**中立层**：框架包与业务包都不是它的家，`subagents/` 是。
这也是三条守卫反复用的处置方式——**让结构说话，而不是给守卫开后门**
（放宽任何一条都要写理由进审计，而这里没有任何正当需要）。

`TestManager` 仍在 `agents/manager.py` 且**不 import 本包**：manager 的
`spawn(agent_cls, ...)` 由调用方给类，组装发生在中立层——依赖方向保持
`subagents → agents` / `subagents → {planner,explorer,diagnosis}` 单向。
"""
