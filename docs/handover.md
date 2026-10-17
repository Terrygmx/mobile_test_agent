# 交接阅读地图 — Mobile Test Agent

> **这份文件是「读什么」的地图，不是知识库。** 规则、纪律、踩过的坑的**正文**在
> `.workbuddy-ai/memory/MEMORY.md`（会自动注入到会话上下文）；本文件只回答
> 「除了 `docs/` 之外，还要读哪些、为什么、按什么顺序」。
>
> 生成于 2026-10-09（P3 M2 收口时）；**快照刷新于 2026-10-10**（P3 M3 进行中）。
> **状态快照见 §9** —— 若快照与实测不符，以 `git log` / 全量 pytest 的实测为准。

## 0. 优先级图例与「先读什么」

| 记号 | 含义 |
|---|---|
| **P0** | 开工前必读。不读会直接做错（违反纪律、踩已知的坑、重复报已修的缺口） |
| **P1** | 动手前读。影响你写出来的东西对不对 |
| **P2** | 需要时查。查历史决策、查某个细节的出处 |

**最小起手式（三件套，约 20 分钟）**：

1. `.workbuddy-ai/memory/MEMORY.md` —— 7 节：环境约定 / 真机 gate / 工作流 / 硬纪律 /
   通用教训 / 本项目特有的坑 / 进度与现状。**读完这一份，交接完成约 80%。**
2. `.workbuddy-ai/skills/mta-p2-task-workflow/SKILL.md` —— 任务循环的**正式定义**
   （8 步）。⚠️ 标题写着 P2、部分内容已过期，见 §8。
3. `.workbuddy-ai/memory/2026-10-09.md` —— 最近一天的流水（当前进度的一手记录；
   **2026-10-10 的工作尚未单独成篇**，那天的一手记录在 `docs/p3_data_audit.md` 的
   「Task 3.2 评审修订记录（round 2）」段 + `git log`）。

---

## 1. 项目背景（P1）

| 文件 | 作用 |
|---|---|
| `pyproject.toml` | 一句话定位：*Source-aware intelligent regression executor for iOS apps*。依赖面（Appium-Python-Client / pydantic / PyYAML），`requires-python >= 3.11` |
| `run_phase0_demo.py` | **唯一一份「讲完整故事」的可执行 demo**（元素漂移 → RecoveryEngine 恢复 → RECOVERED）。已迁到当前唯一管线（`SessionPipeline` + `TraceStore`），旧 P0 执行链已退役 |
| `ios_demo/`（`LoginDemo.xcodeproj` + Swift 源） | **被测 App 本体** —— 背景的另一半，不看它无法理解 metadata 与「漂移」从哪来 |
| `docs/windows_dev_notes.md` | **非工程产物**：Windows 侧开发必须知道的环境差异（`PYTHONUTF8=1`）与 **6 条 POSIX-only 测试的登记**。在 macOS 上开发可跳过；跨 OS 接手先读它 |

**这一类的用途**：让接手者知道这是「源码感知的 iOS 回归**选择** + 恢复**执行**平台」，
而不是一堆测试脚本的集合。**判断标准**：读完能说清「一次 `mta run` 从 git 到报告的
数据流有哪几段」。

## 2. 架构设计（P0）

**顶层包清单就是架构**（`ls -d */`）：

```text
source/      源码扫描 → 12.3 metadata / git diff / build identity
tracer/      append-only 流水库（runs / testcase_runs / steps）+ 脱敏 + 报告
graph/       运行时图 / 源图 / 图 diff / 影响面
experience/  经验库（候选→验证→晋升）+ 恢复引擎底座 + knowledge（KnowledgeSources）
executor/ runner/ session/ environment/  设备侧：Guard / 步骤执行 / Appium 会话 / 环境
agent/       P1/P2 的 RecoveryEngine
llm/         Provider + 严格 parser + 预算/熔断 + prompt 模板
repository/  overrides + generated 双源合并、元素/屏解析（4.1）
testcase/    用例 schema + lint
report/      报告与 Experience 指标（设计 §17）
cli/         全部子命令（`main.py` ~1900 行，是接线事实的来源）
agents/ planner/ knowledge/    ★ P3 新增：Agent 数据模型与工具层 / Test Planner / 知识装配
generator/ candidates/         ★ P3 M3 新增：Coverage Gap / 生成管线（generator/）
                               与 Candidate 指纹 / 库表 / 审核（candidates/）
```

**架构的权威是「可执行的守卫」，不是任何图** —— 改架构会先撞这些测试：

| 守卫 | 钉住的不变量 |
|---|---|
| `tests/unit/test_knowledge_retrieval.py`（3 条） | `agents/**` 不 import `planner`；`P2KnowledgeSources(` 构造点唯一；`planner/**` 不直连数据源（F3） |
| `tests/unit/test_tool_allowlist_gate.py` | 工具全集 / 注册表 / 禁用名 / tier 值域 / `GUARDED_TOOLS` 与设计逐字对账（F2、F12） |
| `tests/unit/test_repo_hygiene.py` | 重复顶层定义、`def test_` 数与收集数对账、三处 store 的写必须在 `with self._write_tx()` 内 |
| `tests/fault_injection/test_p3_matrix_m1.py` | 自主命令必须过 F7 前置，且 `env_kind` 不许是字面量 |

