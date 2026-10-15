# Mobile Test Agent（MTA）用户手册

> 更新日期：2026-10-11　·　对应版本：`v0.3-p3-complete`（P0–P3 全部收口，
> 全量基线 **2320 passed / 0 failed**，里程碑 tag `checkpoint-p3-m6`）
>
> 本手册面向**使用者**：项目能干什么、怎么装、怎么跑、每条命令什么时候用。
> 架构与开发纪律见 `docs/handover.md`；各阶段设计见 `docs/mobile-test-agent-phase*-design.md`。

---

## 1. 这个项目是干什么的

一句话定位（`pyproject.toml`）：**Source-aware intelligent regression executor for iOS apps** ——
一个「源码感知的 iOS 回归**选择** + 恢复**执行**平台」。

它不是一堆测试脚本的集合，而是围绕一个核心问题构建的：
**App 源码改了之后，测什么？元素变了跑挂了，怎么办？**

MTA 的完整能力链：

| 阶段 | 能力 | 一句话说明 |
|---|---|---|
| 源码情报 | `mta repo` / `mta source` | 扫描 Swift/ObjC 源码生成元素/屏幕 metadata；算 source coverage、比对两个 build 的源码差异 |
| 静态检查 | `mta lint` | 用例 YAML 落地前检查（引用的元素/屏在 Repository 里解析得到才算合法） |
| 回归选择 | `mta plan` | 按 git diff × 影响面 × 历史失败率 × 风险，产出**每一项都有理由**的回归优先级 Plan |
| 用例执行 | `mta run` | 在模拟器/真机上执行用例套件（Appium/XCUITest），元素找不到时先确定性恢复、再 LLM 恢复 |
| 恢复复用 | `mta review` / `mta experience` | 人工确认 LLM 恢复结果 → 沉淀为 Experience → 验证后晋升为 overrides（下次不再求助 LLM） |
| 图与影响面 | `mta graph` | 从 trace / 源码建 UI 状态图，算「源图 vs 运行时图」「build vs build」差异及受影响用例 |
| 用例生成 | `mta generate` / `mta candidate` | 按 Plan 的 coverage gap 让 LLM 生成候选用例（校验→去重→Dry Run），**人工审核后**才落盘进 `suites/` |
| 有界探索 | `mta explore` | Agent 在模拟器上自主探索 App（四维预算 + 循环检测），把新屏/新转移登记为 discovery 候选 |
| 失败诊断 | `mta diagnose` | 对失败 run 采集证据（复现、崩溃报告、源码变更关联…），证据分过阈值才**建议**归因，产出 Bug 候选 |
| 自评估 | `mta agent benchmark` | 固定 7 类 case 给 Agent 自己跑回归，指标下降超阈值 → exit 1（AGENT_REGRESSION），可接 CI |

**贯穿全程的两条红线**：

1. **人工在环**——LLM 生成/恢复/归因的产物一律先落 `PENDING` 候选，人工 accept 才进正式资产；
2. **环境隔离（F7）**——`plan / generate / explore / diagnose / agent` 五个**自主命令**在
   `--env-kind production` 下启动即拒（无豁免），`explore` 只允许 sandbox + 模拟器。

---

## 2. 核心概念（读命令参考前先看这页）

| 概念 | 是什么 | 在哪 |
|---|---|---|
| **Repository** | 元素与屏幕定义的「唯一事实源」，**双源合并**：手写 `overrides` 优先于机器生成的 `generated` | `repository/overrides/`、`repository/generated/<build>/` |
| **source_metadata.json** | 源码扫描产物：screen → elements → 源码位置（`source.file` 等）。它是一座桥：改动文件 → 元素 → 用例的影响面全靠它 | `repository/generated/local/` |
| **用例 YAML（schema 0.2）** | 步骤式用例：`action / target / wait_for / assertion`，支持 `${ENV_VAR}`（如 `${TEST_PASSWORD}` + `sensitive: true`）、幂等性标记、postcondition | `suites/<suite>/*.yaml`，活样例 20 个 |
| **trace.db** | append-only 运行流水库：`runs / testcase_runs / steps / infra_events`，已脱敏 | `out/trace.db` |
| **experience.db** | 经验库：恢复经验从 CANDIDATE → VERIFIED →（晋升）→ overrides | `out/experience.db` |
| **graph.db** | UI 状态图（源图 + 运行时图）与图差异 | `out/graph.db` |
| **agent.db** | Agent 侧库：test_plans / test_candidates / discovery_events / bug_candidates / benchmark_runs | `out/agent.db` |
| **policy.yaml** | 启动期校验的唯一配置源：自主命令总开关、探索四维预算、升级阈值、归因证据权重表 | `config/policy.yaml` |
| **Build Identity** | 产物 App 内注入 `MTA_GIT_COMMIT`/`MTA_BUILD_ID`，`mta run` 默认 **fail-closed** 校验「App 与 metadata 同 commit」，不一致 exit 3 | 见 `make p1-build` |
| **Candidate** | 一切「机器产出、等人拍板」资产的统称（test / discovery / bug 三种 kind），走同一套审核入口 | `mta candidate review` |

