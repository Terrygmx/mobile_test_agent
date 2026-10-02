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

## 11. Task 2.5 Review（R15）记账 — 2026-09-30

**口径修正（R15-2，P2）——§10 的「四条全部接通」说过头了。**
实测：`StepRunner / Lifecycle / SuiteRunner` 只被 `tests/` 引用；
`runner/testcase_runner.py`、gate 脚本、`cli/main.py`、demo **零引用**。
事实是：**模块级完成 + 测试级接线属实，生产链路仍未切换**。§10 里
「链路闭合」的说法作废——真正的闭合要等 2.6 `mta run` 把 SuiteRunner
接成入口，并有一次真实套件跑出新字段。同时 `lifecycle.py` docstring 里
「这里把 TestcaseRunner 的执行结果写进 TraceStore」与事实不符
（TestcaseRunner 不用 Lifecycle），该 docstring 措辞已随代码演进纠正。

- **R15-1（P1）P0 Recorder 兼容回退是坏的 —— 已修**（探针实锤：跑一步
  SUCCESS，**steps 表 0 行**）。根因两层：
  ① 真实 `tracer.recorder.Recorder.record_step` 首参 `run_id` 是**必填**，
  回退调用漏传 → 回退自身 TypeError → 被外层 except 兜住 → 只剩 warning；
  ② `except TypeError` 盲捕分不清「签名不兼容」与「调用方拼错 kwarg /
  recorder 函数体自身的编程错误」，后者会被误判成兼容路径再走一次回退。
  修法：**签名探测**取代盲捕——`inspect.signature` 先判定 recorder 形态
  （认 `run_id` / `tc_run_id` 两个必填首参），只在签名确实不兼容时走窄
  字段路径；必填首参缺失在**构造期**就抛（放到运行期才发现，报告上只表现
  为「步骤失败但 trace 空」，看不出是配置错）。
  修复过程中还暴露一个连带问题：只补 `run_id` 会把 TraceStore 形态
  （首参 `tc_run_id`）打挂——所以是「按签名传对的那个」，不是写死。
  新增 `tests/integration/test_p0_recorder_compat.py`（9 条）**用真实
  `tracer.recorder.Recorder` 而非替身**（替身失真正是当初漏掉这个 bug 的
  原因，与 R2-0 同款）。复验：0 行 → 1 行，run_id 正确，无 warning。

P3 八条处置：
- **P3-1 `keywords` 是死参数**：原实现算出 `table` 紧跟 `del table`，
  自定义关键词表被静默忽略（7.4 说「启发式可配置」）。→ 真透传给
  `keyword_heuristic_hit`，两处调用都接上。
- **P3-2 无操作聚合**：`aggregate_status([x]) or x` 恒等，且已由上面的
  except 直接改写 status。→ 删除，注明跨 attempt 合并归 suite 层。
- **P3-3 run_id 跨套件碰撞**：`f"suite_{len(self.results)}"` 每个
  SuiteRunner 都从 0 数，撞 14.2 `runs.run_id` 主键。→ 改 uuid。
- **P3-4 8.2 枚举缺 ASSERTION_TARGET_DRIFT**：这是**设计文档的 spec 缺口**，
  不擅自扩枚举。→ 暂归 `ELEMENT_NOT_FOUND` 但在 detail 保留
  `kind="assertion_target"`（7.3 的值），**待 2.6 拍板**——JUnit/Report
  消费 failure_type 之前必须先定这件事，否则漂移在报告里与「元素找不到」
  不可区分。
- **P3-5 SECURITY_BLOCKED 标成可重试**：Guard 判定确定性，重试必然再拦，
  语义不通。→ 专用 RetryDecision(allowed=False) + 明确 reason。
- **P3-6 restart_wda 静默跳过却记 RESTART_WDA**：账实不符。→ 如实记
  `SKIP_NO_RESTART_API` / `SKIP_NO_DEVICE_SESSION` 并 warn。
- **P3-7 `GuardContext.data_class` 死字段**：→ **保留但明写「当前不
  使用」**（删了将来 10.4 的 SENSITIVE 脱敏兜底要改形状；留着必须不留
  「已生效」的假象）。
