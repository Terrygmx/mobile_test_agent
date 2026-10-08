"""policy_config.py — P3 环境与预算约束的集中配置（设计 §7.2/§7.4/§8.3/§10；Task 1.2 / P3-02）。

设计 §7.4（F7）原文：

```text
Exploration 默认只能: Simulator + UITest Build + env.kind == sandbox
真机: 只运行已审核的 Test Plan，不运行自由探索
production: Autonomous Agent 整体不可用（policy.yaml 启动时校验，不是运行时才拦）
```

所以本模块是**启动期**的唯一配置入口：自主命令（`plan`/`generate`/`explore`/
`diagnose`/`agent`）在动手之前先 `load_policy` + `cli.main._check_autonomous_env`，
过了才继续。「启动时校验而不是运行时才拦」是 F7 的**核心**——所以这里的判据要
能挡住「配置拼错」：**未知键、错型、负预算一律 fail-loud**，静默生效的配置错误
是最难查的一类回归。

## 三处「单一真值源」

| 值 | 真值源 | 设计出处 |
|---|---|---|
| 探索四维预算 | `EXPLORATION_BUDGET` | §7.2（F11） |
| 归因证据权重 | `DIAGNOSIS_EVIDENCE_WEIGHTS` | §8.3（原文 6 键） |
| 评分权重（初版） | `PLANNER_WEIGHTS` | §5.2（**设计未给数值**，见下） |

⚠️ **`PLANNER_WEIGHTS` 的数值不在设计里**：设计 §5.2 只说「评分公式和权重是初版，
需在真实数据上校准」，没有给数。plan Task 1.2 要求本文件先落一段
`planner_weights`（M2 初版），故这里取一组**可解释的初值**（impact 3 / history 2 /
risk 5）并在 `config/policy.yaml` 里标注「初版待校准」——**不假装精确**（沿用 P2
验证阈值的态度）。Task 2.2 的 `priority_score` 消费它；校准后回填设计。

## 为什么权重表要校验**键集**

`diagnosis_evidence_weights` / `planner_weights` 是**整表替换**（YAML 不做深合并），
少写一个键不会立刻报错——它会一路走到 `score_evidence` / `priority_score` 里变成
运行时 `KeyError`，离现场很远。既然 F7 的精神是「启动时校验」，这里就把它挡在
加载期：键集必须与真值源**完全一致**（漏/多都在报错里点名）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

__all__ = [
    "DEFAULT_POLICY_PATH",
    "DIAGNOSIS_EVIDENCE_WEIGHTS",
    "EXPLORATION_BUDGET",
    "PLANNER_WEIGHTS",
    "AutonomousPolicy",
    "EscalationPolicy",
    "EvidencePolicy",
    "ExplorationPolicy",
    "PolicyConfig",
    "PolicyConfigError",
    "load_policy",
]

# 首次入库的配置文件路径（CLI 的默认值；单点，别在别处再写字面量）
DEFAULT_POLICY_PATH = Path("config/policy.yaml")

# --- 三处真值源（默认值 = 设计初版值，逐字抄自设计，不四舍五入） ---------------

EXPLORATION_BUDGET: dict[str, int] = {
    # 设计 §7.2 原文（F11：超限即 *_BUDGET_EXCEEDED 并停止，不自动放宽重试）
    "max_steps": 100,
    "max_duration_seconds": 600,
    "max_llm_calls": 20,
    "max_repeated_state": 3,      # 复用 P2 screen_fingerprint 判定「重复状态」（F8）
}

# 设计 §8.3 原文（初版，需用真实 Bug 数据校准，不是精确值）
DIAGNOSIS_EVIDENCE_WEIGHTS: dict[str, int] = {
    "crash_detected": 5,
    "reproducible_3_times": 5,
    "source_change_correlated": 3,
    "same_issue_multiple_tests": 3,
    "backend_evidence": 2,
    "llm_hypothesis_only": 1,
}

# 设计 §5.2 只给了公式的三个分量（impact / history / risk），**没给数值**；
# 见模块 docstring 的说明。初版待 M2 校准。
PLANNER_WEIGHTS: dict[str, int] = {
    "impact": 3,
    "history": 2,
    "risk": 5,
}

# 设计 §9.2：ESCALATED 触发条件之一 = 连续 N 次决策被 Guard 拦截
GUARD_BLOCKS_BEFORE_ESCALATE = 3

# plan Task 1.2 定档（M5 初版）：证据分 ≥ 阈值才建议归因，否则保持 UNTRIAGED（F10）
ATTRIBUTION_THRESHOLD = 10


class PolicyConfigError(ValueError):
    """policy 文件不可用（读不出 / 不是 YAML / 顶层不是对象 / 内容不合法）。

    刻意**不**吞 pydantic 的 `ValidationError` 细节——把它连原样带在消息里，
    并且**补上文件路径**（否则「哪个文件坏了」只能靠调用栈猜）。
    """


class _Frozen(BaseModel):
    """E 系纪律的载体：未知键拒绝 + 不可变（配置读进来就不该被改）。

    `extra="forbid"` 是「未知键 fail-loud」的落点——拼错的键不会静默生效。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_exact_keys(where: str, got: dict[str, Any],
                        source: dict[str, Any]) -> None:
    """`got` 的键集必须与真值源 `source` 完全一致（漏/多都点名）。"""
    missing = sorted(set(source) - set(got))
    extra = sorted(set(got) - set(source))
    if missing or extra:
        detail = []
        if missing:
            detail.append(f"缺少 {missing}")
        if extra:
            detail.append(f"多出 {extra}")
        raise ValueError(
            f"{where} 的键集必须与真值源完全一致（{'；'.join(detail)}）；"
            f"期望 {sorted(source)}")


