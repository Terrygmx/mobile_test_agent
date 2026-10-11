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

⚠️ **两个卡死窗口都要关掉**（review_p3_task35 P2-1）：上面的顺序关掉了
「改了状态但没落盘」；反方向的「**已落盘但没记账**」同样会卡死（重试被
「目标文件已存在」挡住），故 `commit` 之后的写库失败会**真回滚**——只追加
历史、不改写，与打印给用户的「撤销 = git revert <sha>」同一手法。两条路径都保持
「候选 PENDING + 工作区干净 → 重试即续传」。⚠️ 撤销本身有两条路（round 2 P2-C）：
`git revert` 在**脏暂存区**下会被 git 拒绝，故退路是只作用于自己路径的逆提交
（见 `_undo_promote`）——**两条路都不会碰别人的在途工作**。

## 依赖方向

`candidates → agents`（单向，不 import `cli`）：`existing_case_ids` 由调用方
（CLI 用 `pipeline.discover()`）现算传入——本模块不碰用例发现，也不碰 sqlite
（写 SQL 的唯一入口是 `agents/storage.py`）。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from candidates.discovery import graph_registration, register
from candidates.test_case import dump_testcase_yaml
from source.vcs import run_git
from testcase.lint import Severity, lint, max_severity
from testcase.schema import parse_testcase_dict

__all__ = ["CandidateReviewError", "decide_candidate", "export_candidate_patch"]

# Task 3.5 只接 test；Task 4.5 扩 discovery（M4）；Task 5.6 扩 bug（M5）。
_SUPPORTED_KINDS = ("test", "discovery")


class CandidateReviewError(RuntimeError):
    """审核的域错误（决策非法 / 候选不存在 / 已决 / 落盘前置不满足）。

    刻意继承 `RuntimeError`：与 `agent/review.py::ReviewError` 同款——CLI 的
    `except` 只需接一个基类（`cmd_candidate` 逐条对应 `cmd_review` 的错误面）。
    """


def _require_kind(kind: str) -> None:
    if kind not in _SUPPORTED_KINDS:
        raise CandidateReviewError(
            f"未知的候选类型 {kind!r}——目前只接 "
            f"{'/'.join(_SUPPORTED_KINDS)}（Bug 候选归 Task 5.6 接入）")


