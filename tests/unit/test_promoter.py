"""Task 4.1 / P2-09：promoter——Proposal 生成 → 人工 approve → 写 overrides
→ git commit（设计 9.1–9.3 / 9.5 / E10）。

plan step 1 的失败测试清单逐条对应：
  - Proposal 只对 VERIFIED 生成（或走 9.5 显式人工路径：非 VERIFIED 需
    `manual_override_of_auto_policy=true` + 理由）；
  - diff 是可 review 的 YAML 补丁文本；
  - approve 后 overrides 文件含 `origin: experience`；
  - approve 后 `git log` 出现 commit（工具辅助生成、走正常 PR 流程，E10）。

两条时间线（9.4）：promote **不改** Experience.status——它只把
`promoted=True + promoted_commit` 记在 Experience 行上；状态归 Verifier 管。

9.3 红线（review_p2_task34 建议动作 3）：人工 promote 必须走 proposal
审计，不提供「跳过 proposal 直写 overrides」的入口。
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from experience import SQLiteExperienceStore
from experience.models import CandidateSeed, ExperienceRun, ExperienceStatus
from experience.promoter import (
    approve_proposal,
    generate_proposal,
)
from repository.loader import LocatorStrategy


@pytest.fixture()
def repo_root(tmp_path) -> Path:
    """一个真 git 仓库（approve 要落 commit；E10 的 revert 前提）。"""
    root = tmp_path / "proj"
    root.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)}
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, env=env,
                       capture_output=True)
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (root / "README.md").write_text("init\n")
    git("add", ".")
    git("commit", "-qm", "init")
    return root


@pytest.fixture()
def store(tmp_path) -> SQLiteExperienceStore:
    return SQLiteExperienceStore(str(tmp_path / "experience.db"))


def _seed(store: SQLiteExperienceStore, *,
          target_id: str = "login_button") -> str:
    exp = store.create_candidate(CandidateSeed(
        review_id=7, recovery_id=1, seed_run_id="run_seed", seed_step_id=12,
        seed_recovery_review_id=7, app_id="com.x", screen_id="HomeView",
        target_id=target_id,
        strategy=LocatorStrategy(type="accessibility_id", value="v2",
                                 origin="experience")))
    for i in range(10):
        store.record_run(exp.experience_id, ExperienceRun(
            experience_id=exp.experience_id, run_id=f"r_{i}",
            step_id=100 + i, app_build="1026", result="SUCCESS"))
    # E7（矩阵 #6）：record_run 不动 validated_builds——build 只在完整
    # Guard+执行成功后追加，测试里直接用库侧记账方法。
    store.record_success_build(exp.experience_id, "1026")
    return exp.experience_id


def _verified(store: SQLiteExperienceStore, **kw) -> str:
    exp_id = _seed(store, **kw)
    store.update_status(exp_id, ExperienceStatus.VERIFIED, "THRESHOLD_MET")
    return exp_id


# --- Proposal 生成（9.2/9.3） -------------------------------------------------


def test_proposal_generated_for_verified(store):
    exp_id = _verified(store)
    p = generate_proposal(store, store.get_experience(exp_id))
    assert p.status == "PENDING"
    assert p.experience_id == exp_id
    assert p.manual_override_of_auto_policy is False
    # evidence_summary 是可复核的证据快照（9.3 四要素）
    ev = json.loads(p.evidence_summary)
    assert ev["sample_count"] == 10 and ev["success_rate"] == 1.0
    assert ev["distinct_runs"] == 10 and "1026" in ev["validated_builds"]
    # diff 是可 review 的 YAML 补丁文本
    assert "kind: element" in p.diff
    assert "id: login_button" in p.diff and "screen: HomeView" in p.diff
    assert "origin: experience" in p.diff


def test_proposal_requires_verified_or_explicit_manual_override(store):
    """非 VERIFIED → 拒绝；9.5 显式人工路径必须带 `manual_override=True`
    **和**理由（缺一即拒——无理由的越权不可审计）。"""
    cand = _seed(store, target_id="cand_button")
    with pytest.raises(ValueError, match="VERIFIED"):
        generate_proposal(store, store.get_experience(cand))

    p = generate_proposal(store, store.get_experience(cand),
                          manual_override=True,
                          reason="业务确认只此一处的支付确认按钮")
    assert p.manual_override_of_auto_policy is True
    ev = json.loads(p.evidence_summary)
    assert ev["override_reason"] == "业务确认只此一处的支付确认按钮"
    assert store.list_promotion_proposals(status="PENDING")[0] \
        .proposal_id == p.proposal_id


def test_proposal_manual_override_requires_reason(store):
    cand = _seed(store)
    with pytest.raises(ValueError, match="理由"):
        generate_proposal(store, store.get_experience(cand),
                          manual_override=True, reason=None)


def test_proposal_rejected_experience_is_terminal(store):
    """REJECTED 不可 promote（含人工路径）——9.5 的人工出口只服务
    「从未被否决」的候选；否决过的要走 E5 重新学习。"""
    rid = _seed(store, target_id="rejected_button")
    store.update_status(rid, ExperienceStatus.REJECTED, "MANUAL_REJECT")
    with pytest.raises(ValueError, match="REJECTED"):
        generate_proposal(store, store.get_experience(rid),
                          manual_override=True, reason="r")


def test_proposal_persisted_and_queryable(store):
    exp_id = _verified(store)
    p = generate_proposal(store, store.get_experience(exp_id))
    got = store.get_promotion_proposal(p.proposal_id)
    assert got is not None and got.experience_id == exp_id
    assert got.diff == p.diff and got.status == "PENDING"


# --- Approve（写 overrides + git commit + promoted 记账） ---------------------


def test_approve_writes_overrides_and_commits(store, repo_root):
    exp_id = _verified(store)
    p = generate_proposal(store, store.get_experience(exp_id))

    approved, sha = approve_proposal(store, p.proposal_id,
                                     repo_root=repo_root, committer="t")

    target = repo_root / "repository" / "overrides" / "elements" \
        / "HomeView.yaml"
    text = target.read_text(encoding="utf-8")
    assert "origin: experience" in text and "id: login_button" in text
    assert approved.status == "APPROVED" and approved.resolved_commit == sha

    # git log 出现 promotion commit（E10：可 revert）
    log = subprocess.run(["git", "log", "--oneline"], cwd=repo_root,
                         capture_output=True, text=True).stdout
    assert "promote" in log and sha[:7] in log, \
        "promotion commit 出现在 git log（E10：可 revert 的正常 commit）"

    # 9.4：两条时间线——promoted 记账，status 不动
    exp = store.get_experience(exp_id)
    assert exp.promoted is True and exp.promoted_commit == sha
    assert exp.status is ExperienceStatus.VERIFIED


def test_approve_twice_fails_loud(store, repo_root):
    exp_id = _verified(store)
    p = generate_proposal(store, store.get_experience(exp_id))
    approve_proposal(store, p.proposal_id, repo_root=repo_root)
    with pytest.raises(ValueError, match="PENDING"):
        approve_proposal(store, p.proposal_id, repo_root=repo_root)


def test_approve_refuses_to_shadow_existing_manual_override(store, repo_root):
    """目标 overrides 文件里已有同 id 的元素定义 → fail-loud（不可静默
    覆盖——人工写过的 override 只能人工处置）。"""
    exp_id = _verified(store)
    lv = repo_root / "repository" / "overrides" / "elements"
    lv.mkdir(parents=True)
    (lv / "HomeView.yaml").write_text(
        "schema_version: \"1.0\"\nkind: element\nid: login_button\n"
        "screen: HomeView\ntype: button\nstrategies:\n"
        "  - {type: accessibility_id, value: manual_v1, origin: manual}\n",
        encoding="utf-8")
    p = generate_proposal(store, store.get_experience(exp_id))
    with pytest.raises(ValueError, match="已存在"):
        approve_proposal(store, p.proposal_id, repo_root=repo_root)
    assert store.get_promotion_proposal(p.proposal_id).status == "PENDING"


def test_approve_unknown_proposal_exit(store, repo_root):
    with pytest.raises(ValueError, match="no such promotion proposal"):
        approve_proposal(store, "prop_nope", repo_root=repo_root)


# --- CLI：mta experience promote（两段式） ------------------------------------


def _run_cli(argv: list[str]) -> tuple[int, str]:
    import io
    from contextlib import redirect_stdout

    from cli.main import main
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


def test_cli_promote_generate_then_approve(store, repo_root, tmp_path):
    exp_id = _verified(store)
    db = str(tmp_path / "experience.db")

    code, out1 = _run_cli(["experience", "promote", exp_id, "--exp-db", db])
    assert code == 0
    assert "PENDING" in out1 and "origin: experience" in out1
    assert store.list_promotion_proposals(status="PENDING")
    proposal_id = store.list_promotion_proposals(status="PENDING")[0] \
        .proposal_id
    assert not (repo_root / "repository" / "overrides").exists(), \
        "generate 阶段不写任何文件（9.3 审计）"

    code, out2 = _run_cli(["experience", "promote", exp_id, "--exp-db", db,
                           "--approve", proposal_id, "--repo-root",
                           str(repo_root)])
    assert code == 0
    assert "APPROVED" in out2 and "promoted=True" in out2
    assert store.get_experience(exp_id).promoted is True
    assert "origin: experience" in (
        repo_root / "repository" / "overrides" / "elements"
        / "HomeView.yaml").read_text(encoding="utf-8")


def test_cli_promote_candidate_without_override_exit_3(store, tmp_path):
    exp_id = _seed(store)
    code, out = _run_cli(["experience", "promote", exp_id,
                          "--exp-db", str(tmp_path / "experience.db")])
    assert code == 3
    assert "VERIFIED" in out and "manual_override" in out, \
        "错误信息写明两条出路（verify 升级 / 9.5 显式越权）"


def test_cli_promote_manual_override_requires_reason_exit_3(store, tmp_path):
    cand = _seed(store, target_id="cand_button")
    code, _ = _run_cli(["experience", "promote", cand,
                        "--exp-db", str(tmp_path / "experience.db"),
                        "--manual-override"])
    assert code == 3