**这一类怎么验证**：`pytest tests/unit/test_knowledge_retrieval.py tests/unit/test_repo_hygiene.py -q` 全绿。

⚠️ **一条只有踩过才知道的坑**（2026-10-10 实测）：上面这些守卫读的是**盘上的源码**，
所以**别在只读评审子代理跑 mutant 的同时并发跑全量** —— mutant 会临时改实现文件，
并发的那次全量读到被改的实现，表现成「随机 flaky」（同一测试时红时绿、单跑必绿）。
串行跑，或先确认没有在飞的评审。

## 3. 接口与数据规范（P0 / P1）

| 文件 | 权威内容 |
|---|---|
| `testcase/schema.py` | 用例 schema（现行 **`"0.2"`**）：`extra="forbid"`、`SCHEMA_VERSION`、`TargetRef` 字符串语法糖、`WaitSpec`/`AssertionSpec` 条件矩阵。⚠️ **Candidate 扩展方式是 Modify 既有模型**（`status` / `generated_by` / `generation_evidence`，Task 3.1），不是平行模型 |
| `source/hashing.py` | ★ M3：`stable_hash(parts)` —— **共享哈希原语**（拼接 → sha256 前 16 hex）。`source/screen.py::screen_fingerprint` 与 `candidates/fingerprint.py::test_fingerprint` 共用它（F8）；**只做精确哈希相等，禁相似度** |
| `agents/migrations/001_agent_schema.sql` | `agent_tasks / agent_trace / test_plans` 三表；⚠️ 里面 `CHECK (state IN …)` 的词表是 Python 枚举的**第二份实现**，有对账测试 |
| `tracer/migrations/*.sql` | trace 库表结构（`steps` 表**没有时间戳**——只能取所属 `testcase_run` 的 `end_time`） |
| `repository/loader.py` · `resolver.py` | 元素/屏定义与解析契约。**两种解析产物**：`EffectiveElement`（有 `.screen`/`.risk`）与 `EffectiveScreen`（**没有**），判别式 `hasattr(eff, "marker")` |
| `config/policy.yaml` | 配置规范。⚠️ 必须与代码内置默认值**逐字段相等**（有测试；校准时两处一起改） |
| `repository/generated/local/source_metadata.json` | 12.3 metadata 的**真实形状**。`screen_elements[].elements[].source.file` 是 P3 影响面（改动文件 → 元素 → 用例）的唯一桥梁 |
| `suites/**/*.yaml` | 4 个 suite × 5 例 = **20 个真实用例**，用例规范的活样例 |
| `phase0/verify_p3_m2.py` 的 `_metadata()` / `_element_yaml()` / `_screen_yaml()` | **现成的「最小合法 metadata / element / screen」构造器** —— 比翻文档快得多，写新夹具时直接抄 |

**这一类的用途**：接口的权威在**代码**（schema 类、SQL、Protocol），设计文档可能滞后。
**判断标准**：写夹具时不猜字段名，而是抄上面那三个构造器。

## 4. 开发与部署流程（P1）

| 文件 | 作用 |
|---|---|
| `Makefile` | 6 个目标：`test` / `scan`（SwiftPM 扫描器）/ `scan-test` / `repo-generate`（12.3 + 一致性 Gate）/ `p1-build` / `p1-install`。**注释里带实测坑**：swift-syntax 必须与 Xcode 工具链同代、`INFOPLIST_KEY_*` 不进产物（改用 PlistBuddy 注入）、`-derivedDataPath` 与 `-target` 互斥 |
| `pytest.ini` + `conftest.py` | 测试运行约定：`addopts = -q`（**别再加 `-q`**，`-qq` 会吞汇总行）、`filterwarnings = always`（去 flake）、根目录注入 `sys.path` |
| `.venv/bin/python` | **必须用它** —— 系统 python 没有 appium 依赖 |
| `scripts/` | `p1_stability_run.sh`（稳定性跑批）、`p2_seed_recoveries.sh`（种子数据）、两个报告脚本 |
| `phase0/`（24 个 `.py`，其中 `verify_*.py` 22 个） | 真机/Gate 脚本 —— 见 §5（测试与验收） |

⚠️ **本项目没有 CI 配置**（无 `.github/`、无 `.gitlab-ci.yml`）。所谓「进 CI 门禁」目前等于
「一条会在全量 pytest 里跑的测试」。**别以为有流水线在跑。**

**这一类怎么验证**：`make test` 能跑通（等价于全量 pytest）。

## 5. 测试与验收标准（P0）

**验收标准 = 可复现的命令 + 期望数字**，不是文档里的形容词。

