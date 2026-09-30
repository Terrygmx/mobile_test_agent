"""guard — 10.1 Executor 层安全闸（与 LLM 无关，对所有动作生效）。

**Guard 不受 testcase 字段影响，也不受 LLM 输出影响**（10.1 末句）。这是
本模块存在的全部意义：风险等级由 policy 从确定性信息推导（4），任何人
（包括用例作者、包括 LLM）都不能在这里开后门。

三条规则（10.1）：
  1. `effective_risk == CRITICAL` → SECURITY_BLOCKED，唯一例外是
     `env.kind == sandbox` 且该元素显式 `allow_in_sandbox: true`；
  2. `env.kind == production` → 拒绝所有 HIGH/CRITICAL，且启动时需显式
     `--allow-production`（没给 flag 则连 LOW 都不跑）；
  3. `policy.yaml` 的 `blocked_targets`（Screen/Element/Action 模式）直接拦。

`Guard.check` 是纯函数式判定（无副作用、无 IO），可脱离设备单测（H18）。
"""

from __future__ import annotations

import enum
import fnmatch
from dataclasses import dataclass

from testcase.schema import Risk


class EnvKind(str, enum.Enum):
    SANDBOX = "sandbox"
    STAGING = "staging"
    PRODUCTION = "production"


@dataclass(frozen=True)
class GuardContext:
    """Guard 的判定输入。**只能由确定性信息填充**。

    刻意没有 testcase 覆盖入口（`risk_override` 之类）——10.1 明文要求
    Guard 不受 testcase 字段影响；测试 `test_guard_ignores_testcase_level_
    override` 钉住这一点。
    """

    risk: Risk
    screen_id: str
    element_id: str
    action: str
    data_class: str = "NORMAL"


@dataclass(frozen=True)
class BlockedTarget:
    """`blocked_targets` 一条：Screen / Element / Action 模式。

    语法 `kind:pattern`，无前缀默认按 element（裸名最常见）。
    模式用 fnmatch（`delete_*` / `Pay*`）。
    """

    kind: str          # screen | element | action
    pattern: str

    @classmethod
    def parse(cls, text: str) -> "BlockedTarget":
        raw = text.strip()
        if ":" in raw:
            kind, _, pattern = raw.partition(":")
            kind, pattern = kind.strip().lower(), pattern.strip()
        else:
            kind, pattern = "element", raw
        if kind not in ("screen", "element", "action"):
            # fail-loud：写错 kind 的策略条目不该被静默当 element 处理
            raise ValueError(
                f"invalid blocked_target kind: {kind!r} "
                f"(expected screen/element/action)")
        return cls(kind=kind, pattern=pattern)

    def matches(self, ctx: GuardContext) -> bool:
        subject = {
            "screen": ctx.screen_id,
            "element": ctx.element_id,
            "action": ctx.action,
        }[self.kind]
        return fnmatch.fnmatch(str(subject).lower(), self.pattern.lower())


class GuardViolation(Exception):
    """违反安全策略 → SECURITY_BLOCKED。**不可被 testcase 或 LLM 绕过**。"""

    def __init__(self, reason: str, risk: Risk, ctx: GuardContext,
                 failure_type: str = "SECURITY_BLOCKED"):
        super().__init__(reason)
        self.reason = reason
        self.risk = risk
        self.context = ctx
        self.failure_type = failure_type


@dataclass
class Guard:
    """安全闸。`check(ctx)` 放行则返回 None，拦则抛 GuardViolation。

    `allow_in_sandbox`：元素 id 集合，只在 sandbox 生效（10.1 唯一例外）。
    `allow_production`：对应 CLI 的 `--allow-production`；它是「允许在
    production 环境跑这套用例」的总闸，**不代表 HIGH/CRITICAL 放行**——
    HIGH/CRITICAL 按各自规则仍被拦。
    """

    env_kind: EnvKind = EnvKind.SANDBOX
    allow_in_sandbox: frozenset[str] = frozenset()
    allow_production: bool = False
    blocked_targets: tuple[BlockedTarget, ...] = ()

    def __init__(self, env_kind=EnvKind.SANDBOX,
                 allow_in_sandbox=frozenset(), allow_production=False,
                 blocked_targets=()):
        self.env_kind = EnvKind(env_kind)
        self.allow_in_sandbox = frozenset(allow_in_sandbox)
        self.allow_production = bool(allow_production)
        self.blocked_targets = tuple(blocked_targets)

    def check(self, ctx: GuardContext) -> None:
        # 规则 3：blocked_targets 优先（显式黑名单不该被风险等级稀释）
        for target in self.blocked_targets:
            if target.matches(ctx):
                raise GuardViolation(
                    f"blocked_targets: {target.kind} 模式 "
                    f"{target.pattern!r} 命中 "
                    f"({target.kind}={self._subject(target, ctx)!r})",
                    risk=ctx.risk, ctx=ctx)

        # 规则 2：production 总闸
        if self.env_kind is EnvKind.PRODUCTION and not self.allow_production:
            raise GuardViolation(
                "production 环境需显式 --allow-production 才允许运行"
                "（10.1）",
                risk=ctx.risk, ctx=ctx)

        # 规则 1：CRITICAL
        if ctx.risk is Risk.CRITICAL:
            sandbox_exception = (
                self.env_kind is EnvKind.SANDBOX
                and ctx.element_id in self.allow_in_sandbox)
            if not sandbox_exception:
                raise GuardViolation(
                    f"CRITICAL 风险动作被拦截（effective_risk={ctx.risk.name}，"
                    f"env={self.env_kind.value}）；唯一例外是 sandbox 环境"
                    f"且元素显式 allow_in_sandbox=true"
                    f"（当前 sandbox 例外={sandbox_exception}）",
                    risk=ctx.risk, ctx=ctx)

        # 规则 2：production 拒 HIGH/CRITICAL（CRITICAL 已在上面对 CRITICAL
        # 判过，但 sandbox 例外不该把 production 放行——上面 sandbox_exception
        # 已限定 env 是 sandbox，故此处 HIGH 拦截对 production 必然生效）
        if self.env_kind is EnvKind.PRODUCTION and ctx.risk >= Risk.HIGH:
            raise GuardViolation(
                f"production 环境拒绝 {ctx.risk.name} 风险动作（10.1）",
                risk=ctx.risk, ctx=ctx)

    @staticmethod
    def _subject(target: BlockedTarget, ctx: GuardContext) -> str:
        return {
            "screen": ctx.screen_id,
            "element": ctx.element_id,
            "action": ctx.action,
        }[target.kind]


def blocked_target_from(raw: str) -> BlockedTarget:
    return BlockedTarget.parse(raw)
