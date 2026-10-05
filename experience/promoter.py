"""promoter.py — Promotion 生成与落地（设计 9.1–9.3 / 9.5 / E10；Task 4.1 / P2-09）。

流程（9.3，两段式，中间是人工）：

  `generate_proposal` → PENDING proposal（diff + evidence_summary 落库，
  **不写任何文件**）→ 人工 review → `approve_proposal`（写
  `repository/overrides/elements/<Screen>.yaml`（origin: experience）+
  git commit + promoted 记账）。

## 红线

- **9.3 审计**：人工 promote 必须走 proposal——本模块没有「跳过 proposal
  直写 overrides」的入口；review accept（Task 2.3）的 H15 补丁出口与本
  路径是两回事（那里建 Candidate，这里把已验证的 Candidate 策略转正）。
- **9.4 两条时间线**：approve 不改 Experience.status——promoted /
  promoted_commit 是 Experience 行上的独立字段；撤销 Promotion 是人工
  `git revert`（E10），不是状态机行为。
- **9.5 人工越权路径**：非 VERIFIED（CANDIDATE/DEGRADED）生成 proposal
  必须 `manual_override=True` **且**给理由（无理由的越权不可审计）；
  REJECTED 是终态——连人工路径也不开，复活只能重新 ACCEPT（E5）。
- **不可静默覆盖**：目标 overrides 文件已有同 id 元素定义（多半是人工
  写的）→ fail-loud，人工 override 只能人工处置。
"""
from __future__ import annotations

import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from experience.models import Experience, ExperienceStatus, PromotionProposal
from experience.store import SQLiteExperienceStore
from experience.verifier import distinct_run_count

__all__ = ["generate_proposal", "approve_proposal"]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _render_diff(exp: Experience, proposal_id: str, *,
                 manual_override: bool) -> str:
    """可 review 的 YAML 补丁文本（9.3：diff 供人工 review）。

    命名注记（review_p2_task41 P3-4）：产出是**完整 YAML 文档**而非逐行
    diff——9.3 说的「YAML 补丁文本」落地为「将被追加进 overrides 的那份
    文档」，review 者看到的就是 approve 会写的内容。

    `mode: append`（review_p2_task41 P2-2 定档 (c)）：merge 层语义是
    「generated/manual/source 链在前，本 override 追加链尾」——§9.2 的
    「promoted experience 是链里最后一条，不是被丢弃」由**写入口**兑现，
    不靠使用方手拼文档；element 未在 generated 登记时 append 无害。
    `type` 字段**有意省略**（接线前置②的定档，review_p2_task23 P3-7）：
    Experience 不携带 element type，override 的 type 省略时 merge 沿用
    generated/manual 层的真实类型（5.3 语义）；连 generated 都没有的
    overrides-only 元素，EffectiveElement.type 回落 "other"——Guard 的
    类型校验用的是用例的 expected_type，不受此字段影响。
    """
    header = [
        f"# promotion: experience {exp.experience_id}（proposal {proposal_id}）",
        f"# 状态={exp.status.value} 样本={exp.sample_count} "
        f"成功率={exp.success_rate:.4f}",
    ]
    if manual_override:
        header.append("# ⚠️ manual_override_of_auto_policy=true——9.5 人工"
                      "越权路径（理由见 evidence_summary）")
    header.append("# 撤销 = git revert <commit>（E10；Experience Store"
                  "历史不受影响，9.6）")
    body = [
        "schema_version: \"1.0\"",
        "kind: element",
        f"id: {exp.target_id}",
        f"screen: {exp.screen_id}",
        "mode: append",
        "strategies:",
        f"  - {{type: {exp.strategy.type}, value: {exp.strategy.value},"
        " origin: experience}",
    ]
    return "\n".join(header) + "\n" + "\n".join(body) + "\n"