| 层 | 位置 | 说明 |
|---|---|---|
| 单元 / 集成 / 故障注入 | `tests/`（**90 个 `.py`**：`unit/` 77、`fault_injection/` 9、`integration/` 3，另 `tests/__init__.py`） | 判据的主要载体 |
| P3 故障注入矩阵 | `tests/fault_injection/test_p3_matrix_m1.py` 等 | 矩阵 #1–#18 分散在各 milestone 的 `test_*_matrix_*.py` |
| 真机 / Gate | `phase0/verify_*.py`（**22 个脚本**；目录内共 24 个 `.py`） | 当前收口脚本：**`phase0/verify_p3_m2.py`**（自足工程，exit 0/1 + `out/p3_m2_gate/summary.json`）；P2 的是 `verify_p2_final.py`。⚠️ **M3 的 Gate 脚本尚未写**（M3 收口时才出） |

**开工前先跑一遍基线**（照抄即可）：

```bash
cd /Users/terrygmx/PycharmProjects/Mobile_test_agent
rm -rf out/_ho_pt && env PYTHONPATH=. CODEBUDDY_BROKERED_FS_HOOK_ENABLED=0 \
  CODEBUDDY_SAFE_DELETE_SANDBOX=0 .venv/bin/python -m pytest tests \
  -p no:cacheprovider --basetemp=./out/_ho_pt
rm -rf out/_ho_pt
# 期望：1691 passed
```

```bash
env PYTHONPATH=. CODEBUDDY_BROKERED_FS_HOOK_ENABLED=0 CODEBUDDY_SAFE_DELETE_SANDBOX=0 \
  no_proxy=127.0.0.1,localhost .venv/bin/python phase0/verify_p3_m2.py
# 期望：exit 0，8/8 PASS
```

三条**必须先知道**的坑（细节在 `MEMORY.md` §1）：

1. `--basetemp` **必须**指工作区内，否则受限环境拦系统临时目录 → 200+ 个
   `PermissionError: EEXIST … pytest-of-unknown` **假失败**（不是真回归）；
2. **「6 个固定假失败」的旧说法已过时**：`test_p0_recorder_compat.py`（在
   `tests/integration/`，不在 `unit/`）+ `test_implicit_wait.py` 在 2026-10-10 实测
   **11 passed**（两个 env 加不加都一样绿）。当前基线是 **0 failed**；若真出现红，
   先按「真回归」查；
3. 真机 gate **必须禁用沙箱**跑，否则 SwiftUI 宏报
   `could not be found for macro 'State()'`（**看起来像代码坏了**）；
4. ⚠️ **别在评审子代理正在跑 mutant 时并发跑全量**（2026-10-10 实测）：mutant 会临时
   改盘上的 `candidates/fingerprint.py` / `source/hashing.py`，并发的那次全量会读到被改
   的实现 → 表现成**随机 flaky**（同一测试时红时绿、单跑必绿）。**串行跑**，或先确认
   没有在飞的只读评审。

## 6. 变更记录（P1）

| 来源 | 说明 |
|---|---|
| `git log`（**146 提交**） | 提交信息写得很长：含**偏离登记**与**实测数字**。「为什么是这样」的第一手答案 |
| `git tag`（**49 个**） | 里程碑权威：`checkpoint-p1-*`、`checkpoint-p2-m1..m5`、`checkpoint-p3-m1..m2`、`v0.1-p1-complete`、`v0.2-p2-complete`。当前分支 **`P3-1010`**（按日期命名，不是 `main`）。⚠️ **`checkpoint-p3-m3` 尚未打**（M3 收口时才打） |
| `review/`（**57 份**） | 每任务两段 review（spec 合规 → 代码质量）+ 修订轮。命名 `review_pN_taskNN_YYYY-MM-DD.md`；`skipped_and_reasons_round2.md` 记「为什么跳过某检查」 |
| `docs/*_data_audit.md` | 每任务一段「完成记录」+ 每轮评审一段「修订记录」——决策与偏离的流水（在 `docs/`，此处只作索引） |
| `docs/p2_data_audit.json` | P2 审计的**机读孪生**（`summary` / `seedable_accepts` / `screen_target_pairs`） |

**这一类的用途**：接手者最容易重复劳动的两种情形是「报一个已修的缺口」和
「重做一个已否掉的方案」——两者都靠 review/ + 审计文档避免。

## 7. 协作约定（P0）

| 文件 | 约定 |
|---|---|
| `.workbuddy-ai/skills/mta-p2-task-workflow/SKILL.md` | **任务循环 8 步**：读 plan（含「⚠️ 接线前置」段）→ 读现有实现 → TDD → 全量回归 → **追加审计记录** → commit + tag → **独立两段 review（用子代理，要求探针实测）** → 修订后追加「评审修订记录」再提交 |
| `MEMORY.md` §3 / §4 / §5 | 工作流约定 / 硬纪律（E1–E13、F7、单点清单）/ 通用教训 |
| `docs/phase3-plan.md`「执行注意事项」段 | 复用优先于实现 / F2·F12 红线 / 回归底线 / open questions（**遇到即停下确认，不猜**） |
| `.zcodeignore` | 长期未跟踪物，**已定案保留原样**：别动、别 `git add`、别提议处理 |

