"""fingerprint.py — Test Fingerprint（设计 F8；Task 3.3 / P3-11）。

```python
test_fingerprint(tc: TestCase) -> str
```

设计 F8 原文：**Test Fingerprint = screen 序列 + action 序列 + assertion 的哈希，
用同一套哈希工具**（`source/hashing.py::stable_hash`），不新造一套相似度算法。

用途：Test Generator 的去重（设计 6.1「Test Fingerprint 命中已有用例 → SKIP」）。
**只做精确哈希相等**——不做相似度 / 模糊匹配 / 编辑距离。

## 三个分量

```
test_fingerprint(tc) = stable_hash([
    "screens:"    + "|".join(_screen_tokens(tc)),
    "actions:"    + "|".join(_action_tokens(tc)),
    "assertions:" + "|".join(_assertion_tokens(tc)),
])
```

**落点规则**（⚠️ **不是**「每个步骤恰好落入一个分量」——review_p3_task33 P3-2
实测证伪了原措辞），分量内保持步骤顺序：

| 步骤形态 | 落入 | token（`repr` 元组） |
|---|---|---|
| 动作步骤（launch_app / terminate_app / tap / input / swipe / back） | actions | `(action, "type:id", direction, value)` |
| **屏目标**的 `wait_for` / `assertion` | screens | `(kind, X, condition)` |
| **element 目标**的 `wait_for` / `assertion` | assertions | `(kind, condition, "type:id", expected)` |
| 动作的 `postcondition`（按**目标类型**） | screens（屏）/ assertions（element） | `(kind, …)` |

⚠️ **带 `postcondition` 的动作步骤落两个分量**：动作本身是行为 → `actions`；
`postcondition` 声明的到达点 / 观测是另一件事 → 按目标类型进 `screens` / `assertions`。
真语料 `login_again_001` / `logout_001` 各有 1 处此形态（`tap` + `postcondition screen:X`）。

token 用 `repr(元组)` 而非手工拼接：元组 repr 自带定界（括号 + 引号转义），
字段里含 `|` / `:` / `,` 也不会与相邻 token 粘连——「两个不同用例撞成同一
指纹」是去重最不该犯的错，所以这里不省这一步。

## 不进指纹的字段（「同语义不同命名 → 同指纹」的落点）

`id` / `name` / `suite` / `tags` / `precondition` / `cleanup` / `schema_version`，
以及 Candidate 溯源三字段（`status` / `generated_by` / `generation_evidence`）——
它们描述「这条用例**叫什么、从哪来**」，不描述「它**做什么**」。

## 已登记的边界（不假装完整）

- **分量间不保留交错顺序**：`[tap A, assert B]` 与 `[assert B, tap A]` 得到同一
  指纹（两个分量各自相同）。真语料里没有「把独立步骤对调」的用例；Task 3.4
  生成器若产生此类形态，再改为保留全局顺序（登记）。
- **input 的 `value` 进指纹**：不同测试数据（如「空用户名」与「空密码」两条
  负例）动作序列相同，不含 value 会被误判为重复而 SKIP 掉一条真用例。YAML 里的
  value 是占位符或字面量，**非密钥**（H9：密钥只经 `SecretProvider`，不进 YAML），
  故可入指纹。
- **`expected` 参与断言 token**：`text_equals "Welcome"` 与 `text_equals "Hi"`
  是两条不同的用例。
- ⚠️ **`precondition`（含 `reset`）不进指纹 → 真语料已有同指纹用例**（已拍板，
  见 `docs/p3_data_audit.md` Task 3.3 修订记录）：真语料 **20 例里有 2 对同指纹**：
  - `reset_logout_001`（`reset: LOGOUT`）vs `login_001`（`reset: RELAUNCH`）——
    **步骤逐字相同、仅 `precondition` 不同**。Task 3.4 的去重会 **SKIP 掉其中
    一条**，即「LOGOUT 重置后登录」这条**重置策略回归**不再被生成。
  - `search_001` vs `search_filter_001`——**连 `precondition` 都相同**，是语料里
    真的冗余（同一用例进了两个 suite）。

  **口径**：`precondition` 描述「**怎么起跑**」，不描述「**做什么**」——故与
  `id` / `name` / `suite` 同类，不进指纹（否则同一用例换 suite 就被判成新用例）。
  代价是第一对同指纹**已知且接受**：该 `reset` 维度目前无自动消费者，登记于此；
  **若 Task 3.4 接线后 SKIP 掉它，升级为 P2**——届时在 `test_candidates` 的
  SKIP 留痕里带上「与哪条既有用例撞指纹」，让人工能看见被跳过的是哪一条。
"""
from __future__ import annotations

from source.hashing import stable_hash
from testcase.schema import ActionStep, AssertionStep, TestCase, WaitStep

__all__ = ["test_fingerprint"]

_SCREEN = "screen"


def _ref(target) -> str:
    """`TargetRef` → `"<type>:<id>"`；`None`（无目标动作）→ 空串。"""
    if target is None:
        return ""
    return f"{target.type}:{target.id}"


def _screen_tokens(tc: TestCase) -> list[str]:
    """屏目标步骤 → token（`wait_for screen:X` / `assertion screen:X` /
    `postcondition screen:X`）。条件参与 token：`screen:X exists` 与
    `screen:X not_exists` 是**不同**的用例。"""
    out: list[str] = []
    for step in tc.steps:
        if isinstance(step, WaitStep):
            w = step.wait_for
            if w.target.type == _SCREEN:
                out.append(repr(("wait", w.target.id, w.condition)))
        elif isinstance(step, AssertionStep):
            a = step.assertion
            if a.target.type == _SCREEN:
                out.append(repr(("assert", a.target.id, a.condition)))
        elif isinstance(step, ActionStep) and step.postcondition is not None:
            post = step.postcondition
            if post.target.type == _SCREEN:
                out.append(repr(("post", post.target.id, post.condition)))
    return out


def _action_tokens(tc: TestCase) -> list[str]:
    """动作步骤 → token（含 `value`，见模块 docstring 的边界说明）。"""
    out: list[str] = []
    for step in tc.steps:
        if isinstance(step, ActionStep):
            out.append(repr((step.action, _ref(step.target),
                             step.direction or "", step.value or "")))
    return out


def _assertion_tokens(tc: TestCase) -> list[str]:
    """**element 目标**的观测步骤 → token（wait / assertion / postcondition）。

    屏目标的观测归 `_screen_tokens`——两个分量互斥且合起来覆盖全部步骤。
    `postcondition` 无 `expected` 字段，占位空串。
    """
    out: list[str] = []
    for step in tc.steps:
        if isinstance(step, WaitStep):
            w = step.wait_for
            if w.target.type != _SCREEN:
                out.append(repr(("wait", w.condition, _ref(w.target), w.expected)))
        elif isinstance(step, AssertionStep):
            a = step.assertion
            if a.target.type != _SCREEN:
                out.append(repr(("assert", a.condition, _ref(a.target), a.expected)))
        elif isinstance(step, ActionStep) and step.postcondition is not None:
            post = step.postcondition
            if post.target.type != _SCREEN:
                out.append(repr(("post", post.condition, _ref(post.target), "")))
    return out


def test_fingerprint(tc: TestCase) -> str:
    """**纯函数**：`TestCase` → 稳定指纹（16 hex）。同语义不同命名必得同值。"""
    return stable_hash([
        "screens:" + "|".join(_screen_tokens(tc)),
        "actions:" + "|".join(_action_tokens(tc)),
        "assertions:" + "|".join(_assertion_tokens(tc)),
    ])
