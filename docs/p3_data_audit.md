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
  回退修复后**三条**断言立刻红，与评审的发现逐字吻合：

  | 测试 | 失败形态 |
  |---|---|
  | `test_trace_history_app_build_filter_is_honored` | `2 != 3`（静默返回另一个 build 的 steps） |
  | `test_trace_history_unknown_build_is_empty_not_another_build` | `[...] != []`（库里没有的 build 也返回数据） |
  | `test_trace_history_app_build_type_is_gated` | `DID NOT RAISE ValueError`（类型闸门也没生效） |

  ⚠️ **数字更正（review_p3_task11_final P3-3）**：本行原写「两条」，实测 **3 条**
  ——第 3 条（类型闸门）的失败形态是 `DID NOT RAISE` 而非「断言值不等」，我当初
  只数了后者。教训：写「回退后 N 条红」时，**`DID NOT RAISE` 类失败也算红**。
  （提交信息已不可改，此处的文档是可改的——它正是「给后来者的实测真值源」，
  数字偏小会让后来者以为覆盖更弱。）

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

---

## Task 1.1 收口评审修订记录（review_p3_task11_final 收口，2026-10-08）

评审结论：**通过** —— 上一轮 2×P2 + 5×P3 **7/7 全部核销、无遗留**；两处 P2 的修法
被记为「优于评审建议」。本轮新增 **0×P2 + 3×P3**，都是**新引入的收口面**，无回归。

### P3-1 `source/sqlite_tx.py` 的 `finally` 把 `close()` 放在 `release()` 之前且无保护

- 症状：`finally: if conn is not None: conn.close()` 后紧跟 `lock.release()` ——
  `close()` 一旦抛异常，`release()` **永不执行**，锁照样泄漏。这与 P2-2 是
  **同一个失败模式，只差一层**（P2-2 修的是「`_connect()` / `BEGIN IMMEDIATE` 在
  保护区间外」），而本模块的**全部存在意义**就是这条不变量，docstring 却把话说到
  最满（「必然 close + release」「任何早退路径都不得泄漏事务或锁」）。
- 修法：`try: close() finally: release()`（`release()` 在自己的 `finally` 里）。
  docstring 补一段「为什么 close() 要在 release() 的保护里」+ **诚实边界**。
- **诚实边界（评审探针③，我在测试 docstring 里也写明）**：真 sqlite3（CPython 走
  `sqlite3_close_v2`——延迟关闭、返回 `SQLITE_OK`）**不会在 `close()` 上抛**，所以
  只能用**替身**复现。触发它需要将来把 `_connect` 换成返回包装对象/`Connection`
  子类，或 sqlite3 行为变化——但**爆炸半径是三个库**（收敛的代价），一行成本，
  就地修。
- 补 2 条测试：`test_lock_released_when_close_raises`（替身，`close()` 抛）+
  `test_lock_released_when_body_and_close_both_fail`（真连接 + `close()` 抛，断言
  锁释放**且事务已回滚**）。回退探针：两条都在 `not lock.locked()` 上红。

### P3-2 `agents/storage.py::_connect` 复制了 experience **已修掉**的模式

- 三处形态（评审探针①实测 `PRAGMA busy_timeout` 都是 5000ms → 功能等价）：

  | 位置 | 原写法 | 实际生效 |
  |---|---|---|
  | `agents/storage.py` | `timeout=30` + `PRAGMA busy_timeout=5000` | 5000 —— `timeout=30` 是**死字面量** |
  | `experience/store.py` | `timeout=5` + PRAGMA | 5000 |
  | `graph/storage.py` | 不传 `timeout`、不设 PRAGMA | 5000（默认值） |

- **这是「已知问题的复制」**：`experience/store.py` 的注释原文就记着
  「review P3-2：connect `timeout=30` 与 PRAGMA 5000 曾意图不一致，后设者胜靠阅读
  顺序」——同一个模式当时被报为 P3 并**已修**（`30` → `5`）；P3-01 新写
  `agents/storage.py` 时又抄了一遍。
- 修法：`agents` 对齐 experience（`timeout=5` + 同一句注释）；`graph` 补
  `timeout=5` + `PRAGMA busy_timeout = 5000`，**三处字面量一致**。
- 补 2 条测试：
  - 行为面 `test_busy_timeout_is_consistent_across_the_three_stores`（三处都是
    5000ms）；
  - 源码面 `test_store_connects_use_the_same_timeout`（**AST**：三处各**恰一个**
    `sqlite3.connect`，都写了 `timeout=5`）。
    ⚠️ 初版用「`"timeout=30" not in src`」的**纯文本**判据 → 被 experience 注释里
    为解释该模式而提到的数字**误报**；改成 AST 只看真正的 `timeout=` 实参。
    教训：「源码里不许出现某字符串」要先问「注释里为解释它而提到它算不算」。

### P3-3 回退探针的红灯数：声称「两条」，实测 **3 条**

- `test_trace_history_app_build_type_is_gated` 的失败形态是
  `DID NOT RAISE ValueError`，我当初只数了「断言值不等」的两条。已在本文件
  Task 1.1 评审修订段更正为三条并列出三种失败形态（提交信息不可改，文档可改）。
- **教训（已并入 `MEMORY.md`）**：写「回退后 N 条红」时，**`DID NOT RAISE` 类失败
  也算红**。

### 顺带落地评审建议动作 5：把「单写者唯一入口」固化成机械守卫

评审把它列为**可选**，并提醒「复用 `tests/unit/test_repo_hygiene.py` 的手法，别再开
第二个体检入口」。已在**同一个文件**里加两条（未新增体检入口）：

1. `test_store_writes_are_inside_the_write_tx_region`（**AST**）：三处 store 的
   写语句（`execute*` 首参是以 `INSERT/UPDATE/DELETE/REPLACE` 开头的字符串常量）
   **必须落在 `with self._write_tx()` 的行区间内**；带防空转下界（实测 23 条：
   experience 13 / agents 4 / graph 6，下界取 18）。
   只认 `ast.Constant` —— 隐式字符串拼接在解析期已折叠（多行 SQL 照样命中），而
   `"… WHERE " + where` 这类动态拼串与 `f"…"` 会跳过，避免误判读语句。
2. `test_write_tx_boilerplate_is_not_copied_back`：三处 `_write_tx` 必须仍是
   `write_tx(self._write_lock, self._connect)` 转口，且源码里不得出现
   `_write_lock.acquire()` / `"BEGIN IMMEDIATE"`。

**探针验证两条都会红**：注入「把一条 UPDATE 挪出保护区」→ 第 1 条指名报出
`agents/storage.py:110`；注入「抄回 `_write_lock.acquire()`」→ 第 2 条报出
`agents/storage.py 又抄回了手写锁样板`。

**为什么值得加**（不只是「照做」）：同一次评审的 P3-2 就是**「已修过的模式被复制」**
的实例——这个类已经复发过一次。收敛的收益是「这类错误只有一处可能犯」，代价是
「那一处犯错的爆炸半径 = 全部三个库」；机械守卫把「记得别抄」从纪律变成**会红的
测试**。

### 未处理（登记）

- **`db_path` 有默认值之后的脚枪面**（评审 §4）：任何未来「忘记传路径」的调用都会
  写 **CWD 的 `out/agent.db`**。仓库里 `out/experience.db` 就是这么落地的（mtime
  2026-10-06 23:14，非测试所写）。当前唯一无参调用点有 `monkeypatch.chdir` 保护。
  **定档：不撤默认值**（与 `SQLiteExperienceStore` 的 `out/experience.db` 同款，
  可变状态库给默认值便于开箱即用），但**测试夹具一律显式传 `tmp_path`**；Task 2.4
  接 CLI `--agent-db` 时一并复核（已写进 `MEMORY.md`）。
- **`tracer/storage.py` 是第四个 SQLite 写者**（无进程锁、无 `BEGIN IMMEDIATE`）：
  `source/sqlite_tx.py` 的 docstring **明确把范围限定为「三个库」**（不夸大）；
  trace.db 是 append-only 流水库。属**存量（P1 期）**，是否收敛到同一纪律应由后续
  任务显式决策，**不顺手改**。
- **真机 gate**：`phase0/verify_p3_*.py` 尚不存在，Gate M1 属 Task 1.2/1.3 之后。
- 工作区仍有 2 个在飞的 Task 1.2 TDD 红测试（不在本提交范围，Gate M1 前必须收口）。

### 实测

- 全量 pytest **1286 passed**（本轮 +6：sqlite_tx 4 + repo_hygiene 2）。
- 回退探针：P3-1 的两条测试在锁泄漏断言上真红；两条新守卫在注入违规后真红并**指名
  报出文件与行号**。
- 三处 store 的 `PRAGMA busy_timeout` 实测均为 **5000ms**，`sqlite3.connect` 的
  `timeout=` 均为 **5**。

---

## Task 1.2 完成记录（P3-02 policy.yaml + 启动校验 F7，2026-10-08）

**Objective**：设计 §7.2/§7.4/§8.3/§10 —— 环境与预算约束集中一处，**启动时校验
而不是运行时拦截**。

### 交付

- `config/policy.yaml`（**首次入库**）：`autonomous` / `exploration` / `escalation` /
  `planner_weights` / `diagnosis_evidence_weights` / `evidence` 六段，值 = 设计初版值。
- `agents/policy_config.py`（新）：`PolicyConfig`（pydantic，`extra=forbid` +
  `frozen`）+ 三个真值源常量 + `load_policy(path)` + `PolicyConfigError`。
- `cli/main.py`：`AUTONOMOUS_COMMANDS`（自主命令名单**单点**）+
  `_check_autonomous_env(policy, *, env_kind, command)`（F7 公共前置）。
- `agents/__init__.py`：导出 `load_policy` / `PolicyConfig` / `PolicyConfigError` /
  `DEFAULT_POLICY_PATH`（与 models/storage 同款包门面）。
- 测试：`tests/unit/test_policy_config.py`（**17 例**：TDD 起点 7 + 本任务补 10）、
  `tests/fault_injection/test_p3_matrix_m1.py`（矩阵 #1/#4，**16 例**：TDD 起点 10 +
  本任务补 6）。两文件合计 **33**（与全量 +33 对账）。

### 关键决策

1. **`load_policy` 的签名是 `(path)` 而不是 plan 里写的 `(args)`。** plan Task 1.2
   的 Files 段写「`_check_autonomous_env(args)`：解析 env_kind（复用 `--env-kind`
   既有语义）」——但先行的 TDD 测试（本任务的工作区起点）定的是
   `_check_autonomous_env(policy, *, env_kind, command)`。**测试是契约**：把
   「解析 args」与「判定」拆开，判定这一层才能脱离 argparse 单测（F13 精神），
   而 `env_kind`/`command` 的来源由各命令在调用点解析（Task 2.4）。
   **登记为对 plan 的一处偏离**。
2. **`production` 一票否决且无豁免参数**：`mta run --allow-production` 是 P1/P2 的
   **单次运行总闸**（允许在 production 跑**已审核**用例），自主命令是「在 production
   下整体不存在」——**两条规则**，故本函数签名里没有也不该有 `allow_production`。
   用 `inspect.signature` 把它钉成机械断言（`test_gate_has_no_exemption_parameter`）。
   拒绝消息里**点名那个不适用**的 flag，否则读者会去找一个不存在的豁免参数。
3. **`env_kind` 复用 P1 的 `executor.guard.EnvKind`**（字符串与枚举都收）——F7 原文
   就是「环境隔离复用 P1 `env.kind`」，不新建环境枚举。
4. **权重表校验「键集完全一致」**：`diagnosis_evidence_weights` / `planner_weights`
   是**整表替换**（YAML 不做深合并），少写一个键不会立刻报错，而会一路走到
   `score_evidence` / `priority_score` 里变成运行时 `KeyError`（离现场很远）。
   既然 F7 的精神是「启动时校验」，就挡在加载期（漏/多都点名）。
5. **`PLANNER_WEIGHTS` 的数值不在设计里**：设计 §5.2 只说「权重是初版，需在真实
   数据上校准」，**没给数**。plan 要求本文件先落一段（M2 初版），故取一组可解释的
   初值（impact 3 / history 2 / risk 5）并在 yaml 与模块 docstring 里**显式标注
   「设计未给数值」**——不假装精确（沿用 P2 验证阈值的态度）。校准后回填设计。
6. **配置文件缺失 = 全默认值，不是错误**（矩阵 #4 的回归底线）：policy.yaml 是 P3
   新增层，P2 的既有命令不该因为它不在而行为改变。配套加了
   `test_shipped_policy_yaml_equals_builtin_defaults`——入库的 yaml 与内置默认值
   **逐字段相等**，否则「文件缺失」与「文件在」会给出两套配置，而「行为不变」的
   承诺只在其中一套下成立（以后校准初版值时**两处一起改**，本测试会拦住只改一处）。

### ⚠️ 一处必须说清的边界：`_check_autonomous_env` 目前**没有生产调用者**

自主命令（`plan`/`generate`/`explore`/`diagnose`/`agent`）在 **M2+ 逐个接线**（plan
第 12 节），Task 2.4（`mta plan`）是第一个。所以本任务交付的是**公共前置本身**，
它在 Task 2.4 之前只有测试调用者——这是 **plan 的安排，不是遗漏**。

为免它变成「写个没人调的函数就当已覆盖」（review_p2_task55 P3-4 的教训），做了两件
事：① `AUTONOMOUS_COMMANDS` 作为**单点名单**（接线时注册解析器与过前置都从它取）；
② 加了守卫 `test_every_wired_autonomous_command_passes_the_gate`——它扫描
`cli/main.py` 的 `cmd_<name>`，只要名字落在 `AUTONOMOUS_COMMANDS` 里就要求函数体里
出现 `_check_autonomous_env`。**这条守卫现在空转通过**（`cmd_plan` 还不存在），
Task 2.4 加 `cmd_plan` 的那天它开始工作。

