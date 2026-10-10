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

import sqlite3
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
    # 落盘内容与库**同源**：落盘 = `dump(parse(库文本))`。夹具的库文本本就是
    # 规范 dump，故此处逐字节相等；**非规范文本**（flow-style / 带注释）会被
    # 规范化重排——那条关系由下一条测试钉住（review_p3_task35 P3-9）。
    assert target.read_text() == store.get_candidate(cid)["testcase_yaml"]


def test_landed_file_is_the_normalized_form_of_non_canonical_db_text(
        store, repo_root, repository):
    """库文本**非规范**（flow-style + 注释）时，落盘仍是**可被 P1 读回**的规范形态。

    早先的注释把这层关系说成「落盘内容 = 库里的文本」（恒等）——只有在库文本
    恰好是规范 dump 时成立。真实关系是「落盘 = dump(parse(库文本))」，故这里
    故意塞一份非规范文本，断言落盘 ≠ 库文本、但**语义等价**（parse 回来逐字段
    相同）。E10 的 git diff 可读性也依赖落盘走规范化（人工改过的库文本不会
    把 diff 搅乱）。
    """
    cid = "cand_flow"
    flow = ('schema_version: "0.2"\nid: cand_flow\nname: 搜索 # 人工备注\n'
            'suite: smoke\nstatus: CANDIDATE\ngenerated_by: generator_agent\n'
            'generation_evidence: {coverage_gap: [], bug_history: [], '
            'source_refs: []}\n'
            'steps: [{action: tap, target: SearchView.submit_button}, '
            '{wait_for: {target: "screen:SearchView", condition: active, '
            'timeout: 10}}]\n')
    store.save_candidate(cid, testcase_yaml=flow,
                         generation_evidence={"coverage_gap": [],
                                              "bug_history": [],
                                              "source_refs": []},
                         fingerprint="fp_flow")
    decide_candidate(store, "test", cid, "ACCEPT", "op",
                     repo_root=repo_root, suites_root=repo_root / "suites",
                     repository=repository, secrets=EnvSecretProvider())
    landed = (repo_root / "suites" / "smoke" / f"{cid}.yaml").read_text()
    assert landed != flow, "落盘走规范化重排（不是库文本的逐字节拷贝）"
    assert parse_testcase_dict(yaml.safe_load(landed)).model_dump() == \
        parse_testcase_dict(yaml.safe_load(flow)).model_dump(), \
        "但语义必须等价（P1 读回同一份用例）"


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


def test_write_failure_after_commit_reverts_it_and_keeps_pending(
        store, repo_root, repository, monkeypatch):
    """**反方向的卡死窗口**（review_p3_task35 P2-1）：commit 成功、写库失败。

    早先这里没有处置——commit 已落、库还是 PENDING，重试被「目标文件已存在」
    永久挡死（正是本模块选落盘顺序时想避开的那种卡死，只是方向相反）。现在
    自动 `git revert` 那个 commit：历史留痕、工作区干净、候选仍 PENDING，
    重试即续传。
    """
    cid = _seed(store)
    real = store.decide_candidate

    def boom(*a, **kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "decide_candidate", boom)
    with pytest.raises(RuntimeError, match="git revert"):
        _accept(store, repo_root, repository, cid)
    monkeypatch.setattr(store, "decide_candidate", real)
    # 候选没被记账（写库失败）→ 仍 PENDING
    assert store.get_candidate(cid)["review_status"] == "PENDING"
    # 工作区干净（落盘被撤销）→ 重试不会被「文件已存在」挡住
    assert not (repo_root / "suites" / "smoke" / "cand_search_001.yaml").exists()
    # 重试成功（续传语义成立）
    row, sha = _accept(store, repo_root, repository, cid)
    assert row["review_status"] == "ACCEPT" and sha


def test_revert_failure_is_reported_with_the_commit_sha(
        store, repo_root, repository, monkeypatch):
    """连 `git revert` 都失败时：异常必须**带上 sha**（人工接手的第一手信息）。

    静默吞掉会让「库 PENDING + 文件已提交」这个最坏形态失去线索。
    """
    cid = _seed(store)
    monkeypatch.setattr(store, "decide_candidate",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            sqlite3.OperationalError("disk I/O error")))

    import candidates.review as mod
    from source.vcs import run_git as real_run_git

    def flaky_git(root, *args):
        if args and args[0] == "revert":
            raise RuntimeError("revert 撞上冲突")
        return real_run_git(root, *args)

    monkeypatch.setattr(mod, "run_git", flaky_git)
    with pytest.raises(RuntimeError) as e:
        _accept(store, repo_root, repository, cid)
    msg = str(e.value)
    assert "已提交" in msg and "revert" in msg and "人工处理" in msg, msg
    # 如实报告：库仍 PENDING，文件仍在（撤销失败 = 最坏形态，线索齐全）
    assert store.get_candidate(cid)["review_status"] == "PENDING"


def test_save_candidate_has_no_review_status_parameter(store):
    """P2-2：`save_candidate` **不再收** `review_status` —— 「唯一写路径」名实相符。

    早先它接受并写 `review_status`，于是能产出「ACCEPT 但 reviewer/reviewed_at
    全 NULL」的行（看起来像已审核，实则无人审核），且绕过了 `decide_candidate`
    的全部领域校验。删形参而不是加断言：断言仍留一个可绕过的入口，而删掉之后
    调用方会以 `TypeError` fail-loud。
    """
    with pytest.raises(TypeError, match="review_status"):
        store.save_candidate("cand_1", testcase_yaml="y",
                             generation_evidence={}, fingerprint="fp",
                             review_status="ACCEPT")
    # 正常入库仍是 PENDING（字面量，不由调用方给）
    assert store.save_candidate(
        "cand_2", testcase_yaml="y", generation_evidence={},
        fingerprint="fp")["review_status"] == "PENDING"


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
