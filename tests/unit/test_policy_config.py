"""Task 1.2 / P3-02：policy.yaml 加载器（设计 §7.2/§7.4/§8.3/§10）。

- 文件缺失 → 内置默认值（= 设计初版值），P2 行为不变（矩阵 #4）；
- 未知键 / 错型 → fail-loud（配置拼错静默生效是最难查的一类回归）；
- 值即设计原文：exploration 四维预算（7.2）、evidence 权重（8.3 原值）、
  attribution_threshold=10。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.policy_config import (DEFAULT_POLICY_PATH, PolicyConfig,
                                  PolicyConfigError, load_policy)

# 仓库根（本文件在 tests/unit/ 下）——**别用 CWD 相对路径**：那样测试会跟着
# 调用目录走，而 `config/policy.yaml` 相对 CWD 解析（见 P3-2 的说明）。
_REPO = Path(__file__).resolve().parents[2]


def test_missing_file_yields_design_defaults(tmp_path, monkeypatch):
    """**默认路径**读不到 → 内置默认值（= 设计初版值），P2 行为不变（矩阵 #4）。

    ⚠️ review_p3_task12 P3-2 把「缺失」的语义拆成两件事，本测试的入参因此从
    「显式传一个不存在的路径」改为「默认路径不存在」：前者现在是 **fail-loud**
    （拼错路径不该静默退回默认值），后者才是矩阵 #4 的回归底线。两者不再共用
    一个出口——原断言编码的是旧语义。
    """
    import agents.policy_config as pc

    monkeypatch.setattr(pc, "DEFAULT_POLICY_PATH", tmp_path / "nope.yaml")
    policy = load_policy()
    assert policy.autonomous.enabled is True
    assert policy.exploration.max_steps == 100
    assert policy.exploration.max_duration_seconds == 600
    assert policy.exploration.max_llm_calls == 20
    assert policy.exploration.max_repeated_state == 3
    assert policy.escalation.guard_blocks_before_escalate == 3
    assert policy.evidence.attribution_threshold == 10
    assert load_policy(None) == policy, "`None` = 显式要求内置默认值，同义"


def test_shipped_policy_yaml_loads():
    """首次入库的 config/policy.yaml 必须能被加载器消化（同源校验）。"""
    path = _REPO / DEFAULT_POLICY_PATH
    assert path.is_file(), f"入库的 {DEFAULT_POLICY_PATH} 不在了：{path}"
    policy = load_policy(path)
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

    ⚠️ **先断言文件在**（review_p3_task12 P2-1）：本测试的断言目标
    （`== PolicyConfig()`）**正是** `load_policy` 在「路径不存在」时返回的东西
    ——不先断言 `is_file()`，文件被删/改名时它照样全绿（空转）。
    """
    path = _REPO / DEFAULT_POLICY_PATH
    assert DEFAULT_POLICY_PATH == Path("config/policy.yaml")
    assert path.is_file(), (
        f"入库的 {DEFAULT_POLICY_PATH} 不在了——本测试其余断言会因此**空转**"
        f"（load_policy 对缺失路径返回默认值，与期望值恰好相等）")
    assert load_policy(path) == PolicyConfig()


def test_default_resolution_actually_reads_the_default_path(tmp_path,
                                                           monkeypatch):
    """`load_policy()`（不传参）必须**真的去读**默认路径。

    探针手法（review_p3_task12 P2-1 同源）：把默认路径指到一个**值不同**的临时
    文件 → 读到的必须是文件里的值。只断言「== PolicyConfig()」是测不出这件事的
    ——文件在不在都相等，那正是空转的形态。
    """
    import agents.policy_config as pc

    alt = tmp_path / "policy.yaml"
    alt.write_text("exploration:\n  max_steps: 7\n", encoding="utf-8")
    monkeypatch.setattr(pc, "DEFAULT_POLICY_PATH", alt)
    assert load_policy().exploration.max_steps == 7


def test_missing_default_path_falls_back_to_defaults(tmp_path, monkeypatch):
    """默认路径不存在 → 内置默认值（矩阵 #4 的回归底线）。"""
    import agents.policy_config as pc

    monkeypatch.setattr(pc, "DEFAULT_POLICY_PATH", tmp_path / "nope.yaml")
    assert load_policy() == PolicyConfig()


def test_none_means_builtin_defaults_without_reading_any_file(tmp_path,
                                                              monkeypatch):
    """`None` = **显式要求内置默认值**，不读任何文件（与「没给」是两件事）。"""
    import agents.policy_config as pc

    alt = tmp_path / "policy.yaml"
    alt.write_text("exploration:\n  max_steps: 7\n", encoding="utf-8")
    monkeypatch.setattr(pc, "DEFAULT_POLICY_PATH", alt)
    assert load_policy(None) == PolicyConfig(), "7 不该被读到"


