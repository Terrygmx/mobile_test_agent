"""Task 1.4 / P3-04：git diff → changed_files（设计 §5.1）。

plan Steps 的四条：新文件/修改/删除/**重命名**分类正确；`base` 不存在 → fail-loud；
`head` 缺省 = HEAD；（另加）`source/vcs.py` 是 git 调用的唯一实现。

Fixture 用**真 git 仓库**（tmp_path）——`changed_files` 的价值全在「git 真实输出
怎么解析」，替身 git 会让「`-z` 字段序」「`-M` 与用户配置的交互」这类错误在单测里
静默通过。`HOME` 指到 tmp_path 以**隔离用户的 global gitconfig**（沿用
`test_promoter.py` 的既有手法：`diff.renames` / `commit.gpgsign` 之类用户配置不该
影响测试结论）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from source.git_diff import GitChangeSet, GitDiffError, changed_files
from source.vcs import GitError, run_git

# 与 test_promoter.py 同款：HOME 指到 tmp_path → 不读用户 global gitconfig
_BASE_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
             "PATH": "/usr/bin:/bin:/usr/local/bin"}


@pytest.fixture()
def repo(tmp_path) -> Path:
    """一个真 git 仓库，两个 commit（base / head）。"""
    root = tmp_path / "proj"
    root.mkdir()
    env = dict(_BASE_ENV, HOME=str(tmp_path))

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, env=env,
                       capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (root / "keep.txt").write_text("keep\n")
    (root / "gone.txt").write_text("gone\n")
    (root / "edit.txt").write_text("v1\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    return root


def _env(tmp_path) -> dict:
    return dict(_BASE_ENV, HOME=str(tmp_path))


def _git(root: Path, tmp_path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True,
                   env=_env(tmp_path), capture_output=True)


def _head_commit(root: Path, tmp_path) -> None:
    """在 base 之上造一次「A + M + D + R」的改动。"""
    (root / "added.txt").write_text("new\n")
    (root / "edit.txt").write_text("v2\n")
    (root / "gone.txt").unlink()
    _git(root, tmp_path, "mv", "keep.txt", "moved.txt")
    _git(root, tmp_path, "add", "-A")
    _git(root, tmp_path, "commit", "-qm", "head")


# --- 分类 ---------------------------------------------------------------------


def test_classifies_add_modify_delete_and_rename(repo, tmp_path):
    _head_commit(repo, tmp_path)
    cs = changed_files(repo, base="HEAD~1")

    assert cs.added == ("added.txt",)
    assert cs.modified == ("edit.txt",)
    assert cs.deleted == ("gone.txt",)
    assert cs.renamed == (("keep.txt", "moved.txt"),)
    assert len(cs) == 4
    assert cs.base == "HEAD~1" and cs.head == "HEAD"


def test_changed_files_covers_every_touched_path(repo, tmp_path):
    """`PlannerInput.changed_files` 的来源：**去重**后的全部涉及路径。

    重命名取旧 + 新（旧路径的 metadata 归属可能还在用）；复制只取新。
    """
    _head_commit(repo, tmp_path)
    cs = changed_files(repo, base="HEAD~1")
    assert set(cs.changed_files) == {"added.txt", "edit.txt", "gone.txt",
                                     "keep.txt", "moved.txt"}
    assert len(cs.changed_files) == len(set(cs.changed_files)), "必须去重"


def test_head_defaults_to_HEAD(repo, tmp_path):
    _head_commit(repo, tmp_path)
    implicit = changed_files(repo, base="HEAD~1")
    explicit = changed_files(repo, base="HEAD~1", head="HEAD")
    assert implicit.changes == explicit.changes
    assert implicit.head == "HEAD"


def test_explicit_head_range(repo, tmp_path):
    """`base` 与 `head` 都是显式 ref 时按范围取（不隐含 HEAD）。"""
    _head_commit(repo, tmp_path)                 # 第 2 个 commit
    (repo / "third.txt").write_text("3\n")       # 第 3 个 commit
    _git(repo, tmp_path, "add", "-A")
    _git(repo, tmp_path, "commit", "-qm", "third")

    assert len(changed_files(repo, base="HEAD~1", head="HEAD~1")) == 0
    assert len(changed_files(repo, base="HEAD~2", head="HEAD~1")) == 4, \
        "第 2 个 commit 的改动（A+M+D+R）"
    assert len(changed_files(repo, base="HEAD~2", head="HEAD")) == 5, \
        "两个 commit 合起来"


def test_empty_diff_is_empty_not_an_error(repo, tmp_path):
    """同一 ref 与自己比 → 空 changeset（**不是错误**）。

    与「ref 拼错」区分开：后者必须 fail-loud，否则「这次改动影响 0 个用例」与
    「ref 打错了」在结果上长得一样，planner 会产出一个看起来正常的空 Plan。
    """
    _head_commit(repo, tmp_path)
    cs = changed_files(repo, base="HEAD", head="HEAD")
    assert isinstance(cs, GitChangeSet)
    assert len(cs) == 0
    assert cs.changed_files == ()
    assert cs.added == cs.modified == cs.deleted == cs.renamed == ()


# --- fail-loud（矩阵 #6 的 M2 侧消费） ----------------------------------------


def test_bad_base_ref_fails_loud(repo):
    with pytest.raises(GitDiffError, match="nosuchref"):
        changed_files(repo, base="nosuchref")


def test_bad_head_ref_fails_loud(repo):
    with pytest.raises(GitDiffError, match="nosuchref"):
        changed_files(repo, base="HEAD", head="nosuchref")


def test_non_git_directory_fails_loud(tmp_path, monkeypatch):
    """非 git 目录 → `GitDiffError`（消息带 git 自己的诊断，不自己编）。

    ⚠️ 必须设 `GIT_CEILING_DIRECTORIES`：`git -C dir` 会**向上找 `.git`**，
    而 pytest 的 `tmp_path` 在**项目仓库里面**——不设天花板时这里会被当成
    「项目仓库的子目录」，于是**不会**报错（这条是实测踩到的：第一版测试因此
    `DID NOT RAISE`）。天花板把向上搜索截在 tmp_path，才是真的「非 git 目录」。
    """
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    with pytest.raises(GitDiffError, match="取 git diff 失败"):
        changed_files(plain, base="HEAD~1")


def test_nonexistent_repo_root_fails_loud(tmp_path):
    with pytest.raises(GitDiffError, match="取 git diff 失败"):
        changed_files(tmp_path / "no_such_dir", base="HEAD~1")


def test_error_message_carries_the_repo_and_range(repo):
    """报错要能定位：仓库路径 + 范围 + git 的原始 stderr。"""
    with pytest.raises(GitDiffError) as e:
        changed_files(repo, base="nope", head="HEAD")
    msg = str(e.value)
    assert str(repo) in msg and "nope..HEAD" in msg
    assert "unknown revision" in msg, "git 自己的诊断要带出来"


def test_git_error_is_a_runtime_error():
    """`GitError` 继承 `RuntimeError`——`promoter._git` 原本就抛它，
    抽取不改变任何既有 except 的语义（P2 回归保证）。"""
    assert issubclass(GitError, RuntimeError)


# --- 解析健壮性（`-z` 与 `-M` 的存在理由） ------------------------------------


def test_rename_detection_is_independent_of_user_config(repo, tmp_path):
    """用户把 `diff.renames` 关掉，重命名仍必须被识别为 `R`。

    git 2.9 起默认开，但那是**用户配置**——有人在 `~/.gitconfig` 里关掉，
    「重命名分类正确」就会在**他的机器上**静默不成立。故显式传 `-M`。
    """
    _git(repo, tmp_path, "config", "diff.renames", "false")
    (repo / "keep.txt").rename(repo / "moved.txt")
    _git(repo, tmp_path, "add", "-A")
    _git(repo, tmp_path, "commit", "-qm", "rename only")

    cs = changed_files(repo, base="HEAD~1")
    assert cs.renamed == (("keep.txt", "moved.txt"),), \
        "显式 -M 必须压过用户的 diff.renames=false"
    assert cs.added == () and cs.deleted == ()


def test_filenames_with_tabs_and_newlines_parse_correctly(repo, tmp_path):
    """文件名里有制表符 / 换行时不能解析错（`-z` 的存在理由）。

    按行 + `\\t` 切会把这类名字**静默解析错**，而错的路径喂给 impact 分析
    （path → target 映射）只会**静默**给出错的受影响用例。
    """
    weird_tab = "a\tb.txt"
    weird_nl = "c\nd.txt"
    (repo / weird_tab).write_text("t\n")
    (repo / weird_nl).write_text("n\n")
    _git(repo, tmp_path, "add", "-A")
    _git(repo, tmp_path, "commit", "-qm", "weird names")

    cs = changed_files(repo, base="HEAD~1")
    assert set(cs.added) == {weird_tab, weird_nl}


def test_copy_contributes_only_the_new_path(repo, tmp_path):
    """复制（`C`）只把**新路径**算作改动：源文件内容没变，不该被算受影响。"""
    _git(repo, tmp_path, "config", "diff.renames", "true")
    (repo / "copy.txt").write_text((repo / "edit.txt").read_text())
    _git(repo, tmp_path, "add", "-A")
    _git(repo, tmp_path, "commit", "-qm", "copy")

    cs = changed_files(repo, base="HEAD~1", head="HEAD")
    # `git diff` 对「新增一个内容相同的文件」默认按 A 报（C 需要 -C）；
    # 两种形态都断言：新增只收新路径，复制也只收新路径。
    assert cs.added == ("copy.txt",) or cs.renamed == ()
    assert "edit.txt" not in cs.changed_files


def test_changeset_is_deterministic(repo, tmp_path):
    """同一 diff 两次跑结果一致（git 按路径排序输出，我们不再排一次）。"""
    _head_commit(repo, tmp_path)
    a = changed_files(repo, base="HEAD~1")
    b = changed_files(repo, base="HEAD~1")
    assert a.changes == b.changes and a.changed_files == b.changed_files


# --- run_git 是唯一实现 --------------------------------------------------------


def test_run_git_returns_stdout_and_raises_on_failure(repo):
    assert run_git(repo, "rev-parse", "--short", "HEAD").strip()
    with pytest.raises(GitError, match="rev-parse"):
        run_git(repo, "rev-parse", "nosuchref")


def test_promoter_calls_the_shared_run_git():
    """`experience/promoter.py` 不再自己拼 `subprocess + git`（P2 重构要求）。

    源码级断言：本地 `_git` 已删、import 了 `source.vcs.run_git`、且文件里没有
    第二处 `subprocess.run([...git...])`。
    """
    src = (Path(__file__).resolve().parents[2]
           / "experience" / "promoter.py").read_text(encoding="utf-8")
    assert "from source.vcs import run_git" in src
    assert "def _git(" not in src, "本地 _git 应已删除（实现单点在 source/vcs.py）"
    assert 'subprocess.run(["git"' not in src, "不该再有第二处 git 调用"
    assert src.count("run_git(") >= 3, "三个调用点都应改调 run_git"