class AutonomousPolicy(_Frozen):
    """设计 §7.4（F7）：自主能力的总开关。"""

    enabled: bool = True


class ExplorationPolicy(_Frozen):
    """设计 §7.2（F11）：每一步行动的四维预算。"""

    max_steps: int = Field(default=EXPLORATION_BUDGET["max_steps"], ge=0)
    max_duration_seconds: int = Field(
        default=EXPLORATION_BUDGET["max_duration_seconds"], ge=0)
    max_llm_calls: int = Field(default=EXPLORATION_BUDGET["max_llm_calls"], ge=0)
    max_repeated_state: int = Field(
        default=EXPLORATION_BUDGET["max_repeated_state"], ge=0)


class EscalationPolicy(_Frozen):
    """设计 §9.2：连续 N 次决策被 Guard 拦截 → `ESCALATED`。"""

    guard_blocks_before_escalate: int = Field(
        default=GUARD_BLOCKS_BEFORE_ESCALATE, ge=0)


class EvidencePolicy(_Frozen):
    """设计 §8.3 / F10：证据阈值。"""

    attribution_threshold: int = Field(default=ATTRIBUTION_THRESHOLD, ge=0)


class PolicyConfig(_Frozen):
    """P3 的集中配置。所有字段都有设计初版默认值——**文件缺失也能跑**（矩阵 #4）。

    「文件缺失 → 全默认值」不是宽容，是**回归底线**：policy.yaml 是 P3 新增层，
    P2 的既有命令不该因为它不在而行为改变。
    """

    autonomous: AutonomousPolicy = Field(default_factory=AutonomousPolicy)
    exploration: ExplorationPolicy = Field(default_factory=ExplorationPolicy)
    escalation: EscalationPolicy = Field(default_factory=EscalationPolicy)
    planner_weights: dict[str, int] = Field(
        default_factory=lambda: dict(PLANNER_WEIGHTS))
    diagnosis_evidence_weights: dict[str, int] = Field(
        default_factory=lambda: dict(DIAGNOSIS_EVIDENCE_WEIGHTS))
    evidence: EvidencePolicy = Field(default_factory=EvidencePolicy)

    @field_validator("planner_weights")
    @classmethod
    def _planner_weights_keys(cls, v: dict[str, int]) -> dict[str, int]:
        _require_exact_keys("planner_weights", v, PLANNER_WEIGHTS)
        return v

    @field_validator("diagnosis_evidence_weights")
    @classmethod
    def _evidence_weights_keys(cls, v: dict[str, int]) -> dict[str, int]:
        _require_exact_keys("diagnosis_evidence_weights", v,
                            DIAGNOSIS_EVIDENCE_WEIGHTS)
        return v


def load_policy(path: str | Path | None = DEFAULT_POLICY_PATH) -> PolicyConfig:
    """读 policy 文件 → `PolicyConfig`。

    - `None` / 路径不存在 → **全默认值**（= 设计初版值，P2 行为不变，矩阵 #4）；
    - 读不出 / 不是合法 YAML / 顶层不是对象 / 未知键 / 错型 / 负预算 →
      `PolicyConfigError`（**fail-loud**：配置拼错静默生效是最难查的一类回归）。

    不做深合并：文件就是整份配置（省略的段回落到该段的默认值）。
    """
    if path is None:
        return PolicyConfig()
    p = Path(path)
    if not p.is_file():
        return PolicyConfig()
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except OSError as e:
        raise PolicyConfigError(f"policy 文件不可读：{p}（{e}）") from e
    except yaml.YAMLError as e:
        raise PolicyConfigError(f"policy 文件不是合法 YAML：{p}（{e}）") from e
    if raw is None:                      # 空文件 = 全默认（不是错误）
        raw = {}
    if not isinstance(raw, dict):
        raise PolicyConfigError(
            f"policy 顶层必须是对象（段 → 值）：{p}（实得 "
            f"{type(raw).__name__}）")
    try:
        return PolicyConfig.model_validate(raw)
    except ValidationError as e:
        raise PolicyConfigError(f"policy 内容不合法：{p}\n{e}") from e
