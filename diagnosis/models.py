"""models.py — 诊断的值对象（设计 §8.1/§8.3/§11；Task 5.1 / P3-20）。

## F10 的第一道防线在这里

诊断产出的是 **Hypothesis（假设）**，不是结论。两道机械闸门：

1. **空 `evidence` 拒绝创建**（`Hypothesis._evidence_not_empty`）：一条没有证据
   的假设与「LLM 觉得」在对账时没有区别——那正是设计 §8.2 与 F10 要防的东西。
   证据是**结构化的**（`EvidenceItem.kind` 必须落在权重表
   `DIAGNOSIS_EVIDENCE_WEIGHTS` 的键集里），不是一段自由文本：自由文本无法
   参与打分，也就无法被 §8.3 的证据分复核。
2. **`BugCandidateDraft.attribution_hypothesis` 默认 `UNTRIAGED`**：归因是
   **人工**通道（P1 `TraceStore.triage_testcase`）。系统可以给
   `suggested_attribution`（Task 5.3，需过证据阈值），但**从不**直接写
   `failure_attribution`——8.2 明文「不得仅凭 ELEMENT_NOT_FOUND 判 APP_DEFECT」。

## 三处词表与它们的真值源

| 词表 | 本文件的常量 | 真值源 | 一致性由谁钉 |
|---|---|---|---|
| 证据类别 | `EVIDENCE_KINDS` | **派生**自 `agents.policy_config.DIAGNOSIS_EVIDENCE_WEIGHTS` | 派生即一致（无第二处字面量） |
| 假设来源 | `HYPOTHESIS_ORIGINS` | 设计 §8.2（`llm_hypothesis`）+ 规则引擎 | `tests/unit/test_diagnosis_models.py` |
| 归因 | `ATTRIBUTIONS` | P1 `tracer/storage.py::ATTRIBUTIONS` | `test_diagnosis_attributions_match_p1`（两处字面量 + 一致性测试，手法同 `test_state_check_constraint_matches_enum`） |

⚠️ **`ATTRIBUTIONS` 是刻意复制的第二处字面量**，不是疏漏：F3 禁止
`diagnosis/**` 直连数据源（`tracer.storage` 在禁列），所以拿不到那个集合，
只能复制 + 钉一致性。这与 `candidates/test_case.py` 复制迁移 002 的 CHECK
词表是同一种处置。

## 为什么 `severity` 复用 `Risk` 的档名

设计 §11 只写了 `severity TEXT`，**没给词表**。取 `Risk` 的四档名
（`CRITICAL/HIGH/MEDIUM/LOW`）而不是另造一套（`P0/P1/…`）：同一个项目里
「严重程度」的两套词表只会让人在两处之间做心算。`DEFAULT_SEVERITY` 取
`MEDIUM`——**不是** `HIGH`：诊断在证据不足时的默认立场是「不知道多严重」，
抬到 HIGH 会让每个未复核的候选都挤进高优先队列。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents.policy_config import DIAGNOSIS_EVIDENCE_WEIGHTS

__all__ = [
    "ATTRIBUTIONS", "BUG_SEVERITIES", "BUG_STATUSES", "DEFAULT_SEVERITY",
    "EVIDENCE_KINDS", "HYPOTHESIS_ORIGINS",
    "BugCandidateDraft", "EvidenceItem", "Hypothesis",
]

# 证据类别 = 权重表的键集（**派生**，不是手抄——两处永不漂移）。
EVIDENCE_KINDS: tuple[str, ...] = tuple(DIAGNOSIS_EVIDENCE_WEIGHTS)

# 假设来源：规则引擎（§8.2 的确定性规则）与 LLM 补充（§8.2 末段，权重表里
# 对应 `llm_hypothesis_only: 1` 那一档——最低）。
HYPOTHESIS_ORIGINS: tuple[str, ...] = ("rule_based", "llm_hypothesis")

# P1 的归因词表（复制，见模块 docstring；一致性有测试钉）。
ATTRIBUTIONS: tuple[str, ...] = (
    "UNTRIAGED", "APP_DEFECT", "AUTOMATION_DEFECT", "ENVIRONMENT_DEFECT",
    "TEST_DATA_DEFECT", "INFRASTRUCTURE_DEFECT")

# 设计 §8.5 的状态机词表（转换表在 Task 5.6；本任务只定型词表与落库）。
BUG_STATUSES: tuple[str, ...] = (
    "DISCOVERED", "ANALYZING", "CANDIDATE", "HUMAN_REVIEW",
    "CONFIRMED", "REJECTED", "DUPLICATE")

BUG_SEVERITIES: tuple[str, ...] = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
DEFAULT_SEVERITY = "MEDIUM"


class _Strict(BaseModel):
    """多余字段拒绝（与 `agents/models.py` / `planner/models.py` 同款）。"""

    model_config = ConfigDict(extra="forbid")


class EvidenceItem(_Strict):
    """一条**结构化**证据（F10：假设必须能被证据复核）。

    `kind` 是**权重表的键**（`DIAGNOSIS_EVIDENCE_WEIGHTS`）——Task 5.3 的
    `score_evidence` 就是按它求和，所以拼错的 kind 必须当场报错，而不是
    静默加 0 分（「证据看起来提交了、其实一分没加」是最难查的一类）。
    """

    kind: str
    detail: str = ""
    source: str = ""      # 这条证据从哪来（可追溯：如 "trace:run_x/step_3"）

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v: str) -> str:
        if v not in EVIDENCE_KINDS:
            raise ValueError(
                f"未知证据类别 {v!r}——必须是 {list(EVIDENCE_KINDS)} 之一"
                f"（权重表 DIAGNOSIS_EVIDENCE_WEIGHTS 的键集；拼错的 kind "
                f"会静默加 0 分）")
        return v


class Hypothesis(_Strict):
    """设计 §8.1：一条带证据的假设（**不是结论**）。

    `confidence` 是**假设自己的置信度**，不是排序依据——设计 §8.3 明文排序
    由 Evidence Score 决定（Task 5.3 的钉子测试：同证据集换 confidence，
    次序不变）。
    """

    statement: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceItem]
    origin: str

    @field_validator("statement")
    @classmethod
    def _statement_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("statement 不能为空/纯空白（空假设没有可复核的内容）")
        return v

    @field_validator("evidence")
    @classmethod
    def _evidence_not_empty(cls, v: list[EvidenceItem]) -> list[EvidenceItem]:
        """**F10 的第一道防线**：空证据的假设拒绝创建。

        `[]` 与「一段说不出来源的推断」在审计上是同一件事——设计 §8.2 要求
        规则给不出结论才用 LLM，而 LLM 的输出「仍然只产出带 confidence 的
        Hypothesis」，即**仍须挂证据**（哪怕那条证据是 `llm_hypothesis_only`）。
        """
        if not v:
            raise ValueError(
                "Hypothesis 必须至少有一条 evidence（F10：没有证据的假设与"
                "「LLM 觉得」在对账时没有区别）")
        return v

    @field_validator("origin")
    @classmethod
    def _known_origin(cls, v: str) -> str:
        if v not in HYPOTHESIS_ORIGINS:
            raise ValueError(
                f"未知 origin {v!r}——必须是 {list(HYPOTHESIS_ORIGINS)} 之一")
        return v


class BugCandidateDraft(_Strict):
    """设计 §11 `bug_candidates` 行的**诊断侧**投影（入库前的形态）。

    与 `candidates/bug.py::BugCandidate` 的分工，照 `TestCase` vs
    `TestCandidate` 的先例：本模型是**诊断产出的草稿**（没有 `created_at`、
    没有 `reviewer`——那些是行元数据，归 DB 与人工审核通道），
    `BugCandidate` 是**落库行**。

    ⚠️ `attribution_hypothesis` 默认 `UNTRIAGED`：**系统不自动归因**（F10）。
    它装的是「假设的归因方向」，`suggested_attribution`（Task 5.3）过阈值
    才填；真正的 `failure_attribution` 只经人工 triage 写。
    """

    title: str
    failure_type: str | None = None
    severity: str = DEFAULT_SEVERITY
    attribution_hypothesis: str = "UNTRIAGED"
    evidence: list[EvidenceItem] = Field(default_factory=list)
    evidence_score: int = Field(default=0, ge=0)
    reproducible: bool | None = None
    reproduction_count: int = Field(default=0, ge=0)
    dedup_key: str | None = None
    affected_tests: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title 不能为空/纯空白（审核队列里无法辨认）")
        return v

    @field_validator("severity")
    @classmethod
    def _known_severity(cls, v: str) -> str:
        if v not in BUG_SEVERITIES:
            raise ValueError(
                f"未知 severity {v!r}——必须是 {list(BUG_SEVERITIES)} 之一")
        return v

    @field_validator("attribution_hypothesis")
    @classmethod
    def _known_attribution(cls, v: str) -> str:
        if v not in ATTRIBUTIONS:
            raise ValueError(
                f"未知 attribution {v!r}——必须是 {list(ATTRIBUTIONS)} 之一")
        return v

    def to_dict(self) -> dict[str, Any]:
        """JSON 可序列化的快照（落库前由 `candidates/bug.py` 消费）。"""
        return self.model_dump(mode="json")