def decide_candidate(store, kind: str, candidate_id: str, decision: str,
                     reviewer: str, note: str | None = None, *,
                     repo_root: str | Path | None = None,
                     suites_root: str | Path = "suites",
                     existing_case_ids: frozenset[str] = frozenset(),
                     repository=None, secrets=None,
                     graph_store=None) -> tuple[dict, str | None]:
    """PENDING → ACCEPT/REJECT（F6 的统一入口）。返回 `(候选行, commit sha)`。

    ⚠️ **`repo_root` / `repository` / `secrets` 变成可选**（Task 4.5）：它们的
    消费者只有 `test` 分支（落盘 + lint + git commit）。`discovery` 分支的
    ACCEPT 副作用是**登记进 Source Graph**（`graph_store`），与 git / 用例
    发现无关——把「test 分支的输入」做成全局必填会逼 discovery 的调用方
    伪造一堆用不上的参数。**必填性没有消失，只是按 kind 分流**：
    `_validated_testcase` 在缺 `repo_root` 时照样 fail-loud（有测试钉住）。

    REJECT 时 sha 恒为 `None`（不落盘）。`test` 的 ACCEPT 时 sha 是落盘那次
    commit；`discovery` 的 ACCEPT 不碰 git，sha 也是 `None`。

    退出码/异常归 CLI：本函数只抛 `CandidateReviewError`（域错误）与
    `RuntimeError`（git 失败已回滚）——两者 CLI 都映成 exit 3。
    """
    _require_kind(kind)
    if decision not in ("ACCEPT", "REJECT"):
        raise CandidateReviewError(f"invalid decision: {decision!r}")
    if decision == "REJECT" and not (note or "").strip():
        # 拒绝没有理由 = triage 数据断流（与 `decide_review:176-178` 逐字同款）
        raise CandidateReviewError("reject 需要 --note（拒绝理由必须留痕）")

    if kind == "discovery":
        return _decide_discovery(store, candidate_id, decision, reviewer, note,
                                 graph_store=graph_store)

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
                             suites_root=suites_root, repo_root=repo_root)
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
    # ⚠️ 这里只会是**新建**文件（`original is not None` 已在上面前置抛掉），
    # 故回滚 = 删掉自己刚写的那个（review_p3_task35 P3-1：早先抄 promoter 的
    # 「已存在则恢复原内容」分支在前置一挡之下**永远轮不到**，是死代码）。
    try:
        target.write_text(dump_testcase_yaml(tc), encoding="utf-8")
        run_git(repo_root, "add", rel)
        # ⚠️ `commit` 必须带 **pathspec**（`-- rel`，review_p3_task35 round 2 P1-B）：
        # 不带路径的 `git commit` 提交的是**整个暂存区**——别人 `git add` 的在途
        # 工作会被连带提交，而本模块的报错文案只说「promote: candidate …」，
        # 用户无从得知自己的文件被捎走了。反向更重：DB 记账失败时的 `git revert`
        # 会**把别人的改动一起撤掉**（实测 `others_work.py` 被提交、revert 后文件
        # 消失且 `git status` 干净——连「有东西被删了」都不留痕）。
        # 上面的 `target.relative_to(repo_root)` 那道 fail-loud 早已说明作者知道
        # 「只该动自己的文件」，只是没在 commit 上贯彻。
        run_git(repo_root, "commit", "-m",
                f"promote: candidate {tc.id}（suite={tc.suite}，"
                f"reviewer={reviewer}）\n\n"
                f"origin: LLM 生成候选经人工审核 ACCEPT（P3-13 / F6）；"
                f"撤销 = git revert <sha>",
                "--", rel)
    except Exception as e:  # noqa: BLE001 — git 失败（GitError）与 IO 失败同一处置
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"落盘失败，{rel} 的写入已回滚（候选仍为 PENDING，"
            f"修复后重试 accept 即续传）：{e}") from e
    sha = run_git(repo_root, "rev-parse", "HEAD").strip()

    try:
        store.decide_candidate(candidate_id, review_status="ACCEPT",
                               reviewer=reviewer, note=note)
    except Exception as e:  # noqa: BLE001 — commit 已落，记账失败必须真回滚
        # ⚠️ **commit 与记账之间没有事务**（review_p3_task35 P2-1）：外部的
        # commit 已经落定，而库还是 PENDING——此时若什么都不做，重试会被
        # 「目标文件已存在」永久挡死（**反向的**同一个卡死窗口，正是本模块
        # 选这个顺序时想避开的那个）。故**真回滚**：只追加、不改写历史，
        # 与打印给用户的「撤销 = git revert <sha>」同一手法。
        undone, how = _undo_promote(repo_root, rel, sha)
        if not undone:
            raise RuntimeError(
                f"落盘已提交 {sha}，但库状态更新失败（{e}）；撤销也失败"
                f"——候选仍为 PENDING，需人工处理：先 `git rm {rel}` 并提交"
                f"（或 `git stash` 后 `git revert {sha}`），再重试 accept") from e
        raise RuntimeError(
            f"落盘已提交 {sha}，但库状态更新失败（{e}）——已撤销（{how}），"
            f"候选仍为 PENDING，修复后重试 accept 即续传") from e
    decided = store.get_candidate(candidate_id)
    assert decided is not None
    return decided, sha


