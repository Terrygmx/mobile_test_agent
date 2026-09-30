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
- **已修（Task 2.1 / P1-05）**：`executor/wait.py` 的 `wait_for_settle()` 实现树
  静止判定（连续 `stable_polls` 次树哈希不变），runner 命中条件后调用，
  `TAP_SETTLE_SECONDS` 已删除。**screen target 例外**：`active` 走 7.3 廉价路径
  （只 find marker，不拉 page_source），默认不做静止判定。
- **R11-1 真机复跑结论（3 轮 M1 Gate，2026-09-29）**：screen 豁免**确实会丢 tap**。
  第 1 轮 5/5 通过，第 2、3 轮 `profile_001` / `logout_001` 挂在
  `WAIT_TIMEOUT: target='ProfileView' condition='active'`——`wait_for screen:HomeView
  active` 成立即返回，紧随的 `go_profile` tap 被 SwiftUI 转场吞掉，ProfileView 永不出现。
  兜底缓冲删了、替代机制又不覆盖这个模式，原始事故面确实回到了无保护状态。
  **过渡方案**：`WaitConfig.settle_on_screen_wait`（默认 False = 守住 7.3 廉价路径；
  置 True 则 active 后补树静止判定）。M1 Gate 开启后复跑 3 轮全绿。
  `stable_polls=3 × stable_interval=0.25`（≈0.5s 下限）——SwiftUI push/pop 转场实测
  0.35~0.45s，默认的 2×0.2 偏紧。
  **未解决**：M3 的 gesture-ready 信号（App 侧显式标记可交互时刻）落地后应能关掉
  `settle_on_screen_wait`，恢复 7.3 的纯廉价路径。现在是「多花 page_source 换稳定」，
  不是设计意图的正解。

## 4. `polling_interval` 断言层缺失（已修）

- AssertionSpec 没有 polling_interval（WaitSpec 有）——runner 最小 assertion
  复用 wait 轮询时曾想借用导致 AttributeError。已在 runner 内联轮询（0.5s）。
  **0.2 不给 AssertionSpec 加 polling_interval**：断言是终态判定不是等待，
  timeout 兜底即可；若 2.2 需要再加。

## 5. precondition.reset 仍是松散 dict（R5-3 遗留，顺延 0.3 / M2 开头）

- 5 条用例都写了 `precondition: {reset: RELAUNCH}`，schema 未校验枚举，
  `RESET_STATEE` typo 会静默通过到运行期才炸（`reset_state` matrix 运行期
  fail-loud 兜底，但 lint 阶段就该拦）。
- **0.2 未含 EnvSpec**（0.2 仅版本号变更，见下）。M2 开头升 0.3 时定型
  EnvSpec（枚举：RESET_STATE/RELAUNCH/TERMINATE/LOGOUT/REINSTALL/SNAPSHOT），
  记账位置：本节 + R5-3 + M1 末冻结盘点。本次 5 条用例只用 RELAUNCH，
  风险已知可控。

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

## 7. Task 2.2 Review（R12）记账 — 2026-09-30

R12-1/2 已修（`executor/assertion.py`）：
- **R12-1**：`_judge` 属性读取加 stale 防护（wait.py R11-3 同款）。断言恰恰最常
  发生在转场后，find 成功后读 `.text`/`is_enabled()` 抛 stale 直接冒泡会是既非
  ValueMismatch 也非 Drift 的裸异常（不在 recovery 白名单）。现按「本轮不满足」
  继续轮询至 deadline，到点标 Drift（可恢复）；ValueError 保持 fail-loud。
- **R12-2**：`element_count expected=0` 语义 bug——schema validator 只拦
  `expected is None`，`expected=0` 是合法断言（「元素已从列表消失」），但
  ElementNotFound 分支无条件判 mismatch，expected=0/actual=0 判 FAIL 自相矛盾。
  现判 passed（expected=0 + actual=0）；expected=0 + ">=2" 仍为 mismatch
  （必然不符，值判定）。原「count=0 是 mismatch」测试改为期望非 0 的版本。

R12-3/5 记账到 **Task 2.4**（trace schema 迁移加列时一并落）：
- `detail.kind = "assertion_target"` 全库无消费点——trace 只落异常字符串前缀，
  结构化 `AssertionResult`（expected/actual/timeout）不落库；
- `AssertionValueMismatch.result` 同样只在异常属性里，error 字段只有格式化串。
- 两者都等 schema 0.1 → 迁移版的 warning/detail 列，避免现在越界改表。

R12-4 记入 **M4 漂移 build 验收口径**：
- 断言漂移恢复 = tap 重定位，恢复后**不重验断言值**（text_contains 类断言的值
  从未对新元素验证）。故障矩阵 #15 的验收就是步骤 RECOVERED，但报告口径必须写明
  **RECOVERED ≠ 断言通过**，避免被误读为该步断言成功。

## 8. Task 2.3 Review（R13）记账 — 2026-09-30

