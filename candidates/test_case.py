"""test_case.py — Candidate Test 的表读写（设计 §11；Task 3.4 / P3-12）。

设计 §11 的 `test_candidates` 只表达「**入库的候选**」一种状态。但生成管线的
判据要求「**被拒的**也留痕」：

- 矩阵 #7：Schema/lint 不过 → **拒绝入库**（fail-loud），但拒绝的理由必须可查；
- 矩阵 #8：Test Fingerprint 命中 → **SKIP**，且 skip 原因（**撞的是哪一条**）可查。

故本模块对应**两张表**（迁移 002）：`test_candidates`（F6 审核队列的输入）与
`test_candidate_rejections`（生成期审计留痕）。分开的理由见迁移脚本头部。

## 分层：SQL 不在本模块

与 `planner/models.py` 同款——本模块提供**模型 + 与 store 的互转**，SQL 全在
`agents/storage.py`（plan §2 注意事项 #5 的明文：agent.db 的可变状态库单写者
是 `agents/storage.py`）。依赖方向单向：`generator → candidates → agents`，
`agents/**` 对 `candidates` 的依赖为**零**。

## 序列化口径：`testcase_yaml` 是**人可复核的 YAML 文本**

设计 §11 的列名就是 `testcase_yaml`，且它的下游（Task 3.5 的 ACCEPT → 落
`suites/<suite>/<id>.yaml`）要的是**能直接进 P1 流程的文件文本**。故这里存
`yaml.safe_dump(model_dump(mode="json"))`，不是 JSON 串——`dump_testcase_yaml`
是本仓**唯一**的「TestCase → YAML 文本」实现（Task 3.5 落盘复用同一个函数，
不各写一份，否则「库里的文本」与「落盘的文本」会漂移）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from testcase.schema import GenerationEvidence, TestCase

__all__ = [
    "DRY_RUN_STATUSES", "REVIEW_STATUSES", "REJECTION_REASONS",
    "CandidateRejection", "TestCandidate", "dump_testcase_yaml",
]

# 迁移 002 的 CHECK 词表（与 `002_test_candidates.sql` 是**两处字面量**，
# 一致性由 `tests/unit/test_candidate_store.py` 钉住——手法同 001 的
# `test_state_check_constraint_matches_enum`）。
DRY_RUN_STATUSES = ("PENDING", "PASS", "FAIL")
REVIEW_STATUSES = ("PENDING", "ACCEPT", "REJECT")
REJECTION_REASONS = ("SCHEMA_INVALID", "SEMANTIC_REJECTED", "DUPLICATE")


class _Strict(BaseModel):
    """多余字段拒绝（与 `agents/models.py` / `planner/models.py` 同款）。"""

    model_config = ConfigDict(extra="forbid")


class TestCandidate(_Strict):
    """设计 §11 的 `test_candidates` 行。

    ⚠️ **`generation_evidence` 必填**（与 `TestCase.generation_evidence` 可选
    不同）：正式用例可以没有溯源（人写的），而**候选按定义必须能回答「从哪来」**
    ——那是 F5 的落点，也是人工审核时唯一能判断「这条值不值得看」的依据。
    生成器拿不出证据时应当**不建候选**（写 `test_candidate_rejections`），
    而不是建一条溯因为空的候选。
    """

    __test__ = False        # pytest collection（同 `TestPlan` / `TestCase`）

    candidate_id: str
    testcase_yaml: str
    generated_by: str | None = None
    generation_evidence: GenerationEvidence
    fingerprint: str | None = None
    dry_run_status: str = "PENDING"
    # P2-2：Dry Run 的**执行结论**（`failure_type` / `attempt` /
    # `cleanup_status`）。只落一个 `FAIL` 是「看起来正常」——人工审核看到 FAIL
    # 却不知道**为什么**失败。与 `generation_evidence`（**溯源**：这条候选从
    # 哪来）分开存，两件事不该混在一个 JSON 里。
    dry_run_detail: dict = Field(default_factory=dict)
    review_status: str = "PENDING"
    # 迁移 004（Task 3.5）：人工审核的理由留痕（REJECT 必填——F6 与 P2
    # `recovery_reviews` 的 note 字段对齐）。NULL = 未决或无理由。
    review_note: str | None = None
    reviewer: str | None = None
    reviewed_at: str | None = None
    created_at: str = ""

    # --- 与 agent.db 的 `test_candidates` 表互转 --------------------------------

    def to_store_dict(self) -> dict[str, Any]:
        """行模型的**字段快照**（与 `agents/storage.py` 的 `_candidate_from_row`
        逐一对应；`from_store_dict` 是它的逆）。

        ⚠️ **不是** `save_candidate(**...)` 的实参形状（review_p3_task35 P2-4）：
        早先这里写着「→ save_candidate 的关键字实参形状」，实测
        `save_candidate(**tc.to_store_dict())` 是 `TypeError`——审核三列
        （`review_status` / `reviewer` / `reviewed_at`，以及迁移 004 的
        `review_note`）**不在**它的形参里。那不是缺漏，是刻意的单向门：
        `review_status` 的唯一写路径是 `decide_candidate`（P2-2），
        `save_candidate` 只写 `'PENDING'`。Task 3.5 的 ACCEPT 落盘走的是
        `dump_testcase_yaml(tc)`，**从没**消费本方法。

        `generation_evidence` 落 `model_dump(mode="json")`（JSON 可序列化）；
        `created_at` **不在这里**——它由 store 在写入时取 `_now()`（与
        `save_plan` 同款：时间戳归 DB 拥有，调用方不塞）。
        """
        return {"candidate_id": self.candidate_id,
                "testcase_yaml": self.testcase_yaml,
                "generated_by": self.generated_by,
                "generation_evidence": self.generation_evidence
                .model_dump(mode="json"),
                "fingerprint": self.fingerprint,
                "dry_run_status": self.dry_run_status,
                "dry_run_detail": self.dry_run_detail,
                "review_status": self.review_status,
                "review_note": self.review_note,
                "reviewer": self.reviewer,
                "reviewed_at": self.reviewed_at}

    @classmethod
    def from_store_dict(cls, row: dict[str, Any]) -> "TestCandidate":
        """← `SQLiteAgentStore.get_candidate(...)` 的返回。

        **多余的键被忽略**（`created_at` 之外的 DB 列）、**缺字段仍然 fail-loud**
        （与 `TestPlan.from_store_dict` 同款：`extra="forbid"` 保护的是「输入」，
        不是「读出」）。
        """
        return cls(candidate_id=row["candidate_id"],
                   testcase_yaml=row["testcase_yaml"],
                   generated_by=row.get("generated_by"),
                   generation_evidence=row.get("generation_evidence") or {},
                   fingerprint=row.get("fingerprint"),
                   dry_run_status=row.get("dry_run_status") or "PENDING",
                   dry_run_detail=row.get("dry_run_detail") or {},
                   review_status=row.get("review_status") or "PENDING",
                   review_note=row.get("review_note"),
                   reviewer=row.get("reviewer"),
                   reviewed_at=row.get("reviewed_at"),
                   created_at=row.get("created_at") or "")


class CandidateRejection(_Strict):
    """`test_candidate_rejections` 行：生成期被拒/SKIP 的留痕（矩阵 #7/#8）。

    `candidate_id` 与 `fingerprint` **都可空**：Schema 不过的候选可能连 id 都
    取不出来（`id` 字段本身缺失/类型错），指纹更算不出（步骤解析不了）。用
    空串表示「没有」会让「没算出来」与「算出来是空」混为一谈——故一律 `None`。
    """

    rejection_id: str
    candidate_id: str | None = None
    reason: str
    fingerprint: str | None = None
    duplicate_of: str | None = None
    detail: dict = Field(default_factory=dict)
    created_at: str = ""

    def to_store_dict(self) -> dict[str, Any]:
        return {"rejection_id": self.rejection_id,
                "candidate_id": self.candidate_id,
                "reason": self.reason,
                "fingerprint": self.fingerprint,
                "duplicate_of": self.duplicate_of,
                "detail": self.detail}

    @classmethod
    def from_store_dict(cls, row: dict[str, Any]) -> "CandidateRejection":
        return cls(rejection_id=row["rejection_id"],
                   candidate_id=row.get("candidate_id"),
                   reason=row["reason"],
                   fingerprint=row.get("fingerprint"),
                   duplicate_of=row.get("duplicate_of"),
                   detail=row.get("detail") or {},
                   created_at=row.get("created_at") or "")


def dump_testcase_yaml(tc: TestCase) -> str:
    """`TestCase` → 可人工复核、可直接落盘的 YAML 文本（**唯一实现**）。

    `sort_keys=False`：保持模型字段序（`schema_version` / `id` / `name` / …），
    人读与 git diff 都更稳；`allow_unicode=True`：中文 `name` 不转义成 `\\uXXXX`
    ——`testcase_yaml` 是给人看的（Task 3.5 的 ACCEPT 直接落这份文本）。

    `mode="json"`：枚举落字符串（`Idempotency.IDEMPOTENT` → `"IDEMPOTENT"`），
    与 `suites/*.yaml` 的现行形态一致（P1 的用例文件就是这么写的）。
    """
    return yaml.safe_dump(tc.model_dump(mode="json"), allow_unicode=True,
                          sort_keys=False, default_flow_style=False)


def utcnow() -> str:
    """行时间戳（与 `agents/storage.py::_now` **同格式**）。

    ⚠️ **当前零生产调用方**（review_p3_task34 P3-2 实锤；3.5 修订后仍然如此
    ——别让「Task 3.5 会用它」的说法再撒一次谎）：`reviewed_at` 的盖章归
    `agents/storage.py::decide_candidate`（时间戳与 SQL 同事务、单一来源，
    `to_store_dict` 的注释同理）。本函数留作 candidates 侧需要同一格式
    时间戳的**未来**消费方；格式一致是硬要求（时间戳列进 `ORDER BY … DESC`，
    两处格式不同会让同批行排到别处），所以它与 `_now` 必须逐字同形。
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