def _decide_discovery(store, event_id: str, decision: str, reviewer: str,
                      note: str | None, *, graph_store=None
                      ) -> tuple[dict, None]:
    """`kind="discovery"` 的 ACCEPT/REJECT（设计 §7.5 / F6；Task 4.5）。

    与 test 分支的**形状差异**（不是「差不多」，是刻意不同）：

    | | test | discovery |
    |---|---|---|
    | ACCEPT 的副作用 | 写 `suites/*.yaml` + `git commit` | 登记进 Source Graph |
    | 需要 git / lint / 用例发现 | 是 | **否** |
    | sha | 落盘 commit | 恒 `None`（不碰 git） |
    | 顺序 | 校验 → 落盘 → 改状态（commit 不可幂等重放） | **改状态 → 登记图** |

    ⚠️ **顺序反过来了**，理由与 test 分支正好对称：test 的副作用（commit）不可
    幂等重放，所以先落盘后改状态（失败重试即续传）；discovery 的副作用
    （`register`）**幂等**（同一条已登记 → 返回 False 且不写库），所以先改状态
    是安全的——若先登记图再改状态，改状态失败会留下「图里有了、库里还是
    PENDING」的行，重试时 `register` 幂等返回 False 而状态照样推进，最终一致；
    反过来（先登记后改状态失败且 register 不幂等）才会真卡死。

    ⚠️ **REJECT 不动图**：拒绝的意义就是「不要把这个发现当成已知资产」——
    去改图反而是把它登记了。
    """
    row = store.get_discovery_event(event_id)
    if row is None:
        raise CandidateReviewError(f"发现事件 {event_id} 不存在")
    if row["review_status"] != "PENDING":
        raise CandidateReviewError(
            f"发现事件 {event_id} 已是 {row['review_status']}（不可二次决策）")

    if decision == "ACCEPT":
        if graph_store is None:
            # 前置在**改状态之前**（零副作用那一步）：ACCEPT 的副作用就是登记
            # 图，没有图库时先把状态改成 ACCEPT 会让重试撞上「不可二次决策」
            # ——与 test 分支「校验必须发生在状态变更之前」同一条纪律。
            raise CandidateReviewError(
                "discovery 的 ACCEPT 需要 graph_store（ACCEPT 的副作用就是"
                "把发现登记进 Source Graph）——CLI 用 `--graph-db` 提供")
        # 登记范围在**改状态之前**算好：`_graph_scope` 会在范围有歧义时
        # fail-loud，而那时状态还没改（重试即续传）。
        scope = _graph_scope(graph_store)

    store.decide_discovery_event(event_id, review_status=decision,
                                 reviewer=reviewer, note=note)
    if decision == "ACCEPT":
        register(graph_store, row, app_id=scope[0], app_build=scope[1])
    decided = store.get_discovery_event(event_id)
    assert decided is not None
    return decided, None


def _graph_scope(graph_store) -> tuple[str, str]:
    """登记的 `(app_id, app_build)`：取图库里**唯一**的范围。

    发现事件自己不携带 app 身份（它产生于一次探索任务，而任务的作用域就是
    图的作用域），所以范围只能从图库推：

    - 库里**没有**范围 → `("", "")`（全新库：第一条发现就是第一张声明面；
      `app_id`/`app_build` 允许为空串，001 的列定义如此）；
    - 库里**恰好一个**范围 → 用它（发现登记进既有那张声明面）；
    - 库里**多个** app_id 或多个 app_build → `CandidateReviewError`（fail-loud：
      猜一个会让发现登记到别的 app 上，而那是**静默写错库**）。
    """
    scopes = graph_store.list_scopes()
    app_ids = {s[0] for s in scopes}
    builds = {s[1] for s in scopes}
    if len(app_ids) > 1:
        raise CandidateReviewError(
            f"graph 库里有多个 app_id {sorted(app_ids)}——发现事件不携带 app "
            f"身份，无法判定登记到哪一个（先清理或分库）")
    if len(builds) > 1:
        raise CandidateReviewError(
            f"graph 库里有多个 app_build {sorted(builds)}——发现事件不携带 "
            f"build 身份，无法判定登记到哪一个（先清理或分库）")
    return (next(iter(app_ids), ""), next(iter(builds), ""))


