# P3 数据审计 / 任务记账（Mobile Test Agent）

> 本文件是 **P3 的任务级记账**（沿用 `docs/p2_data_audit.md` 的惯例）：每个任务
> 交付时追加「完成记录」（决策、偏离 plan 之处、实测 pytest 数），每轮评审修订
> 再追加一段。**跨会话都要遵守的约定与教训在 `.workbuddy-ai/memory/MEMORY.md`**，
> 本文件只记「这一次做了什么、和计划差在哪、实测多少」。

## ⚠️ 更正记录（review_p3_task11 P3-2：提交信息已不可改，在此更正）

`e55c119`（`feat(p3): agent db + asset tiers + task models (P3-01)`）的提交信息有
**两处数字错**，实现本身是对的：

| 提交信息声称 | 实测 | 说明 |
|---|---|---|
| 设计 §10.1 工具全集 **24 个** | **22** | design §10.1 原文解析 22、`TOOL_ASSET_TIER` 22、测试常量 `DESIGN_10_1_TOOLS` 22——**三处集合完全相等**。是提交信息单方面夸大，不是代码缺了两个工具 |
| 全量 **1243** collected（1242→1243，+23） | **1265** | 同句自相矛盾（`1243-1242=1≠23`）。**1242（`4cea731` 基线）+ 23（本提交两文件实测收集数）= 1265**，与实测完全吻合——基线与增量都对，只有末值写成了 1243 |

**后续引用基线时用 1265，不要沿用 1243。**

---

## Task 1.1 完成记录（P3-01 agent.db + Agent 数据模型 + 资产权限分级，2026-10-08）

**Objective**：设计 §4 / §9.2 / §10.1 / §11 —— agent.db 首批三表 + 四级资产权限
（F2 落点）+ 九态词表。

### 交付

- `agents/models.py`：`AssetTier` 四级枚举；`TOOL_ASSET_TIER`（设计 §10.1 全集
  **22 个**工具的单一真值源，值域只有 READ_ONLY / CANDIDATE）；`AgentState` 九态；
  `AgentTask` / `AgentTraceEntry`（pydantic `extra=forbid`）。
- `agents/storage.py`：`SQLiteAgentStore`（构造即迁移；单写者；`agent_trace`
  追加式）。
- `agents/migrations/001_agent_schema.sql`：**只建三表**（`agent_tasks` /
  `agent_trace` / `test_plans`）——`test_candidates` / `discovery_events` /
  `bug_candidates` 随 M3/M4/M5 各自定型（P2 Task 1.2「表结构随实现定型，不提前
  冻结」的教训）。
- `agents/__init__.py`、`tests/unit/test_agent_models.py`、
  `tests/unit/test_agent_store.py`。
- 设计文档与实施计划随首个 P3 commit 入库（`phase3-design.md` /
  `phase3-plan.md` / `phase3-plan_by_claude.md`）。

### 关键决策

- **工具归属按「资产的读写视角」而非「App 副作用视角」**：`tap` / `input` /
  `swipe` 等执行类工具不创建也不修改任何资产（App 侧风险由 F1 Guard 拦），故
  `READ_ONLY`；`run_candidate_test` 写候选资产的 `dry_run_status`，故 `CANDIDATE`。
  这条口径决定了 `TOOL_ASSET_TIER` 的值域**只有前两级**（PRODUCTION / CRITICAL
  工具在构造上不存在——F2）。
- **`state` 的 SQL `CHECK` 是加固，不是设计原文**：设计 §11 的 `state TEXT` 可空，
  实现给 `NOT NULL DEFAULT 'IDLE'` + `CHECK` 词表。词表与 `AgentState` 是**同一
  词表的两处字面量**，一致性由测试钉住（见评审修订 P3-3）。
- **`ESCALATED` 不是错误态**：必附上下文供人工接手（M6 语义，词表先定型）；
  docstring 显式承接，避免 M6 读成失败路径。
- **转换表 M6 才强制**：store 只做枚举校验，不做转换校验——不为了让状态机
  「看起来完整」而提前实现 M6 的语义。

### 实测

