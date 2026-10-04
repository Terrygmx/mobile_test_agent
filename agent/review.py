"""review.py — 人工确认流程（9.5 / H15，Task 4.2；P2 出口 Task 2.3）。

目的（9.5 原文）：避免「App 真出了 bug → LLM 找到另一个按钮 → 变绿 →
永久学坏」。RECOVERED 不是 PASS——只有人工 ACCEPT 过的恢复才允许变成
overrides，且**工具只导出补丁，绝不自动写入** repository/overrides（H15，
「实现存在但零消费」的镜像纪律：这里是有能力写入也故意不写）。

P2 新增（设计 8.1 / E5，Task 2.3）：人工 ACCEPT 同时是 **Experience
Candidate 的唯一入口**——`decide_review(..., experience_store=...)` 在状态
流转后立刻 `create_candidate`，不靠后台扫描判断。REJECT 的记录**不经过
任何代码路径**进入 Experience Store。

CLI（14.6）：
  mta review list [--status PENDING]     列出待确认恢复（join recoveries）
  mta review accept <id> [--out PATH]    确认 + 导出 overrides 补丁 + 建 Candidate
  mta review reject <id> --note "..."    拒绝（note 必填——拒绝理由是 triage 数据）

H18：纯查询/导出逻辑离设备；store 经参数注入。
"""
from __future__ import annotations

import io

import yaml

from experience.models import CandidateSeed, Experience
from repository.loader import LocatorStrategy

__all__ = ["list_reviews", "decide_review", "export_overrides_patch",
           "seed_candidate", "ReviewError"]


class ReviewError(RuntimeError):
    """review 流程错误（id 不存在 / 状态非法 / 导出信息缺失）。fail-loud。"""


def list_reviews(store, status: str | None = "PENDING") -> list[dict]:
    """列出 review 行（join recoveries 拿补丁导出所需的候选信息）。"""
    rows = store.list_reviews(None if status in (None, "ALL") else status)
    return [dict(r) for r in rows]


def _resolve_seed_fields(store, review_id: int) -> dict:
    """E5 种子字段的「取 + 校验」——缺任一必填项即 ReviewError，**不改状态**。

    追溯链断裂（P0 遗留 `step_id=0` / 关联不到 steps）在这里被挡下，与
    Task 1.1 审计的 `seed_ready=False` 同一判据：审计暴露缺口，消费方
    拒绝消费，两侧都不许静默跳过（E5）。
    """
    row = store.get_review_seed(review_id)
    if row is None:
        raise ReviewError(f"review {review_id} 不存在")
    required = {
        "app_id": "runs.app_bundle_id（Candidate 主键第一段；mta run 需 "
                  "--bundle-id）",
        "seed_run_id": "追溯链 run_id",
        "seed_step_id": "追溯链 steps.id",
        "screen": "恢复行 screen",
        "target_id": "恢复行 expected_target",
        "candidate_target": "恢复行 candidate_target（定位策略值）",
    }
    missing = [f"{k} ← {why}" for k, why in required.items()
               if not row.get(k)]
    if row.get("seed_step_id") == 0:
        missing.append("seed_step_id ← steps.id（0 是 P0 悬空写入形态）")
    if missing:
        raise ReviewError(
            f"review {review_id} 的 recovery 行种子字段不齐——E5 拒绝建 "
            f"Candidate: " + "; ".join(missing))
    return row


def seed_candidate(store, experience_store, review_id: int) -> Experience:
    """设计 8.1 / E5：ACCEPT → Experience Candidate 的唯一入口。

    幂等：同一 review 已产生过 Experience（CANDIDATE/VERIFIED/DEGRADED）
    → 返回既有行，不重复建（重放、重复触发安全）。REJECTED 的行 `lookup`
    看不见（Store 层语义，设计 7.1 修订记录）——人工判过「不可用」的策略
    再被 ACCEPT 一次，本就该重新走一遍学习，不视为重复。
    """
    row = _resolve_seed_fields(store, review_id)
    if row["review_status"] != "ACCEPT":
        raise ReviewError(
            f"review {review_id} 状态为 {row['review_status']}——只有 ACCEPT "
            f"允许做 Candidate 种子（E5）")
    for existing in experience_store.lookup(
            row["app_id"], row["screen"], row["target_id"]):
        if existing.seed_recovery_review_id == review_id:
            return existing

    # strategy 复用 P1 LocatorStrategy（单一真值源）；类型固定
    # accessibility_id（LLM 候选给的是 accessibility identifier 值），
    # origin="experience"——P2 9.2 已把 experience 纳入 origin 词汇（Task 4.1
    # 扩 loader.ORIGINS）；本策略非人工手写（manual）也非源码生成（source）。
    # 在 Task 4.1 落地前它只存于 experience.db、不经 Repository loader
    # （其 ORIGINS 校验不含 experience），不构成拦截。
    seed = CandidateSeed(
        review_id=review_id,
        recovery_id=row["recovery_id"],
        seed_run_id=row["seed_run_id"],
        seed_step_id=row["seed_step_id"],
        seed_recovery_review_id=review_id,
        app_id=row["app_id"],
        screen_id=row["screen"],
        target_id=row["target_id"],
        strategy=LocatorStrategy(
            type="accessibility_id", value=row["candidate_target"],
            origin="experience"),
        app_build=row.get("app_build"),
        reviewer=row.get("reviewer"),
    )
    exp = experience_store.create_candidate(seed)
    # 设计 11.1：trace 事件（追加 P1 Trace，不新建存储体系）
    store.record_experience_event(
        "candidate_created", run_id=row["seed_run_id"],
        tc_run_id=row.get("seed_tc_run_id"),
        detail={
            "experience_id": exp.experience_id,
            "app_id": exp.app_id,
            "screen_id": exp.screen_id,
            "target_id": exp.target_id,
            "status": exp.status.value,
            "seed_recovery_review_id": review_id,
            "strategy": {"type": seed.strategy.type,
                         "value": seed.strategy.value},
        })
    return exp