**四条最容易被打破的纪律**（每条都有 review 抓过的实例）：

1. **同一概念只许一处实现** —— 发现「同一条纪律在第二个地方又写了一遍」时，第二遍就是
   抽单点的信号；
2. **提交信息里的数字必须在 HEAD 上照抄命令复现**（引用父提交上的数要写明「父提交」）；
3. **守卫/判据要能区分「名字变了」和「名字数变了」**（名单类不变量必须逐字对账原文）；
4. **「固化」= 一条会自动失败的测试**，不是「我手工跑过一次」。

## 8. ⚠️ 文档与实测不一致的七处（交接必须点明）

| # | 说法 | 实测 |
|---|---|---|
| 1 | ~~`SKILL.md` 自称 **P2**（引用 `docs/phase2-plan.md`、`925+ passed`）~~ | **2026-10-11 已修**（`.workbuddy-ai/skills/mta-p2-task-workflow/SKILL.md`）：plan 路径 → `phase3-plan.md`、基线 → **2219**、`feat(p3):` / `checkpoint-p3-mN` / `verify_p3_m*.py` / `p3_data_audit.md`，并补了 7 条 P3 实锤坑。⚠️ 该目录**不入库**（`git ls-files .workbuddy-ai` = 0），改动只在本机生效；**目录名仍叫 p2**（改名会动 skills 结构，已在正文首行标注） |
| 2 | `docs/phase3-plan_by_claude.md` 看起来像计划 | 它是**底稿**。权威是 `phase3-design.md` + `phase3-plan.md` |
| 3 | 两批记忆目录 | `.workbuddy/memory/`（旧，9 月）vs `.workbuddy-ai/memory/`（现行）。**只有后者会自动注入** |
| 4 | 旧文档写「`AgentToolkit` 装配点归 Task 2.4」 | **已被实现证伪**：`mta plan` 离线、不碰 App，装配它是死对象。真正的装配点是**第一个会驱动设备的命令**（M6 自主闭环 / **M3 Dry Run**） |
| 5 | `docs/handover.md` §9 自己写的快照（曾停在 `b4810f7` / 1609 / 「下一步 Task 3.1」） | **本文件已刷新到 2026-10-11**（含 Task 6.7）；再过期时以 `git log` + 全量 pytest 为准（这是本文件的固有风险：**它不会自动更新**） |
| 6 | `candidates/fingerprint.py` / `docs/p3_data_audit.md` 称「**每个步骤恰好落入一个分量**」 | **实测为假**：带 `postcondition` 的 `ActionStep` **同时落入 `screens` + `actions` 两个分量**（真语料 2 处）——见 `review/review_p3_task33_2026-10-10.md` P3-2 |
| 7 | `MEMORY.md` §1 记的「**6 个固定假失败**」+ 基线停在「3.5 round 2 修订后 1866」 | **均已过时**：2026-10-10 实测那两条文件 **11 passed**（当前基线 0 failed）；基线已到 **2219**。⚠️ `.workbuddy-ai/memory/MEMORY.md` **不入库**（该目录 0 个 tracked 文件），只能人工改——本次未改，**下个会话若读到「6 个固定假失败」或「1866」即知是它** |

## 9. 当前状态快照（**2026-10-11**，以实测为准）

- **分支 / HEAD**：`P4-1011` / **`2b621db`**（**M1–M6 全部收口**，其上又叠了 Task 6.7
  「工具层接线补齐」、Batch B「设备侧证据采集 + `APP_CRASH` 判定」、Batch B 修正
  （§10 崩溃判定覆盖所有失败出口）、以及 §11「步骤行兜底 + 扩词表」；
  里程碑 tag `checkpoint-p3-m6` = `f63b381` **不可动**）
- **全量基线**：**2320 passed / 0 failed**（M6 收口 2207 → Task 6.7 的 2219 →
  Batch B 的 2261 → Batch B 修正的 2311 → §11 收口的 2320）
- ⚠️ **Batch B 的教训（P3 收口后仍抓到真缺陷）**：§9 曾声称「崩溃链在真机上是通的」，
  那是**函数级**实测；`mta run` 真机端到端实测当场推翻——App 崩了记的是
  `CURRENT_SCREEN_UNKNOWN`（崩溃判定只挂在 `except InfraError` 上，而 App 死了
  最常见的表现是普通步骤失败）。**函数级 E2E ≠ 端到端 E2E**，详见
  `docs/p3_data_audit.md` §10。修正后真机复验：`FAIL (APP_CRASH)`，
  用例行与**步骤**行同步改判（规则引擎读的是步骤行），诊断链闭合。