- **P3-8 `llm_calls=llm_calls or attempts`**：两个语义无关的参数写成互为
  fallback 的别名，不传就把用例尝试数当 LLM 调用数 → Report 的
  `LLM Invocation Rate` 虚高。→ 解耦，`attempts` 落到新增的
  `total_attempts`。**旧测试 `test_summary_aggregates_attempt_counts`
  正是在固化这个危险默认，已改写并补 2 条**。

测试 497 → 507。真机 smoke PASS（首跑失败是 App 状态残留，重装即恢复，
非代码问题）。

### Task 2.6 验收清单（累积）
1. **必须让至少一次真实套件走完整新管线**（SuiteRunner + Lifecycle +
   TraceStore），并在 trace.db 里看到新字段（effective_risk /
   failure_phase / failure_type / detail）——否则 R15-2 的口径问题只是
   往后推（R15-2）
2. 8.2 是否补 `ASSERTION_TARGET_DRIFT` 枚举，**必须在 JUnit/Report 消费
   failure_type 之前拍板**（P3-4）
3. Report 首页对 trace 写盘 warning 计数暴露「trace 丢失」（Task 2.5 遗留）
4. `mta run` 全参数（8.4 退出码真跑一次，验 `3>2>4>1>5` 优先级）

## 12. Task 2.7 + R18 记账 — 2026-09-30（文档单一事实源补齐）

上轮 R17 的四条欠账中三条在本轮落地（`4d742eb` 之后的 R18 修复提交）：

1. **R17-1/R18-4-1 `--no-llm` / `--allow-metadata-mismatch` 仍零读取**：
   维持「记账不修」口径。`--no-llm` 的替代语义是 gate 的结构性断言
   （`llm_calls=0` + `llm_calls_assert_zero`，无 Recovery 接线时零调用是
   结构必然，不是 flag 生效）；`--allow-metadata-mismatch` 等 M3 Build
   Identity 落地时给真实语义（metadata mismatch WARNING↔ERROR 分级）。
   M3 开工前若两 flag 仍无消费点，考虑先从 argparse 摘除防「参数存在=
   功能存在」。
2. **R17-3/R18-4-2 HTML LLM rate 分母失真（已修）**：`Lifecycle.steps_recorded`
   累计真实步骤数（record_step 无条件 +1），`cmd_run` 传它做分母；
   `test_r18_4` 断言 steps_recorded == DB steps 行数。曾经的
   `len(run.results)` 是**用例数**冒充步骤数，20 条套件比率放大约 7 倍。
3. **R16-3「Recovery 接线→2.7」改口「M3/后续 task」**：Recovery/Reconciliation
   与 ReconciliationEngine 的接线是 M3+ 语义（依赖 Repository generated，
   因为 RECOVERED 判定要「目标是否漂移」的 source 真相）。2.7 只交付了
   postcondition 执行端（H7 的判定闭环），未做 Recovery 编排。
4. **Risk YAML 拍板（影响所有后续用例，本轮定案）**：`Risk` 是 int 枚举
   （policy 按 value 取 max），YAML 写 `risk: LOW` 被 schema 拒（只能写
   1-4）。**拍板：P1 用例不声明 `risk`**，由 element metadata + 关键词启发
   式推导（R16-1 已把取严逻辑收口 policy 纯函数，元数据是唯一事实源）。
   `risk` 字段保留给**需要显式覆盖 metadata 的场景**，届时写数字并注明等
   级（或 M3 给 schema 加值别名解析，低优先级）。

### Task 2.7 完成态（tag checkpoint-p1-m2 之后）

- R18-1（flake 已修）：pytest.ini `filterwarnings = always`——warn 断言与
  执行历史解耦（`__warningregistry__` 同进程去重是「隔离绿、全量红」的
  根因）。
- R18-2（轮数已对齐）：gate `ROUNDS=3` + subprocess 带 DEVELOPER_DIR 修复
  （Xcode 27 beta 下 xcrun 无该变量报「unable to find utility simctl」，
  曾伪装成 "no booted simulator"）。
