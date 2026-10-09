"""Task 2.4 / P3-08：`mta plan` CLI（**首个自主命令**）。

判据（plan Task 2.4 Steps）：参数校验 / 输出双格式 / plan 入库可回查；
矩阵 #1（production 启动即拒绝）与 #6（空改动 → 空 Plan 并明示）在这里**端到端**跑
（planner 自身的判据在 `test_planner_e2e.py`）。

fixture 用**真 git 仓库 + 真 sqlite**（`drift_repo` 的 generated 目录 + `TraceStore`）：
替身 git/sql 会让「ref 写法」「列名」这类错误静默通过（Task 2.2/2.3 的教训）。
"""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from agents.storage import SQLiteAgentStore
from agents.policy_config import PolicyConfigError
from cli.main import main
from tests.fault_injection.fi_support import drift_repo

APP_FILE = "App.swift"
OTHER_FILE = "Other.swift"
# 与 `drift_repo` 的 HomeView/ProfileView 元素对齐（风险：login LOW / pay HIGH /
# confirm_pay CRITICAL / ghost LOW）
_FILE_ELEMENTS = {
    APP_FILE: ["HomeView.login_button", "HomeView.pay_button",
               "HomeView.confirm_pay_button"],
    OTHER_FILE: ["ProfileView.ghost_button"],
}
_CASES = {
    "login_001": "HomeView.login_button",
    "pay_002": "HomeView.confirm_pay_button",
    "search_004": "HomeView.pay_button",
    "profile_005": "ProfileView.ghost_button",
}


def _git(home, cwd, *args) -> str:
    """跑一条 git（`HOME` 隔离到 tmp_path，免得用户的 `~/.gitconfig` 改行为）。"""
    env = dict(os.environ, HOME=str(home))
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                       text=True, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _case_yaml(case_id: str, target: str) -> str:
    return (f'schema_version: "0.2"\nid: {case_id}\nname: {case_id}\n'
            f'suite: smoke\nsteps:\n  - action: launch_app\n'
            f'  - action: tap\n    target: {target}\n')


def _metadata(git_commit: str) -> dict:
    by_screen: dict[str, list[dict]] = {}
    for file, targets in _FILE_ELEMENTS.items():
        for target in targets:
            screen, elem = target.split(".", 1)
            by_screen.setdefault(screen, []).append(
                {"id": elem, "accessibility_id": elem, "type": "button",
                 "resolution_type": "literal", "source": {"file": file, "line": 1}})
    return {"app_version": "debug", "build": "local", "git_commit": git_commit,
            "screens": sorted(by_screen),
            "screen_elements": [{"name": s, "elements": els}
                                for s, els in by_screen.items()]}


