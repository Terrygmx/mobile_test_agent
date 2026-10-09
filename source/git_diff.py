"""git_diff.py — `changed_files` 的数据源（设计 §5.1；Task 1.4 / P3-04）。

设计 §5.1 的 `PlannerInput.changed_files: list[str]` 是「git diff 结果」——
本模块就是它。**全仓此前没有任何 git diff 工具**（plan 第 0 节核实的差距）。

## 调用形状

```python
cs = changed_files(repo_root, base="1025", head=None)   # head=None → HEAD
cs.added / cs.modified / cs.deleted / cs.renamed        # 分类视图
cs.changed_files                                        # 去重后的全部涉及路径
```

## 三条设计决定（都影响下游的 impact 分析）

1. **`-z`（NUL 分隔）而不是按行解析**：文件名里可以有制表符与换行（git 允许），
   按行 + `\t` 切会把这类名字**静默解析错**——而错的路径喂给 impact 分析
   （`planner/impact.py` 的 path→target 映射）只会**静默**给出错的受影响用例。
   `-z` 让「解析」这一步没有歧义。
2. **显式传 `-M`（重命名检测）**：git 2.9 起 `diff.renames` 默认是 true，但那是
   **用户配置**——有人在 `~/.gitconfig` 里关掉，重命名就会变成 `A` + `D` 两行，
   「重命名分类正确」在**他的机器上**静默不成立。显式 `-M` 让结果与用户配置无关。
3. **`changed_files` 对重命名**取**旧 + 新两个路径**：impact 的映射是「路径 →
   受影响 target」，而重命名后 metadata 的文件归属可能还挂在旧路径上（未重新
   生成）、也可能已挂到新路径——**两边都收**是保守的（沿用 P2「匹配宁可多包含」
   的取向），只收一边会在另一边的 metadata 形态下漏掉影响面。复制（`C`）只收
   新路径（源文件内容没变，不该被算作受影响）。

## 失败语义（fail-loud，矩阵 #6 的 M2 侧消费）

非 git 目录 / 坏 ref（`base` 或 `head` 不存在）→ **`GitDiffError`**，消息里带 git
自己的 stderr（「unknown revision」「Not a git repository」都在那）。**不静默返回
空 changeset**——「这次改动影响 0 个用例」与「ref 拼错了」在结果上长得一模一样，
而前者会让 planner 产出一个看起来完全正常的空 Plan。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from source.vcs import GitError, run_git

__all__ = ["FileChange", "GitChangeSet", "GitDiffError", "changed_files"]

# `--name-status` 的状态字母 → 本模块的分类桶。
# 未列出的字母（`U` unmerged / `X` unknown / `B` broken pair）不丢：它们仍在
# `GitChangeSet.changes` 里，只是不落进四个分类视图（设计 §5.1 只要「改了哪些
# 文件」，分类是给人看的；把未知状态硬塞进某个桶反而是编造）。
#
# ⚠️ `C` 单独成常量：`_ADDED` 与 `_parse_name_status_z` 都要判它，写两处 `"C"`
# 就会漂移（review_p3_task14 小观察 2：「同一概念只许一处实现」）。
_COPIED = frozenset({"C"})
_ADDED = frozenset({"A"}) | _COPIED  # C（复制）也**新建了一个文件**
_MODIFIED = frozenset({"M", "T"})    # T = type change（普通文件 ↔ 符号链接）
_DELETED = frozenset({"D"})
_RENAMED = frozenset({"R"})


class GitDiffError(ValueError):
    """`changed_files` 不可用：不是 git 仓库 / ref 不存在 / 输出无法解析。

    继承 `ValueError`（与 `PolicyConfigError` / `RepositoryLoaderError` 同族）：
    它描述的是「**你给的仓库或 ref 用不了**」，是调用方要修的输入/环境问题。
    底层 `source.vcs.GitError`（`RuntimeError`）在这里被**翻译**成域错误——
    与 `experience/knowledge.py::_read_steps` 把裸 sqlite 异常翻译成 `ValueError`
    同一手法（review_p2_task55 P3-2）。
    """


@dataclass(frozen=True)
class FileChange:
    """一条改动。`status` 是**单字母**（`R100` 的相似度分数丢弃——没有消费者，
    不留投机字段）；重命名/复制的源路径在 `old_path`。"""

    status: str                    # A / M / D / R / C / T / …
    path: str                      # 当前路径（D 时是被删掉的那个）
    old_path: str | None = None    # 仅 R / C 有值

    @property
    def is_added(self) -> bool:
        return self.status in _ADDED

    @property
    def is_modified(self) -> bool:
        return self.status in _MODIFIED

    @property
    def is_deleted(self) -> bool:
        return self.status in _DELETED

    @property
    def is_renamed(self) -> bool:
        return self.status in _RENAMED


@dataclass(frozen=True)
class GitChangeSet:
    """一次 `base..head` 的改动集合。

    `changes` 保持 **git 的输出顺序**（git 按路径排序输出，所以同一 diff 两次
    跑结果一致——确定性由构造保证，不需要我们再排一次）。
    """

    repo_root: str
    base: str
    head: str
    changes: tuple[FileChange, ...]

    # --- 分类视图（路径，按 git 顺序） ---

    @property
    def added(self) -> tuple[str, ...]:
        return tuple(c.path for c in self.changes if c.is_added)

    @property
    def modified(self) -> tuple[str, ...]:
        return tuple(c.path for c in self.changes if c.is_modified)

    @property
    def deleted(self) -> tuple[str, ...]:
        return tuple(c.path for c in self.changes if c.is_deleted)

    @property
    def renamed(self) -> tuple[tuple[str, str], ...]:
        """`((旧路径, 新路径), ...)`——两个路径都要，见模块 docstring 决定 3。"""
        return tuple((c.old_path or "", c.path)
                     for c in self.changes if c.is_renamed)

    @property
    def changed_files(self) -> tuple[str, ...]:
        """`PlannerInput.changed_files` 的来源：**去重**后的全部涉及路径。

        重命名取旧 + 新（决定 3）；复制只取新（源文件没变）；其余取自身。
        去重保序（同一路径在 `changes` 里出现两次时只留第一次）。
        """
        out: list[str] = []
        for c in self.changes:
            for p in ((c.old_path, c.path) if c.is_renamed else (c.path,)):
                if p and p not in out:
                    out.append(p)
        return tuple(out)

    def __len__(self) -> int:
        return len(self.changes)


def _parse_name_status_z(raw: str, *, where: str) -> tuple[FileChange, ...]:
    """解析 `git diff --name-status -z` 的输出。

    形状（实测）：`M\\0path\\0` / `R100\\0old\\0new\\0`——状态与路径各占一个
    NUL 字段，**R/C 后面跟两个路径**。字段数对不上说明输出被截断（不该发生，
    发生了就 fail-loud，不猜）。
    """
    tokens = raw.split("\0")
    if tokens and tokens[-1] == "":
        tokens.pop()                     # 结尾的 NUL
    changes: list[FileChange] = []
    i = 0
    while i < len(tokens):
        status = tokens[i][:1]
        i += 1
        if status in _RENAMED or status in _COPIED:
            if i + 1 >= len(tokens):
                raise GitDiffError(
                    f"{where}: git 输出被截断（{status} 后面缺路径）：{raw!r}")
            old, new = tokens[i], tokens[i + 1]
            i += 2
            changes.append(FileChange(status=status, path=new, old_path=old))
        else:
            if i >= len(tokens):
                raise GitDiffError(
                    f"{where}: git 输出被截断（{status} 后面缺路径）：{raw!r}")
            changes.append(FileChange(status=status, path=tokens[i]))
            i += 1
    return tuple(changes)


def changed_files(repo_root: str | Path, base: str,
                  head: str | None = None) -> GitChangeSet:
    """`git diff --name-status -z -M base..head` → `GitChangeSet`。

    - `head=None` → `"HEAD"`（本次改动相对 `base` 的全部变化）；
    - 非 git 目录 / `base` 或 `head` 不是可解析的 ref → **`GitDiffError`**
      （消息带 git 的 stderr，不自己编诊断）。
    """
    resolved_head = head or "HEAD"
    where = f"{base}..{resolved_head}"
    try:
        raw = run_git(repo_root, "diff", "--name-status", "-z", "-M", where)
    except GitError as e:
        raise GitDiffError(
            f"取 git diff 失败（repo_root={repo_root!r}，范围 {where}）：{e}"
        ) from e
    return GitChangeSet(repo_root=str(repo_root), base=base,
                        head=resolved_head,
                        changes=_parse_name_status_z(raw, where=where))
