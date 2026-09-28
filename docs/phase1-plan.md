# Mobile Test Agent — Phase 1 实施计划

> **For Hermes:** 用 subagent-driven-development 按任务派发执行；每任务两段 review（spec 合规 → 代码质量）。
>
> **Goal:** 在 P0 基线上实现"关闭 LLM 也稳定跑真实 iOS 回归、开启 LLM 仅受控恢复"的测试平台（P1 设计文档 12 目标 / 18 硬约束 / 5 里程碑 / 24 项故障注入矩阵全部落地）。
>
> **Architecture:** 沿用 P0 分层（session/executor/runner/source/agent/llm/trace），按设计第 16 节扩展为 cli/runner/executor/environment/repository/source/agent/llm/trace/report/testcase 全家桶。核心原则：**确定性核心先行（M1/M2/M3），Recovery+LLM 最后（M4），稳定性基线收尾（M5）**。所有纯逻辑（resolver/risk/idempotency/决策表/redactor/聚合）脱离设备单测（H18）。
>
> **Tech Stack:** Python 3.11 + Pydantic 2.13（已装）、pytest（M0 安装）、Appium-Python-Client 6.x、SwiftSyntax（SwiftPM 包 `mta-source-scan`）、SQLite（trace schema 0.1 + migrations）。
>
> **工作分支:** `P1-0928`（已存在，含 P0 全部历史）。**设计基线:** `docs/mobile-test-agent-phase1-design.md` v1.0-draft。**每任务完成即 commit**（沿用 P0 惯例）+ 里程碑末打 tag `checkpoint-p1-mN`。

---

## 0. P0 基线与现状差距（写计划前已核实）

| 设计要求 | 现状 | 差距 |
|---|---|---|
| Trace schema 0.1（14.2） | P0 五表（runs/steps/recoveries/infra_events） | 缺 `testcase_runs/recovery_reviews`、`failure_phase/attribution`、`schema_migrations` |
| `find`/`act` 拆分（7.1） | `Executor.find/tap/input` 已分开，但无 phase 记录 | 补 `failure_phase` 标注 |
| Locator 来源（5.5） | runner 内 `_locator_chain` 硬编码两策略 | 改为 Repository 解析的 strategies |
| Recovery（9.2） | `agent/recovery.py` 单函数直调 LLM | 重构为 RecoveryEngine 管线 + 校验链 |
| budget（10.5） | 仅 per-run 计数 | 加 per-testcase/timeout/熔断 |
| source scan（12.1） | 单文件 main.swift + objc_scan.py | 项目级 SwiftPM 包 + constant 两遍扫描 + Storyboard |
| Screen（13） | 无 | 全新：App 侧 marker + Spike + `current_screen()` |
| CLI（14.6） | `run_phase0_demo.py` 脚本 | 全新 `mta` CLI |
| 测试基建（17.1） | 无 pytest / tests/ | 全新 tests/{unit,integration,fault_injection} |
| 被测 App | LoginDemo 仅登录/首页 2 Screen | 需扩展 Search/Detail/Profile + reset hook + plist 注入（成功标准要 20+ 用例 5+ Screen） |

**环境事实**：模拟器 iPhone 14 UDID `AEBDAE77-7C5B-468B-A5A7-01D41EDAAD9E`（iOS 18.5）；Appium 3.7.0 需 `DEVELOPER_DIR`；LLM 网关 `http://127.0.0.1:15721/v1` model `step-5-preview`（key 走 env）；`/tmp/swift_scan` 会被清理（重扫前 `make scan`）。

**P0 行为保留原则**：P0 的 `phase0/verify_stage*.py` 全部保留且须持续通过（回归底线）；`run_phase0_demo.py` 在 P1-07 完成后标记 deprecated 但不删。

---

## 1. 里程碑总览与依赖

```text
M0 基建(0.5d) ─► M1 Schema/Repo/Screen(3d) ─► M2 确定性平台(4d) ─► M3 Source/Build(3d) ─► M4 Recovery/LLM(4d) ─► M5 稳定性(1d+整夜)
                        │                        │                     │
                        └── P1-03 Spike 是 M1 的第一件事，结论决定 App 侧 marker 实现方式
```

