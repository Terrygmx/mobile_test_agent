"""Task 3.5（P3-13）：统一候选审核（F6 首个消费者）——状态机 + ACCEPT 落盘。

钉四块：

1. **状态机与 P2 对齐**（F6）：PENDING→ACCEPT/REJECT、REJECT 必须 note、
   已决不可二次决策；`REVIEW_STATUSES` 与 `tracer/storage.py` 逐值相等。
2. **ACCEPT 落盘**（promoter 模式）：写 `suites/<suite>/<id>.yaml` + git commit
   + 库状态 ACCEPT；失败回滚且候选保持 PENDING。
3. **落盘前置 fail-loud**：未声明 suite / id 冲突 / lint ERROR / 目标文件已存在。
4. **H15 备选**：`export_candidate_patch` 零写入。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from agents.storage import SQLiteAgentStore
from candidates.review import (CandidateReviewError, decide_candidate,
                               export_candidate_patch)
from candidates.test_case import dump_testcase_yaml
from environment.secrets import EnvSecretProvider
from repository.resolver import Repository
from testcase.schema import parse_testcase_dict

# 一个最小可解析的正式元素/屏幕 repository（lint 要能解析 target）。
# 形态逐字照 `repository/generated/local/` 的真文件（`strategies` 不是
# `locators`，screen 要带 `marker`——手写夹具漂移过一次，lint 立刻报
# `unknown_target`，正好说明这道前置是真的在跑）。
_ELEMENT = """schema_version: '1.0'
kind: element
id: submit_button
screen: SearchView
type: button
strategies:
- type: accessibility_id
  value: submit
  origin: source
  source_file: Demo.swift
  source_line: 1
metadata:
  origin: source
"""

_SCREEN = """schema_version: '1.0'
kind: screen
id: SearchView
marker: screen.SearchView
kind_hint: page
metadata:
  risk: LOW
  origin: source
