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
能挡住「配置拼错」：**未知键、错型（严格类型，不靠 lax 强转）、负值、权重表键集
不符一律 fail-loud**，静默生效的配置错误是最难查的一类回归。

⚠️ 注意「**默认路径不存在 → 内置默认值**」（矩阵 #4 的回归底线）与「**显式给了
路径却不存在 → 报错**」是**两件事**，别共用一个出口——后者会让人以为「我在用配置
文件，其实没有」。详见 `load_policy` 的入参语义表。

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
from typing import Annotated, Any

import yaml
from pydantic import (BaseModel, ConfigDict, Field, ValidationError,
                      field_validator)

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

# 首次入库的配置文件路径（CLI 的默认值；单点，别在别处再写字面量）。
# ⚠️ **相对 CWD**——与 `suites`（`--suites-root` 默认）`out/trace.db`（`--db`
# 默认）同款，是本项目的既有约定（P1 起「用旗标/相对路径显式给」）；**不是**
# 相对仓库根。所以「从别的目录跑 `mta`」会读到那个目录下的 `config/policy.yaml`
# ——这正是「用户在自己项目里放一份配置」想要的行为（review_p3_task12 P3-2 要求
# 把这个事实写出来，而不是悄悄锚到 `__file__`）。
DEFAULT_POLICY_PATH = Path("config/policy.yaml")

# 「调用方没给路径」的哨兵（review_p3_task12 P3-2）。**不能**用 `None` 兼作默认
# 值：`None` 的语义是「显式要求内置默认值、别读文件」，而「没给」应当去读
# `DEFAULT_POLICY_PATH`（读不到才回落默认值——那是矩阵 #4 的回归底线）。
_UNSET = object()

# 非负整数（权重表的值域闸门；见 `PolicyConfig` 的两个权重字段）
_NonNegInt = Annotated[int, Field(ge=0)]

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
    """E 系纪律的载体：未知键拒绝 + 不可变 + **严格类型**。

    - `extra="forbid"`：未知键 fail-loud——拼错的键不会静默生效；
    - `frozen=True`：配置读进来就不该被改（注意是**浅冻结**：属性重绑被拒，
      但 `dict` 字段的内容仍可改；`default_factory` 保证实例间与模块常量
      **不共享**，所以改不到真值源）；
    - `strict=True`（review_p3_task12 P3-4）：pydantic 默认 lax 会把
      `"100"→100`、`3.0→3`、**`true→1`**、`"true"→True` 静默强转——`max_steps:
      true` 会让预算从 100 静默变成 **1 步**，即「配置错误表现为功能退化而不是
      报错」。既然 F7 的精神是「启动时校验」，类型也必须严。
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


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
    # 值域闸门 `_NonNegInt`（review_p3_task12 P3-1）：负权重会算出负分，直接污染
    # 「归因是否达标」（F10）与「先做哪个」（§5.2）。标量段一直有 `ge=0`，权重表
    # 漏了——而 docstring 与 yaml 头注释都声称「负值 fail-loud」。
    planner_weights: dict[str, _NonNegInt] = Field(
        default_factory=lambda: dict(PLANNER_WEIGHTS))
    diagnosis_evidence_weights: dict[str, _NonNegInt] = Field(
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


def load_policy(path: str | Path | None | object = _UNSET) -> PolicyConfig:
    """读 policy 文件 → `PolicyConfig`。四种入参语义（review_p3_task12 P3-2）：

    | 入参 | 行为 |
    |---|---|
    | 不给（`_UNSET`） | 读 `DEFAULT_POLICY_PATH`（**相对 CWD**）；读不到 → 内置默认值 |
    | `None` | **显式要求内置默认值**，不读任何文件 |
    | 存在的文件 | 读它（未知键 / 错型 / 负值 / 权重键集不符 → `PolicyConfigError`） |
    | 显式给了却不存在 | **`PolicyConfigError`**（fail-loud） |

    「不给 → 读不到就回落默认值」是矩阵 #4 的回归底线（policy.yaml 是 P3 新增层，
    P2 的既有命令不该因为它不在而行为改变）；而「**显式给了却不存在**必须报错」是
    另一回事——那说明路径拼错或文件被删，静默退回默认值会让人以为「我在用配置
    文件，其实没有」。这两件事此前共用一个出口（review_p3_task12 P3-2）。

    不做深合并：文件就是整份配置（省略的段回落到该段的默认值）。
    """
    explicit = path is not _UNSET
    if path is _UNSET:
        path = DEFAULT_POLICY_PATH
    if path is None:
        return PolicyConfig()
    p = Path(path)
    if not p.is_file():
        if not explicit:
            return PolicyConfig()            # 默认路径不存在 = 回归底线
        raise PolicyConfigError(
            f"policy 文件不存在：{p}（显式指定的路径必须存在——拼错的路径静默"
            f"退回内置默认值会让人以为「我在用配置文件，其实没有」；想用内置"
            f"默认值请传 None）")
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