### 实测

- 全量 pytest **1319 passed**（Task 1.2 新增 33；上一轮基线 1286 + 33 = 1319）。
  ⚠️ **本任务让全量 pytest 重新变绿**：此前那 2 个在飞的 TDD 红文件（收集期
  `ImportError`）现在有了实现，**跑全量不再需要 `--ignore`**——Gate M1 的「全量回归
  绿」这条判据从此可以取得。
- 真机路径探针（模拟 Task 2.4 的调用）：`production` × 五个命令名 → 全部拒绝且
  消息含 `F7`；`staging`/`sandbox` × 五个命令名 → 全部通过；`policy.yaml` 读到的
  值 = `exploration{100,600,20,3}` / `planner_weights{3,2,5}` / `threshold=10`。
- 全仓重复顶层定义体检：**零命中**。

---

## Task 1.2 评审修订记录（review_p3_task12 收口，2026-10-08）

评审「有条件通过」：功能声称**全部对账通过**（1319 / +33 / 设计值逐项 / 核销 3/3），
但「**启动时校验**」这一层的**守卫强度**有两处与自己的声称不符 → **1×P2 + 4×P3**。
两者当前都不改变运行行为（`_check_autonomous_env` 尚无生产调用者、权重表尚无消费者），
但都会在 **Task 2.4 接线那天变成真问题**，故在接线前收口。

### P2-1 「shipped yaml ↔ 内置默认值」的守卫**空转**

- 症状：两条 shipped-yaml 测试的断言目标（`enabled=True`、`max_steps=100`、
  `== PolicyConfig()`）**正是** `load_policy` 在「路径不存在」时返回的东西
  （`if not p.is_file(): return PolicyConfig()`）→ **文件在不在，结果一样**。
  评审探针⑨⑩：把默认路径指到不存在的文件、或把 CWD 换到 `/tmp`，两条测试**仍然全绿**。
  也就是说它们**既不能发现文件被删/改名，也不能发现 CWD 不对**——而「文件缺失 →
  默认值」的唯一守卫就是它们。
- 为什么定 P2：提交信息「关键决策 6」与审计文档都把这条测试写成**保证**
  （「以后校准初版值时必须两处一起改，本测试会拦住只改一处」）。**声称的能力 > 实际
  能力**，且修法两行——正是 `MEMORY.md` 记的那类错（「『固化』必须是一条会自动失败的
  测试」），与上一轮 P3-3（「声称两条实为 3 条」）同源。
- 修法：`_REPO = Path(__file__).resolve().parents[2]` + `path = _REPO /
  DEFAULT_POLICY_PATH` + **先 `assert path.is_file()`**（报错消息写明「其余断言会因此
  空转」）。**探针复现**：临时把 `config/policy.yaml` 改名 → 两条测试**真红**（修复前
  全绿）；改名还原。
- 顺带补了一条**能测出「默认解析真的读文件」**的测试
  （`test_default_resolution_actually_reads_the_default_path`）：把默认路径
  monkeypatch 到一个**值不同**的临时文件，断言读到的是文件里的值——只断言
  「`== PolicyConfig()`」是测不出这件事的，那正是空转的形态。

### P3-1 两张权重表的**值**没有任何值域闸门（负权重静默接受）

- 症状：`field_validator` **只校键集、不校值**，而同文件所有**标量段**都有 `ge=0`。
  评审探针⑪：`crash_detected=-1` / `llm_hypothesis_only=-99` / `impact=-1` / `risk=-100`
  全部**被接受**；对照组的 `exploration.max_steps=-1` 等三个标量**全被拒**。
- 口径不符：模块 docstring 与 yaml 头注释都写「未知键 / 错型 / **负预算** fail-loud」。
  消费者（`score_evidence` / `priority_score`）拿到负权重会算出**负分**，直接污染
  「归因是否达标」（F10）与「先做哪个」（§5.2）——P3 的定档理由是「现在还没有消费者」，
  M2 接线即升级为 P2。
- 修法：`_NonNegInt = Annotated[int, Field(ge=0)]`，两个权重字段改成
  `dict[str, _NonNegInt]`；yaml 与 docstring 的「负预算」改「负值」（让声称与覆盖面
  一致）。补 4 条测试（负权重 3 例 × 两张表 + 标量对照）。

### P3-2 `load_policy` 分不清「文件缺失」与「路径拼错」，且默认路径是 CWD 相对

- 症状：`if not p.is_file(): return PolicyConfig()` —— 缺失、拼错、是目录、`None`
  **全走这一条**。评审探针③：`config/polcy.yaml`（拼错）与 `config/policy.yaml`
  （正确）**长得一模一样**。
- 两个后果：① **生产侧**：从仓库外的目录跑 `mta plan` 会**静默拿到内置默认值**——
  用户的 policy.yaml 被无声忽略；这不是「文件缺失 → 默认」那条回归底线（那是设计
  要求的），而是「**你以为你在用配置文件，其实没有**」。② **将来**：加了
  `--policy` 之后，一个拼错的路径会静默退回默认值，而 `--policy` 的全部意义就是
  「我指定了配置」。
- 修法（用哨兵把两件事分开，与 `experience/knowledge.py::_UNSET` 同一手法）：

  | 入参 | 行为 |
  |---|---|
  | 不给（`_UNSET`） | 读 `DEFAULT_POLICY_PATH`（相对 CWD）；读不到 → 内置默认值（矩阵 #4） |
  | `None` | **显式要求内置默认值**，不读任何文件 |
  | 存在的文件 | 读它（未知键/错型/负值/权重键集不符 → `PolicyConfigError`） |
  | 显式给了却不存在 | **`PolicyConfigError`**（fail-loud） |

  `DEFAULT_POLICY_PATH` 的 CWD 相对**保持不变**——那是本项目的既有约定（`suites` /
  `out/trace.db` 同款，P1 起「用旗标/相对路径显式给」），而且「用户在自己项目里放一份
  配置」正是想要的行为；改成锚 `__file__` 反而与全局不一致。**把事实写进 docstring 与
  yaml 头注释**，而不是悄悄改语义。
- ⚠️ **两处 TDD 起点的测试因此要改断言**（`test_missing_file_yields_design_defaults`
  与 `test_matrix_4_missing_policy_yaml_defaults`）：它们原先把「显式传一个不存在的
  路径」当作矩阵 #4 的场景，而那是**旧语义**。已改为「把默认路径指到不存在的位置」——
  矩阵 #4 的**意图**（P2 既有命令行为不变）完整保留，只是换了正确的触发方式。
  这是评审明确要求的方向（P3-2 的修法），不是为了让测试变绿而改断言。
- 补 4 条测试：默认解析真的读文件 / 默认路径缺失 → 默认值 / `None` 不读文件 /
  显式缺失 → fail-loud。

### P3-3 `env_kind` 的来源无落点；docstring 指向不存在的 `args.policy`

- 症状：plan 要求「解析 env_kind（**复用 `--env-kind` 既有语义**）」，但 `--env-kind`
  **只挂在 `run` 子命令**上，五个自主命令**没有任何 env 来源**；`--policy` / `args.policy`
  **全仓（含 plan 与 design）零命中**，只出现在 docstring 第 384 行。
- 这是 `MEMORY.md` 那一课的**复现**：「文档声称的能力必须有落点——写 docstring 前
  grep 自己提到的符号；没落地的写将来时 + 任务号」。
- **更实质的一半**：若 Task 2.4 顺手写 `env_kind="sandbox"`（因为当时没别的来源），
  **F7 就退化成永真**——判据还在、测试还绿、但永远不拒。
- 修法：
  1. docstring 的 `load_policy(args.policy)` 改成**将来时 + 任务号**，并明写
     「`--policy` 目前不存在」；
  2. 抽 `cli/main.py::_resolve_env_kind(args)` 作 `--env-kind` 的**唯一解析点**，
     **`cmd_run` 改用它**（所以它有真实生产调用者，不是悬空 helper）；自主命令的
     `--env-kind` 与 `run` 同款，定为 **Task 2.4 的接线前置**（已写进 plan Task 2.4，
     连同 `--policy` 的拍板项与「第一行过前置」的顺序要求）；
  3. 守卫 `test_every_wired_autonomous_command_passes_the_gate` **加强**：除「必须调
     前置」外，还要求**`env_kind` 实参不是字面量**（AST 判定，`env_kind=args.env_kind`
     合法、`env_kind="sandbox"` 红），且 `command=` 的字面量必须与函数名一致（否则
     拒绝消息会指名错的命令）。补 `test_env_kind_resolution_is_shared_with_run`
     钉住「解析只许一处实现」。
- **附注（给 M4 留的一条）**：拒绝消息目前对用户说「请在 sandbox **或 staging** 下
  运行」，而 plan #3 与 design §7.4 对 Exploration 要求 **sandbox-only 不能有例外**
  ——已写进 plan **Task 4.6（`mta explore`）**：落地时必须让 staging 也被拒，否则
  文案与行为打架。

### P3-4 「错型 fail-loud」的实际覆盖面比声称的窄

- 症状 A（pydantic 默认 lax）：`"100"→100`、`3.0→3`、`"true"→True`、**`true→1`**
  全部静默强转。最尖的一例是 `max_steps: true → 1`：F11 规定超预算即
  `*_BUDGET_EXCEEDED` 并停止，一个静默变成 **1 步**的预算会让探索在第一步就停——
  **配置错误表现为功能退化，而不是报错**。而 `test_wrong_type_fails_loud` 用的
  `"abc"` 是唯一会被拒的那种，所以实际覆盖面没被测试暴露。
- 症状 B（前置函数的**第三个出口**）：`_check_autonomous_env` 的 docstring 说
  「任一不过即 `SystemExit`」，但 `EnvKind(bad)` 抛的是 **`ValueError`**（探针⑤：
  `'PRODUCTION'` / `'Prod'` / `''` / `None` / `'sandbox '` 六种坏值）。后果：CLI 上
  打一整段 traceback，而且**任何用 `except Exception` 兜底的调用方都会把否决一起
  吞掉**。安全闸门只许一个出口。
- 修法：`_Frozen` 加 `strict=True`（一行，全字段生效）；`EnvKind(env_kind)` 包进
  `try/except ValueError` → `SystemExit`（消息列出合法值）。补测试：7 例 lax 强转
  全拒 + YAML 布尔（`yes`）不被误伤 + 6 例坏 `env_kind` 全走 `SystemExit` + 坏值
  消息列出合法环境。

### 未处理（登记，评审 §4 的存疑项）

- **`frozen` 是浅冻结**：属性重绑与嵌套模型赋值被拒，但 `planner_weights["impact"] =
  999` 成功。好消息是 `default_factory` 保证**实例间与模块常量都不共享**（改一个实例
  污染不到真值源），故不构成发现；但「dict 内容可改」这件事**没有测试记录**。若将来
  要把配置当只读对象传进引擎，需 `MappingProxyType` 或语义升级——**独立立项**。
- **真机 gate**：`phase0/verify_p3_*.py` 尚不存在；M1 的真机/Sandbox 集中点在 Task 3.4
  之后，本轮只做库内 + 纯函数层验证。

### 实测

- 全量 pytest **1343 passed**（本轮 +24；上一轮 1319 + 24 = 1343）。两文件收集数
  **33 / 24**（合计 57）。
- **探针复现 P2-1**：临时把 `config/policy.yaml` 改名 → 两条 shipped-yaml 测试
  **真红**（修复前全绿）；改名还原。
- 全仓重复顶层定义体检：**零命中**。

---

## Task 1.3 完成记录（P3-03 Agent 工具层 / F2/F12 唯一通路，2026-10-08）

**Objective**：设计 §10/§10.1 —— `ALLOWED_TOOLS → TOOL_ASSET_TIER → Executor+Guard`
的**唯一分发层**；被禁工具**在构造上不存在**。

### 交付

- `agents/tools.py`（新，唯一分发层）：三张表 + 注册表 + `AgentToolkit.call`。
- `tests/unit/test_agent_tools.py`（**53 例**）：行为面（plan Steps 的五条验收）。
- `tests/unit/test_tool_allowlist_gate.py`（**16 例**，**CI 门禁**）：静态/结构性断言。
- `agents/__init__.py`：导出三张表 + 工具层异常；**包级 docstring 的「尚未落地」
  段落删掉**（那两件就是本任务），改成「唯一通路」的正式表述 + 一句边界说明。
- `agents/models.py`：docstring 里「CI 门禁归 Task 1.3」改成「已落地」。

### 三张表 + 注册表（都不是手抄）

| 表 | 内容 | 来源 |
|---|---|---|
| `ALLOWED_TOOLS` | 22 | `frozenset(TOOL_ASSET_TIER)`（**派生**，门禁 A 钉住） |
| `GUARDED_TOOLS` | 10 | 设计 §10.1 中间组「执行（仍经 Guard）」 |
| `BANNED_TOOLS` | 8 | 设计 §10.1 末句逐字 |
| `TOOL_METHODS` | 22 | 注册表；键集 == `ALLOWED_TOOLS`（门禁 B） |
| `NOT_WIRED` | 11 | 声明式「还没接线 + 归哪个任务」表 |

**已接线 11 / 未接线 11**（`_TOOL_METHODS` 键集仍是全集，未接线的走
`ToolNotWired` 明确报错——**不静默返回空结果**）。

### 关键决策（都写进了模块 docstring）

1. **`GUARDED_TOOLS` 的边界 = 设计 §10.1 的分组**：只读组（`get_*`）与候选创建组
   （`create_*`）**不经 Guard**。Guard 的三条规则都是关于**动作风险**
   （`effective_risk` + action），对纯读取没有判据可用；`get_current_screen` 确实
   调 WDA，但它的「风险」不是风险等级能表达的——环境侧的兜底是 **F7 的启动期
   校验**（两道闸不重叠、不互相替代）。有专测钉住「只读工具在 production Guard 下
   也照常返回，且 `guard` 字段为 None」。
