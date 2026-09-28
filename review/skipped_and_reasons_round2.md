# Round 2 遗留项处理说明（skipped & reasons）

> 2026-09-27 ｜ 回归基线：8/8 Stage 验收脚本 + run_phase0_demo 全部 exit 0
> 本文档只记录**本轮有意不处理**的项及理由；已修复项见 review_round2 报告与 git log。

## 跳过项

### ~~F5 — 验收用真实改名重编译，而非"查不存在元素"模拟~~ ✅ 已关闭（2026-09-28）
- **已补齐**：内网环境就绪后执行了真实改名端到端（`phase0/verify_stage9_f5.py`）：
  - `username_field`→`account_field`、`login_button`→`submit_button` 真实改名，
    重编译重装，metadata 保持旧产物 → 源/运行时真实 DRIFT；
  - 真实 LLM（内网网关 step-5-preview）走 reconcile → recover → 唯一性校验 →
    执行，steps.status=RECOVERED、runs.status=PASS、recoveries.accepted=1 且
    step_id 精确回填；
  - 验证后源码已还原、重编译，Stage 6 + demo 复跑通过（基线无回归）。
- **附带发现**：tap 登录按钮（提交类）时 LLM 可能判 HIGH → fail-closed 拒绝
  （LLM_RISK_NOT_LOW）。这是 prompt 规则的正确执行，但意味着"tap 提交类按钮"
  不适合做恢复成功的硬断言场景；确定性断言请用 input 场景。

### F6 — 元素 type 恒为 unknown（未传元素级 type）
- **原因**：对 reconciliation 候选过滤是增强项（减少 candidates 噪声），
  非正确性问题；改 type 推断需动 SwiftSyntax visitor 主链路，收益/风险比不划算。
- **现状**：locator 链（accessibility_id → predicate name==）不依赖 type。
- **优先级**：P2。等 Phase 1 元素 schema 统一时一并处理。

### D7 — objc_scan 等 Phase 1 内容混入 Phase 0 仓库
- **原因**：D7 内容已随 R2-0 修复合入主干（reconciliation screen 语义、
  objc UNKNOWN:file:line），且回归通过；此时挪分支/隔离的收益低于
  再动主干的风险。
- **决定**：保留原状，Phase 1 正式启动时再做目录级隔离。

## 本轮实际修复（对照，非跳过）
- R2-0：reconciliation screen 过滤语义（declared-screens 集合基准）+ stage7 mock 升级真实 schema
- R2-1：RECOVERED 不 raise（run 级 PASS 闭环）
- R2-2：input 恢复值优先 SecretProvider 原值
- R2-3：recoveries.step_id 精确行回填（rec_row_id）
- R2-4：DeviceSession infra SQLite 接线（verify_stage2 端到端验证）
- R2-6：objc UNKNOWN:file:line 同步、跨行块注释、prompt target.value/input_value
  措辞消歧、runs RUNNING 残留启动清理
- 附带：verify_stage7 sys.path 修正、verify_stage2 缺失的 Recorder 导入
