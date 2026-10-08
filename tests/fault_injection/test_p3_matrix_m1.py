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


def test_matrix_4_missing_policy_yaml_defaults(tmp_path, monkeypatch):
    """矩阵 #4：**默认路径**的 policy.yaml 缺失 → 内置默认值，P2 行为不变。

    ⚠️ review_p3_task12 P3-2 拆开了「默认路径缺失」（→ 默认值，本矩阵的回归
    底线）与「显式给的路径不存在」（→ fail-loud）。本测试的入参因此从「显式传
    一个不存在的路径」改为「把默认路径指到不存在的位置」——原断言编码的是旧语义。
    """
    import agents.policy_config as pc

    monkeypatch.setattr(pc, "DEFAULT_POLICY_PATH", tmp_path / "nope.yaml")
    policy = load_policy()
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


@pytest.mark.parametrize("bad", ["PRODUCTION", "Prod", "prod", "", None,
                                 "sandbox "])
def test_gate_rejects_unknown_env_kind(bad):
    """坏 `env_kind` 也必须走 `SystemExit`（安全闸门**只许一个出口**）。

    `EnvKind(bad)` 抛的是 `ValueError`——那是**第三个出口**：CLI 上会打一整段
    traceback，而且**任何用 `except Exception` 兜底的调用方都会把否决一起吞掉**
    （review_p3_task12 P3-4）。注意 `None` 也拒：拿不到合法环境时**不假设
    sandbox**（那正是 F7 退化成永真的形态）。
    """
    with pytest.raises(SystemExit) as e:
        _check_autonomous_env(PolicyConfig(), env_kind=bad, command="plan")
    assert "F7" in str(e.value)


def test_gate_unknown_env_message_lists_the_valid_kinds():
    """坏值的拒绝消息要**列出合法值**（否则用户不知道往哪改）。"""
    with pytest.raises(SystemExit) as e:
        _check_autonomous_env(PolicyConfig(), env_kind="prod", command="plan")
    msg = str(e.value)
    for kind in ("sandbox", "staging", "production"):
        assert kind in msg


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
    """任何**已接线**的自主命令都必须过前置，且 `env_kind` 不许硬编码。

    ⚠️ 本任务只落地公共前置，命令本身在 M2+ 逐个接线（plan 第 12 节）——所以
    这条守卫**现在空转通过**（`cmd_plan` 等还不存在），Task 2.4 加 `cmd_plan`
    的那天它开始工作。防的是两件事：

    1. **接线时忘了过前置**（review_p2_task55 P3-4 的教训：判据函数要接进
       main 链，别写个没人调的函数就当已覆盖）；
    2. **`env_kind` 硬编码**（review_p3_task12 P3-3）：若顺手写
       `env_kind="sandbox"`（因为当时没有别的来源），F7 会退化成**永真**——
       判据还在、测试还绿、但**永远不拒**。合法写法是
       `env_kind=_resolve_env_kind(args)`（与 `mta run` 同款的唯一解析点）。
       另外 `command=` 的字面量必须与函数名一致（否则拒绝消息会指名错的命令）。
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
        if not (node.name.startswith("cmd_") and name in AUTONOMOUS_COMMANDS):
            continue
        body = ast.get_source_segment(src, node) or ""
        assert "_check_autonomous_env" in body, (
            f"`mta {name}` 是自主命令，但 `{node.name}` 没调 "
            f"_check_autonomous_env（F7 前置）")
        for call in _gate_calls(node):
            for kw in call.keywords:
                if kw.arg == "env_kind":
                    assert not isinstance(kw.value, ast.Constant), (
                        f"`{node.name}` 把 env_kind 写成了字面量——那会让 F7 "
                        f"退化成永真（应写 `_resolve_env_kind(args)`）")
                if kw.arg == "command" and isinstance(kw.value, ast.Constant):
                    assert kw.value.value == name, (
                        f"`{node.name}` 的 command={kw.value.value!r} 与函数名"
                        f" {name!r} 不一致（拒绝消息会指名错的命令）")


def _gate_calls(fn_node):
    """该函数体里所有 `_check_autonomous_env(...)` 调用节点。"""
    import ast

    out = []
    for call in ast.walk(fn_node):
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "_check_autonomous_env"):
            out.append(call)
    return out


def test_env_kind_resolution_is_shared_with_run():
    """`--env-kind` 的解析**单点**（`run` 与自主命令共用，review_p3_task12 P3-3）。

    两处各写一份 `EnvKind(getattr(args, "env_kind", None) or "sandbox")` 必然
    漂移——而 `run` 那一份现在有真实调用者，正是「先有落点再有话」的形态。
    """
    import inspect
    from pathlib import Path

    from cli.main import _resolve_env_kind

    assert "sandbox" in inspect.getsource(_resolve_env_kind), "默认值仍是 sandbox"
    src = Path("cli/main.py").read_text(encoding="utf-8")
    assert src.count('EnvKind(getattr(args, "env_kind", None) or "sandbox")') == 1, \
        "解析只许一处实现（`_resolve_env_kind`）"
