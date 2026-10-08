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