2. **BLOCK 是「结果」不是「异常」**：走 `ToolResult(ok=False, guard="BLOCK",
   failure_type="SECURITY_BLOCKED")` ——与 `StepRunner.run_step` 的
   `SECURITY_BLOCKED` 同款（返回而非抛）。这样「同一 Guard 下工具层与 run_step
   结论一致」是**可比对**的（F1 的一致性测试就是比这个字段），也让 Agent 循环不必
   用 except 表达预期结果。**异常只留给配置/编程错误**：`ToolViolation`（不是工具）、
   `ToolNotWired`（没实现）、`ToolDependencyError`（依赖没注入）、
   `ToolkitAuditError`（BLOCK 却无法留痕），以及 `ValueError`/`TypeError`（坏参数）
   与 `InfraError`（设备故障，与 `run_step` 同款照原样上抛）。
3. **审计不能静默丢弃**：BLOCK 时若没配 `agent_db`/`task_id`，**抛
   `ToolkitAuditError`** 而不是「算了不记了」——被拦的动作没有痕迹，事后无法回答
   「它想干什么」（F1 的审计根基）。有专测。
4. **审计与返回值都不回显 `input` 的值**：值可能是密码/验证码（14.4 的
   SENSITIVE/SECRET 遮蔽）。审计只记 tool/action/screen/element/risk/拦截原因，
   `input` 的返回值只给 `value_len`。两条专测。
5. **`_guard_context` 要认两种 resolve 产物**：`Repository.resolve` 对
   `screen:X` 返回 `EffectiveScreen`（**没有 `.screen` 属性**，只有
   id/marker/kind_hint）——直接摸 `eff.screen` 会让 `wait screen:X` 在
   AttributeError 上炸（**写实现时真踩了**，测试当场抓到）。判别式沿用
   `cli/pipeline._locator_for` 的 `hasattr(eff, "marker")`。
6. **风险推导与 `cli/pipeline._prepare` 同式**：
   `effective_risk(element=eff.risk, element_id=eff.id)`——不另立一套（F1）。
   有 spy guard 的专测断言「metadata 的 `risk: HIGH` 确实进了 Guard」。

### ⚠️ 两处必须说清的边界

**① 不持有 `knowledge` / `budget`（对 plan Files 段的一处偏离）**：plan 把这两个
列进了 `AgentToolkit` 的持有物，但**本任务没有任何工具消费它们**
（`get_relevant_*` / `get_graph_neighbors` 归 Task 2.2 的 KnowledgeSources 装配；
`budget` 是 LLM 调用配额，而工具层是确定性的、不调 LLM）。按「文档声称的能力必须
有落点」的纪律**等有消费者时再加**，不留「已在生效」的假象（对照
`GuardContext.data_class` 的处置）。已在模块 docstring 显式登记。

**② `AgentToolkit` 在 M2+ 之前没有生产调用者**（与 `_check_autonomous_env` 同款，
plan 的安排）：装配点（`cli/pipeline` 的 `deps` 或首个自主命令 `cmd_plan`）在
**Task 2.4**。本任务交付的是工具层本身 + CI 门禁；端到端可达性由 Gate M2 的
`mta plan` 提供。

### CI 门禁（`test_tool_allowlist_gate.py`）——plan 执行注意事项 #2 的落点

五道门禁 + 一条防空转：A 全集 = tier 表键集（防手抄第二份名单）／B 注册表键集 =
全集／C 禁用名与**三张面**（全集、注册表、tier 表）都不相交／D tier 值域无
PRODUCTION/CRITICAL（F2）／E 8 个禁用名在 `agents/**` 全包**只出现一次**且必须在
`BANNED_TOOLS` 字面量里（AST 扫**非 docstring** 的字符串常量——文档里提到禁用名
合法）／F 扫描范围自证（文件数与常量数下界 + `tools.py` 必须覆盖到）。

**探针验证门禁会红**：往 `TOOL_ASSET_TIER` 加 `delete_testcase` → **4 条**同时红
（A/B/C/E）；把 `tap` 标成 `PRODUCTION` → D 红。还原后全绿。

### 实测

- 全量 pytest **1412 passed**（Task 1.3 新增 **69**：行为 53 + 门禁 16；
  上一轮基线 1343 + 69 = 1412）。
- 两文件 `--collect-only`：53 / 16。
- 全仓重复顶层定义体检：**零命中**。
- 实现期由测试抓到的真 bug 一处：`EffectiveScreen` 没有 `.screen`
  （`wait screen:X` 的 Guard 输入构造会 AttributeError）。

---

## Task 1.3 评审修订记录（review_p3_task13 收口，2026-10-08）

评审「有条件通过 — 1×P2 + 3×P3」，上一轮 `a01fb35` 的 **5/5 全清**，功能声称逐项对账通过。
唯一 P2 出在**「Guard 到底跑不跑」的那张表**（`GUARDED_TOOLS`）**没有任何与设计原文对账的
钉子**。

### P2-1 `GUARDED_TOOLS` 是手抄且无逐字钉 —— 等长替换后 1412 条全绿

- 症状：`GUARDED_TOOLS` 是手写的 10 个名字字面量，它决定 `call` 里
  `if tool in GUARDED_TOOLS` 那一行**是否构造 `GuardContext` 并调 `guard.check`**
  ——「Guard 到底跑不跑」完全由它决定。而包级 docstring 把它写成**包级纪律**
  （「任何会碰 App 的动作都经 … → Executor+Guard」）。
- 评审探针（A/B，真改文件）：把 `"assert_text"` 换成只读的 `"get_logs"`
  （**等长 10**、仍是全集子集）→ **门禁 + 工具层 69 passed、全量 1412 passed，
  一条都没红**。此时 ① `assert_text`（带 target、风险由元素推导）**不再过 Guard**；
  ② `get_logs`（设计归「只读」组）**反而过 Guard**，与设计 §10.1 的分组、也与模块
  docstring 自己的边界声明矛盾。
- 为什么是 P2：这是**安全不变量的声称**却**没有落点**——正是 `MEMORY.md` 记了两次的
  那一课（`--agent-db` 一次、`_check_autonomous_env` 的 `args.policy` 一次）的**第三次**；
  修法方向明确、约 8 行；而且**同一文件里对「禁用名」做了这件事、对「Guard 名单」没做**
  ——防住了「给 Agent 加上危险工具」，没防住「**让 Guard 不再跑**」。
- 修法（三处）：
  1. 门禁文件加 `DESIGN_10_1_GUARDED`（设计 §10.1 中间组**逐字重打**，与
     `DESIGN_10_1_BANNED` 同一手法、**互不反推**）+ `test_guarded_set_matches_the_design_group_verbatim`；
  2. 补**行为面**的 `test_every_guarded_tool_actually_calls_the_guard`：名单里每个工具
     都必须真的触发 `guard.check`（静态对账挡「名单被改」，行为面挡「`call` 里的
     `if tool in GUARDED_TOOLS` 被挪走/短路」——两者缺一 Guard 都可能不跑）；
  3. 模块 docstring 的「三张表（键集都是**派生**，不是手抄）」措辞改掉——
     `GUARDED_TOOLS` / `BANNED_TOOLS` 是**手抄设计原文**（设计是散文，抄合理，但
     **手抄就必须有逐字对账的钉子**）。
- **探针复现**：等长替换后**全量 1 failed**（`test_guarded_set_matches_the_design_group_verbatim`），
  修复前是 1412 passed。

### P3-1 失败分类有**两个落点**，docstring 只说了外层；内层词汇有三套

- 症状：`wait` 超时 / `assert_*` 不过这两类最常发生的失败，分类在
  `value["failure_type"]` 而不是 `ToolResult.failure_type`；而 `ToolResult.failure_type`
  的 docstring 写的是「与 `steps.failure_type` 对齐」。同时内层词汇三套：
  `assert_*` 用 `passed` / `wait` 用 `satisfied` / `create_promotion_proposal` 用 `ok`
  （后者还与**外层** `ToolResult.ok` 撞词——探针：外层 `ok=True` 而 `value={'ok': False,…}`）。
- 修法：① `ToolResult` 加**只读属性** `step_failure_type`（两处落点合并成一个读取口，
  外层优先、两者互斥所以 `or` 不掩盖任何一类）+ 类 docstring 画出「哪一类在哪一层」的表；
  ② 内层词汇**统一成 `passed`**（`wait` 的 `satisfied` 与 `create_promotion_proposal`
  的 `ok` 一并改）——三个近义词并存时调用方总有一个会读错；③ 模块 docstring 加
  「判定类工具的 `value` 词汇（一套，不是三套）」小节。
- 补测：`test_step_failure_type_merges_both_places`（外层 / 内层 / 成功路径三种）。

### P3-2 `failure_type` 由**异常消息字符串**派生

- 症状：`"failure_type": str(e).split(":", 1)[0]` —— 能工作，但**结论依赖另一个模块的
  消息格式**（`executor/assertion.py` 的文案）。已有一层保护（测试用真异常类），
  不是空转，但分类是结构信息，不该由文案承担。
- 修法：类 → 名映射。⚠️ 实现时先写成**精确类查表**，结果被自己新加的测试当场抓到
  ——`except` 捕的是**子类**，精确查表会让子类掉进兜底名（**分类口径与捕获口径不一致**）。
  改成 `isinstance` 查表。测试 `test_assert_failure_type_does_not_depend_on_the_exception_message`
  用一个**改了文案的异常子类**构造，断言分类仍正确。

### P3-3 一处断言过宽

- `pytest.raises(Exception)` → `pytest.raises(ValueError)`（pydantic 的
  `ValidationError` 是它的子类）。原写法会把 `ToolDependencyError` / `TypeError`
  之类**无关**异常也算通过，与 docstring 的「非法值必须炸」不等义。

### 小观察（评审标「不建议单独立项」）—— 三处已改，两处登记

| 观察 | 处置 |
|---|---|
| 门禁 C 的「三张面」实际是 2 个独立面（`ALLOWED_TOOLS` 派生自 `TOOL_ASSET_TIER`，门禁 A 已钉住相等） | ✅ 措辞改掉（说明第三条是**冗余**而非第三张面，留着便宜） |
| 门禁 F 的自证下界偏松（`>=4` / `>50`，实测 5 / 393） | ✅ 按实测收紧到 `>=5` / `>300`，并**逐一列出必须覆盖的 5 个文件** |
| `screenshot_dir` 默认 `"out/screenshots"` 是 CWD 相对 | ✅ 写进类 docstring（与 `DEFAULT_POLICY_PATH` / `suites` / `out/trace.db` 同款） |
| 门禁 E 的扫描范围是 `agents/**`，`cli/` 等包看不见 | **登记**：当前工具层只在 `agents/`，可接受；**M2 装配 `AgentToolkit` 时复看一眼** |
| `_tool_*` 方法是公开可访问属性（`tk._tool_tap(...)` 会绕过 Guard） | **登记**：当前威胁模型下没问题（Agent 是 LLM，只能经 `call`）；**M2 起若有 Python 侧调用者需重新评估**（拆私有内部类 / 名字改写） |

### 实测

- 全量 pytest **1417 passed**（本轮 +5：工具层 53→55、门禁 16→19；1412 + 5 = 1417）。
- **P2-1 探针复现**：等长替换 `assert_text` → `get_logs` → 全量 **1 failed**（修复前 1412 全绿）。
- 全仓重复顶层定义体检：**零命中**。

---

## Task 1.4 完成记录（P3-04 git diff / changed_files + run_git 单点，2026-10-08）

**Objective**：设计 §5.1 的 `PlannerInput.changed_files` 数据源（plan 第 0 节核实的差距：
**全仓此前没有任何 git diff 工具**）。

### 交付

- `source/vcs.py`（新）：`run_git(repo_root, *args) -> str` + `GitError(RuntimeError)`
  ——git 调用的**唯一实现**。
- `source/git_diff.py`（新）：`changed_files(repo_root, base, head=None) -> GitChangeSet`
  + `FileChange` / `GitChangeSet` / `GitDiffError`。
- `experience/promoter.py`（改）：删掉本地 `_git`，三个调用点改调 `run_git`；
  去掉不再用的 `import subprocess`。**行为与报错消息逐字不变**（P2 回归保证：
  `test_promoter.py` + `test_p2_matrix_m4.py` 20 例全绿）。
- `tests/unit/test_git_diff.py`（新，**17 例**，真 git 仓库 fixture）。

### 三条设计决定（都影响下游 impact 分析，都写进了模块 docstring）

1. **`-z`（NUL 分隔）而不是按行 + `\t` 切**：文件名里可以有制表符与换行（git 允许），
   按行切会把这类名字**静默解析错**——而错的路径喂给 `planner/impact.py` 的
   path→target 映射只会**静默**给出错的受影响用例。有专测（`a\tb.txt` / `c\nd.txt`）。
2. **显式传 `-M`**：git 2.9 起 `diff.renames` 默认 true，但那是**用户配置**——有人在
   `~/.gitconfig` 里关掉，重命名就会变成 `A` + `D`，「重命名分类正确」在**他的机器上**
   静默不成立。**实测**：git 2.54 下不传 `-M` 也会报 `R100`（默认开），但**探针验证**
   去掉 `-M` 后 `test_rename_detection_is_independent_of_user_config` **真红**——即
   这条钉子不是空转，它钉的正是「用户配置不该改变结论」。
3. **`changed_files` 对重命名取旧 + 新两个路径**：impact 的映射是「路径 → 受影响
   target」，重命名后 metadata 的文件归属**可能还挂在旧路径上**（未重新生成）、也可能
   已挂到新路径——两边都收是保守的（沿用 P2「匹配宁可多包含」的取向），只收一边会在
   另一种 metadata 形态下漏掉影响面。复制（`C`）**只收新路径**（源文件内容没变，
   不该被算受影响）。有专测。

### 失败语义（fail-loud，矩阵 #6 的 M2 侧消费）

