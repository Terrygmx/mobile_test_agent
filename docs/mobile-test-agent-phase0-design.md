# Mobile Test Agent — Phase 0 设计文档

> 本文档面向两类读者：
> 1. **人类工程师** —— 理解 Phase 0 要验证什么、为什么这么设计。
> 2. **编码智能体（如 Claude Code）** —— 可以直接依据第 4/5/6 节的接口定义、目录结构、schema 和验收步骤动手写代码，无需额外澄清设计意图。
>
> Phase 0 不产出可用于回归测试的完整系统，只产出一条**从源码到 LLM Recovery 完整跑通一次**的最小闭环，用来提前消灭本项目最大的几个技术风险。

---

## 0. 前置约束（编码智能体必须遵守）

- 语言：Python 3.11+
- 目标平台：iOS（Simulator 优先，真机后置）
- 不允许在 Phase 0 引入：Experience Store / UI State Graph / 并行设备调度 / Subagent —— 这些是 V1 后期或 V2 的内容，Phase 0 出现会导致范围失控。
- 所有涉及密码/token 的地方，必须先接 `SecretProvider` 接口（哪怕 V1 版实现只是读环境变量），**不允许把明文密码写进 YAML 或直接传入 Trace**。
- 所有 Trace 写入前必须经过 `Redactor`，不允许"先落盘后脱敏"。
- LLM 调用必须经过 `LLMBudget`（Circuit Breaker），Phase 0 即使只跑一次 Demo 也要接上这个开关，防止之后忘记补。

---

## 1. Phase 0 的目标（8 项，逐项可验收）

| # | 目标 | 验收方式 |
|---|------|---------|
| 1 | Appium + XCUITest Driver + WDA + Simulator 打通 | 能通过 Python 对 Simulator 执行 launch/tap/input/swipe/screenshot/page_source |
| 2 | WDA Health Check + 自动重启 | 人为 kill WDA 进程后，系统能检测到并自动恢复，不需要重启整条 pipeline |
| 3 | App Session 级别的状态重置 | 支持 relaunch / terminate / reinstall，且与 WDA 生命周期解耦 |
| 4 | SwiftSyntax 解析一个真实页面 | 输出结构化的 `source_metadata.json`，包含 `resolution_type` 字段 |
| 5 | 局部 Source/Runtime Reconciliation | 对一次 locator 查找失败，只在当前 Screen 范围内做 diff，而不是全 App 扫描 |
| 6 | 完整 Trace | 一次 testcase 执行的每一步都有记录，密码等敏感字段已脱敏 |
| 7 | 一次真实的 LLM Recovery | 人为把 `login_button` 改名为 `signin_button`，系统能检测失败→触发 Recovery→LLM 建议→唯一性校验→执行成功 |
| 8 | LLM Budget / Circuit Breaker 生效 | 故意把 budget 设为 0，验证系统会 fail closed 而不是继续调用 LLM |

**Phase 0 完成的判定标准**：8 项全部跑通一次，并留下对应的 Trace 记录作为证据。不要求性能优化、不要求覆盖多个页面、不要求处理真机。

---

## 2. 环境搭建

### 2.1 前置软件

```bash
# macOS 上执行
brew install node
npm install -g appium
appium driver install xcuitest

# 验证安装
appium driver list --installed
```

需要：
- Xcode（含对应 iOS Simulator runtime）
- 一个可以正常编译、跑在 Simulator 上的 iOS 项目（先用你们现有 App 的 Debug/UITest Build，或先用官方 Demo App 打通流程也可以）

### 2.2 启动 Appium Server

```bash
appium --allow-cors
```

### 2.3 Python 依赖

```bash
pip install Appium-Python-Client sqlmodel pydantic anthropic swift-syntax-parser  # swift-syntax 部分见 4.6 说明
```

> `swift-syntax` 本身是 Swift 库，Python 侧通常通过一个小的 Swift 脚本调用 `swift-syntax` 生成 AST，再输出 JSON 给 Python 读取。Phase 0 只需要跑通"调用 → 拿到结构化输出"这条链路，具体见 4.6。

### 2.4 第一次连通性验证（编码智能体的第一个任务）

写一个 `phase0/smoke_test.py`，只做这几件事，作为整个项目的"Hello World"：

