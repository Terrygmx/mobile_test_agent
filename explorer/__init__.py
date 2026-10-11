"""explorer — Bounded Exploration（设计 7 节 / P3-C；M4）。

**sandbox-only 无例外**（F7 / plan 执行注意事项 #3）：探索的环境约束是
**启动期**事实，落点在 CLI（Task 4.6 的 `mta explore` 前置）——本包内任何
模块都不自带环境判断，也不给「非 sandbox 也能跑」的开关。

模块图：
- `agent.py`     主循环（observe → decide → guard → execute → verify）；
- `policy.py`    候选动作构造 + 风险预判（F1 本体复用）；
- `frontier.py`  frontier 选择（Task 4.2）；
- `state.py`     探索预算（Task 4.3）；
- `loop_detector.py` 循环检测（Task 4.4）。

F12：一切设备动作经构造注入的 `AgentToolkit`——本包内无第二条执行通路
（守卫：`tests/unit/test_explorer_guard.py` 的 AST 级断言）。
"""
