"""Task 4.4 / P3-17：Loop Detection（设计 7.3 / F8）。

plan Steps 的判据：
1. `Home→Search→Home→Search→Home` fixture → 识别 loop；
2. `max_repeated_state` 生效（阈值不到 → 不算 loop）；
3. **`current_fingerprint=None`（页面不可解析）→ 不计入重复窗口 + 审计留痕**
   （open question #3 的默认口径：不计入；审计留痕由调用方负责——纯函数
   只回答 True/False）；
4. F13：纯函数、确定性；**窗口/阈值非法 → fail-loud**（配置错误不是「不算
   loop」的静默分支——静默会让人以为检测在跑，其实从没生效）。

指纹来自 P2 `screen_fingerprint`（F8 同一套哈希）；本模块**不做任何哈希**
——只做精确相等计数（禁相似度）。

接线（偏离登记：plan 4.4 只列了纯函数，矩阵 #12「max_repeated_state 触发
→ 停止」需要主循环消费它）——`Explorer` 加 `loop_window` /
`max_repeated_state` 构造参数（默认 None = 不启用；4.3 的 ExplorationBudget
统一接 policy 默认值，避免这里再造一份默认值）。
"""
from __future__ import annotations

import pytest

from explorer.agent import ExplorationOutcome
from explorer.loop_detector import is_loop

H = "h" * 16
S = "s" * 16


# --- 纯函数面 ------------------------------------------------------------------


def test_ping_pong_history_is_detected_as_loop():
    # Home→Search→Home→Search→Home：窗口内 H 第 3 次出现
    assert is_loop([H, S, H, S, H], H, window=6, max_repeat=3) is True


def test_threshold_not_reached_is_not_a_loop():
    assert is_loop([H, S, H, S, H], H, window=6, max_repeat=4) is False


def test_window_bounds_the_lookback():
    # 窗口只看最后 window 条：window=2 时 recent=[S, H]，H 只 1 次
    assert is_loop([H, S, H, S, H], H, window=2, max_repeat=2) is False
    # window=5 时 recent 含 3 个 H
    assert is_loop([H, S, H, S, H], H, window=5, max_repeat=3) is True


def test_none_fingerprint_is_never_a_loop():
    """页面不可解析（fingerprint=None）→ 不计入窗口，也不判 loop
    （open question #3 的默认口径）。"""
    assert is_loop([H, H, H], None, window=6, max_repeat=3) is False
    # 空串同样视为「不可解析」（save_candidate 对空指纹同款口径）
    assert is_loop([H, H, H], "", window=6, max_repeat=3) is False


def test_invalid_window_or_threshold_fails_loud():
    with pytest.raises(ValueError):
        is_loop([H], H, window=0, max_repeat=3)
    with pytest.raises(ValueError):
        is_loop([H], H, window=5, max_repeat=0)
    with pytest.raises(ValueError):
        is_loop([H], H, window=-1, max_repeat=3)


def test_empty_history_with_single_arrival_is_not_a_loop():
    assert is_loop([], H, window=6, max_repeat=3) is False


# --- 接线：主循环消费 is_loop（矩阵 #12） --------------------------------------


def _toggle_explorer(tmp_path, *, max_repeated_state):
    """FakeExecutor 每次 tap 在 Home↔Profile 间切换 → 指纹必然振荡。"""
    from agents.storage import SQLiteAgentStore
    from agents.tools import AgentToolkit
    from executor.guard import EnvKind, Guard
    from tests.fault_injection.fi_support import FakeExecutor, drift_repo

    home = ("<App><Node name='screen.HomeView' visible='true'/></App>")
    profile = ("<App><Node name='screen.ProfileView' visible='true'/></App>")

    class _ToggleExec(FakeExecutor):
        def __init__(self):
            super().__init__()
            self._page = home

        def tap(self, strategies):
            self.tap_calls += 1
            self._page = profile if self._page == home else home

        def page_source(self):
            return self._page

    repo = drift_repo(tmp_path)
    store = SQLiteAgentStore(tmp_path / "agent.db")
    store.create_task("task_loop", agent_type="explorer", goal="explore")
    ex = _ToggleExec()
    toolkit = AgentToolkit(
        guard=Guard(EnvKind.SANDBOX), executor=ex,
        locator_for=lambda t: [{"type": "accessibility_id", "value": t}],
        repository=repo, agent_db=store, task_id="task_loop")
    from explorer.agent import Explorer
    from explorer.policy import ProposedAction

    def scripted(observation, graph, visited):
        return ProposedAction(tool="tap", target="login_button")

    explorer = Explorer(toolkit=toolkit, graph=None, agent_db=store,
                        task_id="task_loop", decider=scripted,
                        repository=repo,
                        loop_window=6, max_repeated_state=max_repeated_state)
    return explorer, store, ex