def _undo_promote(repo_root: str | Path, rel: str, sha: str) -> tuple[bool, str]:
    """撤销刚做的 promote commit。返回 `(成功?, 手法说明)`。

    首选 `git revert --no-edit <sha>`：**只追加**历史、不改写，与打印给用户的
    「撤销 = git revert <sha>」同一手法。

    ⚠️ **但 `git revert` 在暂存区有任何东西时直接拒绝**（round 2 P2-C 实测）：
    `error: your local changes would be overwritten by revert`——连「暂存了一个
    **无关既有文件**的修改」都触发，而「改到一半顺手审一个候选」正是最常见状态。
    拒绝本身不弄坏任何东西，却会把仓库留在**最坏形态**（commit 已落 + 库还
    PENDING），且原先那句「先 `git revert` 再重试」的建议**自己也执行不了**。

    退路是**只作用于自己那个路径**的逆提交：`git rm -- rel` + `git commit -- rel`。
    它同样只追加历史，且与 promote commit 一样带 pathspec——P1-B 那条判据在
    撤销侧再贯彻一次：**绝不会碰别人的在途工作**。
    """
    try:
        run_git(repo_root, "revert", "--no-edit", sha)
        return True, f"git revert {sha[:8]}（只追加历史，工作区干净）"
    except Exception:  # noqa: BLE001 — 脏暂存区被拒是常态，走退路
        pass
    try:
        run_git(repo_root, "rm", "--quiet", "--", rel)
        run_git(repo_root, "commit", "-m",
                f"revert: 撤销 candidate 落盘 {sha[:8]}\n\n"
                f"库状态更新失败，故只按路径撤销本次落盘；不影响其他在途改动",
                "--", rel)
        return True, (f"revert 被脏暂存区拒绝，已改用只作用于 {rel} 的逆提交"
                      f"（只追加历史，未碰其他在途改动）")
    except Exception:  # noqa: BLE001 — 两条路都断，交由调用方如实报
        return False, ""


def _validated_testcase(row: dict, *, repository, secrets,
                        existing_case_ids: frozenset[str],
                        suites_root: str | Path, repo_root):
    """ACCEPT 的全部前置（**零副作用**），任一不过即 `CandidateReviewError`。

    五条：① `repo_root` 已给（落盘目录必须落在 git 仓库内）；② `testcase_yaml`
    可解析（F5 的同一 Schema）；③ `suite` 已声明（落盘目录无从确定）；④ 过
    `mta lint`（落盘的文件必须能进 P1 流程——设计 6.1「不是新路径」）；⑤ id 不与
    正式用例冲突（**fail-loud**，与 promoter 的「同 id 并入」不同：那是它的领域
    主流程，候选落盘是新文件）。

    ① 是 Task 4.5 补的：`repo_root` 从签名必填变成按 kind 分流之后，「test 分支
    没拿到它」不再由 Python 签名保证，必须在这里显式挡（否则 `Path(None)` 会以
    `TypeError` 漏成 traceback，而 CLI 只接域错误与 `RuntimeError`）。
    """
    if repo_root is None:
        raise CandidateReviewError(
            "test 候选的 ACCEPT 需要 repo_root（落盘目录必须落在 git 仓库内）"
            "——它现在是按 kind 分流的输入，discovery 分支才不用它")
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

    坏 YAML 与 ACCEPT 路径**同一处置**（review_p3_task35 P3-5）：解析失败包成
    `CandidateReviewError` 而不是把 `yaml.YAMLError` 漏给调用方——同一个
    「候选的文本读不出来」在两条命令下不该一条 exit 3、一条 traceback。
    """
    _require_kind(kind)
    row = store.get_candidate(candidate_id)
    if row is None:
        raise CandidateReviewError(f"候选 {candidate_id} 不存在")
    try:
        tc = parse_testcase_dict(yaml.safe_load(row["testcase_yaml"]))
    except Exception as e:  # noqa: BLE001 — 与 `_validated_testcase` 同款
        raise CandidateReviewError(
            f"候选 {candidate_id} 的 testcase_yaml 解析失败"
            f"（F5 的 P1 Schema）：{type(e).__name__}: {e}") from e
    target = Path(suites_root) / (tc.suite or "<suite>") / f"{tc.id}.yaml"
    return (f"# 建议落盘路径: {target.as_posix()}\n"
            f"# 应用: 写入该文件后 git add/commit（工具不代劳，H15）\n"
            + dump_testcase_yaml(tc))