非 git 目录 / 坏 `base` / 坏 `head` → `GitDiffError(ValueError)`，消息带**仓库路径 +
范围 + git 自己的 stderr**（`unknown revision` / `Not a git repository` 都在那，不自己
编诊断）。**不静默返回空 changeset**——「这次改动影响 0 个用例」与「ref 拼错了」在结果
上长得一模一样，而前者会让 planner 产出一个看起来完全正常的空 Plan。
底层 `GitError(RuntimeError)` 在 `changed_files` 里被**翻译**成域错误（与
`knowledge._read_steps` 把裸 sqlite 异常翻译成 `ValueError` 同一手法，review_p2_task55
P3-2 的先例）。

### 实测踩到的 git 行为（写进测试 docstring）

`git -C dir` 会**向上找 `.git`**——而 pytest 的 `tmp_path` 在**项目仓库里面**，所以
「造一个非 git 目录」的测试**第一版 `DID NOT RAISE`**（那个目录被当成「项目仓库的
子目录」）。修法：`GIT_CEILING_DIRECTORIES=tmp_path` 把向上搜索截住。
这条同时说明：`changed_files(Path("."))` 从仓库子目录调用**能正常工作**（会找到外层
仓库）——那是想要的行为。

### ⚠️ 登记：另有两处 git 调用**没有**收敛（有意为之）

| 位置 | 形态 | 为什么不动 |
|---|---|---|
| `source/metadata.py::_git_commit` | 不传 `-C`（靠进程 CWD），失败一律 `"unknown"` | **P1 存量**；plan Task 1.4 的 Files 段**只限定 promoter**；调用形态不同（失败策略是调用方的，不是 git 层的） |
| `tracer/recorder.py::_git_commit` | 同上（两者互为副本） | 同上 |

`source/vcs.py` 的 docstring 已把这个范围**写清楚**（「本次只收敛 promoter 那一条」），
并注明「是否收敛由后续任务显式决策，不顺手改」——沿用 `review_p3_task11_final` 肯定过的
边界诚实做法（当时没顺手改 `tracer` 的第四/第五个 SQLite 写者）。

### 实测

- 全量 pytest **1434 passed**（Task 1.4 新增 **17**；上一轮基线 1417 + 17 = 1434）。
- P2 回归：`test_promoter.py` + `test_p2_matrix_m4.py` **20 passed**（promoter 换
  `run_git` 后行为不变）。
- **探针**：去掉 `-M` → `test_rename_detection_is_independent_of_user_config` **真红**。
- 全仓重复顶层定义体检（含 `source/`）：**零命中**。

### M1 收口

**Gate M1 的 5 条判据全部满足**：agent.db 迁移幂等 ✅ / 单写者生效（异常路径已修）✅ /
policy.yaml + production 一票否决 ✅（函数层；CLI 端到端归 Task 2.4）/
工具注册表静态断言进 CI ✅ / `source/git_diff.py` ✅。→ 打 tag **`checkpoint-p3-m1`**。

---

## Task 1.4 评审修订记录（review_p3_task14 收口，2026-10-09）

评审「有条件通过 — **0×P2** + 3×P3」，上一轮 `16808d0` 的 1×P2 + 3×P3 **4/4 全清**，
**Gate M1 五条判据逐条有落点**。三条 P3 都出在**同一处**：**「声称的验证范围」**——
一条恒真的断言、一个自称钉 `~/.gitconfig` 实际只钉了仓库本地 config 的测试、一句自称
「唯一实现」但同包里还有第二处的 docstring。

### P3-1 `test_copy_contributes_only_the_new_path` 的断言**恒真**，而 `C` 在 shipped flags 下不可达

- 症状：`assert cs.added == ("copy.txt",) or cs.renamed == ()` —— `C` 不在 `_RENAMED`
  里，所以右分支**恒真**，整条断言**不可能失败**（把 `_ADDED` 换成 `{"C"}` 它照样绿）。
- 评审探针（C 可达性）：`extra=()` → `A|copy.txt`；`-C`（单档）→ 仍 `A`；
  `-C -C` / `--find-copies-harder` → `C100|edit.txt|copy.txt`；用户配置
  `diff.renames=copies` 也逼不出 `C`。**即 `C` 在 shipped flags 下不可达**。
- 连带：`_ADDED` 的 `"C"` 与解析器的 `status == "C"` 分支是**防御性死代码**；而模块
  docstring 决定 3、提交信息、本审计文档都写「复制（C）…**专测**」——**声称超出验证范围**。
  这是 `MEMORY.md` §5「文档声称的能力必须有落点」的第 4 次（前三次：`--agent-db` /
  `args.policy` / 「都经 `agents/tools.py`」）。
- 修法（采纳评审的**推荐**项 = 加真测，而不是撤声称）：
  1. git 层那条改成 `test_copy_is_reported_as_add_under_shipped_flags`（**删掉恒真的
     `or`**，断言 `added == ("copy.txt",)` / `renamed == ()` / 源文件不进 `changed_files`），
     并把「`C` 在 shipped flags 下不可达」写进 docstring（这正是纯函数测存在的理由）；
  2. 新增 `test_copied_status_yields_only_the_new_path`——`_parse_name_status_z` 是**纯函数**，
     直接喂 `"C100\x00edit.txt\x00copy.txt\x00"`，断言 `FileChange(status="C",
     path="copy.txt", old_path="edit.txt")` 且 `changed_files == ("copy.txt",)`（旧路径不进）。
     这是 `C` 分支**唯一**的守卫。
- **探针复现**：把 `changed_files` 改成「复制也收旧路径」→
  `test_copied_status_yields_only_the_new_path` **真红**（修复前那条恒真断言不会红）。

### P3-2 `-M` 的钉子自称钉 `~/.gitconfig`，实际只钉了**仓库本地** config

- 症状：`run_git` 的 `subprocess.run(...)` **不传 `env=`** → 被测代码继承 `os.environ`、
  看到**真实 HOME / 真实 global gitconfig**；而 fixture 的 `HOME=tmp_path` 只作用于测试
  **自己的** `_git` helper（它显式传 `env=`），**管不到被测代码**。测试里那句
  `git config diff.renames false` 写进的是**仓库本地** `.git/config` → 与 docstring
  声称的「`~/.gitconfig`」**名实不符**。
- 后果：这是全模块**唯一**在钉「用户配置不该改变结论」的地方，却钉的是另一条通路；
  一旦有人为隔离给 `run_git` 加 `env=`，它会**静默失去意义**。
- 修法：`monkeypatch.setenv("GIT_CONFIG_GLOBAL", <一份含 [diff] renames=false 的文件>)`
  ——**实测 git 2.54 生效**（无 `-M` → `D`+`A`，带 `-M` → `R100`）；并**参数化**
  `["global", "local"]` 两条配置通路都钉。
- **探针复现**：去掉 `-M` → `test_rename_detection_is_independent_of_user_config[global]`
  与 `[local]` **两条都红**（修复前只有 local 那条是"真走过"的）。

### P3-3 `source/vcs.py` 首句自称「git 调用的**唯一实现**」，而同包里还有第二处

- 症状：首句「唯一实现」，而实测全仓 `subprocess.run(["git"…` 有 3 处
  （`vcs.py` + `source/metadata.py:52` + `tracer/recorder.py:107`）——**第二个实现就在
  同一个 `source/` 包里**。同文件第 15 行**已经披露**了范围（「本次只收敛 promoter 那一条
  ……不顺手改」），审计文档也登记了，所以**不是隐瞒**；问题只在**首句**：扫首句的人拿到的
  印象与实测不符。
- 修法（1 行）：首句改「**统一实现**（新代码一律走这里）」，并加一句「⚠️ 不是「唯一」：
  `source/metadata.py` 与 `tracer/recorder.py` 的 `_git_commit` 仍各自 subprocess 调 git
  （P1 存量）」——范围披露保留在下面，只把首句对齐实测。

### 小观察（评审标「不建议单独立项」）—— 两条都改了

| 观察 | 处置 |
|---|---|
| `step_failure_type` 的「两者互斥」没有机械守卫（AST 扫 3 个 `ToolResult(...)` 构造点，无一处同写两处） | ✅ 措辞改成「**外层优先、内层作为回退**」并明写「**没有机械守卫**：若两处都有值，外层胜出、内层被静默掩盖」；模块 docstring 同步；补 `test_step_failure_type_prefers_the_outer_place` 把「谁赢」钉成**可预期**行为 |
| `"C"` 在模块里有两处落点（`_ADDED` 与解析器的裸字面量） | ✅ 提 `_COPIED = frozenset({"C"})`，`_ADDED = frozenset({"A"}) \| _COPIED`，解析器改判 `status in _COPIED`（同一概念一处实现） |

另两条观察**留 M2**（评审也未要求本任务处理）：`changed_files` 目前生产零调用者（边界
如实）；`GitChangeSet.head` 记的是传入的 ref 串而非解析后的 SHA（M2 若要做「Plan 可追溯
到确定的 commit」需额外一次 `rev-parse`）。

### 实测

- 全量 pytest **1437 passed**（本轮 +3：`test_git_diff.py` 17→**19**、
  `test_agent_tools.py` 55→**56**；1434 + 3 = 1437）。
- **探针复现两条修复**：① 让「复制也收旧路径」→ C 分支的纯函数测试**真红**；
  ② 去掉 `-M` → global 与 local **两条都红**。
- 恒真断言残留检查：`or cs.renamed == ()` 与 `pytest.raises(Exception)` 只出现在
  **docstring/注释的说明文字**里（描述被删掉的东西），无实际断言残留。

---

## Task 2.1 完成记录（P3-05 planner 数据模型 + Plan 持久化，2026-10-09）

**Objective**：设计 §5.1 的模型落地，Plan 存 agent.db 的 `test_plans` 表。**M2 的第一个任务**。

### 交付

- `planner/__init__.py`（新）：只 re-export `models`（**带一条禁令**，见下）。
- `planner/models.py`（新）：`PLAN_SCHEMA_VERSION` / `PRIORITY_MIN|MAX` / `PlanSource`
  （`Literal["existing","generated_gap"]`）/ `_Strict` / `PlannerInput` / `TestPlanTask` /
  `TestPlan`（含 `to_store_dict` / `from_store_dict`）。
- `tests/unit/test_planner_models.py`（新，**14 例**）。
- ⚠️ **`agents/storage.py` 未改动**——见下面的偏离登记。

### 校验与不校验（都对着设计/plan 的原文定）

| 项 | 处置 | 出处 |
|---|---|---|
| `reasons` 非空 | **拒绝** | plan Task 2.1 Steps：可解释性是 F13 的落地，「不是裸分数」 |
| `priority ∈ [0,100]` | **拒绝越界** | 设计 §5.1 行内注释「0-100，确定性评分」——越界是算分 bug，fail-loud 比让 300 分排最前好 |
| `source` 仅两值 | **拒绝第三值** | 设计 §5.1 的 `Literal`（用 `Literal` 而非 Enum：与设计逐字一致，且 `model_dump(mode="json")` 直接给字符串） |
| `extra="forbid"` | **拒绝未知键** | 与 `agents/models.py` / `experience/models.py` 同款 |
| **`changed_files` 允许为空** | **不校验** | 设计矩阵 #6：改动为空 → 空 Plan 且**明示**。把「空」当错误会让「这次真没改什么」与「git diff 失败了」混为一谈——后者在 `source/git_diff.py` 已是 fail-loud 的 `GitDiffError` |
| **`tasks` 允许为空** | **不校验** | 同上（空 Plan 是合法产物） |

### `schema_version` 是 **TestPlan 自己的版本**

`PLAN_SCHEMA_VERSION = "0.1"` 与 TestCase 的 `"0.2"`（`testcase/schema.py::SCHEMA_VERSION`）
是**两条独立的版本线**——`test_plan_version_is_independent_of_testcase_schema` 把「两个常量
不同」钉住（有人把 testcase 的常量搬过来当默认值就会红）。

### 关键决策 1：`agents/storage.py` **不改**（对 plan Files 的一处偏离）

plan Task 2.1 的 Files 写「Modify: `agents/storage.py`（`save_plan / get_plan` 落
`test_plans` 表）」——但那三个方法（`save_plan` / `get_plan` / `list_plans`）**在 Task 1.1
建库时已随 `001_agent_schema.sql` 一起落地**，本任务核对后**无需改动**（upsert、排序、
`created_at` 由 DB 拥有，都已就位）。

更实质的一层：**存取转换刻意留在 `planner/models.py`**（`to_store_dict` /
`from_store_dict`），`agents/storage.py` 收发的仍是**裸 dict**。两条理由：

1. 那张表的 `tasks_json` 是**不透明 JSON blob**（不像 `agent_tasks` 有逐字段列），
   store 没有理由知道 `TestPlanTask` 的结构；
2. **避免包级循环**：`planner/__init__.py` 会 re-export `planner.planner`（Task 2.3 起，
   它反过来 import `agents.storage`）——一旦 `agents.storage` 反向 import
   `planner.models`，包初始化期就会成环。保持 `agents` 侧对 `planner` 的依赖为**零**
   是**结构上的保证**，不是「我们小心一点」。
   `test_agents_storage_does_not_import_planner`（AST 扫 import）钉住它，并**探针验证**：
   往 `agents/storage.py` 插一行 `from planner.models import TestPlan` → **真红**。

`planner/__init__.py` 的 docstring 也把这条禁令写给了后来者（「**不要**在这里 re-export
`planner.planner`」）——Task 2.3 落地时最容易踩的就是它。

### 关键决策 2：`TestPlan` **没有 `created_at`**

设计 §5.1 的模型里没有它，它是 `test_plans` **行**的元数据（§11 的表有 `created_at`）
→ 由 DB 拥有、由 store 的 dict 携带，`from_store_dict` 明确**忽略**它（不塞进模型，
也不假装模型有）。

### ⚠️ 登记：`schema_version` **当前不落库**（已知缺口）