---

## 3. 环境准备

### 3.1 基础依赖

- **macOS + Xcode**（被测 App 是 iOS；Windows 侧开发注意事项见 `docs/windows_dev_notes.md`，需 `PYTHONUTF8=1`）
- **Python ≥ 3.11**，且**必须用项目 `.venv`**（系统 python 没有 appium 依赖）：

```bash
source .venv/bin/activate    # 或直接用 .venv/bin/python
```

- **Appium server**（真机/模拟器执行需要，默认 `http://127.0.0.1:4723`，可用环境变量 `APPIUM_URL` 覆盖）
- 被测 App：仓库自带 demo App `ios_demo/LoginDemo.xcodeproj`（bundle id `com.phaset0.logindemo`）

### 3.2 构建 demo App（模拟器路径）

```bash
# 1. 源码扫描器（SwiftPM，首次需编译）
make scan

# 2. 生成 metadata + 一致性 Gate（overrides vs generated）
make repo-generate

# 3. 构建 demo App 并注入 Build Identity（PlistBuddy 写 MTA_GIT_COMMIT）
make p1-build

# 4. 启动一个模拟器并安装
open -a Simulator
make p1-install          # MTA_SIM_UDID=xxx make p1-install 可显式指定设备
```

> ⚠️ **`make p1-build` 之后必须重新 `make repo-generate`**（保持同 commit），
> 否则 `mta run` 真机/模拟器路径会被 Build Identity 校验 fail-closed 拦截（exit 3）。

### 3.3 环境变量

| 变量 | 用途 |
|---|---|
| `LLM_API_KEY` | 启用 LLM 恢复 / 生成 / 排序（不给则确定性路径照常，LLM 功能关闭） |
| `LLM_BASE_URL` / `LLM_MODEL` | 可选，LLM 端点与模型 |
| `APPIUM_URL` | Appium server 地址（默认 127.0.0.1:4723） |
| `TEST_USERNAME` / `TEST_PASSWORD` | 用例里 `${TEST_USERNAME}` 等占位符的取值。**lint 与 run 都会校验**：环境变量缺失 → lint 报 `unknown_secret` exit 3（H9：secret 必须可由 SecretProvider 解析） |
| `MTA_SIM_UDID` | `make p1-install` 的目标模拟器（缺省取第一个 booted 设备） |
| `CI=true` + `MTA_ALLOW_METADATA_MISMATCH_CI=1` | 配合 `--allow-metadata-mismatch` 在 CI 下放行 metadata 不一致 |

---

## 4. 快速上手（不连设备也能跑）

```bash
source .venv/bin/activate

# ⓪ 先备好用例里引用的密钥（EnvSecretProvider 从环境变量取值；
#    缺了会被 lint 以 unknown_secret 拦下，exit 3）
export TEST_USERNAME=qa_agent
export TEST_PASSWORD=demo-only-not-a-secret

# ① 静态检查用例（引用解析不到 → exit 3）
python -m cli.main lint suites/smoke/login_001.yaml --overrides repository/overrides

# ② 不连真机冒烟：--fake-driver 注入最小桩，跑通整条 run 管线
python -m cli.main run --suite smoke --fake-driver

# ③ 看跑出来的流水
sqlite3 out/trace.db "SELECT run_id, suite, status FROM runs ORDER BY rowid DESC LIMIT 5;"

# ④ 体验完整的 Phase 0 demo（元素漂移 → LLM 恢复 → PASS）
python run_phase0_demo.py --stub          # 无 LLM key 用 stub
python run_phase0_demo.py                 # 有 key 走真 LLM
```

连真实模拟器的完整回归（前置：Appium 在跑、`make p1-build` + `p1-install` 已做）：