- R18-3（trace 保真已修）：`SuiteRunner.on_cleanup_failure` 回写通道 +
  pipeline 实现；`testcase_runs` 不再出现「PASS 但套件中止」矛盾终态。
  cleanup 成功路径补 PENDING→OK 语义（套件路径此前留 None）。

### M3 开工前置清单（自本节起算）

1. Recovery/Reconciliation 与新管线对接（--no-llm 消费点的前提）。
2. `verify_p1_m2.py` 的 `from_dirs(generated_root=overrides)` 语义拆分：
   M3 起 generated_root=<generated 目录> + overrides_root=<overrides 目录>
   （5.3 generated+override 合并语义，TODO(M3, R10-5.2) 已记账）。
3. M3 集成测试的替身规格直接用生产 Executor 契约（find→list[dict] /
   tap·input / 无 perform）——2.6-2.7 四次「替身失真」的教训。

### M3 Task 3.1-3.3 顺延项（review_m3_task31_2026-09-30 核销记录）

- P2-1（已修，commit 见下）：storyboard 行号恒 0。`ET.parse` 产出的
  Element 没有 `_line` 属性（自定义 TreeBuilder 才有），裸
  `getattr(el, "_line", 0)` 恒 0 → UNKNOWN id 退化为 "file:0"，同文件
  多个 unknown 不可区分。修复：`xml.parsers.expat` 直驱自定义
  TreeBuilder（`CurrentLineNumber` 公开可用；`ET.XMLParser` 在 3.11 不注入
  底层 expat，`parser` 属性恒 None），`id(el)→line` map 反查。测试补
  `test_line_numbers_are_real`。
- P2-2（已修，commit 见下）：一致性 Gate 前缀豁免吞真实漂移。
  `_covered_by_prefix` 对 `cell_removed` 与 `cell_alpha` 同样命中
  startswith("cell_") 一并吞掉、REMOVED 静默为空。修复：新增
  `prefix_matched` 桶——**不算失败但必须可见**（12.2「人工确认」精神：
  自动豁免 ≠ 人工确认；前缀越短盲区越大）。`format_report` 以 `~` 前缀
  列出待人工确认。当前实际生效：HomeView.cell_alpha/beta/gamma +
  SearchView.cell_beta 共 5 条均可见。
- P2-3（**顺延，本单核销条件**）：`mta run` 无 `--generated` 参数
  （`lint` 有），运行时 Repository 仍是「overrides 兼职 generated」旧
  形态。「实现存在但零消费」平行实现第 6 次（R11-4/R13-2/R14-2/R15-2/
  R16-4 之后）。**Task 3.3 Gate 验收时核销**：
  `mta run --generated repository/generated/<build> ...` 真机实跑，
  双源合并与手写基线一致 + build 不匹配拦截（12.5）随该 run 一并验。
- P3-4（已拍板并执行）：local generated 产物 `.gitignore`
  （`repository/generated/local/`），git rm --cached 历史移除；时间戳/
  git_commit 每次生成必变的噪音消除，release/staging build 产物仍可入库。
- P3-5（已修）：`repo generate --check` 的 overrides 根参数化
  （`--overrides`，默认 repository/overrides）。
- P3-6（已修，根因在 Swift 侧）：container struct 名混入 metadata 顶层
  `screens`（marker 层）。`SpikeTabScreen`(struct) vs
  `.mtaScreen("SpikeTab")`——导出的 screen 条目 marker 永远匹配不到真机，
  属猜值（12.2 同源纪律）。修复：SwiftUIVisitor.buildResult() 顶层
  screens 只收 mtaScreen marker 声明名；container 名仅用于元素归属。
  screens 12→10（去掉 2 个 struct 名猜值条目）。
- P3-7（已修）：①`export._element_metadata` 死参数 el 删；
  ②`_map_containers` tag 判定改 ascii lowercase endswith，覆盖
  tableViewController/collectionViewController/navigationController 等
  camelCase 家族（原 endswith("ViewController") 漏匹配）；
  ③SwiftUIVisitor.location(of:) 每 modifier 新建
  SourceLocationConverter 的 O(n²) 隐患——大文件性能项，暂挂
  （正确性无碍；App 源文件 < 千行级）。

### Task 3.3 收口（M3 宣告 PASS 之后的实质缺口补齐，2026-10-02）