- ⚠️ **紧接着又清掉两处既存缺口**（`docs/p3_data_audit.md` §11，用户拍板「清掉 3、4」）：
  ① **断言值不符的出口一行步骤都不落**（外层 `except Exception` 唯一漏掉的出口，
  而它是最常见的断言失败形态）→ `_record_failed_step_once` 兜底 + 按
  `(tc_run_id, step_index)` **查重**（内部已落行的分支不能被补写第二行）；
  ② **`ACTION_FAILED` 不在 `FAILURE_TYPES`** → 用户拍板**扩词表**而非归并，
  设计 §8.2 补「修订记录」。守卫还**抓出第二个漏网值** `PREPARE_FAILED`
  （不在登记清单里——靠人肉清点本来就会漏）。新增守卫
  `tests/unit/test_trace_completeness.py` 把「**故意生产者 ⊆ 词表**」钉住。
  ⚠️ **A/B 探针的 T3 第一版是「假红」**：变异留下悬空 `else` → `SyntaxError`，
  pytest 报的是**收集错误**而不是断言失败——那条「红」什么也没证明。**判定探针
  是否有效，要看它是断言失败还是收集/导入错误。**
- **最新 tag**：`checkpoint-p3-m6` = `f63b381`（**P3 收口**；里程碑 tag `v0.3-p3-complete`；
  上一个 `checkpoint-p3-m5` = `233ab1e`）
- **Gate 状态**：M1 ✅（`checkpoint-p3-m1`）· M2 ✅（`phase0/verify_p3_m2.py` exit 0，8/8 PASS）
  · **M3 ✅**（`phase0/verify_p3_m3.py` exit 0，**11/11 PASS**，`out/p3_m3_gate/summary.json`
  verdict=PASS；G8 内含全量 1866 passed）
  · **M4 ✅**（`phase0/verify_p3_m4.py` exit 0，**device_half=PASS**，
  `out/p3_m4_gate/summary.json` verdict=PASS；G7 内含全量 **2108 passed**。
  G1 三种启动期拒绝 / G2 预算 / G3 discovery 不写图 / G4 HIGH 拦截 /
  G5 LOOP_DETECTED / G6 卫生 全 PASS）
  · **M5 ✅**（`phase0/verify_p3_m5.py` exit 0，**device_half=PASS**，
  `out/p3_m5_gate/summary.json` verdict=PASS；G10 内含全量 **2148 passed**。
  漂移法真机路径：规则命中 → 复现 3 次 → **11 分过阈值 10** →
  `AUTOMATION_DEFECT` → 人工 accept → `CONFIRMED` + Evidence Bundle）
- **已完成**：P0 / P1 / P2 全部；P3 的 **M1、M2、M3 全部**（3.1–3.5）
  - 3.1 Candidate 字段（`72106e1`，评审两轮已清）
  - 3.2 Coverage Gap（`43555e9` + 修订 `0ede83e`）——本体收口，**round 2 核销 P2-1'
    （`31c0fdc`）**：`pending` 只被「到达点」消费（元素 wait / 否定屏 wait 都不消费它）
  - 3.3 Test Fingerprint（`1ede89f`）：`source/hashing.py::stable_hash` 共享原语；
    review（`review/review_p3_task33_2026-10-10.md`）**通过 + 3×P3**，**3×P3 已修订
    （`8456e14`）**：wait 分支补钉子 + `precondition` 不入指纹的口径登记（含一条
    **故意钉住已知碰撞**的测试）
  - 3.4 生成管线（`feff5ec`）：迁移 002（`test_candidates` + `test_candidate_rejections`）
    + `candidates/test_case.py` + `generator/{validator,dry_run,generator}.py`
    + `llm/{prompt,parser}.py` 的生成面 + `cli/pipeline.py::wire_device_stack` 抽取。
    review round 1（`review/review_p3_task34_2026-10-10.md`）**不通过
    （1×P0 + 2×P1 + 4×P2 + 4×P3）**，**全部已修订**；**round 2 复核**
    （`review/review_p3_task34_final_2026-10-10.md`）**有条件通过**：11 条中 10 条
    真核销，发现 round 1 的 `recovery.guard` 那条**并未真核销**（同族缺陷，已补
    真钉子）+ 迁移 002 原地编辑（已抽 `003_dry_run_detail.sql`）——**均已修订**
  - **3.5 统一审核 + `mta generate`（P3-13，M3 收口任务）**
    - 实现 `2c6fb73`：`agents/migrations/004_review_note.sql` + `candidates/review.py`
      + `cli/main.py::cmd_generate`/`cmd_candidate` + `phase0/verify_p3_m3.py`。
      **F6 首个消费者**——M4 Discovery Event / M5 Bug Candidate 复用同一入口
      （`kind` 判别，现只接 `"test"`）。矩阵 **#7–#10** 至此全部可验。
    - review round 1（`review/review_p3_task35_2026-10-10.md`）**不通过
      （1×P1 + 4×P2 + 9×P3）**，**14 条全部已修订（`0e760fe`）**：① `--bundle-id`
      缺省走 trace 归一化的 `app_id` 且不一致**前置**报错（P1，顺带分出「库未初始化」
      与「空库」两种情形——前者 exit 3、后者零 gap exit 0）；② ACCEPT 的**两个**
      卡死窗口都关掉（文件回滚 + `git revert` 反向回滚）；③ `save_candidate` 的
      `review_status` **形参删除**（不是加断言——断言仍留可绕过的入口）；
      ④ F7 的 AST 守卫改用**结构**判据（`ast.Call` + 死分支检测），docstring 里提名字
      不再算。
    - **AE（已决候选不被覆写）双层**：`save_candidate` 对已决行 fail-loud（belt）+
      `generate` 预过滤已决 gap（suspenders）。两层**各自独立可探针**。
    - **round 2 复核**（`review/review_p3_task35_final_2026-10-10.md`）**有条件通过**：
      14 条中 9 条真核销、5 条部分；**修订引入 2×P1**（`_trace_app_id` 在 try 之外
      → traceback 换路径复活；promote commit 不带 pathspec → 连带提交/撤销别人的
      在途工作）+ 暴露 2×P2（脏暂存区下 `git revert` 被拒、AST 守卫 4 种规避形态
      空转）+ 7 条 P3。**全部已修订**（见 `docs/p3_data_audit.md` round 2 段）：
      撤销改为 `_undo_promote`（revert 被拒 → 只作用于自己路径的逆提交）、
      AST 守卫收紧为「无条件执行位置」、`review_status` 读侧收进
      `Literal[*REVIEW_STATUSES]`。修订后：全量 **1866 passed / 0 failed** +
      Gate **exit 0**（11/11）。
