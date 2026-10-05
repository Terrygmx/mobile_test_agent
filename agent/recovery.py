"""agent.recovery — P1 RecoveryEngine（设计 9.2，本模块主体）+ P0 遗留函数。

**P1（下方 RecoveryEngine / RunMemo）**：Task 4.1 起的确定性恢复半边——
决策表准入 → postcondition → settle → 当前屏识别 → Local Reconciliation →
RUN_MEMO → ExperienceStore → LLM 钩子（4.2 接入）。引擎不持有
Executor/LLM，设备交互经 RecoveryContext 注入 callable。

**P0（模块级 `recover()` 函数，Stage 8 / 设计 4.8.3 遗留）**：budget →
prompt → LLM → 解析 → risk 检查（只 LOW 自动执行）→ 唯一性校验 → 执行 →
记录 recoveries。仅供 P0 verify_stage8 回归路径消费，Task 4.2 LLM 校验链
迁入引擎后退役——**P1 管线不得调用它**（单消费原则，防平行实现）。

P0 review 修复存档：P1-2 action 白名单透传（input 恢复不再误 tap）；
P0-2 step_id 由调用方传入，recoveries 与 steps 真正关联；P2-6 JSON 正则
非贪婪。
"""

from __future__ import annotations

import json
import re
import time

from executor.executor import AmbiguousElement, ElementNotFound, Executor
from llm.budget import LLMBudget
from llm.prompt import PROMPT_TEMPLATE
from llm.provider import LLMProvider
from source.reconciliation import reconcile_local
from source.screen import current_screen
from tracer.recorder import Recorder

def _extract_json(raw: str):
    """从 LLM 输出提取第一个完整 JSON 对象（review P2-6：非贪婪正则会截断嵌套 JSON）。"""
    start = raw.find("{")
    if start == -1:
        return None
    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(raw[start:])
        return obj
    except json.JSONDecodeError:
        return None


# review P1-2：只放行白名单动作；input 恢复不允许升级成 tap（fail closed）
ALLOWED_ACTIONS = {"tap": {"tap"}, "input": {"input"}}


def recover(expected_id: str, error: Exception, ex: Executor,
            source_metadata: dict, budget: LLMBudget, llm: LLMProvider,
            step_id: int | None = None, recorder: Recorder | None = None,
            step_action: str = "tap", step_value: str | None = None) -> dict:
    t0 = time.time()
    result = {"status": None, "llm_target": None, "confidence": 0.0,
              "risk_level": None, "action": None}

    def done(status: str, accepted: bool = False) -> dict:
        result["status"] = status
        if recorder is not None:
            rid = recorder.record_recovery(step_id or 0, "llm", result["llm_target"],
                                           result["confidence"], result["risk_level"] or "NONE",
                                           int((time.time() - t0) * 1000), accepted)
            # review R2-3：返回精确行 id，调用方无需用全局 MAX(id) 猜
            result["rec_row_id"] = rid
        return result

    # 1. budget（fail closed）
    if not budget.try_acquire():
        return done("LLM_BUDGET_EXCEEDED")

    page = ex.page_source()
    recon = reconcile_local(expected_id, source_metadata.get("screen", ""),
                            source_metadata, page)

    # 2. prompt + 调用
    source_elements = [e.get("accessibilityId") or e["id"]
                       for e in source_metadata.get("elements", [])]
    prompt = PROMPT_TEMPLATE.format(
        expected=expected_id, action=step_action, error=type(error).__name__,
        page_source=page[:8000],
        source_elements=json.dumps(source_elements, ensure_ascii=False),
        reconciliation=json.dumps(recon, ensure_ascii=False),
        screen=source_metadata.get("screen", ""),
    )
    try:
        raw = llm.complete(prompt)
    except Exception:
        return done("LLM_PROVIDER_ERROR")

    # 3. 解析结构化输出
    out = _extract_json(raw)
    if out is None:
        return done("LLM_INVALID_OUTPUT")
    try:
        result["llm_target"] = (out.get("target") or {}).get("value")
        result["confidence"] = float(out.get("confidence", 0))
        result["risk_level"] = out.get("risk_level")
        result["action"] = out.get("action")
        # review R3-1：解析器与 prompt 契约同步——input_value 是输入内容字段；
        # 旧字段 value 保留为 legacy 兜底（防 LLM 无视新契约回填旧字段）
        result["input_value"] = out.get("input_value")
    except Exception:
        return done("LLM_INVALID_OUTPUT")
    if not result["llm_target"]:
        return done("LLM_NO_TARGET")

    # 4. risk 检查：Phase 0 只允许 LOW 自动执行（fail closed）
    if result["risk_level"] != "LOW":
        return done("LLM_RISK_NOT_LOW")

    # 4.5 review P1-2：动作白名单——LLM 不得改动作类型
    if result["action"] not in ALLOWED_ACTIONS.get(step_action, set()):
        return done("LLM_ACTION_MISMATCH")
    # review R2-2：input 恢复值必须优先用 SecretProvider 解析的原值；
    # LLM 编造的输入内容（幻觉密码等）只在原步骤无值时兜底
    if (result["action"] == "input" and not step_value
            and not (result.get("input_value") or result.get("value"))):
        return done("LLM_NO_VALUE")

    # 5. 唯一性校验 + 执行
    loc = [{"type": "accessibility_id", "value": result["llm_target"]}]
    try:
        ex.find(loc)
    except ElementNotFound:
        return done("LLM_TARGET_NOT_FOUND")
    except AmbiguousElement:
        return done("LLM_TARGET_AMBIGUOUS")
    if result["action"] == "input":
        # review R2-2：原值（secret 解析后）优先，LLM value 仅兜底
        ex.input(loc, step_value or result.get("input_value")
                 or result.get("value") or "")
    else:
        ex.tap(loc)
    return done("RECOVERED", accepted=True)


# ---------------------------------------------------------------------------
# P1 Recovery Engine（设计 9.2；ExperienceStore 预留位旧稿「20 节」现为
# P2 设计 7 节，Task 4.1 确定性半边）
#
# 与上方 P0 `recover()` 的关系：P0 函数保留至 Task 4.2——LLM 校验链迁入
# RecoveryEngine 后由引擎接管，P0 verify_stage8 回归路径随之切换（记账见
# p1_schema_review.md）。重构完成前两者并存，但**消费只有一条**：P1 管线
# 只走 RecoveryEngine，P0 函数仅供 P0 verify 脚本回归。
# ---------------------------------------------------------------------------