```python
from appium import webdriver
from appium.options.ios import XCUITestOptions

options = XCUITestOptions()
options.platform_name = "iOS"
options.automation_name = "XCUITest"
options.device_name = "iPhone 15"
options.platform_version = "18.0"
options.bundle_id = "com.yourcompany.app"  # 替换为实际 bundle id

driver = webdriver.Remote("http://127.0.0.1:4723", options=options)
driver.get_screenshot_as_file("smoke_test.png")
print(driver.page_source[:500])
driver.quit()
```

跑通这一步，代表目标 1 完成。

---

## 3. Phase 0 目录结构

只包含跑通闭环所必需的最小模块，字段命名与 V1 最终架构保持一致，方便后续直接扩展（不需要重命名）：

```text
mobile-test-agent/
├── phase0/
│   └── smoke_test.py
│
├── session/
│   ├── device_session.py      # WDA 生命周期 + health check
│   └── app_session.py         # App 生命周期（launch/terminate/reinstall）
│
├── executor/
│   ├── executor.py            # tap / input / swipe / screenshot / page_source
│   └── locator.py             # Locator Strategy Chain（Phase 0 只需 accessibility_id + predicate 两种）
│
├── source/
│   ├── swift_scan.swift       # 调用 SwiftSyntax，输出 AST 提取结果
│   ├── metadata.py            # 解析 swift_scan 输出，生成 source_metadata.json
│   └── reconciliation.py      # 局部 reconciliation
│
├── agent/
│   └── recovery.py            # Recovery Engine（Phase 0 只实现 LLM 分支）
│
├── llm/
│   ├── provider.py            # 封装 LLM API 调用
│   ├── budget.py              # Circuit Breaker
│   └── prompt.py              # Recovery Prompt 模板
│
├── trace/
│   ├── recorder.py
│   ├── redactor.py            # 脱敏，必须在 recorder 写入前调用
│   └── storage.py             # SQLite + 文件系统
│
├── testcase/
│   └── login_demo.yaml        # Phase 0 唯一的测试用例
│
└── run_phase0_demo.py         # 串联全部流程的入口脚本
```

---

## 4. 模块接口设计（编码智能体按此实现）

### 4.1 `session/device_session.py` —— WDA 生命周期 + Health Check

```python
class DeviceSession:
    """
    管理 Appium/WDA 连接本身的生命周期。
    不感知 App 内部状态（登录态、页面等）——那是 AppSession 的职责。
    """

    def __init__(self, appium_url: str, capabilities: dict):
        ...

    def connect(self) -> "WebDriver":
        """建立/复用一个 Appium session"""

    def health_check(self) -> bool:
        """
        向 WDA 发一个轻量请求（如 get window size）。
        超时或异常 -> 返回 False。
        """

    def restart_wda(self) -> None:
        """
        杀掉当前 WDA session，重新建立连接。
        必须记录一条 INFRA_FAILURE 类型的 trace 事件（见 4.6 Trace Schema）。
        """

    def ensure_alive(self) -> None:
        """
        Executor 在每个 action 之前调用。
        health_check() 失败 -> restart_wda() -> 再检查一次，仍失败则抛出 InfraError。
        """
```

**关键约束**：`ensure_alive()` 抛出的异常类型必须与"测试用例本身失败"的异常类型不同（比如 `InfraError` vs `TestAssertionError`），后续 Trace 和报告要能区分 `INFRA_FAILURE` 和 `TEST_FAILURE`。

### 4.2 `session/app_session.py` —— App 生命周期

```python
class AppSession:
    def __init__(self, device_session: DeviceSession, bundle_id: str):
        ...

    def launch(self) -> None: ...
    def terminate(self) -> None: ...
    def reinstall(self, ipa_path: str) -> None: ...

    def reset_state(self, strategy: str) -> None:
        """
        strategy 取值见 Reset Capability Matrix（4.2.1）。
        必须先查 capability matrix，不支持的策略直接抛出 UnsupportedResetError，
        不要静默降级成别的策略。
        """
```

#### 4.2.1 Reset Capability Matrix（Phase 0 版本，写死即可，先不做动态探测）

