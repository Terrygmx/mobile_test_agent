"""P2 故障注入矩阵 #13/#14（设计 16 节 M4 行；Task 4.2 / P2-10）。

Gate M4（plan）：人工 Promotion 后，后续运行在**正常 find() 阶段命中**、
不再进 Recovery；DEGRADED **不自动撤销** Git 中的 Promotion；`git revert`
回滚且 Experience Store 历史不受影响；矩阵 #13/#14 全过。

| # | 场景 | 预期 |
|---|---|---|
| 13 | promote 后注入连续失败 → Experience DEGRADED | overrides YAML **未被程序改动**（工作区 diff 干净——撤销是人工决策，9.4） |
| 14 | `git revert` promotion commit | resolver 恢复原策略；experience 库状态事件链完整可查（Store 历史不受影响，9.6/E10） |

全部 FakeExecutor（无网络无真机）；真机版（设计 18 步骤 9–10）在
`phase0/verify_p2_m4.py`。

git 语义不 mock：真 `git init` / `commit` / `revert`（与 promoter 测试同一
纪律——subprocess 边界替身会掩盖 revert 与工作区状态的真实交互）。
"""
from __future__ import annotations

import os
import subprocess

import pytest

from executor.executor import ElementNotFound
from experience import (
    SQLiteExperienceStore,
    apply_outcome,
    evaluate,
)
from experience.models import (
    CandidateSeed,
    ExperienceRun,
    ExperienceStatus,
    VerificationPolicy,
)
from experience.promoter import approve_proposal, generate_proposal
from repository.loader import LocatorStrategy
from tests.fault_injection.fi_support import (
    FakeExecutor,
    drift_repo,
    run_matrix,
)

APP = "com.matrix.app"
HOME_PAGE = "<App><Node name='screen.HomeView' visible='true'/></App>"

TAP = """\
schema_version: "0.2"
id: p2_m4_tap
name: p2 m4 promoted tap
suite: smoke
tags: [smoke]
steps:
  - action: launch_app
  - action: tap
    target: HomeView.login_button
    idempotency: IDEMPOTENT
"""


