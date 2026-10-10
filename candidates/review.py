"""review.py — 统一候选审核（设计 6.1 / F6；Task 3.5 / P3-13）。

F6 的原文：Candidate Test / Bug Candidate / Discovery Event 的「AI 提议 → 人工
Accept/Reject → 进入正式资产」遵循与 P2 `recovery_reviews` / `promotion_proposals`
**相同的模式**（状态机、retention、CLI 范式）。本模块是**首个消费者**——M4 的
Discovery Event 与 M5 的 Bug Candidate 复用同一入口（`kind` 判别，本任务只接
`"test"`）。

## 与 P2 两处先例的关系（两处都照，但照的是不同的部分）

| 照谁 | 照什么 | 为什么 |
|---|---|---|
| `agent/review.py::decide_review` | **状态机与报错文案**：PENDING→ACCEPT/REJECT；REJECT 必须 note；已决不可二次决策；`kind`/动词/`--note` 命名对齐 | F6 的「范式一致」是**给人记忆用的**——同一个 `mta` 下两套审核动词会让人猜 |
| `experience/promoter.py::approve_proposal` | **落盘的顺序**：校验（零副作用）→ 写文件 + git commit（失败回滚文件）→ 改状态 | 本处的副作用是 **git commit，不可幂等重放**（`decide_review` 的副作用是建 Experience，可重放，所以它先改状态；见下面「顺序」段） |

## 顺序：为什么**不**照 `decide_review` 的「先改状态、后副作用」

`decide_review` 的顺序是「校验 → 改状态 → 建 Candidate」——它的副作用可幂等重放，
所以先改状态是安全的（重试会命中 `find_by_seed_review`）。

本处的副作用是 **写 `suites/<suite>/<id>.yaml` + `git commit`**，不可幂等重放
（重试会撞上「同 id 文件已存在」→ fail-loud）。若先改状态后落盘，落盘失败时
候选已是 ACCEPT 且「不可二次决策」→ **永远卡死**（修复后重试被自己的状态挡住）。
故顺序照 `approve_proposal:215-298`：**全部校验 → 落盘（失败回滚）→ 改状态**。
失败时候选保持 PENDING，修复 git 后重试即续传。

## 依赖方向

`candidates → agents`（单向，不 import `cli`）：`existing_case_ids` 由调用方
（CLI 用 `pipeline.discover()`）现算传入——本模块不碰用例发现，也不碰 sqlite
（写 SQL 的唯一入口是 `agents/storage.py`）。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from candidates.test_case import dump_testcase_yaml
from source.vcs import run_git
from testcase.lint import Severity, lint, max_severity
from testcase.schema import parse_testcase_dict

__all__ = ["CandidateReviewError", "decide_candidate", "export_candidate_patch"]

# 本任务只接 test；M4（Discovery Event）/ M5（Bug Candidate）扩这个集合。
_SUPPORTED_KINDS = ("test",)


class CandidateReviewError(RuntimeError):
    """审核的域错误（决策非法 / 候选不存在 / 已决 / 落盘前置不满足）。

    刻意继承 `RuntimeError`：与 `agent/review.py::ReviewError` 同款——CLI 的
    `except` 只需接一个基类（`cmd_candidate` 逐条对应 `cmd_review` 的错误面）。
    """


def _require_kind(kind: str) -> None:
    if kind not in _SUPPORTED_KINDS:
        raise CandidateReviewError(
            f"未知的候选类型 {kind!r}——本任务只接 "
            f"{'/'.join(_SUPPORTED_KINDS)}（Discovery/Bug 候选归 M4/M5 接入）")


def decide_candidate(store, kind: str, candidate_id: str, decision: str,
                     reviewer: str, note: str | None = None, *,
                     repo_root: str | Path,
                     suites_root: str | Path = "suites",
                     existing_case_ids: frozenset[str] = frozenset(),
                     repository, secrets) -> tuple[dict, str | None]:
    """PENDING → ACCEPT/REJECT（F6 首个消费者）。返回 `(候选行, commit sha)`。

    REJECT 时 sha 为 `None`（不落盘）。ACCEPT 时 sha 是落盘那一次 commit。

    退出码/异常归 CLI：本函数只抛 `CandidateReviewError`（域错误）与
    `RuntimeError`（git 失败已回滚）——两者 CLI 都映成 exit 3。
    """
    _require_kind(kind)
    if decision not in ("ACCEPT", "REJECT"):
        raise CandidateReviewError(f"invalid decision: {decision!r}")
    if decision == "REJECT" and not (note or "").strip():
        # 拒绝没有理由 = triage 数据断流（与 `decide_review:176-178` 逐字同款）
        raise CandidateReviewError("reject 需要 --note（拒绝理由必须留痕）")

    row = store.get_candidate(candidate_id)
    if row is None:
        raise CandidateReviewError(f"候选 {candidate_id} 不存在")
    if row["review_status"] != "PENDING":
        # 与 `decide_review` 同款：**没有幂等分支**，重复决策 fail-loud
        raise CandidateReviewError(
            f"候选 {candidate_id} 已是 {row['review_status']}（不可二次决策）")

    if decision == "REJECT":
        store.decide_candidate(candidate_id, review_status="REJECT",
                               reviewer=reviewer, note=note)
        decided = store.get_candidate(candidate_id)
        assert decided is not None
        return decided, None

    # --- ACCEPT：全部校验（零副作用）→ 落盘（失败回滚）→ 改状态 -------------
    tc = _validated_testcase(row, repository=repository, secrets=secrets,
                             existing_case_ids=existing_case_ids,
                             suites_root=suites_root)
    target = Path(suites_root) / tc.suite / f"{tc.id}.yaml"
    try:
        rel = target.relative_to(Path(repo_root)).as_posix()
    except ValueError:
        # `git add` 只吃**仓库内**的路径（promoter 同款：`target.relative_to`）。
        # 落盘目录在 git 根之外 → 提交不可能成功，前置就 fail-loud（不先写
        # 文件再让 git 报错——那是「写了一半」的形态）。
        raise CandidateReviewError(
            f"落盘目录 {target} 不在 git 仓库根 {repo_root} 之内——"
            f"`--suites-root` 与 `--repo-root` 必须同源（promoter 的 E10 前提："
            f"commit 落当前分支）") from None
    original = target.read_text(encoding="utf-8") if target.exists() else None
    if original is not None:
        # 上一步的 id 冲突校验已挡「同 id 的正式用例」；走到这里说明文件存在
        # 但 id 不在 `existing_case_ids`（调用方没发现它）——仍然 fail-loud：
        # 静默覆盖人工文件是本仓最防的形态。
        raise CandidateReviewError(
            f"目标文件已存在（{rel}）——落盘不覆盖人工文件；"
            f"若确认要替换，先人工删除/改名，或换 candidate 的 id")

    target.parent.mkdir(parents=True, exist_ok=True)
    # 写文件与 git commit 之间没有事务性（promoter:264-284 的同款处理）：git
    # 失败时回滚文件写——否则重试会被自己写了一半的文件挡死。候选保持 PENDING。
    try:
        target.write_text(dump_testcase_yaml(tc), encoding="utf-8")
        run_git(repo_root, "add", rel)
        run_git(repo_root, "commit", "-m",
                f"promote: candidate {tc.id}（suite={tc.suite}，"
                f"reviewer={reviewer}）\n\n"
                f"origin: LLM 生成候选经人工审核 ACCEPT（P3-13 / F6）；"
                f"撤销 = git revert <sha>")
    except Exception as e:  # noqa: BLE001 — git 失败（GitError）与 IO 失败同一处置
        if original is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(original, encoding="utf-8")
        raise RuntimeError(
            f"落盘失败，{rel} 的写入已回滚（候选仍为 PENDING，"
            f"修复后重试 accept 即续传）：{e}") from e
    sha = run_git(repo_root, "rev-parse", "HEAD").strip()

    store.decide_candidate(candidate_id, review_status="ACCEPT",
                           reviewer=reviewer, note=note)
    decided = store.get_candidate(candidate_id)
    assert decided is not None
    return decided, sha


def _validated_testcase(row: dict, *, repository, secrets,
                        existing_case_ids: frozenset[str],
                        suites_root: str | Path):
    """ACCEPT 的全部前置（**零副作用**），任一不过即 `CandidateReviewError`。

    四条：① `testcase_yaml` 可解析（F5 的同一 Schema）；② `suite` 已声明
    （落盘目录无从确定）；③ 过 `mta lint`（落盘的文件必须能进 P1 流程——
    设计 6.1「不是新路径」）；④ id 不与正式用例冲突（**fail-loud**，与
    promoter 的「同 id 并入」不同：那是它的领域主流程，候选落盘是新文件）。
    """
    try:
        tc = parse_testcase_dict(yaml.safe_load(row["testcase_yaml"]))
    except Exception as e:  # noqa: BLE001 — 解析失败的各种形态都归「前置不过」
        raise CandidateReviewError(
            f"候选 {row['candidate_id']} 的 testcase_yaml 解析失败"
            f"（F5 的 P1 Schema）：{type(e).__name__}: {e}") from e
    if not tc.suite:
        # 落盘目录 = suites/<suite>/；没声明就无处可写。CLI 在**生成时**给
        # --suite，审核时不许事后补（补了库与文件就分叉——AE 同族）。
        raise CandidateReviewError(
            f"候选 {tc.id} 未声明 suite——落盘目录无从确定；"
            f"生成时用 `--suite` 指定，审核期不许补（库与文件会分叉）")
    if tc.id in existing_case_ids:
        raise CandidateReviewError(
            f"id {tc.id} 与正式用例冲突——落盘会遮蔽既有用例（fail-loud，"
            f"不静默覆盖；换一个 candidate id 或先处理既有用例）")
    issues = lint([tc], repository, secrets)
    if max_severity(issues) is Severity.ERROR:
        detail = "；".join(f"{i.code}: {i.message}" for i in issues
                          if i.severity is Severity.ERROR)
        raise CandidateReviewError(
            f"候选 {tc.id} 未过 lint（ERROR）——落盘的文件必须能进 P1 流程："
            f"{detail}")
    return tc


def export_candidate_patch(store, kind: str, candidate_id: str, *,
                           suites_root: str | Path = "suites") -> str:
    """ACCEPT 将要写的**文本**（H15 备选：`--export-patch` 只导出、不写盘）。

    返回「建议路径 + YAML 正文」——给「完全人工落盘」的团队习惯留一条路。
    **零写入**（不碰 git、不碰文件系统）。
    """
    _require_kind(kind)
    row = store.get_candidate(candidate_id)
    if row is None:
        raise CandidateReviewError(f"候选 {candidate_id} 不存在")
    tc = parse_testcase_dict(yaml.safe_load(row["testcase_yaml"]))
    target = Path(suites_root) / (tc.suite or "<suite>") / f"{tc.id}.yaml"
    return (f"# 建议落盘路径: {target.as_posix()}\n"
            f"# 应用: 写入该文件后 git add/commit（工具不代劳，H15）\n"
            + dump_testcase_yaml(tc))