M3 复盘发现：Task 3.3 只交付了第 2 步（`mta source diff`，commit a76d449），
第 1/3/4 步（12.5 Build Identity 拦截、真机验证、tag）**从未实现**——
`source/build_identity.py` 在 git 历史中不存在、`--allow-metadata-mismatch`
仍是占位 warn、设计 Gate M3 第 3 判据「build 不匹配被正确拦截」无实现可验。
305882a 的「M3 Gate 5/5 验收」只是解析回归门，M3 宣告 PASS 属超报（与
review_m3_task32 P2-2「Gate 判据手工清单漏项」同源，且这次漏的是整个
Task 3.3 前半）。本轮补齐：

- **12.5 拦截落地**：`source/build_identity.py`（evaluate/override_allowed/
  metadata_identity 纯逻辑离设备 + read_app_identity/resolve_booted_udid
  subprocess 隔离 + gate_run_start 编排，read 注入点供单测/Gate）；
  `mta run` 真机路径启动前 fail-closed 校验，拦截/放行均落 runs 审计列。
  fail-closed 定档：未注入/缺身份/读不到 ≠ 一致，放行开关不豁免「没身份」。
  `--allow-metadata-mismatch` 语义反转生效；CI 第二开关
  `MTA_ALLOW_METADATA_MISMATCH_CI=1`。
- **P2-3 核销**（review_m3_task31 顺延项）：`mta run --generated DIR` 接线，
  运行时 Repository 双源合并（5.3），测试
  `test_run_generated_flag_consumes_dual_source` 实证 generated-only 元素
  可解析——「实现存在但零消费」平行实现系列第 6 次至此闭环。
- **注入方式实证**：`INFOPLIST_KEY_<自定义键>` 不进生成的 Info.plist
  （固定白名单，clean rebuild 复现两次），`make p1-build` 走 12.5 本就允许
  的本地脚本 PlistBuddy 注入 + `-target`/SYMROOT（无 shared scheme，
  `-derivedDataPath` 与 `-target` 互斥实测 Error 64）。
- **真机验证**：`phase0/verify_p1_task33.py` 四路径全过
  （out/p1_task33_verify/summary.json）：V1 注入读回==HEAD；V2 一致路径
  放行（mismatch=0）；V3 漂移拦截 exit 3、未启动用例、mismatch=1/override=0
  （矩阵 #21 预演）；V4 放行留痕 mismatch=1/override=1。
- **Gate M3 补 G8**（review_m3_task32 P2-2 教训的机械映射：设计 Gate M3
  第 3 判据「build 不匹配被正确拦截」）：离设备判定链三断言 + 真机四路径
  由 task33 脚本承担。Gate 重跑 9/9 PASS（G1×2/G2–G8）。
- `test_r16_2` 文案断言放宽（build identity 前置错误可能先于「组件未装配」
  触发，两者同为 exit 3 前置配置错误）。
- 实测：pytest 667 passed（+27：build_identity 21 + CLI 接线 6）。

### Task 4.1 完成态（P1-11 RecoveryEngine 非 LLM 部分，2026-10-02）

- **交付**：agent/policy.py（9.2 前半决策表：admitted_actions 纯函数 +
  RecoveryConfig 默认值即设计值）+ agent/context.py（RecoveryContext/
  RecoveryResult + ExperienceStore/EmptyExperienceStore 预留）+
  agent/risk.py（候选风险门控：仅 LOW，None 按 fail-closed 不放行）+
  agent/recovery.py 重构（RecoveryEngine 确定性半边 + RunMemo 9.4 骨架；
  P0 recover() 保留至 4.2 迁移，消费只有一条：P1 管线只走引擎）。
- **7.5 WDA 规则接线**（handle_wda_failure 此前零消费——平行实现第 7 次，
  M2 只交付了判定逻辑没接管线）：run_case 改 attempt 循环；InfraError 上
  附着 non_idempotent_dispatched（StepRunner 打标：dispatch 中途断连算
  已发出）；非幂等已发出 → SKIP_RERUN；预算内重启重跑 attempt=2（
  testcase_runs.attempt 落库）；预算耗尽 → detail.terminate_run →
  SuiteRunner SuiteAborted 终止 run（剩余用例不跑不算 total）。