设计 §11 的 `test_plans` 只有 `plan_id / app_build / git_commit / created_at /
tasks_json`——**没有版本列**，而 `tasks_json` 按列名只装 tasks。后果：**读回的行总是当前
版本**；将来把 `PLAN_SCHEMA_VERSION` 从 `"0.1"` 抬到 `"0.2"` 时，旧行会被**静默**当成新版
本。两条修法（都要动 schema，**不在 Task 2.1 范围**）：① 迁移 002 加 `schema_version` 列；
② 把 `tasks_json` 换成带信封的 `{"schema_version": …, "tasks": […]}`（列名要一并改）。
**谁 bump 版本谁先处理这一条**——已写进 `TestPlan` 的类 docstring。

### 实测踩到的一处（仓内既有手法）

`TestPlan` / `TestPlanTask` 的名字以 `Test` 开头（设计 §5.1 **逐字要求**，不改名），
被 import 进测试模块后 pytest 会把它们当**测试类**收集 → 两条
`PytestCollectionWarning`。修法沿用仓内既有手法 `__test__ = False`
（`executor/assertion.py` 的两个断言异常、`runner/runner.py` 同款）；并用
`-W error::pytest.PytestCollectionWarning` 复跑确认清零。

### 实测

- 全量 pytest **1451 passed**（Task 2.1 新增 **14**；上一轮基线 1437 + 14 = 1451）。
- **探针**：往 `agents/storage.py` 插 `from planner.models import TestPlan` →
  分层守卫**真红**。
- 收集警告：`-W error::pytest.PytestCollectionWarning` 下 14 passed（无警告）。

---

## Task 2.1 评审修订记录（review_p3_task21 收口，2026-10-09）

评审「有条件通过 — **0×P2** + 3×P3」，上一轮 `9bfa0b0` 的 3×P3 **3/3 全清** + 两条小观察也落地。
三条 P3 仍集中在**「声称 vs 落点」**。

### P3-1 `reasons` 的「非空」校验能被**一个空串**绕过

- 症状：`if not v:` 只判**容器**不判**元素** → `reasons=[""]` / `["   "]` / `["ok",""]`
  **全被接受**，而它们给出的可解释性**恰好为零**（与「裸分数」在对账时没有区别）。
- 为什么现在要修：这是 plan Task 2.1 Steps **唯一**点名的校验，而它拦不住**生产者最容易
  写出的那种值**——Task 2.2 的 `priority_score` 与 Task 2.3 的 LLM 解释都在字符串拼接 /
  模板渲染里产出理由（`f"{impact}"` 在 `impact` 为空时就是 `""`）。等 `mta plan` 渲染出
  「有 reasons 但读不出理由」的 Plan 再补，`test_plans` 里已经有行、报告里已经有输出——
  **错的是已落库的数据**。
- 修法：`if not v or not all(r.strip() for r in v):`（消息写明「空串/纯空白不算理由」）
  + 参数化扩到 6 例（`[]` / `()` / `[""]` / `["   "]` / `["ok",""]` / `["ok","  "]`）。
- **探针复现**：把校验退回 `if not v:` → 新增的 4 例（`bad2`–`bad5`）**全红**。

### P3-2 分层守卫只扫 `agents/storage.py`，而声称是「`agents` 侧依赖为零」

- 症状：`planner/models.py` 写着「`agents` 侧对 `planner` 的依赖为**零**——这是结构上的
  保证」，而守卫只读**一个文件**。评审 A/B：插进 `agents/storage.py` → 真红；
  插进 **`agents/models.py` → 14 passed 全绿**。
- 为什么现在要修：「最可能在未来 import `planner` 的恰恰不是 storage.py」——
  `agents/tools.py`（工具层要声明 plan 的返回类型）、`agents/models.py` 都是自然落点。
  一句「依赖为零」配只扫一个文件的守卫 = **声称宽于守卫**。
- 修法（采纳评审的**扩面**项，而不是收窄声称）：守卫改名
  `test_agents_package_does_not_import_planner`，**逐文件扫整个 `agents/` 包**
  （`rglob("*.py")` + 逐文件 AST），失败时**指名文件**；并带防空转下界
  （`>=5` 个文件 + `storage.py` 必须在扫描面内）。同时把 `planner/models.py` 的措辞对齐
  成「**`agents/**` 对 `planner` 的依赖为「零」**（依赖方向单向：`planner → agents`）」。
- **为什么是「扩面」而不是「收窄声称」**：这条规则的**真实不变量**是**包级无环**——
  `planner → agents`（Task 2.3 起 `planner.planner` import `agents.storage`），反方向一旦
  出现就成环；而 `planner/__init__.py` 会 re-export `planner.planner`，所以 `agents` 侧
  **任何** module-level 的 `import planner…` 都会在包初始化期把它拉进来。按**包**粒度说
  「依赖为零」才是与结构一致的表述（只对 `storage.py` 说，是把结构性质降级成一个文件的巧合）。
  守卫的失败消息里留了**逃生门**：真有正当需要时用 `if TYPE_CHECKING:` 下的 import
  （不在运行期执行、不成环），并在该测试里为它开一条带理由的白名单——**别把整条守卫删掉**。
- **探针复现**：插进 `agents/models.py` → 扩面后的守卫**真红**（修复前全绿）。

### P3-3 `parametrize([[], ()])` 的两条其实是**同一个输入**

- 症状：`list([])` 与 `list(())` 都是 `[]` → 两条 test id 喂的是**同一个实参**，
  测试数 +1、覆盖面 +0。而它是本任务**唯一**钉 F13 那条校验的测试（P3-1 正在说它有洞）
  ——**在一条保护性测试上虚报覆盖面**比在普通测试上更贵。
- 修法：**不做 `list()` 转换**，让 `[]` 与 `()` 各自直接作为 `reasons` 传入（两种真的不同
  的输入形态，顺带钉住 pydantic 对 tuple 的归一化）。

### 小观察：两处已改、两处登记

| 观察 | 处置 |
|---|---|
| `test_saved_tasks_json_is_the_model_shape` 里 `json.loads(json.dumps(x))` 是**恒等**（`get_plan` 已经 loads 过） | ✅ 删掉那两个调用，直接取 `get_plan(...)["tasks"]` |
| `schema_version` 的登记没提 **`from_store_dict` 要跟着读**（它现在完全忽略该键、永远取默认值） | ✅ `TestPlan` docstring 补上：「两种修法**都必须同时改 `from_store_dict`**——只加列不改读取口，旧行仍会被静默当成新版本」 |
| 三个 `_Strict` 的 lax 口径（`priority=True → 1` 等，与 `agents` / `experience` 两个 `_Strict` **完全一致**） | **登记**：要收紧是**三个包一起动**的单独立项，不是本任务缺口（MEMORY §4 已记该形态） |
| `plan_id` 的来源未定（upsert 语义依赖它的稳定性） | **登记 + 写进 plan Task 2.3 的接线前置**：① 随机 uuid → upsert 永不发生、库线性增长；② 由 `app_build + git_commit` 派生 → 同 build 重复 plan 会覆盖，但留不下两份。**拍板后回填**，且生成方式只许一处实现 |

### 实测

- 全量 pytest **1455 passed**（本轮 +4：`test_planner_models.py` 14→**18**；1451 + 4 = 1455）。
- **探针复现两条修复**：① 校验退回「只判容器」→ 空串那 4 例**全红**；
  ② 插 `from planner.models import TestPlan` 进 `agents/models.py` → 扩面后的守卫**真红**
  （修复前 14 passed 全绿）。
- `-W error::pytest.PytestCollectionWarning` 下 18 passed（收集警告仍为零）。

---

## Task 2.2 完成记录（P3-06 KnowledgeSources 装配 + filters 扩展 + priority_score，2026-10-09）

**Objective**：设计 §5.2 的确定性打分；F3 的装配入口定型。

### 交付

- `knowledge/__init__.py` + `knowledge/retrieval.py`（新）：`build_knowledge(...)`
  ——**只装配，不新增检索方法**。
- `planner/prioritizer.py`（新）：`TestCaseMeta` / `priority_score` / `order_key` /
  三个初版常量。
- `planner/risk.py`（新）：`case_risk`（**转发** P1 的 `effective_risk`，取用例所涉
  元素的 max）。
- `experience/knowledge.py`（改）：`trace_history` 的过滤键从 `{app_build, limit}`
  扩到 `{app_build, testcase_id, failure_type, limit}` + `screen_id`（fail-loud，见下）。
- `graph/models.py` + `graph/builder.py`（**改，P2 追加式**）：`TraceStep` 补
  `testcase_id` / `failure_type`，`read_trace_steps` 把它们读出来（见下面的偏离登记）。
- `planner/__init__.py`（改）：re-export `prioritizer` / `risk`（仍**不**碰
  `planner.planner`——那条禁令的理由写进了 docstring）。
- 测试：`tests/unit/test_prioritizer.py`（**22**）+ `tests/unit/test_knowledge_retrieval.py`
  （**15**）。

### 打分公式（设计 §5.2 + plan 给的初版数字）

```text
score = 40 × [用例落在受影响集合里] + 30 × 历史失败率∈[0,1]
score = max(score, RISK_FLOOR[risk])          # CRITICAL→90 / HIGH→70
score = clamp(score, 0, 100)                  # 设计 §5.1 的 priority 值域
```

- **纯函数**（F13）：四入参即全部输入，不读库/不看 LLM/不依赖时间——有专测
  （重复调用同结果）。
- **`history` 越界 fail-loud，不静默夹取**：失败率 > 1 是**统计口径的 bug**
  （分子分母弄反之类），夹到 1 会让「算错了」表现为「历史一直很差」——与
  `planner/models.py` 对 `priority ∈ [0,100]` 同一条理由。
- ⚠️ **HIGH 的 70 与「impact 命中 + 历史满」的 70 会打平**（base 上限恰好是
  40+30=70）。plan 的判据是「CRITICAL 进**顶部区间**」（≥70），不是「HIGH 严格高于
  所有非 HIGH」；平局按 `order_key` 的 `testcase_id` 字典序裁决。**这条算术后果写进了
  模块 docstring 并配了专测**——将来调参时它会红，提醒改的人是有意为之还是碰巧。
- `order_key(score, id) = (-score, id)`：全序、与输入顺序无关（有专测）；**LLM 只许在
  同一 key 的组内做 tie-break**，跨组顺序由它钉住（矩阵 #5 的判据由 Task 2.3 的编排
  层执行）。

### ⚠️ 偏离登记 1：`build_knowledge` **没有 `graph store` 参数**

plan 的 Files 写「组合 experience_store/trace_db/**graph store**/cases/app_build」，
但 P2 的实现**不接受** graph store：`P2KnowledgeSources.graph_query` 用
`read_trace_steps` + `build_runtime_graph` **从 trace 现建**运行时图——那是 **12.1 的
定档**（「trace 是运行时图的唯一来源」）。若装配处再传一个 `graph.db` 句柄，就会出现
**第二个图来源**，正是 12.1 要避免的。故**不设该参数**（也不设一个收了不用的死参数）。

### ⚠️ 偏离登记 2：**必须**顺带改 P2 的 `TraceStep` / `read_trace_steps`

plan 的 Files 只列了 `experience/knowledge.py`，但白名单里的三个键**在 `TraceStep` 上
不存在**（它只有 `testcase_run_id/step_index/step_type/target_id/status/detail/observed_at`）：

| 键 | 数据在哪 | 处置 |
|---|---|---|
| `failure_type` | `steps.failure_type`——**一直是 `steps` 的列**，只是没被读出来 | 追加读入 |
| `testcase_id` | `testcase_runs.testcase_id`——SQL 里**已经 JOIN 了这张表**，只是没选该列 | 追加读入 |
| `screen_id` | **哪里都没有**：`steps` 表不记 screen；`target_id` 是**解析后的裸元素 id**（不含屏）；`detail_json` 也没有（`runner._record` 的 payload 只有 step_index/action_type/status/error/latency_ms） | **fail-loud**（见下） |

两个新字段是**追加式**的（带默认值、放在末尾）：既有构造点全是关键字实参、消费方
（`build_runtime_graph` / `graph.diff`）不读新字段——P2 回归
（`-k "graph or knowledge or impact"` 126 例）全绿。

### ⚠️ `screen_id`：**fail-loud，不静默放过**

`trace_history({"screen_id": ...})` 会**报错**而不是返回未过滤的结果。理由与该方法自己
的未知键检查**同一条**：静默接受一个过滤不了的键，等于把「想过滤没过滤」伪装成
「结果恰好都对」——同一个道理不能对自己网开一面。

**数据源缺口的两条候选修法**（都不在 Task 2.2 范围，需设计层拍板）：
① 在 `steps` 表加 `screen_id` 列（P1 schema 变更 + recorder 写入点 + 迁移）；
② 让知识层持有 Repository，用 `target_id → eff.screen` 反查（但知识层是**数据层**，
引入 Repository 依赖是分层倒退，且漂移元素会解析失败）。
**谁需要 `screen_id` 过滤谁先处理这一条。**

### `planner/risk.py`：转发而非复制

- `effective_risk` 在模块层 re-export；测试用 **import 级断言**
  （`planner.risk.effective_risk is executor.policy.effective_risk`）钉住「转发」——
  光测行为相同**挡不住**「有人抄了一份判定逻辑、恰好结果一样」。
- 取值 = 用例所涉元素 `effective_risk` 的 **max**（设计 §5.3「不管改动大小」）：取 max
  而非均值，因为「用例里有 CRITICAL 元素」不该被同用例里的 LOW 元素稀释。
- **解析不到的元素照原样上抛**（`UnknownReferenceError`），**不跳过、不当 LOW**：一个
  CRITICAL 元素因漂移而解析失败时，静默按 LOW 计会让它在 Plan 里**沉底**——错误的
  方向恰好是「看不见」那一侧。容错由调用方显式处理（漂移是 Task 2.3 的职责）。

