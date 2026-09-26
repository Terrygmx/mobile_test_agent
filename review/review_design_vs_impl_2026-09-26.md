# Mobile Test Agent — 设计文档 vs 实际实现 Review 报告

> 审查对象：`docs/mobile-test-agent-phase0-design.md` + `docs/phase0-plan.md` vs 当前代码库
> 审查日期：2026-09-26 ｜ 性质：只读 review，未修改任何项目文件
> 对比基线：Phase 0 设计文档（8 项目标 + 模块接口定义）与 phase0-plan.md（Stage 1-9 执行计划）

---

## 一、总体结论

| 维度 | 评价 |
|---|---|
| 架构一致性 | ★★★★☆ 模块边界与设计基本吻合，session/executor/trace/source/llm/agent 分层清晰 |
| 功能完整性 | ★★★☆☆ 8 项目标大部分有实现与验证脚本，但 **Recovery 未接入自动流程**、**infra 事件未落 SQLite**、**steps 表 screenshot/ui_tree 从不写入** |
| 接口符合度 | ★★★☆☆ 多处签名/命名偏离设计（Locator 非类、recover 签名改形、accessibilityId 驼峰 vs accessibility_id 下划线） |
| 范围纪律 | ★★★☆☆ Phase 1 内容（objc_scan、class_chain、locator 兜底链）已提前混入 Phase 0 代码库 |
| 代码卫生 | ★★☆☆☆ main.py 模板残留、Makefile 死代码、幽灵文档引用（"设计文档 21 节"不存在）、sys.path 插错层级 |

**一句话结论**：Phase 0 闭环"能跑通"，但与设计文档存在约 20 处实质性偏离；其中 3 处属于会影响验收判定和后续 V1 扩展的结构性差距，建议优先补齐。

---

## 二、差异明细（按维度）

### 2.1 目录结构与模块划分

| # | 设计文档 | 实际代码 | 差异性质 |
|---|---|---|---|
| D1 | `trace/`（recorder/redactor/storage） | `tracer/`，且**没有 storage.py**，SQLite 逻辑内嵌 recorder.py | 命名偏离 + 模块合并 |
| D2 | `executor/locator.py`，`Locator` 为类 | 无独立文件，`Locator = list[dict]` 类型别名，并入 executor.py | 简化偏离（可接受，但与文档不符） |
| D3 | `source/swift_scan.swift` | `source/main.swift`（编译产物 `/tmp/swift_scan`） | 文件改名，Makefile 中仍引用 swift_scan.swift，存在遗留死代码 |
| D4 | 无 runner 模块（第 3 节目录未列） | 新增 `runner/testcase_runner.py` + `TestFailure` 异常 | 合理补充，但**未回写设计文档** |
| D5 | 无 environment 模块 | 新增 `environment/secrets.py`（SecretProvider） | phase0-plan Stage 5 有此设计，两份文档之间本身不一致 |
| D6 | 被测 App 用"官方 Demo App" | 自建 `ios_demo/LoginDemo/`（LoginView 内嵌于 LoginDemoApp.swift） | 偏离设计 4.6"对 LoginView.swift 单文件解析"的前提 |
| D7 | 无 | 新增 `source/objc_scan.py`（ObjC 正则扫描，注释自称"Phase 1 改造"） | **范围蔓延**：违反设计第 0/6 节 Phase 0 红线 |
| D8 | 无 | `main.py` 为 PyCharm "print_hi" 模板残留 | 垃圾文件 |

### 2.2 接口定义偏离

