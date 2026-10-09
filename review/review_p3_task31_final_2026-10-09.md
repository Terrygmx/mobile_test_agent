# Review — P3 Task 3.1 收口修订（`66cb8d5`）+ `review_p3_task31` 的 3×P3 + 4 条小观察核销

| 项 | 值 |
|---|---|
| 审查对象 | **`66cb8d5`** — `fix(p3): review_p3_task31 修订——例数口径 + 设计 6.2 勘误回填（0×P2 + 3×P3）`（HEAD） |
| 核销对象 | `review/review_p3_task31_2026-10-09.md`（上一轮对 `72106e1` 的评审）的 **3×P3 + 4 条小观察** |
| 依据 | 设计 `docs/mobile-test-agent-phase3-design.md` §6.2（:211-213）/ F5（:52）；plan `docs/phase3-plan.md` Task 3.1（:289）+ §0 表（:35）；MEMORY §3.6 / §5.A / §5.C |
| 评审范围 | `docs/mobile-test-agent-phase3-design.md`（+3/-2）、`docs/p3_data_audit.md`（+77/-4）、`docs/phase3-plan.md`（+1/-1）、`tests/unit/test_testcase_candidate_fields.py`（+5/-4）、`tests/unit/test_lint_candidate_compat.py`（+7/-4） |
| 基线 | 全量 **1625 passed in 16.54s**（命令行**无** `--ignore`）；`--collect-only` 同为 **1625** ✓ 与修订前一致 |
| 结论 | **通过 — 上轮 7/7 全清（3×P3 实质落地 + 2 条小观察顺手清 + 2 条按理由正当不升级）；本轮新增 0×P2 + 2×P3，全是「同一形状没扫全」的文档口径，无回归** |
| 只读性 | 未改动任何项目文件（**3 次 A/B** 均先 `cp` 到 `/tmp/t31fbak/` 后还原，`git diff --stat` 核对为空；探针脚本与 `out/_ptrev31*` 跑完即删） |

**一句话**：三条 P3 **全部实质落地、无一条是「改注释交差」**——设计 §6.2 的 `"0.1"`/`name` 真回填了（我抠出文档 YAML 块直接喂 `parse_testcase_dict` → **PARSED OK**），例数 13/3 与探针 E 的 4 red 我用 `--collect-only` 与真改文件两次独立复算**均吻合**，plan:289 的 `:15→:19` 也已更正。
本轮新增的两条 P3 是**同一件事的两半**：上一轮小观察 4 点出的「drive-by 扩行把 `path:line` 弄旧」这个形状，修订**只修了被点的那一处**（plan:289），**同一行 drive-by 弄旧的 `plan:35` 的另外两处引用（`:119`→实为 `:123`、`:141-149`→实为 `:145-153`）没扫**——MEMORY §5.A 明写的「修完被点的那一处要顺手扫全包」在这一轮又空转了一次。另一条是同一「0.1 vs 0.2」口径在**代码里还有三处**（`testcase/lint.py:4,157`、`testcase/loader.py:35`），属 P1 存量、非本轮引入。

---

## 一、核销 `review_p3_task31_2026-10-09.md`（3×P3 + 4 条小观察）→ **7/7 全清** ✅