def generate_proposal(store: SQLiteExperienceStore, exp: Experience, *,
                      manual_override: bool = False,
                      reason: str | None = None,
                      reviewer: str | None = None) -> PromotionProposal:
    """9.2/9.3：生成 PENDING proposal（只落 proposal 表，不碰文件）。"""
    import json

    if exp.status is ExperienceStatus.REJECTED:
        # 终态不可 promote（含人工路径）：否决过的策略再转正等于绕过
        # 当时的否决——复活只能重新 ACCEPT 一条新 Candidate（E5）。
        raise ValueError(
            "REJECTED 是终态，不可 promote（含 9.5 人工路径）——"
            "要复活只能重新 ACCEPT 建 Candidate（E5 重新学习）")
    if exp.status is not ExperienceStatus.VERIFIED:
        if not manual_override:
            raise ValueError(
                f"只有 VERIFIED 可直接生成 Promotion Proposal，当前是 "
                f"{exp.status.value}——非 VERIFIED 走 9.5 人工路径需 "
                f"manual_override=True + 理由")
        if not (reason or "").strip():
            raise ValueError(
                "manual_override 必须提供理由（9.5：无理由的越权不可审计）")

    runs = store.get_runs(exp.experience_id)
    evidence: dict = {
        "status_at_proposal": exp.status.value,
        "sample_count": exp.sample_count,
        "success_rate": exp.success_rate,
        "distinct_runs": distinct_run_count(runs),
        "validated_builds": list(exp.validated_builds),
        "strategy": f"{exp.strategy.type}:{exp.strategy.value}",
    }
    if manual_override:
        evidence["manual_override_of_auto_policy"] = True
        evidence["override_reason"] = reason

    proposal_id = f"prop_{uuid.uuid4().hex[:12]}"
    proposal = PromotionProposal(
        proposal_id=proposal_id,
        experience_id=exp.experience_id,
        diff=_render_diff(exp, proposal_id, manual_override=manual_override),
        evidence_summary=json.dumps(evidence, ensure_ascii=False),
        status="PENDING",
        manual_override_of_auto_policy=manual_override,
        reviewer=reviewer,
    )
    return store.create_promotion_proposal(proposal)


def approve_proposal(store: SQLiteExperienceStore, proposal_id: str, *,
                     repo_root: Path,
                     committer: str | None = None) -> tuple[PromotionProposal,
                                                            str]:
    """9.3：approve → 写 overrides（origin: experience）+ git commit +
    promoted 记账。返回 (更新后的 proposal, commit sha)。

    E10 口径注记（review_p2_task41 P3-3）：commit 由工具辅助生成、落
    **当前分支**——PR 化（不直推主干）由团队流程负责，工具不强制。
    """
    import re

    if committer is None:
        import getpass
        committer = getpass.getuser()

    proposal = store.get_promotion_proposal(proposal_id)
    if proposal is None:
        raise ValueError(f"no such promotion proposal: {proposal_id}")
    if proposal.status != "PENDING":
        raise ValueError(
            f"proposal {proposal_id} 已决（{proposal.status}）——"
            f"只有 PENDING 可 approve")
    exp = store.get_experience(proposal.experience_id)
    if exp is None:
        raise ValueError(
            f"proposal 指向的 experience 不存在: {proposal.experience_id}")

    target = (Path(repo_root) / "repository" / "overrides" / "elements"
              / f"{exp.screen_id}.yaml")
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        # \b 而非 \s*$：行尾注释（`id: x  # 备注`）也要命中（review_p2_task41
        # P3-2——漏检会追加出同 id 双文档，loader 后者胜，静默覆盖）。
        if re.search(rf"^id:\s*{re.escape(exp.target_id)}\b",
                     existing, re.MULTILINE):
            # 不可静默覆盖：同 id 的 override 已存在（多半是人工写的）。
            raise ValueError(
                f"overrides 已存在同 id 元素定义：{exp.target_id!r} in "
                f"{target}——人工写过的 override 只能人工处置，工具不代改")
        original = existing
    else:
        original = None
        target.parent.mkdir(parents=True, exist_ok=True)

    # 写文件与 git commit 之间没有事务性（review_p2_task41 P2-1）：git 失败
    # 时**回滚文件写**（新建的删除、已存在的恢复原内容）——否则重试会被
    # 自己写了一半的文件挡死，还被误报成「人工 override」。proposal 保持
    # PENDING，修复 git 后重试即续传。
    try:
        if original is not None:
            target.write_text(original.rstrip("\n") + "\n---\n"
                              + proposal.diff, encoding="utf-8")
        else:
            target.write_text(proposal.diff, encoding="utf-8")
        rel = target.relative_to(Path(repo_root)).as_posix()
        _git(repo_root, "add", rel)
        _git(repo_root, "commit", "-m",
             f"promote: experience {exp.experience_id} "
             f"(proposal {proposal_id})\n\n"
             f"Committer: {committer}\n"
             f"origin: experience 策略转正（P2-09）；撤销 = git revert <sha>"
             f"（E10）")
    except Exception as e:
        if original is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(original, encoding="utf-8")
        raise RuntimeError(
            f"git 提交失败，overrides 写入已回滚（proposal 仍为 PENDING，"
            f"修复 git 后重试 approve 即续传）：{e}") from e
    sha = _git(repo_root, "rev-parse", "HEAD").strip()

    store.set_promotion_proposal_status(proposal_id, "APPROVED",
                                        git_commit=sha, reviewer=committer)
    store.record_promotion(exp.experience_id, sha)
    return store.get_promotion_proposal(proposal_id), sha


def _git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo_root), *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {result.stderr.strip()[:300]}")
    return result.stdout