| # | 设计签名 | 实际签名 | 影响 |
|---|---|---|---|
| I1 | `Executor.__init__(driver, device_session)` | `Executor(device_session)`，driver 变 property | 低；调用方需同步 |
| I2 | `ensure_alive() -> None` | `ensure_alive() -> WebDriver` | 低，属增强 |
| I3 | `AppSession.reinstall(ipa_path)` | `reinstall(app_path)`，且用 `d.capabilities.get("udid", "")` 传给 simctl | **caps 缺 udid 时传空字符串，simctl install 会失败**（当前 demo caps 写死 udid 掩盖了问题） |
| I4 | `recover(step, error, executor, ...)` | `recover(expected_id, error, ex, ..., run_id=None, recorder=None)` | 签名改形；recorder/run_id 为可选导致"无论结果都写 recoveries 表"（设计 4.8.3 第 6 条）仅在显式传参时成立 |
| I5 | YAML：`- wait_for: {target, condition, timeout}` / `- assertion:` 嵌套结构 | 统一平铺为 `- action: wait_exists / assert_exists` | schema 偏离，loader/runner 自洽但与文档样例不兼容 |
| I6 | LLM 输出含 `"action": "tap"` 字段 | prompt 模板删除 action 字段，recovery 硬编码 `ex.tap()` | **若失败步骤是 input，recovery 仍执行 tap**，语义错误风险（见 P1-2） |
| I7 | metadata 元素字段 `accessibility_id`（下划线）| `accessibilityId`（驼峰），swift_scan/objc_scan/reconciliation/recovery 全链路用驼峰 | schema 命名与设计不一致；跨团队/跨文档消费时易踩坑 |

### 2.3 功能完整性差距

| # | 设计要求 | 实际状态 | 差距 |
|---|---|---|---|
| F1 | 目标 2 验收："检查 `infra_events` 表里有 WDA_DEAD + WDA_RESTARTED" | `DeviceSession._log_infra()` 只写 `out/infra_events.jsonl`；`Recorder.record_infra()` 存在但**全库无人调用**；注释自称"Stage 5 迁入 SQLite"但从未迁移 | **双轨记录未收敛，设计验收标准实际无法达成** |
| F2 | steps 表含 `screenshot_path` / `ui_tree_path`；目标 6"每一步都有记录" | 两列从不写入；runner 执行步骤时**不截屏、不存 page_source** | 失败步骤无 UI 证据，无法回放定位 |
| F3 | `recoveries.step_id` 关联 steps.id | recovery.py `record_recovery(0, ...)` **step_id 硬编码 0** | recoveries 与 steps 永远无法关联，Trace 数据链断裂 |
| F4 | 元素定位失败 → 自动触发 reconcile → recovery（目标 7 闭环） | `TestcaseRunner._run_step` 失败直接 raise，**recovery 只在 run_phase0_demo.py 中手动编排** | 自动化流程中 Recovery 不闭环，是"组件齐、集成缺"的关键 gap |
| F5 | 验收步骤 6：真实修改 App 源码（login_button→signin_button）重编译验证 | demo 注释自认"用不存在元素模拟源码 metadata 仍是旧的" | 验收强度弱化，未验证"源码有/运行时改名"的真实 DRIFT 场景 |
| F6 | swift_scan 元素 `type: button/textfield` | type 恒为 `"unknown"` | 元素类型识别缺失，reconciliation 的"同类型候选"退化为"全部 name 候选" |
| F7 | reconciliation "只在当前 Screen 范围内 diff"（性能红线） | 扫描粒度是**单文件全量**（LoginDemoApp.swift 含 Login+Home 两页元素混在一起），`screen` 参数仅作回显不参与过滤 | 单页 Demo 下无感；多页 App 下候选会跨页面污染，LLM 可能选中其他页面的元素 |
| F8 | `runs.status` 枚举 PASS/FAIL/INFRA_FAILURE | demo 还写入 `"RECOVERED"` | 状态枚举超出设计，下游按枚举消费会出错 |
| F9 | SecretProvider 解析 `${VAR}` | 已实现（EnvSecretProvider），但 runner 只支持**整值匹配** `${VAR}`，不支持部分替换 | 符合设计精神，属保守实现（可接受） |
| F10 | Redactor 硬约束"先脱敏再落盘" | `record_step` 对 locator 先 redact ✓；但 `error` 字段未过 Redactor | 低风险（当前 error 消息不含输入值），但约束执行不完整 |

### 2.4 数据流差异

| # | 设计数据流 | 实际数据流 |
|---|---|---|
| T1 | infra 事件 → SQLite `infra_events` 表 | JSONL 文件（device_session）+ 空表（recorder），两套并存 |
| T2 | locator 失败 → runner 内触发 reconcile/recovery → steps.status=RECOVERED | runner 失败即抛；recovery 由入口脚本手动调用，**steps 表里永远不会出现 RECOVERED 状态**（demo 的 recovery 结果只写 runs.status 和 recoveries 表） |
| T3 | source_metadata.json 是持久产物，recovery 消费它 | 每次 demo 运行重新 build_metadata 并覆盖 `out/source_metadata.json`；设计的"不重新生成 metadata 模拟漂移"场景被改为"查询不存在的元素"，metadata 时效性语义丢失 |

