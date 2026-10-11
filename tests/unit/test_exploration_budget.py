"""Task 4.3 / P3-16：探索 Budget（设计 7.2 / F11 / F4）。

plan Steps 的判据：
1. 四类预算逐一超限 → 对应终态且 `agent_trace` 终态正确（steps / duration /
   llm 各自 `*_BUDGET_EXCEEDED`；**repeated_state 维由 loop detector 执行**，
   终态是矩阵 #12 的 `LOOP_DETECTED`——口径登记：四维之一不是第四种
   `*_BUDGET_EXCEEDED`，与矩阵 #11/#12 的分工一致）；
2. **LLM 预算底层就是 P1 `LLMBudget` 实例**（F4，类型断言——不新写计数器）；
3. 超限即停，**不自动放宽重试**（steps 停在恰好 max 处，不多跑一步）；
4. 默认值来自 `agents/policy_config.py` 的真值源 `EXPLORATION_BUDGET`；
   `--budget` 文件**同一结构覆盖**，校验复用 `ExplorationPolicy` 同一模型
   （未知键/错型/负值 fail-loud——不写第二份校验）。
"""
from __future__ import annotations

import pytest
import yaml

from agents.policy_config import EXPLORATION_BUDGET
from explorer.agent import ExplorationOutcome
from explorer.state import DEFAULT_LOOP_WINDOW, ExplorationBudget
from llm.budget import LLMBudget


# --- ExplorationBudget 本体 ----------------------------------------------------


def test_budget_defaults_come_from_the_policy_truth_source():
    b = ExplorationBudget.from_policy()
    for key, value in EXPLORATION_BUDGET.items():
        assert getattr(b, key) == value, f"{key} 必须与真值源一致（初版）"


def test_budget_file_override_uses_the_same_validation_model(tmp_path):
    f = tmp_path / "budget.yaml"
    f.write_text(yaml.safe_dump({
        "max_steps": 5, "max_duration_seconds": 60,
        "max_llm_calls": 2, "max_repeated_state": 2}))
    b = ExplorationBudget.from_file(f)
    assert (b.max_steps, b.max_llm_calls) == (5, 2)

    bad_keys = tmp_path / "bad_keys.yaml"
    bad_keys.write_text(yaml.safe_dump({**EXPLORATION_BUDGET, "oops": 1}))
    with pytest.raises(ValueError):
        ExplorationBudget.from_file(bad_keys)

    bad_type = tmp_path / "bad_type.yaml"
    bad_type.write_text(yaml.safe_dump(
        {**EXPLORATION_BUDGET, "max_steps": "100"}))   # 字符串不 lax 强转
    with pytest.raises(ValueError):
        ExplorationBudget.from_file(bad_type)

    negative = tmp_path / "negative.yaml"
    negative.write_text(yaml.safe_dump({**EXPLORATION_BUDGET,
                                        "max_steps": -1}))
    with pytest.raises(ValueError):
        ExplorationBudget.from_file(negative)


def test_budget_file_missing_fails_loud(tmp_path):
    with pytest.raises(ValueError):
        ExplorationBudget.from_file(tmp_path / "nope.yaml")


def test_loop_window_has_a_registered_initial_default():
    """design 7.3 的 `window` 不在四维预算里——初版常量在此单点定义
    （留档：真实运行后校准时改这一处）。"""
    assert DEFAULT_LOOP_WINDOW > 0


# --- 主循环的四维消费 ----------------------------------------------------------

from agents.storage import SQLiteAgentStore          # noqa: E402
from agents.tools import AgentToolkit                # noqa: E402
from executor.guard import EnvKind, Guard            # noqa: E402
from explorer.agent import Explorer                  # noqa: E402
from explorer.policy import ProposedAction           # noqa: E402
from tests.fault_injection.fi_support import (       # noqa: E402
    FakeExecutor, drift_repo)


def _explorer(tmp_path, task_id, *, budget=None, llm_budget=None,
              decider=None, max_repeated_state=None):
    repo = drift_repo(tmp_path)
    store = SQLiteAgentStore(tmp_path / "agent.db")
    store.create_task(task_id, agent_type="explorer", goal="explore")
    ex = FakeExecutor()
    toolkit = AgentToolkit(
        guard=Guard(EnvKind.SANDBOX), executor=ex,
        locator_for=lambda t: [{"type": "accessibility_id", "value": t}],
        repository=repo, agent_db=store, task_id=task_id)
    if decider is None:
        def decider(observation, graph, visited):
            return ProposedAction(tool="tap", target="login_button")
    explorer = Explorer(toolkit=toolkit, graph=None, agent_db=store,
                        task_id=task_id, decider=decider, repository=repo,
                        budget=budget, llm_budget=llm_budget,
                        max_repeated_state=max_repeated_state)
    return explorer, store, ex