def test_explicit_missing_path_fails_loud(tmp_path):
    """**显式给了**路径却不存在 → fail-loud（拼错路径不该静默退回默认值）。

    这与「默认路径不存在 → 默认值」是**两件事**（review_p3_task12 P3-2）：
    后者是矩阵 #4 的回归底线，前者会让用户以为「我在用配置文件，其实没有」。
    """
    with pytest.raises(PolicyConfigError, match="不存在"):
        load_policy(tmp_path / "typo.yaml")


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
    p = tmp_path / "policy.yaml"
    p.write_text("explorations:\n  max_steps: 10\n", encoding="utf-8")
    with pytest.raises(PolicyConfigError, match="explorations"):
        load_policy(p)


def test_negative_budget_fails_loud(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("exploration:\n  max_steps: -1\n", encoding="utf-8")
    with pytest.raises(PolicyConfigError, match="max_steps"):
        load_policy(p)


def test_weight_key_sets_are_validated(tmp_path):
    """权重表是**整表替换**：漏键会在 `score_evidence`/`priority_score` 里变成
    运行时 `KeyError`（离现场很远）→ 挡在加载期，漏/多都点名。"""
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
    p = tmp_path / "policy.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(PolicyConfigError, match="顶层必须是对象"):
        load_policy(p)


def test_bad_yaml_fails_loud(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("exploration: [unclosed\n", encoding="utf-8")
    with pytest.raises(PolicyConfigError, match="不是合法 YAML"):
        load_policy(p)


def test_error_message_carries_the_file_path(tmp_path):
    """报错要指名**哪个文件**坏了（否则只能靠调用栈猜）。"""
    p = tmp_path / "policy.yaml"
    p.write_text("exploration:\n  bogus: 1\n", encoding="utf-8")
    with pytest.raises(PolicyConfigError) as e:
        load_policy(p)
    assert str(p) in str(e.value)


# --- review_p3_task12 P3-1：权重表的**值**也要过闸门 --------------------------


@pytest.mark.parametrize("bad", [-1, -99, -100])
def test_negative_weights_fail_loud(tmp_path, bad):
    """负权重必须 fail-loud（与标量段同款 `ge=0`）。

    消费者（`score_evidence` / `priority_score`）拿到负权重会算出**负分**，直接
    污染「归因是否达标」（F10）与「先做哪个」（§5.2）。此前 `field_validator`
    只校键集、不校值——而 docstring 与 yaml 头注释都声称「负值 fail-loud」。
    """
    for body in (f"planner_weights:\n  impact: {bad}\n  history: 2\n  risk: 5\n",
                 f"diagnosis_evidence_weights:\n  crash_detected: {bad}\n"
                 f"  reproducible_3_times: 5\n  source_change_correlated: 3\n"
                 f"  same_issue_multiple_tests: 3\n  backend_evidence: 2\n"
                 f"  llm_hypothesis_only: 1\n"):
        p = tmp_path / "policy.yaml"
        p.write_text(body, encoding="utf-8")
        with pytest.raises(PolicyConfigError):
            load_policy(p)


def test_scalar_negative_values_still_fail_loud(tmp_path):
    """对照：标量段的负值一直是被拒的（本次没动它们）。"""
    for body in ("exploration:\n  max_steps: -1\n",
                 "evidence:\n  attribution_threshold: -1\n",
                 "escalation:\n  guard_blocks_before_escalate: -1\n"):
        p = tmp_path / "policy.yaml"
        p.write_text(body, encoding="utf-8")
        with pytest.raises(PolicyConfigError):
            load_policy(p)


# --- review_p3_task12 P3-4：类型要**严格**，不靠 lax 强转 ---------------------


@pytest.mark.parametrize("body", [
    'exploration:\n  max_steps: "100"\n',        # 数字字符串
    "exploration:\n  max_steps: 3.0\n",          # 浮点整
    "exploration:\n  max_steps: true\n",         # 布尔当整数 → 曾静默变成 1 步
    'autonomous:\n  enabled: "true"\n',          # 字符串布尔
    "planner_weights:\n  impact: \"3\"\n  history: 2\n  risk: 5\n",
    "planner_weights:\n  impact: true\n  history: 2\n  risk: 5\n",
    "planner_weights:\n  impact: 3.0\n  history: 2\n  risk: 5\n",
])
def test_lax_coercions_are_rejected(tmp_path, body):
    """pydantic 默认 lax 会把这些**静默强转**；`strict=True` 后全部 fail-loud。

    最尖的一例是 `max_steps: true → 1`：F11 规定超预算即 `*_BUDGET_EXCEEDED`
    并停止，一个静默变成 **1 步**的预算会让探索在第一步就停——**配置错误表现为
    功能退化，而不是报错**。
    """
    p = tmp_path / "policy.yaml"
    p.write_text(body, encoding="utf-8")
    with pytest.raises(PolicyConfigError):
        load_policy(p)


def test_yaml_booleans_still_parse(tmp_path):
    """对照：YAML 的 `yes`/`no` 本来就是布尔，不该被 strict 误伤。"""
    p = tmp_path / "policy.yaml"
    p.write_text("autonomous:\n  enabled: yes\n", encoding="utf-8")
    assert load_policy(p).autonomous.enabled is True