- 每个 Gate 不通过不进下一里程碑（设计 17 节原则）。
- 任务内 TDD：先写失败测试 → 最小实现 → 过测试 → commit。
- 真机/模拟器只跑必须真机的（Spike、reset hook、稳定性）；其余用 FakeDriver 单测。

---

## M0 — 测试基建（半天）

### Task 0.1: pytest 环境 + 首个纯逻辑单测样本

**Objective:** 建 tests/ 目录结构与 CI 可跑的单测底座。

**Files:**
- Create: `tests/unit/test_smoke.py`、`tests/__init__.py`、`tests/unit/__init__.py`
- Create: `pytest.ini`（`testpaths = tests`，`-q`）

**Steps:**
1. `source .venv/bin/activate && pip install pytest -q`
2. 写 `tests/unit/test_smoke.py`：
   ```python
   from tracer.redactor import redact

   def test_redactor_importable():
       assert callable(redact)
   ```
3. Run: `python -m pytest tests/unit -q` → Expected: 1 passed
4. Makefile 加 `test:` 目标（`python -m pytest tests -q`）。
5. Commit: `chore(p1): pytest infra`

---

## M1 — Schema / Repository / Screen（设计 P1-01~04）

> Gate M1：5 条真实用例通过；Schema 冻结到 0.2；Screen marker 在 Tab/Navigation/Sheet 三场景验证；引用无歧义。

### Task 1.0（先行）: P1-03 Spike — SwiftUI Screen marker 三场景验证

**Objective:** 验证 `screen.<Name>` marker 挂根视图后子元素 identifier 不被吞（设计 13.3 明确：结论出来前不铺开）。

**Files:**
- Modify: `ios_demo/LoginDemo/LoginDemoApp.swift`（临时 spike 页）
- Create: `docs/p1_spike_screen_marker.md`（结论记录）

**Steps:**
1. 在 Demo App 临时加三个容器场景：TabView 页、NavigationStack push 页、sheet 页，根视图分别用 `mtaScreen("screen.TabX")` 修饰（设计 13.3 的 extension 示意代码），容器与子元素各挂 identifier。
2. 用 P0 smoke 脚本模式写 `phase0/spike_screen.py`：page_source 断言 ① marker 可见唯一 ② 同屏子元素 identifier 仍可 find。
3. 三场景各跑一次，截图 + page_source 留档到 `out/spike/`。
4. **产出决策**写入 `docs/p1_spike_screen_marker.md`：`mtaScreen()` 方案可行 / 需改（如 `accessibilityElement(children: .contain)` 换 `.combine` 或挂 background 层）。后续 Task 1.5 的 App 侧实现按此结论。
5. Commit: `spike(p1): screen marker feasibility on Tab/Nav/Sheet`

⚠️ 若三场景任一失败，回到设计层讨论（marker 改挂 overlay 层等），不要带病进入 Task 1.5。

### Task 1.1: P1-01 TestCase Schema（Pydantic, 0.1）

**Objective:** 设计 6.3 骨架落地 + `extra="forbid"` + version 分发。

**Files:**
- Create: `testcase/schema.py`、`testcase/loader.py`（重构 P0 版）
- Test: `tests/unit/test_schema.py`

**Steps:**
1. 失败测试（覆盖设计 6.1/6.2/6.3 全部字段）：
   ```python
   import pytest
   from testcase.schema import TestCase, TargetRef
   from testcase.loader import load_testcase

   def test_target_ref_str_sugar():
       assert TargetRef.model_validate("login_button") == TargetRef(type="element", id="login_button")
       assert TargetRef.model_validate("screen:HomeView") == TargetRef(type="screen", id="HomeView")

   def test_unknown_field_rejected():
       with pytest.raises(ValueError):
           load_testcase_from_dict({"schema_version": "0.1", "id": "x", "steps": [{"action": "tap", "typo_field": 1}]})

   def test_unknown_schema_version_errors():
       with pytest.raises(ValueError, match="schema_version"):
           load_testcase_from_dict({"schema_version": "9.9", "id": "x", "steps": []})

   def test_wait_condition_active_screen_only(): ...   # active + element target → 校验错误
   def test_retry_key_forbidden(): ...                  # 用例配置 retry → 报错（H7/6.2）
   ```