```bash
python -m cli.main run --suite smoke \
  --bundle-id com.phaset0.logindemo \
  --overrides repository/overrides \
  --generated repository/generated/local
# 可选：--junit out/junit.xml --html out/report.html --no-llm
```

---

## 5. 典型工作流

### 5.1 日常回归（P1/P2 能力）

```text
改代码 → make scan + repo-generate（重扫 metadata）
      → mta source diff（看两个 build 的源码差异，可选）
      → mta run --suite regression（执行 + 元素漂移自动恢复）
      → mta review list（LLM 恢复的需要人工确认）
      → mta review accept <id> --out patch.yaml（导出 overrides 补丁，人工合入）
      → mta experience verify / promote（经验转正，下次不再求助 LLM）
```

要点：

- 恢复确认走 **H15 红线**：工具只导出补丁，**绝不自动写 Repository**，合入由人工完成。
- `mta experience promote <id>` 缺省生成 PENDING proposal；`--approve <PROPOSAL_ID>` 才落盘
  overrides + git commit（撤销 = `git revert`）。

### 5.2 智能回归选择（P3：plan）

```bash
python -m cli.main plan --base-build <旧build> --json
```

- diff base：`--base-build` 给了就用那份 metadata 的 git_commit，否则 `HEAD~1`。
- 排序可解释：`priority_score = risk_weight × coverage_impact + change_correlation`，
  每一项都有落库字段——不是「LLM 觉得该测这个」。
- `--no-llm` 时 reasons 只用规则摘要，确定性排序不变。Plan 落 `agent.db` 的 `test_plans` 表。

### 5.3 生成→审核→执行闭环（P3：generate + candidate + run）

```bash
# ① 按 plan 现场重算 coverage gap → LLM 生成候选（校验/去重/Dry Run 全过才入库）
python -m cli.main generate --gap-from <plan_id> --suite search \
  --requirement "搜索页新增筛选入口的覆盖"

# ② 人工审核（默认列 PENDING；--rejections 可看生成期拒绝留痕）
python -m cli.main candidate review list
python -m cli.main candidate review accept <candidate_id> --kind test   # 落盘 suites/<suite>/ + git commit
python -m cli.main candidate review reject <candidate_id> --note "理由必填"

# ③ 接受的用例与普通用例同跑
python -m cli.main run --suite search --fake-driver
```

要点：

- 生成必须要有 LLM（`--no-llm` 等于「不生成」）。
- 接受的候选文件名自带出处（屏、动作、目标、指纹四段），accept 输出里带撤销路径。
- **已决候选不可覆写**（fail-loud 双层防护）；翻案通道当前显性不做。
- `kind=discovery` 的候选 accept 后幂等登记进 UI 图；`kind=bug` 确认后落 Evidence Bundle JSON。

### 5.4 有界探索（P3：explore）

```bash
python -m cli.main explore --goal "覆盖登录页所有可达转移" \
  --bundle-id com.phaset0.logindemo
```

- **只允许 sandbox + 模拟器**（传别的直接拒，无豁免 flag）。
- 四维预算（步数/时长/LLM 调用/重复状态，见 `config/policy.yaml` 的 `exploration` 段）
  超限即停，**不自动放宽重试**；`LOOP_DETECTED` 是**正常终态**（自己撞墙了就停）。
- 探索只落 PENDING discovery 候选、**不写图**（E12）——图在人工 accept 后才登记。

### 5.5 失败诊断（P3：diagnose）

```bash
# 先从 trace.db 找到失败 run 的 run_id（如 run_ab12cd34）
python -m cli.main diagnose --run-id run_ab12cd34 --base-build <旧build>
```

- 采集证据：真机复现 3 次、`--crash-report`（.ips）、`--logs`（App 日志）、
  build-to-build 图 diff 关联的源码变更、多用例同因佐证……
- 证据分（权重表见 `config/policy.yaml` 的 `diagnosis_evidence_weights`）**≥ 阈值 10 才建议归因**，
  最终归因仍由人工确认；不过阈值保持 `UNTRIAGED`，不硬编故事。
- 产出 Bug 候选（`DISCOVERED→…→HUMAN_REVIEW` 状态机），人工 accept（`mta candidate review accept --kind bug`）
  后落 Evidence Bundle。
- 诊断是**离线命令**，不自动连设备采集日志——日志/崩溃报告由 `mta run` 侧或人工提供。

### 5.6 Agent 自评估（P3：agent benchmark）

