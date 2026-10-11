"""state.py — 探索预算（设计 7.2 / F11；Task 4.3 / P3-16）。

`ExplorationBudget` 是四维预算的**值对象**：默认值来自
`agents/policy_config.py` 的真值源 `EXPLORATION_BUDGET`（设计 §7.2 初版）；
`--budget <file>` 同结构覆盖——**校验复用 `ExplorationPolicy` 同一模型**
（未知键 / 错型（strict，不 lax 强转）/ 负值 fail-loud），不写第二份校验
（同一事实两处字面量必漂移）。

超限语义（F11）：任一维耗尽即 `*_BUDGET_EXCEEDED` 停止，**不自动放宽重试**
——检查点在 `explorer.agent.Explorer.run()` 的每轮顶部；终态词表：
- `max_steps`        → STEPS_BUDGET_EXCEEDED
- `max_duration_seconds` → DURATION_BUDGET_EXCEEDED
- `max_llm_calls`    → LLM_BUDGET_EXCEEDED（底层是 P1 `LLMBudget`，F4）
- `max_repeated_state` → **经 loop detector 执行，终态 `LOOP_DETECTED`**
  （矩阵 #12 的口径：这一维不是第四种 `*_BUDGET_EXCEEDED`）

`DEFAULT_LOOP_WINDOW`：设计 7.3 的 `is_loop(window)` 不在四维预算里，
policy 也没有这个键——初版常量在此**单点**定义（校准时改这一处）。
"""
from __future__ import annotations

from dataclasses import dataclass

import yaml
from agents.policy_config import ExplorationPolicy, PolicyConfigError

__all__ = ["DEFAULT_LOOP_WINDOW", "ExplorationBudget"]

# is_loop 的回看窗口初版（覆盖 ping-pong 交替：max_repeat=3 时窗口 3 检不出
# H-S-H-S-H，窗口取 max_repeat 的 3 倍余量；真实运行后校准）。
DEFAULT_LOOP_WINDOW = 10

_BUDGET_FIELDS = ("max_steps", "max_duration_seconds", "max_llm_calls",
                  "max_repeated_state")


@dataclass(frozen=True)
class ExplorationBudget:
    """设计 §7.2 的四维预算（不可变值对象）。"""

    max_steps: int
    max_duration_seconds: int
    max_llm_calls: int
    max_repeated_state: int

    def __post_init__(self) -> None:
        for f in _BUDGET_FIELDS:
            value = getattr(self, f)
            if not isinstance(value, int) or isinstance(value, bool) \
                    or value < 0:
                raise ValueError(
                    f"{f} 必须是非负 int，got {value!r}"
                    f"（bool/负值/浮点都是配置错误，fail-loud）")

    @classmethod
    def from_policy(cls, policy: ExplorationPolicy | None = None
                    ) -> "ExplorationBudget":
        """从 policy 的 exploration 段取值（缺省 = 真值源初版，无文件 IO）。"""
        p = policy or ExplorationPolicy()
        return cls(**{f: getattr(p, f) for f in _BUDGET_FIELDS})

    @classmethod
    def from_file(cls, path) -> "ExplorationBudget":
        """`--budget <file>`：YAML 顶层就是 exploration 段的同结构 dict。

        校验**复用** `ExplorationPolicy`（与 policy.yaml 同一模型）——
        未知键 / 错型 / 负值的报错口径与主配置完全一致。文件读不出 /
        顶层不是对象 / 校验不过 → `PolicyConfigError`（带路径，fail-loud）。
        """
        from pathlib import Path
        p = Path(path)
        if not p.is_file():
            raise PolicyConfigError(f"budget 文件不存在：{p}")
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        except OSError as e:
            raise PolicyConfigError(f"budget 文件不可读：{p}（{e}）") from e
        except yaml.YAMLError as e:
            raise PolicyConfigError(f"budget 文件不是合法 YAML：{p}（{e}）") from e
        if not isinstance(raw, dict):
            raise PolicyConfigError(
                f"budget 顶层必须是对象（四维键 → 值）：{p}（实得 "
                f"{type(raw).__name__}）")
        try:
            policy = ExplorationPolicy.model_validate(raw)
        except Exception as e:   # pydantic ValidationError → 带路径的域错误
            raise PolicyConfigError(f"budget 内容不合法：{p}\n{e}") from e
        return cls.from_policy(policy)