2. 实现：按设计 6.3 骨架逐字段写（`Idempotency/Risk/TargetRef/Postcondition/ActionStep/WaitStep/AssertionStep/TestCase`），`model_config = ConfigDict(extra="forbid")`；loader 按 `schema_version` 分发 parser，未知版本明确报错。
3. Run: `python -m pytest tests/unit/test_schema.py -q` → PASS
4. Commit: `feat(p1): testcase schema 0.1 + loader (P1-01)`

### Task 1.2: Repository — overrides 起步 + 合并规则

**Objective:** 设计 5.1–5.5：手写 overrides 起步，EffectiveElement 合并（M3 才有 generated）。

**Files:**
- Create: `repository/resolver.py`、`repository/loader.py`、`repository/overrides/elements/LoginView.yaml`、`repository/overrides/screens/LoginView.yaml`
- Test: `tests/unit/test_repository.py`

**Steps:**
1. 失败测试：
   ```python
   def test_resolve_short_name_unique(): ...          # "login_button" → EffectiveElement
   def test_resolve_qualified_name(): ...             # "LoginView.confirm_button"
   def test_resolve_screen_sugar(): ...               # screen:HomeView → EffectiveScreen
   def test_ambiguous_short_name_lints(): ...         # 两 Screen 同名 → LintIssue（不允许运行时猜，4.1）
   def test_override_replace_prepend_append(): ...    # 5.3 三种 mode
   def test_override_shadows_source_warning(): ...    # 冲突 → warnings 含 override_shadows_source
   def test_origin_preserved_after_merge(): ...       # 每条策略保留 origin（5.3）
   ```
2. 实现 `EffectiveElement/EffectiveScreen` dataclass + `Repository.resolve/elements_of/lint`（签名按设计 5.5）。
3. Run → PASS → Commit: `feat(p1): repository overrides + merge rules (P1-02)`

### Task 1.3: `mta lint` 基础版

**Objective:** 设计 6.4 静态检查（退出码 3 的前置门）。

**Files:**
- Create: `testcase/lint.py`、`cli/main.py`（argparse 骨架：`mta lint/run/review/...` 子命令占位）
- Test: `tests/unit/test_lint.py`

**Steps:**
1. 失败测试逐条覆盖 6.4：schema 非法 / target 解析不了 / 短名歧义 / `${VAR}` SecretProvider 解析不了 / 用例含 `sleep` / `active` 用在 element / 非幂等缺 postcondition（**警告**级）。
2. 实现 `lint(testcases, repo, secrets) -> list[LintIssue]`；`mta lint` CLI 出口码：有 ERROR → 3，仅 WARNING → 0。
3. Run → PASS → Commit: `feat(p1): mta lint (P1-01)`

### Task 1.4: `current_screen()` 运行时判定

**Objective:** 设计 13.2 纯函数版（page_source + repo → ScreenResult）。

**Files:**
- Create: `source/screen.py`
- Test: `tests/unit/test_screen.py`

**Steps:**
1. 失败测试覆盖 13.2 全分支：0 marker → `CURRENT_SCREEN_UNKNOWN`；1 → 该 Screen；多 marker 无 modal → `SCREEN_AMBIGUOUS`；恰一 modal/overlay → 取它；`kind_hint: page` 多个 → AMBIGUOUS。
2. 实现（纯函数，输入 XML 字符串 + repo，不发网络）。
3. Run → PASS → Commit: `feat(p1): current_screen pure function (P1-03)`

### Task 1.5: Demo App 侧 Screen marker + 扩展页面

**Objective:** 按 Spike 结论给 LoginDemo 加 `mtaScreen()`；扩展 Search/Detail/Profile 页（P1-04 的 5 条用例、M2 的 20+ 用例都依赖）。

**Files:**
- Modify: `ios_demo/LoginDemo/LoginDemoApp.swift`、`ios_demo/LoginDemo/ContentView.swift`
- Create: `ios_demo/LoginDemo/MTAScreen.swift`（`mtaScreen()` modifier）