from agent.context import (          # noqa: E402
    RecoveryContext,
    RecoveryResult,
)
# P2-03：ExperienceStore 的唯一定义在 experience.store——
# 直接 import 单一真值源（context.py 只 re-export 兼容旧路径）。
# P1 的 EmptyExperienceStore 占位已在 Task 2.4 退役（旧签名 build≠app_id）。
from experience.store import ExperienceStore   # noqa: E402
from experience.ranker import rank_experiences   # noqa: E402
from agent.policy import (           # noqa: E402
    RecoveryAction,
    RecoveryConfig,
    admitted_actions,
)
from experience.runtime_guard import (  # noqa: E402
    GUARD_POLICY_BLOCK_REASONS,
    GUARD_POLICY_BLOCK_REASONS,
    GUARD_REASON_TO_FAILURE_TYPE,
    RuntimeContext,
    experience_locator,
    experience_runtime_guard,
    guard_candidate,
)
from llm.budget import BudgetConfig, LLMBudget  # noqa: E402
from llm.parser import parse_llm_output         # noqa: E402
from llm.prompt import build_recovery_prompt, redact_ui_tree  # noqa: E402
from runner.result import recovered_kind        # noqa: E402
from source.screen import screen_fingerprint    # noqa: E402

__all__ = ["RunMemo", "RecoveryEngine", "resolve_deferred_sample"]


class RunMemo:
    """9.4 作用域内复用（非学习）：同 run 内 (screen, target_id, app_build)
    的已校验恢复结果，内存态，run 结束即丢弃。**不写 Repository，不跨
    run**（H15）。P1 只存经确定性校验的条目（4.2 起 LLM 校验通过后写入）；
    每次使用仍标 RECOVERED，recoveries.kind='RUN_MEMO'。"""

    def __init__(self) -> None:
        self._memo: dict[tuple[str, str, str], dict] = {}

    def lookup(self, screen: str, target_id: str,
               app_build: str) -> dict | None:
        return self._memo.get((screen, target_id, app_build))

    def save(self, screen: str, target_id: str, app_build: str,
             strategy: dict) -> None:
        self._memo[(screen, target_id, app_build)] = strategy


def _single_element(found) -> object:
    """Executor 契约归一（2.6 实锤：单元素或列表两种替身形态）。"""
    if isinstance(found, (list, tuple)):
        if len(found) != 1:
            raise LookupError(f"expected exactly 1 element, got {len(found)}")
        return found[0]
    return found


def resolve_deferred_sample(rec: RecoveryResult, *, succeeded: bool,
                            failure_reason: str | None = None) -> int:
    """aux 命中后的**观测回填**（设计 4.7 的 aux 半边，Task 2.4 评审 P2-1）。

    引擎侧 aux（wait/assert）无 dispatch 语义，执行结果此刻不可观测——所以
    `_queue_sample` 产出 `result=None` 的**待定**样本而不是写死 SUCCESS
    （猜错的 SUCCESS 会抬高 success_rate 把 Candidate 推向 VERIFIED，
    E4/E11 级别）。但**调用方能观测**：它在覆盖定位后重跑了一次断言/等待。
    本函数就是那条回填路径——把观测结果翻成 4.7 的 `result`。

    为什么不让调用方直接改 payload：那样「写什么样本」的决策就散到管线里
    了，正是 4.7 单点纪律要避免的。调用方只说「成没成」，翻译留在本模块。
    返回回填的样本数（0 = 没有待定样本，调用方无需在意）。

    同时回填 §11.1 的 `experience_execution` **事件**（`pending_observation`
    那条）：样本与事件记的是同一次观测，没有道理一边回填、一边停在「不可
    观测」。回填后 `execution` 反映**最终观测到的**终态（aux 的
    `not_dispatched` 只是引擎侧的事实，最终结果由调用方那次重跑决定）。
    """
    filled = 0
    for sample in (rec.detail or {}).get("experience_runs") or []:
        if not sample.pop("pending_observation", False):
            continue
        sample["result"] = "SUCCESS" if succeeded else "FAILURE"
        if not succeeded:
            sample["guard_reason"] = failure_reason or "AUX_RERUN_FAILED"
        filled += 1
    # §11.1 的 experience_execution 事件**同款回填**：aux 命中时引擎把
    # `execution="not_dispatched"` 记进去、`result` 留空，调用方观测后在这里
    # 补上真实终态。两边都记同一件事，就不该一边回填、一边停在「不可观测」。
    for ev in (rec.detail or {}).get("experience_events") or []:
        detail = ev.get("detail") or {}
        if not detail.pop("pending_observation", False):
            continue
        detail["execution"] = "SUCCESS" if succeeded else "FAILURE"
        detail["result"] = recovered_kind("experience") if succeeded else None
        if not succeeded:
            detail["reason"] = failure_reason or "AUX_RERUN_FAILED"
    return filled


class _FindAllAdapter:
    """把 ctx 注入的 `find_all` 适配成 `experience_runtime_guard` 要的
    executor 形态（只需 `.find_all(Locator) -> list`），顺带捕获**数量**与
    **唯一命中元素**。

    引擎不持有 Executor（9.1 隔离纪律），但 Experience 的执行端要复用
    Guard 刚找到的那个元素——按候选策略再 find 一次等于多一次设备往返，
    且两次之间 UI 可能又变（TOCTOU）。与 `_llm_stage` 的 `found_box`
    同款做法。`last_count` 是 4.7 样本里 `uniqueness_count` 的来源
    （异常语义反推不出真数量，所以必须由 find_all 给出）。
    """

    def __init__(self, find_all) -> None:
        self._find_all = find_all
        self.last_count: int | None = None
        self.matched: object | None = None

    def find_all(self, locator) -> list:
        found = self._find_all(locator)
        items = list(found) if isinstance(found, (list, tuple)) else [found]
        self.last_count = len(items)
        self.matched = items[0] if len(items) == 1 else None
        return items