```bash
python -m cli.main agent benchmark run --label v1
python -m cli.main agent benchmark compare v1 v2 --threshold 0.05
# 关键指标下降超阈值 → exit 1（AGENT_REGRESSION），可直接接 CI
```

---

## 6. CLI 命令参考

统一入口：`python -m cli.main <子命令>`（无 console-script，需在项目根、用 `.venv` 执行）。

| 命令 | 类型 | 干什么 | 关键参数 |
|---|---|---|---|
| `lint` | 人工 | 用例静态检查 | `TESTCASE_YAML…`、`--overrides`、`--generated` |
| `run` | 人工 | 执行用例/套件（三选一：`--suite/--tag/--case`） | `--no-llm`、`--junit`、`--html`、`--fake-driver`、`--bundle-id`、`--udid`、`--app-path`、`--env-kind`、`--allow-metadata-mismatch`、`--db`、`--exp-db` |
| `plan` | **自主** | git diff → 回归优先级 Plan | `--base-build`、`--build`、`--json`、`--no-llm`、`--policy` |
| `repo generate` | 人工 | 扫源码 → metadata（+`--check` 一致性 Gate） | `SOURCES…`、`--out`、`--build`、`--check` |
| `source coverage` | 人工 | 用例引用的 identifier 有多少被 metadata 解析 | `--strict`（dynamic 也算未达标）、`--json/--html` |
| `source diff` | 人工 | 两个 build 的源码差异（限用例到达的 Screen） | `--old`（必填）、`--new`、`--fail-on-drift` |
| `review list/accept/reject/reseed` | 人工 | 恢复结果人工确认 | `accept` 只导出补丁（`--out`），`reject --note` 必填，`reseed` 补建 Candidate |
| `report triage` | 人工 | FAIL 用例人工归因（审计落 trace） | `tc_run_id`、`--attribution`、`--note` |
| `experience list/show/verify/revalidate/sweep/promote` | 人工 | 经验库运维 | `verify`（资格判定）、`promote`（两段式 + `--approve`）、`sweep --max-idle-days` |
| `graph build/diff/show` | 人工 | UI 状态图 | `build --from-trace/--from-source`；`diff --build --base-build --cases`（算影响面） |
| `generate` | **自主** | gap → LLM 生成候选用例 | `--gap-from`（必填）、`--suite`（必给）、`--requirement`、`--fake-driver` |
| `explore` | **自主** | 有界探索（sandbox+模拟器 only） | `--goal`（必填）、`--budget`、`--device-type` |
| `diagnose` | **自主** | 失败诊断 → Bug 候选 | `--run-id`（必填）、`--step`、`--base-build`、`--crash-report`、`--logs`、`--no-llm` |
| `agent benchmark run/compare` | **自主** | Agent 质量回归 | `--label`、`compare V1 V2 --threshold` |
| `candidate review list/accept/reject` | 人工 | 三种候选的统一审核（F6） | `--kind test/discovery/bug`、`accept --export-patch`（只导出不落盘）、`reject --note` 必填 |

**自主命令（F7）**：`plan / generate / explore / diagnose / agent` 五个。启动时校验：

1. `--env-kind production` → 拒绝，**无豁免 flag**（exit 1，消息含 `F7` 便于检索）；
2. `config/policy.yaml` 的 `autonomous.enabled: false` → 整体禁用（连 sandbox 也拒）；
3. policy.yaml 有未知键/错型/负值/权重表键集不符 → fail-loud（拼错的键不会静默生效）。

---

## 7. 退出码约定

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 通用失败（如 F7 环境拒绝、AGENT_REGRESSION） |
| 2 | 占位/未实现（NotImplemented，避免误当成功） |
| 3 | fail-closed 拦截：lint 有 ERROR、Build Identity metadata 不一致、`repo generate --check` 缺口非空、`source coverage --strict` 未达标 |

---

## 8. 数据与产物一览

| 路径 | 内容 |
|---|---|
| `out/trace.db` | 运行流水（runs/testcase_runs/steps/infra_events），append-only |
| `out/experience.db` | 恢复经验库 |
| `out/graph.db` | UI 状态图与图差异 |
| `out/agent.db` | test_plans / test_candidates / discovery_events / bug_candidates / benchmark_runs |
| `out/source_coverage.json` | `mta source coverage` 产物 |
| `out/<bundle-dir>/` | Evidence Bundle JSON（Bug 确认后落盘） |
| `repository/generated/<build>/` | metadata（**generated 不手改**，只能 `mta repo generate` 重新生成） |
| `repository/overrides/` | 手写元素/屏幕定义（人工合入恢复补丁 / experience 晋升的落点） |
| `out/p3_final_gate/summary.json` 等 | 各 Gate 脚本产物（`phase0/verify_p*.py`） |