**Steps:**
1. `mtaScreen()` 按 Spike 结论实现；Login/Home/Search/Detail/Profile 五页根视图挂 `screen.<Name>`。
2. 加最小业务：首页列表（3 个 cell，identifier `cell_<key>`，附录 A3）→ Search 页 → Detail 页；Profile 页含 logout 按钮。
3. 构建安装（P0 命令），`xcrun simctl launch` 后用 Appium page_source 验证 5 个 marker 均唯一可见。
4. Commit: `feat(app): screen markers + search/detail/profile pages`

### Task 1.6: P1-04 — 5 条真实用例跑通 + Schema 复盘

**Objective:** Login/Logout/Search/OpenDetail/Profile 五条 YAML（`suites/smoke/`）在最小 Executor 上全绿。

**Files:**
- Create: `suites/smoke/login_001.yaml` 等 5 条、`phase0/verify_p1_m1.py`（Gate 验收脚本）
- Modify: `runner/testcase_runner.py`（target 改走 Repository resolve；暂不加 wait/assertion 引擎，用现有 step 类型 + 新 schema 字段子集）

**Steps:**
1. 写 5 条 YAML（schema_version 0.1；password 步骤 `sensitive: true`；登录按钮显式 `idempotency: IDEMPOTENT`——设计 6 示例）。
2. `mta lint` 先过（引用/secret 全解析）。
3. 最小 runner 适配：`TargetRef` → repo.resolve → strategies → find/tap/input。
4. `python phase0/verify_p1_m1.py` → 5/5 PASS。
5. **复盘记录**：Schema 用着别扭的点写进 `docs/p1_schema_review.md`；确认无阻塞后 schema_version 升 `0.2`（Gate 要求），同步更新 5 条 YAML。
6. Commit: `feat(p1): 5 real testcases on schema 0.2 (P1-04)` + tag `checkpoint-p1-m1`

---

## M2 — 确定性平台（设计 P1-05~08）

> Gate M2：20+ 用例 `--no-llm` 全绿；cleanup 失败终止套件；Trace 0.1 完整；JUnit CI 可见。

### Task 2.1: P1-05 Wait Engine

**Objective:** 设计 7.3：轮询 wait、`WAIT_TIMEOUT`、implicit wait=0、screen active 廉价路径。

**Files:**
- Create: `executor/wait.py`；Modify: `executor/executor.py`（session 建 session 时设 implicit wait 0）
- Test: `tests/unit/test_wait.py`（FakeDriver：可控的 find 结果序列）

**Steps:**
1. 失败测试：超时抛 `WAIT_TIMEOUT`（非 ELEMENT_NOT_FOUND）；`polling_interval` 来自参数不写死；`not_exists` 命中即返回；`text_contains/text_equals/enabled/disabled/visible` 条件矩阵；screen active 只 find marker 不拉 page_source（FakeDriver 记录调用证明）。
2. 实现。Run → PASS → Commit: `feat(p1): wait engine (P1-05)`

### Task 2.2: P1-05 Assertion Engine

**Objective:** 设计 7.3：结构化结果 + 值断言/目标漂移两条路径。

**Files:**
- Create: `executor/assertion.py`
- Test: `tests/unit/test_assertion.py`

**Steps:**
1. 失败测试：`exists/not_exists/text_equals/text_contains/element_count/enabled/disabled`；值不符 → `ASSERTION_VALUE_MISMATCH` 结构化结果（expected/actual/target/passed）；目标定位失败 → `ASSERTION_TARGET_DRIFT` 标记（供 Recovery 分流）；`not_exists` 目标找不到 = passed。
2. 实现 → PASS → Commit: `feat(p1): assertion engine (P1-05)`

### Task 2.3: P1-06 Environment Manager + App reset hook

**Objective:** 设计 11：reset 策略矩阵 + CapabilityResolver + cleanup 规则；附录 A4 的 App 侧 hook。

**Files:**
- Create: `environment/manager.py`、`environment/reset.py`、`environment/capabilities.py`
- Modify: `ios_demo/LoginDemo/LoginDemoApp.swift`（启动参数 `-UITestReset` → 展示前清 UserDefaults/缓存/登录态，仅 DEBUG 生效）
- Test: `tests/unit/test_environment.py`