def _git(root, *args: str) -> str:
    env = dict(os.environ,
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    r = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout.strip()


@pytest.fixture()
def repo_env(tmp_path):
    """git 仓库形态的矩阵现场：generated 入库、*.db / state/ 忽略。"""
    (tmp_path / ".gitignore").write_text("*.db\nstate/\n", encoding="utf-8")
    drift_repo(tmp_path)          # generated/ 在 tmp_path 下，随 init 入库
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _promoted(store: SQLiteExperienceStore, repo_root) -> tuple[str, str]:
    """设计 18 步骤 9 的库侧等价：VERIFIED 经验 → proposal → approve
    （写 overrides + git commit）。返回 (experience_id, commit sha)。"""
    exp = store.create_candidate(CandidateSeed(
        review_id=1, recovery_id=1, seed_run_id="run_seed", seed_step_id=1,
        seed_recovery_review_id=1, app_id=APP, screen_id="HomeView",
        target_id="login_button",
        strategy=LocatorStrategy(type="accessibility_id",
                                 value="signin_button",
                                 origin="experience")))
    for i in range(10):
        store.record_run(exp.experience_id, ExperienceRun(
            experience_id=exp.experience_id, run_id=f"r_{i}",
            step_id=100 + i, app_build="1026", result="SUCCESS"))
    store.record_success_build(exp.experience_id, "1026")
    store.update_status(exp.experience_id, ExperienceStatus.VERIFIED,
                        "THRESHOLD_MET", operator="matrix")
    p = generate_proposal(store, store.get_experience(exp.experience_id),
                          reviewer="matrix")
    _, sha = approve_proposal(store, p.proposal_id, repo_root=repo_root,
                              committer="matrix")
    return exp.experience_id, sha


def _engine(tmp_path, store):
    from agent.recovery import RecoveryEngine
    return RecoveryEngine(repo=drift_repo(tmp_path), llm=None, budget=None,
                          experience_store=store, sleep=lambda s: None)


def _overrides_file(repo_env):
    return (repo_env / "repository" / "overrides" / "elements"
            / "HomeView.yaml")


# --- Gate M4 头条：promote 后正常 find() 命中，不再进 Recovery ----------------


def test_promoted_run_hits_in_normal_find_no_recovery(repo_env):
    store = SQLiteExperienceStore(repo_env / "state" / "experience.db")
    exp_id, _sha = _promoted(store, repo_env)
    ex = FakeExecutor(page_source=HOME_PAGE)

    run, trace, _ds, ex = run_matrix(
        repo_env, TAP, ex=ex, repo=drift_repo(repo_env),
        recovery=_engine(repo_env, store), bundle_id=APP)

    r = run.results[0]
    assert r.status == "PASS" and run.exit_code == 0, \
        "promote 后策略进 Locator 链，正常 find() 就命中"
    assert not r.detail.get("recovery_kinds"), \
        "不再进 Recovery（Gate M4：正常 Locator，不触发恢复）"
    assert ex.tap_calls == 1
    # 经验未被使用（正常链命中，不是恢复路径）——样本不记
    assert len(store.get_runs(exp_id)) == 10


# --- 矩阵 #13：DEGRADED 不动 overrides ---------------------------------------


def test_p2_13_degrade_leaves_overrides_untouched(repo_env):
    store = SQLiteExperienceStore(repo_env / "state" / "experience.db")
    exp_id, sha = _promoted(store, repo_env)
    assert _git(repo_env, "status", "--porcelain") == ""

    # 注入连续失败：正常 find 失败 → recovery → 候选 Guard find_all=[]
    # → TARGET_NOT_FOUND（4.7：计入失败样本）×2 → 滑动窗口触发。
    for i in range(2):
        # run_matrix 以该参数落 trace/suites——每次独立子目录避免
        # runs.run_id 唯一约束冲突；state/ 已被 .gitignore 忽略。
        workdir = repo_env / "state" / f"injection_{i}"
        workdir.mkdir(parents=True, exist_ok=True)
        ex = FakeExecutor(find_script=[ElementNotFound("drifted")],
                          find_all_script=[[]], page_source=HOME_PAGE)
        run, _trace, _ds, _ex = run_matrix(
            workdir, TAP, ex=ex, repo=drift_repo(repo_env),
            recovery=_engine(repo_env, store), bundle_id=APP)
        assert run.exit_code == 1

    exp = store.get_experience(exp_id)
    assert exp.sample_count == 12 and exp.failure_count == 2, \
        "两次注入都记了失败样本（E11）"
    out = evaluate(exp, store.get_runs(exp_id), VerificationPolicy(),
                   auto_verify_eligible=None)
    apply_outcome(store, exp, out, operator="matrix")
    assert store.get_experience(exp_id).status is ExperienceStatus.DEGRADED

    # 9.4 核心：状态降级是 Experience Store 的事——overrides **未被程序
    # 改动**（撤销 Promotion 是人工 git revert，不是状态机行为）。
    assert _git(repo_env, "status", "--porcelain") == "", \
        "工作区 diff 干净——降级不写 overrides"
    text = _overrides_file(repo_env).read_text(encoding="utf-8")
    assert "origin: experience" in text and "signin_button" in text, \
        "Promotion 未被自动撤销"


# --- 矩阵 #14：git revert 恢复 Repository，Store 历史不受影响 ----------------


def test_p2_14_git_revert_restores_repo_store_history_intact(repo_env):
    store = SQLiteExperienceStore(repo_env / "state" / "experience.db")
    exp_id, sha = _promoted(store, repo_env)
    events_before = [(e.from_status, e.to_status, e.reason)
                     for e in store.get_state_events(exp_id)]

    _git(repo_env, "revert", "--no-edit", sha)

    # Repository 恢复原策略：overrides 的 experience 文档被 revert 掉，
    # resolve 链回到 source-only。
    assert not _overrides_file(repo_env).exists(), \
        "revert 撤掉了 promotion 写入的 overrides 文档"
    eff = drift_repo(repo_env).resolve("HomeView.login_button", build="1026")
    assert all(s.origin != "experience" for s in eff.strategies), \
        "9.6：resolver 恢复原策略"

    # Experience Store 历史不受影响（9.6/E10：两个独立的事实来源）
    exp = store.get_experience(exp_id)
    assert exp.promoted is True and exp.promoted_commit == sha, \
        "promoted 记账是 Store 的事实，git revert 不改写它"
    assert exp.status is ExperienceStatus.VERIFIED
    assert [(e.from_status, e.to_status, e.reason)
            for e in store.get_state_events(exp_id)] == events_before
    assert len(store.get_runs(exp_id)) == 10, "样本证据原样保留（11.2）"
