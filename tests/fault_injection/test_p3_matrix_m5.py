"""P3 故障注入矩阵 #15（M5；Task 5.4 / P3-23）。

| # | 场景 | 预期 | 约束 |
|---|---|---|---|
| 15 | 非幂等 POST_DISPATCH 失败且 postcondition 成立 → **不计复现次数、`reproducible` 不加分** | 不重跑 + `reproducible=False` + 无 `reproducible_3_times` 证据 | F9/§8.4 |

`docs/phase3-plan.md` 的矩阵把 #15 定为「F9 的验证点」：**复现引擎的阶段判定
必须与 P1 一致**。所以本文件刻意**用真 trace 与真 `StepRunner` 造那一步的
三列**（`failure_phase` / `effective_idempotency` / `effective_risk`），而不是
手搓 `StepProbe`——单测（`tests/unit/test_reproducer.py`）已经覆盖了每一格
判据，这里证明「判据在**真实数据形状**上成立」。

## ⚠️ 与 P1 矩阵 #15 的编号撞车（沿用 `test_p3_matrix_m3.py` 的登记）

P1 的 24 行矩阵里 #15 是「断言目标 ID 漂移」（`docs/p1_schema_review.md`），
语义完全不同——读编号先看文件。

## 三行的分工

1. **postcondition 成立** → 本行是矩阵的字面要求：`False` + `count=0` + 无证据
   + **`runner` 一次都没被调用**（H7：绝不重发）；
2. **postcondition 不成立** → 设计 §8.4 明文「不成立/无法判断 → 计一次『无法
   确认复现』」：`None` + `count=0` + 无证据；
3. **PRE_DISPATCH** → 设计 §8.4 第一行「PRE_DISPATCH 失败直接计入复现次数」：
   同样的非幂等 + 高风险步骤，只因为**阶段**不同就该重跑并计入——这一行是
   「判据来自阶段而不是幂等性」的对照。
"""
from __future__ import annotations

import itertools

from diagnosis.context import DiagnosisContext
from diagnosis.reproducer import _probe_from_step, reproduce
from agents.tools import AgentToolkit
from executor.guard import EnvKind, Guard
from graph import read_trace_steps
from llm.budget import LLMBudget
from runner.result import TestcaseResult
from tracer.storage import TraceStore

APP = "com.phaset0.logindemo"
BUILD = "1026"

_SEQ = itertools.count()


def _seq() -> int:
    return next(_SEQ)
SPEC = {"target": "screen:HomeView", "condition": "active", "timeout": 5}


def _pay_step_trace(tmp_path, *, phase: str, idempotency="NON_IDEMPOTENT",
                    risk="HIGH"):
    """写一条**真 trace**：一步「非幂等 / 已发出 / 高风险」的支付失败。

    走真 `TraceStore`（不手搓 SQL）——读侧要经过 `read_trace_steps` 的
    三列，这条链断在哪一段都会红。
    """
    # 每个调用一个独立的库文件：同一测试里要造**多条** trace（对照行），
    # 共用一个库会在 `runs.run_id` 上撞 UNIQUE（run_1 只能存在一次）。
    db = tmp_path / f"trace_{phase}_{idempotency}_{risk}_{_seq()}.db"
    store = TraceStore(db)
    store.start_run("run_1", suite="smoke", app_bundle_id=APP, app_build=BUILD)
    tc = store.start_testcase("run_1", "checkout_001", attempt=1)
    store.record_step(tc, 0, "tap", target_id="pay_button", status="FAILED",
                      failure_type="ACTION_OUTCOME_UNKNOWN",
                      effective_idempotency=idempotency,
                      effective_risk=risk, failure_phase=phase)
    store.end_testcase(tc, "FAIL", failure_type="ACTION_OUTCOME_UNKNOWN")
    store.end_run("run_1", status="FAIL", exit_code=1)
    steps, app_id, build = read_trace_steps(db)
    return DiagnosisContext(run_id="run_1", app_id=app_id, app_build=build,
                            steps=tuple(steps))


class _Recorder:
    """重跑替身：记下每次调用并回一个固定状态。"""

    def __init__(self, status="FAIL"):
        self.status = status
        self.calls: list[str] = []

    def __call__(self, testcase_id: str):
        self.calls.append(testcase_id)
        return TestcaseResult(testcase_id=testcase_id, status=self.status)


def _toolkit(tmp_path):
    """真 `AgentToolkit`（F12 的构造级门槛）+ 真 `LLMBudget`（F4）。

    **不注入**任何执行依赖：本矩阵的三行都由 `runner` 回调驱动（它才是
    重跑的通路）；toolkit 在这里的作用是满足 F12 的类型闸门。
    """
    return AgentToolkit(guard=Guard(EnvKind.SANDBOX))


def _run(ctx, tmp_path, *, runner, checker=None, budget=10, declared=True):
    """跑一次复现判定。

    `declared=True` 模拟 Task 5.6 的 CLI：**用例声明**了 postcondition，
    故由 `probe_for` 把 trace 三列与该声明合成一个 `StepProbe`（trace 里
    没有 postcondition 这一项——它是用例的属性，不是那一步的记录）。
    """
    probe_for = (lambda step: _probe_from_step(step, postcondition=SPEC)) \
        if declared else None
    return reproduce(ctx, toolkit=_toolkit(tmp_path), runner=runner,
                     postcondition_checker=checker, probe_for=probe_for,
                     llm_budget=LLMBudget(max_calls_per_run=budget))