**Steps:**
1. 失败测试：CapabilityResolver 矩阵（11.1 表逐行：SNAPSHOT 真机不支持等）→ 不支持抛 `UnsupportedResetError`（不静默降级）；cleanup 失败 → `CleanupError`；lint 阶段拦截不支持的策略（11.2）。
2. App 侧 hook：`ProcessInfo.processInfo.arguments.contains("-UITestReset")` 时清理；构建安装后验证 reset 后回到未登录态。
3. Run → PASS → Commit: `feat(p1): environment manager + app reset hook (P1-06)`

### Task 2.4: Trace schema 0.1 迁移

**Objective:** 设计 14.2 五表 → 新 schema + migrations，Redactor 前置不变（H8）。

**Files:**
- Create: `trace/migrations/001_p1_schema0.1.sql`、`trace/storage.py`
- Modify: `tracer/recorder.py`（新表写入接口；`record_*` 内部先 redact 的现有行为保留）
- Test: `tests/unit/test_trace_schema.py`

**Steps:**
1. 失败测试：`schema_migrations` 版本链；`testcase_runs` 优先级聚合（8.1：ENV > INFRA > BLOCKED > FAIL > RECOVERED > PASS 纯函数）；`failure_attribution` 默认 UNTRIAGED；recoveries 新列齐全（kind/校验五项/llm_tokens）。
2. 实现 `migrate(conn)`：旧库自动升级（P0 数据保留），新库直建。
3. Run → PASS → Commit: `feat(p1): trace schema 0.1 + migrations (P1-07)`

### Task 2.5: P1-07 Runner 重构 + 结果模型 + 退出码

**Objective:** 设计 7.1 执行管线 + 8 章结果模型 + 多用例套件。

**Files:**
- Create: `runner/runner.py`、`runner/suite.py`、`runner/lifecycle.py`、`runner/result.py`、`executor/policy.py`（7.4 重试规则表纯函数）、`executor/guard.py`（10.1）
- Modify: `runner/testcase_runner.py`（逐步替换，P0 脚本兼容到 M2 Gate 后退役）
- Test: `tests/unit/test_policy.py`、`tests/unit/test_result.py`、`tests/integration/test_runner_fakedriver.py`

**Steps:**
1. 失败测试（全部纯函数，H18）：
   - `effective_idempotency(step, element)`：声明取更严格（NON > UNKNOWN > IDEMPOTENT）；关键词命中 → NON；UNKNOWN 按 NON（7.4 逐条）。
   - `effective_risk`：max(step, element, screen, env, heuristic_bump)；显式声明禁启发（H17）。
   - 重试规则表 7.4：PRE_DISPATCH 任意可重试；POST_DISPATCH+IDEMPOTENT 有界；POST_DISPATCH+NON **禁止**、postcondition 分支。
   - Guard：CRITICAL → SECURITY_BLOCKED（sandbox+allow_in_sandbox 例外）；production 拒 HIGH/CRITICAL；blocked_targets。
   - 退出码优先级 `3 > 2 > 4 > 1 > 5`（8.4 纯函数）。
   - 通过率口径 8.3：PASS Rate ≠ (PASS+RECOVERED)/TOTAL 的回归断言。
2. 实现 `run_step` 管线（resolve → guard → ensure_alive → find[PRE_DISPATCH] → perform[POST_DISPATCH]）；`non_idempotent_dispatched` 状态跟踪（7.5）。
3. FakeDriver 集成测：find 失败/act 超时/WDA 死亡/多匹配四脚本。
4. Run → PASS → Commit: `feat(p1): runner + policy + guard + result model (P1-07)`

### Task 2.6: Report + JUnit + CLI run

**Objective:** 设计 14.5/14.6/8.5。

**Files:**
- Create: `report/html.py`、`report/junit.py`
- Modify: `cli/main.py`（`mta run` 全参数：--suite/--tag/--case/--no-llm/--junit/--html/--allow-metadata-mismatch/--allow-production/--config）
- Test: `tests/unit/test_junit.py`