### 顺带修掉的一处（Task 2.1 同款）

`TestCaseMeta` 名字以 `Test` 开头（设计 §5.2 逐字要求）→ 被 import 进测试模块后 pytest
会当**测试类**收集 → `__test__ = False`（仓内既有手法）。`-W error::PytestCollectionWarning`
下 37 passed（零警告）。

### 实测

- 全量 pytest **1492 passed**（Task 2.2 新增 **37**：prioritizer 22 + knowledge 15；
  上一轮基线 1455 + 37 = 1492）。
- P2 回归（`-k "graph or knowledge or impact"`）：**126 passed**（`TraceStep` 的追加
  字段不影响既有消费方）。
- `-W error::pytest.PytestCollectionWarning` 下两个新文件 **37 passed**（零警告）。

---

## Task 2.2 评审修订记录（review_p3_task22 收口，2026-10-09）

评审「有条件通过 — **0×P2** + 3×P3」，上一轮 `4d572f3` 的 3×P3 **3/3 全清** + 两条小观察
也落地；**8 条钉子经 A/B 真改文件验证无一空转**。三条 P3 全出在**同一个形状**：**「静默
降级」**——而它们自己写的两条理由（`case_risk` 的「错误落在看不见那一侧」、`screen_id` 的
「同一个道理不能对自己网开一面」）恰好就是判据。

### P3-1 `priority_score` 的入参闸门只做了一半（`risk` 无闸门 / `history` 被 `Risk` 绕过）

- 症状（探针实测）：`risk="CRITICAL"` / `"critical"` / `None` / `4` → **全部不报错**，走
  `RISK_FLOOR.get()` 返回 `None` 的那一支 → **CRITICAL 的 floor 静默失效**；
  `history=Risk.LOW` → 被当成失败率 1.0（**白拿 30 分**）——因为
  `class Risk(int, enum.Enum)` 使 `isinstance(Risk.LOW, (int, float))` 为 **真**。
- 为什么现在修（而不是等 Task 2.3）：**Task 2.3 的编排层是第一个会把「外部来的值」喂进
  打分的地方**（LLM 输出 / policy.yaml / CLI 参数——全仓的 `strict=True` 教训说明「外部值
  错型」在本项目里真实发生过），而失败的形态是**分数偏低 → CRITICAL 用例在 Plan 里沉底**，
  错误方向恰好是「看不见」那一侧。等到 Plan 里出现一个「CRITICAL 排在末尾」的结果，错的
  已经是**已落库的 Plan**。
- 修法（各一行）：`isinstance(risk, Risk)` 闸门（消息写明「`RISK_FLOOR.get()` 对非 Risk
  返回 None → floor 静默失效」）；`history` 判定补 `isinstance(history, Risk)` 排除。
- **探针复现**：① 删掉 `risk` 闸门 → **5 例**（`CRITICAL`/`critical`/`HIGH`/`4`/`None`）
  全红；② `history` 判定去掉 `Risk` 排除 → `test_risk_is_not_accepted_as_history` 红。
- 顺带补 `test_valid_risk_still_works_after_the_gate`：**闸门不能误伤**——四个 `Risk`
  成员逐一断言「有 floor 的抬到 floor、没有的不抬」。

### P3-2 `case_risk` 的 `getattr(tc_meta, "element_ids", ())` 让「传错对象」静默变 `LOW`

- 症状：探针的替身 repo 一被调用就抛 `AssertionError`，实测**未被调用**——传一个没有
  `element_ids` 的对象（或把字段名拼成 `element_id`）时函数**静默返回 `Risk.LOW`**。
- 与 P3-1 同形，且**与本函数自己的 docstring 冲突**：它明写「解析不到的元素**照原样上抛**，
  不跳过、不当 LOW」，却对**自己的入参**留了一个静默兜底。`getattr` 的默认值把「用例根本
  没有/拼错了元素字段」与「用例确实不涉及任何元素」压成同一个 `Risk.LOW`——而后者是
  **有意为之**的兜底（有专测）。两种语义混在一个 LOW 里。
- 修法：`for element_id in tc_meta.element_ids:`（`TestCaseMeta` 必有该字段）+ 补类型标注
  `tc_meta: TestCaseMeta`。传错就 `AttributeError`，不静默降级。
- **探针复现**：恢复 `getattr` 兜底 → `test_case_risk_refuses_a_wrong_meta_object` 红。

### P3-3 `build_knowledge` 的「单一入口」是**现在时声称**，且 F3 的守卫只钉住一半

- 症状：docstring 写「P3 的 planner / explorer / diagnosis **都** `from knowledge import
  build_knowledge`」，而全仓 `build_knowledge` 的**生产**命中只有 `knowledge/` 包自己两处
  （`__init__` 的 re-export + 定义）；唯一调用方是单元测试。同时
  `test_build_knowledge_adds_no_retrieval_method` 钉的是「实例**公开面** == 四方法」——
  **挡不住**「有人在 P3 侧直接 `P2KnowledgeSources(...)` 绕过入口」。
- 修法（**两条都做**，而不是二选一）：
  1. docstring 改**将来时 + 落点**：明写「零生产调用者」、第一个真实调用点是 Task 2.3 的
     `planner/planner.py`，并用一张表列出两条守卫**各自钉住哪一半、钉不住哪一半**；
  2. 补守卫 `test_p2_knowledge_sources_is_constructed_in_one_place`：用 **AST** 扫全仓的
     **真实构造调用**（`ast.Call`，不扫 docstring/注释里的名字——本模块 docstring 自己就
     写了「而不是让调用方直接 `P2KnowledgeSources(...)`」，文本扫描会把它当成一次构造），
     要求命中集 == `["knowledge/retrieval.py"]`；`phase0/`（P2 的 gate 脚本）与 `tests/`
     （集成测试直接测那个类）显式排除；带**防空转下界**（`scanned >= 80`，实测 94）。
- **探针复现**：在 `planner/risk.py` 里插一个直接构造 `P2KnowledgeSources(...)` 的函数 →
  守卫**真红**（修复前无此守卫）。
- 顺带：`test_knowledge_retrieval.py` 里 `P2KnowledgeSources` 是**未使用**的导入，删掉。

### 小观察五条 —— 全部处理

| # | 观察 | 处置 |
|---|---|---|
| 1 | `round()` 是**银行家舍入**（`30×0.15=4.5→4` 而 `30×0.05=1.5→2`，方向随尾数奇偶翻转） | ✅ 改 `math.floor(score + 0.5)`（半分一律进位），补 5 例参数化测试钉住（`0.05→2 / 0.15→5 / 0.25→8 / 0.35→11 / 0.45→14`）。**探针**：退回 `round()` → 2 例红 |
| 2 | 提交信息里「P2 回归 126 passed」是**父提交**上的数 | ✅ 本轮更正为可照抄复现的口径：**146 passed = 126 既有 + 20 新增**（16 来自 `test_knowledge_retrieval.py` 全部 + 4 来自 `test_prioritizer.py` 名字含 `impact` 的）；审计与提交信息都用这个口径 |
| 3 | `build_knowledge(cases: Any = ())` 无精确标注 | ✅ 改 `Iterable[TestCase]`（`pipeline.discover()` 的返回元素类型），并在 docstring 说明「传错形态在**类型检查层**就能发现，而不是等 `impact_of` 时才发现装配早就装完了」 |
| 4 | `RISK_FLOOR` 是模块级**可变** `dict` 且被 re-export（`planner.RISK_FLOOR[Risk.HIGH] = 0` 会全局生效） | ✅ 改 `MappingProxyType`（标注 `Mapping[Risk, int]`），注释写明「校准入口只该是这一处常量定义」；补 `test_risk_floor_is_read_only`（读透明 + 写 `TypeError`）。**探针**：退回裸 dict → 该测试红 |
| 5 | `priority_score` 每次调用都 `frozenset(impact)` → O(用例数 × \|impact\|) | ✅ 已经是 `set`/`frozenset` 时**不重建**（`in` 语义完全相同），注释说明调用方按用例循环传同一个集合；补 `test_impact_accepts_any_iterable` 钉住「非 set 形态（list / 生成器）照常工作」 |

### §五 存疑项之一也顺手修了

`test_case_meta_is_frozen` 用 `pytest.raises(Exception)`（上一轮同类问题已修过一处，此处
漏了）→ 收窄到 `dataclasses.FrozenInstanceError`（它是 `AttributeError` 的子类，
`raises(Exception)` 会把「任何异常」都算通过，与「冻结生效」不等义）。

### 给 Task 2.3 的接线前置（已写进 plan）

`planner/planner.py` 必须**经 `knowledge.build_knowledge` 取 `KnowledgeSources`**，
不许直接构造 `P2KnowledgeSources`（守卫 `test_p2_knowledge_sources_is_constructed_in_one_place`
钉住）；`priority_score` 的 `risk` 只收 `Risk`（错型 fail-loud），`impact` 传
`set`/`frozenset` 可免重复拷贝。

### 实测

- 全量 pytest **1508 passed**（本轮 +16：`test_prioritizer.py` 22→**37**、
  `test_knowledge_retrieval.py` 15→**16**；1492 + 16 = 1508）。
- P2 回归 `-k "graph or knowledge or impact"`：**146 passed = 126 既有 + 20 新增**
  （按文件拆分：graph_diff 40 / graph_builder 28 / graph_source 24 / graph_impact 18 /
  knowledge_sources(集成) 11 / policy_config 3 / experience_schema 1 / agent_tools 1 = 126 既有；
  knowledge_retrieval 16 + prioritizer 4 = 20 新增）。
- **六条 A/B 探针全部真红**（每次先读原文、改后立即还原并断言字节相等）：
  删 `risk` 闸门 → 5 red；`history` 不排除 `Risk` → 1 red；恢复 `getattr` 兜底 → 1 red；
  `planner/` 里直接构造 `P2KnowledgeSources` → 1 red；`RISK_FLOOR` 退回裸 dict → 1 red；
  舍入退回 `round()` → 2 red。
- `-W error::pytest.PytestCollectionWarning` 下两文件 **53 passed**（零收集警告）。
- 全仓重复顶层定义体检：**零命中**。

---

## Task 2.3 完成记录（P3-07 diff-driven planner + LLM 解释层，2026-10-09）

**Objective**：设计 §5.2/§5.3 —— `Git Diff → Impact → TestPlan`，**LLM 只解释/同分打平，
不决定顺序**。M2 的第三个任务。

### 交付

- `planner/impact.py`（新）：`changed_targets` / `unmatched_changed_files` /
  `metadata_has_file_attribution` / `drift_targets` / `ImpactMappingError`。
  **适配层**：一行索引逻辑都没有（索引本体是 `source/coverage.py` + `graph/impact.py`）。
- `planner/planner.py`（新）：`plan(...)` / `PlanResult` / `Unrankable` / `failure_rate` /
  `rule_basis` / `llm_order_violations`。
- `llm/prompt.py`（改）：`build_plan_rationale_prompt`（解释层模板，10.4 分区结构照旧）。
- `llm/parser.py`（改）：`parse_plan_reasons` / `PlanRationale` / `MAX_REASON_CHARS`。
- `planner/models.py`（改）：`plan_id_for`（前置 ① 的拍板落点）+ 分层段措辞更正。
- `planner/__init__.py`（改）：导出 `plan_id_for`；**禁令理由对齐实测**（见下）。
- 测试：`tests/unit/test_planner_impact.py`（**16**）、`tests/unit/test_planner_e2e.py`（**24**）、
  `test_llm_contract.py` 26→**47**、`test_planner_models.py` 18→**24**。

### 前置 ① 拍板：`plan_id` = **内容寻址**

唯一生成点 `planner/models.py::plan_id_for(app_build, git_commit, changed_files)`
= `"plan_" + sha256(三者)[:16]`。三个候选的取舍：

| 方案 | 后果 |
|---|---|
| 随机 uuid | `save_plan` 是 upsert → **upsert 永不发生**，库随调用线性增长；同一输入两次跑出两份「内容可能不同」的 Plan（LLM 解释层不稳定），无法回答「这个 build 的 plan 是哪一份」 |
| 只由 `app_build + git_commit` | 同一 build 换 base（改动集不同）→ **静默覆盖**前一份（丢数据） |
| **内容寻址（本实现）** | 同输入幂等（不增长）、不同输入不互相覆盖；`changed_files` 先排序 → 集合相同即 id 相同 |

### 三条口径（都写进了模块 docstring）

1. **候选集 = 影响面内的用例**（不是全部用例）。设计 §5.3 的流程从「受影响用例」出发再把
   历史/风险**加进排序**；§5.3 那句「CRITICAL/HIGH 强制高优先级，**不管改动大小**」说的是
   **排序**（diff 只碰它一个字符也照样给 floor），不是「把未受影响的 CRITICAL 也拉进来」。
   这条同时让**矩阵 #6 自洽**：`changed_files` 为空 → 影响面为空 → 空 Plan（若候选集是全体，
   空改动会产出一份「排好序的全体用例」，那就不是空 Plan 了）。未受影响的用例由 `notes` 明示。
2. **`reasons` = 规则摘要（永远在）+ `llm: ` 前缀的 LLM 解释（有则追加）**。F13 要的是
   「可解释，**不是『LLM 觉得』**」——数值依据由代码生成、**永远存在**，LLM 的文字只做追加，
   来源一眼可辨。LLM 挂掉/预算耗尽/输出不合契约 → 只是少了那几行，Plan 照常产出。
3. **`failure_rate` 按运行统计**：分母 = `testcase_run_id` 去重数、分子 = 有 ≥1 个
   `status=="FAILED"` 步骤的运行数。`trace_history` 给的是**步骤流**，但 `TraceStep`
   带着 `testcase_run_id` → 分组即可，**不新增接口、不手搓 SQL**。三条细则：
   `RECOVERED` **不算失败**（它是恢复成功，E7 口径）；按步骤算会让步骤多的用例被稀释；
   **空历史 → 0.0**（没有失败证据 ≠ 一直失败——给 1.0 会让新用例凭空挤掉有失败史的老用例）。