def decide_review(store, review_id: int, decision: str, reviewer: str,
                  note: str | None = None, *,
                  experience_store=None) -> Experience | None:
    """PENDING → ACCEPT/REJECT 流转（reviewer 落库，审计依据）。

    `experience_store` 非空且 decision=ACCEPT 时（设计 8.1 / E5）：**先校验
    种子字段 → 再改状态 → 最后 create_candidate**。顺序不可颠倒——种子不齐
    时若已落 ACCEPT，二次 accept 会被「不可二次决策」挡住，Candidate 就
    永远建不出来（fail-loud 必须发生在状态变更之前）。

    返回 ACCEPT 且带 experience_store 时新建/命中的 Candidate，其余情况
    None（旧调用方忽略返回值即可，行为不变）。
    """
    if decision not in ("ACCEPT", "REJECT"):
        raise ReviewError(f"invalid decision: {decision!r}")
    if decision == "REJECT" and not (note or "").strip():
        # 拒绝没有理由 = triage 数据断流（8.2 归因纪律的 review 侧投影）
        raise ReviewError("reject 需要 --note（拒绝理由必须留痕）")
    row = store.get_review(review_id)
    if row is None:
        raise ReviewError(f"review {review_id} 不存在")
    if row["review_status"] != "PENDING":
        raise ReviewError(
            f"review {review_id} 已是 {row['review_status']}（不可二次决策）")
    if decision == "ACCEPT" and experience_store is not None:
        _resolve_seed_fields(store, review_id)      # 先校验，不改状态
    store.decide_review(review_id, decision, reviewer, note)
    if decision == "ACCEPT" and experience_store is not None:
        return seed_candidate(store, experience_store, review_id)
    return None


def export_overrides_patch(store, review_id: int,
                           reviewed_by: str | None = None) -> str:
    """ACCEPT 的恢复 → overrides 补丁文本（YAML，单元素）。

    H15：**返回文本**，写不写盘由调用方决定；CLI 默认 stdout、--out 落盘，
    任何路径都不触碰 repository/overrides。
    """
    row = store.get_review(review_id)
    if row is None:
        raise ReviewError(f"review {review_id} 不存在")
    if row["review_status"] != "ACCEPT":
        raise ReviewError(
            f"review {review_id} 状态为 {row['review_status']}——只有 ACCEPT "
            f"允许导出（9.5）")
    for field in ("candidate_target", "screen", "candidate_type"):
        if not row.get(field):
            raise ReviewError(
                f"review {review_id} 的 recovery 行缺 {field}——补丁需要"
                f"完整定位信息（screen/type/value），缺了就是猜值（12.2）")
    element = {
        "schema_version": "1.0",
        "kind": "element",
        "id": row["expected_target"],
        "screen": row["screen"],
        "type": row["candidate_type"],
        "strategies": [{
            "type": "accessibility_id",
            "value": row["candidate_target"],
            "origin": "manual",
        }],
        "metadata": {
            "origin": "manual",
            "review_id": review_id,
            "reviewed_by": reviewed_by or row.get("reviewer"),
        },
    }
    buf = io.StringIO()
    buf.write(f"# mta review accept {review_id} 导出（H15：人工合入 "
              f"repository/overrides/elements/，工具不自动写入）\n")
    buf.write(f"# recovery: {row['recovery_id']}  kind: {row.get('kind')}"
              f"  reviewed_by: {reviewed_by or row.get('reviewer')}\n")
    yaml.safe_dump([element], buf, allow_unicode=True, sort_keys=False)
    return buf.getvalue()
