"""Task 1.2 / P3-02：policy.yaml 加载器（设计 §7.2/§7.4/§8.3/§10）。

- 文件缺失 → 内置默认值（= 设计初版值），P2 行为不变（矩阵 #4）；
- 未知键 / 错型 → fail-loud（配置拼错静默生效是最难查的一类回归）；
- 值即设计原文：exploration 四维预算（7.2）、evidence 权重（8.3 原值）、
  attribution_threshold=10。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.policy_config import PolicyConfig, load_policy


def test_missing_file_yields_design_defaults(tmp_path):
    policy = load_policy(tmp_path / "nope.yaml")
    assert policy.autonomous.enabled is True
    assert policy.exploration.max_steps == 100
    assert policy.exploration.max_duration_seconds == 600
    assert policy.exploration.max_llm_calls == 20
    assert policy.exploration.max_repeated_state == 3
    assert policy.escalation.guard_blocks_before_escalate == 3
    assert policy.evidence.attribution_threshold == 10


def test_shipped_policy_yaml_loads():
    """首次入库的 config/policy.yaml 必须能被加载器消化（同源校验）。"""
    policy = load_policy(Path("config/policy.yaml"))
    assert policy.autonomous.enabled is True
    assert policy.exploration.max_steps == 100


def test_evidence_weights_match_design_8_3():
    policy = load_policy(None)
    assert policy.diagnosis_evidence_weights == {
        "crash_detected": 5,
        "reproducible_3_times": 5,
        "source_change_correlated": 3,
        "same_issue_multiple_tests": 3,
        "backend_evidence": 2,
        "llm_hypothesis_only": 1,
    }


def test_unknown_key_fails_loud(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("exploration:\n  max_steps: 10\n  bogus_key: 1\n",
                 encoding="utf-8")
    with pytest.raises(Exception, match="bogus_key"):
        load_policy(p)


def test_wrong_type_fails_loud(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text('exploration:\n  max_steps: "abc"\n', encoding="utf-8")
    with pytest.raises(Exception):
        load_policy(p)


def test_disabled_autonomous_flag_parses(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("autonomous:\n  enabled: false\n", encoding="utf-8")
    assert load_policy(p).autonomous.enabled is False


def test_policy_config_defaults_direct():
    policy = PolicyConfig()
    assert policy.exploration.max_llm_calls == 20


# --- 入库的 yaml 与内置默认值必须同源（Task 1.2 补） --------------------------


def test_shipped_policy_yaml_equals_builtin_defaults():
    """`config/policy.yaml` 的值必须与内置默认值**逐字段相等**。

    否则「文件缺失 → 默认值」与「文件在 → 读到的值」会给出**两套配置**，而
    「P2 既有命令行为不变」的承诺（矩阵 #4）只在其中一套下成立。以后校准初版值
    时**两处一起改**——本测试会拦住只改一处。
    """
    from agents.policy_config import DEFAULT_POLICY_PATH

    assert DEFAULT_POLICY_PATH == Path("config/policy.yaml")
    assert load_policy(DEFAULT_POLICY_PATH) == PolicyConfig()


def test_partial_section_falls_back_to_defaults(tmp_path):
    """不做深合并：**段内**未写的字段与**未写的段**都回落默认值。"""
    p = tmp_path / "policy.yaml"
    p.write_text("exploration:\n  max_steps: 7\n", encoding="utf-8")
    policy = load_policy(p)
    assert policy.exploration.max_steps == 7
    assert policy.exploration.max_llm_calls == 20, "段内未写的用默认"
    assert policy.autonomous.enabled is True, "未写的段用默认"


def test_empty_file_yields_defaults(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("", encoding="utf-8")
    assert load_policy(p) == PolicyConfig()


def test_policy_config_is_immutable():
    """配置读进来就不该被改（frozen）：运行期改写 policy 会让「启动时校验」
    与「实际行为」脱钩。"""
    policy = PolicyConfig()
    with pytest.raises(Exception):
        policy.exploration.max_steps = 1


# --- 坏输入 fail-loud（「启动时校验」的边界） --------------------------------


def test_unknown_top_level_key_fails_loud(tmp_path):
    """顶层拼错（`explorations`）也必须报——否则整段配置静默失效。"""
    from agents.policy_config import PolicyConfigError

    p = tmp_path / "policy.yaml"
    p.write_text("explorations:\n  max_steps: 10\n", encoding="utf-8")
    with pytest.raises(PolicyConfigError, match="explorations"):
        load_policy(p)


def test_negative_budget_fails_loud(tmp_path):
    from agents.policy_config import PolicyConfigError

    p = tmp_path / "policy.yaml"
    p.write_text("exploration:\n  max_steps: -1\n", encoding="utf-8")
    with pytest.raises(PolicyConfigError, match="max_steps"):
        load_policy(p)


def test_weight_key_sets_are_validated(tmp_path):
    """权重表是**整表替换**：漏键会在 `score_evidence`/`priority_score` 里变成
    运行时 `KeyError`（离现场很远）→ 挡在加载期，漏/多都点名。"""
    from agents.policy_config import PolicyConfigError

    p = tmp_path / "policy.yaml"
    p.write_text("diagnosis_evidence_weights:\n  crash_detected: 5\n",
                 encoding="utf-8")
    with pytest.raises(PolicyConfigError, match="缺少"):
        load_policy(p)

    p.write_text("planner_weights:\n  impact: 1\n  history: 1\n  risk: 1\n"
                 "  bogus: 1\n", encoding="utf-8")
    with pytest.raises(PolicyConfigError, match="多出"):
        load_policy(p)


def test_non_mapping_top_level_fails_loud(tmp_path):
    from agents.policy_config import PolicyConfigError

    p = tmp_path / "policy.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(PolicyConfigError, match="顶层必须是对象"):
        load_policy(p)


def test_bad_yaml_fails_loud(tmp_path):
    from agents.policy_config import PolicyConfigError

    p = tmp_path / "policy.yaml"
    p.write_text("exploration: [unclosed\n", encoding="utf-8")
    with pytest.raises(PolicyConfigError, match="不是合法 YAML"):
        load_policy(p)


def test_error_message_carries_the_file_path(tmp_path):
    """报错要指名**哪个文件**坏了（否则只能靠调用栈猜）。"""
    from agents.policy_config import PolicyConfigError

    p = tmp_path / "policy.yaml"
    p.write_text("exploration:\n  bogus: 1\n", encoding="utf-8")
    with pytest.raises(PolicyConfigError) as e:
        load_policy(p)
    assert str(p) in str(e.value)
