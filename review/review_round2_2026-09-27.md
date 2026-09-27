# Mobile Test Agent — 第二轮 Review 报告（修复验证）

> 审查日期：2026-09-27 ｜ 性质：只读 review，未修改任何项目文件
> 对比基线：第一轮报告 `review/review_design_vs_impl_2026-09-26.md` 的 P0×3 / P1×6 / P2×8
> 验证方式：逐文件重读 + `out/trace.db` 实际数据核查 + `out/source_metadata.json` 产物比对

---

## 一、总体结论

**17 项第一轮问题：11 项完全修复 ✅，2 项部分修复 ⚠️，4 项未处理 ❌；同时本轮发现 2 个由修复引入的新缺陷（其中 1 个 P0 级回归）+ 6 个小问题。**

| 评价 | 说明 |
|---|---|
| 修复质量 | 主干方向全对，集成点（runner↔recovery↔recorder）已打通且 trace.db 有真实 RECOVERED 数据佐证 |
| 最大风险 | **P0-3 的修复引入了新回归：真实链路上 DRIFT 永远判不出来**（screen 键不匹配），而 verify_stage7 用旧 schema 的 mock 数据，测试全绿掩盖了断裂 |
| 次级风险 | RECOVERED 后仍无条件 raise → run 级别仍 FAIL、后续步骤中断，目标 7 的"testcase 最终 PASS"仍未闭环 |

---

## 二、第一轮问题修复核对表

### P0（结构性）

| # | 第一轮问题 | 状态 | 核查证据 |
|---|---|---|---|
| P0-1 | Recovery 未接入自动流程 | ✅ 修复* | `TestcaseRunner._run_step` 捕获 ElementNotFound/AmbiguousElement → `_try_recovery` → steps.status=RECOVERED；trace.db 实测有 1 条 RECOVERED step。*但引入 R2-1，见下 |
| P0-2 | recoveries.step_id=0 + infra 不落 SQLite | ⚠️ 部分 | infra：DeviceSession 新增 recorder 回调 ✓，但 demo 和 verify_stage2 都没接线，`infra_events` 表实测仍为空。step_id：runner 用 `UPDATE ... WHERE id=(SELECT MAX(id))` 回填，db 证实 step_id=121 关联成功 ✓；但 `recover()` 内 `step_id or 0` 仍会写 0（demo 直接调用处），且 MAX(id) 是全局锁非 run 维度 |
| P0-3 | metadata 无 screen 归属，reconcile 屏幕红线失守 | ❌ 回归 | swift_scan 已按 struct 加元素级 screen ✓，但顶层 screen 回退为文件名 "LoginDemoApp"，与元素级 "LoginView"/"HomeView" **不匹配** → 真实链路过滤得空集 → DRIFT 永远判成 UNKNOWN。详见 R2-0 |

### P1

| # | 问题 | 状态 | 说明 |
|---|---|---|---|
| P1-1 | 失败步骤无 UI 证据 | ✅ | `_save_evidence` 截屏+存 page_source，`out/artifacts/c33b0fd8_step0.{png,xml}` 实证 |
| P1-2 | recovery 动作硬编码 tap | ✅* | action 白名单 + LLM_ACTION_MISMATCH + LLM_NO_VALUE；但 input 恢复时 LLM 返回值优先于 secret 原值（R2-2） |
| P1-3 | reinstall 静默传空 udid | ✅ | 缺失时抛 InfraError，报错清晰 |
| P1-4 | 字符串比较判异常 | ✅ | 改为 `isinstance(e, InfraError)` |
| P1-5 | trace locator 与实际不符 | ✅ | `record_step` 现记录真实执行的完整策略链 |
| P1-6 | 未知策略 KeyError | ✅ | 改为 ValueError + 支持列表提示 |

### P2

