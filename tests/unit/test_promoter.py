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
import os
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


def test_approve_merges_into_existing_override_doc(store, repo_root):
    """同 id 已有 override（漂移场景的常态：旧名是 P1 人工登记）→
    **并入**该文档 strategies 链尾，单文档不产双 doc（Gate M4 真机实锤：
    拒绝同 id 会堵死 Promotion 的正常用法）。

    并入的 strategy 不带 mode 键副作用——resolver 的 origin 稳定排序保证
    experience 落尝试链尾（9.2）；git diff 可审计，revert 可整笔撤销。
    """
    exp_id = _verified(store)
    lv = repo_root / "repository" / "overrides" / "elements"
    lv.mkdir(parents=True)
    (lv / "HomeView.yaml").write_text(
        "schema_version: \"1.0\"\nkind: element\nid: login_button\n"
        "screen: HomeView\ntype: button\nstrategies:\n"
        "  - {type: accessibility_id, value: manual_v1, origin: manual}\n",
        encoding="utf-8")
    p = generate_proposal(store, store.get_experience(exp_id))

    approved, sha = approve_proposal(store, p.proposal_id,
                                     repo_root=repo_root)

    import yaml
    docs = [d for d in yaml.safe_load_all(
        (lv / "HomeView.yaml").read_text(encoding="utf-8")) if d]
    assert len(docs) == 1, "单文档——不产同 id 双 doc（静默覆盖不存在）"
    origins = [(s["origin"], s["value"]) for s in docs[0]["strategies"]]
    assert origins == [("manual", "manual_v1"), ("experience", "v2")], \
        "experience 策略并入链尾，manual 主策略保留"
    assert approved.status == "APPROVED" and sha
    assert store.get_experience(exp_id).promoted is True


def test_approve_merge_preserves_hand_written_comments(store, repo_root):
    """P2-1（review_p2_task42）：并入用**行级手术**——手写 overrides 的
    头注释与行内注释逐字节保留（注释承载出处/理由/幂等声明，yaml 重
    序列化会静默抹掉它们，属人的数据丢失）。"""
    exp_id = _verified(store)
    lv = repo_root / "repository" / "overrides" / "elements"
    lv.mkdir(parents=True)
    before = (
        "# LoginView 手写元素（M1 起点；改动走人工 review）\n"
        "# login_button 的 idempotency 声明：H7 非幂等不重试\n"
        "schema_version: \"1.0\"\n"
        "kind: element\n"
        "id: login_button  # 源码 LoginDemoApp.swift:58\n"
        "screen: HomeView\n"
        "type: button\n"
        "strategies:\n"
        "  - {type: accessibility_id, value: manual_v1, origin: manual}\n")
    (lv / "HomeView.yaml").write_text(before, encoding="utf-8")
    p = generate_proposal(store, store.get_experience(exp_id))

    approve_proposal(store, p.proposal_id, repo_root=repo_root)

    after = (lv / "HomeView.yaml").read_text(encoding="utf-8")
    assert before.splitlines()[0] in after, "头注释保留"
    assert "H7 非幂等不重试" in after, "行内理由注释保留"
    assert "源码 LoginDemoApp.swift:58" in after, "id 行内注释保留"
    import yaml
    docs = [d for d in yaml.safe_load_all(after) if d]
    assert len(docs) == 1
    assert ("experience", "v2") in [(s["origin"], s["value"])
                                    for s in docs[0]["strategies"]]
    # 既有字节除插入行外不动（git diff = 纯新增）
    assert before in after, "原文全文保留（插入式，非重排）"


def test_approve_refuses_duplicate_experience_strategy(store, repo_root):
    """同 id 文档已含相同 experience 策略 → 重复 promote，fail-loud
    （不重复追加，proposal 保持 PENDING）。"""
    exp_id = _verified(store)
    lv = repo_root / "repository" / "overrides" / "elements"
    lv.mkdir(parents=True)
    (lv / "HomeView.yaml").write_text(
        "schema_version: \"1.0\"\nkind: element\nid: login_button\n"
        "screen: HomeView\ntype: button\nstrategies:\n"
        "  - {type: accessibility_id, value: manual_v1, origin: manual}\n"
        "  - {type: accessibility_id, value: v2, origin: experience}\n",
        encoding="utf-8")
    p = generate_proposal(store, store.get_experience(exp_id))
    with pytest.raises(ValueError, match="重复 promote"):
        approve_proposal(store, p.proposal_id, repo_root=repo_root)
    assert store.get_promotion_proposal(p.proposal_id).status == "PENDING"


def test_approve_unknown_proposal_exit(store, repo_root):
    with pytest.raises(ValueError, match="no such promotion proposal"):
        approve_proposal(store, "prop_nope", repo_root=repo_root)