| Reset 策略 | Simulator | 真机 | Phase 0 是否实现 |
|---|---|---|---|
| `RELAUNCH` | ✅ | ✅ | ✅ |
| `TERMINATE` | ✅ | ✅ | ✅ |
| `REINSTALL` | ✅ | ✅ | ✅ |
| `LOGOUT`（走 App 内测试 Hook） | ✅ | ✅ | 预留接口，Demo 用例里用 REINSTALL 代替 |
| `SNAPSHOT` | ✅ | ❌ | 不实现 |

### 4.3 `executor/executor.py` + `executor/locator.py`

```python
class Locator:
    """
    Locator Strategy Chain。Phase 0 只需要两种策略。
    """
    strategies: list[dict]
    # 例: [{"type": "accessibility_id", "value": "login_button"},
    #      {"type": "predicate", "value": "label == '登录'"}]


class Executor:
    def __init__(self, driver, device_session: DeviceSession):
        ...

    def find(self, locator: Locator):
        """
        按 strategies 顺序尝试查找。
        找到 0 个 -> raise ElementNotFound
        找到 >=2 个 -> raise AmbiguousElement（唯一性校验，Phase 0 就要接上，见目标 7）
        找到 1 个 -> 返回该元素
        每次调用前先 device_session.ensure_alive()
        """

    def tap(self, locator: Locator): ...
    def input(self, locator: Locator, value: str): ...
    def swipe(self, direction: str): ...
    def screenshot(self, path: str): ...
    def page_source(self) -> str: ...
```

### 4.4 `testcase/login_demo.yaml` —— Phase 0 唯一的测试用例

```yaml
id: login_demo_001
name: 登录 Demo（Phase 0 验收用例）

precondition:
  reset: REINSTALL

steps:
  - action: launch_app

  - action: tap
    target: login_button

  - action: input
    target: username_field
    value: "${TEST_USERNAME}"

  - action: input
    target: password_field
    value: "${TEST_PASSWORD}"

  - action: tap
    target: login_button

  - wait_for:
      target: home_page
      condition: exists
      timeout: 10

  - assertion:
      type: exists
      target: home_page
```

变量通过 `SecretProvider` 解析，Phase 0 的实现：

```python
class SecretProvider:
    def get(self, key: str) -> str: ...

class EnvSecretProvider(SecretProvider):
    """Phase 0 版本：读环境变量。V1 后期再接 Vault/KMS。"""
    def get(self, key: str) -> str:
        import os
        val = os.environ.get(key)
        if val is None:
            raise KeyError(f"secret {key} not found")
        return val
```

### 4.5 `trace/` —— Trace 记录

#### 4.5.1 SQLite Schema（Phase 0 最小集）

```sql
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    test_case TEXT,
    app_version TEXT,
    app_build TEXT,
    source_commit TEXT,
    start_time TEXT,
    end_time TEXT,
    status TEXT  -- PASS / FAIL / INFRA_FAILURE
);

CREATE TABLE steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    step_index INTEGER,
    action_type TEXT,
    locator TEXT,          -- JSON, 已脱敏
    status TEXT,           -- SUCCESS / FAILED / RECOVERED
    error TEXT,
    latency_ms INTEGER,
    screenshot_path TEXT,
    ui_tree_path TEXT
);

CREATE TABLE recoveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    step_id INTEGER,
    strategy TEXT,          -- llm
    llm_target TEXT,
    confidence REAL,
    risk_level TEXT,
    latency_ms INTEGER,
    accepted BOOLEAN        -- 是否通过唯一性校验并执行
);

CREATE TABLE infra_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    event_type TEXT,        -- WDA_DEAD / WDA_RESTARTED
    timestamp TEXT
);
```

#### 4.5.2 `trace/redactor.py`

```python
class Redactor:
    """
    在任何数据写入 recorder 之前调用。
    Phase 0 规则：字段名命中以下列表 -> 替换为 "***REDACTED***"
    ["password", "token", "secret", "auth", "credential"]
    """
    SENSITIVE_KEYS = {"password", "token", "secret", "auth", "credential"}

    def redact(self, data: dict) -> dict: ...
```

**硬约束**：`Recorder.record_step(...)` 内部必须先调用 `Redactor.redact()`，再写 SQLite / 文件。不允许 Recorder 有任何"先写后脱敏"的路径。

### 4.6 `source/` —— Source Intelligence（Phase 0 只做一个页面）