| # | 问题 | 状态 |
|---|---|---|
| P2-1 sys.path 插错层级 | ✅ 改为 `.parent` |
| P2-2 幽灵引用"设计文档 21 节" | ✅ 已删 |
| P2-3 Makefile 死代码 | ✅ 清理干净 |
| P2-4 main.py 残留 | ✅ 已删除 |
| P2-5 build_metadata 无 git 保护 | ✅ 加 try，与 recorder 一致 |
| P2-6 `_JSON` 贪婪正则 | ✅ 改用 `json.JSONDecoder().raw_decode`，实现正确且优于建议 |
| P2-7 UNKNOWN id 重复 | ⚠️ swift 侧已带 `UNKNOWN:file:line`；**objc_scan.py 侧仍是裸 "UNKNOWN"，未同步** |
| P2-8 objc 块注释误报 | ⚠️ 只处理了单行内的 `/* */`；跨行块注释逐行剥离不掉，仍会误报 |

### 其他第一轮项

| # | 问题 | 状态 |
|---|---|---|
| F5 验收用"查不存在元素"模拟而非真实改名 | ❌ 未处理（demo 场景原样保留） |
| F6 元素 type 恒为 unknown | ❌ 未处理 |
| F8 runs.status 写 "RECOVERED" 超枚举 | ✅ 收敛为 PASS/FAIL |
| C5 明文口令进源码 | ✅ 改为 `demo-only-not-a-secret` 占位 + 提示 |
| D7 objc_scan 等 Phase 1 内容混入 | ❌ 未隔离（保留原状，可接受但需自觉） |

---

## 三、本轮新发现问题

### R2-0【P0·回归】screen 过滤键不匹配，DRIFT 永远判不出

**链条**：swift_scan 元素级 `screen` = 所属 struct 名（"LoginView"/"HomeView"）；顶层 `screen` 在 walk 结束后 `currentScreen` 已被 visitPost 置 nil → 回退文件名 `"LoginDemoApp"`。`reconcile_local` 用**顶层 screen** 过滤：`e.get("screen") in (None, screen)` → 所有元素（LoginView/HomeView）≠ "LoginDemoApp" → **screen_elements 为空集 → source_ids 空 → expected_in_source 恒 False → 状态从 DRIFT 退化为 UNKNOWN**。

实测 `out/source_metadata.json`：顶层 `"screen": "LoginDemoApp"`，6 个元素 screen 全是 `"LoginView"`/`"HomeView"` —— 断裂实锤。

**为什么测试没抓住**：`verify_stage7.py` 的 METADATA mock 元素**没有 screen 字段**（`e.get("screen")`=None，命中 `in (None, screen)` 放行分支）→ 4 个断言全绿，但 mock schema 与 swift_scan 真实输出 schema 脱节。**验证脚本测的是旧数据形状，不是生产数据形状。**

**影响**：核心验收场景（目标 5/7 的 DRIFT 检测）在真实链路上静默失效；reconciliation 结果传给 LLM 的也是空 source_ids，prompt 质量同步劣化。

**修复方向**（三选一）：
1. swift_scan 顶层 screen 改为 struct→screen 的 map（`"screens": {"LoginView": [...], "HomeView": [...]}`），reconcile 先用 runtime 元素反查当前 screen 再取子集（最贴合设计意图）；
2. 顶层 screen 输出为元素 screen 的集合，reconcile 匹配任一即纳入；
3. 最小改：reconcile 过滤条件改为"元素无 screen 或 screen ∈ metadata 声明的 screens 集合"。
**同时必须更新 verify_stage7 的 mock，让其携带真实 schema 的 screen 字段**，并加一条"LoginDemoApp 顶层 screen + LoginView 元素"的回归用例。

### R2-1【P1·高】RECOVERED 后仍无条件 raise，run 判 FAIL、流程中断

`_run_step`：捕获异常 → recovery 成功 → `status="RECOVERED"` → **仍执行 `raise`** → `run()` 捕获 → `end_run("FAIL")` → 剩余步骤全部不执行。结果是：单步恢复成功（RECOVERED 落库），但整个 run 标 FAIL 且用例中断——设计目标 7 明确要求"该 testcase 最终状态为 PASS，steps 里这步是 RECOVERED"。**P0-1 的修复因此只算完成一半：状态能落库，run 级闭环未达成。**

