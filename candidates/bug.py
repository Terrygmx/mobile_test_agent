"""bug.py — `bug_candidates` 的行模型（设计 §11；Task 5.1 / P3-20）。

与 `candidates/test_case.py` 的分工完全同款：本模块只有**行模型与 dict 互转**，
**SQL 不在本包**——agent.db 的单写者是 `agents/storage.py`（E9；有守卫扫
`_STORE_FILES` 的写语句区间）。

与 `diagnosis/models.py::BugCandidateDraft` 的关系（照 `TestCase` vs
`TestCandidate` 的先例）：

| | 谁产出 | 有什么 |
|---|---|---|
| `BugCandidateDraft` | 诊断（Task 5.2/5.3/5.4/5.5） | 假设、证据、分数——**没有**行元数据 |
| `BugCandidate`（本文件） | 落库行 | 上述全部 + `status` / `reviewer` / `created_at` |

`created_at` **不在这里**（与 `TestCandidate` 同款）：它是 DB 元数据，由 store
在写入时取 `_now()`——调用方不塞时间戳。

⚠️ **`review_decision` 不是 `status`**（F10）：`status` 是 §8.5 状态机的当前位置
（`DISCOVERED → … → CONFIRMED`），`review_decision` 是人工审核那一笔的**原始
决定词**（ACCEPT / REJECT，Task 5.6 的 `decide_candidate` 写）。两者一起看才能
回答「它现在是 CONFIRMED，但那是因为 accept 还是因为 duplicate 合并」。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from diagnosis.models import (ATTRIBUTIONS, BUG_SEVERITIES, BUG_STATUSES,
                              DEFAULT_SEVERITY, EvidenceItem)

__all__ = ["BugCandidate"]

# 迁移 005 的 CHECK 词表与 `diagnosis.models` 的常量是**两处字面量**，
# 一致性由 `tests/unit/test_bug_store.py` 钉住（手法同 001 的
# `test_state_check_constraint_matches_enum`）。
BUG_STATUS_VALUES = BUG_STATUSES
BUG_SEVERITY_VALUES = BUG_SEVERITIES
BUG_ATTRIBUTION_VALUES = ATTRIBUTIONS


class _Strict(BaseModel):
    """多余字段拒绝（与 `candidates/test_case.py` 同款）。"""

    model_config = ConfigDict(extra="forbid")


class BugCandidate(_Strict):
    """设计 §11 `bug_candidates` 行。

    `evidence` 是 `EvidenceItem` 列表（与 `BugCandidateDraft` 同一个值对象
    ——证据的形状只有一处定义）；落库时序列化成 `evidence_json`。
    """

    candidate_id: str
    title: str
    severity: str = DEFAULT_SEVERITY
    failure_type: str = ""
    attribution_hypothesis: str = "UNTRIAGED"
    evidence: list[EvidenceItem] = Field(default_factory=list)
    evidence_score: int = 0
    # 三态：None = 还没试过复现 / False = 试过，复现不了 / True = 复现了
    # （迁移 005 的 `reproducible` 列保持可空，理由见那里的注释）
    reproducible: bool | None = None
    reproduction_count: int = 0
    dedup_key: str | None = None
    status: str = "DISCOVERED"
    affected_tests: list[str] = Field(default_factory=list)
    reviewer: str | None = None
    review_decision: str | None = None
    reviewed_at: str | None = None
    created_at: str = ""

    # --- 与 agent.db 的 `bug_candidates` 表互转 -------------------------------

    def to_store_dict(self) -> dict[str, Any]:
        """行模型的**字段快照**（与 `agents/storage.py::_bug_from_row` 逐一对应；
        `from_store_dict` 是它的逆）。

        ⚠️ **不是** `save_bug_candidate(**...)` 的实参形状——审核三列
        （`reviewer` / `review_decision` / `reviewed_at`）与 `status` 的**推进**
        不在那个方法的形参里（与 `TestCandidate.to_store_dict` 的说明同款）：
        状态推进的唯一入口是 Task 5.6 的状态机 + `decide_candidate`，
        `save_bug_candidate` 只写 `'DISCOVERED'`。
        """
        return {"candidate_id": self.candidate_id,
                "title": self.title,
                "severity": self.severity,
                "failure_type": self.failure_type,
                "attribution_hypothesis": self.attribution_hypothesis,
                "evidence": [e.model_dump(mode="json") for e in self.evidence],
                "evidence_score": self.evidence_score,
                "reproducible": self.reproducible,
                "reproduction_count": self.reproduction_count,
                "dedup_key": self.dedup_key,
                "status": self.status,
                "affected_tests": list(self.affected_tests),
                "reviewer": self.reviewer,
                "review_decision": self.review_decision,
                "reviewed_at": self.reviewed_at,
                "created_at": self.created_at}

    @classmethod
    def from_store_dict(cls, row: dict[str, Any]) -> "BugCandidate":
        return cls(
            candidate_id=row["candidate_id"],
            title=row["title"],
            severity=row["severity"],
            failure_type=row.get("failure_type") or "",
            attribution_hypothesis=row.get("attribution_hypothesis")
            or "UNTRIAGED",
            evidence=[EvidenceItem.model_validate(e)
                      for e in (row.get("evidence") or [])],
            evidence_score=row.get("evidence_score") or 0,
            # ⚠️ `or None` 会把 **False 变成 None**（False 是假值）——三态列
            # 上这是「复现不了」被静默改写成「还没试过」的形态，正是本仓
            # 最防的「看起来正常」。故显式判 `is None`。
            reproducible=(None if row.get("reproducible") is None
                          else bool(row["reproducible"])),
            reproduction_count=row.get("reproduction_count") or 0,
            dedup_key=row.get("dedup_key"),
            status=row.get("status") or "DISCOVERED",
            affected_tests=list(row.get("affected_tests") or []),
            reviewer=row.get("reviewer"),
            review_decision=row.get("review_decision"),
            reviewed_at=row.get("reviewed_at"),
            created_at=row.get("created_at") or "")
