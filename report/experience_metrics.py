"""experience_metrics.py — 设计 17 节的指标聚合（Task 4.3 / P2 收口）。

设计 17 的五个指标 + 一个由 review 提出的口径补充，全部落在这里：

| 指标 | 定义 |
|---|---|
| Experience Resolution Rate | 由 Experience 解决的 Recovery / **全部 Recovery Attempts** |
| 四状态计数 | CANDIDATE / VERIFIED / DEGRADED / REJECTED |
| Promotion Rate | 已 Promote / VERIFIED |
| lookup / cache / LLM 延迟梯度 | 各段单次操作耗时均值（验证「Cache < Store < LLM」） |
| Revalidation 成功率 | REVALIDATED 次数 / 曾降级次数 |

## 口径上必须说清的五件事

1. **分母是「全部 Recovery Attempts」，不是「Experience 被查过几次」。**
   `experience_lookup` 事件只覆盖「真的查了库」的那些尝试——`no_store` /
   `incomplete_key` / `no_page_source` / `no_find_all` 四类**根本没尝试**查库
   （Task 2.4 明文「没查库就不发事件」），拿事件当分母会系统性高估命中率。
   所以分母取 **trace `steps` 里带 recovery 标记的步骤数**——那是「失败步骤进了
   恢复管线」的步骤级事实，与 Experience 路径是否被咨询无关。分子同样取步骤级
   （见第 2 点），两边同源、单位一致。
   plan 说「数据源为 trace `experience_*` 事件 + experience 库」，此处**扩到
   trace 的 `steps`**，理由即上述（事件表在语义上无法表达「没查库的 attempt」）。

2. **分子按「机制名」判，不按 §10 的分类标签**（review_p2_task43 P2-2）。
   `recovered_kind` 是「对使用者有意义」的标签，会被**上下文压过**——断言目标
   漂移的上下文（`context="assertion_target"`）把机制名盖成
   `RECOVERED_ASSERTION_TARGET`，而那条路径正是「aux 命中 Experience」（矩阵
   #15）。拿标签当分子会让设计 §17 的**核心指标对整整一类 Experience 恢复记 0**。
   判据 = `recovery_kind == "experience"` ∧ 有 `recovered_kind`（第二个条件必需：
   `_aux_rerun_failed` 在**失败**步骤上也写 `recovery_kind`）。
   `by_recovered_kind` 仍按 §10 标签分组——两者刻意不同，一个答「谁救的」、
   一个答「对使用者意味着什么」。

3. **Promotion Rate 的分子限定 VERIFIED**（review_p2_task43 P2-1）。设计 §17 的
   定义是「Verified 中被 Promote 的比例」；不限定会让分子含已 DEGRADED/REJECTED
   的经验（§9.4：Promotion 后降级**不撤销**已进 Git 的策略），而分母只数
   VERIFIED——比率能算出 **300%**。

4. **revert 之后的「预期恢复」要能与真回归区分**（review_p2_task42 建议动作 #2）。
   Promotion 被 `git revert` 之后，被转正的策略不在 find 链上，每次 run 都会
   稳定地多走一次恢复——这**不是**新回归。可判定的信号是：一条
   `promoted=True` 的 Experience 仍在产生成功的 `experience_execution`
   （说明它的策略没在 find 链里生效，很可能已被 revert）。故单列
   `promoted_but_recovering` 计数，并在报告里注明这是**推断**而非事实。

5. **延迟梯度只报能测的段**。`cache` / `store` 来自 `experience_lookup` 事件的
   `latency_ms`（**浮点毫秒**——`int()` 截断会让亚毫秒操作恒记 0，梯度退化成
   `0 < 0`），`llm` 来自 `llm_call` stage 的 `latency_ms`。`promoted` 段**不报**
   ——被转正的策略是 P1 `find()` 链上的一条普通 Locator，它的耗时在
   `steps.latency_ms`（整步）里，与恢复期内部的单次操作不同量级，硬放在同一张
   梯度表里比会得出错误结论。`deterministic`（settle / reconcile / run_memo）
   是纯进程内计算，没有单独计时，同样标 N/A 而不是填 0——填 0 会被读成
   「快到测不出」，那是另一种谎。
"""
from __future__ import annotations

import html as _html
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from experience.models import Experience, ExperienceStatus
from experience.verifier import is_state_transition
from tracer.storage import EXPERIENCE_EVENT_TYPES