### 矩阵 #5：LLM 只许同分重排 —— 判据可机械核对

`llm_order_violations(ordered, proposed)`：① `proposed` 必须是**排列**（漏/多/重复都算违规）；
② 按**分数**给段位（同分共享一段），`proposed` 的段位序列必须**非递减**。

⚠️ **段位必须按分数算，不能按名次算**——同分两条互换是设计**允许**的 tie-break；按名次判会
把合法操作全判成违规，「矩阵 #5 的守卫」就变成「LLM 永远被拒」，判据与设计不等义。有专测
（`test_llm_order_violations_allows_any_within_group_order` 用三条同分覆盖）。

违规 → **整条顺序丢弃** + `audit` 里记 `llm_order_rejected`（含 `moved_up` / `displaced`
与各自分数——**字段名按角色给**，第一版写成 `higher`/`lower` 指反了，审计读者会把分数读反）。
另外两条留痕：`llm_unknown_testcase_ids`（提到本 Plan 没有的 id）、`llm_ignored_fields`
（LLM 试图给 `priority` → 忽略但可见）。

### 矩阵 #6：空改动的三种成因都**明示**

| 成因 | 处置 |
|---|---|
| `changed_files` 为空 | 空 Plan + `notes`「本次没有改动 → 空 Plan（矩阵 #6）」 |
| 改动非空、**一个元素都没映射到** | 空 Plan + `notes` **列出未匹配的文件**（`unmatched_changed_files`） |
| 改动命中了元素、**没有用例引用** | 空 Plan + `notes` 说明 |

三种都是「不编造依据」。第一种与第二种的差别正是 `unmatched_changed_files` 存在的理由：
「这次没碰 UI」与「metadata 的路径写法与 git 不一致」在结果上长得一模一样，而后者会让
planner **长期**产出空 Plan 却没人发现。

### ⚠️ 偏离登记 1：`changed_targets` 多了 `unmatched_changed_files` 兄弟函数

plan 的 Files 只写了 `changed_targets(changes, metadata) -> tuple[str, ...]`（**签名照办**）。
实现时发现上面那张表里的第二种成因必须可见 → 补一个只读视图，与 `changed_targets` 共用
同一个私有索引 `_file_index`（不是第二份实现）。

### ⚠️ 偏离登记 2：`drift_targets` 需要 `cases`（plan 只写了「回退 drift 面」）

核实 `source/build_diff.py::diff_builds`：它的**范围**由 `reached_screens(cases, old)` 决定
——**没有 cases 就退化成空 scope、什么都比不出来**，而结果看起来只是「没变化」。所以回退面
必须收 `cases`，且 `cases` 为空时**显式拒绝**（`ImpactMappingError`）。
调用方（`planner/planner.py`）额外收一个**可选** `base_metadata`：有它才走回退面，没有就
照原样上抛 `ImpactMappingError`（不是「空 Plan」）。open question #2 要求的「精度损失写进
reasons」落在 `notes` 里（drift 是 build 之间的元素变化，比「本次 commit 改了哪些文件」宽）。

### ⚠️ 偏离登记 3：`PlanResult` 是 plan 之外的**过程产物**

设计 §5.1 的 `TestPlan` 只有 `schema_version / plan_id / app_build / git_commit / tasks`
——**没有**承载「为什么是这个 Plan」的字段。但本任务 Steps 明确要求两件事可见：「空 Plan 且
**明示**」（矩阵 #6）与「LLM 重排被拒 → **审计留痕**」（矩阵 #5）。所以编排层返回
`PlanResult(plan, notes, unrankable, audit)`：`plan` 是**持久化产物**（`save_plan` 只收它），
其余三项是**过程结论**（CLI 渲染；M6 起写 `agent_trace`）。**不给 `TestPlan` 加字段**——那要
动 `test_plans` 的 schema（设计 §11 的表没有这些列）。

### ⚠️ 坏引用用例：**列出来**，不猜、不静默丢

进了影响面但元素解析不到的用例（`UnknownReferenceError` / `AmbiguousReferenceError`）→
`PlanResult.unrankable` + `notes` 点名。**不给它猜一个风险等级**：猜 LOW 会让它在 Plan 里
沉底，而它恰恰是最可能失败的那个。依据是 P1 的同一句话——`runner/testcase_runner.py` 把引用
错误当**用例缺陷**（lint 应已拦截），该用例直接 FAIL、不做 recovery。
（口径待 M2 Gate 演示后回设计层确认：排除 / 按 unknown-risk 计 / fail-loud，三选一。）

### `planner/__init__.py` 的禁令理由**对齐实测**

Task 2.1 写的禁令理由是「`planner.planner` 要 import `agents.storage` → 成环」。实现时核实：
**编排层不 import `agents`**（落库是 CLI 的职责，AST 扫过导入表：无 `agents`），所以那条
成环路径**今天不成立**。禁令**保留**，理由换成两条真的：① `import planner` 不该顺带拉起
`repository` / `source.build_diff`；② M6 的自主闭环很可能让编排层直接落 `agent.db`，那时
成环会**立刻**回来。`planner/models.py` 的「分层」段同步更正。

### ⚠️ 登记：本任务只产出 `source="existing"`

设计 §5.1 的 `Literal["existing","generated_gap"]` 两值都已支持，但 `generated_gap`
（生成补齐）的生产者归 **M3 的 Test Generator**——那时才有「这条任务是生成的」这个事实。
现在写它是**没有生产者**，不是漏了。

### 实测

- 全量 pytest **1575 passed**（Task 2.3 新增 **67**：`test_planner_impact.py` 16 +
  `test_planner_e2e.py` 24 + `test_llm_contract.py` +21 + `test_planner_models.py` +6；
  上一轮基线 1508 + 67 = 1575）。
- **九条 A/B 探针全部真红**（每次先读原文、改后立即还原并断言字节相等）：
  ① 删「跨分重排判违规」→ 2 red；② 删预算闸门 → 1 red；③ 候选集改成全部用例 → 7 red；
  ④ 失败率改成按步骤 → 3 red；⑤ 删「缺归属 fail-loud」→ 3 red；⑥ 删单字符串闸门 → 1 red；
  ⑦ 删 drift 的 cases 闸门 → 1 red；⑧ LLM 解释改成「替换」而非「追加」→ 1 red；
  ⑨ `plan_id` 改成常量 → 4 red。
- 实现期由测试抓到的真 bug 一处：`_resolve_targets` 的回退分支**漏了 `return`**（回退面
  没接上，返回 `None` → `frozenset(None)` 抛 TypeError）。测试当场抓到。
- 全仓重复顶层定义体检（含 `llm/`）：**零命中**。
- `-W error::pytest.PytestCollectionWarning` 下 `test_planner_models.py` 24 passed（零警告）。

---

## Task 2.3 评审修订记录（review_p3_task23 收口，2026-10-09）

评审「有条件通过 — **1×P2** + 3×P3」，上一轮 `15ced55` 的 3×P3 + 5 条小观察 **5/5 全清**，
九条钉子经评审复跑**无一空转**（其中「候选集改全体」实际红 **14** 条，比自述的 7 条更强）。
唯一的 P2 出在**契约的落点比契约本身弱一级**。

### P2-1 `drift_targets` 要求「必须写进 `reasons`」，调用方写进了**不落库的** `notes`

- 症状：`drift_targets` 的 docstring 写「调用方**必须**把精度损失写进 Plan 的 `reasons`
  （open question #2 的原文要求），**不能只把它记在日志里**」，而 `_resolve_targets` 把它
  `notes.append` 了。`notes` 属于 `PlanResult`（**过程产物**），
  `save_plan(plan_id, *, app_build, git_commit, tasks)` **只写 `tasks_json`** —— 于是落库的
  Plan 里每条 `reasons` 都写「impact: 命中改动影响面」，却没有任何一处说明**这个「命中」
  来自回退面**。
- 为什么是现在：drift 面的影响面**比本次 commit 宽**（它是 build 之间的元素变化）。等人
  回头复核一份**已落库**的 Plan 时，「影响面是精确的」与「影响面是宽的」长得一模一样
  —— 正是本任务自己在 `unmatched_changed_files` 上写下的那条理由。
- 为什么不能只算「已登记的偏离」：提交信息把它写在偏离登记 ②（「精度损失写进 reasons 落在
  notes 里」），但落点**弱于** docstring 自己写下的硬要求（MUST + 明确排除「只记日志」）。
  契约与落点差一格 —— 判定标准正是「口径与实测不符」。
- 修法（采纳评审**倾向的**方案 1，并处理它指出的「plan 级结论重复 N 次」）：
  `rule_basis` 加 `drift` 参数，把标记**附着在它限定的那一行上**：
  `impact: 命中改动影响面（drift 回退：影响面比本次 commit 宽）` —— 不是每条 reasons
  追加一整句 plan 级说明。`notes` 保留完整说明（CLI 一眼可见）。
- 顺带把这条约束**写进模块 docstring 的偏离登记**（评审 §三 P2-1 的第二半）：
  「`notes` / `unrankable` / `audit` **不进 `test_plans`**，所以任何『必须随 Plan 一起被
  复核』的结论都不能只放在它们里」。
- **探针复现**：`drift=drift_fallback` → `drift=False` → 落库 reasons 的断言**真红**。

### P3-1 `failure_rate` 的 `getattr` 兜底 —— 同形在本包**第三次**

- 症状（探针实测）：两个缺 `testcase_run_id` 的 FAILED 步骤 → **1.0**（两个 run 塌成一个）；
  缺 `status` → **0.0**。两个方向都不报错。失败率占 **30 分**权重 —— 塌成 0 让有失败史的
  用例**沉底**、塌成 1 让没失败史的用例白拿 30 分，两侧都是「看不见」那一侧。
- 为什么现在修：这是同一形状在本包的第三次 —— ① 上一轮 `planner/risk.py` 的
  `getattr(tc_meta, "element_ids", ())` 刚被判 P3-2；② 本任务自己在 `planner/impact.py::
  _paths` 为「单个字符串」写了同一条纪律；③ 本函数却没有用这条纪律对待自己的入参。
- 修法：`run_id = step.testcase_run_id` / `failed = step.status == _FAILED_STEP`
  （`TraceStep` 两个字段都在，Task 2.2 才把它们读出来）。
- **顺手清掉同形的第二处**（评审没点，但同一条纪律）：`plan()` 里
  `candidates = [... if getattr(c, "id", "?") in impact]` 与 `cid = getattr(case, "id", "?")`
  → 改 `c.id`。传错形态的用例对象原先会让**所有对象塌成同一个 `"?"`**，Plan 里出现一条
  id 为 `"?"` 的任务而全程无报错。补 `test_plan_refuses_wrong_shaped_cases` 钉住。
- **探针复现**：恢复 `getattr` → `test_failure_rate_refuses_wrong_shaped_steps` 红；
  恢复 `getattr(c, "id", "?")` → `test_plan_refuses_wrong_shaped_cases` 红。

### P3-2 `plan_id_for` 缺兄弟函数刚加上的单字符串闸门

- 症状（探针实测）：`plan_id_for("1026", "abc", "a.swift")` **不报错**，且 id 与
  `plan_id_for("1026", "abc", list("a.swift"))` **完全相同**，长度与前缀（`plan_` + 16 hex）
  与正常 id 无从区分。而 `plan_id` 是 `test_plans` 的**主键 + upsert 键** —— 两次 plan 会
  落到互不相干的行（或覆盖到错误的行），全程无报错。
- 修法：**抽唯一的闸门** `planner/models.py::require_paths(value, *, where)`，
  `plan_id_for` 与 `planner/impact.py::_paths` **共用它**（原先两处各有一份判据 —— 这正是
  「同一概念只许一处实现」要防的漂移）。顺带修掉旧实现的 `str(p) for p in changed_files`：
  它把非 str **静默强转**成字符串（`[42]` → `"42"`），现在一并交给闸门（非 str 元素也拒）。
- **探针复现**：去掉 `require_paths` → 单字符串 / 非 str 两条**都红**。

### P3-3 F3 在 Task 2.3 的 import 级断言**缺位**

- 症状：plan 的「F1–F13 → 矩阵项映射」写 **F3 → M2–M5 各任务 import 级断言**。实测
  `planner/**` 的顶层 import 表**干净**（无 `sqlite3`/`tracer`/`graph`/`experience`/`agents`），
  属性成立 —— 但**没有任何测试钉它**。已有的两条守卫各钉一半、都不是这一半：
  `agents/**` 不 import `planner`（**反向**依赖）；`P2KnowledgeSources(` 的构造点唯一
  （挡不住 planner 侧直连 `sqlite3` / `tracer.storage`）。
- 为什么现在修：Task 2.3 是 `KnowledgeSources` 的**第一个生产调用者** —— 「单一入口」从
  约定变成事实就在这一格；M3–M5 还有三个同类调用方要来。
- 修法：`tests/unit/test_knowledge_retrieval.py::test_planner_package_does_not_touch_data_sources_directly`
  —— AST 扫 `planner/**` 的**全部** import（含函数内的按需导入，按需导入也是直连），
  失败**指名文件**，带防空转下界（文件数 + 必须覆盖到 `planner.py`/`impact.py`/`models.py`）。
  判据 `_is_datasource` 另有**自证测试**（`graph.storage` 命中而 `graph.impact` 不误伤）。
- **探针复现**：往 `planner/planner.py` 插 `import sqlite3` → 守卫**真红**。

### 小观察六条 —— 全部处理