"""


@pytest.fixture()
def repo_root(tmp_path) -> Path:
    """真 git 仓库（ACCEPT 要落 commit；撤销靠 revert——E10 同款前提）。"""
    root = tmp_path / "proj"
    (root / "suites" / "smoke").mkdir(parents=True)
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
def repository(tmp_path) -> Repository:
    gen = tmp_path / "generated"
    (gen / "local" / "elements").mkdir(parents=True)
    (gen / "local" / "screens").mkdir(parents=True)
    (gen / "local" / "elements" / "SearchView.yaml").write_text(_ELEMENT)
    (gen / "local" / "screens" / "SearchView.yaml").write_text(_SCREEN)
    return Repository.from_dirs(generated_root=gen / "local",
                                overrides_root=None)


def _tc_yaml(*, tc_id="cand_search_001", suite="smoke", steps=None) -> str:
    tc = parse_testcase_dict({
        "schema_version": "0.2", "id": tc_id, "name": "搜索候选",
        "suite": suite, "status": "CANDIDATE",
        "generated_by": "generator_agent",
        "generation_evidence": {"coverage_gap": ["SearchView -> DetailView"],
                                "bug_history": [], "source_refs": []},
        "steps": steps if steps is not None else [
            {"action": "tap", "target": "SearchView.submit_button"},
            {"wait_for": {"target": "screen:SearchView", "condition": "active",
                          "timeout": 10}}]})
    return dump_testcase_yaml(tc)


@pytest.fixture()
def store(tmp_path) -> SQLiteAgentStore:
    return SQLiteAgentStore(tmp_path / "agent.db")


def _seed(store, *, tc_id="cand_search_001", suite="smoke", **over) -> str:
    kw = {"testcase_yaml": _tc_yaml(tc_id=tc_id, suite=suite),
          "generation_evidence": {"coverage_gap": [], "bug_history": [],
                                  "source_refs": []},
          "fingerprint": "fp_" + tc_id, "dry_run_status": "PASS"}
    kw.update(over)
    store.save_candidate(tc_id, **kw)
    return tc_id


def _accept(store, repo_root, repository, cid, **over):
    kw = dict(kind="test", candidate_id=cid, decision="ACCEPT", reviewer="op",
              repo_root=repo_root, suites_root=repo_root / "suites",
              existing_case_ids=frozenset(), repository=repository,
              secrets=EnvSecretProvider())
    kw.update(over)
    return decide_candidate(store, **kw)


# --- 1. 状态机（F6 与 P2 对齐） -----------------------------------------------


def test_review_statuses_match_p2(store):
    """F6：候选审核的状态词表与 P2 `recovery_reviews` **逐值相等**。

    两处各持一套词表就会漂移（`ACCEPTED` vs `ACCEPT` 这类）——F6 的「范式
    一致」正是要防它。与 SQL/Python 两处字面量的对账同一手法。
    """
    from candidates.test_case import REVIEW_STATUSES
    from tracer.storage import REVIEW_STATUSES as P2_STATUSES
    assert set(REVIEW_STATUSES) == set(P2_STATUSES)


def test_accept_writes_file_commits_and_marks_the_row(store, repo_root,
                                                      repository):
    """ACCEPT：文件 + commit + 库状态三处一致（缺一即「看起来正常」）。"""
    cid = _seed(store)
    row, sha = _accept(store, repo_root, repository, cid)
    target = repo_root / "suites" / "smoke" / "cand_search_001.yaml"
    assert target.exists(), "ACCEPT 必须落盘"
    assert sha, "ACCEPT 必须产出 commit sha"
    log = subprocess.run(["git", "-C", str(repo_root), "log", "--oneline"],
                         capture_output=True, text=True).stdout
    assert "cand_search_001" in log, "commit message 要点名候选 id"
    assert row["review_status"] == "ACCEPT" and row["reviewer"] == "op"
    assert row["reviewed_at"]
    # 落盘内容 = 库里的文本（唯一实现，不漂移）
    assert target.read_text() == store.get_candidate(cid)["testcase_yaml"]


def test_landed_file_roundtrips_through_p1_schema(store, repo_root, repository):
    """落盘的文件必须能被 P1 原样读回（设计 6.1「不是新路径」）。"""
    cid = _seed(store)
    _accept(store, repo_root, repository, cid)
    text = (repo_root / "suites" / "smoke" / "cand_search_001.yaml").read_text()
    tc = parse_testcase_dict(yaml.safe_load(text))
    assert tc.id == "cand_search_001" and tc.status == "CANDIDATE"
    assert tc.suite == "smoke"


def test_reject_requires_a_note(store, repo_root, repository):
    """REJECT 无 note（含纯空白）→ 拒绝执行（triage 数据不许断流）。"""
    cid = _seed(store)
    with pytest.raises(CandidateReviewError, match="note"):
        decide_candidate(store, "test", cid, "REJECT", "op",
                         repo_root=repo_root, suites_root=repo_root / "suites",
                         repository=repository, secrets=EnvSecretProvider())
    with pytest.raises(CandidateReviewError, match="note"):
        decide_candidate(store, "test", cid, "REJECT", "op", "   ",
                         repo_root=repo_root, suites_root=repo_root / "suites",
                         repository=repository, secrets=EnvSecretProvider())
    assert store.get_candidate(cid)["review_status"] == "PENDING", \
        "校验不过不许改状态（fail-loud 在状态变更之前）"


def test_reject_lands_the_note_and_no_file(store, repo_root, repository):
    cid = _seed(store)
    row, sha = decide_candidate(store, "test", cid, "REJECT", "op",
                                "与既有用例语义重复",
                                repo_root=repo_root,
                                suites_root=repo_root / "suites",
                                repository=repository,
                                secrets=EnvSecretProvider())
    assert sha is None and row["review_status"] == "REJECT"
    assert row["review_note"] == "与既有用例语义重复"
    assert not (repo_root / "suites" / "smoke" / "cand_search_001.yaml").exists()


def test_second_decision_is_refused(store, repo_root, repository):
    """已决不可二次决策（与 `decide_review` 同款：**没有幂等分支**）。"""
    cid = _seed(store)
    _accept(store, repo_root, repository, cid)
    with pytest.raises(CandidateReviewError, match="不可二次决策"):
        decide_candidate(store, "test", cid, "REJECT", "op", "改主意了",
                         repo_root=repo_root, suites_root=repo_root / "suites",
                         repository=repository, secrets=EnvSecretProvider())


def test_unknown_decision_and_kind_are_refused(store, repo_root, repository):
    cid = _seed(store)
    with pytest.raises(CandidateReviewError, match="invalid decision"):
        decide_candidate(store, "test", cid, "MAYBE", "op",
                         repo_root=repo_root, suites_root=repo_root / "suites",
                         repository=repository, secrets=EnvSecretProvider())
    with pytest.raises(CandidateReviewError, match="候选类型"):
        decide_candidate(store, "discovery", cid, "ACCEPT", "op",
                         repo_root=repo_root, suites_root=repo_root / "suites",
                         repository=repository, secrets=EnvSecretProvider())


def test_missing_candidate_is_refused(store, repo_root, repository):
    with pytest.raises(CandidateReviewError, match="不存在"):
        _accept(store, repo_root, repository, "nope")


# --- 2. 落盘前置（零副作用，全在任何写之前） ---------------------------------


def test_accept_without_suite_is_refused(store, repo_root, repository):
    """未声明 suite → 落盘目录无从确定（不许审核期事后补——库与文件会分叉）。"""
    cid = _seed(store, tc_id="cand_x")
    # 手工把库里的 yaml 换成没有 suite 的
    no_suite = dump_testcase_yaml(parse_testcase_dict({
        "schema_version": "0.2", "id": "cand_x", "name": "x",
        "steps": [{"action": "launch_app"}]}))
    store.save_candidate("cand_x", testcase_yaml=no_suite,
                         generation_evidence={"coverage_gap": [],
                                              "bug_history": [],
                                              "source_refs": []},
                         fingerprint="fp_x")
    with pytest.raises(CandidateReviewError, match="suite"):
        _accept(store, repo_root, repository, "cand_x")
    assert store.get_candidate("cand_x")["review_status"] == "PENDING"


def test_accept_with_conflicting_id_is_refused(store, repo_root, repository):
    """id 与正式用例冲突 → fail-loud（不静默覆盖；与 promoter 的「并入」不同）。"""
    cid = _seed(store)
    with pytest.raises(CandidateReviewError, match="冲突"):
        _accept(store, repo_root, repository, cid,
                existing_case_ids=frozenset({"cand_search_001"}))
    assert not (repo_root / "suites" / "smoke" / "cand_search_001.yaml").exists()


def test_accept_with_lint_error_is_refused(store, repo_root, repository):
    """lint ERROR（target 引用不存在的元素）→ 落盘被拒。"""
    bad = dump_testcase_yaml(parse_testcase_dict({
        "schema_version": "0.2", "id": "cand_bad", "name": "x", "suite": "smoke",
        "status": "CANDIDATE", "generated_by": "generator_agent",
        "generation_evidence": {"coverage_gap": [], "bug_history": [],
                                "source_refs": []},
        "steps": [{"action": "tap", "target": "SearchView.no_such_element"}]}))
    store.save_candidate("cand_bad", testcase_yaml=bad,
                         generation_evidence={"coverage_gap": [],
                                              "bug_history": [],
                                              "source_refs": []},
                         fingerprint="fp_bad")
    with pytest.raises(CandidateReviewError, match="lint"):
        _accept(store, repo_root, repository, "cand_bad")
    assert store.get_candidate("cand_bad")["review_status"] == "PENDING"


def test_accept_refuses_to_overwrite_an_existing_file(store, repo_root,
                                                      repository):
    """目标文件已存在但 id 不在 `existing_case_ids`（调用方没发现它）→ 仍拒绝。"""
    cid = _seed(store)
    target = repo_root / "suites" / "smoke" / "cand_search_001.yaml"
    target.write_text("# 人工文件\n")
    with pytest.raises(CandidateReviewError, match="已存在"):
        _accept(store, repo_root, repository, cid)
    assert target.read_text() == "# 人工文件\n", "人工文件不许被覆盖"


def test_accept_refuses_a_suites_root_outside_the_git_root(store, tmp_path,
                                                           repository):
    """落盘目录在 git 根之外 → 前置 fail-loud（不先写文件再让 git 报错）。

    `git add` 只吃仓库内的路径（promoter 同款 `relative_to`）——先写文件再失败
    就是「写了一半」，正是回滚逻辑要处理的那种形态；能在前置挡掉就不该走到那。
    """
    cid = _seed(store)
    outside = tmp_path / "elsewhere" / "suites"
    outside.mkdir(parents=True)
    git_root = tmp_path / "onlygit"
    git_root.mkdir()
    with pytest.raises(CandidateReviewError, match="不在 git 仓库根"):
        _accept(store, git_root, repository, cid, suites_root=outside)
    assert not (outside / "smoke" / "cand_search_001.yaml").exists()
    assert store.get_candidate(cid)["review_status"] == "PENDING"


# --- 3. 落盘失败回滚（promoter 模式） -----------------------------------------


def test_git_failure_rolls_back_the_file_and_keeps_pending(store, tmp_path,
                                                           repository):
    """git 失败 → 文件回滚 + 候选保持 PENDING（修复后重试即续传）。

    用一个**不是 git 仓库**的目录当 `repo_root`：`run_git` 会抛 `GitError`。
    """
    cid = _seed(store)
    not_a_repo = tmp_path / "plain"
    (not_a_repo / "suites" / "smoke").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="回滚"):
        _accept(store, not_a_repo, repository, cid)
    assert not (not_a_repo / "suites" / "smoke"
                / "cand_search_001.yaml").exists(), "失败必须回滚文件写"
    assert store.get_candidate(cid)["review_status"] == "PENDING", \
        "落盘失败不许改状态（否则永远卡死）"


# --- 4. H15 备选：export 零写入 -----------------------------------------------


def test_export_patch_writes_nothing(store, repo_root, repository):
    cid = _seed(store)
    text = export_candidate_patch(store, "test", cid,
                                  suites_root=repo_root / "suites")
    assert "cand_search_001" in text and "smoke" in text
    assert not (repo_root / "suites" / "smoke" / "cand_search_001.yaml").exists()
    assert store.get_candidate(cid)["review_status"] == "PENDING"


def test_export_patch_for_missing_candidate_is_refused(store):
    with pytest.raises(CandidateReviewError, match="不存在"):
        export_candidate_patch(store, "test", "nope")