@pytest.fixture()
def proj(tmp_path):
    """自洽的 `mta plan` 工程：git 仓库（两次提交）+ generated/local + suites。"""
    home = tmp_path / "home"
    home.mkdir()
    drift_repo(tmp_path)                       # tmp_path/generated/local/{elements,screens}
    gen = tmp_path / "generated" / "local"

    git_root = tmp_path / "git"
    subprocess.run(["git", "init", "-q", str(git_root)], check=True,
                   capture_output=True)
    _git(home, git_root, "config", "user.email", "t@t")
    _git(home, git_root, "config", "user.name", "t")
    (git_root / APP_FILE).write_text("v1\n")
    _git(home, git_root, "add", "-A")
    _git(home, git_root, "commit", "-qm", "base")
    (git_root / APP_FILE).write_text("v2\n")   # ← 本次「改动」
    _git(home, git_root, "add", "-A")
    _git(home, git_root, "commit", "-qm", "change")
    head = _git(home, git_root, "rev-parse", "HEAD").strip()

    (gen / "source_metadata.json").write_text(
        json.dumps(_metadata(head), ensure_ascii=False), encoding="utf-8")
    suites = tmp_path / "suites" / "smoke"
    suites.mkdir(parents=True)
    for case_id, target in _CASES.items():
        (suites / f"{case_id}.yaml").write_text(_case_yaml(case_id, target),
                                                encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    return {"tmp": tmp_path, "home": home, "git": git_root, "gen": gen,
            "suites_root": tmp_path / "suites", "head": head,
            "trace_db": out / "trace.db", "exp_db": out / "experience.db",
            "agent_db": out / "agent.db"}


def _argv(proj, *extra) -> list[str]:
    return ["plan", "--repo-root", str(proj["git"]),
            "--generated", str(proj["gen"]),
            "--suites-root", str(proj["suites_root"]),
            "--db", str(proj["trace_db"]),
            "--exp-db", str(proj["exp_db"]),
            "--agent-db", str(proj["agent_db"]),
            "--no-llm", *extra]


def _plan_row(proj) -> dict:
    plans = SQLiteAgentStore(proj["agent_db"]).list_plans()
    assert len(plans) == 1, plans
    return plans[0]


# --- 矩阵 #1：F7 前置（启动即拒） ---------------------------------------------


def test_production_is_refused_before_anything_is_written(proj, capsys):
    """`--env-kind production` → **启动即拒**，且不留下任何库文件。"""
    with pytest.raises(SystemExit) as e:
        main(_argv(proj, "--env-kind", "production"))
    assert "F7" in str(e.value)
    assert "production" in str(e.value)
    assert not proj["agent_db"].exists(), "前置在写任何东西之前，不该有库文件"


def test_autonomous_disabled_in_policy_is_refused(proj):
    policy = proj["tmp"] / "off.yaml"
    policy.write_text("autonomous:\n  enabled: false\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="autonomous.enabled=false"):
        main(_argv(proj, "--policy", str(policy)))


def test_explicit_policy_path_must_exist(proj):
    """Task 1.2 的 `--policy` 语义：**显式给了却不存在 → 报错**（不静默用默认值）。"""
    with pytest.raises(PolicyConfigError):
        main(_argv(proj, "--policy", str(proj["tmp"] / "nope.yaml")))


def test_default_policy_resolution_is_used_when_flag_absent(proj):
    """不给 `--policy` → 走默认解析（读不到就用内置默认值），**不报错**。"""
    assert main(_argv(proj)) == 0


# --- 前置输入错误 → exit 3 ----------------------------------------------------


def test_missing_metadata_exits_3(proj, capsys):
    assert main(_argv(proj, "--metadata",
                      str(proj["tmp"] / "nope.json"))) == 3
    assert "PREFLIGHT ERROR" in capsys.readouterr().out


def test_missing_base_build_metadata_exits_3(proj, capsys):
    assert main(_argv(proj, "--base-build", "9999")) == 3
    assert "base-build" in capsys.readouterr().out


def test_unresolvable_git_ref_exits_3(proj, capsys):
    """`--base-build` 的 metadata 存在但 `git_commit` 是个坏 ref → exit 3。"""
    bad = proj["gen"].parent / "9999"
    bad.mkdir()
    (bad / "source_metadata.json").write_text(
        json.dumps({**_metadata("nosuchref")}), encoding="utf-8")
    assert main(_argv(proj, "--base-build", "9999")) == 3
    assert "git 改动失败" in capsys.readouterr().out


def test_bad_suite_yaml_exits_3(proj, capsys):
    (proj["suites_root"] / "smoke" / "broken.yaml").write_text(
        "schema_version: '9.9'\nid: broken\n", encoding="utf-8")
    assert main(_argv(proj)) == 3
    assert "PREFLIGHT ERROR" in capsys.readouterr().out


# --- 主路径：排序 + 落库 + 双格式 ---------------------------------------------


def test_plan_is_ranked_and_persisted(proj, capsys):
    assert main(_argv(proj)) == 0
    out = capsys.readouterr().out
    row = _plan_row(proj)
    tasks = row["tasks"]                       # `get_plan`/`list_plans` 已解析
    assert [t["testcase_id"] for t in tasks] == ["pay_002", "search_004",
                                                 "login_001"]
    assert [t["priority"] for t in tasks] == [90, 70, 40]
    assert tasks[0]["reasons"][0].startswith("impact: 命中改动影响面")
    assert any(r.startswith("score: ") for r in tasks[0]["reasons"])
    assert row["plan_id"] in out and "plan: 3 tasks" in out


def test_unaffected_case_is_not_in_the_plan(proj, capsys):
    main(_argv(proj))
    assert "profile_005" not in capsys.readouterr().out


def test_json_output_shape(proj, capsys):
    assert main(_argv(proj, "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "0.1"
    assert payload["app_build"] == "local"
    assert payload["range"]["base"] == "HEAD~1"
    assert payload["range"]["head"] == proj["head"]   # metadata 的 git_commit
    assert len(payload["changed_files"]) == 1
    assert payload["changed_files"][0].endswith(APP_FILE)
    assert [t["testcase_id"] for t in payload["tasks"]][0] == "pay_002"
    assert payload["notes"], "过程结论（口径/降级）必须在 JSON 里可见"
    assert isinstance(payload["audit"], list)


def test_git_commit_is_a_resolved_sha_not_a_moving_ref(proj, capsys):
    """`git_commit` 必须是**确定的 sha**：`plan_id` 由它参与内容寻址。

    `HEAD` 是**移动的 ref** —— 两个不同的提交会算出同一个 `plan_id`，后写的
    **静默覆盖**前一份（review_p3_task14 登记的那条）。
    """
    main(_argv(proj, "--json"))
    payload = json.loads(capsys.readouterr().out)
    assert payload["git_commit"] == proj["head"]
    assert len(payload["git_commit"]) == 40
    assert payload["git_commit"] != "HEAD"


def test_rerun_is_idempotent_and_does_not_grow_the_table(proj):
    """同一输入 → 同一 `plan_id` → upsert（库**不增长**）。"""
    assert main(_argv(proj)) == 0
    first = _plan_row(proj)["plan_id"]
    assert main(_argv(proj)) == 0
    assert len(SQLiteAgentStore(proj["agent_db"]).list_plans()) == 1
    assert _plan_row(proj)["plan_id"] == first


def test_different_changed_files_yield_a_different_plan_id(proj):
    """改动集不同 → 不同 `plan_id`（**不互相覆盖**）。"""
    main(_argv(proj))
    base = _plan_row(proj)["plan_id"]
    # 换一个 base：HEAD~1 与「空范围」（base=当前提交）得到不同的改动集
    same = proj["gen"].parent / "same"
    same.mkdir()
    (same / "source_metadata.json").write_text(
        json.dumps(_metadata(proj["head"])), encoding="utf-8")
    assert main(_argv(proj, "--base-build", "same")) == 0
    ids = [p["plan_id"] for p in SQLiteAgentStore(
        proj["agent_db"]).list_plans()]
    assert base in ids and len(set(ids)) == 2, ids


# --- 矩阵 #6：空改动 → 空 Plan + 明示 ----------------------------------------


def test_empty_diff_yields_empty_plan_with_reason(proj, capsys):
    """base == head → 无改动 → 空 Plan，且**明示**原因（矩阵 #6）。"""
    same = proj["gen"].parent / "same"
    same.mkdir()
    (same / "source_metadata.json").write_text(
        json.dumps(_metadata(proj["head"])), encoding="utf-8")
    assert main(_argv(proj, "--base-build", "same")) == 0
    out = capsys.readouterr().out
    assert "EMPTY PLAN" in out
    assert "changed_files 为空" in out
    assert _plan_row(proj)["tasks"] == []


# --- 历史失败率（含「无历史」与「读不出来」的分界） ---------------------------


def _seed_trace(proj, *, fail_first_of_two: bool = True) -> None:
    from tracer.storage import TraceStore

    store = TraceStore(proj["trace_db"])
    for run, failed in (("run_1", fail_first_of_two), ("run_2", False)):
        store.start_run(run, suite="smoke", app_bundle_id="com.x",
                        app_build="local")
        tc = store.start_testcase(run, "login_001", attempt=1)
        store.record_step(tc, 0, "launch_app", status="SUCCESS")
        store.record_step(tc, 1, "tap", target_id="login_button",
                          status="FAILED" if failed else "SUCCESS")
        store.end_testcase(tc, "FAIL" if failed else "PASS")
        store.end_run(run, "FAIL" if failed else "PASS")


def test_history_lifts_the_score(proj, capsys):
    """有 trace 库且 `login_001` 跑 2 次失败 1 次 → 40 + 30×0.5 = 55。"""
    _seed_trace(proj)
    main(_argv(proj, "--json"))
    tasks = {t["testcase_id"]: t for t in
             json.loads(capsys.readouterr().out)["tasks"]}
    assert tasks["login_001"]["priority"] == 55
    assert "history: 历史失败率 0.50" in tasks["login_001"]["reasons"]


def test_missing_trace_db_degrades_with_a_visible_marker(proj, capsys):
    """trace 库**不存在** = 合法（还没跑过用例）：按 0.0 计，但**每条都标注**。

    标记必须进 `reasons`（唯一落库的位置）——否则落库的 Plan 上「真的没失败过」
    与「根本没有历史」长得一模一样。
    """
    assert not proj["trace_db"].exists()
    main(_argv(proj, "--json"))
    payload = json.loads(capsys.readouterr().out)
    for task in payload["tasks"]:
        assert any("（无历史数据：trace 库尚不存在）" in r
                   for r in task["reasons"]), task
    assert any("trace 库尚不存在" in n for n in payload["notes"])


def test_unreadable_trace_db_exits_3(proj, capsys):
    """库**存在但读不了** → exit 3（**不**按「无历史」兜）。"""
    proj["trace_db"].write_text("这不是 sqlite 库", encoding="utf-8")
    assert main(_argv(proj)) == 3
    assert "读 trace 历史失败" in capsys.readouterr().out


# --- LLM 开关 -----------------------------------------------------------------


def test_no_llm_flag_skips_provider_construction(proj, monkeypatch):
    """`--no-llm` 与「没有 LLM_API_KEY」都不构造 provider（与 `mta run` 同款）。"""
    import llm.provider as provider_mod

    built: list[str] = []

    class _Spy:
        def __init__(self, *a, **kw):
            built.append("provider")

    monkeypatch.setattr(provider_mod, "LLMProvider", _Spy)
    monkeypatch.setenv("LLM_API_KEY", "x")
    assert main(_argv(proj)) == 0                  # 默认带 --no-llm（见 _argv）
    assert built == []
    assert main(_argv(proj)[:-1]) == 0             # 去掉 --no-llm
    assert built == ["provider"]


# --- 参数覆盖 -----------------------------------------------------------------


def test_build_flag_overrides_the_metadata_build(proj, capsys):
    main(_argv(proj, "--build", "1026", "--json"))
    payload = json.loads(capsys.readouterr().out)
    assert payload["app_build"] == "1026"


def test_base_build_supplies_the_diff_base(proj, capsys):
    """`--base-build` 的 metadata 提供 git diff 的 base（并作为 drift 回退面）。"""
    parent = proj["gen"].parent / "1025"
    parent.mkdir()
    # 基线 = 第一个提交（HEAD~1）→ 与默认范围等价
    first = _git(proj["home"], proj["git"], "rev-parse", "HEAD~1").strip()
    (parent / "source_metadata.json").write_text(
        json.dumps(_metadata(first)), encoding="utf-8")
    assert main(_argv(proj, "--base-build", "1025", "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["range"]["base"] == first
    assert [t["testcase_id"] for t in payload["tasks"]][0] == "pay_002"


def test_git_commit_is_resolved_even_when_metadata_lacks_it(proj, capsys):
    """metadata **没有** `git_commit` 时也要落到确定的 sha（`HEAD` → rev-parse）。

    这条才是 `rev-parse` 的守卫：`plan_id` 由 `(app_build, git_commit, changed_files)`
    内容寻址，而 `HEAD` 是**移动的 ref** —— 两个不同的提交会算出同一个 `plan_id`，
    后写的**静默覆盖**前一份。
    """
    meta = _metadata(proj["head"])
    del meta["git_commit"]
    (proj["gen"] / "source_metadata.json").write_text(
        json.dumps(meta), encoding="utf-8")
    assert main(_argv(proj, "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["git_commit"] == proj["head"]
    assert payload["range"]["head"] == "HEAD", "范围里如实反映「没给具体 commit」"


def test_empty_policy_path_is_not_treated_as_absent(proj):
    """`--policy ""` → **报错**，不是静默退回默认解析。

    实现用 `is not None` 而不是 `if args.policy`：后者会把空串当「没给」，
    于是「操作员明确指了一个（坏）路径」与「没给」长得一模一样。
    """
    with pytest.raises(PolicyConfigError):
        main(_argv(proj, "--policy", ""))
