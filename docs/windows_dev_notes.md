# Windows 开发环境说明（非工程产物，供跨 OS 开发记录）

> 用途：本仓的历史开发与评审在 macOS 上进行（`Makefile`、`xcodebuild`、
> `xcrun simctl`、Appium 真机路径等均假定 macOS）。本文件记录**在 Windows 上
> 开发时**必须知道的环境差异，以及**只能在 macOS 上跑的测试**——后者在此登记，
> 由维护者在 macOS 上补跑，不在 Windows 上强改。
>
> 更新于 2026-10-09（P3 Task 3.3 开工前）。

## 1. 环境搭建（Windows）

仓库**不自带 `.venv`**，且系统 Python 可能已损坏（实测本机 `C:\Python313`：
`pydantic_core` 缺编译产物、`_pytest` 被污染、无 `sqlmodel`）。步骤：

```bash
python -m venv .venv                     # .venv 已在 .gitignore
.venv/Scripts/python.exe -m pip install -U pip
.venv/Scripts/python.exe -m pip install \
    "pydantic>=2.0" "PyYAML>=6.0" "pytest>=8.0" "Appium-Python-Client>=4.0"
```

### ⚠️ 必须带 `PYTHONUTF8=1`

中文 Windows 的默认 locale 编码是 **GBK（cp936）**，而仓内多处 `Path.read_text()`
/ `open()` **未显式传 `encoding="utf-8"`**（如 `tracer/storage.py:200` 读
`001_trace_schema_0_1.sql`）。不带该变量时，任何读含中文的 UTF-8 文件都会
`UnicodeDecodeError: 'gbk' codec can't decode ...`，表现为**大批量假失败**。

Python 3.7+ 的 UTF-8 模式（`PYTHONUTF8=1` 或 `-X utf8`）把默认编码改成 UTF-8，
**不改任何代码即可绕过**：

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests -q
```

> 这是一处**代码侧的可移植性缺口**（`read_text()`/`open()` 应显式 `encoding="utf-8"`），
> 不是测试问题。彻底修需给这些调用点补 encoding（属独立立项），本文件只做记录。

## 2. Windows 上跑不了的测试（登记 → 由维护者在 macOS 上跑）

基线（HEAD `0ede83e`，`PYTHONUTF8=1`）：**1662 collected / 1656 passed / 6 failed**。
6 个失败**全部是测试自身假定 POSIX**，与业务代码无关，**不在 Windows 上修改**：

> **更新（2026-10-09，Task 3.3 后）**：**1685 collected / 1679 passed / 6 failed**
> （+23 例来自 Task 3.3）。上表 6 条不变。


| # | 测试 | 根因 |
|---|---|---|
| 1 | `tests/unit/test_cli_coverage.py::test_defaults_are_repo_layout` | 断言 POSIX 路径 `repository/generated/local/...`，Windows 打印 `\` |
| 2 | `tests/unit/test_git_diff.py::test_error_message_carries_the_repo_and_range` | 同上（`str(WindowsPath)` 用反斜杠） |
| 3 | `tests/unit/test_git_diff.py::test_filenames_with_tabs_and_newlines_parse_correctly` | Windows 文件名不允许含 `\t`（`OSError: [Errno 22]`）——**该测试本身 POSIX-only** |
| 4 | `tests/unit/test_knowledge_retrieval.py::test_p2_knowledge_sources_is_constructed_in_one_place` | F3「单一入口」守卫扫描结果用了 `\` 分隔符 |
| 5 | `tests/unit/test_tool_allowlist_gate.py::test_banned_names_appear_exactly_once_in_the_agents_package` | **F2 CI 门禁**守卫，同上路径分隔符 |
| 6 | `tests/unit/test_trace_schema.py::test_duration_ms_uses_utc_not_local_timezone` | `time.tzset()` 在 Windows 不存在 |

**Windows 上跑「兼容子集」的命令**（deselect 上述 6 条）：

```bash
PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests -q \
  --deselect tests/unit/test_cli_coverage.py::test_defaults_are_repo_layout \
  --deselect tests/unit/test_git_diff.py::test_error_message_carries_the_repo_and_range \
  --deselect tests/unit/test_git_diff.py::test_filenames_with_tabs_and_newlines_parse_correctly \
  --deselect tests/unit/test_knowledge_retrieval.py::test_p2_knowledge_sources_is_constructed_in_one_place \
  --deselect tests/unit/test_tool_allowlist_gate.py::test_banned_names_appear_exactly_once_in_the_agents_package \
  --deselect tests/unit/test_trace_schema.py::test_duration_ms_uses_utc_not_local_timezone
```

**macOS 上补跑**（这 6 条在 macOS 上应全绿，同时跑全量确认无回归）：

```bash
.venv/bin/python -m pytest tests -q          # 全量；应 1662 passed
```

## 3. 与设备/Swift 相关的目标（本就不在 Windows 范围）

以下任务在 Windows 上**无法执行**，需 macOS：

- `make scan` / `make scan-test`（SwiftPM `swift-syntax` 扫描器）
- `make p1-build` / `make p1-install`（`xcodebuild` + `simctl`）
- 所有 `phase0/verify_p1_*.py` / `verify_p2_*.py` / `verify_p3_m*.py` 真机/模拟器验收脚本
- Appium + XCUITest Driver 相关链路

纯逻辑任务（F13 纯函数，如 P3 Task 3.3 Test Fingerprint）不受影响，可在 Windows 上开发 + 跑单测。