### 2.5 技术选型偏离

| # | 设计（2.3 节） | 实际 | 评价 |
|---|---|---|---|
| C1 | `sqlmodel` | 标准库 sqlite3 | 减依赖，合理；文档未更新 |
| C2 | `anthropic` SDK | urllib 直连 OpenAI 兼容接口（LLM_BASE_URL/LLM_MODEL 可配） | 选型变更（Anthropic→OpenAI 兼容网关），合理但设计文档未同步 |
| C3 | `swift-syntax-parser`（Python 侧调 Swift 脚本） | swiftc 编译 `source/main.swift` 为独立二进制 `/tmp/swift_scan` | 链路可行，但**构建产物依赖 /tmp**（重启丢失），且 Makefile scan 目标含死代码（见 P2-3） |
| C4 | 依赖声明 requires-python >=3.11 | pyproject 符合 ✓ | 一致 |
| C5 | 设计禁止明文密码入 YAML/Trace | YAML 无明文 ✓；但 `run_phase0_demo.py` 将 `TEST_PASSWORD=test_pass_123` setdefault 进环境变量 | 擦边：未违反字面约束，但明文口令进了源码，V1 接 Vault 时需清理 |

---

## 三、潜在问题与影响评估

### P0（结构性，建议优先处理）

| ID | 问题 | 影响 | 位置 |
|---|---|---|---|
| P0-1 | Recovery 未接入 TestcaseRunner 自动流程，steps.status 永远不会是 RECOVERED | 目标 7 的自动化闭环不成立；V1 扩展时需重构 runner 主循环；Trace 里 recovery 与 step 无法对应（叠加 P0-2） | runner/testcase_runner.py、agent/recovery.py |
| P0-2 | recoveries.step_id 硬编码 0 + record_infra 无人调用（infra 只落 JSONL） | Trace 数据链两处断裂：recovery↔step 关联失效、目标 2 的 SQLite 验收标准无法通过 | agent/recovery.py:33、session/device_session.py:29 |
| P0-3 | source metadata 无 Screen 归属划分，reconcile_local 的 screen 参数形同虚设 | 多页面 App 下 reconciliation 候选跨页污染 → LLM 可能恢复到错误页面的元素并执行 tap；这是 V1 "局部 reconciliation" 性能红线的实际失守 | source/main.swift、source/reconciliation.py |

### P1（功能缺陷/高风险写法）

| ID | 问题 | 影响 |
|---|---|---|
| P1-1 | steps 表 screenshot_path/ui_tree_path 从不写入，失败步骤无 UI 证据 | 违背目标 6"每一步都有记录"的本意，排障效率显著下降 |
| P1-2 | recovery 动作硬编码 tap，忽略 LLM 输出的 action/目标类型 | 对 input/scroll 类步骤的恢复语义错误；Phase 0 demo 恰好全是 tap 掩盖了问题 |
| P1-3 | `AppSession.reinstall` 依赖 `capabilities["udid"]`，缺失时静默传空串 | simctl install 失败且报错信息晦涩；换设备/真机时必现 |
| P1-4 | runner 用 `type(e).__name__ == "InfraError"` 字符串判异常 | 异常一旦被包装（如 raise ... from）即误判为 FAIL 而非 INFRA_FAILURE；应 isinstance |
| P1-5 | runner 记录的 locator 只是 accessibility_id 单策略，实际执行用 id+predicate 双策略链 | Trace 保真度不足：复盘时看到的定位策略与真实执行不一致 |
| P1-6 | Executor.find 对未知策略 type 直接 KeyError | 配置错误时报错不友好；且第一策略 0 命中后继续下一策略，但 >1 命中立即 fail closed——该行为符合设计，但未在文档中写明 |

### P2（卫生/一致性，低风险）

