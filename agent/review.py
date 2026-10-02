"""review.py — 人工确认流程（9.5 / H15，Task 4.2）。

目的（9.5 原文）：避免「App 真出了 bug → LLM 找到另一个按钮 → 变绿 →
永久学坏」。RECOVERED 不是 PASS——只有人工 ACCEPT 过的恢复才允许变成
overrides，且**工具只导出补丁，绝不自动写入** repository/overrides（H15，
「实现存在但零消费」的镜像纪律：这里是有能力写入也故意不写）。

CLI（14.6）：
  mta review list [--status PENDING]     列出待确认恢复（join recoveries）
  mta review accept <id> [--out PATH]    确认 + 导出 overrides 补丁
  mta review reject <id> --note "..."    拒绝（note 必填——拒绝理由是 triage 数据）

H18：纯查询/导出逻辑离设备；store 经参数注入。
"""
from __future__ import annotations

import io

import yaml

__all__ = ["list_reviews", "decide_review", "export_overrides_patch",
           "ReviewError"]


class ReviewError(RuntimeError):
    """review 流程错误（id 不存在 / 状态非法 / 导出信息缺失）。fail-loud。"""


def list_reviews(store, status: str | None = "PENDING") -> list[dict]:
    """列出 review 行（join recoveries 拿补丁导出所需的候选信息）。"""
    rows = store.list_reviews(None if status in (None, "ALL") else status)
    return [dict(r) for r in rows]


def decide_review(store, review_id: int, decision: str, reviewer: str,
                  note: str | None = None) -> None:
    """PENDING → ACCEPT/REJECT 流转（reviewer 落库，审计依据）。"""
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
    store.decide_review(review_id, decision, reviewer, note)


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
