"""vcs.py — git 调用的**唯一实现**（Task 1.4 / P3-04）。

抽自 `experience/promoter.py::_git`（Task 1.4 的重构要求：promoter 改调本函数，
**行为与报错消息逐字不变**——那是 P2 的回归保证）。`source/git_diff.py` 也用它。

## 为什么要有这个模块

`git` 在本仓被调用的地方不止一处（`experience/promoter.py` 的 `add/commit/
rev-parse`、`source/metadata.py::_git_commit`、`tracer/recorder.py::_git_commit`），
每处各自拼 `subprocess.run(["git", ...])` 就会各自决定「失败怎么办」。这里把
「怎么调 + 失败怎么报」收敛成一处；**调用方决定失败语义**（`run_git` 一律抛
`GitError`，要「读不到就退回默认值」的调用方自己 catch——`_git_commit` 那种
`unknown` 兜底是**调用方的策略**，不是 git 层的）。

⚠️ **本次只收敛 promoter 那一条**（plan Task 1.4 的 Files 明确限定）：
`source/metadata.py` / `tracer/recorder.py` 的 `_git_commit` 是 P1 存量，且
它们的调用形态不同（不传 `-C`，靠进程 CWD；失败一律 `unknown`）——是否收敛
由后续任务显式决策，**不顺手改**（已登记在 `docs/p3_data_audit.md`）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

__all__ = ["GitError", "run_git"]


class GitError(RuntimeError):
    """git 命令非零退出（含「不是 git 仓库」「坏 ref」）。

    刻意继承 `RuntimeError`：`promoter._git` 原本就抛 `RuntimeError`，继承它
    让抽取**不改变任何既有 except 的语义**（P2 回归保证）。
    """


def run_git(repo_root: str | Path, *args: str) -> str:
    """在 `repo_root` 下跑一条 git 命令，返回 stdout；非零退出抛 `GitError`。

    用 `-C repo_root` 而不是依赖进程 CWD——调用方的 CWD 与目标仓库无关
    （`mta plan` 可能从任意目录跑）。报错消息里带 git 自己的 stderr（截断到
    300 字符）：坏 ref / 非 git 目录的诊断信息都在那里，不自己编一套。
    """
    result = subprocess.run(["git", "-C", str(repo_root), *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed: {result.stderr.strip()[:300]}")
    return result.stdout