- **已完成（M4，2026-10-11，`checkpoint-p3-m4`）**：Bounded Exploration 4.1–4.6
  - 4.1 `explorer/agent.py` 主循环（observe→frontier→guard→execute→verify，每步落
    `agent_trace`）+ `explorer/policy.py`（`effective_risk`==LOW 才自动执行，复用 P1）
  - 4.2 `explorer/frontier.py::select_frontier`（unexplored 优先 + novelty）纯函数
  - 4.3 `explorer/state.py::ExplorationBudget`（四维；LLM 维就是 P1 `LLMBudget` 实例）
  - 4.4 `explorer/loop_detector.py::is_loop`（纯计数；`None` 指纹不计入+留痕）
  - 4.5 迁移 `006_discovery_events.sql` + `candidates/discovery.py`（**只落 PENDING、
    不写图**，E12；ACCEPT 后经 `register` 幂等登记）
  - **4.6 `cli/main.py::explore` + `phase0/verify_p3_m4.py`**（M4 收口，`2fb0215`）：
    F7 **两层**前置（公共 + 探索专属，**staging 也拒**）、`--build` 不做静默兜底、
    App 激活、run 行成功路径收口。两段对抗性 review 后再补六处**失败路径**收口
    （僵尸任务行 / 泄漏 Appium 会话 / 半张库 / 陈旧 trace 范围 / 坏库 traceback /
    模型收不下的预算维）。**未做**：翻案通道（沿 3.5 拍板，M4+ 显性登记）。
- **已完成（M5，2026-10-11，Task 5.6 / P3-25 收口）**：Failure Diagnosis 出口
  - `candidates/bug.py` 状态机（`DISCOVERED→ANALYZING→CANDIDATE→HUMAN_REVIEW→
    {CONFIRMED|REJECTED|DUPLICATE}`，转换表纯函数，非法转换 fail-loud）+
    `candidates/review.py` 接 `kind="bug"`（F6 同一入口）+ `cli/main.py::diagnose
    --run-id [--step] [--base-build]` + `phase0/verify_p3_m5.py`。
  - **Gate 实测抓出四个真缺陷**（全部「离线单测绿、真机上不成立」那一类，详见
    `docs/p3_data_audit.md` §5.6）：① `classifier` 只读 `detail["recovery"]`，
    漏掉恢复**失败**时的 `recovery_attempted.stages[reconcile].outcome=DRIFT`
    → 真机漂移一条假设都产不出；② `cmd_diagnose` **先算归因、后跑复现** →
    复现证据进不了归因的证据集（分数 8 但归因空）；③
    `same_issue_multiple_tests`（权重 3）**全仓没有生产者** → 漂移现场分数上限
    8 < 阈值 10，F10 的「建议」在真机上不可达；④ `--db` 指向未初始化的库 →
    `ValueError` 冒成 traceback 而不是 exit 3。
  - **Gate 的设计前提被证伪并改写**：早先的「注入 `fatalError` 崩溃按钮」走不通
    （点它 Appium 记 SUCCESS、用例照常 PASS；P1 根本没有 APP_CRASH 判定），
    改用 plan 自己写的**漂移法**。⚠️ **漂移的形态必须是「元数据旧、App 新」**：
    `mta run` 的 lint 会拒掉「元数据里不存在的 target」，先改名再重扫 metadata
    会让用例根本跑不起来（那是配置错误不是漂移）。
  - Gate 结果：`out/p3_m5_gate/summary.json` verdict=PASS、**device_half=PASS**
    （漂移 run → 规则命中 → 真机复现 3 次 → **11 分过阈值 10** →
    `AUTOMATION_DEFECT` → `HUMAN_REVIEW` → 人工 accept → `CONFIRMED` +
    Evidence Bundle；G10 内含全量 **2148 passed**）。
  · **M6 ✅**（`phase0/verify_p3_final.py` exit 0，**33/33 PASS**，**device_half=PASS**，
  `out/p3_final_gate/summary.json` verdict=PASS，78.2s；G11 内含全量 **2207 passed**。
  四段：OFF（F7 production 拒绝 + Benchmark 三段闭环 + 矩阵 #18 manager 级）/
  CHAIN（plan→generate→人工 accept→run --fake-driver）/
  DEV（真机 explore `LOOP_DETECTED steps=3` → 漂移注入 → run 失败 → 真机复现 3 次
  → **11 分过阈值 10** → `AUTOMATION_DEFECT` → 人工 accept → `CONFIRMED` +
  Evidence Bundle）/ 矩阵 #17 资产零改动）。逐条对照设计 §1.2 的留证见
  `docs/p3_demo_record.md`。