__all__ = [
    "ExperienceMetrics",
    "RECOVERY_MARKER_KEYS",
    "entered_recovery",
    "compute_experience_metrics",
    "collect_experience_metrics",
    "render_experience_metrics_section",
]

# 「这一步进过恢复管线」的 detail 标记（四选一即算一次 Recovery Attempt）。
# 四个键对应管线里四条互斥的写入路径：
#   recovery            已恢复（动作步 / aux 步成功）
#   recovery_attempted  未恢复（动作步失败）
#   recovered_kind      aux 重跑失败时只盖机制名，不盖分类标签
#   recovery_kind       同上（机制名是事实，分类标签只盖成功步骤）
RECOVERY_MARKER_KEYS = ("recovery", "recovery_attempted", "recovered_kind",
                        "recovery_kind")

# 引擎里 Experience 机制的**机制名**（`rec.kind`，不是 §10 的分类标签）
_EXPERIENCE_MECHANISM = "experience"

_STATUSES = ("CANDIDATE", "VERIFIED", "DEGRADED", "REJECTED")


def entered_recovery(step_detail: dict | None) -> bool:
    """这一步是否**进过**恢复管线（= 一次 Recovery Attempt）。"""
    if not step_detail:
        return False
    return any(k in step_detail for k in RECOVERY_MARKER_KEYS)


@dataclass(frozen=True)
class ExperienceMetrics:
    """设计 17 的指标快照。比率在**无分母**时是 `None` 而不是 0.0。"""

    # ① Experience Resolution Rate
    recovery_attempts: int = 0
    experience_resolved: int = 0
    resolution_rate: float | None = None
    # §10 的分类标签分布（按**上下文编码**的标签分组，与分子的判据不同——
    # 分子按机制名，见 compute 的注释）
    by_recovered_kind: dict = field(default_factory=dict)

    # ② 四状态计数
    status_counts: dict = field(default_factory=dict)

    # ③ Promotion Rate。
    # `promoted` 的语义**限定为「VERIFIED 中已 promote 的条数」**（设计 §17 的
    # 定义是「Verified 中被 Promote 的比例」）。不这样限定会让分子含已
    # DEGRADED/REJECTED 的经验——§9.4 明文「Promotion 后 DEGRADED 不自动撤销
    # 已进 Git 的策略」，那些条数留在分子里而分母只数 VERIFIED，比率能算出
    # **300%**（review_p2_task43 P2-1）。
    promoted: int = 0
    promotion_rate: float | None = None
    promoted_but_recovering: int = 0

    # ④ 延迟梯度（毫秒；键缺失 = 该段不可测）
    latency: dict = field(default_factory=dict)
    latency_samples: dict = field(default_factory=dict)

    # ⑤ Revalidation 成功率
    revalidation_attempts: int = 0
    revalidation_successes: int = 0
    revalidation_rate: float | None = None

    # 事件面计数（lookup/cache/store 的规模，解释率的时候要用）
    events: dict = field(default_factory=dict)


def _rate(num: int, den: int) -> float | None:
    return (num / den) if den else None


def _mean(values: list[int]) -> float | None:
    return (sum(values) / len(values)) if values else None


def _stages_of(step_detail: dict) -> list[dict]:
    """步骤 detail 里恢复引擎的 stages（成功与未恢复两条路径各一个键）。"""
    section = (step_detail.get("recovery")
               or step_detail.get("recovery_attempted") or {})
    return section.get("stages") or []