- **恢复接线**：cmd_run 装配 RecoveryEngine(repo)；动作步失败 → 决策表
  准入 → postcondition/settle/memo/experience 确定性半边；恢复成功步骤
  终态 RECOVERED（record_step 新增 status 覆盖参数）+ recoveries 行落库
  （kind 大写枚举映射）+ 用例 RECOVERED（exit 5 走 8.4 既有优先级）。
- **顺延 4.2**（记账，review_m4_task41 补点名后）：①12.3 两键 metadata →
  reconcile_local 子集的适配（LLM prompt 同需）；②LLM 调用 + 9.3 五项候选
  校验链；③review CLI；④budget/熔断；⑤**矩阵 #15**（断言目标 ID 漂移 →
  RECOVERED kind=assertion_target，exit 5）——需要 LLM 候选 + **断言步骤
  接入恢复管线**（现 assert 分支与 wait 一样走 aux 分支不进引擎），
  4.3 要 24/24 全过，此处不点名必漏；⑥`recovery.on_wait_timeout` 死旋钮
  接通：wait 步骤恢复管线与 ⑤ 同一套接线，接通前配置项不得宣称可用
  （review P3-2）；⑦**memo save 时机**：LLM 校验通过后写入 RUN_MEMO
  （消费端 find_with 已在 4.1 闭环，P3-1）；⑧**postcondition 双查定档**：
  StepRunner 成功路径与引擎各查一次、两次结果不一致以第二次为准（时间
  窗口）——校验链设计时显式拍板是否合并/去重（review P3-4）。
  P0 recover() 退役随迁移一并做（P0 verify 脚本回归路径）。
- 实测：pytest 694 passed（+27：决策表/引擎 19 + 故障注入 8，FakeDriver
  矩阵 #13/#14/#16/#17/#18/#19 预演全过）。


### review_m4_task41_2026-10-02 核销记录（5×P3，无 P0/P1/P2）

- P3-1（已修）：RUN_MEMO 命中路径不消费 memo 策略——引擎此前命中后调
  refind（闭包捕获原始 strategies），memo 里存的恢复策略从未用于定位。
  修复：RecoveryContext 新增 `find_with(strategies)` 注入端（管线侧
  ex.find 透传），memo 命中后按保存的策略重找 + redispatch；测试断言
  find_with 收到的恰是 memo 策略（不再是 stub 恒成功、与内容无关）。
  memo save 时机（LLM 校验后写入）仍属 4.2（顺延清单 ⑦）。
- P3-2（已定档）：`recovery.on_wait_timeout` 端到端死旋钮——决策表可
  放行但 wait 步骤不进恢复管线。RecoveryConfig docstring 显式标注 P1
  预留语义（不宣称可用）；接通进 4.2 顺延清单 ⑥（与矩阵 #15 同一套
  aux 分支接线）。
- P3-3（已记账）：矩阵 #15 顺延未点名——补入 4.2 清单 ⑤（含断言步骤
  接恢复管线的接线需求）。
- P3-4（已修/已定档）：①ExperienceStore 位置测试补 fake repo 用例——
  stages 实证 settle → screen → reconcile(DRIFT) → experience → llm，
  「reconciliation 之后」不再靠测试名声称；②postcondition 双查语义差
  定档进 4.2 清单 ⑧。
- P3-5（已修）：①agent/recovery.py 模块 docstring 两段式（P1 引擎主体
  在前、P0 遗留函数与退役路径在后）；②pipeline.py:419 条件表达式的
  13 个多余空格格式化（heredoc 写入时折行被压平所致）；③suite.py
  run_suite 循环前 list() 物化一次（generator 半路消费会让 remaining
  统计静默失真）。
- P0 回归（review 建议动作 4）：verify_stage8 重跑通过（budget/risk/
  唯一性/RECOVERED 分支全绿；P0 recover() 未动，import 路径实测无损）。
  **过程中挖出并修复 P0/P1 schema 漂移一处**：tracer/recorder.py（P0
  写入层）start_run/record_recovery 用定位 INSERT（runs 8 值 vs 并集
  schema 27 列、recoveries 7 值 vs 26 列）——M1 扩列时即断，因 P0 链路
  此前无人对 live 库跑过而未暴露（又一例「真机项不跑就没有证据」）。
  改命名列 INSERT（P0 的 8 列在 M1 迁移中全部保留，命名列对纯净库与
  并集库都成立）。


