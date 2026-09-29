# P1 Schema 复盘（Task 1.6，2026-09-29）

> 5 条真实用例（suites/smoke/）在真机跑通后的复盘。结论先行：**无阻塞问题，
> schema_version 升 0.2**。以下按「别扭点 → 处理」记录。

## 1. TargetRef 限定名语法糖位置不统一（已修，resolver 缺口）

- **现象**：`TargetRef(id="HomeView.go_search")` 在 lint（`_lint_ref` 有限定名
  分支）通过，运行时 `resolve()` 却抛 UnknownReferenceError——两个入口规则不一致。
- **修复**：`Repository.resolve` 的 TargetRef 分支补齐限定名处理（ref.id 含 "."
  → 显式 screen），与 `_lint_ref` 同规则。
- **教训**：lint 与 resolve 共享同一套引用语义，规则只应写一次。0.2 不改 schema
  结构；V2 若引入更多语法糖应抽共享的 parse 函数。

## 2. 步骤形态判型的脆弱性（已修）

- **现象**：`WaitStep`/`AssertionStep` 无 `action` 字段，runner 里
  `step.action` 直接 AttributeError；且 Pydantic 模型上 `getattr(step, "action", None)`
  不安全（模型有 `model_config` 等属性，判型要精确）。
- **修复**：`hasattr(step, "wait_for") / hasattr(step, "assertion") / else step.action`
  三分支判型；`record_step` 也改用分派后的 action 名（原 finally 块里的
  `step.action` 会二次炸）。
- **复盘**：6.3 的三形态 union 是静态可判型的，schema 0.2 给三形态各加一个
  `kind` 判别字段？——**不加**（YAML 作者不该写冗余键，判别由 key 本身承担：
  `wait_for:`/`assertion:`/`action:`，extra=forbid 已保证互斥）。runner 的
  hasattr 判型保留。

## 3. `active` 语义与实际可用性存在差距（记账 Task 2.1）

- **现象**：`wait_for screen active` 成功（marker 可 find）后立即 tap 会被
  SwiftUI NavigationStack 转场吞掉（probe4/5 实测）。runner 现以
  `TAP_SETTLE_SECONDS=1.0` 固定缓冲近似「树静止」。
- **0.2 决策**：schema 不变（`active` 条件语义正确——屏在树上就为 active）。
  「可交互」是执行层职责：Task 2.1 wait engine 落地「树静止判定」
  （连续 N 次 page_source 哈希不变）替换固定 sleep，删除 TAP_SETTLE_SECONDS。

## 4. `polling_interval` 断言层缺失（已修）

- AssertionSpec 没有 polling_interval（WaitSpec 有）——runner 最小 assertion
  复用 wait 轮询时曾想借用导致 AttributeError。已在 runner 内联轮询（0.5s）。
  **0.2 不给 AssertionSpec 加 polling_interval**：断言是终态判定不是等待，
  timeout 兜底即可；若 2.2 需要再加。

## 5. precondition.reset 仍是松散 dict（R5-3 遗留，0.2 冻结前必须解决）

- 5 条用例都写了 `precondition: {reset: RELAUNCH}`，schema 未校验枚举，
  `RESET_STATEE` typo 会静默通过到运行期才炸。**0.2 升版时一并定型
  EnvSpec**（枚举：RESET_STATE/RELAUNCH/TERMINATE/LOGOUT/REINSTALL/SNAPSHOT）。
  本次 5 条用例只用 RELAUNCH，风险已知可控。

## 6. 别扭但保留的点

- **secret 引用只支持 `${VAR}` 完整匹配**（`MAX_ID` 正则）：`prefix-${VAR}`
  不解析。够用且防部分替换泄漏（runner `_resolve` 注释），保留。
- **`screen:` 语法糖 vs `type: screen` 显式写法**：YAML 里 `screen:HomeView`
  字符串形式可读性好，保留；lint 曾误判（本任务修复）说明两层都要认识语法糖，
  已由 TargetRef `_accept_sugar` 集中处理。
- **idempotency 声明负担**：登录按钮要求显式 `IDEMPOTENT`（设计 6 示例），
  5 条用例共写了 6 处。lint 的 NON_IDEMPOTENT 关键词启发式只兜底「疑似非幂等
  未声明」，不要求幂等元素显式声明（可从 Repository metadata 继承）——0.2 起
  runner 侧 target 解析后可取 EffectiveElement.idempotency 作为默认值，
  YAML 仅在覆盖 repo 值时才需要写。本次未实现（记账 2.x）。

## 结论

schema 0.1 结构经 5 条真实用例验证**无阻塞缺陷**；上述修复均为执行层/工具层。
升 **0.2**：SCHEMA_VERSION 与 SUPPORTED_SCHEMA_VERSIONS 同步（0.1 用例不再
可加载——loader 报「unsupported schema_version: '0.1'」，明确拒优于静默放行），
precondition EnvSpec 定型顺延至 M1 末冻结盘点（R5-3 记账不变）。