处置结果：

- **R13-1（P1）reset 矩阵双源分叉 —— 已修**（未留到 2.5）。
  正解就是 review 建议的「runner 切 ResetExecutor」，改动小且 review 已指明，
  提前做比在 2.5 里混着做更容易验证：`runner/testcase_runner.py` 新增
  `device_type` 构造参数 + `self._reset_executor`，`run()` 的 reset 分支
  从 `self.app.reset_state(...)` 改为 `self._reset_executor.reset(...)`。
  跨源一致性测试 12 条钉住（R13-1 的空头支票：capabilities.py 注释曾声称
  「有单测钉住」而实际没有）：矩阵声称支持 × 可执行、runner 源码级断言
  禁止直调 P0 matrix、RESET_STATE/RELAUNCH 经 runner.run 全链路不抛。
  其中 `_coerce` 静默归一那条做了**反向验证**（改回静默 → 测试 FAIL），
  确认测试非空过。
- **R13-2（P2）EnvironmentManager 零消费 —— 部分修，剩余记账到 Task 2.5**。
  reset 分支已接线（R13-1 一并消掉）；但 `prepare()` / `cleanup()` 仍无调用方，
  `CleanupError` 的 H10/ABORT_SUITE 语义无人能触发。**Task 2.5 验收清单必须
  显式列入**「EnvironmentManager.cleanup 接线 + CleanupError → H10
  ENVIRONMENT_FAILURE/ABORT_SUITE 落 suite 层」，否则又是「实现存在、链路不通」。
- **R13-3（P2）LOGOUT App 侧 hook 未实现 —— 已修**。`MTAResetHook.performLogout()`
  + `LoginDemoApp` 的 `-UITestLogout` 消费点（同样 `#if DEBUG` 门 + release
  fatalError）。设备实测日志 `MTA_LOGOUT_HOOK executed`。语义粒度与 RESET_STATE
  有别（只清登录态，不清缓存/UserDefaults），并由 verify V3 在设备上证明差异真实
  （LOGOUT 保留 marker / RESET_STATE 清掉）。
- **R13-4（P3）验证证据未落盘 —— 已修**。`phase0/verify_p1_task23.py`（V1~V5 五组
  探针）落 `out/p1_task23/verify_summary.json`，当前 7/7 ALL PASS。脚本内注释
  记录了两个探针坑：marker 必须由 App 自己写（simctl spawn defaults 写的是设备级
  domain）、读 plist 前必须 quiesce（UserDefaults 运行期只在内存）。附带收益：
  V5 成为「矩阵 ↔ 执行路径」跨源一致性的可执行版本。
- **R13-5（P3）卫生项 —— 已修**。`cleanup(tc=None)` 的 `tc` 参数补注释说明是为
  0.3 EnvSpec 签名稳定而保留（非死代码漏删）并加测试；`_coerce` 保留拼写容错
  （大小写/空格/连字符）但对真正未知的 device_type 改为抛 `UnsupportedResetError`
  ——此前静默归一为 REAL_DEVICE，`emulator` 这种值会被当「真机」继续跑。

commit `84dcad0` / tag `checkpoint-p1-task2.3-r13`；测试 197 → 214。

## 9. Task 2.4 Review（R14）记账 — 2026-09-30

处置结果：

- **R14-1（P1）`_duration_ms` 时区错位 —— 已修**（探针实锤，本机 UTC+8 下
  真实 sleep 1.2s 记录成 28,801,468ms）。根因：`_now()` 用 `gmtime` 写 **UTC**
  字符串，`_duration_ms` 却用 `time.mktime`（按**本地**时区）解析，整整差一个
  时区偏移。修：`calendar.timegm`。Report（2.5/2.6）马上要消费 duration，
  这个偏差会让每条用例耗时与总时长全部失真。
  测试补两条：①端到端容差断言（`< 60_000ms`，旧实现 ~28.8M 必挂）；
  ②跨 TZ 钉住解析口径——**冻结 `time.time()`** 再比 UTC vs Asia/Shanghai
  （`_duration_ms` 内部调 time.time()，不冻结会差 1~2ms，那是流逝不是时区；
  第一版测试就栽在这里）。修复后实测 1200ms → 1546ms。

- **R14-2（P2）TraceStore 零消费 —— 记账到 Task 2.5**（第三次平行实现，§8 先例）。
  实测确认：runner / 旧 Recorder / gate 脚本 / verify_p1_m1 全无 TraceStore
  引用。plan 原文是「Modify recorder.py」，实现改成平行 TraceStore——方向可
  （enum fail-loud、redact 前置、逐列具名比在旧 Recorder 上叠干净），但接线
  顺延必须在 **2.5 验收清单显式列出**，否则就是 R11-4 / R13-2 之后第三个
  「实现存在、链路不通」。

