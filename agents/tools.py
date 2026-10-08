"""tools.py — Agent 工具层（**唯一分发层**；设计 §10/§10.1；Task 1.3 / P3-03）。

设计 §10 的四层不可绕过：`Policy → Executor → Environment → Trace`。本模块是
Agent 侧进入这条链的**唯一入口**：

```text
Agent（Planner/Explorer/Diagnosis/Subagent）
      │  AgentToolkit.call(tool, **kwargs)
      ▼
  查表（不在 ALLOWED_TOOLS → ToolViolation）
      ▼
  执行类 → GuardContext（effective_risk）→ Guard.check
      │            ├── BLOCK → 写 agent_trace（guard_result=BLOCK）→ 返回 ok=False
      │            └── ALLOW
      ▼
  既有原语（Executor / WaitEngine / AssertionEngine / Repository / promoter）
```

## 三张表（键集都是「单一真值源」的派生，不是手抄）

| 表 | 内容 | 来源 |
|---|---|---|
| `ALLOWED_TOOLS` | 工具全集 **22** | `frozenset(TOOL_ASSET_TIER)`（Task 1.1 的定档） |
| `GUARDED_TOOLS` | 设计 §10.1 中间组「执行（仍经 Guard）」10 个 | 设计原文分组 |
| `BANNED_TOOLS` | 设计 §10.1 末句的 8 个禁用名 | 设计原文（CI 门禁逐一断言不存在） |

`_TOOL_METHODS` 是**注册表**：键集必须 == `ALLOWED_TOOLS`（测试钉住）。注册表
里的工具若实现未接线，走 `NOT_WIRED` 明确报错——**不静默返回空结果**。

## 两条边界（都必须说清，否则会被读成「已经在生效」）

1. **`GUARDED_TOOLS` 的边界 = 设计 §10.1 的分组**。只读组（`get_*`）与候选创建组
   （`create_*`）**不经 Guard**：Guard 的三条规则都是关于**动作风险**
   （`effective_risk` + action），对纯读取没有判据可用。`get_current_screen`
   确实会调 WDA 读页面，但它的「风险」不是风险等级能表达的；环境侧的兜底是
   **F7 的启动期校验**（production 下自主命令整体不存在，见
   `cli.main._check_autonomous_env`）——两道闸不重叠、不互相替代。
2. **本任务不持有 `knowledge` / `budget`**（plan 的 Files 段把它们列进了
   `AgentToolkit` 的持有物）。理由：**本任务没有任何工具消费它们**
   （`get_relevant_*` / `get_graph_neighbors` 归 Task 2.2 的 KnowledgeSources
   装配；`budget` 是 LLM 调用的配额，而工具层是确定性的、不调 LLM）。
   按「文档声称的能力必须有落点」的纪律，**等有消费者时再加**——不留
   「已在生效」的假象（对照 `executor/guard.py::GuardContext.data_class` 的处置）。

## 返回值的分工（与 `StepRunner.run_step` 同款，便于 F1 一致性比对）

- **`ToolResult`**：工具调用的**结果**（`ok` / `failure_type` / `value`）。
  Guard BLOCK、元素找不到、断言不过、等待超时都是**结果**，不是异常——Agent
  的职责就是「观测并决定」，用异常表达预期结果会逼调用方写 except 流程。
- **异常**只留给**配置/编程错误**：`ToolViolation`（根本不是工具）、
  `ToolNotWired`（注册了但没实现）、`ToolDependencyError`（依赖没注入）、
  `ToolkitAuditError`（BLOCK 却无法留痕）。
- **`InfraError` 照原样上抛**（设备故障不是测试结果——与 `run_step` 同款）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from agents.models import TOOL_ASSET_TIER, AssetTier

__all__ = [
    "ALLOWED_TOOLS", "BANNED_TOOLS", "GUARDED_TOOLS", "NOT_WIRED",
    "TOOL_METHODS", "AgentToolkit", "ToolDependencyError",
    "ToolError", "ToolNotWired", "ToolResult", "ToolViolation",
    "ToolkitAuditError",
]

# --- 三张表 -------------------------------------------------------------------

# 工具全集 = 资产归属表的键集（**派生**，不是手抄——两处永不漂移）
ALLOWED_TOOLS: frozenset[str] = frozenset(TOOL_ASSET_TIER)

# 设计 §10.1 中间组「执行（仍经 Guard）」：会执行到 App 上的动作
GUARDED_TOOLS: frozenset[str] = frozenset({
    "tap", "input", "swipe", "back", "wait",
    "assert_exists", "assert_text", "screenshot",
    "run_testcase", "run_candidate_test",
})

# 设计 §10.1 末句：「以下工具**永不出现在任何 Agent 的工具表里**」。
# 单独列出来是为了让 CI 门禁能逐一断言（`tests/unit/test_tool_allowlist_gate.py`）。
BANNED_TOOLS: frozenset[str] = frozenset({
    "delete_testcase", "modify_expectation", "modify_verified_experience",
    "modify_repository_directly", "production_api_call", "real_payment",
    "arbitrary_shell", "arbitrary_python",
})

# 注册表：工具名 → 本类的方法名。**键集必须 == ALLOWED_TOOLS**（测试钉住）。
TOOL_METHODS: dict[str, str] = {
    # 只读（8）
    "get_current_screen": "_tool_get_current_screen",
    "get_ui_tree": "_tool_get_ui_tree",
    "get_relevant_source": "_tool_get_relevant_source",
    "get_relevant_experience": "_tool_get_relevant_experience",
    "get_graph_neighbors": "_tool_get_graph_neighbors",
    "get_logs": "_tool_get_logs",
    "get_network_summary": "_tool_get_network_summary",
    "get_crash_info": "_tool_get_crash_info",
    # 执行（10；仍经 Guard）
    "tap": "_tool_tap",
    "input": "_tool_input",
    "swipe": "_tool_swipe",
    "back": "_tool_back",
    "wait": "_tool_wait",
    "assert_exists": "_tool_assert_exists",
    "assert_text": "_tool_assert_text",
    "screenshot": "_tool_screenshot",
    "run_testcase": "_tool_run_testcase",
    "run_candidate_test": "_tool_run_candidate_test",
    # 候选资产（4）
    "create_test_candidate": "_tool_create_test_candidate",
    "create_bug_candidate": "_tool_create_bug_candidate",
    "create_discovery_event": "_tool_create_discovery_event",
    "create_promotion_proposal": "_tool_create_promotion_proposal",
}

# 已注册但**实现未接线**的工具 → 接线它的任务（`call` 明确报错，不静默）。
# 声明式而非散在方法体里：这样「哪些还没接线」是一张可读、可断言的表。
NOT_WIRED: dict[str, str] = {
    # 知识类：等 Task 2.2 的 KnowledgeSources 装配（F3：复用 P2 的四查询）
    "get_relevant_source": "Task 2.2（KnowledgeSources 装配）",
    "get_relevant_experience": "Task 2.2（KnowledgeSources 装配）",
    "get_graph_neighbors": "Task 2.2（KnowledgeSources 装配）",
    # 诊断数据源：等 M5 的 diagnosis 输入层（设计 §8.1）
    "get_logs": "M5（Failure Diagnosis 的输入层）",
    "get_network_summary": "M5（Failure Diagnosis 的输入层）",
    "get_crash_info": "M5（Failure Diagnosis 的输入层）",
    # Dry Run 入口：等 M3 的 test_candidates + Dry Run（设计 §6）
    "run_testcase": "M3（Dry Run 入口）",
    "run_candidate_test": "M3（Dry Run 入口）",
    "create_test_candidate": "M3（test_candidates 表，迁移 002）",
    "create_bug_candidate": "M5（bug_candidates 表，迁移 004）",
    "create_discovery_event": "M4（discovery_events 表，迁移 003）",
}


# --- 错误与结果 ---------------------------------------------------------------


class ToolError(Exception):
    """工具层的基类。**只用于配置/编程错误**，不用于「动作没做成」
    （那是 `ToolResult(ok=False)`）。"""


class ToolViolation(ToolError):
    """工具不在 `ALLOWED_TOOLS` 里（含设计 §10.1 的 8 个禁用名）。

    F2 的落点：被禁工具**在构造上不存在**——它们不在 `TOOL_ASSET_TIER`、
    不在注册表，调用它们拿到的就是这个异常（而不是「被拒绝的调用」）。
    """


class ToolNotWired(ToolError):
    """工具已注册但实现未接线（见 `NOT_WIRED` 的任务表）。"""


class ToolDependencyError(ToolError):
    """工具需要的依赖没注入（不静默降级成「空结果」）。"""


class ToolkitAuditError(ToolError):
    """Guard BLOCK 却无法留痕 → **拒绝静默丢弃**（F1 的审计根基）。

    `agent_db` / `task_id` 缺失时不许「算了不记了」——审计断档比拦错动作
    更糟：被拦的动作没有痕迹，事后无法回答「它想干什么」。
    """


@dataclass(frozen=True)
class ToolResult:
    """一次工具调用的结果。

    - `ok`：**工具调用本身**是否完成。断言不过 / 等待超时也算完成（结论在
      `value` 里）——见模块 docstring 的「返回值的分工」。
    - `guard`：`None` = 本工具不经 Guard（只读/候选类）；`"ALLOW"` / `"BLOCK"`
      = 执行类工具的 Guard 结论。
    - `failure_type`：与 P1 同名的失败分类（`ELEMENT_NOT_FOUND` /
      `SECURITY_BLOCKED` / `ASSERTION_VALUE_MISMATCH` / `WAIT_TIMEOUT` …），
      便于与 trace 的 `steps.failure_type` 对齐。
    """

    tool: str
    tier: AssetTier
    ok: bool = True
    guard: str | None = None
    value: Any = None
    failure_type: str | None = None
    error: str | None = None
    blocked_reason: str | None = None
    latency_ms: float = 0.0


# --- 工具层 -------------------------------------------------------------------


class AgentToolkit:
    """Agent 的工具分发层。**构造上不存在危险工具**（F2）。

    `guard` 必填——没有 Guard 的工具层等于没有闸门。其余依赖按工具需要注入；
    缺依赖时 `call` 抛 `ToolDependencyError`（不静默降级）。

    | 依赖 | 谁需要 |
    |---|---|
    | `guard` | 全部执行类（`GUARDED_TOOLS`） |
    | `executor` | `get_current_screen` / `get_ui_tree` / `tap` / `input` / `swipe` / `screenshot` |
    | `locator_for` | `tap` / `input`（定位解析归 Repository，注入而非自建） |
    | `repository` | `tap` / `input`（取 screen/element/risk 供 Guard 判定） |
    | `device_session` | `back`（P1 的分流：back 归 driver） |
    | `wait_engine` | `wait` |
    | `assertion_engine` | `assert_exists` / `assert_text` |
    | `experience_store` | `create_promotion_proposal` |
    | `agent_db` + `task_id` | **BLOCK 留痕**（缺失则 `ToolkitAuditError`） |
    """

    def __init__(self, *, guard, executor=None, locator_for=None,
                 repository=None, device_session=None, wait_engine=None,
                 assertion_engine=None, experience_store=None,
                 agent_db=None, task_id: str | None = None,
                 build: str = "local",
                 screenshot_dir: str | Path = "out/screenshots") -> None:
        self.guard = guard
        self.executor = executor
        self.locator_for: Callable[[Any], list] | None = locator_for
        self.repository = repository
        self.device_session = device_session
        self.wait_engine = wait_engine
        self.assertion_engine = assertion_engine
        self.experience_store = experience_store
        self.agent_db = agent_db
        self.task_id = task_id
        self.build = build
        self.screenshot_dir = Path(screenshot_dir)

    # --- 入口 ---------------------------------------------------------------

    def call(self, tool: str, **kwargs) -> ToolResult:
        """调用一个工具。流程见模块 docstring 的四层图。

        `kwargs` 里的 `target` 是目标引用（`"Screen.elem"` / `"elem"` /
        `"screen:X"`，与 `Repository.resolve` 同语法）。
        """
        if tool not in TOOL_METHODS:
            raise ToolViolation(
                f"{tool!r} 不在 Agent 工具表里（设计 10.1 的 {len(ALLOWED_TOOLS)} "
                f"个工具；被禁名 {sorted(BANNED_TOOLS)} **在构造上不存在**）")
        tier = TOOL_ASSET_TIER[tool]
        t0 = time.monotonic()

        # ① Guard（安全判定在最前：被拦的动作连实现都不该碰）
        guard_verdict: str | None = None
        if tool in GUARDED_TOOLS:
            ctx = self._guard_context(tool, kwargs)
            from executor.guard import GuardViolation
            try:
                self.guard.check(ctx)
            except GuardViolation as e:
                # BLOCK → 先留痕（写不进去就抛，绝不静默丢弃），再返回结果
                self._audit_block(tool, ctx, e.reason)
                return ToolResult(
                    tool=tool, tier=tier, ok=False, guard="BLOCK",
                    failure_type=e.failure_type, error=e.reason,
                    blocked_reason=e.reason, latency_ms=self._ms(t0))
            guard_verdict = "ALLOW"

        # ② 未接线（已过安全判定，如实报错而不是静默返回空结果）
        if tool in NOT_WIRED:
            raise ToolNotWired(
                f"{tool!r} 尚未接线（归 {NOT_WIRED[tool]}）——本工具在 "
                f"ALLOWED_TOOLS 里、也过了 Guard，但还没有实现。"
                f"**不静默返回空结果**：调用方会拿到这条明确报错。")

        # ③ 执行
        impl = getattr(self, TOOL_METHODS[tool])
        try:
            value = impl(**kwargs)
        except (ToolError, ValueError, TypeError):
            # 配置/编程错误照原样上抛（模块 docstring 的「返回值的分工」）：
            # 坏参数（`assert_text(condition="bogus")`、`WaitSpec` 校验不过）
            # 是调用方写错了，不该被翻成「工具执行失败」的结果——那会把
            # 编程错误伪装成运行期结论。
            raise
        except Exception as e:  # noqa: BLE001
            from session.device_session import InfraError
            if isinstance(e, InfraError):
                raise                     # 设备故障不是测试结果（与 run_step 同款）
            return ToolResult(
                tool=tool, tier=tier, ok=False, guard=guard_verdict,
                failure_type=self._classify(tool, e),
                error=f"{type(e).__name__}: {e}",
                latency_ms=self._ms(t0))
        return ToolResult(tool=tool, tier=tier, ok=True, guard=guard_verdict,
                          value=value, latency_ms=self._ms(t0))

    @staticmethod
    def _ms(t0: float) -> float:
        return round((time.monotonic() - t0) * 1000, 3)

    @staticmethod
    def _classify(tool: str, e: Exception) -> str:
        """异常 → 与 P1 同名的 failure_type（`steps.failure_type` 同域）。"""
        from executor.executor import AmbiguousElement, ElementNotFound
        if isinstance(e, ElementNotFound):
            return "ELEMENT_NOT_FOUND"
        if isinstance(e, AmbiguousElement):
            return "AMBIGUOUS_ELEMENT"
        return f"TOOL_ERROR_{type(e).__name__.upper()}"

    # --- Guard 输入 ---------------------------------------------------------

    def _guard_context(self, tool: str, kwargs: dict):
        """构造 `GuardContext`。

        `risk` 的推导与 `cli/pipeline.py` 的 `_prepare` **同式**
        （`effective_risk(element=eff.risk, element_id=eff.id)`）——不另立
        一套风险推导（F1：风险判定复用 P1 的 `effective_risk`）。
        无 target 的动作（`swipe` / `back`）没有 screen/element：`screen_id` /
        `element_id` 为空串，风险取 `effective_risk()` 的兜底 LOW；
        此时 `blocked_targets` 只能按 `kind=action` 命中（screen/element 模式
        没有主语可匹配——这是事实，不是缺口）。
        """
        from executor.guard import GuardContext
        from executor.policy import effective_risk

        target = kwargs.get("target")
        if target is None:
            return GuardContext(risk=effective_risk(), screen_id="",
                                element_id="", action=tool)
        eff = self._resolve_target(target)
        # `Repository.resolve` 有两种产物：`EffectiveElement`（有 screen/risk）
        # 与 `EffectiveScreen`（`screen:X`，只有 id/marker/kind_hint）。
        # 用 `marker` 判别（与 `cli/pipeline._locator_for` 同一判别式）——
        # 直接摸 `eff.screen` 会在 screen 目标上 AttributeError（`wait
        # screen:X` 就是这条路径）。
        if hasattr(eff, "marker"):           # EffectiveScreen
            screen_id, element_id = eff.id, ""
        else:                                 # EffectiveElement
            screen_id, element_id = eff.screen, eff.id
        return GuardContext(
            risk=effective_risk(element=getattr(eff, "risk", None),
                                element_id=element_id or None),
            screen_id=screen_id, element_id=element_id, action=tool)

    def _resolve_target(self, target):
        """`Repository.resolve` 的薄封装（Guard 输入与元素定位共用）。"""
        self._need(repository=self.repository)
        return self.repository.resolve(target, build=self.build)

    def _need(self, **deps) -> None:
        missing = sorted(name for name, value in deps.items() if value is None)
        if missing:
            raise ToolDependencyError(
                f"工具需要的依赖没注入：{missing}——构造 AgentToolkit 时补上。"
                f"不静默降级（缺依赖时报错，不是返回空结果）")

    # --- BLOCK 留痕（F1 的审计根基） ------------------------------------------

    def _audit_block(self, tool: str, ctx, reason: str) -> None:
        """把 BLOCK 写进 `agent_trace`（`guard_result=BLOCK`）。

        **不记 kwargs 的值**：`input` 的值可能是密码/验证码（14.4 的
        SENSITIVE/SECRET 遮蔽）——审计要能回答「它想干什么」，不需要把秘密
        抄进库里。记 tool / action / screen / element / risk / 拦截原因。
        """
        if self.agent_db is None or self.task_id is None:
            raise ToolkitAuditError(
                f"Guard 拦下 {tool!r}（{reason}），但 agent_db/task_id 未注入 → "
                f"**无法留痕**。拒绝静默丢弃：被拦的动作没有痕迹，事后无法"
                f"回答「它想干什么」（F1 的审计根基）。")
        self.agent_db.append_trace(
            self.task_id,
            observation={"tool": tool, "action": ctx.action,
                         "screen_id": ctx.screen_id,
                         "element_id": ctx.element_id},
            decision={"guard_result": "BLOCK", "reason": reason,
                      "risk": ctx.risk.name, "tier":
                      TOOL_ASSET_TIER[tool].value},
            guard_result="BLOCK", result="BLOCKED")

    # ======================================================================
    # 只读（8）—— 不经 Guard
    # ======================================================================

    def _tool_get_current_screen(self, **kw) -> dict:
        """当前屏判定（13.2 纯函数）+ 屏指纹（F8 的同一套哈希工具）。"""
        self._need(executor=self.executor, repository=self.repository)
        from source.screen import current_screen, screen_fingerprint
        page = self.executor.page_source()
        res = current_screen(page, self.repository)
        return {"status": res.status, "screen": res.screen,
                "visible_markers": list(res.visible_markers),
                "fingerprint": screen_fingerprint(page)}

    def _tool_get_ui_tree(self, **kw) -> str:
        """UI 树（= `page_source`）。"""
        self._need(executor=self.executor)
        return self.executor.page_source()

    def _tool_get_relevant_source(self, **kw):
        raise ToolNotWired(NOT_WIRED["get_relevant_source"])

    def _tool_get_relevant_experience(self, **kw):
        raise ToolNotWired(NOT_WIRED["get_relevant_experience"])

    def _tool_get_graph_neighbors(self, **kw):
        raise ToolNotWired(NOT_WIRED["get_graph_neighbors"])

    def _tool_get_logs(self, **kw):
        raise ToolNotWired(NOT_WIRED["get_logs"])

    def _tool_get_network_summary(self, **kw):
        raise ToolNotWired(NOT_WIRED["get_network_summary"])

    def _tool_get_crash_info(self, **kw):
        raise ToolNotWired(NOT_WIRED["get_crash_info"])

    # ======================================================================
    # 执行（10）—— 经 Guard（GUARDED_TOOLS）
    # ======================================================================

    def _tool_tap(self, *, target, **kw) -> dict:
        self._need(executor=self.executor, locator_for=self.locator_for)
        self.executor.tap(self.locator_for(target))
        return {"target": target, "action": "tap"}

    def _tool_input(self, *, target, value: str = "", **kw) -> dict:
        """输入文本。**返回值不回显 value**（可能是密码/验证码，14.4）。"""
        self._need(executor=self.executor, locator_for=self.locator_for)
        self.executor.input(self.locator_for(target), value)
        return {"target": target, "action": "input", "value_len": len(value)}

    def _tool_swipe(self, *, direction: str = "up", **kw) -> dict:
        self._need(executor=self.executor)
        self.executor.swipe(direction)
        return {"direction": direction}

    def _tool_back(self, **kw) -> dict:
        """返回上一屏。P1 的分流：`back` 归 device session 的 driver
        （不是 Executor 的动作，见 `cli/pipeline._APP_LEVEL_ACTIONS`）。

        ⚠️ **与 pipeline 的 `_device_session_or_stub` 不同：这里不回落 no-op
        桩**。pipeline 那么做是为了让 `mta run --fake-driver` 端到端跑通
        （测试替身的 `ensure_alive()` 返回 None）；工具层是 Agent 侧的**真实**
        通路，拿不到 driver 就如实报错（`ok=False`）——静默 no-op 会让 Agent
        以为「按过返回键了」。
        """
        self._need(device_session=self.device_session)
        self.device_session.ensure_alive().back()
        return {"action": "back"}

    def _tool_wait(self, *, target, condition: str = "exists",
                   expected=None, timeout: float | None = None,
                   **kw) -> dict:
        """等待条件成立。**超时是结果不是异常**（Agent 要能把「没等到」当成
        观测）；`failure_type=WAIT_TIMEOUT` 与 P1 的等待超时同域。"""
        self._need(wait_engine=self.wait_engine)
        from executor.wait import WaitTimeout
        from testcase.schema import WaitSpec
        spec = WaitSpec(target=target, condition=condition, expected=expected,
                        **({} if timeout is None else {"timeout": timeout}))
        try:
            self.wait_engine.wait_for(spec)
        except WaitTimeout as e:
            return {"target": target, "condition": condition, "satisfied": False,
                    "failure_type": "WAIT_TIMEOUT", "error": str(e)}
        return {"target": target, "condition": condition, "satisfied": True}

    def _tool_assert_exists(self, *, target, **kw) -> dict:
        return self._assert(target=target, condition="exists")

    def _tool_assert_text(self, *, target, expected, condition: str = "equals",
                          timeout: float = 10, **kw) -> dict:
        """文本断言。`condition="equals"|"contains"` → 6.2 的
        `text_equals` / `text_contains`（**不把 6.2 的字面量暴露给 Agent**：
        工具名与用例条件名是两套词汇，这里做一次显式映射）。"""
        mapping = {"equals": "text_equals", "contains": "text_contains"}
        if condition not in mapping:
            raise ValueError(
                f"assert_text 的 condition 只接受 {sorted(mapping)}，"
                f"got {condition!r}")
        return self._assert(target=target, condition=mapping[condition],
                            expected=expected, timeout=timeout)

    def _assert(self, *, target, condition: str, expected=None,
                timeout: float = 10) -> dict:
        """断言三态 → 结果（`passed` / `failure_type`）。

        `AssertionEngine.check` 的契约是「passed → 返回结果，否则抛异常」；
        工具层把异常**翻成结果**并保留 P1 的分类（H6 值不符 vs 7.3 目标漂移）
        ——两者对 Recovery 的含义不同（漂移可恢复，值不符直接 FAIL），
        不能在这里被压成一个布尔。
        """
        self._need(assertion_engine=self.assertion_engine)
        from executor.assertion import (AssertionTargetDrift,
                                        AssertionValueMismatch)
        from testcase.schema import AssertionSpec
        spec = AssertionSpec(target=target, condition=condition,
                             expected=expected, timeout=timeout)
        try:
            res = self.assertion_engine.check(spec)
        except (AssertionValueMismatch, AssertionTargetDrift) as e:
            r = e.result
            return {"passed": False, "condition": r.condition,
                    "target": r.target, "expected": r.expected,
                    "actual": r.actual,
                    "failure_type": str(e).split(":", 1)[0], "error": str(e)}
        return {"passed": True, "condition": res.condition,
                "target": res.target, "actual": res.actual}

    def _tool_screenshot(self, *, path: str | None = None, **kw) -> dict:
        self._need(executor=self.executor)
        if path is None:
            self.screenshot_dir.mkdir(parents=True, exist_ok=True)
            path = str(self.screenshot_dir / f"agent_{int(time.time() * 1000)}.png")
        self.executor.screenshot(path)
        return {"path": str(path)}

    def _tool_run_testcase(self, **kw):
        raise ToolNotWired(NOT_WIRED["run_testcase"])

    def _tool_run_candidate_test(self, **kw):
        raise ToolNotWired(NOT_WIRED["run_candidate_test"])

    # ======================================================================
    # 候选资产（4）—— 不经 Guard（不碰 App）
    # ======================================================================

    def _tool_create_test_candidate(self, **kw):
        raise ToolNotWired(NOT_WIRED["create_test_candidate"])

    def _tool_create_bug_candidate(self, **kw):
        raise ToolNotWired(NOT_WIRED["create_bug_candidate"])

    def _tool_create_discovery_event(self, **kw):
        raise ToolNotWired(NOT_WIRED["create_discovery_event"])

    def _tool_create_promotion_proposal(self, *, experience_id: str,
                                        reason: str | None = None,
                                        manual_override: bool = False,
                                        reviewer: str | None = None,
                                        **kw) -> dict:
        """转调 `experience/promoter.py`（F2：**唯一**能「建议」修改 Repository
        的方式；approve 仍归人工）。

        只落 proposal 表、**不写文件**（9.3 两段式的第一段）——所以它不经
        Guard（不碰 App），但它的产出必须由人工 approve 才生效。
        """
        self._need(experience_store=self.experience_store)
        from experience.promoter import generate_proposal
        exp = self.experience_store.get_experience(experience_id)
        if exp is None:
            return {"ok": False, "failure_type": "EXPERIENCE_NOT_FOUND",
                    "experience_id": experience_id}
        try:
            proposal = generate_proposal(
                self.experience_store, exp,
                manual_override=manual_override, reason=reason,
                reviewer=reviewer)
        except ValueError as e:
            # 9.5 / E5 的红线（非 VERIFIED 需显式 override、REJECTED 终态…）
            # → 如实回报，不让它变成异常流程
            return {"ok": False, "failure_type": "PROMOTION_REJECTED",
                    "experience_id": experience_id, "error": str(e)}
        return {"ok": True, "proposal_id": proposal.proposal_id,
                "experience_id": experience_id}