---

## 9. 常见坑（前人实测踩过，别再踩）

1. **`make p1-build` 后必须 `make repo-generate`** —— 不同 commit 的 metadata 会让 `mta run` fail-closed（exit 3）。确要放行：`--allow-metadata-mismatch`（trace 留痕；CI 下还需 `MTA_ALLOW_METADATA_MISMATCH_CI=1`）。
2. **`${TEST_USERNAME}` 等 secret 未 export 时 lint 直接 exit 3** —— 报 `unknown_secret: … not resolvable by SecretProvider (H9)`。这不是用例写错了，是环境变量没备好（见 §3.3）。
3. **漂移的形态必须是「元数据旧、App 新」** —— 想注入漂移场景时改 Swift 源码后**不要**重扫 metadata；先改名再重扫会让 lint 拒掉用例（那是配置错误，不是漂移）。
4. **跑 pytest 必须指定 `--basetemp` 在工作区内** —— 否则受限环境会产出 200+ 个 `PermissionError: EEXIST` 假失败：
   ```bash
   env PYTHONPATH=. .venv/bin/python -m pytest tests -p no:cacheprovider --basetemp=./out/_pt
   ```
5. **真机 Gate 必须禁用沙箱跑** —— 否则 SwiftUI 宏报 `could not be found for macro 'State()'`（看起来像代码坏了，其实不是）。
6. **`explore` 冷启动可能 frontier 为空** —— 探索的声明面来自图库播种的屏；冷启动停在未播种屏（如 LoginView）时 frontier 是空的。先让 `run` 跑过、`mta graph build --from-trace` 建图后再探索。
7. **App 崩了不一定记 APP_CRASH** —— 已修正为覆盖所有失败出口，但诊断时仍建议显式给 `--crash-report`（.ips），崩溃判定需要这份证据。
8. **本项目没有 CI 流水线** —— 「门禁」= 全量 pytest 里的一条测试，别以为有东西在后台替你跑。

---

## 10. 当前边界（显性不做 / 未闭项）

使用前应知道这些**有意**留下的缺口（均为登记在案的决策，不是遗漏）：

- **翻案通道**：已 accept 的候选无法翻案（只可 `git revert`）。
- **`screen_id` 屏级历史过滤**：trace 的 steps 表不记 screen，`relevant_history` 只按 app_build + limit 过滤；等数据源补齐。
- **模型路由**：移出核心里程碑（当前单模型，`LLM_MODEL` 指定）。
- **3 个诊断数据源未接线**：`get_logs / get_network_summary / get_crash_info` 本仓无采集代码，故 `backend_evidence`（权重 2）暂无真实生产者——诊断时日志/崩溃报告靠人工提供。
- **`run_testcase / run_candidate_test` 两个工具本体故意无默认实现**（执行上下文不在工具层）；重跑入口目前由 `mta diagnose` 的复现流程承担。
- **权重未校准**：`policy.yaml` 的所有数值都是「初版待校准」（校准方法写在 `diagnosis/evidence.py` docstring，触发条件 ≥30 条人工确认归因）。
- **Benchmark 的两个指标**（Selection Precision / Diagnosis Accuracy）依赖人工标注真值，collect 不产出。

---

## 11. 一次性看懂全流程（备忘卡）

```text
源码 ──repo generate──▶ metadata ──┬──▶ lint ──▶ run ──▶ trace.db ──▶ review/experience ──▶ overrides
                                   │              │
      git diff ──plan──▶ Test Plan │              └──▶ graph build/diff（影响面）
                                   │
              coverage gap ──generate──▶ candidate（PENDING）──人工 accept──▶ suites/
                                   │
              explore（sandbox）──▶ discovery 候选 ──人工 accept──▶ graph
                                   │
              run 失败 ──diagnose──▶ Bug 候选（证据分≥10 才建议归因）──人工 accept──▶ Evidence Bundle

Agent 自身质量：agent benchmark run/compare（退化 → exit 1）
```

有问题先查三处：`docs/handover.md`（交接地图）、`git log`（每条提交都写明偏离与实测数字）、
`docs/p3_demo_record.md`（六条成功标准的逐条留证与复现方式）。