- 全量 pytest **1265 passed**（关 FS 钩子，排除 2 个在飞的 Task 1.2 红测试；
  上一提交 `4cea731` 基线 1242，本提交两文件收集 +23）。
- 并发 2×50 `append_trace` → 100 行且 `(w, i)` 组合**恰 100 个**（不是「行数对但
  内容丢」）。
- 迁移幂等重放：二次构造不炸、`schema_migrations` 仍 1 行。

---

## Task 1.1 评审修订记录（review_p3_task11 收口，2026-10-08）

评审「有条件通过」：`e55c119` 本身无 P1/P2，但**核销对象 `4cea731` 引入 1×P2
回归**（`trace_history` 的 `app_build` 过滤键被静默忽略）。合计 **2×P2 + 5×P3**。

### P2-1 `trace_history` 的 `app_build` 过滤键被静默忽略（`4cea731` 引入）

- 症状：`experience/knowledge.py` 仍算出 `app_build = filters.get("app_build",
  self._app_build)`，但读取改走 `self._read_steps()`，而后者写死
  `read_trace_steps(..., app_build=self._app_build)` → **第 104 行的值再也没被
  读过**（AST 取证：只赋值未读取 `['app_build']`）。
- 后果两个方向都误导人：**单 build 库**给错数据不报错（`{"app_build": "999"}`
  静默返回库里那个 build 的 steps）；**多 build 库**报「用 `app_build=` 过滤」，
  而调用方**正是这么做的**。而单 build 库下「恰好返回正确数据」正是现有测试
  （只覆盖 `{}` / `limit` / 未知键）漏网的原因。
- 修法：`_read_steps(app_build=_UNSET)` **按次覆盖**；新增模块级哨兵 `_UNSET`
  ——**不能**用 `None` 兼作默认值，因为 `read_trace_steps(app_build=None)` 的语义
  是「取全部 run」，而「没给」应落到构造值，混用会让 `{"app_build": None}`
  （显式要求不过滤）被静默改写。顺带给 `app_build` 加**值类型闸门**
  （非 str/None → `ValueError`；与 `limit` 同款纪律：非 str 绑进 SQL 等值比较会
  一条也匹配不到，「过滤生效了只是库里没有」与「参数是坏的」长得一样）。
- 补 4 条集成测试（双 build 库：1025 跑 2 步用例、1026 跑 3 步用例——**steps 数
  不同**才能把「过滤生效」与「静默返回另一个 build」在断言上分开）。探针复现：
  回退修复后两条断言立刻红（`2 != 3`、`[...] != []`），与评审的发现逐字吻合。

### P2-2 单写者事务在异常路径上泄漏进程锁（→ 抽公共实现）

- 症状：`agents/storage.py::_Tx.__enter__` 把 `_connect()` 与
  `BEGIN IMMEDIATE` 放在保护区间**之外**；`acquire()` 无超时、`__exit__` 不会被
  调用（异常发生在进入 `with` 之前）→ **该 store 之后每一次写都永久阻塞**。
- A/B 探针（本机复现，真锁 + 真 SQLite）：

  | | 旧形状（`_Tx`） | 新形状（`write_tx`） |
  |---|---|---|
  | 写结果 | `OperationalError: database is locked` | 同 |
  | 失败后锁仍被持有 | **True** | **False** |
  | 释放外部锁后再写一次 | **2s 内未返回（永久挂死）** | 正常 |

- 修法（不止挪一行）：**抽 `source/sqlite_tx.py::write_tx`** 作单写者事务的
  唯一实现，三处 store 各留一个两行转口：
  - `experience/store.py`（原 `@contextmanager`，`_connect()` 也在 `try` 之外
    ——同一个窄口）；
  - `agents/storage.py`（原手写 `_Tx`，就是本 P2 的现场）；
  - `graph/storage.py`（原 `with conn:`，**无进程锁、无 `BEGIN IMMEDIATE`**
    ——第三形态，本次一并补齐）。
  这同时还掉了 `MEMORY.md` 里登记的那笔欠账（「单写者事务样板有三处 → 待抽
  `source/sqlite_tx.py`」）。
