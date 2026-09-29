"""mta lint 静态检查（设计 6.4，Task 1.3 / P1-01）。

检查项（失败 → 退出码 3 的前置门）：
  - schema 非法（YAML dict 过不了 0.1 strict parser）→ ERROR；
  - target 解析不了 / 短名歧义（委托 Repository.lint，4.1 不允许运行时猜）→ ERROR；
  - `${VAR}` 无法由 SecretProvider 解析（H9）→ ERROR；
  - 用例含 sleep（H11：implicit wait=0）→ ERROR；
  - wait `active` 用在 element target → ERROR（schema 已拦，lint 对原始 dict 再拦一层）；
  - 非幂等步骤缺 postcondition → **WARNING**（仅提示，不影响退出码）；
  - 非幂等关键词命中且未显式声明 → WARNING 提示显式声明（H17 / 7.4）。

`lint()` 接受两种输入：原始 dict（CLI 加载后先 lint schema 层）与
Pydantic TestCase（严格解析后的常态路径）。
LintIssue/Severity 单一类型定义在 repository.resolver（Task 1.2 引入），
本模块复用——Repository 侧与 testcase 侧产出同构结果。
ERROR → `mta lint` 退出码 3；仅 WARNING 或干净 → 0。
"""
from __future__ import annotations

import re
from typing import Any, Protocol

from pydantic import ValidationError

from repository.resolver import LintIssue, Repository, Severity
from testcase.schema import TestCase, parse_testcase_dict

SECRET_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# 7.4 非幂等关键词启发式（作用于 element id / label / accessibility_id）
NON_IDEMPOTENT_KEYWORDS = (
    "submit", "pay", "payment", "order", "checkout", "purchase",
    "delete", "remove", "refund", "transfer", "withdraw", "confirm_pay",
)


class SecretProviderProtocol(Protocol):
    """H9：密钥只经 SecretProvider；lint 只探测可解析性，不取值。"""

    def get(self, key: str) -> str: ...


def _check_sleep(steps: list[dict], tc_id: str, issues: list[LintIssue]) -> None:
    """H11：用例禁止 sleep（implicit wait=0；等待一律 wait_for 显式条件）。"""
    for idx, step in enumerate(steps):
        if step.get("action") == "sleep":
            issues.append(LintIssue(
                "sleep_forbidden",
                f"testcase {tc_id!r} step {idx}: action 'sleep' forbidden (H11); "
                f"use wait_for with explicit condition instead",
                tc_id, idx,
            ))


def _check_active_scope(steps: list[dict], tc_id: str, issues: list[LintIssue]) -> None:
    """6.2：`active` 仅限 screen target（对原始 dict 再拦一层）。"""
    for idx, step in enumerate(steps):
        wait = step.get("wait_for") if isinstance(step, dict) else None
        if not isinstance(wait, dict) or wait.get("condition") != "active":
            continue
        target = wait.get("target")
        # str 语法糖：screen:<Name> 是 screen，其余是 element（6.3 TargetRef 语法）
        if isinstance(target, str):
            is_element = not target.startswith("screen:")
        elif isinstance(target, dict):
            is_element = target.get("type") != "screen"
        else:
            is_element = True  # 缺 target 的 active 本身就不合法
        if is_element:
            issues.append(LintIssue(
                "active_on_element",
                f"testcase {tc_id!r} step {idx}: condition 'active' requires "
                f"screen target (6.2)",
                tc_id, idx,
            ))


def _check_secrets(
    steps: list[dict], tc_id: str, secrets: SecretProviderProtocol,
    issues: list[LintIssue],
) -> None:
    """H9/6.4：`${VAR}` 必须可由 SecretProvider 解析。"""
    for idx, step in enumerate(steps):
        value = step.get("value") if isinstance(step, dict) else None
        if not isinstance(value, str):
            continue
        for key in SECRET_REF.findall(value):
            try:
                secrets.get(key)
            except Exception:
                issues.append(LintIssue(
                    "unknown_secret",
                    f"testcase {tc_id!r} step {idx}: secret ${{{key}}} not "
                    f"resolvable by SecretProvider (H9)",
                    tc_id, idx,
                ))