def compute_experience_metrics(*, step_details: list[dict] | None = None,
                               events: list[dict] | None = None,
                               experiences: list[Experience] | None = None,
                               state_events: list[dict] | None = None
                               ) -> ExperienceMetrics:
    """**纯函数**：原始行 → 指标。不读库、不看时钟、不碰设备（E13 精神）。

    入参都是「已经取出来的行」：
      - `step_details`：每条 steps 行的 `detail` dict；
      - `events`：每条 `experience_*` infra 事件的 `{"event_type","detail"}`；
      - `experiences`：experience 库的全部 Experience；
      - `state_events`：全部状态事件 `{"experience_id","from_status",
        "to_status","reason"}`。
    """
    step_details = step_details or []
    events = events or []
    experiences = experiences or []
    state_events = state_events or []

    # ① Resolution Rate（分子分母都取**步骤级**）
    attempts = [d for d in step_details if entered_recovery(d)]
    by_kind: dict[str, int] = {}
    for d in attempts:
        kind = d.get("recovered_kind")
        if kind:
            by_kind[kind] = by_kind.get(kind, 0) + 1
    # 分子按**机制名**判，不按 §10 的分类标签（review_p2_task43 P2-2）：
    # `recovered_kind` 是「对使用者有意义」的标签，会被**上下文压过**——
    # 断言目标漂移的上下文（`context="assertion_target"`，`cli/pipeline.py` 的
    # aux 路径）把机制名盖成 `RECOVERED_ASSERTION_TARGET`，而那条路径恰恰是
    # 「aux 命中 Experience」（矩阵 #15）。用标签当分子会把这一整类记 0。
    # 第二个条件（有 `recovered_kind`）是必需的：`_aux_rerun_failed` 会在
    # **失败**步骤上写 `recovery_kind`（那是事实），只判机制名会把失败算成解决。
    resolved = sum(1 for d in attempts
                   if d.get("recovery_kind") == _EXPERIENCE_MECHANISM
                   and "recovered_kind" in d)

    # ② 四状态计数
    status_counts = {s: 0 for s in _STATUSES}
    for exp in experiences:
        status_counts[exp.status.value] = status_counts.get(
            exp.status.value, 0) + 1

    # ③ Promotion Rate（分子**限定 VERIFIED**——见 dataclass 注释里的 300% 反例）
    verified = status_counts.get("VERIFIED", 0)
    promoted = sum(1 for e in experiences
                   if e.promoted and e.status is ExperienceStatus.VERIFIED)
    # revert 推断：promoted 但仍在本期产生了成功的 experience_execution
    hit_ids = {ev["detail"].get("experience_id")
               for ev in events
               if ev.get("event_type") == "experience_execution"
               and (ev.get("detail") or {}).get("execution") == "SUCCESS"}
    promoted_but_recovering = sum(
        1 for e in experiences
        if e.promoted and e.experience_id in hit_ids)

    # ④ 延迟梯度
    def _ms_of(x):
        """延迟样本（浮点毫秒）。bool 是 int 的子类，显式排除。"""
        return x if isinstance(x, (int, float)) and not isinstance(x, bool) \
            else None

    cache_ms = [v for ev in events
                if ev.get("event_type") == "experience_lookup"
                and (ev.get("detail") or {}).get("source") == "cache"
                if (v := _ms_of((ev.get("detail") or {}).get("latency_ms")))
                is not None]
    store_ms = [v for ev in events
                if ev.get("event_type") == "experience_lookup"
                and (ev.get("detail") or {}).get("source") == "store"
                if (v := _ms_of((ev.get("detail") or {}).get("latency_ms")))
                is not None]
    llm_ms = [v for d in step_details for st in _stages_of(d)
              if st.get("stage") == "llm_call"
              if (v := _ms_of(st.get("latency_ms"))) is not None]
    latency = {"cache": _mean(cache_ms), "store": _mean(store_ms),
               "llm": _mean(llm_ms)}
    latency_samples = {"cache": len(cache_ms), "store": len(store_ms),
                       "llm": len(llm_ms)}

    # ⑤ Revalidation 成功率
    # 只数**真跳变**进 DEGRADED 的行：`mark_revalidation_required` 刻意写
    # from == to 的同值标记行，把当前正处于 DEGRADED 的那条也算进来会把
    # 1 次真降级报成 2 次（review_p2_task43 P3-1）。判据用 `is_state_transition`
    # ——与 `has_state_event_since_transition` 的 SQL 同义，不各写一份。
    reval_attempts = sum(
        1 for ev in state_events
        if ev.get("to_status") == "DEGRADED"
        and is_state_transition(ev.get("from_status"), ev.get("to_status")))
    reval_success = sum(1 for ev in state_events
                        if ev.get("reason") == "REVALIDATED"
                        and ev.get("to_status") == "VERIFIED")

    # 事件面
    counts: dict[str, int] = {}
    for ev in events:
        et = ev.get("event_type")
        counts[et] = counts.get(et, 0) + 1
    counts["lookup_source_cache"] = sum(
        1 for ev in events
        if ev.get("event_type") == "experience_lookup"
        and (ev.get("detail") or {}).get("source") == "cache")

    return ExperienceMetrics(
        recovery_attempts=len(attempts),
        experience_resolved=resolved,
        resolution_rate=_rate(resolved, len(attempts)),
        by_recovered_kind=by_kind,
        status_counts=status_counts,
        promoted=promoted,
        promotion_rate=_rate(promoted, verified),
        promoted_but_recovering=promoted_but_recovering,
        latency=latency,
        latency_samples=latency_samples,
        revalidation_attempts=reval_attempts,
        revalidation_successes=reval_success,
        revalidation_rate=_rate(reval_success, reval_attempts),
        events=counts,
    )