Phase 0 不追求覆盖整个 App，只对 `LoginView.swift` 做一次解析，验证链路可行。

```text
LoginView.swift
      ↓
swift_scan.swift（调用 SwiftSyntax）
      ↓
raw_ast_extract.json
      ↓
metadata.py 解析
      ↓
source_metadata.json
```

`source_metadata.json` 格式（对应第 3 轮 review 中确定的字段）：

```json
{
  "app_version": "debug",
  "build": "local",
  "git_commit": "<当前 commit hash>",
  "generated_at": "2026-09-24T10:00:00Z",
  "parser_version": "0.1.0",
  "screen": "LoginView",
  "elements": [
    {
      "id": "login_button",
      "type": "button",
      "label": "登录",
      "accessibility_id": "login_button",
      "resolution_type": "literal",
      "source": {"file": "LoginView.swift", "line": 25}
    },
    {
      "id": "username_field",
      "type": "textfield",
      "accessibility_id": "username_field",
      "resolution_type": "literal",
      "source": {"file": "LoginView.swift", "line": 18}
    }
  ]
}
```

`resolution_type` 只允许这几个值：`literal` / `dynamic` / `unknown`。**无法从 AST 静态求值的 identifier，必须标 `unknown`，不允许硬猜一个值填进去。**

Phase 0 的 `swift_scan.swift` 只需要能识别这一种模式：

```swift
SomeView(...)
    .accessibilityIdentifier("literal_string")
```

遇到非字面量参数（变量、表达式），直接输出 `resolution_type: "unknown"`，不尝试 resolve。这是 Phase 0 明确的范围边界，不要在这里扩大工作量。

### 4.7 `source/reconciliation.py` —— 局部 Reconciliation

```python
def reconcile_local(expected_element_id: str,
                     screen: str,
                     source_metadata: dict,
                     runtime_page_source: str) -> dict:
    """
    只在 Locator 查找失败时调用，只对比 `screen` 对应的 source_metadata 子集，
    不做全 App 扫描（这是 review 中强调的性能红线）。

    返回：
    {
      "status": "DRIFT" | "MATCH" | "UNKNOWN",
      "expected": "login_button",
      "candidates_in_runtime": [...]   # 从 runtime_page_source 里提取出的同类型元素
    }
    """
```

Phase 0 的用法：这个结果会作为 Prompt 的一部分传给 LLM（见 4.8），不是用来自动做判断的最终结论。

### 4.8 `llm/` + `agent/recovery.py` —— LLM Recovery

#### 4.8.1 `llm/budget.py`

```python
class LLMBudget:
    def __init__(self, max_calls_per_run: int = 5):
        self.max_calls_per_run = max_calls_per_run
        self._used = 0

    def try_acquire(self) -> bool:
        if self._used >= self.max_calls_per_run:
            return False
        self._used += 1
        return True
```

Recovery Engine 调用 LLM 前必须先 `budget.try_acquire()`，返回 False 时记录 `LLM_BUDGET_EXCEEDED` 并直接判该 step 失败，不再尝试。

#### 4.8.2 Recovery Prompt 输出的结构化格式（LLM 必须只返回这个 JSON）

```json
{
  "action": "tap",
  "target": {
    "type": "accessibility_id",
    "value": "signin_button"
  },
  "scope": "LoginView",
  "reason": "源码 login_button 未在当前页面找到，但发现语义等价元素 signin_button",
  "confidence": 0.93,
  "risk_level": "LOW"
}
```

#### 4.8.3 `agent/recovery.py`

```python
def recover(step, error, executor, source_metadata, budget, llm_provider) -> dict:
    """
    1. budget.try_acquire()，失败 -> 返回 {"status": "LLM_BUDGET_EXCEEDED"}
    2. 组装 prompt（当前 step 目标、error、runtime page_source、
       source_metadata 中该 screen 的元素列表、reconcile_local 结果）
    3. 调用 llm_provider，解析结构化输出
       解析失败 -> 返回 {"status": "LLM_INVALID_OUTPUT"}
    4. risk_level 检查：Phase 0 只允许 LOW 自动执行，其余一律 fail closed
    5. Locator 唯一性校验：executor.find(candidate_locator)
       0 个 -> {"status": "LLM_TARGET_NOT_FOUND"}
       >=2 个 -> {"status": "LLM_TARGET_AMBIGUOUS"}
       1 个 -> 执行 tap，返回 {"status": "RECOVERED", ...}
    6. 无论结果如何，都要写一条 recoveries 表记录
    """
```

