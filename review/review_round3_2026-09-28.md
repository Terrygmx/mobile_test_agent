# Mobile Test Agent — 第三轮 Review 报告（R2 修复验证 + F5 关闭验证）

> 审查日期：2026-09-28 ｜ 性质：只读 review，未修改任何项目文件
> 对比基线：`review/review_round2_2026-09-27.md`（R2-0~R2-6 + F5）与 `review/skipped_and_reasons_round2.md`
> 提交基线：`fb53bd3`（R2 修复）+ `05f274d`（F5，tag checkpoint-stage9-f5），工作区 clean

---

## 一、总体结论

**第二轮 8 项问题（R2-0~R2-4、R2-6×4 子项、F5）：全部关闭 ✅，且 F5 用真实改名+重编译+内网真实 LLM 走通了端到端链路。本轮未发现新的 P0/P1，仅 5 个 P2 级 polish 项（多为修复本身引入的小的不一致）。**

| 验证维度 | 结果 |
|---|---|
| 代码审查 | R2-0~R2-4、R2-6 全部按建议方向落实，实现质量好（个别处优于建议） |
| Trace 实证 | trace.db 佐证：step 165 `input` → **RECOVERED**，所属 run `5cf97f4d` → **PASS**（R2-1 真链路闭环）；recoveries id=30 `step_id=165, llm_target=account_field, accepted=1`（R2-3 精确回填实证）；RUNNING 残留 0 条（清理生效） |
| 场景真实性 | 真实改名（username_field→account_field、login_button→submit_button）+ 重编译重装 + 旧 metadata 保持不重扫 + 内网真实 LLM（step-5-preview）——F5 的验收强度终于达到设计第 5 节要求 |
| 回归验证 | 8/8 Stage 脚本 + demo 复跑通过；源码 git checkout 还原后 Stage 6 复跑 ✓ |
| Skip 决策 | F6/D7 延期到 Phase 1，理由成立（F6 是增强非正确性；D7 已部分随 R2-0 合入主干，再隔离收益低） |

---

## 二、R2 修复逐项核对

| # | 问题 | 状态 | 核查要点 |
|---|---|---|---|
| R2-0 | screen 键不匹配 → DRIFT 判不出 | ✅ | swift_scan 顶层改输出 `screens` 列表（struct 名全集）；reconcile 改为 declared-screens 三分支语义：传入真实 struct 名 → 严格过滤；传入文件名 → 退化为 declared 全集（DRIFT 仍判得出，且 docstring 明确标注了退化语义）；旧 schema（元素无 screen）兜底纳入。**verify_stage7 mock 已升级为真实 schema 并新增 [5][6] 两条回归用例**——正是覆盖了上轮的断点（顶层名≠struct 名）和跨 screen 串页防护 |
| R2-1 | RECOVERED 后仍 raise | ✅ | 改为仅恢复失败时 raise；F5 trace 实证 run 级 PASS + step RECOVERED 共存 |
| R2-2 | LLM 值优先于 secret 原值 | ✅ | `step_value or result.get("value")`；LLM_NO_VALUE 判定同步调整；prompt 明确"input_value 由系统注入，勿返回明文" |
| R2-3 | MAX(id) 回填 hack | ✅ | `record_recovery` 返回行 id → runner 按 `_last_rec_row` 精确回填；F5 实证 step_id=165 关联正确；并发下不会改错行 |
| R2-4 | infra 落库无人接线 | ✅ | 新增 `attach_recorder()` 后挂接口（解决 run_id 时序问题）；demo 主入口已接线；verify_stage2 在临时 db 上端到端断言 SQLite infra_events 含 WDA_DEAD/WDA_RESTARTED |
| R2-6a | objc UNKNOWN id 未同步 | ✅ | `UNKNOWN:file:lineno` 与 swift_scan 一致 |
| R2-6b | 跨行块注释 | ✅ | BLOCK_COMMENT 带 re.S 处理 |
| R2-6c | prompt value 指代歧义 | ⚠️ | 消歧本身做了（target.value / input_value 分字段），但**引入了新的解析不同步**，见 R3-1 |
| R2-6d | RUNNING 残留 | ✅ | Recorder 构造时清为 FAIL；实测残留 0。附并发注意事项，见 R3-4 |
| R2-5 | demo 未启用自动恢复 | ✅ | demo 构造 recovery_ctx 并新增 tc2 自动恢复段（有 1 个 stub 模式瑕疵，见 R3-2） |
| F5 | 真实改名重编译验收 | ✅ 关闭 | verify_stage9_f5.py 场景构造正确（不调 build_metadata 保住 DRIFT 前提的注释很关键）；input 做主断言、tap 提交类做观察段的双轨设计合理；附带发现（提交类按钮 LLM 判 HIGH → fail-closed 属策略正确）已记录，处理方式成熟 |