| # | 观察 | 处置 |
|---|---|---|
| 1 | `rule_basis(hit=…)` 在编排路径上**恒真**（候选集 == 影响面） | ✅ docstring 写明「编排路径上 `hit` 恒为 True，`IMPACT_WEIGHT` 的 +40 在候选集内区分度为零 —— 这是口径 1 的正确推论」。**参数保留**：本函数是纯函数，契约由「命中/未命中」两值定义，不由某一个调用方的用法定义（`hit=False` 有直接调用的专测） |
| 2 | `trace_history` 在候选循环里**逐用例**调用 → O(候选数 × 全库步骤数) | ✅ 提到循环外**一次读全量**再按 `testcase_id` 分组（`_history_by_case`；分组键正是 Task 2.2 追加到 `TraceStep` 上的字段）。补 `test_history_is_read_once_not_per_candidate`（spy 计数 = **1**，旧写法是 4） |
| 3 | `test_plan_result_exposes_tasks` 名实不符（docstring 说视图，函数体只构造 `Unrankable`） | ✅ 改名 `test_plan_result_tasks_is_a_view_of_the_plan` 并**真的测视图**（`result.tasks == tuple(result.plan.tasks)` 且元素同一）；`Unrankable` 由坏引用那条测试覆盖 |
| 4 | `test_planner_e2e.py` 用真 sqlite（plan 的 Files 写「纯内存 fixture」） | ✅ 补进**提交信息**的偏离登记（文件 docstring 早写了理由：替身 SQL 会让「列名/字段写错」静默通过） |
| 5 | `plan()` 的 `cases` / `knowledge` / `repository` / `llm` / `budget` / `metadata` 无类型标注 | ✅ 补标注（`KnowledgeSources` / `Iterable[TestCase]` / `RepositoryProtocol` / `LLMProvider` / `LLMBudget` / `Mapping`），全部放 `if TYPE_CHECKING:` —— `from __future__ import annotations` 已把标注变成字符串，**运行期不付导入代价** |
| 6 | `impact.py` 按需导入 `diff_builds` 而 `planner.py` 顶层导入 `source.coverage`，风格不一致 | ✅ 统一为**顶层导入**（编排路径本来就要付这笔代价；风格一致比省一次导入更值） |

### §五 存疑项之一顺手补测

`_MAX_VIOLATIONS = 20` 的 `truncated` 分支原先无测试（评审以为需要 20+ 条同分组用例）——
其实 `llm_order_violations` 是**纯函数**，直接喂 21 条全反序即可。
补 `test_llm_violations_are_capped_and_marked_truncated`（**探针**：删掉截断逻辑 → 真红）。

### 登记（未改，评审也未要求）

- `_llm_pass` 的 `except Exception`（`noqa: BLE001`）：捕获 provider 故障并如实降级 +
  `budget.record_failure()`，与「吞掉」有本质区别。**但**它同时会吞掉我们自己的 bug ——
  将来若要区分「provider 挂了」与「我们的 bug」，这里需要收窄（评审 §五 记的一笔）。
- `MAX_REASON_CHARS` 截断后的 `reasons` 与 `TestPlanTask` 其它校验的交互未测（200 字符内
  应当安全）。
- 「坏引用用例」的三选一口径仍待 M2 Gate 演示后回设计层确认。

### 实测

- 全量 pytest **1586 passed**（本轮 +11：`test_planner_e2e.py` 24→**30**、
  `test_planner_models.py` 24→**27**、`test_knowledge_retrieval.py` 16→**18**；
  1575 + 11 = 1586）。
- **九条 A/B 探针全部真红**（每次先读原文、改后立即还原并断言字节相等）：
  ① 去掉 drift 标记 → 1 red；② 恢复 `failure_rate` 的 `getattr` → 1 red；
  ③ 去掉 `plan_id_for` 的闸门 → 2 red；④ `planner` 直连 `sqlite3` → 1 red；
  ⑤ `history` 置空 → 3 red；⑥ `history` 退回循环内 → 1 red；
  ⑦ `PlanResult.tasks` 置空 → 8+ red；⑧ 删掉违规数截断 → 1 red；
  ⑨ 恢复 `getattr(c, "id", "?")` → 1 red。
- 全仓重复顶层定义体检：**零命中**。
- `-W error::pytest.PytestCollectionWarning` 下 `test_planner_models.py` 27 passed（零警告）。


---

## Task 2.4 完成记录（P3-08 `mta plan` CLI + Gate M2 演示，2026-10-09）

**Objective**：设计 12 节 CLI —— 首个**自主命令**，`_check_autonomous_env` 的 CLI 端到端
接线从它开始。**M2 收口任务**。

### 交付

- `cli/main.py`（改）：`plan` 子命令 + `cmd_plan` + `_plan_range` / `_render_plan`；
  **抽出 `_metadata_path(args)`**（`_resolve_app_build` 与 `cmd_plan` 原先各写一份同样的
  路径表达式 → 同一课第二次）；`main()` 分发。
- `phase0/verify_p3_m2.py`（新）：**自足工程**的 Gate M2 脚本（见下）。
- `tests/unit/test_cli_plan.py`（新，**23 例**）。
- `planner/planner.py`（改）：`plan(..., no_history_reason=None)` + `rule_basis(no_history=)`。
- `experience/knowledge.py`（改，**P2 侧**）：`_read_steps` 的翻译面
  `OperationalError` → **`DatabaseError`**（父类）。
- `agents/storage.py` / `knowledge/retrieval.py` / `docs/phase3-plan.md`：**更正三处会
  变成假声称的 docstring**（见下）。

### 前置三条的落点

| 前置 | 拍板 / 实现 |
|---|---|
| ① `env_kind` 来源 | `--env-kind`（与 `run` 同款 choices/default）+ **唯一解析点** `_resolve_env_kind(args)`；`cmd_plan` **第一行**过 `_check_autonomous_env`。M1 的守卫 `test_every_wired_autonomous_command_passes_the_gate` 从本轮起**真的在工作**（探针 A/B/C 各红） |
| ② `policy` 来源 | **加了 `--policy`**：不给 → `load_policy()`（读相对 CWD 的 `config/policy.yaml`，读不到 → 内置默认值，矩阵 #4）；给了 → 该路径，**不存在即报错**。实现 `load_policy(args.policy) if args.policy is not None else load_policy()` —— **`is not None` 而非 `if args.policy`**：`--policy ""` 必须报错（探针 E） |
| ③ 调用顺序 | `cmd_plan` 第一行过前置，过了才读 metadata/git —— F7 是「启动时校验」，不是「跑一半才拦」 |
| ④ `AgentToolkit` 装配点 | **已证伪，未装配**（见下） |

### ⚠️ 偏离登记 1：**没有**在 `cmd_plan` 装配 `AgentToolkit`（更正 Task 1.3 的推测）

Task 1.3 的 docstring 与 plan 的接线前置都写「装配点归 Task 2.4」。实现时核实：
`mta plan` **不碰 App**（离线规划：git diff + metadata + 用例集 + 历史），装配
`AgentToolkit` 会是一个**死对象**（还要为它建真机会话）——正是「无消费者就不加字段」
要防的。**真正的装配点是第一个会驱动设备的命令**（M6 的自主闭环 / M3 的 Dry Run）。
plan 的接线前置已改成删除线 + 更正说明；`agents/tools.py` 的同类声称在早前一轮已改。
**教训**：Task 1.3 的「装配点归 Task 2.4」是**推测**，写进 docstring 时就该写成
将来时 + 待核实——它差一点变成一条假声称。

### ⚠️ 偏离登记 2：`no_history_reason`（plan 没预料到的输入态）

`trace_history` 对**不存在的 trace 库**抛 `ValueError`（P2 的既有语义）。而「还没跑过
用例」是**合法状态**，`mta plan` 不该因此拒绝服务。但把「读不出来」也按 0.0 兜住就是
静默降级。按「**空结果的成因必须可分辨**」处理：

| 状态 | 处置 |
|---|---|
| trace 库**不存在** | 合法：历史项按 0.0 计，原因进 `notes` **且每条 `reasons` 的 history 行标注**「（无历史数据：trace 库尚不存在）」 |
| 库存在但**读不了** | **exit 3**（`trace_history` 抛 → `cmd_plan` catch 成前置错误），**不**用上面的开关兜 |

分界由 **CLI 用文件存在性**判（不解析异常消息——文案不该承担语义，Task 1.3 P3-2 的
同一课）。`plan()` 的 `no_history_reason` docstring 明写这条边界。

### ⚠️ 偏离登记 3：`git_commit` 必须 rev-parse 成**确定的 sha**

`plan_id` 由 `(app_build, git_commit, changed_files)` 内容寻址，而 `HEAD` 是**移动的
ref** —— 两个不同的提交会算出同一个 `plan_id`，后写的**静默覆盖**前一份。所以
`cmd_plan` 额外跑一次 `run_git(repo_root, "rev-parse", head_ref)`。
这正是 `review_p3_task14` 登记的「M2 若要做『Plan 可追溯到确定的 commit』需额外一次
rev-parse」的落点。`range.head` 仍如实显示 metadata 里写的（可能是 `HEAD`），
两者**故意不同**：前者是标识（必须定），后者是口径（如实反映输入）。

### 实现期由测试抓到的两处真问题

1. **`_read_steps` 的翻译面漏了 `DatabaseError`**：`sqlite3.OperationalError` 是
   `DatabaseError` 的**子类**，所以「文件不是 sqlite 库」「库被加密」抛的 `DatabaseError`
   **直接漏到了 CLI**（traceback）。该方法的 docstring 承诺「把裸 sqlite 异常转成可诊断
   的域错误」—— 捕父类才让这句声称成立。**P2 侧改动**，全量回归 1607 通过。
2. **Gate 脚手架的第三个 commit**：metadata 写盘后又 `git commit` 了一次，于是
   `HEAD~1..HEAD` 变成「metadata 的改动」而不是「Swift 的改动」，Plan 空。修法是
   **写盘但不提交**（metadata 是生成产物，读取走文件系统不走 git）。

### 更正三处会变成假声称的 docstring（「文档声称必须有落点」第 7 次）

| 位置 | 原文 | 更正 |
|---|---|---|
| `agents/storage.py` | 「CLI `--agent-db` **尚未接线**」 | 「**已接线**（Task 2.4 的 `mta plan` 直接 import `DEFAULT_AGENT_DB`）」——`review_p3_task11` P3-1 记的「写了 flag 而零命中」就此核销 |
| `knowledge/retrieval.py` | 「「单一入口」目前**零生产调用者**」「第一个真实调用点是 Task 2.3 的 `planner/planner.py`」 | 改成「第一处生产调用点 = `cli/main.py::cmd_plan`」+ 一句更正：**`planner/planner.py` 不是调用者**（它把 `knowledge` 当**参数**收）——原措辞把「使用方」当成了「构造方」 |
| `docs/phase3-plan.md` Task 2.4 前置 ②④ | 「`--policy` 目前不存在」「装配点归 Task 2.4」 | 回填拍板结论 / 删除线 + 证伪说明 |

### Gate M2（`phase0/verify_p3_m2.py`）——**exit 0，8/8 PASS**

**自足工程**：在 `out/p3_m2_gate/gate_env/` 下自建 `.git` + `generated/local` + suites
（**不碰真实仓库**）；建仓后断言 `--show-toplevel == 工程根`（P2 M4 Gate 的
「`rev-parse` 穿透到主仓库」事故在此显式防御）。「一次真实改动」= 改同一个
`LoginDemoApp.swift` 再提交。

| # | 判据 | 结果 |
|---|---|---|
| G1 | 矩阵 #1：`--env-kind production` → 启动即拒（含 `F7`）+ **不留任何库文件** | PASS |
| G2 | 排序正确（**CRITICAL 居顶**）/ `reasons` 非空可追溯 / `git_commit` 是 40 位 sha / `range` 可见 | PASS（5 条子断言） |
| G3 | 落库：按 `plan_id` 回查 `test_plans`，`tasks_json` 与输出一致 | PASS |
| G4 | 幂等：同输入重跑 → 同一 `plan_id`、**库不增长** | PASS |
| G5 | **矩阵 #5**：本地 LLM 桩返回**跨分重排** → 丢弃重排 + 保留确定性顺序 + `audit` 留痕（`llm_order_rejected`）；给过 id 的解释生效、没给的没有 | PASS |
| G6 | **矩阵 #6**：`changed_files` 为空 → 空 Plan + 明示 | PASS |
| G7 | 「无历史」标注 vs「读不出来」exit 3 的分界 | PASS |
| G8 | 全量回归 `pytest tests` 全绿 | PASS |

### 实测

- 全量 pytest **1609 passed**（Task 2.4 新增 **23**：`test_cli_plan.py`；1586 + 23 = 1609）。
- Gate M2 脚本 **exit 0**，`out/p3_m2_gate/summary.json` 落档。
- **九条 A/B 探针全部真红**（每次先读原文、改后立即还原并断言字节相等）：
  ① 删 F7 前置调用 → 2 red；② `command=` 与函数名不一致 → 1 red（M1 守卫）；
  ③ `env_kind` 硬编码字面量 → 2 red；④ 去掉 `rev-parse` → 1 red；
  ⑤ `is not None` 改 `if args.policy` → 1 red；⑥ 去掉「库不存在」判定 → 7 red；
  ⑦ 去掉落库 → 4 red；⑧ 翻译面收回 `OperationalError` → 1 red；
  ⑨ 不传 `no_history_reason` → 7 red。
- **`cat >>` 重复执行**（仓内守卫记着的老坑）本轮又踩一次：两个新测试被追加两遍 →
  `test_repo_hygiene.py::test_test_files_collect_count_matches_definitions` **抓到**。
  已去重；全仓重复顶层定义**零命中**。
- `-W error::pytest.PytestCollectionWarning` 下零警告。

### M2 收口

**Gate M2 判据全绿**：`mta plan` 输出可解释的优先级排序（`reasons` 非空、依据可追溯）；
评分纯函数可单测（Task 2.2/2.3）；LLM 重排被拒（矩阵 #5）与空改动明示（矩阵 #6）
在 CLI 端到端跑通。→ 打 tag **`checkpoint-p3-m2`**。