# --- 采集（本模块唯一的 I/O 区：只做「读行」，不做判定） --------------------


def collect_experience_metrics(*, trace_db: str | Path,
                               experience_store) -> ExperienceMetrics:
    """从 trace.db + experience 库取行，交给上面的纯函数。

    与 `experience/verifier.py` 同款分工：判定全在纯函数里，本函数只负责
    把数据搬过来（I/O 与规则分开，规则才可脱离设备单测）。

    读 `steps` 全表而不是只读本次 run：Resolution Rate 是**累积**指标
    （设计 17：「应随运行次数上升」），单 run 的分母太小、噪声压过信号。
    """
    conn = sqlite3.connect(str(trace_db))
    try:
        step_details = []
        for row in conn.execute("SELECT detail_json FROM steps"):
            step_details.append(json.loads(row[0]) if row[0] else {})
        # 显式列举 §11.1 的**全部** 12 个事件类型，不用 `LIKE 'experience_%'`
        # （review_p2_task43 P3-5）：① 12 个里有 4 个不带该前缀
        # （candidate_created / candidate_verified / promotion_proposed /
        # promotion_approved），LIKE 是**隐式白名单**——将来任何指标想用
        # `promotion_*` 事件都会静默取到 0；② LIKE 里 `_` 是单字符通配符，
        # `'experience_%'` 会匹配 `'experienceXlookup'`。
        types = sorted(EXPERIENCE_EVENT_TYPES)
        placeholders = ",".join("?" * len(types))
        events = []
        for et, detail in conn.execute(
                f"SELECT event_type, detail_json FROM infra_events"
                f" WHERE event_type IN ({placeholders})", types):
            events.append({"event_type": et,
                           "detail": json.loads(detail) if detail else {}})
    finally:
        conn.close()

    experiences = experience_store.list()
    state_events: list[dict] = []
    for exp in experiences:
        for ev in experience_store.get_state_events(exp.experience_id):
            state_events.append({"experience_id": exp.experience_id,
                                 "from_status": (ev.from_status.value
                                                 if ev.from_status else None),
                                 "to_status": ev.to_status.value,
                                 "reason": ev.reason})

    return compute_experience_metrics(step_details=step_details, events=events,
                                      experiences=experiences,
                                      state_events=state_events)


# --- 渲染（HTML 片段；纯字符串拼接，无 I/O） -------------------------------


def _esc(text) -> str:
    """HTML 转义（review_p2_task43 P3-4）。

    `report/html.py` 有自己的 `_esc`，但本模块被它 import（反向会成环），
    所以各持一份**同一个标准库调用**（`html.escape`），不是两套实现。
    为什么要转义：trace 库是**可写的外部输入**（`steps.detail_json` 里的
    `recovered_kind` 等值），而报告会被打开在浏览器里。
    """
    return _html.escape(str(text))


def _pct(x: float | None) -> str:
    return "N/A" if x is None else f"{x * 100:.1f}%"


def _ms(x: float | None) -> str:
    """毫秒渲染——**亚毫秒要看得见**（review_p2_task43 P3-2）。

    `{x:.1f}ms` 会把 0.004ms 渲染成 `0.0ms`，而设计 §17 的延迟梯度正是要
    分辨 cache 与 store 这种同量级的段——一律显示 `0.0ms` 等于把梯度指标
    废掉，还会让读者读成「快到测不出」（那是另一种谎，本模块 docstring 里
    已经为 `deterministic` 立过这条规矩）。
    """
    if x is None:
        return "N/A"
    if x < 1:
        return f"{x:.3f}ms"
    return f"{x:.1f}ms"