def test_repeated_state_stops_the_exploration(tmp_path):
    """矩阵 #12：max_repeated_state 触发 → LOOP_DETECTED 正常停止。"""
    explorer, store, ex = _toggle_explorer(tmp_path, max_repeated_state=2)
    result = explorer.run()

    assert result.outcome is ExplorationOutcome.LOOP_DETECTED
    # 第 3 次到达 Home 时触发（H 第 2 次重复）→ 前两步已执行
    assert result.steps == 2
    assert ex.tap_calls == 2
    assert store.get_task("task_loop").outcome == "LOOP_DETECTED"
    # 停下的那一步要留痕（审计：为什么停）
    trace = store.get_trace("task_loop")
    stop = [e for e in trace if e.result == "LOOP_DETECTED"]
    assert stop, "触发 loop 的那一步必须留痕"


def test_loop_detection_disabled_when_not_configured(tmp_path):
    """未配置（None）→ 不做循环检测（4.3 的 budget 接入时统一供参）。"""
    from agents.storage import SQLiteAgentStore
    from agents.tools import AgentToolkit
    from executor.guard import EnvKind, Guard
    from tests.fault_injection.fi_support import FakeExecutor, drift_repo

    repo = drift_repo(tmp_path)
    store = SQLiteAgentStore(tmp_path / "agent.db")
    store.create_task("task_noloop", agent_type="explorer", goal="explore")
    toolkit = AgentToolkit(
        guard=Guard(EnvKind.SANDBOX), executor=FakeExecutor(),
        locator_for=lambda t: [{"type": "accessibility_id", "value": t}],
        repository=repo, agent_db=store, task_id="task_noloop")
    from explorer.agent import Explorer
    from explorer.policy import ProposedAction

    calls = {"n": 0}

    def scripted(observation, graph, visited):
        calls["n"] += 1
        return ProposedAction(tool="tap", target="login_button") \
            if calls["n"] <= 3 else None

    explorer = Explorer(toolkit=toolkit, graph=None, agent_db=store,
                        task_id="task_noloop", decider=scripted,
                        repository=repo)
    result = explorer.run()
    # 页面不变（FakeExecutor 固定 page_source）→ 若检测在跑，早该停
    assert result.outcome is ExplorationOutcome.COMPLETED
    assert result.steps == 3


def test_unresolvable_page_never_triggers_loop_stop(tmp_path):
    """fingerprint=None 的页面不进窗口 → 不会因为「看不懂」被误判 loop
    （open question #3 的循环侧口径）。"""
    from agents.storage import SQLiteAgentStore
    from agents.tools import AgentToolkit
    from executor.guard import EnvKind, Guard
    from tests.fault_injection.fi_support import FakeExecutor, drift_repo

    class _BlankExec(FakeExecutor):
        def page_source(self):
            return "<not-a-screen/>"     # 不可解析 → fingerprint=None

    repo = drift_repo(tmp_path)
    store = SQLiteAgentStore(tmp_path / "agent.db")
    store.create_task("task_blank", agent_type="explorer", goal="explore")
    toolkit = AgentToolkit(
        guard=Guard(EnvKind.SANDBOX), executor=_BlankExec(),
        locator_for=lambda t: [{"type": "accessibility_id", "value": t}],
        repository=repo, agent_db=store, task_id="task_blank")
    from explorer.agent import Explorer
    from explorer.policy import ProposedAction

    calls = {"n": 0}

    def scripted(observation, graph, visited):
        calls["n"] += 1
        return ProposedAction(tool="tap", target="login_button") \
            if calls["n"] <= 4 else None

    explorer = Explorer(toolkit=toolkit, graph=None, agent_db=store,
                        task_id="task_blank", decider=scripted,
                        repository=repo, loop_window=6, max_repeated_state=2)
    result = explorer.run()
    # 指纹恒 None：若 None 进了窗口，第 2 步就会被误判 loop
    assert result.outcome is ExplorationOutcome.COMPLETED
    assert result.steps == 4
