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

from experience.models import CandidateSeed, Experience, ExperienceStatus
from repository.loader import LocatorStrategy

__all__ = ["list_reviews", "decide_review", "export_overrides_patch",
           "validate_accept", "seed_candidate", "ReviewError"]


class ReviewError(RuntimeError):
    """review 流程错误（id 不存在 / 状态非法 / 导出信息缺失）。fail-loud。"""


def list_reviews(store, status: str | None = "PENDING") -> list[dict]:
    """列出 review 行（join recoveries 拿补丁导出所需的候选信息）。"""
    rows = store.list_reviews(None if status in (None, "ALL") else status)
    return [dict(r) for r in rows]


# ACCEPT 两个消费入口的必填字段集（review_p2_task23 P3-2 合一闸门的真值源）
_SEED_REQUIRED = {
    "app_id": "runs.app_bundle_id（Candidate 主键第一段；mta run 需 --bundle-id）",
    "seed_run_id": "追溯链 run_id",
    "seed_step_id": "追溯链 steps.id（0/None = P0 悬空写入形态）",
    "screen": "恢复行 screen",
    "target_id": "恢复行 expected_target",
    "candidate_target": "恢复行 candidate_target（定位策略值）",
}
_PATCH_REQUIRED = {
    "candidate_target": "补丁定位策略值（12.2 不许猜值）",
    "screen": "补丁目标屏",
    "candidate_type": "补丁元素类型（screen/type/value 缺一即猜值）",
}


def _require_fields(row: dict, review_id: int, required: dict,
                    gate: str) -> None:
    """必填集校验（fail-loud，缺项逐条列出——不静默跳过）。"""
    missing = [f"{k} ← {why}" for k, why in required.items()
               if not row.get(k)]
    if missing:
        raise ReviewError(
            f"review {review_id} 的 recovery 行不满足 {gate} 前置条件: "
            + "; ".join(missing))


def validate_accept(store, review_id: int) -> dict:
    """ACCEPT 的**全部消费前置条件**，一次跑完、零副作用（P2-04 合一纪律）。

    ACCEPT 有两个消费入口，各自有必填集：
      ① 建 Candidate（E5 / 设计 8.1）：种子三件套 + app_id/screen/target/候选值；
      ② 导出 overrides 补丁（9.5 / H15）：candidate_target/screen/candidate_type。
    两套曾各管各的（review_p2_task23 P3-2 实锤：seed 放行、export 拒绝 →
    状态已改 ACCEPT 而补丁再也导不出来）。这里合并为**单一闸门**，在状态
    变更之前一次跑完——失败即零副作用。

    追溯链断裂（P0 遗留 `step_id=0` / 关联不到 steps）与 Task 1.1 审计的
    `seed_ready=False` 同一判据：审计暴露缺口，消费方拒绝消费。
    """
    row = store.get_review_seed(review_id)
    if row is None:
        raise ReviewError(f"review {review_id} 不存在")
    _require_fields(row, review_id, _SEED_REQUIRED, "E5 种子")
    _require_fields(row, review_id, _PATCH_REQUIRED, "overrides 补丁（H15）")
    return row


def seed_candidate(store, experience_store, review_id: int) -> Experience:
    """设计 8.1 / E5：ACCEPT → Experience Candidate 的唯一入口。

    也供 `mta review reseed` 补种「已 ACCEPT 但没建出 Candidate」的 review
    （review_p2_task23 P3-1：`create_candidate` 自身失败曾留下无出口的半状态）。
    """
    row = store.get_review_seed(review_id)
    if row is None:
        raise ReviewError(f"review {review_id} 不存在")
    if row["review_status"] != "ACCEPT":
        raise ReviewError(
            f"review {review_id} 状态为 {row['review_status']}——只有 ACCEPT "
            f"允许做 Candidate 种子（E5）")
    return _seed_from_row(store, experience_store, review_id, row)


def _seed_from_row(store, experience_store, review_id: int,
                   row: dict) -> Experience:
    """种子字段已校验的行 → Candidate（幂等 + trace 事件）。

    幂等按 `find_by_seed_review` **直查**并显式排除 REJECTED——不靠
    `lookup` 的过滤当判据（review_p2_task23 P3-3：那样「同 review 已种过」
    会退化成「恰好还能读见」，置 REJECTED 后再 seed 会静默多出一行）。
    REJECTED 不算已种：人工判过「不可用」的策略再被 ACCEPT 一次，本就该
    重新走一遍学习。
    """
    _require_fields(row, review_id, _SEED_REQUIRED, "E5 种子")
    for existing in experience_store.find_by_seed_review(review_id):
        if existing.status is not ExperienceStatus.REJECTED:
            return existing

    # strategy 复用 P1 LocatorStrategy（单一真值源）；类型固定
    # accessibility_id（LLM 候选给的是 accessibility identifier 值），
    # origin="experience"——P2 9.2 已把 experience 纳入 origin 词汇。
    # ⚠️ Task 4.1 债（review_p2_task23 P3-4）：loader.ORIGINS 目前只有
    # (source, manual)，Promotion 写 overrides 前必须先扩，否则一上线
    # fail-loud。已记入 plan Task 4.1。
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

    ACCEPT 的顺序是**先校验（两个消费入口的全部前置条件，一次跑完）→ 改状态
    → 建 Candidate**。顺序不可颠倒——前置条件不满足时若已落 ACCEPT，二次
    accept 会被「不可二次决策」挡住，Candidate 就永远建不出来（fail-loud
    必须发生在状态变更之前）。行只取一次，校验与建库共用（不重复查库）。

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
    seed_row = None
    if decision == "ACCEPT":
        seed_row = validate_accept(store, review_id)   # 前置：零副作用
    store.decide_review(review_id, decision, reviewer, note)
    if decision == "ACCEPT" and experience_store is not None:
        return _seed_from_row(store, experience_store, review_id, seed_row)
    return None


def export_overrides_patch(store, review_id: int,
                           reviewed_by: str | None = None) -> str:
    """ACCEPT 的恢复 → overrides 补丁文本（YAML，单元素）。

    H15：**返回文本**，写不写盘由调用方决定；CLI 默认 stdout、--out 落盘，
    任何路径都不触碰 repository/overrides。必填集与 `validate_accept` 同源
    （`_PATCH_REQUIRED`）——同一条 ACCEPT 的两个消费入口不各持一套规则。
    """
    row = store.get_review(review_id)
    if row is None:
        raise ReviewError(f"review {review_id} 不存在")
    if row["review_status"] != "ACCEPT":
        raise ReviewError(
            f"review {review_id} 状态为 {row['review_status']}——只有 ACCEPT "
            f"允许导出（9.5）")
    _require_fields(row, review_id, _PATCH_REQUIRED, "overrides 补丁（H15）")
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