def render_experience_metrics_section(m: ExperienceMetrics) -> str:
    """设计 17 的指标段（14.5 报告体系的一部分）。

    比率无分母时渲染 **N/A**，不是 0.0%：一次都没跑过与「跑了全失败」是两件
    事，报告把它们写成同一个数字会误导基线判读。
    """
    def card(label: str, value: object, cls: str = "") -> str:
        return (f'<div class="card {_esc(cls)}">'
                f'<div class="num">{_esc(value)}</div>'
                f'<div class="label">{_esc(label)}</div></div>')

    status_cards = "".join(
        card(s, m.status_counts.get(s, 0), f"c-exp-{s.lower()}")
        for s in _STATUSES)

    # 延迟梯度：只列可测段，不可测的**显式说明原因**（不填 0 假装很快）
    grad_rows = [
        ("cache", "缓存命中（进程内 LRU）", m.latency.get("cache"),
         m.latency_samples.get("cache", 0)),
        ("store", "Experience Store 磁盘 lookup", m.latency.get("store"),
         m.latency_samples.get("store", 0)),
        ("llm", "LLM complete() 调用", m.latency.get("llm"),
         m.latency_samples.get("llm", 0)),
        ("deterministic", "settle / reconcile / run_memo（纯进程内）", None, 0),
        ("promoted", "Promoted 策略（P1 find 链上的普通 Locator）", None, 0),
    ]
    grad_html = "".join(
        f"<tr><td><code>{_esc(name)}</code></td><td>{_esc(note)}</td>"
        f"<td>{_esc(_ms(v))}</td><td>{_esc(n)}</td></tr>"
        for name, note, v, n in grad_rows)

    kinds = "".join(f"<li><code>{_esc(k)}</code>: {_esc(v)}</li>"
                    for k, v in sorted(m.by_recovered_kind.items())) \
        or "<li>（无）</li>"
    ev = m.events

    return f"""<section class="exp-metrics">
<h2>Experience 指标（设计 17）</h2>
<div class="cards">
{card("Resolution Rate", _pct(m.resolution_rate), "c-rate")}
{card(f"Experience 解决 / 全部尝试",
      f"{m.experience_resolved} / {m.recovery_attempts}")}
{card("Promotion Rate", _pct(m.promotion_rate))}
{card("Revalidation 成功率", _pct(m.revalidation_rate))}
{status_cards}
</div>
<p class="note">Resolution Rate = 由 Experience 解决的 Recovery / <strong>全部
Recovery Attempts</strong>（含 Experience MISS 后回落 LLM 的、以及因缺键/缺页面
根本没查库的）。分母取自 trace <code>steps</code> 的恢复标记（步骤级），不是
<code>experience_lookup</code> 事件数——后者只覆盖「真的查了库」的尝试，拿它当
分母会系统性高估。累积口径（全部历史 run），故随运行次数上升。</p>
<ul class="note"><li>本期恢复分类：</li>{kinds}</ul>
<p class="note">lookup 事件 {_esc(ev.get('experience_lookup', 0))} 次（其中缓存命中
{_esc(ev.get('lookup_source_cache', 0))} 次）；miss {_esc(ev.get('experience_miss', 0))}；
guard_block {_esc(ev.get('experience_guard_block', 0))}；
execution {_esc(ev.get('experience_execution', 0))}。</p>
<p class="note">⚠️ 已 Promote 但仍在产生恢复命中：<strong>
{m.promoted_but_recovering}</strong> 条 —— <em>推断</em>其 Promotion 未在 find
链生效（很可能已被 <code>git revert</code>）。这是「revert 后的预期恢复」，不是
新回归；判定依据是「promoted=True 且本期仍有成功的 experience_execution」，
属启发式而非事实。</p>
<table><thead><tr><th>段</th><th>说明</th><th>单次均值</th><th>样本</th></tr>
</thead><tbody>{grad_html}</tbody></table>
<p class="note">梯度只报能测的段。<code>deterministic</code> 是纯进程内计算
（无 I/O，未单独计时）；<code>promoted</code> 的耗时在 <code>steps.latency_ms</code>
（整步）里，与恢复期内部单次操作不同量级，放在同一张表里比会得出错误结论——
两者标 N/A 而不是填 0。cache / store 是**亚毫秒级**操作，故 &lt;1ms 时保留三位
小数，让「快」与「测不到」在读数上分得开。⚠️ **修订前**（计时用
`int(delta * 1000)` 截断）产生的历史 trace 里，亚毫秒操作会被记成整数 0，读数为
`0.000ms` 属**旧数据残留**，不是本段真的没有耗时。</p>
</section>"""
