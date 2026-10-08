"""P3 故障注入矩阵 #1/#4（M1；Task 1.2）。

| # | 场景 | 预期 |
|---|---|---|
| 1 | `env.kind=production` 下自主命令启动即拒绝（exit≠0，**无豁免 flag**，区别于 `mta run --allow-production`）；`autonomous.enabled=false` 同样拒绝 | F7 |
| 4 | policy.yaml 缺失 → 内置默认值；现有八组 CLI 不受影响 | 回归底线 |

自主命令（plan/generate/explore/diagnose/agent）在 M2+ 逐个接线；
公共前置 `_check_autonomous_env` 本任务落地——CLI 级拒绝在第一个自主
命令（Task 2.4 `mta plan`）落地时由本函数统一触发（同一代码路径，
不另写第二套环境校验——F7 的「启动时校验」只有一处实现）。
"""
from __future__ import annotations

import pytest

from agents.policy_config import PolicyConfig, load_policy
from cli.main import _check_autonomous_env


def test_matrix_1_production_rejects_no_exemption():
    policy = PolicyConfig()
    with pytest.raises(SystemExit) as e:
        _check_autonomous_env(policy, env_kind="production",
                              command="plan")
    assert e.value.code != 0
    assert "F7" in str(e.value)


@pytest.mark.parametrize("cmd", ["plan", "generate", "explore", "diagnose",
                                 "agent"])
def test_matrix_1_all_autonomous_commands_blocked_in_production(cmd):
    with pytest.raises(SystemExit):
        _check_autonomous_env(PolicyConfig(), env_kind="production",
                              command=cmd)


def test_matrix_1_disabled_flag_blocks_even_sandbox(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("autonomous:\n  enabled: false\n", encoding="utf-8")
    policy = load_policy(p)
    with pytest.raises(SystemExit) as e:
        _check_autonomous_env(policy, env_kind="sandbox", command="plan")
    assert e.value.code != 0


def test_matrix_1_sandbox_and_staging_pass():
    assert _check_autonomous_env(PolicyConfig(), env_kind="sandbox",
                                 command="plan") is None
    assert _check_autonomous_env(PolicyConfig(), env_kind="staging",
                                 command="plan") is None


def test_matrix_4_missing_policy_yaml_defaults(tmp_path):
    policy = load_policy(tmp_path / "nope.yaml")
    assert policy.exploration.max_steps == 100
    assert policy.autonomous.enabled is True


def test_matrix_4_existing_cli_groups_untouched():
    """P2 的八组子命令仍在、行为入口不变——policy 是 P3 新增层，不注入
    既有命令（回归底线）。"""
    from cli.main import build_parser
    parser = build_parser()
    args = parser.parse_args(["experience", "list", "--exp-db", "/tmp/x.db"])
    assert args.command == "experience"
    args2 = parser.parse_args(["lint", "suites/smoke/login_001.yaml"])
    assert args2.command == "lint"


# --- 前置的两条结构性钉子（Task 1.2 补） --------------------------------------


def test_autonomous_commands_is_the_single_list():
    """自主命令名单**单点**（`cli.main.AUTONOMOUS_COMMANDS`）。

    Task 2.4 起逐个接线时，解析器注册与前置校验都从这里取——两处各写一份名单
    必然漂移（本文件上面的参数化列表也钉住这五个名字）。
    """
    from cli.main import AUTONOMOUS_COMMANDS

    assert set(AUTONOMOUS_COMMANDS) == {"plan", "generate", "explore",
                                        "diagnose", "agent"}


def test_gate_has_no_exemption_parameter():
    """「无豁免 flag」的**机械**钉子：签名里不许出现豁免参数。

    设计 §7.4 说 production 下自主能力**整体不可用**。一旦有人给它加个
    `allow_production=True`，F7 就退化成 `mta run --allow-production` 那条
    规则了——而两者的语义本就不同（run 是「单次运行总闸」，只跑已审核用例）。
    """
    import inspect

    from cli.main import _check_autonomous_env

    params = list(inspect.signature(_check_autonomous_env).parameters)
    assert not any(("allow" in p) or ("force" in p) or ("exempt" in p)
                   for p in params), f"自主命令不该有豁免参数：{params}"


def test_gate_accepts_env_kind_enum():
    """`env_kind` 复用 P1 的 `EnvKind`（字符串与枚举都收，不新建环境枚举）。"""
    from executor.guard import EnvKind

    assert _check_autonomous_env(PolicyConfig(), env_kind=EnvKind.SANDBOX,
                                 command="plan") is None
    with pytest.raises(SystemExit):
        _check_autonomous_env(PolicyConfig(), env_kind=EnvKind.PRODUCTION,
                              command="plan")


def test_matrix_1_disabled_flag_blocks_staging_too(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("autonomous:\n  enabled: false\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        _check_autonomous_env(load_policy(p), env_kind="staging",
                              command="explore")


def test_production_message_explains_the_no_exemption_rule():
    """production 的拒绝消息要说明「与 `mta run --allow-production` 不同」，
    否则读者会去找一个**不存在**的豁免 flag。"""
    with pytest.raises(SystemExit) as e:
        _check_autonomous_env(PolicyConfig(), env_kind="production",
                              command="plan")
    msg = str(e.value)
    assert "allow-production" in msg, "要点名那个**不适用**的 flag"
    assert "自主命令" in msg


def test_every_wired_autonomous_command_passes_the_gate():
    """任何**已接线**的自主命令都必须调 `_check_autonomous_env`。

    ⚠️ 本任务只落地公共前置，命令本身在 M2+ 逐个接线（plan 第 12 节）——所以
    这条守卫**现在空转通过**（`cmd_plan` 等还不存在），Task 2.4 加 `cmd_plan`
    的那天它开始工作。防的是「接线时忘了过前置」，即
    review_p2_task55 P3-4 的教训：**判据函数要接进 main 链，别写个没人调的
    函数就当已覆盖**。
    """
    import ast
    from pathlib import Path

    from cli.main import AUTONOMOUS_COMMANDS

    src = Path("cli/main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    cmd_names = [n.name for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name.startswith("cmd_")]
    assert "cmd_run" in cmd_names, "扫描范围疑似写错（cli/main.py 里应有 cmd_run）"

    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        name = node.name.removeprefix("cmd_")
        if node.name.startswith("cmd_") and name in AUTONOMOUS_COMMANDS:
            body = ast.get_source_segment(src, node) or ""
            assert "_check_autonomous_env" in body, (
                f"`mta {name}` 是自主命令，但 `{node.name}` 没调 "
                f"_check_autonomous_env（F7 前置）")