- **R14-3（P2）R12-3/5「落地」表述过强 —— 账面已修正**。实际状态是
  「`TraceStore.record_step` 的 detail 参数具备承载能力 + 单测自证」，
  **端到端未通**：本 commit runner 零改动，旧链路 steps.error 仍是格式化
  异常字符串。随 2.5 的 TraceStore 接线一并核销。
  （这正是 R13-1 那条教训的同构形态：接口就绪被写成链路打通。）

- **R14-4（P3）`failure_attribution` 无写入入口 —— 已修**。`end_testcase`
  增加 `failure_attribution` 参数供人工 triage 写入，并加枚举校验（非法值
  抛错）。**刻意不提供 `attribute_failure()` 自动归因 API**——8.2 明文：
  系统不得仅凭 ELEMENT_NOT_FOUND 判 APP_DEFECT。`mta review` 的 triage
  入口在 Task 2.6。

- **R14-5（P3）ABORTED/SKIPPED 分支无专项测试 —— 已修，且暴露出真 bug**。
  补专项用例后发现实现是 `return seen[0]`（**依赖输入顺序**）：同一组状态
  换个顺序聚合出不同终态，Report 会自相矛盾。修法：显式 `_FALLBACK_PRIORITY
  = [ABORTED, SKIPPED]`（ABORTED 跑挂被中止 > SKIPPED 没跑），不再依赖调用方
  传参顺序；并补「优先级链上的终态必须压过这两个」的断言。
  review 说「只在混合场景数据里被顺带覆盖」判断准确——顺带覆盖时顺序恰好
  一致，掩盖了顺序依赖。

commit 见 R14 修复提交；tag `checkpoint-p1-task2.4-r14`。测试 299 → 307。

**Task 2.5 验收清单（累积，务必逐条勾）**：
1. TraceStore 接线，替换/包住旧 Recorder（**R14-2**）
2. R12-3/5 端到端核销：runner 侧断言结构化结果真的走 detail_json 落库（**R14-3**）
3. EnvironmentManager.cleanup 接线 + CleanupError → H10 ENVIRONMENT_FAILURE/
   ABORT_SUITE 落 suite 层（**R13-2**）
4. 退出码 8.4 全表：`3 > 2 > 4 > 1 > 5`

## 10. Task 2.5 记账（R14 遗留四条全部收口） — 2026-09-30

§9 末尾累积的 Task 2.5 验收清单四条，本任务**全部接通**（不是记账，是
核销——每条都有代码落点 + 测试）：

1. **TraceStore 接线（R14-2）已核销** —— `runner/lifecycle.py` 的 `Lifecycle`
   持有 `store: TraceStore`，`record_step_outcome()` / `record_infra_event()`
   把 StepOutcome 与 WDA 事件写入 schema 0.1。旧 P0 Recorder 在
   `StepRunner._record` 侧走窄签名回退（M2 Gate 后退役）。
2. **R12-3/5 端到端已核销（R14-3）** —— `detail` 透传断言结构化结果到
   `TraceStore.record_step(detail=...)`，`kind="assertion_target"` 有真实
   消费点，不再只是单测自证。
3. **EnvironmentManager.cleanup 接线（R13-2）已核销** —— `runner/suite.py`
   是收口点：每条用例 `env.prepare()` → 跑 → `finally: env.cleanup()`；
   cleanup 失败 → `CleanupError` → 该条 ENVIRONMENT_FAILURE + 套件处置
   （H10，默认 ABORT_SUITE）。这是 R11-4 / R13-2 / R14-2 之后第四个
   「实现存在、链路不通」，本轮终于闭合。
4. **退出码 8.4 全表已核销** —— `runner/result.py:compute_exit_code()`，
   优先级 `3 > 2 > 4 > 1 > 5`，`SuiteResult.exit_code` 为派生属性
   （不存字段，避免 add() 后字段与派生值不一致）。

本任务新增/修复：
- `tests/integration/test_runner_fakedriver.py`（45 条）：plan Step 3 要求的
  FakeDriver 四脚本（find 失败 / act 超时 / WDA 死亡 / 多匹配）走完整 7.1
  管线 + 7.5 用例级 WDA 语义。
- **顺带修一个真 bug**：`StepRunner._record` 只 catch 了 `TypeError`，
  trace 写盘异常（磁盘满 / 库锁）会一路冒泡，把 SUCCESS 的步骤变成崩溃。
  改为捕获后 `warnings.warn`——**不静默**（warning 进日志，Report 侧 2.6
  可对 warning 计数暴露 trace 丢失）但不让观测手段打断执行。
  「trace 写失败必须有人管」由 Report 暴露，不靠抛异常阻断运行。
- 测试 307 → 497（+190）。

遗留（不阻塞 Task 2.5）：
- `phase0/verify_p1_task23.py` V1 随机 FAIL（Task 2.3 附带），已定位为
  外部 reader 可见性问题，hook 实现本身经设备验证正确（reset launch 后
  立刻读容器 plist 为空，3/3 轮）。需单独一轮排查。