### Task 4.2 完成态（P1-12 LLM Recovery + 校验链 + review 流程，2026-10-02）

plan Task 4.2 全部交付 + 4.1 顺延清单 8 项全核销：

- **10.4 契约**：llm/prompt.py 分区模板（[SYSTEM INSTRUCTIONS]/[TEST GOAL]/
  [SOURCE METADATA]/[ERROR]/[UNTRUSTED OBSERVED UI]，不可信区殿后）+
  redact_ui_tree（SecureTextField 按 tag 名判定——探针实锤 tag≠属性；
  手机号/邮箱/订单号模式；保留 type/label/层级）；llm/parser.py 严格解析
  （非法 JSON/必填缺失/越界 confidence → LLM_INVALID_OUTPUT；额外字段
  忽略并记录 ignored_fields，H4 risk_level 尤其如此；action 白名单）。
- **9.3 校验链**：引擎 _llm_stage 五项（数量/类型/Screen/risk+Guard/
  confidence，fail-closed 面：候选未登记→screen mismatch、risk None→
  blocked、类型取不到→mismatch）；动作一致性并入契约层（8.2 无
  LLM_ACTION_MISMATCH，映射 LLM_INVALID_OUTPUT + stages 留痕）；
  confidence 提前（零设备成本，不给低置信候选定位机会）。
- **10.5 budget**：per-run+per-testcase 双限独立扣减；熔断连续 3 次失败、
  本 run 不可逆（success 不解锁）；失败定义=尝试未以 RECOVERED 收尾；
  P0 API 兼容（max_calls_per_run 属性 + 无参 try_acquire）。
- **9.4 memo**：save 时机=LLM 校验全过后（⑦）；消费=find_with 按保存
  策略重找（P3-1 闭环）。
- **9.5 review**：agent/review.py + mta review list/accept/reject；accept
  导出单元素 overrides 补丁（--out 或 stdout，origin: manual + review_id
  审计；缺 candidate_target/screen/candidate_type 拒导出——12.2 不猜值）；
  reject note 必填（argparse exit 2 / agent exit 3）；review 不可二次决策；
  LLM 恢复成功自动建 PENDING。H15 全程无路径写 repository/。
- **顺延核销**：①reconcile 适配（引擎 _source_subset，合并视图派生本屏
  子集）；⑤矩阵 #15（assertion 分支接恢复：AssertionTargetDrift→引擎
  验证候选→定位覆盖→重验一次，RECOVERED+exit 5；recoveries.kind=LLM、
  detail.recovery_context=assertion_target——kind 枚举记机制不记上下文，
  拍板记录于此）；⑥on_wait_timeout 接通（wait 分支同一套 aux 恢复；
  决策表默认不准入不变）；⑦memo save 时机；⑧postcondition 双查定档
  （不合并/不去重/以第二次为准，均留痕——见 design 9.2 修订记录）。
- **矩阵覆盖**：#2/#4/#5/#6/#7/#8/#9/#10/#13-on/#15 端到端或单测全过
  （FakeLLM/FakeDriver，无网络）；#11-#14/#16-#19 已有覆盖不变。
- **装配**：cmd_run 按 LLM_API_KEY + --no-llm 装配（缺省=确定性半边，
  llm 阶段 disabled 可见）；llm_calls 取 budget 实数（报告分子）；
  Guard 与动作步同实例（10.1）。
- 实测：pytest 731 passed（+36：budget 7 / llm_contract 24 / llm_matrix 5）；
  M3 Gate 9/9；verify_stage8 全绿（LLMBudget 重构 P0 兼容实测）。
- **4.3 前置已齐**：矩阵 24 项中 19 项已有 FakeDriver/FakeLLM 覆盖，
  #2/#15 真机版（改名重编译）与 #16/#17 WDA 真机项留给 verify_p1_m4.py。