def _budget(**overrides):
    values = {"max_steps": 100, "max_duration_seconds": 600,
              "max_llm_calls": 20, "max_repeated_state": 3}
    values.update(overrides)
    return ExplorationBudget(**values)


def test_steps_budget_exceeded_stops_exactly_at_max(tmp_path):
    explorer, store, ex = _explorer(
        tmp_path, "task_steps", budget=_budget(max_steps=2))
    result = explorer.run()

    assert result.outcome is ExplorationOutcome.STEPS_BUDGET_EXCEEDED
    assert result.steps == 2              # 停在恰好 max——不自动放宽重试
    assert ex.tap_calls == 2
    assert store.get_task("task_steps").outcome == "STEPS_BUDGET_EXCEEDED"
    trace = store.get_trace("task_steps")
    stop = [e for e in trace if e.result == "STEPS_BUDGET_EXCEEDED"]
    assert stop, "预算触发的停止要留痕（审计能回答「为什么停」）"


def test_duration_budget_exceeded_stops_before_first_action(tmp_path):
    explorer, store, ex = _explorer(
        tmp_path, "task_dur", budget=_budget(max_duration_seconds=0))
    result = explorer.run()

    assert result.outcome is ExplorationOutcome.DURATION_BUDGET_EXCEEDED
    assert result.steps == 0
    assert ex.tap_calls == 0
    assert store.get_task("task_dur").outcome == "DURATION_BUDGET_EXCEEDED"


def test_llm_budget_is_a_p1_llmbudget_instance(tmp_path):
    """F4：底层就是 P1 LLMBudget——注入的对象类型断言，不新写计数器。"""
    explorer, _, _ = _explorer(
        tmp_path, "task_llm", budget=_budget(max_llm_calls=7))
    assert isinstance(explorer.llm_budget, LLMBudget)
    assert explorer.llm_budget.max_calls_per_run == 7

    # 注入实例：类型必须对，注入错的对象构造上不可达
    with pytest.raises(TypeError):
        _explorer(tmp_path, "task_bad", llm_budget=object())


def test_llm_budget_exhausted_stops_exploration(tmp_path):
    shared = LLMBudget(max_calls_per_run=2)

    def decider(observation, graph, visited):
        # decider 每步「用一次 LLM」（drain 共享预算）
        shared.try_acquire()
        return ProposedAction(tool="tap", target="login_button")

    explorer, store, ex = _explorer(
        tmp_path, "task_drain", budget=_budget(max_llm_calls=2),
        llm_budget=shared, decider=decider)
    result = explorer.run()

    assert result.outcome is ExplorationOutcome.LLM_BUDGET_EXCEEDED
    assert result.steps == 2              # 前两步各用掉一次配额
    assert shared.calls_used == 2
    assert store.get_task("task_drain").outcome == "LLM_BUDGET_EXCEEDED"


def test_repeated_state_dimension_runs_through_loop_detector(tmp_path):
    """四维里的 repeated_state 由 loop detector 执行（矩阵 #12）——
    终态是 LOOP_DETECTED，不是第四种 *_BUDGET_EXCEEDED（口径登记）。"""
    from tests.unit.test_loop_detector import _toggle_explorer
    explorer, store, ex = _toggle_explorer(
        tmp_path, max_repeated_state=2)
    assert explorer.run().outcome is ExplorationOutcome.LOOP_DETECTED


def test_budget_with_generous_limits_does_not_short_circuit(tmp_path):
    """预算宽裕时行为与 4.1/4.2 完全一致（decider 收手 → COMPLETED）——
    预算层是护栏不是新语义。"""
    calls = {"n": 0}

    def decider(observation, graph, visited):
        calls["n"] += 1
        return ProposedAction(tool="tap", target="login_button") \
            if calls["n"] <= 3 else None

    # max_repeated_state 调高：本用例只验 steps/duration/llm 三维不误伤。
    # （这个 decider 反复点同一个不改变状态的按钮，在默认阈值 3 下**本就
    # 该被判 LOOP_DETECTED**——那是 F11 第四维在正常工作，不是预算层的新
    # 语义；见 test_repeated_state_dimension_runs_through_loop_detector。）
    explorer, store, ex = _explorer(
        tmp_path, "task_ok", budget=_budget(max_repeated_state=100),
        decider=decider)
    result = explorer.run()
    assert result.outcome is ExplorationOutcome.COMPLETED
    assert result.steps == 3