- 补测：`tests/unit/test_sqlite_tx.py`（5 例：成功提交 / 体异常回滚 / `connect`
  抛异常 / `BEGIN IMMEDIATE` 冲突 / 并发 2×50 不丢不重）+ 三个 store 各一条
  「冲突后锁已释放且还能再写」的回归测试。

### P3-1 `agents/` 三处 docstring 描述的是「P3 完成态」

| 位置 | 原声称 | 处置 |
|---|---|---|
| `agents/storage.py` | 「默认 out/agent.db，**CLI --agent-db 可覆盖**」 | 补落点：新增 `DEFAULT_AGENT_DB` 常量（单点，与 graph 侧 `DEFAULT_GRAPH_DB` 同款）+ `db_path` 默认值；CLI 接线归 **Task 2.4**，docstring 写明「尚未接线」 |
| `agents/__init__.py` | 「任何会碰 App 的动作都经 agents/tools.py，**不存在绕过路径**」 | 改成**目标**表述并标注归 **Task 1.3** |
| `agents/models.py` | 禁用名单「由 CI 门禁测试 `test_tool_allowlist_gate.py` **钉死**」 | 该文件归 **Task 1.3**；写明当前由 `test_agent_models.py` 的同一批断言临时兜住 |

- 补测：`test_default_agent_db_is_a_real_single_point`（常量值 + 包门面与模块
  同一对象 + `SQLiteAgentStore()` 真的在默认路径建库）。
- 对照：graph 侧是**先有代码再有话**（`DEFAULT_GRAPH_DB` + CLI 真的接了 default）
  ——本次把 agent 侧拉齐到同一状态。

### P3-3 `agent_tasks.state` 的 SQL CHECK 词表无测试钉住

- `001_agent_schema.sql` 的 `CHECK (state IN (…))` 与 `AgentState` 是同一词表的
  两处字面量（当前一致，各 9 值），但**没有任何测试**把两者绑在一起——M6 加第
  10 态若忘改 SQL → `IntegrityError`（loud、不静默坏数据，故定 P3）。
- 修法：新增 `test_state_check_constraint_matches_enum`（解析 `sqlite_master`
  的 DDL，断言 CHECK 集合 == `{s.value for s in AgentState}`，漂移时**双向差集
  都报出来**）+ `test_state_check_rejects_bad_value_written_by_raw_sql`（绕过
  Python 直接写坏值 → `IntegrityError` 且锁不泄漏、坏值没落库）。

### P3-4 `AgentTaskState` 名实不符

- 原实现是「普通类 + 9 个别名属性」：`issubclass(AgentTaskState, Enum)` 为
  **False**、不可迭代、`AgentTaskState("IDLE")` 抛 `TypeError`——名字像 Enum 却
  不是类型，容易被写成类型标注 `state: AgentTaskState`。
- 修法：`AgentTaskState = AgentState`（**真别名**，一行，语义完全相同）。测试从
  「`AgentTaskState.IDLE is AgentState.IDLE`」加强到 `is` 同一对象 +
  `issubclass(..., Enum)` + 可构造 + 可迭代。

### P3-5 `001_agent_schema.sql` 头注释未列「与 design §11 的差异」

- 项目既有惯例（`review_p2_task12` P3-1）：「差异要么列全，要么写『其余与设计
  一致』」。
- 修法：头注释补一张差异表（8 处加固：4 处 `NOT NULL`/`DEFAULT`、`state` 的
  `CHECK`、`created_at NOT NULL`、2 个索引）+ 一句「其余字段与设计逐字一致」+
  说明为何 `start_time`/`end_time`/`outcome` **保持可空**。

### 顺手修的 nit

`experience/knowledge.py` 的 `RuntimeGraph` 类型标注从 `from graph.diff import`
改为**定义处** `from graph.models import`——知识层不必为了拿一个模型类型而依赖
diff 模块（评审 §0 指出，未列为发现但一处即改）。

### 实测

- 全量 pytest **1280 passed**（本轮 +15：knowledge 4 + sqlite_tx 5 +
  agent_store 5 + graph_builder 1；`test_agent_models.py` 的加强不增计数）。
- P2-1 / P2-2 两处都有**回退探针**：回退修复后对应断言立刻红，与评审的探针
  逐条吻合。
