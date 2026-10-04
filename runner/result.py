"""result — 8 章结果模型 + 退出码（纯函数，H18）。

三块：
  1. `compute_exit_code`（8.4）：码值与优先级 `3 > 2 > 4 > 1 > 5`；
  2. `summarize_statuses`（8.3）：PASS Rate = PASS/TOTAL（**不含
     RECOVERED**，8.3 明令禁止 `(PASS+RECOVERED)/TOTAL`）、Recovery Rate；
  3. `junit_status_for`（8.5）：RECOVERED → failure[type=RECOVERED_NEEDS_REVIEW]。

Task 2.4 追加第四块：`recovered_kind`（设计 10 节）——RECOVERED 的
**明细**分类（LLM / EXPERIENCE / ASSERTION_TARGET / …）。它不改聚合与
退出码，只让报告能回答「这次是 LLM 救的还是经验救的」。

终态优先级**不在这里重写**：复用 `tracer.storage.aggregate_status`
（8.1 的唯一实现，R14-5 已把它修成顺序无关）。两处各写一份优先级，
改一处忘另一处就是 review 里 R13-1 那种「双源分叉」。

H5（硬约束）：RECOVERED ≠ PASS——不计入通过率、退出码非 0、必须人工确认
后才能进 Repository。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from tracer.storage import (
    TESTCASE_STATUSES,
    aggregate_status,
    pass_rate,
    recovery_rate,
)

ATTRIBUTIONS = {"UNTRIAGED", "APP_DEFECT", "AUTOMATION_DEFECT",
                "ENVIRONMENT_DEFECT", "TEST_DATA_DEFECT",
                "INFRASTRUCTURE_DEFECT"}


class ExitCode(enum.IntEnum):
    """8.4 退出码。码值是**外部契约**（CI 依赖），不得重排。"""

    OK = 0              # 全部 PASS
    FAIL = 1            # 存在 FAIL
    INFRA = 2           # 存在 INFRA_FAILURE / ENVIRONMENT_FAILURE
    PREFLIGHT = 3       # 配置 / lint / metadata 不匹配等前置错误
    BLOCKED = 4         # 安全策略拦截
    RECOVERED = 5       # 无 FAIL，但存在 RECOVERED（需人工确认）


def compute_exit_code(statuses, preflight_error: bool = False) -> int:
    """8.4 退出码。并存时 `3 > 2 > 4 > 1 > 5`。

    实现方式是「取集合里优先级最高的一个」而非 if 链——if 链的顺序即
    语义，改动时容易漏改；集合判定 + 显式优先级表更难写错。
    """
    if preflight_error:
        return int(ExitCode.PREFLIGHT)
    present = {s for s in statuses if s}
    if not present:
        return int(ExitCode.OK)
    # 优先级表：键 = 触发该码的状态集合
    for code, triggers in (
        (ExitCode.INFRA, {"INFRA_FAILURE", "ENVIRONMENT_FAILURE"}),
        (ExitCode.BLOCKED, {"BLOCKED"}),
        (ExitCode.FAIL, {"FAIL"}),
        (ExitCode.RECOVERED, {"RECOVERED"}),
    ):
        if present & triggers:
            return int(code)
    # 全 PASS（含 ABORTED/SKIPPED 不在表里但也不构成失败信号）
    return int(ExitCode.OK)


@dataclass(frozen=True)
class JUnitStatus:
    status: str                    # pass | failure | error | skipped
    type: str | None = None
    message: str | None = None


_JUNIT_MAP = {
    "PASS": ("pass", None),
    "FAIL": ("failure", None),
    # 8.5：RECOVERED 必须让 CI 看见 → failure + 专用 type
    "RECOVERED": ("failure", "RECOVERED_NEEDS_REVIEW"),
    "INFRA_FAILURE": ("error", None),
    "ENVIRONMENT_FAILURE": ("error", None),
    "BLOCKED": ("error", None),
    "ABORTED": ("error", None),
    "SKIPPED": ("skipped", None),
}


def junit_status_for(status: str, failure_type: str | None = None
                     ) -> JUnitStatus:
    """8.5 JUnit 映射。RECOVERED 必带 message（H5：需人工确认）。

    Task 2.6：`type` 一律带 failure_type——CI 面上 `<failure type=...>`
    是最廉价的症状入口，只给 RECOVERED 配 type 会让普通 FAIL 丢症状。
    """
    if status not in _JUNIT_MAP:
        raise ValueError(f"unknown testcase status: {status!r}")
    jstatus, jtype = _JUNIT_MAP[status]
    message = None
    if status == "RECOVERED":
        message = (f"RECOVERED 需人工确认后才能进入 Repository"
                   + (f"（failure_type={failure_type}）" if failure_type else ""))
    elif failure_type:
        message = failure_type
    if jtype is None:
        jtype = failure_type
    return JUnitStatus(status=jstatus, type=jtype, message=message)


# --- 设计 10 节：RECOVERED 的明细分类（P2 细分，Task 2.4） ----------------

# 机制 → 分类标签。设计 10 只列了 P2 新出现的三类（LLM / EXPERIENCE /
# ASSERTION_TARGET）；P1 的确定性机制按同一条命名规则给出，避免「有些
# RECOVERED 有分类、有些没有」的半截状态（报告里 None 会被读成「未知」）。
RECOVERED_KIND_BY_MECHANISM = {
    "llm": "RECOVERED_LLM",
    "experience": "RECOVERED_EXPERIENCE",
    "settle_retry": "RECOVERED_SETTLE_RETRY",
    "run_memo": "RECOVERED_RUN_MEMO",
    "postcondition": "RECOVERED_POSTCONDITION",
    "local_reconcile": "RECOVERED_DETERMINISTIC_CANDIDATE",
}

# 断言目标定位漂移（P1 7.3 / 设计 10 第三类）：只允许恢复**定位**，不允许
# 改期望值（E3）。它压过机制分类——「Experience 命中的断言漂移」记
# RECOVERED_ASSERTION_TARGET 而不是 RECOVERED_EXPERIENCE，因为对使用者
# 有意义的问题是「改的是定位还是期望值」。
ASSERTION_TARGET_CONTEXT = "assertion_target"


def recovered_kind(mechanism: str | None,
                   context: str | None = None) -> str | None:
    """设计 10 节：`RECOVERED` 的明细分类（单一真值源）。

    聚合口径与退出码**不变**（仍 RECOVERED ≠ PASS）——分类只进明细，
    回答「这次是 LLM 救的还是经验救的」。

    `context == "assertion_target"` → 第三类（断言目标定位漂移）。
    未知机制 → `RECOVERED_<大写机制名>`（不返回 None：静默的空分类会被
    读成「没恢复」，而调用点本来就是「已恢复」分支）。
    """
    if context == ASSERTION_TARGET_CONTEXT:
        return "RECOVERED_ASSERTION_TARGET"
    if not mechanism:
        return None
    return RECOVERED_KIND_BY_MECHANISM.get(mechanism,
                                           f"RECOVERED_{mechanism.upper()}")


@dataclass(frozen=True)
class RunSummary:
    """8.3 汇总。`pass_rate` 绝不含 RECOVERED。"""

    counts: dict
    total: int
    pass_rate: float
    recovery_rate: float
    worst_status: str | None
    llm_calls: int = 0
    total_attempts: int = 0   # 含 WDA 故障重跑的尝试数（14.2 testcase_runs.attempt）

    @property
    def fail_count(self) -> int:
        return self.counts.get("FAIL", 0)

    @property
    def recovered_count(self) -> int:
        return self.counts.get("RECOVERED", 0)


def summarize_statuses(statuses, attempts: int = 0,
                       llm_calls: int | None = None) -> RunSummary:
    """8.3 汇总。`worst_status` 复用 tracer.storage.aggregate_status（8.1）。

    `llm_calls` 语义（review P3-8 消歧）：**LLM 实际调用次数**。
    原实现 `llm_calls=llm_calls or attempts` 把两个语义无关的参数
    （调用次数 / 用例尝试数）写成互为 fallback 的别名——读代码的人会以为
    没传 llm_calls 就等于「每条用例调一次 LLM」，那是个危险的默认（会把
    调用数虚报成用例数，Report 的 `LLM Invocation Rate` 直接失真）。

    现在：`llm_calls=None` 时**不猜**，RunSummary.llm_calls 保持 0，并要求
    真实调用方显式传 `llm_calls=`。`attempts` 只用于 metadata（总尝试数），
    不参与调用数推算。
    """
    counts: dict[str, int] = {}
    for s in statuses:
        if s is None:
            continue
        if s not in TESTCASE_STATUSES and s not in ("RUNNING", "PENDING"):
            raise ValueError(f"unknown testcase status: {s!r}")
        counts[s] = counts.get(s, 0) + 1
    total = sum(counts.values())
    if llm_calls is None:
        # 无 LLM 的执行路径（--no-llm / P0 链路）→ 0 是**真实值**不是缺失
        llm_calls = 0
    return RunSummary(
        counts=counts,
        total=total,
        pass_rate=pass_rate(counts),
        recovery_rate=recovery_rate(counts),
        worst_status=aggregate_status(list(counts)) if counts else None,
        llm_calls=llm_calls,
        total_attempts=attempts,
    )


@dataclass
class TestcaseResult:
    """单条用例结果。`duration_ms=None` 起步——0 会被当「瞬间完成」的假数据
    （与 tracer.storage._duration_ms 同纪律）。"""

    testcase_id: str
    status: str
    attempt: int = 1
    failure_type: str | None = None
    failure_attribution: str = "UNTRIAGED"
    failure_phase: str | None = None
    cleanup_status: str | None = None
    duration_ms: int | None = None
    non_idempotent_dispatched: bool = False
    detail: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.status not in TESTCASE_STATUSES:
            raise ValueError(
                f"unknown testcase status: {self.status!r} "
                f"(allowed: {sorted(TESTCASE_STATUSES)})")
        if self.failure_attribution not in ATTRIBUTIONS:
            raise ValueError(
                f"invalid failure_attribution: {self.failure_attribution!r}")

    @property
    def junit(self) -> JUnitStatus:
        return junit_status_for(self.status, self.failure_type)


# 类名以 Test 开头会被 pytest 误当测试类收集（与 TestcaseRunner 同款处理）
TestcaseResult.__test__ = False


@dataclass
class RunResult:
    """suite 级结果。`exit_code` 是派生属性，不存字段——避免与 add() 后的
    实际内容脱节（存字段就得记得每次同步，那是 bug 温床）。"""

    run_id: str
    suite: str | None = None
    preflight_error: str | None = None
    results: list[TestcaseResult] = field(default_factory=list)

    def add(self, result: TestcaseResult) -> None:
        self.results.append(result)

    @property
    def statuses(self) -> list[str]:
        return [r.status for r in self.results]

    @property
    def summary(self) -> RunSummary:
        return summarize_statuses(self.statuses,
                                  llm_calls=sum(
                                      r.detail.get("llm_calls", 0)
                                      for r in self.results))

    @property
    def exit_code(self) -> int:
        return compute_exit_code(self.statuses,
                                 preflight_error=bool(self.preflight_error))

    @property
    def passed(self) -> bool:
        return self.exit_code == int(ExitCode.OK)