**Steps:**
1. 失败测试：JUnit 映射（RECOVERED → `<failure type="RECOVERED_NEEDS_REVIEW">`；INFRA/ENV/BLOCKED → `<error>`）；退出码表 8.4 逐行。
2. HTML 首页字段（14.5 清单：RECOVERED 单独栏、LLM 调用数、Invocation Rate、WDA 重启数）。
3. Run → PASS → Commit: `feat(p1): report + junit + mta run (P1-07)`

### Task 2.7: P1-08 — 扩到 20+ 用例 + `--no-llm` 连续跑

**Objective:** Gate M2 主验收。

**Files:**
- Create: `suites/{smoke,search,account,regression}/`（共 20+ 条 YAML）、`phase0/verify_p1_m2.py`

**Steps:**
1. 补写用例覆盖 6.2 全部 step 类型（含 `wait_for` 各 condition、断言各 type、`swipe/back`、postcondition 示例）。
2. `mta lint` 全过；`mta run --no-llm` 连续 3 轮全绿（PASS=100%，LLM 调用 0）。
3. 故意注入一条 cleanup 失败 → 套件终止 + 退出码 2（H10 验证）后移除。
4. `python phase0/verify_p1_m2.py`（Gate 清单逐项）→ Commit + tag `checkpoint-p1-m2`

---

## M3 — Source Intelligence 与 Build Identity（设计 P1-09/10）

> Gate M3：Coverage 报告产出；generated+overrides 合并与手写基线一致；build 不匹配被拦截。

### Task 3.1: P1-09 SwiftPM 包 `mta-source-scan`

**Objective:** 设计 12.1/12.2：项目级扫描（SwiftUI/UIKit/Storyboard/XIB）+ `constant` 两遍扫描。

**Files:**
- Create: `source/swift_scan/Package.swift`、`source/swift_scan/Sources/mta-source-scan/main.swift`（按模块拆 Visitor 文件）
- Create: `source/storyboard.py`（XIB/Storyboard XML 解析）
- Modify: `source/metadata.py`（输出 12.3 schema：screens 数组 + container_type + label + resolution_type 含 constant）
- Test: `tests/unit/test_storyboard.py` + fixture swift 文件

**Steps:**
1. `swift package init --type executable`；Visitor 按类型拆：SwiftUI identifier/label、UIKit 赋值、`static let` 常量表（两遍：先收集唯一命名常量，再解析引用；不唯一 → dynamic/unknown，12.2 不猜值）。
2. 自定义 ViewModifier 模式（`.mtaID("x")` 类）识别。
3. `repository/loader.py` 加 `mta repo generate` → `repository/generated/<build>/`（H16：不手改）。
4. **一致性 Gate**：Task 1.2 手写 overrides 的语义 ID 集合 vs generated 集合 diff，无缺口（缺的在 overrides 补 origin: manual）。
5. `make scan` 保留兼容；新路径 `swift build -c release` 产物进 `source/swift_scan/.build/release/mta-source-scan`。
6. Commit: `feat(p1): project-level source scan SwiftPM (P1-09)`

### Task 3.2: Source Coverage 报告

**Objective:** 设计 12.7 指标纯函数 + 报告页。

**Files:**
- Create: `source/coverage.py`；Modify: `report/html.py`
- Test: `tests/unit/test_coverage.py`

**Steps:**
1. 失败测试：`Identifier Coverage = literal+constant 解析数 / 用例引用总数`；dynamic_ratio/unknown_ratio。
2. 实现 + `mta source scan` CLI 输出。Commit: `feat(p1): identifier coverage (P1-09)`

### Task 3.3: P1-10 Build Identity

**Objective:** 设计 12.5：plist 注入 + 校验 + override + Build-level diff。

**Files:**
- Create: `source/build_identity.py`；Makefile 加 `p1-build`（xcodebuild 注入 `MTA_GIT_COMMIT/MTA_BUILD_ID` 到 Info.plist——pbxproj 手写文件加 infoplist 键）
- Test: `tests/unit/test_build_identity.py`