| # | 上轮发现 | 修法 | 本轮实测（照抄命令，HEAD） | 结论 |
|---|---|---|---|---|
| **P3-1** | 审计文档与提交信息的**分文件例数 12/4** 与 HEAD 实测 **13/3** 不符 | `docs/p3_data_audit.md:1561-1563` 改为 **13 例 / 3 例**并加展开口径注（「8 + 3 个 `def test_`，含 `parametrize` 展开 4 + 3」） | `--collect-only -q` → `test_testcase_candidate_fields.py` **13**、`test_lint_candidate_compat.py` **3**（合计 **16**）；`grep -c "def test_"` = **8 / 3**；11 − 2 + 7 = 16 ✓ | ✅ 数字与口径注**都对得上** |
| **P3-2** | 探针 **E** 自述 3 red、实测 **4** red | `docs/p3_data_audit.md:1609` 改 **4 red** 并注明「4 个参数全红」 | A/B 真改 `schema.py:220` `Literal["CANDIDATE"]` → 裸 `str` → **4 failed, 12 passed**（`[VERIFIED]`/`[candidate]`/`[CANDIDATE ]`/`[PENDING]` 全红，均为 `DID NOT RAISE`） | ✅ 复算一致 |
| **P3-3** | plan 要求「**勘误回填设计文档**」，设计 §6.2 的 `schema_version: "0.1"` **仍未回填、也未登记待办** | 设计 `:211` → `"0.2"` 并加行内注；`:212` 补 `name: 搜索空结果` 并加注 | **探针**：正则抠出 §6.2 的 ```yaml 块 → 仅把伪记法 `steps: [...]` 换成 `[{"action":"launch_app"}]` → 喂 `parse_testcase_dict` → **PARSED OK**（`schema_version=0.2` / `status=CANDIDATE` / `generation_evidence` 三键齐）；`git log -- docs/mobile-test-agent-phase3-design.md` 末次改动即本提交 | ✅ 真回填，非「登记待办」 |
| 小观察 3 | `test_lint_candidate_compat.py` docstring 说 issue 码「由**环境变量**决定」，而该文件用自造 `_KnownSecrets`、不读环境变量 | 收窄为「由 `SecretProvider` 实现与 `repository/generated/` 内容决定」+ 点明本文件用 `_KnownSecrets`、审计里 46 条用 `EnvSecretProvider` | 新 docstring 与代码一致（`_KnownSecrets` 定义在 `:43`，三处 `lint(..., _KnownSecrets())`） | ✅ |
| 小观察 4 | plan:289 的 `testcase/schema.py:15` 被 drive-by 扩行弄旧（实为 `:19`） | plan:289 → `:19`，勘误句改为「已回填」 | 父提交 `:15` 确为 `SCHEMA_VERSION`、HEAD `:19` 确为 `SCHEMA_VERSION`；`git diff 72106e1 66cb8d5 -- testcase/schema.py` **空**（本轮未动实现） | ✅ |
| 小观察 1 | `GenerationEvidence` 元素级空串放行（`[""]` / `["   "]`） | **不升级**，登记为「Task 3.4 若生成器会写空串再补」 | HEAD 实测：`[""]` / `["   "]` / `["\t"]` 仍**全部 ACCEPTED**——与登记一致，未偷偷改实现 | ✅ 按登记不升级 |
| 小观察 2 | 语料 lint 测试只钉 `schema_invalid`，窄于 plan 判据 ④ | **不判缺陷**（理由：`repository/generated/local` 被 `.gitignore:17` 忽略；且探针 F 证明有牙） | 我的独立复现：把 `status` 改成**必填无默认** → **4 failed**，**含** `test_real_suites_lint_without_schema_errors`（20 条 `schema_invalid`） | ✅ 有牙，理由成立 |

**核销小结**：三条 P3 的修法都**落在被点的地方而不是旁边的注释**——P3-3 尤值一记：plan 明写「勘误回填设计文档」，修订真去改了设计文档本体，还顺手补上了同一示例的第二个错（`name`）。审计文档新增的「评审修订记录」段（+66 行）把**位置 / 问题 / 修法 / 验证 / 未修订项**五栏写全，两条「未修订」也各自给了不升级的理由与升级时点——这正是本仓对「口径与实测不符」的标准收口形状。

---

## 二、探针验证表（声称 vs 我的独立复算）

| # | 声称（提交信息 / 审计文档） | 我的复算 | 结论 |
|---|---|---|---|
| 1 | 全量 **1625 passed**（与修订前一致） | `pytest tests` → **1625 passed in 16.54s**；`--collect-only` → **1625** | ✅ |
| 2 | 本轮「只动文档 + 测试注释/docstring，未动实现，未增删测试」 | `git diff --stat 72106e1 66cb8d5` 仅 6 文件、其中两个测试文件各 5/7 行且全在注释/docstring；`testcase/schema.py` **diff 为空**；`def test_` 数两提交**均为 8 / 3** | ✅ |
| 3 | 分文件例数 **13 / 3** | `--collect-only -q` → **13** / **3** | ✅ |
| 4 | 探针 **E** → **4 red** | 真改 → **4 failed** | ✅ |
| 5 | 设计 §6.2 示例探针 **PARSED OK** | 抠块 → 喂 parser → **PARSED OK**（见上表 P3-3） | ✅ |
| 6 | plan:289 的 `:15` 因 drive-by 漂到 `:19` | 父提交 `:15` = `SCHEMA_VERSION`；HEAD `:19` = `SCHEMA_VERSION` | ✅ |
| 7 | 修订后零残留探针 | 修订前工作区只剩 `?? .mcp.json` / `?? .zcodeignore` / `?? docs/handover.md`（三者均为既有未跟踪物）；`out/` 无 `_probe_*`、无 `_rv31` | ✅ |
| 8 | 卫生（重复顶层定义） | `tests/unit/test_repo_hygiene.py` → **5 passed** | ✅ |
| 9 | `-W error::pytest.PytestCollectionWarning` 下零警告 | 两个新文件 + `test_repo_hygiene.py` 在该 flag 下 → **21 passed** 零警告 | ✅ |
| 10 | 两个新文件全绿 | **16 passed in 0.21s** | ✅ |
| 11 | MEMORY 教训已并入 | `MEMORY.md:159-163`（示例先跑 parser + 勘误回填本体）、`:176-179`（例数报 `--collect-only`）两条都在 | ✅ 真并入，非声称 |

---

## 三、发现（本轮新增）

### P3-1 drive-by 扩行弄旧的 `path:line` **只修了被点的那一处**——`plan:35` 还有两处引用已漂

**位置**：`docs/phase3-plan.md:35`（§0 P2 基线核实表的「Executor 能力」行）：

> 用例动作 Literal：launch_app/terminate_app/tap/input/swipe/back（`testcase/schema.py:119`）+ wait_for + assertion（exists/not_exists/text_equals/text_contains/element_count/enabled/disabled `:141-149`）

**实测（`grep -n`，HEAD）**：

| plan 写的 | HEAD 实际 | 父提交（`72106e1^`） | 是否本次 drive-by 弄旧 |
|---|---|---|---|
| `testcase/schema.py:119` | **:123**（`action: Literal["launch_app", …]`） | **:119** ✓ | **是** |
| `testcase/schema.py:141-149` | **:145-153**（`condition: Literal[` 起始 + 7 个条件 + `]`） | **:141**（起始） ✓ | **是** |

同一个文件在 `4cea731`（P2 完成点）与 `72106e1^` 上，这两个引用**都是对的**；Task 3.1 把模块 docstring 从 7 行扩到 11 行后，两者**同步下移 4 行**。

**为什么记一笔**：这正是上一轮小观察 4 报的**同一个形状**，而修订明的确只改了被点的那一处（`plan:289`）。MEMORY §5.A 对此有明文纪律：

> ⚠️ **同一个形状出现第二次/第三次时，修完被点的那一处要顺手扫全包**——只修被点的那一处，下一轮评审还会以「同形第三次」回来。

`docs/phase3-plan.md` 全文对 `testcase/schema.py` 的 `path:line` 引用**只有两处**（:35 与 :289），本轮修了 1 处、漏了 1 处里的 2 个锚点——「扫全包」的成本在这里是**一条 grep**。

**性质**：纯文档 `path:line` 漂移，无功能影响，故 **P3**（与上轮小观察 4 同档）。但 §0 表是「P3 开工前核实现状」的入口，M3 后续任务（3.2/3.3 要碰 step/assertion 语法）会照它定位。

**修法（两处，各一个数字）**：`docs/phase3-plan.md:35` 的 `schema.py:119` → `:123`、`:141-149` → `:145-153`。建议顺手在审计文档记一句「`path:line` 引用随 docstring 扩行漂移，改 docstring 后 grep 一次 `schema.py:`」。

---

### P3-2 同一「0.1 vs 0.2」口径在**代码里还有三处**（P1 存量，非本轮引入）

**实测**：

| 位置 | 原文 |
|---|---|
| `testcase/lint.py:157` | `f"testcase {tc_id!r} failed 0.1 schema: {detail}"` |
| `testcase/lint.py:4` | `- schema 非法（YAML dict 过不了 0.1 strict parser）→ ERROR；` |
| `testcase/loader.py:35` | `"""文件级入口：带 schema_version 的新用例走 0.1 strict 分发，否则回落 P0 松散模型。` |

而 `testcase/schema.py:19-20` 是 `SCHEMA_VERSION = "0.2"` / `SUPPORTED_SCHEMA_VERSIONS = ("0.2",)`——`0.1` 这个版本号在 testcase 侧**自 `7ae8a7e`（P1-04，0.2 上位）起就不再存在**；`lint.py:157` 的字面量自 `d4aa543`（P1-03）写入后未再改。

**为什么记一笔**：本轮刚为「设计文档里写了过时的 `0.1`」做了一次勘误回填，理由写得很清楚——**过时的版本号会让人按错的版本去理解/抄写**。代码里这三处是同一形状的**另一半落点**，其中 `lint.py:157` 是**用户可见的错误文案**：探针里把 `status` 改成必填时，20 条 lint 报错全部长成

```
LintIssue(code='schema_invalid', message="testcase 'login_again_001' failed 0.1 schema: 1 error(s): Field required", …)
```

——报告读者被告知失败于 `0.1`，而实际校验的是 `0.2`。

**性质**：**P1 存量、非 Task 3.1 引入**，且 `PLAN_SCHEMA_VERSION = "0.1"`（`planner/models.py:91`）是**另一个模型自己的版本号**（`test_plans` 信封，与 TestCase 的 0.2 无关，`tests/unit/test_planner_models.py:47-55` 与 `test_cli_plan.py:206` 都钉着它）——**别顺手把那处也改了**。故定 **P3**，不升级。

**修法（三行）**：`lint.py:4` / `lint.py:157` / `loader.py:35` 的 `0.1` → `0.2`（`lint.py:157` 若担心有外部脚本按文案匹配，可先 grep；全仓 `tests/` 与 `phase0/` 对该文案**零命中**，我已查过）。⚠️ 改 `lint.py:157` 前确认没有测试钉住整条 message——实测无。

---

## 四、小观察（不建议单独立项）

1. **「仅把伪记法 `steps: [...]` 换成…」的措辞可以再准一档**：`steps: [...]` **本身是合法 YAML**（实测 `yaml.safe_load` → `{'steps': ['...']}`），它过不了的是**step schema**（`ActionStep` 要 dict，不是 str）。所以那次替换是必要的，但理由是「`['...']` 不是合法 step」而不是「`[...]` 不是合法 YAML」。审计文档那句把两者混在一起说了；不影响结论（探针仍有效），属措辞精度。
2. **修订把「未修订」也写进了审计文档**（小观察 1/2 各带理由与升级时点）——这是比上轮多出来的一栏，正面。若能把两条的**判据**也各加一句「什么时候算触发」（如「生成器开始写 `GenerationEvidence` 的那一刻」），后续任务回来查更省事。

---

## 五、正面项

1. **三条 P3 全部实质落地、无一条改注释交差**：改的是数字（13/3、4 red）、设计文档本体（`"0.1"`→`"0.2"` + 补 `name`）、plan 的 `path:line`（`:15`→`:19`），三处都在 HEAD 上被我独立复算/抠块验证过。
2. **P3-3 的修法比评审建议更完整**：评审只要求回填 `schema_version`，修订把同一示例的**第二个错**（漏 `name`）一并回填，且两处都加行内注标明「系 Task 3.1 勘误回填」——后来者一眼知道这是改过的，不是设计原文。
3. **验证手法对**：用「抠出文档 YAML 块喂真 parser」证明示例可解析，而不是眼看「长得对」——正是 MEMORY §5.C 记的那条（「把文档里的示例块抠出来直接喂真 parser」）。我的复跑**独立重写了探针**（正则抠块 + 整行替换 `steps:` 行），结论一致。
4. **两条「未修订」给了可执行的升级时点**（Task 3.4 / M3 Gate 前），不是「以后再说」。我在 HEAD 上复验了这两条**确未偷偷改**（空串仍放行、lint 测试仍有牙）。
5. **本轮未动实现**：`git diff 72106e1 66cb8d5 -- testcase/schema.py` 为空，全量 1625 与修订前**逐位一致**——修订轮的风险面被压到最小。
6. **教训真并入 MEMORY**（`MEMORY.md:159-163 / :176-179`），不是只在审计文档里写「待并入」。

---

## 六、优先动作

| 优先级 | 动作 | 建议时点 |
|---|---|---|
| P3-1 | `docs/phase3-plan.md:35` 的 `schema.py:119`→`:123`、`:141-149`→`:145-153` | **Task 3.2/3.3 开工前**（两者要碰 step/assertion 语法） |
| P3-2 | `testcase/lint.py:4 / :157`、`testcase/loader.py:35` 的 `0.1` → `0.2`（**别动** `planner/models.py:91` 的 `PLAN_SCHEMA_VERSION`） | 随手 |
| 小观察 2 | 审计文档两条「未修订」各补一句触发判据 | 随手 |

**无 P2、无回归**——Task 3.1（P3-09）**收口**，可进 Task 3.2（P3-10 Coverage Gap 计算）。

---

## 七、探针出处

- **A/B（3 次，全部真改文件 → 跑测试 → 还原 → `git diff --stat` 核对为空）**：备份 `/tmp/t31fbak/schema.py`。
  **E**：`status: Literal["CANDIDATE"] | None = None` → `status: str | None = None` → **4 failed**（`assert count == 1` 过）；
  **F1**：→ `status: Literal["CANDIDATE"] = "CANDIDATE"` → **3 failed**（不含 lint 测试）；
  **F2**（我的独立探针，用于判小观察 2 是否有牙）：→ `status: Literal["CANDIDATE"]`（必填无默认）→ **4 failed**，**含** `test_real_suites_lint_without_schema_errors`。
- **只读探针 1 个**：`out/_probe_design62.py`（正则抠设计 §6.2 的 ```yaml 块 → 整行替换 `steps:` 行 → 喂 `parse_testcase_dict` → PARSED OK）。已删。
- **空串放行复验**：`GenerationEvidence(coverage_gap=["" / "   " / "\t"], …)` → **3/3 ACCEPTED**（内联 `.venv/bin/python`，未落盘）。
- **其它实测**：`pytest tests`（1625 passed in 16.54s）、`--collect-only`（1625 / 13 / 3）、`grep -c "def test_"`（8 / 3）、`tests/unit/test_repo_hygiene.py`（5 passed）、`-W error::pytest.PytestCollectionWarning`（21 passed 零警告）、两个新文件（16 passed）、`git show 72106e1^:testcase/schema.py | sed -n 15p`（父提交确为 `SCHEMA_VERSION`）、`git show 4cea731:testcase/schema.py | grep -n`（:119 / :141，证 plan:35 在 P2 完成点是对的）、`git diff 72106e1 66cb8d5 -- testcase/schema.py`（空）、`grep -rn 'failed 0.1 schema' tests/ phase0/`（零命中）、`git log -- docs/mobile-test-agent-phase3-design.md`（末次改动即 `66cb8d5`）。
- basetemp 用 `./out/_ptrev31*`，已逐个删除；工作区**仅剩** `?? .mcp.json` / `?? .zcodeignore` / `?? docs/handover.md`（三者均为既有未跟踪物，**未动**）与本文件。