| ID | 问题 | 影响 |
|---|---|---|
| P2-1 | `run_phase0_demo.py` 的 `sys.path.insert(0, parent.parent)` 插入的是 PycharmProjects 目录而非项目根（靠 sys.path[0] 兜底才没炸） | 误导性代码；从其他 cwd 启动可能 import 失败 |
| P2-2 | runner 注释引用"设计文档 21 节"——设计文档只有 7 节 | 幽灵引用，说明文档版本失同步 |
| P2-3 | Makefile scan 目标：`mv -f swift_scan.swift /tmp/` 无对象文件、`[ -f main.swift ] || mv main.swift main.swift` 恒假 no-op | 死代码掩盖真实构建流程；/tmp 产物重启即失 |
| P2-4 | `main.py` PyCharm 模板残留 | 噪声文件 |
| P2-5 | `build_metadata` 的 git 调用无异常保护（recorder 的同名逻辑有 try） | 非 git 目录运行直接崩溃，同类逻辑健壮性不一致 |
| P2-6 | recovery 的 `_JSON` 正则 `\{.*\}` 贪婪匹配 | LLM 输出含多段 JSON 或代码块时可能解析失败，落入 LLM_INVALID_OUTPUT |
| P2-7 | swift_scan 为每个 unknown 元素生成 id="UNKNOWN"，多元素 id 重复 | metadata 消费方按 id 去重时丢数据 |
| P2-8 | ObjC 正则扫描跳过 `/* */` 块注释仅处理 `//` | 块注释中的赋值语句会误报（objc_scan.py 自评"无误判空间"过于乐观） |

---

## 四、Phase 0 八项验收目标覆盖度

| 目标 | 状态 | 说明 |
|---|---|---|
| 1. Appium 链路打通 | ✅ | smoke_test.py 完整覆盖 launch/tap/input/swipe/screenshot/page_source |
| 2. WDA 自愈 | ⚠️ 部分 | DeviceSession 实现完整；但 infra 事件只落 JSONL，**"检查 infra_events 表"的验收方式无法执行** |
| 3. App 状态重置 | ✅ | 三策略 + matrix + UnsupportedResetError 不降级，符合硬约束 |
| 4. SwiftSyntax 解析 | ⚠️ 部分 | 链路通、literal/unknown 正确；但元素无 type/label，screen 粒度为全文件 |
| 5. 局部 Reconciliation | ⚠️ 部分 | 函数实现符合签名；"只 diff 当前 Screen"实际是"只 diff 单文件全量" |
| 6. 完整 Trace | ⚠️ 部分 | runs/steps 有记录、密码脱敏 ✓；screenshot/ui_tree 列从不写入 |
| 7. LLM Recovery 闭环 | ⚠️ 部分 | 组件齐、demo 可演示；未接入 runner 自动触发，且用"查不存在元素"模拟而非真实改名重编译 |
| 8. Budget fail closed | ✅ | verify_stage8.py 显式验证 budget=0 零调用，实现与设计逐字一致 |

---

## 五、建议（供后续排期，本报告未做任何改动）

1. **补集成**：把 reconcile + recover 挂进 `TestcaseRunner._run_step` 的 ElementNotFound 分支，让 steps.status=RECOVERED 真正出现在自动流程中（对齐目标 7）。
2. **收敛 Trace**：DeviceSession 持有 Recorder 引用（或回调），infra 事件统一写 SQLite；recovery 用真实 step_id 落 recoveries 表。
3. **metadata 加 screen 归属**：Swift 结构体/View 粒度分组，reconcile_local 按 screen 过滤候选，兑现性能红线。
4. **recovery 支持动作类型**：透传 LLM 输出 action 并白名单校验（Phase 0 可只放行 tap/input）。
5. **失败步骤落证据**：record_step 前截屏 + 存 page_source 副本，写入 screenshot_path/ui_tree_path。
6. **文档回写**：目录名 tracer/、Locator 为 dict 链、YAML 平铺 schema、OpenAI 兼容 provider、accessibilityId 驼峰——任选"改代码就代码"或"改文档就文档"，消除双源真相。
7. **清理**：删 main.py 残留、修 Makefile 死代码、修 sys.path 层级、run_phase0_demo 的明文口令改由环境注入。
8. **范围纪律**：objc_scan/class_chain 等 Phase 1 内容建议挪到独立分支或 package，避免 Phase 0 验收基线被污染。
