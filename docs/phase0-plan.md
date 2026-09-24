# Mobile Test Agent — 实现阶段拆分（依据 docs/mobile-test-agent-phase0-design.md）

> 本文件是 Phase 0 的执行计划。每个 Stage 结束有明确的"验证标准"，
> 完成一个验证一个，不攒到最后联调。

## 环境基线（已确认）

| 项 | 值 |
|---|---|
| Xcode | 27.0 (27A266a)，需 `export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer` |
| Simulator | iPhone 14 (AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E)，iOS 18.5 |
| Appium | 3.7.0 + xcuitest driver 12.13.2 |
| Python | 3.11.9（项目 venv `.venv/`） |
| Python 依赖 | Appium-Python-Client 6.0.7, pydantic 2.x, PyYAML 6.x |

待定：被测 App（先用官方 Demo App 打通，后续替换真实 App 的 UITest Build）。

## Stage 1 — Appium 链路打通（设计文档目标 1）

- `phase0/smoke_test.py`：连接 Appium → launch App → screenshot / page_source。
- 被测 App：Apple 官方示例（UICatalog 或类似）或任意含 accessibility id 的 Demo；
  若无，先验证 simulator 内置 Springboard 层操作（tap/screenshot）。
- **验证**：脚本无异常退出，产出 smoke_test.png + page_source 前 500 字符。

## Stage 2 — DeviceSession：health check + WDA 自愈（目标 2）

- `session/device_session.py`：`connect() / health_check() / restart_wda() / ensure_alive()`
- `InfraError` 异常类型与测试失败隔离。
- **验证**：手动杀 WDA 相关进程/session 后，下一次 action 自动恢复；
  infra 记录 WDA_DEAD + WDA_RESTARTED。

## Stage 3 — AppSession：launch/terminate/reinstall + Reset Matrix（目标 3）

- `session/app_session.py` + capability matrix（写死 Phase 0 版本）。
- **验证**：reinstall/relaunch 后 WDA session 不重建（Appium session 复用）。

## Stage 4 — Executor + Locator（accessibility_id + predicate 两策略）

- `executor/executor.py`, `executor/locator.py`
- 唯一性校验：0 → ElementNotFound；≥2 → AmbiguousElement；每次 find 前 ensure_alive()。
- **验证**：对 Demo App 的元素完成一次 tap + input。

## Stage 5 — Testcase YAML + SecretProvider + Trace（目标 6 前半）

- `testcase/schema.py`（pydantic）+ `loader.py` + `login_demo.yaml`
- `environment/secrets.py`：`SecretProvider` 接口 + `EnvSecretProvider`
- `trace/`：SQLite schema（runs/steps/recoveries/infra_events）+ Recorder + Redactor
  （Redactor 在写盘前调用，硬约束）。
- **验证**：完整跑一个用例，runs/steps 表有记录，密码字段 `***REDACTED***`。

## Stage 6 — Source Intelligence：SwiftSyntax 单页解析（目标 4）

- `source/swift_scan.swift` + `source/metadata.py` → `source_metadata.json`
- 只识别 `.accessibilityIdentifier("literal")`；非字面量 → `resolution_type: "unknown"`。
- **验证**：对一页 Swift 源码产出含 literal/unknown 两类元素的 metadata.json。

## Stage 7 — 局部 Reconciliation（目标 5）

- `source/reconciliation.py`：失败时只 diff 当前 screen 的 metadata 子集。
- **验证**：构造 locator 失败场景，返回 DRIFT + runtime 候选列表。

## Stage 8 — LLM Recovery + Budget（目标 7 + 8）

- `llm/provider.py`（接口 + 可配置 base_url/model）、`llm/budget.py`、`llm/prompt.py`
- `agent/recovery.py`：budget → prompt → 结构化解析 → risk_level 检查（只 LOW 自动执行）
  → 唯一性校验 → 执行；全程写 recoveries 表。
- **验证 A**：改名 login_button → signin_button 重建 App，跑用例走通 Recovery，
  step status = RECOVERED。
- **验证 B**：budget=0，同场景 fail closed，且无真实 LLM 调用。

## Stage 9 — 端到端联调（设计文档第 5 节 8 步验收）

- `run_phase0_demo.py` 串联，逐条核对 8 项目标，导出 Trace 证据。

## 明确不做（Phase 0 范围红线）

Flaky 检测 / Build Reconciliation / Experience Store / Recovery Cache / 多设备并行 /
真机特殊处理 / Non-idempotent postcondition 检查。