# --- #15 主行：postcondition 成立 ---------------------------------------------


def test_matrix_15_postcondition_holding_is_not_a_reproduction(tmp_path):
    """矩阵 #15 的字面要求：**不计复现次数、证据不加分**。"""
    ctx = _pay_step_trace(tmp_path, phase="POST_DISPATCH")
    runner = _Recorder("FAIL")
    res = _run(ctx, tmp_path, runner=runner, checker=lambda _spec: True)

    assert res.reproducible is False, "原始动作其实成功了——没有失败可复现"
    assert res.reproduction_count == 0
    assert res.evidence == (), "不给 reproducible_3_times 加分"
    assert res.attempts == 0


def test_matrix_15_never_re_dispatches_the_non_idempotent_action(tmp_path):
    """H7：判据是 `runner` **一次都没被调用**——不是「结果看起来对」。

    把「绝不重发」写成「重发后发现 postcondition 成立」是同一个结论的两条
    路径，但后者**已经把钱付了两次**。
    """
    ctx = _pay_step_trace(tmp_path, phase="POST_DISPATCH")
    runner = _Recorder("FAIL")
    _run(ctx, tmp_path, runner=runner, checker=lambda _spec: True)
    assert runner.calls == [], "POST_DISPATCH 非幂等步骤一次都不许重发"


def test_matrix_15_the_verdict_is_recorded_for_audit(tmp_path):
    """审计要能回答「为什么没重跑」——`audit` 里带阶段/幂等性与裁决。"""
    ctx = _pay_step_trace(tmp_path, phase="POST_DISPATCH")
    res = _run(ctx, tmp_path, runner=_Recorder(), checker=lambda _s: True)
    assert res.audit[0]["kind"] == "repro_postcondition"
    assert res.audit[0]["satisfied"] is True
    assert res.audit[0]["phase"] == "POST_DISPATCH"
    assert res.audit[0]["idempotency"] == "NON_IDEMPOTENT"


# --- #15 次行：postcondition 不成立 / 无法判断 ---------------------------------


def test_matrix_15_postcondition_not_satisfied_is_unconfirmable(tmp_path):
    """设计 §8.4：「不成立/无法判断 → 计一次『无法确认复现』」。

    `None`（不是 `False`）：我们**没有**得到「跑不出来」的结论。
    """
    ctx = _pay_step_trace(tmp_path, phase="POST_DISPATCH")
    res = _run(ctx, tmp_path, runner=_Recorder(), checker=lambda _s: False)
    assert res.reproducible is None
    assert res.reproduction_count == 0 and res.evidence == ()
    assert res.audit[0]["satisfied"] is False


def test_matrix_15_postcondition_without_a_checker_is_unconfirmable(tmp_path):
    """无法判断（没注入 checker）与「不成立」同样归「无法确认」。"""
    ctx = _pay_step_trace(tmp_path, phase="POST_DISPATCH")
    res = _run(ctx, tmp_path, runner=_Recorder(), checker=None)
    assert res.reproducible is None and res.reproduction_count == 0
    assert res.audit[0]["satisfied"] is None


# --- 对照行：PRE_DISPATCH（判据来自阶段，不来自幂等性） ------------------------


def test_matrix_15_pre_dispatch_counts_towards_reproduction(tmp_path):
    """设计 §8.4 第一行：「PRE_DISPATCH 失败直接计入复现次数」。

    同样的**非幂等 + HIGH**步骤，只把阶段换成 PRE_DISPATCH（动作从未发出）
    就该重跑并计入——若有人把本引擎写成「幂等性驱动」，这一行与上一行会
    得到同一个结论，两者必有一红。
    """
    ctx = _pay_step_trace(tmp_path, phase="PRE_DISPATCH")
    runner = _Recorder("FAIL")
    res = _run(ctx, tmp_path, runner=runner)

    assert res.reproducible is True and res.reproduction_count == 3
    assert len(runner.calls) == 3, "PRE_DISPATCH 允许重跑（跑满上界）"
    assert [e.kind for e in res.evidence] == ["reproducible_3_times"]


def test_matrix_15_pre_dispatch_and_post_dispatch_disagree_on_the_same_step(
        tmp_path):
    """把两行并排：**唯一的自变量是 `failure_phase`**。

    两条 trace 的幂等性、风险、失败类型完全相同——结论必须相反。这是
    「阶段判定复用 P1 本体」（F9）在真实数据上的样子。
    """
    pre = _run(_pay_step_trace(tmp_path, phase="PRE_DISPATCH"), tmp_path,
               runner=_Recorder("FAIL"))
    post = _run(_pay_step_trace(tmp_path, phase="POST_DISPATCH"), tmp_path,
                runner=_Recorder("FAIL"), checker=lambda _s: True)
    assert pre.reproducible is True and post.reproducible is False
    assert pre.reproduction_count == 3 and post.reproduction_count == 0
    assert pre.evidence and not post.evidence