**Steps:**
1. 失败测试：一致 → 通过；不一致 → `BUILD_METADATA_MISMATCH`；`--allow-metadata-mismatch` → 放行但 Trace 记 `metadata_mismatch=1, override=1`；`CI=true` 时需第二开关（12.5）。
2. `mta source diff`：只比已有用例实际到达的 Screen（12.6 表），输出 ADDED/REMOVED/RENAMED?/UNCHANGED/UNKNOWN。
3. 真机验证：改源码不重编 metadata → `mta run` 启动即退出码 3。
4. Commit: `feat(p1): build identity + source diff (P1-10)` + tag `checkpoint-p1-m3`

---

## M4 — 安全版 Recovery 与 LLM（设计 P1-11~13）

> Gate M4：24 项故障矩阵全过；漂移 build → RECOVERED + 退出码 5；--no-llm 同场景 FAIL；LLM 调用率 ≤10%。

### Task 4.1: P1-11 RecoveryEngine 非 LLM 部分

**Objective:** 设计 9.2 流水线前半 + 7.5 WDA 规则 + postcondition + Settle 重试 + RUN_MEMO 骨架。

**Files:**
- Create: `agent/recovery.py`（重构）、`agent/context.py`、`agent/policy.py`、`agent/risk.py`
- Test: `tests/unit/test_recovery_policy.py`、`tests/fault_injection/`

**Steps:**
1. 失败测试（决策表纯函数）：Wait Timeout 默认不进 Recovery（`recovery.on_wait_timeout=false`）；断言值失败不进（H6）；phase×幂等动作集合（9.2 第 4 步）；POST_DISPATCH+非幂等只允许 postcondition 检查；Settle 重试有界 1 次。
2. WDA 中途故障（7.5）：未发出非幂等 → 重启+重跑 1 次 attempt=2；已发出 → INFRA_FAILURE 不重跑；`wda.max_restart_per_run` 超限终止 run。FakeDriver 模拟。
3. `ExperienceStore` 预留（20 节）：`EmptyExperienceStore` 插在 reconciliation 后、LLM 前，恒返回 []。
4. Run → PASS → Commit: `feat(p1): recovery engine deterministic half (P1-11)`

### Task 4.2: P1-12 LLM Recovery + 校验链 + review 流程

**Objective:** 设计 9.3/9.4/9.5/10.4/10.5。

**Files:**
- Modify: `agent/recovery.py`、`llm/prompt.py`（分区模板 10.4）、`llm/budget.py`（per-testcase/timeout/breaker）、`llm/parser.py`（新：严格输出解析，额外字段忽略并记录）
- Create: `agent/review.py`（`mta review list/accept/reject`，只导出 overrides 补丁不自动写入，H15）
- Test: `tests/unit/test_llm_contract.py`、`tests/unit/test_budget.py`

**Steps:**
1. 失败测试（全部 FakeLLM，无网络）：
   - Prompt 分区含 `[UNTRUSTED OBSERVED UI]` 标注，UI 树脱敏后才入 prompt（H14/H8：先断言 prompt 文本无 secret 值）。
   - 输出解析：非法 JSON → `LLM_INVALID_OUTPUT`；`risk_level` 字段被忽略并记录（H4）；`confidence < min_confidence` → `LLM_LOW_CONFIDENCE`（只过滤不当安全依据）。
   - 候选校验链 9.3 五项逐一：数量/类型/Screen/risk/confidence → 对应 failure_type。
   - Budget：per-run + per-testcase 双限；连续 3 次失败熔断（10.5），熔断后 **不发** 新 API 调用（FakeLLM 计数断言，矩阵 #10）。
   - `RUN_MEMO`：同 run 内 (screen,target,build) 复用，内存态 run 结束丢弃（9.4）。
2. `mta review` CLI + `recovery_reviews` 表 PENDING 流转；accept 导出 overrides 补丁到 stdout/文件。
3. Run → PASS → Commit: `feat(p1): llm recovery + validation + review flow (P1-12)`

### Task 4.3: P1-13 故障注入矩阵 24 项全过

**Objective:** 设计 18 节逐项自动化。

**Files:**
- Create: `tests/fault_injection/test_matrix_01_12.py`、`test_matrix_13_24.py`、`phase0/verify_p1_m4.py`