---

## 5. Phase 0 端到端验收 Demo（明确的操作步骤）

这是 8 个目标的联合验收，按顺序执行：

1. 启动 Appium Server + Simulator，跑 `smoke_test.py`（验收目标 1）。
2. 手动 `kill` 掉 WDA 进程（`xcrun simctl` 或直接找到进程 kill），运行任意 Executor 调用，观察 `DeviceSession.ensure_alive()` 检测到异常并自动重启（验收目标 2），并检查 `infra_events` 表里有一条 `WDA_DEAD` + 一条 `WDA_RESTARTED`。
3. 依次调用 `AppSession.reinstall()` / `relaunch()`，确认 WDA session 不需要重新建立（验收目标 3）。
4. 对 `LoginView.swift` 跑 `swift_scan.swift` → `metadata.py`，检查生成的 `source_metadata.json` 里 `login_button` 的 `resolution_type` 是 `literal`（验收目标 4）。
5. 正常运行 `login_demo.yaml`，确认全流程 PASS，检查 `runs` / `steps` 两张表都有完整记录，密码字段是 `***REDACTED***`（验收目标 6，部分验收目标 3）。
6. **故意修改 App 源码**：把 `.accessibilityIdentifier("login_button")` 改成 `.accessibilityIdentifier("signin_button")`，重新编译跑到 Simulator 上，**不重新生成 source_metadata.json**（模拟"源码知道旧的，运行时是新的"这个典型场景）。
7. 再次运行 `login_demo.yaml`：
   - 第一次 `tap(login_button)` 应该 `ElementNotFound`；
   - 触发 `reconcile_local()`，结果应为 `DRIFT`；
   - 触发 `agent.recovery.recover()`，LLM 应返回 `signin_button`，`risk_level = LOW`；
   - Executor 对 `signin_button` 做唯一性校验通过，执行 tap；
   - 该 testcase 最终状态为 `PASS`，且 `steps` 表里这一步的 `status` 是 `RECOVERED`；
   - `recoveries` 表里有对应记录，`accepted = true`（验收目标 7）。
8. 把 `LLMBudget(max_calls_per_run=0)` 传入同一个流程，重复步骤 7，确认这次直接以 `LLM_BUDGET_EXCEEDED` 结束，且**没有**真的发起 LLM API 调用（验收目标 8）。

全部 8 步跑完并留下对应 Trace 记录截图/导出，Phase 0 结束。

---

## 6. 明确排除在 Phase 0 之外的内容

避免范围蔓延，以下内容**不要**在 Phase 0 做：

- Flaky Detection / Candidate → Verified 流程（这是 V1 中后期）
- 全量 Build Reconciliation（这是独立的异步任务，Phase 0 只做失败时的局部对比）
- Experience Store / Recovery Cache（V2）
- 多设备并行 / Device Scheduler
- 真机相关的所有特殊处理（Keychain 清理、真机 WDA 部署等，先在 Simulator 上把逻辑跑通）
- Non-idempotent Action 的 postcondition 检查（这是重要的 V1 特性，但 Demo 用例里没有提交订单这类操作，可以留到 Phase 1）

---

## 7. 建议的执行顺序（给编码智能体的任务队列）

```text
1. smoke_test.py 跑通
2. DeviceSession + health check + restart
3. AppSession（launch/terminate/reinstall）
4. Executor + Locator（先只实现 accessibility_id 一种策略即可跑通登录用例）
5. testcase/login_demo.yaml + 一个最小的 YAML loader
6. trace/（SQLite schema + Recorder + Redactor）—— 先把正常 PASS 路径的 Trace 打通
7. source/swift_scan.swift + metadata.py —— 单页面解析
8. source/reconciliation.py（local 版本）
9. llm/provider.py + budget.py + prompt.py
10. agent/recovery.py —— 把 6/7/8/9 串起来
11. 按第 5 节步骤跑一遍完整 Demo，修 bug
12. 把 LLMBudget=0 的场景也验证一遍
```

每完成一步，建议直接跑一次对应的最小验证（哪怕只是打印结果），不要攒到最后一起联调。
