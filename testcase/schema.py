"""Testcase schema 模型（设计 6.1~6.3，Task 1.1 / P1-01；Task 3.1 / P3-09 扩展）。

原则（设计 H7/H18）：
  - Pydantic 只建设计 6.3 列出的字段 + 6.2 的 Candidate 溯源字段，extra="forbid"
    （未知键一律 ValidationError）；
  - retry / 用例级 timeout 键不进 schema（重试归 Executor Policy，作者不可绕过）；
  - 纯数据模型，不 import 任何执行层模块（H18）。

F5：Candidate Test **就是**本模型的实例，只是多三个可选溯源字段——**不建平行模型**
（`extra="forbid"` 下平行模型会拒新键，且「同一 Schema」是本阶段的红线）。
"""
from __future__ import annotations

import enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "0.2"
SUPPORTED_SCHEMA_VERSIONS = ("0.2",)


class Idempotency(str, enum.Enum):
    """7.4 幂等性：NON_IDEMPOTENT > UNKNOWN > IDEMPOTENT。"""

    IDEMPOTENT = "IDEMPOTENT"
    NON_IDEMPOTENT = "NON_IDEMPOTENT"
    UNKNOWN = "UNKNOWN"


class Risk(int, enum.Enum):
    """7.4 风险等级：Guard 按 7.3 消费（CRITICAL → SECURITY_BLOCKED）。"""

    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class TargetRef(BaseModel):
    """6.3 目标引用：mode="before" 验证器接受 str 语法糖。

    - "login_button"    → element
    - "screen:HomeView" → screen
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["element", "screen"] = "element"
    id: str

    @model_validator(mode="before")
    @classmethod
    def _accept_sugar(cls, data: Any) -> Any:
        if isinstance(data, str):
            if data.startswith("screen:"):
                return {"type": "screen", "id": data.removeprefix("screen:")}
            return {"type": "element", "id": data}
        return data


class WaitSpec(BaseModel):
    """6.2 wait 条件矩阵：`active` 仅限 screen；文本条件需 `expected`（R5-1）。

    `polling_interval`：None = 未声明 → Executor 取配置默认（7.3「不得写死」）。
    """

    model_config = ConfigDict(extra="forbid")

    target: TargetRef
    condition: Literal[
        "exists",
        "not_exists",
        "visible",
        "enabled",
        "disabled",
        "text_equals",
        "text_contains",
        "active",
    ]
    timeout: float = Field(default=10, gt=0)
    polling_interval: float | None = Field(default=None, gt=0)
    expected: Any = None  # text_equals / text_contains 必填

    @model_validator(mode="after")
    def _active_screen_only(self) -> "WaitSpec":
        if self.condition == "active" and self.target.type != "screen":
            raise ValueError(
                f"condition 'active' requires screen target, got {self.target.type!r}"
            )
        return self

    @model_validator(mode="after")
    def _text_wait_needs_expected(self) -> "WaitSpec":
        if self.condition in ("text_equals", "text_contains") and self.expected is None:
            raise ValueError(f"wait condition {self.condition!r} requires 'expected'")
        return self


class WaitStep(BaseModel):
    """6.3 步骤形态 2：`{"wait_for": {...}}`。"""

    model_config = ConfigDict(extra="forbid")

    wait_for: WaitSpec


class Postcondition(BaseModel):
    """6.3 postcondition：动作完成后的目标状态。"""

    model_config = ConfigDict(extra="forbid")

    target: TargetRef
    condition: str
    timeout: float = Field(default=10, gt=0)


class ActionStep(BaseModel):
    """6.3 步骤形态 1：`{"action": ...}`。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["launch_app", "terminate_app", "tap", "input", "swipe", "back"]
    target: TargetRef | None = None
    value: str | None = None
    direction: Literal["up", "down", "left", "right"] | None = None
    sensitive: bool = False
    idempotency: Idempotency | None = None  # None = 未声明 → Executor 按 7.4 推导
    risk: Risk | None = None
    postcondition: Postcondition | None = None

    @model_validator(mode="after")
    def _input_needs_target_and_value(self) -> "ActionStep":
        if self.action == "input" and (self.target is None or self.value is None):
            raise ValueError("action 'input' requires target and value")
        return self