class RecoveryEngine:
    """9.2 流水线。P1 交付确定性半边：决策表 → postcondition → settle →
    当前屏识别 → Local Reconciliation → Experience Store → LLM 钩子
    （P2 Task 2.4 接真 Store）。引擎不持有 Executor/LLM——设备交互经 ctx
    注入，LLM 调用点同样只在引擎（9.1：禁止 Executor 内 call_llm）。"""

    def __init__(self, config: RecoveryConfig | None = None,
                 repo=None,
                 experience_store: ExperienceStore | None = None,
                 run_memo: RunMemo | None = None,
                 llm=None,
                 budget: LLMBudget | None = None,
                 guard=None,
                 sleep=time.sleep) -> None:
        self.config = config or RecoveryConfig()
        self.repo = repo
        # None = 没有经验库（不是「空库」）。空库由真 Store 表达（lookup
        # 返回 []）——Task 2.4 退役 EmptyExperienceStore 后，两者在行为上
        # 等价（P1 行为保留原则），但在报告上可区分（no_store vs miss）。
        self.experience_store = experience_store
        self.run_memo = run_memo or RunMemo()
        # Task 4.2：llm + budget + guard——LLM 候选执行前过 9.3 校验链，
        # 风险门控复用 Guard 实例（10.1：Guard 不受 LLM 输出影响）。
        self.llm = llm
        self.budget = budget
        self.guard = guard
        self._sleep = sleep

    @staticmethod
    def _recovered(kind: str, detail: dict,
                   strategy: dict | None = None) -> RecoveryResult:
        """已恢复结论的统一构造：盖上设计 10 节的明细分类。

        单一入口而不是五处各写一遍 `detail["recovered_kind"]`——漏一处就是
        「同一类恢复有时有分类有时没有」，报告读起来自相矛盾。分类本身
        不改聚合与退出码（RECOVERED ≠ PASS 不变）。
        """
        detail = dict(detail)
        detail["recovered_kind"] = recovered_kind(kind)
        return RecoveryResult(recovered=True, kind=kind, detail=detail,
                              strategy=strategy)

    @staticmethod
    def _current_screen_id(screen_res,
                           ctx: RecoveryContext) -> str | None:
        """当前屏 id（9.3-3 的判定基准）。

        LLM 与 Experience 两条路径**共用本函数**——同一件事在两处各推一次
        必然在某天分叉，而 E1 红线要求同一输入下两条路径的 Guard 结论逐位
        一致。

        **两种情况必须分开**（Task 2.4 终审延后项，本次收口）：

        1. **识别跑过**（`screen_res is not None`）→ 只认观测结果。页面没有
           任何已登记 marker（`CURRENT_SCREEN_UNKNOWN`）、多个 marker 互斥
           （`SCREEN_AMBIGUOUS`）、解析失败——都返回 `None`，即设计 §5/§4.7
           的 `SCREEN_UNKNOWN → MISS 且不计样本`。
           此前这里回落 `ctx.screen_id`，把那条规则变成死代码，更要命的是
           让 Experience 路径的屏校验**自比自**：`current_screen` 与
           `exp.screen_id` 都等于 `ctx.screen_id` → 永远相等 → Guard 的第一
           环从不触发。于是「页面根本没有已登记 marker」时，只要恰好存在
           同名唯一元素，候选就会在**未确认屏**的情况下执行。
        2. **识别没跑**（`screen_res is None`：没有 Repository——marker 表
           本身不存在；没有页面；或该动作未准入 LOCAL_RECONCILE）→ 沿用 P1
           的**登记屏作为先验**。此时不是「证明失败」而是「无从判定」，任何
           屏校验都必然空转（没有 marker 表可比），收紧只会白掉能力而不增加
           安全性。这条保留即 plan「P1 行为保留」在此输入上的落实。

        ⚠️ 情况 1 是**刻意**偏离 plan「空 experience 库时 Recovery 行为必须与
        P1 完全一致」的一处（决策记录见设计附录与 `docs/p2_data_audit.md`）：
        偏离面仅限「屏识别跑过且没结论」这一种 P1 从未规定的输入，方向是
        **收紧**（照常恢复 → fail-closed 跳过）；P1 全部测试与 phase0 验证
        脚本持续通过（回归底线）。
        """
        if screen_res is not None:
            return screen_res.screen or None
        return ctx.screen_id or None

    def _policy_check_for(self, exp, ctx: RecoveryContext):
        """候选策略 → 10.1 Guard 复检 callable（与 LLM 路径同源）。

        E1 的「同一输入同一结论」不止覆盖 Guard 链的六项判定，也覆盖 10.1
        这一环：**同一候选策略，经 LLM 得 vs 经 Experience 得，Guard 结论
        必须一致**。差别只该在「候选从哪来」，不该在「谁给它放行」。

        候选值解析到**登记元素**后才判定：`blocked_targets` 的 element 模式
        匹配 element_id，而候选值（`user_field`）与原目标
        （`username_field`）不是同一个 id——不解析就拿不到判据。解析失败
        （未登记）→ None，与 LLM 路径同款 fail-open（10.1 三条规则都依赖
        登记信息，无登记无从判定）。

        **生产确实接上了**（Task 2.4 终审期实测更正）：`cli/main.py` 在装配
        runner 之后有一句 `pipeline.recovery.guard = runner.guard`——两条路径
        共用同一个 Guard 实例（10.1：Guard 不受 LLM 输出影响，同一策略对象
        才保证这点）。此前 Task 2.4 评审修订记录里「生产没接 guard、10.1 复检
        从未生效」的论断是**错的**，已由探针证伪并更正。
        """
        if self.guard is None or self.repo is None:
            return None
        try:
            eff = self.repo.resolve(exp.strategy.value, build=ctx.app_build)
        except Exception:  # noqa: BLE001 — UnknownReference 等 → 未登记
            return None
        from executor.guard import GuardContext

        def _check() -> None:
            self.guard.check(GuardContext(
                risk=eff.risk, screen_id=eff.screen,
                element_id=eff.id, action=ctx.action or "tap"))

        return _check

    def recover(self, ctx: RecoveryContext) -> RecoveryResult:
        allowed = admitted_actions(
            ctx.failure_type, ctx.phase, ctx.effective_idempotency,
            ctx.has_postcondition, self.config)
        if allowed is None:
            # 不可恢复 ≠ 失败被吞：维持原症状，detail 说明为什么没进流水线
            return RecoveryResult(
                recovered=False, failure_type=ctx.failure_type,
                detail={"recovery": "not_admitted"})

        stages: list[dict] = []
        # 4.7 样本在引擎侧累积，**无论最终是否恢复**都要随结果带出去：Guard
        # 判定的失败样本（NOT_FOUND / AMBIGUOUS / TYPE_MISMATCH）与执行失败
        # 样本都是要计入失败率的真观测，只挂在「已恢复」的结果上等于把它们
        # 丢掉（E11 的失败口径被吞掉，成功率会系统性偏高）。
        samples: list[dict] = []
        # §11.1 的 recovery 期事件，同样由引擎产出 payload、管线落库
        # （与 4.7 样本同款分工：引擎掌握「发生了什么」，管线补 run/tc id）。
        events: list[dict] = []
        # 本次 recovery 里**因安全/风险被 Guard 拦下**的候选 reason。只在
        # 「全被拦 + 无回落」时用来定终态（见 `_unrecovered`）。
        blocks: list[str] = []
        # 本次 recovery 里**因安全/风险被 Guard 拦下**的候选 reason。只在
        # 「全被拦 + 无回落」时用来定终态（见 `_unrecovered`）。
        blocks: list[str] = []

        # --- POSTCONDITION_CHECK（H7：非幂等 POST_DISPATCH 的唯一出口；
        #     幂等动作也先查——postcondition 成立即无需重发，矩阵 #18） ---
        if RecoveryAction.POSTCONDITION_CHECK in allowed:
            if ctx.postcondition_check is None:
                stages.append({"stage": "postcondition",
                               "outcome": "no_checker_injected"})
            else:
                try:
                    ok = bool(ctx.postcondition_check())
                except Exception as e:  # noqa: BLE001 — 观测故障不当判定
                    ok = None
                    stages.append({"stage": "postcondition",
                                   "outcome": "checker_error",
                                   "error": f"{type(e).__name__}: {e}"})
                else:
                    stages.append({"stage": "postcondition",
                                   "outcome": bool(ok)})
                if ok is True:
                    # 动作已生效，不重发（H7：未再次点击）
                    return self._recovered("postcondition",
                                           {"stages": stages})
                if ok is False:
                    return RecoveryResult(
                        recovered=False,
                        failure_type="ACTION_OUTCOME_UNKNOWN",
                        detail={"stages": stages,
                                "recovery": "postcondition_not_satisfied"})

        # --- SETTLE_RETRY（9.2 第 5 步：有界 re-find + 重发，默认 1 次） ---
        if RecoveryAction.SETTLE_RETRY in allowed and ctx.refind is not None:
            for attempt in range(1, self.config.settle_max_attempts + 1):
                self._sleep(self.config.settle_wait_s)
                try:
                    element = _single_element(ctx.refind())
                except Exception as e:  # noqa: BLE001 — settle 失败进下一阶段
                    stages.append({"stage": "settle", "attempt": attempt,
                                   "outcome": f"{type(e).__name__}"})
                    break
                if ctx.redispatch is None:
                    stages.append({"stage": "settle",
                                   "outcome": "no_redispatch_callable"})
                    break
                try:
                    ctx.redispatch(element)
                except Exception as e:  # noqa: BLE001 — 重发失败=恢复未完成，
                    # 原症状保留（异常逃出会让管线整例崩掉、steps 无行——
                    # M4 Gate 真机实锤）
                    stages.append({"stage": "settle", "attempt": attempt,
                                   "outcome": f"redispatch:"
                                              f"{type(e).__name__}"})
                    break
                return self._recovered(
                    "settle_retry",
                    {"stages": stages, "settle_attempt": attempt})

        # --- 取页（一次）+ 脱敏（review_m4_task42 P2-2 定档）---
        # H14：redact 前置于一切消费——current_screen / reconcile_local /
        # prompt 吃的是**同一份脱敏页**。marker 名（screen.*）与元素 id 不
        # 命中遮蔽模式，识别语义不变；运行时文本（recon 候选）从此不可能
        # 以未脱敏形态出进程。也消掉了同一次恢复拉两次页的设备成本与
        # 两次内容不一致的口子。
        page_red: str | None = None
        if ctx.page_source is not None and (
                RecoveryAction.LOCAL_RECONCILE in allowed
                or RecoveryAction.LLM_CANDIDATE in allowed):
            try:
                page_red = redact_ui_tree(ctx.page_source())
            except Exception as e:  # noqa: BLE001 — 取页/脱敏故障如实记录
                stages.append({"stage": "page",
                               "outcome": f"{type(e).__name__}"})

        # --- LOCAL_RECONCILE（9.2 第 6-7 步：当前屏识别 + Source 子集对比）---
        recon: dict | None = None
        screen_res = None          # 当前屏识别结果，LLM 校验链复用（9.3-3）
        if RecoveryAction.LOCAL_RECONCILE in allowed \
                and page_red is not None and self.repo is not None:
            try:
                screen_res = current_screen(page_red, self.repo)
            except Exception as e:  # noqa: BLE001 — 解析故障
                stages.append({"stage": "screen",
                               "outcome": f"{type(e).__name__}"})
            else:
                stages.append({"stage": "screen",
                               "outcome": screen_res.status,
                               "screen": screen_res.screen})
                if screen_res.status == "FOUND":
                    # 顺延①（4.1 记账）：12.3 两键 metadata 的 reconcile 适配
                    # 在引擎内收口——ctx.source_metadata 由调用方给时优先，
                    # 缺省从 Repository 合并视图派生本屏子集。
                    source_meta = ctx.source_metadata or self._source_subset(
                        screen_res.screen)
                    if source_meta:
                        recon = reconcile_local(
                            ctx.element_id or "", screen_res.screen,
                            source_meta, page_red)
                        stages.append({"stage": "reconcile",
                                       "outcome": recon["status"],
                                       "candidates":
                                           recon["candidates_in_runtime"]})
                # 确定性候选自动执行是设计 stretch（9.2 可选）——P1 不自动
                # 执行运行时候选：候选元素无 metadata，risk 未知按最高处理
                # （experience.runtime_guard 的 fail-closed 链）。候选留给 LLM。

        # --- RUN_MEMO（9.4：同 run 复用，reconciliation 后、LLM 前） ---
        # 命中后必须按 memo 保存的**恢复策略**重找（ctx.find_with）——
        # refind 捕获的是原始 strategies，漂移下按原策略找必然再失败；
        # 复用语义 = 策略被消费，不是「查得到」（review P3-1 定档）。
        if RecoveryAction.RUN_MEMO in allowed and ctx.element_id:
            memo = self.run_memo.lookup(
                ctx.screen_id or "", ctx.element_id, ctx.app_build)
            if memo is not None:
                if ctx.find_with is None:
                    stages.append({"stage": "run_memo", "outcome": "hit",
                                   "consumed": "no_find_with_callable"})
                else:
                    stages.append({"stage": "run_memo", "outcome": "hit"})
                    try:
                        element = _single_element(ctx.find_with((memo,)))
                        if ctx.redispatch is not None:
                            ctx.redispatch(element)
                            return self._recovered(
                                "run_memo",
                                {"stages": stages, "memo_strategy": memo})
                    except Exception as e:  # noqa: BLE001
                        stages.append({"stage": "run_memo",
                                       "outcome": f"{type(e).__name__}"})

        # --- Experience Store（P2 设计 5.1 / 7.1：reconciliation 后、LLM 前。
        #     命中即免 LLM 调用——设计 3.1「LLM Budget 不变；Experience 命中
        #     时根本不消耗 Budget」） ---
        exp_result = self._try_experiences(ctx, stages, samples, events,
                                           blocks, screen_res, page_red)
        if exp_result is not None:
            return exp_result

        # --- LLM（9.2 第 8 步；--no-llm / 未配置 → disabled 如实可见） ---
        if self.llm is None:
            stages.append({"stage": "llm", "outcome": "disabled"})
            return self._unrecovered(ctx, stages, samples, events,
                                     "no_llm_engine", blocks=blocks)
        return self._llm_stage(ctx, stages, screen_res, recon, page_red,
                               samples, events)

    @staticmethod
    def _attach(detail: dict, samples: list[dict],
                events: list[dict]) -> dict:
        """把 4.7 样本与 §11.1 事件挂进结论 detail。

        **有才挂**——纯 LLM 的 run 不该凭空多出一个空键，报告读起来会像
        「查了经验库但没记样本/没发事件」。两者合成一个入口而不是各写一份：
        漏挂一处就是「同一类结论有时带事件有时不带」。
        """
        if samples:
            detail["experience_runs"] = samples
        if events:
            detail["experience_events"] = events
        return detail

    def _unrecovered(self, ctx: RecoveryContext, stages: list,
                     samples: list[dict], events: list[dict],
                     reason: str,
                     blocks: list[str] | None = None) -> RecoveryResult:
        """未恢复结论的统一构造（维持原症状 + 可观测的停在哪一步）。

        `blocks`：本次 recovery 里因安全/风险被 Guard 拦下的候选 reason。
        **全被拦且无回落**（无 LLM / `--no-llm`）时，终态用共享映射表的裁决
        而不是原症状——LLM 路径早就是这么映射的（`GUARD_REASON_TO_FAILURE_TYPE`
        的 `miss`），同一候选不该因来路不同而给 CI 两个不同的结论
        （Task 2.4 终审 P3-1）。
        """
        return RecoveryResult(
            recovered=False,
            failure_type=self._terminal_failure_type(ctx, blocks),
            detail=self._attach({"stages": stages, "recovery": reason},
                                samples, events))

    @staticmethod
    def _terminal_failure_type(ctx: RecoveryContext,
                               blocks: list[str] | None) -> str:
        """无回落时的终态 failure_type（安全裁决优先于原症状）。

        多条候选各被拦时取**最严**的一条：`SECURITY_BLOCKED`（10.1 硬拦）
        压过 `RISK_BLOCKED`（风险门控）。报成原症状（ELEMENT_NOT_FOUND）会让
        CI 把「被策略拦下」读成「元素漂移」——排障方向完全错，而
        `SECURITY_BLOCKED` 还会让用例走 BLOCKED/exit 4（8.1/8.4 的既有语义）。
        """
        for reason in ("SECURITY_BLOCKED", "RISK_BLOCKED"):
            if blocks and reason in blocks:
                return GUARD_REASON_TO_FAILURE_TYPE.get(reason, ctx.failure_type)
        return ctx.failure_type

    # --- Experience 消费（设计 5.1 try_experiences；Task 2.4） ---

    def _try_experiences(self, ctx: RecoveryContext, stages: list,
                         samples: list[dict], events: list[dict],
                         blocks: list[str], screen_res, page_red: str | None
                         ) -> RecoveryResult | None:
        """设计 5.1 主循环：lookup → 逐候选 Guard（4.7 决定是否记样本）→
        EXECUTE 则执行 + postcondition → 成功返回 `kind="experience"`；
        全部候选用尽返回 None（调用方回落 LLM，仍受 P1 Budget 控制）。

        排序：`rank_experiences`（设计 5.1，Task 3.2）——信任档位 VERIFIED >
        DEGRADED > CANDIDATE > REJECTED，同档先看 success_rate 再看新近度。
        排序只决定「先试哪条」，不改变每条各自的 Guard 判定（E1）。

        每一处「做不了」都留一条 stage，不静默跳过：静默的「有经验库但没
        查」在报告上与「查了没有」不可区分，排障只能靠猜——与 LLM 阶段的
        `disabled` 同款纪律（review_m4_task41 P3-2）。
        """
        if self.experience_store is None:
            stages.append({"stage": "experience", "outcome": "no_store"})
            return None
        # 主键 (app_id, screen_id, target_id) 三段缺一就查不出东西——不猜键。
        # app_id 缺失是常态（--fake-driver / 未给 --bundle-id）；screen_id 缺失
        # 出现在 Repository 解析不到目标（aux 引用悬空）时。两种都如实报出，
        # 不拿 app_build / 当前屏顶替（顶替会查到别人家的经验）。
        missing = [name for name, value in (
            ("app_id", ctx.app_id), ("screen_id", ctx.screen_id),
            ("element_id", ctx.element_id)) if not value]
        if missing:
            stages.append({"stage": "experience", "outcome": "incomplete_key",
                           "missing": missing[0]})
            return None
        if page_red is None:
            # Screen 校验是 Guard 第一环（设计 5 节）：读不到页 = 证明不了
            # 当前屏 → fail-closed 跳过，且**不记样本**（不是「用了但错了」）。
            stages.append({"stage": "experience", "outcome": "no_page_source"})
            return None
        if ctx.find_all is None:
            # 数量观测端缺失时 Guard 只能把「拿不到元素」当成 0 匹配——那会
            # 写成假失败样本（4.7 里 NOT_FOUND 是要计入失败率的）。宁可不查。
            stages.append({"stage": "experience", "outcome": "no_find_all"})
            return None
        try:
            candidates = self.experience_store.lookup(
                ctx.app_id, ctx.screen_id or "", ctx.element_id)
        except Exception as e:  # noqa: BLE001 — 读库故障不伪装成「没有经验」
            stages.append({"stage": "experience",
                           "outcome": f"lookup_error:{type(e).__name__}"})
            return None
        self._emit(events, "experience_lookup",
                   app_id=ctx.app_id, screen_id=ctx.screen_id,
                   target_id=ctx.element_id, candidates=len(candidates))
        if not candidates:
            stages.append({"stage": "experience", "outcome": "miss"})
            self._emit(events, "experience_miss",
                       app_id=ctx.app_id, screen_id=ctx.screen_id,
                       target_id=ctx.element_id)
            return None
        stages.append({"stage": "experience", "outcome": "hit",
                       "count": len(candidates)})

        current_screen_id = self._current_screen_id(screen_res, ctx)
        fingerprint = screen_fingerprint(page_red)
        for exp in rank_experiences(candidates):
            hit = self._try_one_experience(
                ctx, exp, stages, current_screen_id, fingerprint, samples,
                events, blocks)
            if hit is not None:
                return hit
        stages.append({"stage": "experience", "outcome": "exhausted",
                       "count": len(candidates)})
        return None

    def _try_one_experience(self, ctx: RecoveryContext, exp, stages: list,
                            current_screen_id: str | None,
                            fingerprint: str | None,
                            samples: list[dict],
                            events: list[dict],
                            blocks: list[str]) -> RecoveryResult | None:
        """单个候选：Guard → 4.7 样本 → EXECUTE 则执行 + postcondition。

        返回 RecoveryResult = 本候选救回来了；None = 换下一个候选
        （BLOCK / 执行失败 / 结果不可观测——设计 5.1 的 `continue`）。
        """
        t0 = time.monotonic()
        adapter = _FindAllAdapter(ctx.find_all)
        gres = experience_runtime_guard(
            exp,
            RuntimeContext(current_screen=current_screen_id,
                           expected_type=ctx.expected_type,
                           effective_risk=ctx.effective_risk,
                           action=ctx.action or "tap",
                           element_id=ctx.element_id,
                           screen_fingerprint=fingerprint),
            adapter,
            policy_check=self._policy_check_for(exp, ctx))
        elapsed = int((time.monotonic() - t0) * 1000)
        fp_match = (None if exp.last_screen_fingerprint is None
                    or fingerprint is None
                    else exp.last_screen_fingerprint == fingerprint)
        entry = {"stage": "experience_candidate",
                 "experience_id": exp.experience_id,
                 "status": exp.status.value,
                 "outcome": gres.outcome,
                 "reason": gres.reason,
                 "record_as_sample": gres.record_as_sample,
                 "screen_fingerprint_match": fp_match,
                 "uniqueness_count": adapter.last_count}
        # §11.1 的 experience_hit：**每条候选被评估**发一条（字段对齐设计
        # 示例的 detail），不是「整个 lookup 只发一条」——多候选逐个尝试正是
        # 5.1 的核心行为，合并成一条就看不见「第一条为什么被换掉」。
        self._emit(events, "experience_hit",
                   experience_id=exp.experience_id,
                   status_before=exp.status.value,
                   screen_match=current_screen_id == exp.screen_id,
                   screen_fingerprint_match=fp_match,
                   build_in_validated_set=ctx.app_build in (
                       exp.validated_builds or []),
                   uniqueness_count=adapter.last_count,
                   type_match=self._type_match(gres, ctx),
                   effective_risk=getattr(ctx.effective_risk, "name", None),
                   outcome=gres.outcome, reason=gres.reason)

        if gres.outcome != "EXECUTE":
            stages.append(entry)
            if gres.outcome == "BLOCK":
                if gres.reason in GUARD_POLICY_BLOCK_REASONS:
                    # 只有安全/风险拦截才是「裁决」；NOT_FOUND/AMBIGUOUS/
                    # TYPE_MISMATCH 是普通失败，不参与终态改写。
                    blocks.append(gres.reason)
                if gres.reason in GUARD_POLICY_BLOCK_REASONS:
                    # 只有安全/风险拦截才是「裁决」；NOT_FOUND/AMBIGUOUS/
                    # TYPE_MISMATCH 是普通失败，不参与终态改写。
                    blocks.append(gres.reason)
                # 只有 BLOCK 才是「拦」（MISS 是「此刻不适用」，由上面的
                # experience_hit 的 outcome/reason 如实表达）。事件名如实。
                self._emit(events, "experience_guard_block",
                           experience_id=exp.experience_id,
                           reason=gres.reason,
                           record_as_sample=gres.record_as_sample)
            if gres.record_as_sample:
                # 4.7 第 2/3 行：唯一性/类型失败是「用了但错了」→ 失败样本
                self._queue_sample(samples, ctx, exp, gres, adapter,
                                   fingerprint, elapsed, stages,
                                   result="FAILURE",
                                   guard_reason=gres.reason)
            return None

        # EXECUTE：执行 + postcondition（设计 5.1 的
        # perform_and_check_postcondition）
        element = adapter.matched
        execution = "SUCCESS"
        failure_reason = None
        if ctx.redispatch is None:
            # aux（wait/assert）无 dispatch 语义：候选交调用方覆盖定位后重跑。
            # 执行结果**此刻不可观测**——所以产出 `result=None` 的**待定样本**
            # （`pending_observation`），由调用方观测后回填
            # （`resolve_deferred_sample`）。既不写死 SUCCESS（猜错的 SUCCESS
            # 会抬高 success_rate 把 Candidate 推向 VERIFIED，E4/E11 级别），
            # 也不直接丢弃（丢了则「仅经 aux 命中的 Candidate」sample_count
            # 恒为 0，永远到不了 4.5 的 min_samples，报告上读成「从未被使用」
            # ——P2 的「知识积累」目标对 aux 目标整体失效）。
            execution = "not_dispatched"
            self._queue_sample(samples, ctx, exp, gres, adapter, fingerprint,
                               elapsed, stages, result="SUCCESS",
                               deferred=True)
        else:
            try:
                ctx.redispatch(element)
            except Exception as e:  # noqa: BLE001 — 执行失败 = 本候选没用
                execution = "FAILURE"
                failure_reason = f"redispatch:{type(e).__name__}"
        if execution == "SUCCESS" and ctx.has_postcondition \
                and ctx.postcondition_check is not None:
            # 设计 6：非幂等目标「postcondition 确认成功才记一次 SUCCESS
            # 样本」——postcondition 是执行结果的唯一判据。
            try:
                ok = ctx.postcondition_check()
            except Exception:  # noqa: BLE001 — 观测故障不当判定（P1 同款）
                ok = None
            entry["postcondition"] = ok
            if ok is False:
                execution = "FAILURE"
                failure_reason = "postcondition_not_satisfied"
            elif ok is None:
                execution = "UNKNOWN"      # 观测不到 → 不写样本、不声称恢复
        entry["execution"] = execution
        stages.append(entry)
        self._emit(events, "experience_execution",
                   experience_id=exp.experience_id,
                   execution=execution,
                   uniqueness_count=adapter.last_count,
                   **self._execution_result_fields(execution))

        if execution == "SUCCESS":
            self._queue_sample(samples, ctx, exp, gres, adapter,
                               fingerprint, elapsed, stages, result="SUCCESS")
        elif execution == "FAILURE":
            self._queue_sample(samples, ctx, exp, gres, adapter,
                               fingerprint, elapsed, stages, result="FAILURE",
                               guard_reason=failure_reason)
        if execution in ("FAILURE", "UNKNOWN"):
            # FAILURE：本候选没用 → 换下一个（设计 5.1 的 continue）
            # UNKNOWN：观测不到结果 → 不声称恢复（不猜）
            return None
        # SUCCESS / not_dispatched（aux 交调用方重跑）→ 本候选救回来了
        return self._recovered(
            "experience",
            self._attach(
                {"stages": stages,
                 "experience_id": exp.experience_id,
                 "candidate": experience_locator(exp)[0],
                 "screen": exp.screen_id,
                 # 9.5 补丁导出 / recoveries 行需要：候选类型 = Guard 已
                 # **验证过**的运行时类型（== ctx.expected_type）。期望类型
                 # 未知时留 None——后续 review accept 会 fail-loud 拒绝建种子
                 # （宁缺勿猜）。
                 "candidate_type": ctx.expected_type,
                 "screen_fingerprint_match": fp_match},
                # 4.7 样本（不含 step_id——恢复发生在 record_step 之前，
                # steps.id 那时还不存在；由管线在 record_step 之后补真 id）
                samples, events),
            experience_locator(exp)[0])

    @staticmethod
    def _emit(events: list[dict], event_type: str, **detail) -> None:
        """§11.1 事件 payload 的**唯一**构造点（事件名 + detail）。

        与 4.7 样本同款分工：引擎产出 payload，管线落库——trace 写入需要
        run_id/tc_run_id，而引擎不持有 TraceStore（9.1 隔离纪律）。
        事件名不在这里校验：`tracer.storage.record_experience_event` 是
        `EXPERIENCE_EVENT_TYPES` 的 fail-loud 闸门，两处都判等于两套规则。
        """
        events.append({"event_type": event_type, "detail": detail})

    @staticmethod
    def _execution_result_fields(execution: str) -> dict:
        """`experience_execution` 的 `result` 字段（设计 §11.1 示例的口径）。

        - `SUCCESS` → §10 的机制分类 `RECOVERED_EXPERIENCE`；
        - `not_dispatched`（aux 无 dispatch 语义）→ **结果此刻不可观测**，
          标 `pending_observation`，由调用方观测后经 `resolve_deferred_sample`
          回填（与 4.7 待定样本同一机制——两边都记，就不该一边猜一边不猜）；
        - `FAILURE` / `UNKNOWN` → 没有恢复分类可报，留 None（不谎报）。
        """
        if execution == "SUCCESS":
            return {"result": recovered_kind("experience")}
        if execution == "not_dispatched":
            return {"result": None, "pending_observation": True}
        return {"result": None}

    @staticmethod
    def _type_match(gres, ctx: RecoveryContext) -> bool | None:
        """类型校验的观测（4.7 样本与 §11.1 事件共用一份推导）。

        `None` = 没走到类型校验（NOT_FOUND/AMBIGUOUS）或期望类型未知
        （校验被跳过）——不谎报 True。
        """
        if gres.reason == "TYPE_MISMATCH":
            return False
        if gres.outcome == "EXECUTE":
            return True if ctx.expected_type else None
        return None

    @staticmethod
    def _queue_sample(samples: list, ctx: RecoveryContext, exp, gres,
                      adapter: _FindAllAdapter, fingerprint: str | None,
                      elapsed: int, stages: list, *, result: str,
                      guard_reason: str | None = None,
                      deferred: bool = False) -> None:
        """4.7 的样本 payload（**唯一**的落库内容决策点）。

        只由 `record_as_sample`（MISS/风险拦截 = False）与执行结果决定是否
        被调用——E11 口径在这里收口，别的模块不再判一次。
        无 `run_id`（没有追溯链）时不落库：样本行没有归属 run 就等于伪造
        证据，宁可缺一条（E5 的「宁缺勿假」同款）。

        `deferred=True`（aux 专用）：执行结果引擎侧不可观测 → 产出
        `result=None` + `pending_observation` 的**待定**样本，等调用方观测
        后经 `resolve_deferred_sample` 回填。待定样本**不带 guard_reason**
        （还没观测到，不是「用了但错了」）。
        """
        if not ctx.run_id:
            # 不落库不是「静默跳过」：留一条 stage，否则「有经验库但没记样本」
            # 在报告上与「根本没查」不可区分（本模块一贯的「做不了就留痕」）。
            stages.append({"stage": "experience_sample",
                           "outcome": "no_run_id",
                           "experience_id": exp.experience_id})
            return
        # 与 §11.1 事件共用同一份推导（`_type_match`）——同一观测在两处各写
        # 一遍必然某天分叉，而报告会把两处的差异读成「数据不一致」。
        type_match = RecoveryEngine._type_match(gres, ctx)
        samples.append({
            "experience_id": exp.experience_id,
            "run_id": ctx.run_id,
            "app_build": ctx.app_build,
            "result": None if deferred else result,
            "guard_reason": None if deferred else guard_reason,
            "effective_risk": getattr(ctx.effective_risk, "name", None),
            "uniqueness_count": adapter.last_count,
            "element_type_match": type_match,
            "screen_fingerprint": fingerprint,
            "latency_ms": elapsed,
            **({"pending_observation": True} if deferred else {}),
        })

    # --- LLM 校验链（9.3 五项 + 10.4 契约 + 10.5 budget/熔断） ---

    def _source_subset(self, screen_id: str) -> dict:
        """12.3 → reconcile_local 输入适配（顺延①）：Repository 合并视图
        派生本屏子集。合并视图保留的是「登记过的 id」——动态前缀的人工
        登记实例（12.2 预期流程）也在其中，对 DRIFT/MATCH 判定语义正确。"""
        if self.repo is None or not screen_id:
            return {}
        try:
            elements = self.repo.elements_of(screen_id)
        except Exception:  # noqa: BLE001 — repo 侧异常不伪装成空屏
            return {}
        entries = []
        for e in elements or []:
            entries.append({
                "accessibilityId": e.id,
                "resolution_type": "literal",
                "screen": screen_id,
                "type": getattr(e, "type", None),
            })
        return {"elements": entries, "screens": [screen_id]}

    def _prompt_source_subset(self, screen_id: str) -> list[dict]:
        """[SOURCE METADATA] 分区（可信，来自构建产物）：id/type/风险。"""
        if self.repo is None or not screen_id:
            return []
        try:
            elements = self.repo.elements_of(screen_id)
        except Exception:  # noqa: BLE001
            return []
        return [{"id": e.id,
                 "type": getattr(e, "type", None),
                 "risk": getattr(getattr(e, "risk", None), "name", None)}
                for e in elements or []]

    def _llm_stage(self, ctx: RecoveryContext, stages: list,
                   screen_res, recon: dict | None,
                   page_red: str | None,
                   samples: list[dict] | None = None,
                   events: list[dict] | None = None) -> RecoveryResult:
        samples = samples if samples is not None else []
        events = events if events is not None else []

        def miss(failure_type: str, stage: dict,
                 count_failure: bool = True) -> RecoveryResult:
            stages.append(stage)
            # 10.5 失败定义 = 一次尝试未以 RECOVERED 收尾；预算拒绝没有
            # 发生「尝试」（review P3-2）——不计入熔断连续失败。
            if self.budget is not None and count_failure:
                self.budget.record_failure()
            return RecoveryResult(
                recovered=False, failure_type=failure_type,
                detail=self._attach(
                    {"stages": stages, "recovery": "llm_missed"},
                    samples, events))

        if page_red is None:
            return miss(ctx.failure_type, {"stage": "llm",
                                           "outcome": "no_page_source"},
                        count_failure=False)
        if self.budget is not None and \
                not self.budget.try_acquire(ctx.testcase_id):
            # 矩阵 #10：熔断/预算耗尽后**不发**新 API 调用（FakeLLM 计数为证）
            return miss("LLM_BUDGET_EXCEEDED",
                        {"stage": "llm", "outcome": "LLM_BUDGET_EXCEEDED"},
                        count_failure=False)

        timeout = self.budget.config.timeout_seconds if self.budget else 20
        # recon 是**运行时派生**数据——归位不可信区（review P2-2：放在
        # 可信区会让 [SYSTEM INSTRUCTIONS] 的「不遵循 UI 文字」约束罩不住
        # 它，prompt injection 面）
        untrusted = page_red
        if recon:
            untrusted += ("\n[runtime reconciliation — runtime 派生，"
                          "同样不可信]\n"
                          + json.dumps(recon, ensure_ascii=False))
        prompt = build_recovery_prompt(
            goal_element=ctx.element_id or "",
            goal_action=ctx.action or "tap",
            error=ctx.failure_type or "",
            source_subset=self._prompt_source_subset(
                (screen_res.screen if screen_res and screen_res.screen
                 else ctx.screen_id) or ""),
            page_source=untrusted)
        try:
            raw = self.llm.complete(prompt, timeout=timeout)
        except Exception as e:  # noqa: BLE001 — provider 故障如实分类
            return miss("LLM_PROVIDER_ERROR",
                        {"stage": "llm", "outcome": "LLM_PROVIDER_ERROR",
                         "error": f"{type(e).__name__}: {e}"})

        parsed = parse_llm_output(raw)
        if parsed.ignored_fields:
            stages.append({"stage": "llm", "outcome": "ignored_fields",
                           "fields": sorted(parsed.ignored_fields)})
        if not parsed.valid:
            return miss("LLM_INVALID_OUTPUT",
                        {"stage": "llm", "outcome": "LLM_INVALID_OUTPUT"})
        # 动作一致性（10.4：action 必须与原动作一致；P0 P1-2 白名单同款）
        if ctx.action and parsed.action != ctx.action:
            return miss("LLM_INVALID_OUTPUT",
                        {"stage": "llm", "outcome": "LLM_INVALID_OUTPUT",
                         "reason": f"action {parsed.action!r} != "
                                   f"original {ctx.action!r}"})
        # 9.3-5 confidence（先于执行；只过滤不当安全依据，10.4 注）
        conf = parsed.confidence or 0.0
        min_conf = self.budget.config.min_confidence if self.budget else 0.85
        if conf < min_conf:
            return miss("LLM_LOW_CONFIDENCE",
                        {"stage": "llm", "outcome": "LLM_LOW_CONFIDENCE",
                         "confidence": conf})

        candidate = {"type": parsed.target_type or "accessibility_id",
                     "value": parsed.target_value}
        stages.append({"stage": "llm", "outcome": "candidate",
                       "target": candidate, "confidence": conf})

        # P2-04（E1）：校验链收口到 experience.runtime_guard 共享实现——
        # 本函数不再维护第二套规则（red-line：同一输入与 Experience Guard
        # 结论逐位一致）。
        # Screen（9.3-3）先于设备操作：纯 Repository 比对 + 候选 risk 读取
        #（fail-closed：未登记 = candidate_screen=None）。
        # 当前屏推导与 Experience 路径共用 `_current_screen_id`（E1：两条
        # 路径同一输入必须同一结论，推导也不许各写一份）。
        current_screen_id = self._current_screen_id(screen_res, ctx) or ""
        candidate_screen = risk = None
        eff = None
        try:
            eff = self.repo.resolve(candidate["value"], build=ctx.app_build)
            candidate_screen, risk = eff.screen, eff.risk
        except Exception:  # noqa: BLE001 — UnknownReference 等 → 未登记
            pass
        found_box: list = []

        def _find():
            found = ctx.find_with((candidate,))
            found_box.clear()
            found_box.extend(found if isinstance(found, (list, tuple))
                             else [found])
            return found_box

        def _policy_check() -> None:
            from executor.guard import GuardContext
            self.guard.check(GuardContext(
                risk=eff.risk, screen_id=eff.screen,
                element_id=eff.id, action=parsed.action or "tap"))

        # review P3-4：AND-OR 惯用法换显式条件——将来 _policy_check 若改成
        # 可能假值的形态，静默变 None 会跳过 10.1 Guard。
        policy_check = (_policy_check
                        if (self.guard is not None and eff is not None)
                        else None)
        result = guard_candidate(
            current_screen=current_screen_id or None,
            candidate_screen=candidate_screen,
            find=_find,
            expected_type=ctx.expected_type,
            effective_risk=risk,
            policy_check=policy_check,
            confidence=conf,
            min_confidence=min_conf)
        validate_entry = {"stage": "validate", "outcome": result.outcome,
                          "reason": result.reason,
                          "record_as_sample": result.record_as_sample}
        if result.outcome != "EXECUTE":
            # miss 负责追加终态段——validate 段全路径恰好入栈一次
            #（E1 红线测试钉住：trace 的 validate 段必须带 outcome）
            return miss(GUARD_REASON_TO_FAILURE_TYPE.get(
                            result.reason, ctx.failure_type), validate_entry)
        stages.append(validate_entry)
        element = found_box[0]

        # 全链通过：动作步执行（redispatch 用**原步骤的值**——LLM 编造的
        # 输入内容永不采纳）；aux 步骤不执行，返回策略给管线做覆盖重跑。
        if self.budget is not None:
            self.budget.record_success()
        if ctx.redispatch is not None:
            try:
                ctx.redispatch(element)
            except Exception as e:  # noqa: BLE001 — 重发失败=恢复未完成
                # （原症状保留；异常逃出会让管线整例崩掉、steps 无行——
                # M4 Gate 真机实锤）
                return miss("LLM_REDISPATCH_FAILED",
                            {"stage": "redispatch",
                             "outcome": f"{type(e).__name__}: "
                                        f"{str(e)[:120]}"})
        result = self._recovered(
            "llm",
            self._attach(
                {"stages": stages, "candidate": candidate,
                 "confidence": conf,
                 # 9.5 补丁导出需要：候选登记屏 + 类型（无则导出 fail-loud）
                 "screen": current_screen_id,
                 "candidate_type": getattr(eff, "type", None)},
                samples, events),
            candidate)
        # ⑦（9.4）：LLM 校验通过后写 RUN_MEMO——同 run 同漂移免重复调用
        if ctx.element_id:
            self.run_memo.save(current_screen_id, ctx.element_id,
                               ctx.app_build, candidate)
        return result