- **已完成（M6，2026-10-11，Task 6.1–6.6 / P3-26…P3-31 收口）**：Subagent +
  自主回归闭环 + Benchmark
  - 6.1 `agents/state.py`：`AgentState` 九态 × `AgentEvent` 九事件的**转换表**
    （`transition` / `successors` 纯函数，非法转换 fail-loud）+ `should_escalate`
    （四触发 + 优先级 LLM_POLICY > CRITICAL_ACTION > GUARD_BLOCK_STREAK >
    LLM_INVALID_STREAK > BUDGET_EXHAUSTED）+ `escalation_context` 人工接手包。
    **`update_task_state` 强制走表**（判据复用同一张表，不写第二份）。
    表是这样选的：让既有三处写点（`Explorer.run` 末尾、`cmd_explore` 的两条
    IDLE→ESCALATED 错误路径、`test_agent_store` 的 IDLE→PLANNING）**零改动**
    即合法。ESCALATED 是**终态**（无出边）＝「不重试不放宽」。
  - 6.2 `agents/base.py::Subagent`：构造参数只有 agent_db / task_id / toolkit /
    llm_budget——**没有 executor / driver / device_session 形参**（F12 在构造层
    不可达）。toolkit 与 llm_budget 均有类型断言。**口径**：三个 Subagent 共用
    **同一个 Executor + Guard**（依赖对象同一），但 toolkit **实例**各一个
    （task_id 烤在 toolkit 里，agent_trace 才能按任务归因）。
  - 6.3 `agents/context.py::build_agent_context`：四字段全部经 `KnowledgeSources`
    （F3）。⚠️ **两处签名偏离登记**：设计的 `(task, knowledge)` 两参不可实现
    （graph_query 要 app_id、experience_lookup 要 screen/target）→ 显式 kwargs；
    `relevant_history` **不做屏级过滤**（trace 的 steps 表不记 screen，而
    `trace_history` 对 `screen_id` 是 fail-loud 的）——**用户拍板登记偏离**，
    屏级收窄等数据源补齐，**保持登记不闭**。
  - 6.4 `subagents/`（**中立包**）+ `agents/manager.py::TestManager`：三个薄封装
    委托 M2/M4/M5 的本体；`assess` 是 `should_escalate` 的唯一消费点；升级=
    停止并交包，已 ESCALATED 的任务再跑直接 `RuntimeError`。
    ⚠️ **落点偏离**：plan 写 `agents/planner_agent.py`，本仓两条 import 级守卫把
    两个方向都封死（`agents/**` 不 import `planner.*`；`planner/**` 不 import
    `agents`），薄封装同时要 import 两边 → 落 `subagents/`。**让结构说话，不给
    守卫开后门**。
  - 6.5 `report/agent_metrics.py`（§13.1 指标，分母口径逐条登记：`SCHEMA_INVALID`
    不进重复率分母 / 复现率分母是 `reproducible IS NOT NULL`）+ `agents/benchmark.py`
    （§13.2 固定 7 类 case + `compare_benchmarks` → `mta agent benchmark
    run|compare`，回归即 **exit 1 + AGENT_REGRESSION** 供 CI）+ 迁移
    **007_benchmark_runs.sql**（plan 写 005，005/006 已被占用——偏离登记）。
    `Selection Precision` / `Diagnosis Accuracy` 依赖标注真值，**collect 不产出**。
  - 6.6 `phase0/verify_p3_final.py` + `docs/p3_demo_record.md`：**33/33 PASS**。
    **Gate 自己被实测推翻六处**（见 `docs/p3_data_audit.md` §6.6.2），其中两处是
    M4 那次 `FRONTIER_EMPTY steps=0` 假绿的**同款复发**：`max_llm_calls=0` 让
    第 0 步熔断而 `explore` 仍 exit 0；收紧后又暴露「冷启动停在 LoginView 而
    声明面只播种了 HomeView→ProfileView」→ frontier 从 LoginView 出发是空的。
    判据改成「**steps ≥ 1 且终态在设计 §7.2/7.3 词表内**（唯独排除
    `LLM_BUDGET_EXCEEDED`——那是配置错，不是有界停止）」。
  - ⚠️ **本轮未能先红后绿**：Bash 分类器故障期间 pytest 完全跑不起来，
    M6 全部代码写完后才第一次执行。补做 **A/B 变异探针 19/19 如实变红**
    （改坏一个语义 → 对应测试必须当场红 → 还原 → md5 校验）；其中 3 条第一版
    **没变红**，据此补了两处断言拆分/补钉（`docs/p3_data_audit.md` §6.6.1 的
    E1/E3）。全量回归只告诉你「没红」，A/B 探针才告诉你「断言够不够」。