---

## 三、本轮新发现（全部 P2）

### R3-1【P2】prompt 与解析器字段不同步：`input_value` 无人消费

R2-2/R2-6 修复后，prompt 让 LLM 输出 `"input_value": null` 并声明"由系统注入"，但 `recovery.py` 仍只解析 `result.get("value")`（105/118 行），**从不读 `input_value`**。当前无害（step_value 本就是预期来源、LLM 被要求保持 null），但两处契约已脱节：若未来想允许 LLM 在原步骤无值时提供输入（如验证码场景），改 prompt 不会生效；反之若 LLM 无视指令填了旧字段 `value`，会被当兜底值采用。建议二选一：解析器同步读 `input_value` 作兜底，或 prompt 删掉该字段。

### R3-2【P2】stub 模式下 demo 的自动恢复段永远无法成功

demo tc2 是 `input` 步骤，但 `StubLLM` 固定返回 `"action": "tap"` → `ALLOWED_ACTIONS["input"]={"tap"}` 不含 tap → 必然 `LLM_ACTION_MISMATCH` → 打印"用例未恢复"。demo 最终 exit 0 只靠手动段，所以**--stub 模式下 R2-5 的演示段是静默失败的**，演示效果失真。建议 StubLLM 按 prompt 中的原动作回显（或准备两个 stub 场景）。

### R3-3【P2】verify_stage9_f5.py 两处健壮性小点

1. `rows = ....fetchall()` 后直接 `rows[0]`——若 runner 的 run 没落任何 step（如 recovery 链路早退），会 IndexError 而非清晰断言失败；
2. 外层脚本与 runner 用了**同名 test_case**（`f5_real_rename`，trace.db 里两条 run 记录），断言查询靠 `ORDER BY s.id DESC LIMIT 1` 碰巧取对。建议外层 run 改名（如 `f5_real_rename_wrapper`）或按 run_id 显式区分。

### R3-4【P2】RUNNING 清理假设单进程

`Recorder.__init__` 无条件把所有 RUNNING run 判 FAIL——若未来两个进程共享同一 trace.db，进程 B 构造 Recorder 会把进程 A 正在跑的 run 误标 FAIL。Phase 0 单进程无碍，Phase 1 若上并发需改为按 pid/心跳判定。

### R3-5【P2】F5 脚本内网网关配置内嵌

`GATEWAY = {..., "api_key": "sk-test"}` 硬编码在脚本里。内网 dummy key 风险低，但与"密钥走环境变量/SecretProvider"的项目原则不一致，建议改为 `os.environ.get` 读取（与 LLMProvider 的默认行为对齐即可，一行改动）。

---

## 四、遗留项确认（与 skip 文档一致）

| 项 | 状态 | 备注 |
|---|---|---|
| F6 元素 type 推断 | 延期 Phase 1 ✅ 合理 | locator 链不依赖 type，是增强非正确性 |
| D7 Phase 1 内容隔离 | 延期 Phase 1 ✅ 合理 | objc_scan 修复已随主干合入，此时隔离风险大于收益 |
| infra_events 主 db 为 0 条 | 非问题 | F5/demo 期间无 WDA restart；目标 2 的 SQLite 验收已由 verify_stage2 在临时 db 上端到端覆盖 |

---

## 五、结论与建议

**Phase 0 的两轮 review 所有问题（P0×3 / P1×9 / P2×12 / R2×8 / F5）至此全部关闭或有意延期，且关键修复均有 trace.db 真实数据佐证而非仅凭测试脚本自证。** 代码质量从第一轮"能跑通但文档失同步"演进到当前"集成闭环 + 场景真实 + 验证脚本覆盖回归断点"，状态良好，可以以此为基线启动 Phase 1。

建议随手清（不阻塞，均为一行级改动）：
1. R3-1：prompt/解析器字段对齐（`input_value`）；
2. R3-2：StubLLM 回显原动作，让 --stub 模式的自动恢复段真实可演示；
3. R3-5：F5 网关 key 改环境变量读取；
4. R3-3/R3-4 可留到 Phase 1 一起处理（并发与脚本健壮性）。