修复方向：`status == "RECOVERED"` 时不 raise，继续执行后续步骤；recovery 未成功时维持现行为。

### R2-2【P1】input 恢复时 LLM 值优先于 SecretProvider 原值

`recovery.py`：`value = result.get("value") or step_value or ""` —— LLM 编造的输入内容优先。密码字段恢复时，LLM 幻觉值会替代真实密码被 send 进 App（必失败，且语义上是"用 LLM 猜的密码登录"）。prompt 还主动要求 LLM 提供 value，对 password 场景是反模式。

修复方向：优先 `step_value`（SecretProvider 解析过的原值），LLM value 仅在原步骤无值时兜底；prompt 中删除/弱化对 input 内容的索取，或声明"敏感字段由系统注入，勿返回明文"。

### R2-3【P2】runner 回填 step_id 用全局 MAX(id)

`UPDATE recoveries SET step_id=? WHERE id=(SELECT MAX(id) FROM recoveries)` —— 非 run 维度，多进程/多 run 并发时可能把别人的 recovery 行改掉。更稳的做法：`_try_recovery` 先拿 `recorder` 返回的 recovery id，或 recover() 直接接收 step_id（runner 在 record_step 后再触发 recovery，时序上 step_id 已知——现在是先 recovery 后 record_step，顺序反了才需要回填）。

### R2-4【P2】DeviceSession 的 infra 落库 wiring 别扭且无人接线

recorder + run_id 要在**构造时**传入，但 run_id 在 `rec.start_run()` 之后才存在（demo 里 ds.connect() 又发生在最前）。当前 demo、verify_stage2 都没传 → `infra_events` 表实测仍空，目标 2 的 SQLite 验收在所有现存脚本里都走不通。建议改为 `ds.attach_recorder(rec, run_id)` 式的后挂接口，或在 restart 时惰性取当前 run。

### R2-5【P2】demo 入口未启用自动恢复

`run_phase0_demo.py` 构造 `TestcaseRunner(ex, app, rec, EnvSecretProvider())` **没传 recovery_context**，[2] 段仍是手动编排。P0-1 的集成路径存在且被其他脚本验证过（db 有 RECOVERED step），但端到端 demo 主入口没有覆盖它——验收脚本与集成路径脱节。

### R2-6【P2】杂项

- `verify_stage8.py` [4] 用 `rows[-1]` 断言最新 accepted 行——共享 trace.db 时可能被其他 run 的行干扰（该 db 已被 5+ 个脚本共写，runs 表 41 条、含大量 RUNNING 残留）。
- prompt 模板"找不到等价元素时 value 填 null"：现在 JSON 同时有 `target.value` 和顶层 `value`（输入内容），指代歧义，LLM 可能填错位置。
- objc_scan 的 unknown id 未与 swift_scan 同步为 `UNKNOWN:file:line`。
- 跨行 `/* */` 块注释仍会误报（P2-8 残留）。
- runs 表 9 条 RUNNING 残留（异常中断未 end_run），长期会让状态统计失真，可考虑启动时清理或加 heartbeat。

---

## 四、修复成效速览

| 类别 | 数字 |
|---|---|
| 第一轮问题 | 17 项：✅11 / ⚠️4 / ❌2（另 D7 记未隔离） |
| 本轮新发现 | 8 项：P0×1（R2-0）、P1×2（R2-1、R2-2）、P2×5 |
| trace.db 实证 | steps 含 RECOVERED=1、recoveries.step_id=121 关联成功、artifacts 证据文件落盘 ✓ |

## 五、建议处理顺序

1. **R2-0**（screen 键匹配 + verify_stage7 mock 升级为真实 schema）——不修则目标 5/7 的 DRIFT 检测在真实链路失效；
2. **R2-1**（RECOVERED 不 raise）——不修则目标 7 的"用例最终 PASS"仍不闭环；
3. **R2-2**（input 恢复值优先级）+ **R2-4**（infra wiring）——影响安全性与目标 2 验收；
4. 其余 P2 项可随下次迭代顺手清。