def test_approve_git_failure_rolls_back_file_write(store, repo_root,
                                                   tmp_path):
    """P2-1（review_p2_task41）：git 身份缺失 → commit 失败 → overrides
    写入**回滚**、proposal 保持 PENDING——重试不被自己写了一半的文件
    挡死（首版实锤：文件已写 + 重试被误报「人工 override」）。"""
    exp_id = _verified(store)
    p = generate_proposal(store, store.get_experience(exp_id))
    # 强制 commit 失败的可复现方式：user.useConfigOnly=true + 无 email
    # ——git 遇到该组合必拒（Apple Git 会用账户名/主机名自动推导身份，
    # 仅删身份源在本机探不红）。
    subprocess.run(["git", "-C", str(repo_root), "config",
                    "user.useConfigOnly", "true"], capture_output=True)
    subprocess.run(["git", "-C", str(repo_root), "config", "--unset",
                    "user.email"], capture_output=True)
    subprocess.run(["git", "-C", str(repo_root), "config", "--unset",
                    "user.name"], capture_output=True)
    monkey = pytest.MonkeyPatch()
    for k, v in dict(GIT_CONFIG_GLOBAL="/dev/null",
                     GIT_CONFIG_SYSTEM="/dev/null",
                     HOME=str(tmp_path / "nohome")).items():
        monkey.setenv(k, v)
    try:
        with pytest.raises(RuntimeError, match="已回滚"):
            approve_proposal(store, p.proposal_id, repo_root=repo_root)
    finally:
        monkey.undo()
        subprocess.run(["git", "-C", str(repo_root), "config", "--unset",
                        "user.useConfigOnly"], capture_output=True)
    target = (repo_root / "repository" / "overrides" / "elements"
              / "HomeView.yaml")
    assert not target.exists(), "git 失败 → 新建的文件回滚删除"
    assert store.get_promotion_proposal(p.proposal_id).status == "PENDING"
    assert store.get_experience(exp_id).promoted is False

    # 修好 git 后重试 = 续传（不被残留文件挡死）
    approved, sha = approve_proposal(store, p.proposal_id,
                                     repo_root=repo_root)
    assert approved.status == "APPROVED" and sha
    assert target.exists()


def test_approve_detects_id_with_trailing_comment(store, repo_root):
    """P3-2（review_p2_task41）：`id: login_button  # 备注` 也要命中同 id
    检查——漏检会追加出同 id 双文档（loader 后者胜，静默覆盖）。定档修订
    后命中 → **并入**该文档，行内注释逐字节保留（P2-1 手术式插入）。"""
    exp_id = _verified(store)
    lv = repo_root / "repository" / "overrides" / "elements"
    lv.mkdir(parents=True)
    (lv / "HomeView.yaml").write_text(
        "schema_version: \"1.0\"\nkind: element\nid: login_button  # 人工备注\n"
        "screen: HomeView\ntype: button\nstrategies:\n"
        "  - {type: accessibility_id, value: manual_v1, origin: manual}\n",
        encoding="utf-8")
    p = generate_proposal(store, store.get_experience(exp_id))

    approved, sha = approve_proposal(store, p.proposal_id,
                                     repo_root=repo_root)

    text = (lv / "HomeView.yaml").read_text(encoding="utf-8")
    import yaml
    docs = [d for d in yaml.safe_load_all(text) if d]
    assert len(docs) == 1, "注释行没漏检——并入单文档而非追加双 doc"
    assert ("experience", "v2") in [(s["origin"], s["value"])
                                    for s in docs[0]["strategies"]]
    assert "人工备注" in text, "行内注释保留"
    assert approved.status == "APPROVED"


def test_promoter_diff_uses_append_mode_to_keep_source_chain(store,
                                                             tmp_path):
    """P2-2（review_p2_task41）定档 (c)：override doc 带 `mode: append`——
    merge 层把 generated/manual/source 链保留在前、experience 追加链尾，
    §9.2「排链尾不丢弃」由写入口兑现（首版 replace 会丢 source 链）。"""
    exp_id = _verified(store)
    p = generate_proposal(store, store.get_experience(exp_id))
    assert "mode: append" in p.diff

    # 端到端：promoter 产物与 generated 并存时，source 策略仍在链上
    ov = tmp_path / "overrides" / "elements"
    ov.mkdir(parents=True)
    (ov / "HomeView.yaml").write_text(p.diff, encoding="utf-8")
    gen = tmp_path / "generated"
    (gen / "elements").mkdir(parents=True)
    (gen / "elements" / "HomeView.yaml").write_text(
        "schema_version: \"1.0\"\nkind: element\nid: login_button\n"
        "screen: HomeView\ntype: button\nstrategies:\n"
        "  - {type: accessibility_id, value: src_v1, origin: source}\n",
        encoding="utf-8")
    from repository.resolver import Repository
    eff = Repository.from_dirs(generated_root=str(gen),
                               overrides_root=str(tmp_path / "overrides")) \
        .resolve("HomeView.login_button", build="local")
    assert [(s.origin, s.value) for s in eff.strategies] == [
        ("source", "src_v1"), ("experience", "v2")], \
        "promote 后 source 主策略仍在链上（9.2 原文）"


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
