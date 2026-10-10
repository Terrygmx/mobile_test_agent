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
| `run_phase0_demo.py` | **唯一一份「讲完整故事」的可执行 demo**（P0 Stage 9：元素漂移 → LLM 恢复 → PASS）。12 行 docstring 就说清了产品在干什么 |
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
| 1 | `SKILL.md` 自称 **P2**（引用 `docs/phase2-plan.md`、`925+ passed`、「延后项已全部清空」） | 现行应读 **`docs/phase3-plan.md`**；基线 **1691**；P3 的登记项在 `docs/p3_data_audit.md` |
| 2 | `docs/phase3-plan_by_claude.md` 看起来像计划 | 它是**底稿**。权威是 `phase3-design.md` + `phase3-plan.md` |
| 3 | 两批记忆目录 | `.workbuddy/memory/`（旧，9 月）vs `.workbuddy-ai/memory/`（现行）。**只有后者会自动注入** |
| 4 | 旧文档写「`AgentToolkit` 装配点归 Task 2.4」 | **已被实现证伪**：`mta plan` 离线、不碰 App，装配它是死对象。真正的装配点是**第一个会驱动设备的命令**（M6 自主闭环 / **M3 Dry Run**） |
| 5 | `docs/handover.md` §9 自己写的快照（曾停在 `b4810f7` / 1609 / 「下一步 Task 3.1」） | **本文件已刷新到 2026-10-10**；再过期时以 `git log` + 全量 pytest 为准（这是本文件的固有风险：**它不会自动更新**） |
| 6 | `candidates/fingerprint.py` / `docs/p3_data_audit.md` 称「**每个步骤恰好落入一个分量**」 | **实测为假**：带 `postcondition` 的 `ActionStep` **同时落入 `screens` + `actions` 两个分量**（真语料 2 处）——见 `review/review_p3_task33_2026-10-10.md` P3-2 |
| 7 | `MEMORY.md` §1 记的「**6 个固定假失败**」 | **已过时**：2026-10-10 实测那两条文件 **11 passed**（当前基线 0 failed）。MEMORY 是自动注入的，**它 §1 那行未同步**（该目录不入库，只能人工改） |

## 9. 当前状态快照（**2026-10-10**，以实测为准）

- **分支 / HEAD**：`P3-1010` / `8456e14`（`fix(p3): review_p3_task33 修订——wait 分支钉子 + precondition 口径登记`）
- **全量基线**：**1691 passed / 0 failed**
- **最新 tag**：`checkpoint-p3-m2`（P3 M2 收口）；**M3 尚未收口**
- **Gate 状态**：M1 ✅（`checkpoint-p3-m1`）· M2 ✅（`phase0/verify_p3_m2.py` exit 0，8/8 PASS，
  `out/p3_m2_gate/summary.json` verdict=PASS）· **M3 Gate 未写**（收口时才出）
- **已完成**：P0 / P1 / P2 全部；P3 的 M1、M2 全部；**M3 的 3.1 / 3.2 / 3.3**
  - 3.1 Candidate 字段（`72106e1`，评审两轮已清）
  - 3.2 Coverage Gap（`43555e9` + 修订 `0ede83e`）——本体收口，**round 2 核销 P2-1'
    （`31c0fdc`）**：`pending` 只被「到达点」消费（元素 wait / 否定屏 wait 都不消费它）
  - 3.3 Test Fingerprint（`1ede89f`）：`source/hashing.py::stable_hash` 共享原语；
    review（`review/review_p3_task33_2026-10-10.md`）**通过 + 3×P3**，**3×P3 已修订
    （`8456e14`）**：wait 分支补钉子 + `precondition` 不入指纹的口径登记（含一条
    **故意钉住已知碰撞**的测试）
- **下一步**：**Task 3.4（P3-12）生成管线**（迁移 002 + `generator/validator.py` +
  `dry_run.py` + `generator.py` + 矩阵 #7–#10）。⚠️ **开工前置（必须逐条清）**：
  1. **去重接线时**（3.4 是第一个真实消费者）：`precondition` **不进**指纹（已拍板）；
     真语料已有 2 对同指纹——`reset_logout_001`(reset=LOGOUT) vs `login_001`(reset=RELAUNCH)
     **步骤逐字相同、仅 `precondition` 不同** → 会 SKIP 掉一条语义不同的真用例（这对是危险的
     那个）；`search_001` vs `search_filter_001` 连 precondition 都相同（语料里真的冗余）。
     **SKIP 留痕必须带上「与哪条既有用例撞指纹」**，否则人工看不见被跳过的是哪一条；
  2. `_arrivals` 的 8 处 `getattr` 默认值——引入**新 step 形态**时复查
     （`generator/coverage.py`；评审 §四.2 登记）；
  3. `_step_trigger` 的「trigger 全是动作步骤」是**实测**非构造保证——出现
     `wait_for:` 前缀 trigger 即升级 P2。
- **待拍板的三项**（沿用）：`screen_id` 过滤的数据源缺口（`docs/p3_data_audit.md` Task 2.2）、
  「坏引用用例」的处置口径（同文件 Task 2.3）、`priority_score` / Evidence Score 的
  权重校准所需数据量（`docs/phase3-plan.md` §2 open questions）
