"""Task 6.2 / P3-27：Subagent 绑定统一工具层（设计 §9.1/§10 / F12；矩阵 #18）。

判据：
1. **构造上不可达**（F12）：`Subagent` 没有 executor/driver 形参——直接注入
   执行原语在类型层面写不出来；toolkit 不是 `AgentToolkit` 拒绝；
2. **矩阵 #18 前半**：任意 subagent 的工具调用在 `agent_trace` 可追溯到**各自**
   task_id，底层 guard/executor 是**同一对象**（「三个 Subagent 落到同一个
   Executor + Guard」= 依赖同一，toolkit 实例各一——task_id 烤在 toolkit 里，
   共享一个 toolkit 会让三方的 trace 全记到同一个 task_id 下）；
3. **矩阵 #18 后半**：三 Subagent 各持独立 `LLMBudget`，一个耗尽不挤占其余（F4：
   预算必须就是 P1 `LLMBudget` 实例）；
4. `tests/unit/test_tool_allowlist_gate.py` 的既有断言仍全绿（F2 门禁复验——
   本文件的 import 即触发该门禁的包扫描）。
"""
from __future__ import annotations

import pytest

from agents.base import Subagent
from agents.models import AgentState
from agents.storage import SQLiteAgentStore
from agents.tools import AgentToolkit
from llm.budget import LLMBudget


class _FakeGuard:
    """最小 Guard 替身：记录 check 调用（同一性断言用身份不用行为）。"""

    def __init__(self):
        self.checked = []

    def check(self, ctx):
        self.checked.append(ctx)


class _FakeExecutor:
    """最小 Executor 替身：tap/get_current_screen 够用即可。"""

    def find(self, strategies):
        return None

    def tap(self, strategies):
        return {"tapped": True}

    def page_source(self):
        return "<App><Node name='screen.HomeView' visible='true'/></App>"


class _FakeRepo:
    """toolkit 的 tap 需要 repository 取 screen/risk 供 Guard 判定。

    `resolve` 返回 `EffectiveElement` 的最小形状（`hasattr(eff, "marker")`
    为 False → 走元素分支，`screen`/`id`/`risk` 三属性够 `_guard_context` 用）。
    """

    def resolve(self, ref, build="local"):
        from types import SimpleNamespace
        return SimpleNamespace(screen="HomeView", id="go_profile", risk=None)


def _locator_for(target):
    return [{"type": "accessibility_id", "value": str(target).split(".")[-1]}]


class _Recorder(Subagent):
    """测试用最小子类：基类是抽象協议的载体，不直接实例化。"""

    def do_tap(self):
        return self.call("tap", target="HomeView.go_profile")


@pytest.fixture()
def store(tmp_path):
    return SQLiteAgentStore(tmp_path / "agent.db")


def _kit(store, task_id, *, guard=None, executor=None):
    return AgentToolkit(
        guard=guard or _FakeGuard(), executor=executor or _FakeExecutor(),
        locator_for=_locator_for, repository=_FakeRepo(),
        agent_db=store, task_id=task_id)


# --- 1. 构造上不可达（F12） ------------------------------------------------------


def test_subagent_takes_no_executor_or_driver_parameter(store):
    """F12 的构造层落点：executor/driver **不是形参**——绕过 toolkit 的通道
    在类型层面写不出来（不是「传了会报错」，是「没地方传」）。"""
    import inspect

    params = set(inspect.signature(Subagent.__init__).parameters)
    assert "executor" not in params and "driver" not in params
    assert "device_session" not in params and "guard" not in params, \
        "guard 也只能经 toolkit 持有——Subagent 自己拿 Guard 就能拼出第二条通路"


def test_non_toolkit_is_refused(store):
    with pytest.raises(TypeError, match="AgentToolkit"):
        _Recorder(agent_db=store, task_id="t1", toolkit=object(),
                  llm_budget=LLMBudget(max_calls_per_run=1))


def test_non_llm_budget_is_refused(store):
    """F4：预算必须**就是** P1 `LLMBudget` 实例——散装计数器构造上不可达。"""
    kit = _kit(store, "t1")
    with pytest.raises(TypeError, match="LLMBudget"):
        _Recorder(agent_db=store, task_id="t1", toolkit=kit,
                  llm_budget={"max": 1})


def test_task_row_is_required(store):
    """没有 task 行的 subagent 不允许启动——每步审计的根（F1）。"""
    kit = _kit(store, "ghost")
    with pytest.raises(ValueError, match="no such agent task"):
        _Recorder(agent_db=store, task_id="ghost", toolkit=kit)


# --- 2. 矩阵 #18 前半：同一 Executor+Guard，trace 按任务归因 ---------------------


def test_three_subagents_share_guard_and_executor_but_own_their_traces(store):
    """「同一个 Executor + Guard」= 依赖对象同一；trace 按**各自** task_id 归因。

    共享一个 toolkit 实例会让三方的 agent_trace 全记到同一个 task_id 下——
    那是审计事故，不是「共享」。Manager 的装配契约在这里钉死。
    """
    guard, executor = _FakeGuard(), _FakeExecutor()
    ids = []
    kits = []
    for name in ("plan_1", "exp_1", "diag_1"):
        store.create_task(name, agent_type=name.split("_")[0], goal="g")
        ids.append(name)
        kits.append(_kit(store, name, guard=guard, executor=executor))

    agents = [_Recorder(agent_db=store, task_id=tid, toolkit=kit)
              for tid, kit in zip(ids, kits)]
    for a in agents:
        a.do_tap()

    # 依赖同一（F12 的「没有绕过路径」= 全都落在同一条 Guard 链上）
    for kit in kits:
        assert kit.guard is guard and kit.executor is executor
    assert guard.checked, "三次调用都经过了同一个 Guard"

    # trace 按各自 task_id 归因（三方互不混淆）
    from agents.tools import TOOL_METHODS
    for tid in ids:
        trace = store.get_trace(tid)
        assert trace, tid
        assert all(entry.task_id == tid for entry in trace)


# --- 3. 矩阵 #18 后半：预算独立互不挤占 -----------------------------------------


def test_three_subagent_budgets_are_independent(store):
    """一个 subagent 的 LLM 预算耗尽**不影响**另外两个（F4：任务级独立配额）。"""
    kits = [_kit(store, f"t{i}") for i in range(3)]
    for i in range(3):
        store.create_task(f"t{i}", agent_type="planner", goal="g")
    agents = [_Recorder(agent_db=store, task_id=f"t{i}", toolkit=kits[i],
                        llm_budget=LLMBudget(max_calls_per_run=1))
              for i in range(3)]

    # 第一个耗尽自己的配额
    assert agents[0].llm_budget.try_acquire() is True
    assert agents[0].llm_budget.try_acquire() is False
    # 其余两个毫发无损
    for a in agents[1:]:
        assert a.llm_budget.try_acquire() is True, \
            "预算是任务级的——一个耗尽不得挤占其余（矩阵 #18 后半）"
        assert a.llm_budget.try_acquire() is False


def test_subagent_without_budget_records_zero_llm_calls(store):
    """不配 LLM 预算也允许（纯规则路径）——但 `llm_budget` 必须如实是 None，
    不许造一个「看起来有」的假预算（F4 的反面试：假装有配额比没有更糟）。"""
    store.create_task("t1", agent_type="diagnosis", goal="g")
    a = _Recorder(agent_db=store, task_id="t1", toolkit=_kit(store, "t1"))
    assert a.llm_budget is None