def _check_postcondition(steps: list[dict], tc_id: str, issues: list[LintIssue]) -> None:
    """6.4/7.4：非幂等步骤缺 postcondition → WARNING（H7：动作发出后需可判定）。"""
    for idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        declared = step.get("idempotency")
        has_post = step.get("postcondition") is not None
        action = step.get("action")
        if action not in ("tap", "input"):
            continue
        # R7-2：设计 7.4「UNKNOWN 一律按 NON_IDEMPOTENT 处理」——显式声明
        # UNKNOWN 且缺 postcondition 同样要警告（执行器无法判定动作结果）。
        if declared in ("NON_IDEMPOTENT", "UNKNOWN") and not has_post:
            issues.append(LintIssue(
                "missing_postcondition",
                f"testcase {tc_id!r} step {idx}: {declared} step "
                f"(target={step.get('target')!r}) lacks postcondition (H7/7.4); "
                f"add one so the executor can determine the outcome after dispatch",
                tc_id, idx, Severity.WARNING,
            ))
        elif declared is None:
            # H17：命中非幂等关键词启发式且未显式声明 → 提示显式声明
            target = step.get("target")
            target_str = (
                target.get("id") if isinstance(target, dict)
                else target if isinstance(target, str)
                else ""
            ) or ""
            if any(k in target_str.lower() for k in NON_IDEMPOTENT_KEYWORDS):
                issues.append(LintIssue(
                    "declare_idempotency",
                    f"testcase {tc_id!r} step {idx}: target {target_str!r} matches "
                    f"non-idempotent keyword heuristic; declare idempotency "
                    f"explicitly (H17)",
                    tc_id, idx, Severity.WARNING,
                ))


def _schema_error_issue(data: dict, exc: Exception) -> LintIssue:
    """schema 层失败 → schema_invalid（ERROR）。"""
    if isinstance(exc, ValidationError):
        detail = (f"{exc.error_count()} error(s): "
                  f"{'; '.join(str(e['msg']) for e in exc.errors()[:3])}")
    else:
        detail = str(exc)
    tc_id = data.get("id", "<unknown>") if isinstance(data, dict) else getattr(data, "id", "<unknown>")
    return LintIssue("schema_invalid",
                     f"testcase {tc_id!r} failed 0.1 schema: {detail}")


def _check_unconsumed_declarations(
    raw_steps: list[dict], tc_id: str, issues: list[LintIssue]
) -> None:
    """R10-2（fail-loud）：runner 尚未消费的声明报 ERROR，防止作者以为生效。

    - postcondition：当前最小 runner 不执行（M2 第一批实现 + 失败语义定型）；
    - cleanup：同上（M2 Gate「cleanup 失败终止套件」依赖它）。
    M2 实现翻正时，从 RUNNER_UNCONSUMED 中删除对应键即可。
    """
    RUNNER_UNCONSUMED = ("postcondition", "cleanup")
    for idx, step in enumerate(raw_steps):
        if not isinstance(step, dict) or step.get("action") is None:
            continue
        for key in RUNNER_UNCONSUMED:
            if step.get(key) is not None:
                issues.append(LintIssue(
                    "declaration_not_consumed",
                    f"testcase {tc_id!r} step {idx}: {key!r} is declared but the "
                    f"runner does not consume it yet (M2 implements postcondition/"
                    f"cleanup semantics); remove it or accept it is inert",
                    tc_id, idx, Severity.ERROR,
                ))


def lint(
    testcases: list[dict | TestCase],
    repo: Repository,
    secrets: SecretProviderProtocol,
) -> list[LintIssue]:
    """6.4 全量检查；输入可为原始 dict 或已解析 TestCase。"""
    issues: list[LintIssue] = []
    for data in testcases:
        if isinstance(data, TestCase):
            parsed: TestCase | None = data
            tc_id = data.id
            raw_steps = [s.model_dump() for s in data.steps]
        elif isinstance(data, dict):
            try:
                parsed = parse_testcase_dict(data)
            except (ValidationError, ValueError) as exc:
                issues.append(_schema_error_issue(data, exc))
                continue
            tc_id = parsed.id
            raw_steps = data.get("steps", []) or []
        else:
            issues.append(LintIssue(
                "schema_invalid",
                f"expected dict or TestCase, got {type(data).__name__}",
            ))
            continue

        # schema 合法：Repository target 检查（unknown/ambiguous，均 ERROR）
        issues.extend(repo.lint([parsed]))

        _check_sleep(raw_steps, tc_id, issues)
        _check_active_scope(raw_steps, tc_id, issues)
        _check_secrets(raw_steps, tc_id, secrets, issues)
        _check_postcondition(raw_steps, tc_id, issues)
        _check_unconsumed_declarations(raw_steps, tc_id, issues)
    return issues


def max_severity(issues: list[LintIssue]) -> Severity | None:
    """ERROR > WARNING；空 → None。"""
    if any(i.severity is Severity.ERROR for i in issues):
        return Severity.ERROR
    if any(i.severity is Severity.WARNING for i in issues):
        return Severity.WARNING
    return None


def lint_exit_code(
    testcases: list[dict | TestCase],
    repo: Repository,
    secrets: SecretProviderProtocol,
) -> int:
    """CLI 退出码（8.4）：有 ERROR → 3；仅 WARNING / 干净 → 0。"""
    return 3 if max_severity(lint(testcases, repo, secrets)) is Severity.ERROR else 0