class AssertionSpec(BaseModel):
    """6.2 断言条件（7.3 Assertion Engine 在 Task 2.2 落地时消费）。"""

    model_config = ConfigDict(extra="forbid")

    target: TargetRef
    condition: Literal[
        "exists",
        "not_exists",
        "text_equals",
        "text_contains",
        "element_count",
        "enabled",
        "disabled",
    ]
    timeout: float = Field(default=10, gt=0)
    expected: Any = None  # text_equals / text_contains / element_count 必填

    @model_validator(mode="after")
    def _value_assertions_need_expected(self) -> "AssertionSpec":
        if self.condition in ("text_equals", "text_contains", "element_count") and (
            self.expected is None
        ):
            raise ValueError(f"condition {self.condition!r} requires 'expected'")
        return self


class AssertionStep(BaseModel):
    """6.3 步骤形态 3：`{"assertion": {...}}`。"""

    model_config = ConfigDict(extra="forbid")

    assertion: AssertionSpec


class GenerationEvidence(BaseModel):
    """6.2 候选溯源：这个候选**从何而来**（F5 的「溯源字段」）。

    三个列表装的是**人类可读的引用**，不是活对象——本模型随用例序列化进
    `test_candidates.testcase_yaml` 与最终落盘的 suite YAML，必须是 YAML 友好、
    可人工复核的形态（设计 6.2 示例即 `"Search -> EmptyResultState"` /
    `"SearchView.swift"` 这类字符串）。

    ⚠️ 元素类型定为 `str`：设计未给出结构化元素契约。生产侧（Task 3.4）若确需
    结构化元素，那是**改这份契约**的立项，不要在这里放宽成 `Any` 静默放行。

    三个键**必填**（设计 6.2 示例三者俱在）：空即写 `[]`，「没给」与「显式为空」
    由此可分辨。
    """

    model_config = ConfigDict(extra="forbid")

    coverage_gap: list[str]
    bug_history: list[str]
    source_refs: list[str]


class TestCase(BaseModel):
    """6.3 用例骨架。retry / 用例级 timeout 键不存在（H7）。

    6.2 的 Candidate 溯源三字段可选（默认 `None`）：正式用例不带它们，
    解析结果不受影响。
    """

    __test__ = False  # pytest collection: 数据模型不是测试类

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.2"]
    id: str
    name: str
    suite: str | None = None
    tags: list[str] = Field(default_factory=list)
    # R5-3 记账：precondition 应为 EnvSpec（reset ∈ RESET_STATE/RELAUNCH/TERMINATE/
    # LOGOUT/REINSTALL/SNAPSHOT）、cleanup 应为 CleanupSpec（含 failure_policy）。
    # 枚举校验延至 Task 2.x Environment Manager 落地时定型；M1 末 schema 冻结前必须解决，
    # 否则 `reset: RESET_STATEE` 这类 typo 会静默通过到运行期才炸。
    precondition: dict = Field(default_factory=dict)
    steps: list[ActionStep | WaitStep | AssertionStep] = Field(default_factory=list)
    cleanup: dict | None = None
    # --- Task 3.1 / P3-09（设计 6.2）：Candidate 溯源，追加在末尾（既有键序不动）---
    status: Literal["CANDIDATE"] | None = None
    generated_by: str | None = None
    generation_evidence: GenerationEvidence | None = None


def parse_testcase_dict(data: dict) -> TestCase:
    """schema_version 分发 parser；缺失/未知版本明确报错（设计 6.3 末行）。"""
    version = data.get("schema_version")
    if version is None:
        raise ValueError("missing required key 'schema_version'")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(
            f"unsupported schema_version: {version!r} "
            f"(supported: {SUPPORTED_SCHEMA_VERSIONS})"
        )
    return TestCase.model_validate(data)