- **下一步**：**P3 已全部收口**（M1–M6，`checkpoint-p3-m6` = `f63b381`，
  里程碑 tag `v0.3-p3-complete`）。设计 §1.2 的六条成功标准逐条留证见
  `docs/p3_demo_record.md`，其中**明写未覆盖项**（LLM 排序质量、Benchmark 的
  真实统计意义）。
  遗留的**显性不做 / 未闭项**：① 翻案通道（沿 3.5 拍板）；② `screen_id` 的
  trace 数据源缺口（用户拍板登记偏离）；③ 模型路由（设计 §9.4 移出核心里程碑）；
  ④ Evidence Bundle 的外部系统对接（沿 M5 open question #1）；
  ⑤ `reproduce` 的 `runner` 回调仍是 `cli/main.py::cmd_diagnose` 里手工注入
  （`_run_reproduction`）。**这条现在有明确结论**（Task 6.7 探明）：不是欠账而是
  **正确的形态**——`run_testcase` 的执行上下文（`manage_env` / lifecycle /
  `run_id` / suite）不在工具层拿得到，工具本体**故意没有默认实现**，`NOT_WIRED`
  里写明了原因。若将来要让工具本体也能跑，得先把这套上下文定义进工具层。
  ⑥ **工具表 22 个里已接线 17 个，剩 5 个**（`agents/tools.py::NOT_WIRED`，
  Task 6.7 / P3-32 已把 3 知识 + 2 提案接上，`NOT_WIRED` 10 → 5）：
  - **3 个诊断数据源** `get_logs` / `get_network_summary` / `get_crash_info`：
    本仓**没有任何采集代码**（grep 全仓确认）。`backend_evidence`（权重 2）
    至今**没有真实生产者**就是这条的直接后果。**单独一批建**（用户拍板 ②）。
  - **2 个重跑入口** `run_testcase` / `run_candidate_test`：见上条 ⑤。
  ⚠️ `NOT_WIRED` 的指路**曾是过期里程碑**（「等 Task 2.2」「等 M5」「等 M4/M6」，
  那些里程碑都收口了且没交付它们）——已逐条改写成「缺什么数据源 / 为什么不该接」，
  并有断言钉住（理由里不许出现已完成里程碑的字样）。**这是本仓最容易复发的
  文档说谎形态**。
  ⚠️ 顺带更正本条旧账：testcase 的 `postcondition` **已经**并进 `StepProbe`
  （`diagnosis/reproducer.py` 的 `has_postcondition` / `postcondition`，
  `cli/pipeline.py` 侧按 `step_index` 取用例声明），M5 之前记的这条待办已过时。
- **待拍板的三项**（2026-10-11 用户拍板后**全部收口**）：
  ① `screen_id` 过滤的数据源缺口（`docs/p3_data_audit.md` Task 2.2）——
  **保持登记不闭**（用户拍板）：`relevant_history` 改用 app_build + limit 可用键，
  屏级收窄等 `steps` 表补齐屏列；
  ② 「坏引用用例」的处置口径 → **「列出不猜」**（用户拍板）：不为了让表好看而给
  无数据源的工具编假实现，已机械化为断言（`NOT_WIRED` 理由不得指向已完成里程碑
  且长度 > 30，见 `docs/p3_data_audit.md` §7.3）；
  ③ 权重校准所需数据量 → **登记不动，等真实数据**（用户拍板）：校准方法已写死在
  `diagnosis/evidence.py` 的 docstring（触发条件 ≥30 条人工确认归因 + 每类都要有
  生产者；判据：真 bug 分数中位数 ≥ 阈值、非 bug 95 分位 < 阈值、Benchmark 仍 7/7；
  不许动的三项：类别集合 / 按类去重求和 / 阈值初值）。**当前样本量 1 条**
  （M6 Gate 自注入的那条，且随 gate 工程清理）——差得远，故数值一律不动，
  `config/policy.yaml` 的「初版待校准」标注不许删。
  ⚠️ M6 新增的两个初值 `guard_blocks_before_escalate=3` /
  `llm_invalid_before_escalate=2` 同属「初版待校准」类。