**Steps:**
1. 矩阵每行一个测试（编号即测试名 `test_fi_01_locator_missing_no_llm` … `test_fi_24_recovery_then_cleanup_fail`）；#2/#15 漂移场景复用 F5 的改名重编译方法（skill 已记录流程），其余用 FakeDriver/FakeLLM。
2. 退出码断言并入每项（1/2/3/4/5 按表）。
3. `python -m pytest tests/fault_injection -q` → 24 passed；真机项（#2 漂移、#16/#17 WDA）单独脚本 `verify_p1_m4.py` 跑。
4. Commit: `test(p1): fault injection matrix 24/24 (P1-13)` + tag `checkpoint-p1-m4`

---

## M5 — 稳定性基线（设计 P1-14）

### Task 5.1: 50 次连续运行脚本 + 基线报告

**Objective:** 设计 1.2/17 M5：同 build 同套件连续 50 次，产出基线数据。

**Files:**
- Create: `scripts/p1_stability_run.sh`（循环 `mta run --no-llm`，逐轮落 run_id/结果/耗时到 CSV）、`scripts/p1_stability_report.py`（聚合：PASS/RECOVERED/FAIL/INFRA 比例、flaky 清单、WDA 重启总数、平均耗时）
- Create: `docs/p1_stability_baseline.md`（基线结论）

**Steps:**
1. 冒烟 3 轮验证脚本正确后，正式 50 轮（**预计整夜/分批跑**，脚本支持断点续跑：从 `runs/` 已有记录续）。
2. 每轮后检查：无 WDA session 泄漏（`xcrun simctl listapps` + Appium session 计数）、trace.db 无 RUNNING 残留、无未分类失败（attribution≠UNTRIAGED 的 FAIL 均有记录）。
3. 报告落盘 + Commit: `docs(p1): stability baseline 50 runs (P1-14)` + tag `checkpoint-p1-m5` + `v0.1-p1-complete`

---

## 2. 执行注意事项（给实现者/子智能体）

1. **硬约束检查表**：每个 PR/commit 自查 H1–H18，重点：H2（LLM 不进 Locator）、H5（RECOVERED≠PASS）、H7（POST_DISPATCH 非幂等绝不重试）、H8/H9（脱敏先于写盘/发 LLM）、H15（不自动写 Repository）。
2. **回归底线**：M2 起每个任务完成后跑 `python -m pytest tests -q` + `phase0/verify_stage2.py`（环境就绪时）；P0 的 8 个 verify_stage 脚本在 M4 结束前至少完整回归一次。
3. **Appium 依赖的测试**全部走 FakeDriver 单测；真机验证集中在：Spike（1.0）、reset hook（2.3）、漂移恢复（4.3 #2）、稳定性（5.1）。
4. **会话工具坑**（skill 已记录）：本会话文件读取/diff 回显偶发污染——所有文件修改后必须 terminal 重读/重跑验证，不信 patch 回显。
5. **open questions**（实现中遇到即停下确认，不猜）：
   - Spike 若证明 `mtaScreen()` 在 iOS 18.5 模拟器不可行 → 替代方案需回到设计层；
   - 5 条真实用例需要 App 侧 Search/Detail/Profile 的业务逻辑深度（列表数据从哪来）——默认全部本地 mock 数据，不引网络依赖；
   - `P1-0928` 分支是否直接作为主干工作分支，还是每里程碑开子分支（默认：直接在此分支，每里程碑打 tag）。

## 3. 工作量与顺序摘要

| 里程碑 | 任务 | 预估 | Gate |
|---|---|---|---|
| M0 | 0.1 | 0.5d | pytest 可跑 |
| M1 | 1.0~1.6 | 3d | 5 用例 + Schema 0.2 + Spike 结论 |
| M2 | 2.1~2.7 | 4d | 20+ 用例 --no-llm 全绿 |
| M3 | 3.1~3.3 | 3d | Coverage 报告 + build 拦截 |
| M4 | 4.1~4.3 | 4d | 故障矩阵 24/24 + 退出码 5 |
| M5 | 5.1 | 1d + 夜跑 | 50 次基线无泄漏 |
