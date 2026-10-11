"""diagnosis — Failure Diagnosis（设计 8 节 / P3-D；M5）。

**F10 的第一道防线**：诊断产出的是 **Hypothesis（假设）**，不是 Fact。
本包不写 `failure_attribution`——那是人工 triage 的通道（P1
`TraceStore.triage_testcase`，8.2 明文「系统不得仅凭 ELEMENT_NOT_FOUND 判
APP_DEFECT」）。归因默认 `UNTRIAGED`，证据分过阈值才给 `suggested_attribution`。

模块图（随任务增量登记，不预建空壳——P2 Task 1.2「表结构随实现定型」的教训）：
- `models.py`    假设 / 证据 / Bug 草稿（值对象，纯）；
- `context.py`   `DiagnosisContext` 装配（**只经 `KnowledgeSources`**，F3）；
- `classifier.py` 规则优先的假设引擎（§8.2；崩溃/环境/漂移三条规则）；
- `analyzer.py`  规则空结果才调 LLM 的编排（§8.2 末段）；
- `evidence.py`  Evidence Score（§8.3）——排序由分数而非 LLM confidence 决定；
- `reproducer.py` 复现引擎（§8.4）——**复用 `executor/policy` 的阶段判定本体**
  （`FailurePhase` / `retry_decision`），不重新推断「动作是否已发出」（F9）。

去重随 Task 5.5 落地。

F3：本包**不直连数据源**（`sqlite3` / `tracer` / `graph.storage` /
`experience.store` / `source.sqlite_tx`）——检索一律经
`knowledge.build_knowledge` 的产物。守卫见
`tests/unit/test_knowledge_retrieval.py::test_diagnosis_package_does_not_touch_data_sources_directly`。
"""
