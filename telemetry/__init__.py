"""telemetry — 设备侧证据采集（设计 §8.1 的 App/Crash Logs）。

**为什么单独一个包**：这一层是「从设备/宿主取原始材料」，与
`diagnosis/`（拿材料下结论）职责不同。分开的硬理由有两条：

1. `diagnosis/**` 有一条 **import 级守卫**（`tests/unit/test_diagnosis_models.py`
   的 `test_diagnosis_package_does_not_touch_data_sources_directly`，F3）——
   采集器要 `subprocess` 起 `xcrun`、要读宿主的崩溃报告目录，放进
   `diagnosis/` 会逼着给守卫开一个「除了 subprocess 和 pathlib」的后门。
   **让结构说话**（与 M6 把薄封装落到 `subagents/` 同一条纪律）。
2. 采集器**必须能离设备单测**（F13）：它们吃「一个 `list[str] -> str` 的
   可调用对象」，真实现只在 `default_runner` 一处。放进 `diagnosis/` 会让人
   以为「诊断逻辑要连设备才能测」。
"""
