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

---

## Task 3.1 完成记录（P3-09 TestCase Schema 扩展，2026-10-09）

**M3 第一个任务**。Candidate Test = P1 TestCase + 溯源字段（F5），扩展方式是
**Modify 既有模型**。

### 交付

- `testcase/schema.py`：新增 `GenerationEvidence`（`coverage_gap` / `bug_history` /
  `source_refs`，三键**必填**、`extra="forbid"`）；`TestCase` 末尾追加三个可选字段
  （`status: Literal["CANDIDATE"] | None` / `generated_by: str | None` /
  `generation_evidence: GenerationEvidence | None`）。`SCHEMA_VERSION` **不变**（`"0.2"`）。
- `tests/unit/test_testcase_candidate_fields.py`（**13** 例）、
  `tests/unit/test_lint_candidate_compat.py`（**3** 例）——共 **16** 例。
  （8 + 3 个 `def test_`，含 `parametrize` 展开 4 + 3；见「评审修订记录」。）

### 决策记录

1. **Modify 既有模型**（不建 `generator/models.py` 平行继承）：`extra="forbid"` 下平行
   模型会拒新键，且违反 F5「同一 Schema」。
2. **`GenerationEvidence` 的三个列表必填**（空即写 `[]`）：设计 6.2 示例三者俱在。
   「没给」与「显式为空」由此可分辨——与本仓反复踩的「形式合法、语义等于没有」同一形态。
   plan 只规定了**外层**字段可选（`= None`），内层是否可选属本次拍板。
3. **元素类型定为 `list[str]`，不用 `Any`**：设计示例即字符串
   （`"Search -> EmptyResultState"` / `"SearchView.swift"`），未给结构化元素契约；且
   `generation_evidence` 会随用例序列化进 `test_candidates.testcase_yaml` 与最终落盘的
   suite YAML，必须是 YAML 友好、可人工复核的形态。生产侧（Task 3.4）若确需结构化元素
   → **改这份契约**的立项。
4. **`status` 用 `Literal["CANDIDATE"]`，不引用 `AssetTier.CANDIDATE`**：后者是**工具资产
   分级**（设计 4 节），与「用例状态」是不同概念；且 `testcase/schema.py` 是纯数据模型
   （H18），不 import 执行层。
5. **无跨字段耦合**：`status="CANDIDATE"` 不强制 `generation_evidence` 必填（plan 未要求；
   最小实现，不凭空收紧契约）。

### ⚠️ 偏离登记 1：设计 6.2 的示例**漏写了 `name`**

示例只有 `schema_version / id / status / generated_by / generation_evidence / suite /
steps`，而 `name` 是 P1 必填字段 → **示例本身过不了 strict parser**（实测：报
`name Field required` + 3 个 extra 键）。**不放宽 `name`**（P1 契约不改，F5「同一 Schema」）；
测试夹具补上 `name` 并在注释里写明。设计文档这处待回填。

### ⚠️ 偏离登记 2：「既有用例解析结果逐字节不变」的口径按实测写

plan 的 Steps 写「逐字节不变」，但**加字段后 `model_dump()` 必然多出三个 `None` 键**——
字面「逐字节」不可能成立。实际钉的是**语义不变**：真语料 **20/20** 满足
`TestCase.model_validate(tc.model_dump()) == tc`，且三个新字段一律 `None`。
测试名与 docstring 按实测写（**不写「逐字节」**），避免「声称与实测不符」。

### ⚠️ 偏离登记 3：drive-by 更正模块 docstring

`testcase/schema.py` 首行原写「Testcase schema **0.1** 模型」，而 `SCHEMA_VERSION` 是
`"0.2"`；且原则行「只建设计 6.3 列出的字段」在加了 6.2 的 Candidate 字段后不再成立。
一并更正为「设计 6.3 字段 + 6.2 Candidate 溯源字段」。属「文档与实测不符」的同一形态。

### 实测

- 全量 pytest **1625 passed**（1609 + 16）。
- **5 条 A/B 探针全部真红**（每次先读原文、改后立即还原并断言字节相等）：
  **A** 删三个字段 → **6** red（含两条 lint 路径测试）；**B** 三列表给默认值 → **3** red；
  **C** 去掉 `extra="forbid"` → **1** red；**D** 三列表改裸 `list` → **1** red；
  **E** `Literal` 改裸 `str` → **4** red（`test_status_accepts_only_candidate_literal`
  的 4 个参数全红）。
- 真语料：`suites/` **20/20** round-trip 恒等、新字段全 `None`；全量 lint **0 个
  `schema_invalid`**。（其余既有 issue 由环境/生成仓库决定，**不冻结**：用
  `EnvSecretProvider` 实测 **46** 条，全是 `unknown_secret` / `unknown_target`——
  与本次扩展无关。）
- `-W error::pytest.PytestCollectionWarning` 下零警告。

### 教训（已并入 MEMORY §5，2026-10-09）

- **设计示例本身可能过不了 strict parser**：抄示例当夹具前先跑一次——`name` 缺失、
  `schema_version` 笔误（`0.1` vs 现行 `0.2`）是同一类。
- **「逐字节不变」这类强口径在加字段后必然失真**：要么改口径（语义/round-trip），
  要么别用这个词。
- **报测试例数要报 `--collect-only` 的数，不是 `def test_` 的数**：本次把 13 报成 12，
  就是把 4 个 `parametrize` 参数当成 1 个。凡「例数」二字，一律以收集结果为准
  （探针计数同理：跑 `pytest` 看 `N failed`，别按测试函数个数心算）。

---

## Task 3.1 评审修订记录（2026-10-09，round 1）

**评审报告**：`review/review_p3_task31_2026-10-09.md` —— 结论 **有条件通过，0×P2 + 3×P3**，
实现本体零缺陷；三条 P3 全是文档/数字口径。评审子代理 6 次 A/B 探针全部真改文件并还原
（`git diff --stat` 核对为空），另复跑全量 **1625 passed**、语料 20/20、lint 0 个
`schema_invalid`、46 条既有 issue，均与自述吻合。

### 修订 1（P3-1）：分文件例数 12/4 → **13/3**

- **位置**：本文件「交付」段（原 1561-1562 行）+ 提交 `72106e1` 正文。
- **实测**：`--collect-only` → `test_testcase_candidate_fields.py` **13**、
  `test_lint_candidate_compat.py` **3**（`def test_` 8 / 3，含 `parametrize` 展开 4 + 3）。
  **总数 16 对，分文件数两个都不对**——我写的时候按 `def test_` 计数，忘了展开
  `parametrize`。
- **修法**：本文件改为 13/3 并注明展开口径。提交信息属历史记录不可改，以本记录为准。

### 修订 2（P3-2）：探针 E 计数 3 → **4**

- **位置**：本文件「实测」段探针 E + 提交 `72106e1` 正文。
- **实测**：把 `testcase/schema.py:220` 的 `status: Literal["CANDIDATE"] | None`
  改成 `status: str | None` → **4 failed**（`test_status_accepts_only_candidate_literal`
  的 4 个参数全红）。自述 3 是**漏数一个参数**。
- **性质**：方向是**自述弱于实测**（真实守卫更强，非空转），不改变结论；仍属
  「口径与实测不符」，一并更正。

### 修订 3（P3-3）：回填设计 §6.2 的勘误（plan 要求、原提交漏做）

- **位置**：`docs/mobile-test-agent-phase3-design.md` §6.2 示例。
- **问题**：plan Task 3.1 勘误明写「**勘误回填设计文档**」，但原提交只登记了
  「示例漏 `name`」，`schema_version: "0.1"` **未回填、也未登记为待办** —— 属
  「声称要做而没做」。设计 §6.2 是 Task 3.3/3.4 抄示例的直接来源，谁抄谁踩。
- **修法**：示例 `"0.1"` → `"0.2"`，并补上 P1 必填字段 `name:`（与偏离登记 1 同一处）；
  两处均加行内注说明系 Task 3.1 勘误回填。
- **验证**：探针把文档里的 6.2 YAML 块抠出来直接喂 `parse_testcase_dict`
  （仅把伪记法 `steps: [...]` 换成 `[{"action": "launch_app"}]`）→ **PARSED OK**，
  `status="CANDIDATE"`、`generation_evidence` 三键齐。
- **连带**：`docs/phase3-plan.md:289` 的 `testcase/schema.py:15` 因本次 drive-by 扩行
  已漂到 `:19`，一并更正；勘误句改为「已回填」。

### 修订 4（小观察，顺手清）

- `tests/unit/test_testcase_candidate_fields.py` 夹具注释补一句：设计文档已回填 `name`，
  夹具保持不变（它同时钉住「缺 `name` 的裸示例过不了 strict parser」）。
- `tests/unit/test_lint_candidate_compat.py` 头部 docstring：「由环境变量…决定」收窄为
  「由 `SecretProvider` 实现与 `repository/generated/` 内容决定」（该文件用自造
  `_KnownSecrets`，不读环境变量）。

### 未修订项（登记为后续动作）

- **小观察 1**（`GenerationEvidence` 元素级空串 `[""]` / `["   "]` 放行）：当前无消费者、
  失败形态不是「结果偏低」，**不升级**。Task 3.4 若生成器可能写空串元素，届时补元素非空闸门。
- **小观察 2**（语料 lint 测试只钉 `schema_invalid`，窄于 plan 判据 ④）：理由正当
  （其余 issue 码取决于被 `.gitignore` 的 `repository/generated/local`），且探针 F 证明
  该测试有牙（`status` 改必填 → 真红），**不判缺陷**。

### 修订后实测

- 全量 pytest **1625 passed**（与修订前一致；本轮只动文档 + 测试注释/docstring，
  未动实现，未增删测试）。
- 设计 §6.2 示例探针 **PARSED OK**（见修订 3）。
- 工作区：`testcase/schema.py` 本轮**未改**；无残留探针脚本。

---

## Task 3.1 评审修订记录（2026-10-09，round 2 / 收口核销）

**评审报告**：`review/review_p3_task31_final_2026-10-09.md` —— 结论 **通过**。
上一轮 round 1 的 3×P3 + 4 条小观察**全部核销 7/7**；本轮新增 **0×P2 + 2×P3**，
均为文档 / 版本号口径，**无回归**。

### 核销结论（round 1 的 7 项）

| 项 | 核验 | 结果 |
|---|---|---|
| P3-1 分文件例数 12/4 → 13/3 | `--collect-only -q` → **13** / **3**（`def test_` 8 / 3） | ✅ |
| P3-2 探针 E 计数 3 → 4 | A/B 真改 `Literal`→裸 `str` → **4 failed** | ✅ |
| P3-3 设计 §6.2 勘误回填 | 抠文档 YAML 块喂真 parser → **PARSED OK**；"0.1"→"0.2" + 补 `name` | ✅ |
| 小观察 3 / 4 | docstring 措辞收窄；plan:289 `:15`→`:19` | ✅ |
| 小观察 1 / 2（未升级） | HEAD 复验：空串仍放行、lint 测试仍有牙（探针 F2 → 4 failed） | ✅ |

### 修订 1（P3-1）：`plan:35` 的另两处 `path:line` 引用已漂

- **位置**：`docs/phase3-plan.md:35`（§0「Executor 能力」行）。
- **实测**：`testcase/schema.py:119` → 实为 **:123**（`action: Literal[...]`）；
  `:141-149` → 实为 **:145-153**（`condition: Literal[` 起始 + 7 条件 + `]`）。
  两者在 `4cea731`（P2 完成点）与 `72106e1^` 上**都对**，Task 3.1 把模块 docstring
  从 7 行扩到 11 行后**同步下移 4 行**。
- **为什么记一笔**：round 1 的小观察 4 报的正是这个形状，而修订**只修了被点的那一处**
  （plan:289）。MEMORY §5.A：「修完被点的那一处要顺手扫全包」——`docs/phase3-plan.md`
  对 `schema.py` 的 `path:line` 引用只有两处（:35 与 :289），扫全的成本是一条 grep。
- **修法**：`:119` → `:123`、`:141-149` → `:145-153`。
- **教训**：**改模块 docstring / 头部注释后，grep 一次所有引用该文件的 `path:line`**
  （扩行会让全文件锚点同步下移）。

### 修订 2（P3-2）：testcase 侧代码里残留的 `0.1` 版本号口径

- **位置与修法**：

| 位置 | 原文 | 修法 |
|---|---|---|
| `testcase/lint.py:157` | `f"... failed 0.1 schema: {detail}"` | `"failed {SCHEMA_VERSION} schema: ..."`（**单点派生**，不写第二个字面量） |
| `testcase/lint.py:4` | 「过不了 0.1 strict parser」 | 删版本号，改「过不了 strict parser」 |
| `testcase/loader.py:1` / `:35` | 「schema 0.1 分发入口」/「走 0.1 strict 分发」 | 删版本号 |
| `tests/unit/test_schema.py:178` | 「必须能过 schema 0.1」 | 改「必须能过 strict schema」 |

- **依据**：`SCHEMA_VERSION` 自 `7ae8a7e`（P1-04）起就是 `"0.2"`，`0.1` 在 testcase 侧
  已不存在；`lint.py:157` 是自 `d4aa543`（P1-03）写入后未改的 P1 存量。
- **为什么记一笔**：round 2 刚为「设计文档写了过时的 `0.1`」做了勘误回填，理由是
  **过时的版本号会让人按错的版本去理解/抄写**。代码里这几处是同一形状的**另一半落点**，
  `lint.py` 那条还是**用户可见的错误文案**（20 条 lint 报错全写「failed 0.1 schema」）。
- ⚠️ **两处 `0.1` 不是这一族，没动**：
  ① `planner/models.py:91` `PLAN_SCHEMA_VERSION = "0.1"` 是 **TestPlan 自己的**版本
  （`test_plans` 信封，与 TestCase 的 0.2 无关；`tests/unit/test_planner_models.py:47-55`
  与 `tests/unit/test_cli_plan.py:206` 钉着它）；
  ② `tracer/**` 的 `schema 0.1` 是 **trace schema**（设计 14.2），是另一个 schema。
- **验证**：
  - `lint.py` 改为从 `testcase.schema` 导入 `SCHEMA_VERSION`（该文件已 import 同模块）
    → **版本只有一份定义**。A/B：把 `SCHEMA_VERSION` 改成 `"0.3"` → 报错文案跟着变
    `failed 0.3 schema` → 证明不是又抄了一个字面量。
  - 新文案实测：`testcase 'login_again_001' failed 0.2 schema: …`。
  - 全仓 `grep 'failed 0.1 schema' tests/ phase0/` → 改前**零命中**（无测试钉整条 message）。

### 修订后实测

- 全量 pytest **1625 passed in 17.08s**（与修订前逐位一致）。
- `tests/unit/test_lint.py` + `test_schema.py` + `test_cli.py` → **55 passed**。
- `test_repo_hygiene.py` + 两个 Task 3.1 新文件、`-W error::pytest.PytestCollectionWarning`
  → **21 passed** 零警告。
- A/B 1 次（`SCHEMA_VERSION` → `"0.3"`，验证文案跟随），已还原，`git diff --stat` 为空。

### ⚠️ 本轮踩的坑：A/B 探针留下**陈旧字节码**，`git diff` 看不出来

改完 `lint.py` 后跑全量 —— `test_plan_version_is_independent_of_testcase_schema`
**1 failed**（`assert TESTCASE_VERSION == "0.2"` 得到 `0.3`）。而 `testcase/schema.py`
的 `git diff` **为空**、`grep SCHEMA_VERSION` 也是 `"0.2"`。

**根因**：A/B 探针用 Python 脚本把 `SCHEMA_VERSION = "0.2"` 改成 `"0.3"` 再改回，
**两次写入落在同一秒内、且字节数不变** → 文件的 `st_mtime`（秒精度）与 `st_size`
**都与改前完全一致** → CPython 的 `.pyc` 校验（比对 source mtime + size）判定缓存
**仍然有效**，于是继续用着含 `0.3` 的那份字节码。

**验证**：直接 `import` 拿到 `0.3`；`marshal` 解 `.pyc` 的 `co_consts` 里确实是
`'0.3'`；而源文件 `sed -n 19p` 是 `"0.2"`。清 `__pycache__` 后恢复 `"0.2"`，
全量 **1625 passed**。

**教训（已并入 MEMORY §1）**：
- **A/B 探针还原后要清 `__pycache__`**（或让探针在写入前 `touch` 出一个不同的
  mtime）；**只核对 `git diff --stat` 挡不住字节码级残留**。
- 出现「源码与运行结果不一致、而 git 干净」时，**先怀疑陈旧 `.pyc`**，再怀疑回归。
- 同理：**探针改常量时优先改一个长度不同的值**（`"0.3"` 与 `"0.2"` 等长，size
  不变会让 mtime 成为唯一判据，而它是秒精度）。

---

## Task 3.2 完成记录（P3-10 Coverage Gap 计算，2026-10-09）

**M3 第二个任务**。设计 6.3：`coverage_gap(graph, existing_tests)` —— 找出
「图里有、现有用例从未覆盖」的转移，作为 Task 3.4 生成候选的输入。

### 交付

- `generator/__init__.py`、`generator/coverage.py`：
  `coverage_gap(graph, existing_tests) -> list[UncoveredTransition]`（纯函数）、
  `covered_transitions(existing_tests)`（覆盖集，与前者共用一份推导）、
  `UncoveredTransition`（frozen dataclass，带 `__post_init__` 入参闸门）、
  三个分类常量（`CATEGORY_HAPPY_PATH` / `CATEGORY_BRANCHING` /
  `CATEGORY_ERROR_RECOVERY`）。
- `tests/unit/test_coverage_gap.py`（**20** 例）。

### 决策记录

1. **全集取 Runtime Graph，不是 Source Graph**（对设计 6.3 字面的一处偏离）。
   设计原文签 `graph: ScreenGraph`、docstring 写「**Source Graph** 中存在…的
   Transition」。实测 `build_source_graph` 的转移**恒为 0**（metadata 无导航
   声明，`graph/builder.py` 模块 docstring 已逐关键词扫过注明）→ 拿它当全集，
   本函数**恒返回空列表**，正是本仓「形式合法、语义等于没有」那一类。
   且「**触发**」本就是运行时概念。**已与用户确认。**
2. **「已覆盖」= 用例静态推导，不读 trace**。runtime 图的每条转移都来自
   **已执行过的 trace**，以它为全集又拿它当覆盖集 = 自己减自己 → 恒空、判据无
   鉴别力。故从用例文本推：`wait_for screen:X` / `postcondition screen:X` 是
   「到过 X」的声明，相邻到达点 + 触发动作 = 一条转移。**已与用户确认。**
3. **本函数拒绝 `source_of='source'` 的图**（`ValueError`）：静默接受一张恒空
   的图，等于把「源图没有导航声明」伪装成「没有 gap」。
4. **trigger 形状复用 `graph/builder.py::_trigger_of`**（造真 `TraceStep` 喂它），
   不另写 `f"{action}:{target}"` —— plan §2 第 1 条「同一套逻辑」要求，测试里有
   显式断言。
5. **分类标签是附注字段，不是分类器**（plan Steps 明文「初版启发」）：只按触发
   动作形状打三类标，不建立七类分类体系。

### ⚠️ 偏离登记 1：设计签名的 `ScreenGraph` 类型不存在

设计 6.3 写 `graph: ScreenGraph`，但仓内**没有** `ScreenGraph` 类型——`build_source_graph`
返回的也是 `RuntimeGraph`（`source_of='source'`）。按 plan Task 3.2 的
`graph: RuntimeGraph` 落地（plan 与设计在此处不一致，以可实现的为准）。

### ⚠️ 偏离登记 2：classification 只覆盖七类中的三类

设计 6.3 列七类（Happy Path / Boundary / Negative / Exception / State
Transition / Concurrency-Timing / Recovery），本次只实现可由**转移形状**判别的
三类。**其余四类需要用例语义**（是否边界值、是否异常路径），不是转移本身能回答
的——Task 3.4 若需要，是扩展 `_classify` 的立项，不是本次遗漏。

### ⚠️ 偏离登记 3：真语料实测「零 gap」是本任务的正常结果，不是空转

全量 20 个用例 → gap = **0**（8 条转移全被覆盖）。这不是判据失灵：
去掉 `login_again_001` 一个用例 → 如实多出 1 条 gap
（`LoginView→HomeView@tap:LoginView.login_button`，`observed_count=10`）。
**鉴别力由「去掉一个用例」证明，不是靠全量结果非空。**

### 实测

- 全量 pytest **1645 passed**（1625 + 20；清 `__pycache__` 后复跑同值）。
- 真语料 + 真 trace（`out/p1_m2_gate/trace.db`，8 条转移）：
  覆盖集 **8/8** 与 runtime 图吻合、**零误报**（覆盖集里没有图上不存在的条目）；
  全量用例 gap=**0**；去掉 `login_again_001` → gap=**1**（见偏离登记 3）。
- **7 条 A/B 探针全部真红**（每次改完还原、`git diff --stat` 核对为空）：
  A 全集不减覆盖集 → **4** / B 去自环折叠 → **1** / C 去 source 图闸门 → **1** /
  D trigger 恒空 → **5** / E 分类恒 HAPPY_PATH → **1** / F 去排序 → **1** /
  G `observed_count` 恒 0 → **1**。
- `test_repo_hygiene.py` + `test_knowledge_retrieval.py` → **23 passed**；
  `-W error::pytest.PytestCollectionWarning` 下零警告。

### 教训（已并入 MEMORY §5）

- **判据要钉在「产生问题的那一侧」，不是钉在结果上看巧合**：本次「自环折叠」
  的断言原先只在「图里有自环」时才观察得到差异，而 runtime 图按构造**永远没有
  自环** → A/B 探针去掉折叠后**仍 19 passed**（空转）。改成直接断言
  `covered_transitions` 的输出不含 `X→X` 后才真红（1 failed）。**守卫空转的
  第六种形态：断言依赖一个被测对象按构造不会产生的输入。**
- **「零 gap / 零差异」类结果要先自问鉴别力**：判据的鉴别力由「拿掉一个输入
  后结果是否变化」证明，不能靠「结果非空」——真语料「全都覆盖了」恰恰是健康态。

---

## Task 3.2 评审修订记录（2026-10-09，round 1）

**评审报告**：`review/review_p3_task32_2026-10-09.md` —— 结论 **有条件通过，
1×P2 + 6×P3**（报告另附 P3-7/P3-8 两条附加观察）。评审跑了 **14 个 mutant**，
每个都先备份 → 改 → **清 `__pycache__`** → 跑 → 还原 → 再清 → 核对，并逐条标注
「真红 / 空转 / 有语义后果但无测试」。

提交信息与审计记录的**全部声称均被复现**：全量 1645 passed、7 条 A/B 探针红数
4/1/1/5/1/1/1、真语料 8/8 覆盖零误报、留一法 gap=1。评审还用留一法找到
**4 个**能让 gap 变化的用例（作者只举证了 1 个），鉴别力比作者举证更强。

### 修订 1（P2-1）：否定等待被当成「到达过」——真 gap 静默塌成 0

- **位置**：`generator/coverage.py::_arrivals` 的 wait 分支。
- **问题**：`wait_for screen:X condition=not_exists` 断言的是「X **不**在这里」，
  原实现把它当成一个**到达点** → 凭空造一条覆盖（如
  `LoginView→HomeView@''`）。错误落在「**没覆盖**」那一侧，正是 fail-loud 的对象。
- **真语料命中**：`suites/account/login_negative_002.yaml`（空密码登录失败 +
  `wait_for screen:HomeView not_exists`）。**该用例此前恰好只有一个到达点**
  （配对不出转移），所以缺陷被巧合掩盖——我构造了第二个到达点复现出假覆盖。
- **修法**：新增 `NEGATIVE_WAIT_CONDITIONS = {"not_exists", "disabled"}`，
  命中则**不计入到达**（`disabled` 对屏不成立，一并排除以防 schema 将来放开）。
- **回归**：真语料 8/8 覆盖、gap=0、去掉 `login_again_001` → gap=1 **全部不变**；
  `login_negative_002` 的贡献从「（巧合的）无」变成**确定的空**。
- **新增测试 3 条**（含真语料那条）。A/B：删掉排除 → **1 failed**。

### 修订 2（P3-1）：「同一套逻辑」断言是**空的** → 改用 spy

- **位置**：`test_trigger_shape_reuses_graph_builder_primitive`。
- **问题**：原断言只验 `_trigger_of(TraceStep(...)) == "tap:..."`，验的是**被调用方
  自己的行为**；把 `_step_trigger` 换成行为逐字等价、但不再调 `_trigger_of` 的实现，
  **20 passed**（mutant 实测）——守卫空转（形态 ②）。
- **修法**：`monkeypatch` 替换 **`generator.coverage` 的命名空间**（该模块用
  `from graph.builder import ...` 把名字绑到本地，patch `graph.builder` 无效——
  那正是让这条测试恒绿的第二个坑），spy 记录调用并断言传入的是真 `TraceStep`。
- **A/B**：改成行为等价但不调 `_trigger_of` → **1 failed** ✅。

### 修订 3（P3-2）：`postcondition` 分支零测试 → 补 2 条

mutant（删整条分支）此前 **20 passed**；现补 `postcondition` 计入覆盖 /
无动作时不计入两条 → mutant **1 failed**。

### 修订 4（P3-3）：`_RECOVERY_ACTIONS` 清空仍全绿 → 补参数化分类测试

原 20 条测试**无一构造** `launch_app`/`terminate_app` 转移。补 4 组参数化
（`launch_app`/`terminate_app`/`back`/`tap` 各打各的标）+ 真语料
`suites/regression/terminate_relaunch_001.yaml` 一条 → mutant **3 failed**。

### 修订 5（P3-4）：`trigger`/`observed_count` 闸门零测试 + `bool` 放行

- `observed_count=True` 被放行（**`bool` 是 `int` 子类**，MEMORY §5.A 明文点名要排除
  的一类）→ 闸门加 `isinstance(..., bool)` 排除。
- 补 5 组参数化（`from_screen=42` / `to_screen=""` / `trigger=1` /
  `observed_count=-1` / `observed_count=True`）→ A/B **1 failed**。

### 修订 6（P3-5）：`getattr(graph, "transitions", ())` 静默兜底（本仓**第 5 次**同形）

MEMORY §5.A 记的 `getattr` 静默兜底已踩四遍，这是第五次。改为**直接取属性**。

⚠️ **这条测试自己也空转了一轮**：初版写 `coverage_gap(object(), [])` 期望
`AttributeError`，但 `object()` 连 `source_of` 都没有，**在闸门那步先炸** →
断言恒成立；把 `graph.transitions` 换回 `getattr(...)` 仍然全绿。改成构造一个
「过得了 source 闸门、但缺 `transitions`」的对象后才真红（mutant **1 failed**）。

> **新增教训：断言 `AttributeError` 前要确认异常来自被测的那一步**——更早的
> 闸门先炸会让断言恒成立。这与 §5.C ⑥「钉产生问题的那一侧」同一形态。

### 修订 7（P3-7）：去重注释不成立

`RuntimeGraph.__post_init__` **不去重**（去重是 `build_runtime_graph` 建图时的行为，
构造点可直接塞重复转移）。原注释「图上同一条转移不会重复」不成立——实测重复转移
出 **2 条 gap**（Task 3.4 会拿到重复候选）。已改为按值语义去重（保留首次出现）
并补测试；`test_result_has_no_duplicates` 改用**真的塞重复转移**的图（此前对
`GRAPH` 恒真，14 个 mutant 无一能红）。

### 修订 8（P3-6 / P3-8）：docstring 声称宽于实现

- **P3-6**：两个分类常量的 docstring 声称了 `_classify(trigger)` **结构上拿不到**
  的判据（「不是从详情返回」「目标屏是返回路径」）。实测反例：
  `DetailView→HomeView@tap:…back_cell` 被打成 `Happy Path`（它确实是回边，只是
  触发动作不是 `back`）。已改为只描述**实际判据**，并写明这是**兜底标签**。
- **P3-8**：`_step_trigger` 的「trigger 全是动作步骤」原写成构造性保证，实为
  **实测如此**——runtime 侧取值域本来更宽（`_trigger_of` 取紧邻前一步，可为任意
  `step_type`）。真语料命中 **0** 次；若 Task 3.4 接线后出现 `wait_for:` 前缀的
  trigger，会多出假 gap。已在 docstring 写明，**登记「M4 接线后升级为 P2」**。

### 修订后实测

- 全量 pytest **1662 passed**（1645 + 17；清 `__pycache__` 后复跑同值）。
- 7 条 A/B 探针**修订后全部真红**（P2-1 移除 → 1 / spy 失效 → 1 /
  删 postcondition → 1 / `_RECOVERY_ACTIONS` 清空 → 3 / bool 闸门 → 1 /
  去重移除 → 1 / `getattr` 兜底 → 1）。
- `test_repo_hygiene.py` `-W error::pytest.PytestCollectionWarning` → 5 passed 零警告。

### 教训（已并入 MEMORY §5）

- **「只验被调用方的行为」不等于「钉住复用」**：plan §2 第 1 条要求钉「同一套逻辑」，
  断言 `_trigger_of(step) == "tap:..."` 只证明了 `_trigger_of` 自己没坏——要钉
  **调用**（spy / `monkeypatch`），且**注意 patch 的是使用方的命名空间**。
- **「断言会抛异常」也可能恒成立**：更早的闸门先炸，断言照样绿。写
  `pytest.raises` 前确认**异常来自被测的那一步**。

---

## Task 3.3 完成记录（P3-11 Test Fingerprint 去重，2026-10-09）

**M3 第三个任务**。设计 F8：Test Fingerprint = screen 序列 + action 序列 +
assertion 的哈希，**用同一套哈希工具**，**只做精确哈希相等**（禁一切相似度 /
模糊匹配 / 编辑距离）。用途：Task 3.4 生成器的去重（设计 6.1「命中已有用例 →
SKIP」）。

### 交付

- `source/hashing.py`：`stable_hash(parts: Iterable[str]) -> str`（拼接 → sha256
  → 前 16 hex；不排序、不去重，规范化交给调用方）。
- `candidates/__init__.py`：包 docstring（不预建空壳，照 `generator/__init__.py`）。
- `candidates/fingerprint.py`：`test_fingerprint(tc: TestCase) -> str`（纯函数）+
  三个分量 helper（`_screen_tokens` / `_action_tokens` / `_assertion_tokens`）。
- **Modify** `source/screen.py`：`screen_fingerprint` 改调 `stable_hash`，行为不变。
- `tests/unit/test_test_fingerprint.py`（**19** 例）、
  `tests/unit/test_screen_fingerprint_regress.py`（**4** 例）。
- `docs/windows_dev_notes.md`（非工程产物）：Windows 开发环境说明 + 6 条
  POSIX-only 测试的登记（用户指示：跑不了的登记进 docs，由其在 macOS 补跑）。

### 决策记录

1. **复用口径按 plan 修订 #8 落地为「共享同一哈希原语」，不是「字面调同一函数」**。
   两处指纹输入形状不同（XML `page_source` vs step 序列），字面同一函数不可行；
   抽 `source/hashing.py::stable_hash`，规范化（排序 / 去重 / 顺序 / 字段编码）
   留在各自调用方。红线不变：**禁相似度算法**。
2. **三个分量；落点按「这件事是什么」分，不是按步骤数**（⚠️ **原写「每个步骤恰好
   落入一个分量」为假，review_p3_task33 P3-2 实测证伪，已改**）：`actions`（动作
   步骤本身，含 value）、`screens`（**屏目标**的 wait / assertion，以及动作的
   postcondition **当目标是屏**）、`assertions`（**element 目标**的 wait /
   assertion，以及动作的 postcondition **当目标是 element**）。
   **带 `postcondition` 的动作步骤落两个分量**（动作进 actions，到达点/观测按目标
   类型进 screens/assertions）——真语料 `login_again_001` / `logout_001` 各 1 处。
   分量内保序，分量间不保序。
3. **token 用 `repr(元组)` 而非手工拼接**：元组 repr 自带定界（括号 + 引号转义），
   字段里含 `|` / `:` / `,` 也不会与相邻 token 粘连。**有测试钉住**（见 A/B 探针 B）。
4. **不进指纹的字段**：`id` / `name` / `suite` / `tags` / `precondition` / `cleanup`
   / `schema_version` + Candidate 溯源三字段（`status` / `generated_by` /
   `generation_evidence`）——描述「叫什么、从哪来」，不描述「做什么」。
5. **`input` 的 `value` 与断言的 `expected` 进指纹**：不同测试数据（如「空用户名」
   与「空密码」两条负例）动作序列相同，不含 value 会被误判重复而 SKIP 掉一条真
   用例。YAML 里的 value 是占位符 / 字面量、**非密钥**（H9：密钥只经
   `SecretProvider`，不进 YAML），故可入指纹。
6. **`stable_hash` 对裸 `str` fail-loud（`TypeError`）**：`str` 也是 `Iterable[str]`，
   会被逐字符迭代成「形式合法、语义等于没有」的指纹（MEMORY §5.A 反复踩的形态）。
   元素非 `str` 由 `"\n".join` 自然抛 `TypeError`，**不 `str()` 强转**（强转会让
   `1` 与 `"1"` 撞指纹）。

### ⚠️ 已登记的边界（不假装完整）

- **分量间不保留交错顺序**：`[tap A, assert B]` 与 `[assert B, tap A]` 得同一指纹
  （两个分量各自相同）。真语料无「把独立步骤对调」的用例；Task 3.4 若产生此类
  形态，改为保留全局顺序（登记）。
- **`postcondition` 无 `expected` 字段**，element 目标的 token 里以空串占位。
- ⚠️ **`precondition` 不进指纹 → 真语料已有 2 对同指纹**（口径于 round 1 拍板，
  详见下方「评审修订记录」修订 2）：`reset_logout_001` vs `login_001`（步骤逐字
  相同、仅 `reset` 不同）与 `search_001` vs `search_filter_001`（连 precondition
  都相同）。前者是**已知且接受**的代价，Task 3.4 接线后若 SKIP 掉它则升级 P2。

### 实测

- 全量 pytest（Windows 兼容子集）**1679 passed / 0 failed**（1685 collected − 6
  条 POSIX-only deselect；**清 `__pycache__` 后复跑同值**）。基线 Task 3.2 修订后
  为 1662 collected，本任务 **+23**（19 + 4）→ 1685。
- `test_screen_fingerprint_regress.py` 的期望摘要是**重构前**用裸 `hashlib` 独立
  算出的固定值（`b00ee6cdb98b8128` / `ca978112ca1bbdca` / `154fe76ca1499d78`），
  **不是从新实现回抄**——否则重构把两处一起改错也照样绿。
- `test_repo_hygiene.py` + `test_knowledge_retrieval.py`（去 1 条已知 Windows 红）
  `-W error::pytest.PytestCollectionWarning` → **22 passed 零警告**（`test_fingerprint`
  名字以 `test_` 开头，测试里用 `as fingerprint` 别名导入，避免被 pytest 误收集）。
- **10 条 A/B 探针全部真红**（每次改完还原、`git diff` 核对为空）：
  A action token 丢 value → **1** / B 三分量改 naive `":".join` → **3** /
  C `screen_fingerprint` 内联 hashlib（行为等价、不复用）→ **1** /
  D 把命名字段掺进指纹 → **3** / E screen token 丢 condition → **2** /
  F 去掉裸 str 闸门 → **1** / G 元素 `str()` 强转 → **2** /
  H 删 screen postcondition 分支 → **1** / I 删 element postcondition 分支 → **1** /
  J 删 element wait 分支 → **2**。

### 教训（已并入 MEMORY §5）

- **「对抗性 fixture」才钉得住编码选择**：初版边界测试用 `element:A` vs `element:A:`
  想证明 repr 优于 naive 拼接——但 TargetRef 的 sugar 把整串塞进 `id`（`type='element'`、
  `id='element:A'`），两条 naive 串并不同 → **naive-join mutant 仍 20 passed（空转）**。
  换成真正会撞的对抗 fixture（`id="f::x"/value="y"` vs `id="f"/value="x::y"`）后 mutant
  才真红。**写「守卫某实现选择」的测试前，先用 mutant 确认它真能区分两种实现。**
- **分支覆盖率自查照 Task 3.2 修订 3 的老账**：`_screen_tokens` / `_assertion_tokens`
  的 postcondition 分支、`_assertion_tokens` 的 element-wait 分支初版**零测试**——
  与本仓「postcondition 分支零测试」的历史同形，补 3 例后 A/B 探针 H/I/J 才真红。

### 待办（Windows 侧）

- macOS 上补跑全量 `pytest tests`（应 1685 passed），确认 6 条 POSIX-only 测试全绿
  （见 `docs/windows_dev_notes.md` §2）。
  **已核销（2026-10-10，macOS）**：`pytest tests` → **1685 passed / 0 failed**（exit 0），
  6 条 POSIX-only 测试在 macOS 上全绿。

---

## Task 3.3 评审修订记录（round 1，2026-10-10）

**核销对象**：`review/review_p3_task33_2026-10-10.md`（对 `1ede89f` 的独立两段评审）。
结论 **通过（spec 合规）+ 3×P3 + 3 条小观察、0×P2、0 回归**；三条 P3 本轮**全部修订**。

### 修订 1（P3-1）：`_screen_tokens` 的 **wait** 分支零覆盖 + 测试名实不符

**缺陷**：`test_screen_wait_polarity_changes_fingerprint` 的 docstring 说「`screen:X
exists` 与 `not_exists` 是不同用例」，实际改的是 **`steps[6]`（`AssertionStep`）**，
于是钉住的是 **assertion** 分支，**wait** 分支的 `w.condition` 零测试——把
`out.append(repr(("wait", w.target.id, w.condition)))` 的 `w.condition` 换成常量，
**19 passed 全绿**（评审探针 E2 实测 0 red）。

**修法**：该测试改回 `steps[5]`（真正的 `WaitStep`），**并补一条独立的 assertion
分支钉子**——两侧各钉一条，删任一侧的分支都至少红一条。

### 修订 2（P3-3）：`precondition` 不入指纹的**代价**未登记 + 无钉子（**用户拍板**）

**事实**：真语料 20 例**已有 2 对同指纹**——`reset_logout_001`（`reset: LOGOUT`）vs
`login_001`（`reset: RELAUNCH`）**步骤逐字相同、仅 `precondition` 不同**；
`search_001` vs `search_filter_001` **连 precondition 都相同**（语料里真的冗余）。

**拍板（用户，2026-10-10）**：**`precondition`（含 `reset`）不进指纹**。理由：
`precondition` 描述「**怎么起跑**」，不描述「**做什么**」——与 `id` / `name` / `suite`
同类，否则同一用例换个 `reset` 策略或换个 suite 就会被判成新用例而绕过去重。
代价是第一对同指纹**已知且接受**（该 `reset` 维度目前无自动消费者）。

**修法**：① docstring 的「已登记的边界」段写明该碰撞与口径；② 补
`test_precondition_does_not_affect_fingerprint`（把 `reset` 纳入指纹 → 必红）；
③ 补 `test_real_corpus_has_a_known_fingerprint_collision` **故意钉住这个碰撞**
（含前提核验：两条用例确实只差 precondition）——将来谁把 `reset` 纳入指纹、或改这两条
语料，它会红，逼人回到口径重新拍板，而不是让 SKIP 悄悄发生。

### 修订 3（P3-2）：docstring / 审计称「每个步骤恰好落入一个分量」**为假**

**事实**：带 `postcondition` 的 `ActionStep` **同时落两个分量**（动作进 `actions`，
`postcondition` 按目标类型进 `screens` / `assertions`）；真语料 `login_again_001` /
`logout_001` 各 1 处（复核吻合）。

**修法**：`candidates/fingerprint.py` 的落点表改为「**按目标类型**分」并把「动作 +
postcondition 落两个分量」写成显式规则；本文件「决策记录 #2」同步改写（**不改行为**
——这是声称与实现对齐，不是行为变更）。

### 小观察的处置（评审明示不单独立项）

1. `docs/windows_dev_notes.md` §2 第 2 条的**归因文字**笼统（真机制是 `{repo_root!r}`
   的 `repr` 把反斜杠转义成 `\\`，不是「`str()` 用了反斜杠」）——**下一轮顺手改**。
2. `test_fingerprint` 无生产调用者，docstring 用「用途」措辞——**可接受，不改**。
3. `candidates/__init__.py` 的将来时文件清单——**符合 MEMORY §5.C 写法，留着**。

### 修订后实测

- 全量 pytest **1691 passed**（1688 + 3 例：`test_test_fingerprint.py` 19 → **22**）。
- A/B 探针（每次改完还原 → 清 `__pycache__` → 核对 md5）：**E2** wait 条件改常量 →
  1 red（修订前 0 red）；**E2b** 删 screen-wait 分支整体 → 1 red；**E4** 删
  screen-assertion 分支整体 → 1 red（新钉子独立成立）；**把 `precondition` 纳入指纹**
  → 2 red；**改真语料 `reset: LOGOUT`→`RELAUNCH`** → 1 red（碰撞钉的第二条路径）。

### 教训（已并入 MEMORY §5）

- **测试名里的「wait」可能改的是 assertion 步**：**测试名与它实际触碰的分支要对账**
  ——写「守卫某分支」的测试时，**先跑一次 mutant 确认它真的钉住了那个分支**（本仓
  §5.C 形态⑦的姊妹形态：名字钉一处、实际钉另一处，两处都不算被覆盖）。
- **「同一个概念只许一处实现」也适用于「口径」**：`precondition` 不进指纹这个口径
  出现在 docstring / 审计 / 测试三处，任何一处漂移都会让「已知的 SKIP」重新变成意外
  ——用一条**故意钉住碰撞**的测试把它们绑在一起。

---

## Task 3.2 评审修订记录（round 2 / `review_p3_task32_final` 核销，2026-10-10）

> **时序说明**：这份收口评审（`review/review_p3_task32_final_2026-10-09.md`，审查对象
> `0ede83e`）产出在 Task 3.3 提交（`1ede89f`）**之前**，核销落在其后——故本段排在
> Task 3.3 记录之后（任务级分组 + 时间序的取舍，登记于此）。

**核销对象**：该报告的 **1×P2（P2-1'）** + 三条「小观察」（报告明示不单独立项）。

### 修订 1（P2-1'）：`pending` 只被「到达点」消费

**缺陷**：`0ede83e` 为排除否定等待，把 `pending = None` 从「屏到达」那一支挪到了 `wait`
分支之外——**元素 wait** 于是也清 `pending`。而元素 wait 正是 `_arrivals` docstring 写的
「跳过中间的 wait/assertion」那类步骤 → 下一个到达点的 trigger 塌成 `""`，与图上任何转移
都匹配不上（`_trigger_of` 不产空串）→ 用例**真覆盖过**的转移被**误报成 gap**（错误落在
「多报」那侧，Task 3.4 会据此生成无用候选）。

复现（评审给的命令；修订前 `[('BView','')]`，修订后 `[('BView','tap:AView.go')]`）：

```python
tc = P({"schema_version": "0.2", "id": "n", "name": "n", "steps": [
    {"action": "tap", "target": "AView.go"},
    {"wait_for": {"target": "element:AView.spinner", "condition": "exists", "timeout": 5}},
    {"wait_for": {"target": "screen:BView", "condition": "active", "timeout": 5}}]})
_arrivals(tc)   # → [('BView', 'tap:AView.go')]
```

**修法**：复位移进「真的记了一次到达」那一支（`generator/coverage.py:197-210`）：

```python
if (getattr(tgt, "type", None) == "screen"
        and wait.condition not in NEGATIVE_WAIT_CONDITIONS):
    out.append((tgt.id, pending or ""))
    pending = None          # ← 只有到达点消费 pending
continue
```

**⚠️ 偏离登记（与评审建议的一行修法不同，且**有测试钉住**这个不同）**：报告建议
「复位挪回 `screen`-if 内、**否定等待也复位**」。本仓按 MEMORY §5.A「**同一个形状出现
第二次时，修完被点的那一处要顺手扫全包**」收敛到**唯一口径**——`pending` = 「最近一个
**尚未被到达点消费**的动作」，**只有到达点消费它**；否定屏 wait 不是到达点，故同样
**不消费**。否则 `到达 A → 动作 → 断言不在 B → 到达 C` 会产出同一种假 gap（A→C 的
trigger 被清成 `""`），即「同形第二次」未修。

| 形态 | 元素 wait 后的 trigger | 否定屏 wait 后的 trigger | `test_coverage_gap.py` |
|---|---|---|---|
| HEAD（`0ede83e`） | 塌成 `""` ❌ | 塌成 `""` ❌ | 37 passed（**两处都零覆盖**） |
| 报告建议版 | 保留 ✅ | 塌成 `""` ❌ | **1 failed**（否定那条） |
| 本次落地 | 保留 ✅ | 保留 ✅ | **40 passed** |

**新增 3 例**：`test_element_wait_between_action_and_arrival_keeps_trigger`（评审点名要补
的那条）、`test_element_wait_does_not_produce_a_false_gap`（钉**症状**：图上那条转移真被
跑到 → gap 必须为空，而不只是 trigger 形状对）、
`test_negative_screen_wait_does_not_consume_the_pending_action`（同形第二次）。

### 三条「小观察」的处置（报告明示不单独立项）

1. `NEGATIVE_WAIT_CONDITIONS` 注释里 `disabled` 属**前瞻性防御**而非当前需要——**留着**
   （与报告同判）。
2. `_arrivals` 的 8 处 `getattr` 是「三种 step 形态并存的联合类型」所必需，**不算漏网**；
   报告要求 **Task 3.4 引入新 step 形态时复查一次** → 已登记为 Task 3.4 的开工前置。
3. `_step_trigger` 的 `target_id` 类型闸门在当前唯一调用路径上恒不触发（`TargetRef.id` 是
   `str`）——**防御性分支 + 有专测**的健康形态，**保留**。

### 修订后实测

- 全量 pytest **1688 passed**（1685 + 3；清 `__pycache__` 后复跑同值）。
- 3 组 A/B 探针（每次改完还原 → 清 `__pycache__` → 核对 `md5 -q` = `88996206bb345ea259a2f53ef2750dcd`
  与 `git diff --stat`）：**A** 还原 HEAD 行为（复位挪到 `screen`-if 之外）→ **3 failed**；
  **B** 套用报告建议版（否定等待也复位）→ **1 failed**；**C** 撤掉 P2-1 的排除（否定等待
  算到达）→ **2 failed**（P2-1 的修复仍被钉住）。
- **真语料 20 用例 → 覆盖集 8 条，修订前后逐字相同**（`diff` 为空）：本轮影响的形态
  （「非屏 wait 之后还有屏到达」）在真语料命中 **0** 处，与评审的扫描结论一致。

### 教训（已并入 MEMORY §5）

- **「复位 / 清理」语句的作用域必须等于它所描述的那件事的作用域**：`pending = None`
  描述的是「一个动作被到达点消费了」，却被写成了「一次 wait 发生了」——**作用域写宽一格，
  语义就反了**。与 `getattr` 默认值、`if not v` 放过空串同族：都是**判据的作用域比声称宽**。
- **评审给的「一行修法」也要按同一条判据扫一遍同形**：报告把「元素 wait」这处修对了，
  但同一行里的否定等待仍是同形（报告建议「保持复位」）。核销时**按纪律扫全包，不按建议的
  最小 diff 抄**，并**用探针证明偏离是承重的**（探针 B：照抄建议版 → 1 red）。

---

## Task 3.4 完成记录（P3-12 生成管线 + test_candidates，2026-10-10）

**M3 第四个任务**，也是 M3 里**唯一「写下去」**的一环。设计 §6.1 的主流程
（Coverage Gap → LLM → Schema 校验 → 语义校验 → 指纹去重 → Dry Run →
`test_candidates`）在本任务拼齐；矩阵 **#7–#10** 由此可验。

没有它，`coverage_gap`（3.2）与 `test_fingerprint`（3.3）都是**零生产消费者**
——`generator/__init__.py` 自己写着「只做算出来，不做写下去」。本任务补上前半段
的「写下去」，落库仍走 `agents/storage.py`（单写者不破）。

### 交付

**Create**

| 文件 | 行数 | 内容 |
|---|---|---|
| `agents/migrations/002_test_candidates.sql` | 78 | `test_candidates`（设计 §11 + 加固）+ `test_candidate_rejections`（决定 2） |
| `candidates/test_case.py` | 175 | `TestCandidate` / `CandidateRejection` 行模型 + store 互转 + `dump_testcase_yaml` |
| `generator/validator.py` | 206 | `validate_schema`（转调 `lint`，F5）/ `validate_semantic`（纯函数，F13） |
| `generator/dry_run.py` | 116 | `run_candidate(...)` + `DryRunRefused`（闸门在函数内，矩阵 #9） |
| `generator/generator.py` | 343 | `generate(...)` 编排 + `GenerationResult` |
| `tests/unit/test_candidate_store.py` | 233 | **18** 例 |
| `tests/unit/test_dry_run.py` | 149 | **21** 例 |
| `tests/unit/test_generator_pipeline.py` | 538 | **28** 例 |
| `tests/fault_injection/test_p3_matrix_m3.py` | 270 | **13** 例（矩阵 #7–#10） |

**Modify**

| 文件 | 改动 |
|---|---|
| `agents/storage.py` | +147：`save_candidate`（upsert）/ `get_candidate` / `list_candidates` / `save_rejection`（append-only）/ `get_rejection` / `list_rejections` + 两个 row 转换 |
| `cli/pipeline.py` | +138：`DeviceStack` + `wire_device_stack(...)`（从 `cmd_run` 抽出，决定 3） |
| `cli/main.py` | −117/+…：`cmd_run` 装配段改调 helper（行为等价） |
| `llm/prompt.py` | +93：`build_generation_prompt` + `GENERATOR_ACTIONS` / `GENERATOR_CONDITIONS`（**从 schema 派生**） |
| `llm/parser.py` | +164：`parse_candidate_steps` + `ParsedCandidateSteps` |
| `environment/capabilities.py` | `_coerce` → 公开 `coerce_device_type`（同一实现改名，3 处调用点同步） |
| `agents/tools.py` | `NOT_WIRED` 三项指向 M3 → **M4/M6**（决定 1） |
| `tests/unit/test_llm_contract.py` | +155（47 → 69）：Generator 契约段（prompt 硬规则 / 词表同源 / 解析 happy + 非法 + 全有或全无 + `ignored_fields`） |
| `tests/unit/test_knowledge_retrieval.py` | +32（18 → 19）：`generator/**` 的 AST import 守卫（F3 的 M3 格） |
| `tests/unit/test_repo_hygiene.py` | `_SOURCE_DIRS` += `agents` / `candidates` / `generator` / `knowledge` / `planner`；`_MIN_WRITE_STATEMENTS` 18 → 21 |
| `tests/unit/test_agent_store.py` | 首批表断言改为「5 张表存在、`discovery_events`/`bug_candidates` 不存在」 |

### 决策记录

1. **Dry Run 不经 `AgentToolkit`**（用户拍板）：M3 的 LLM 只产出**步骤序列**、
   不做工具调用循环——`execute` 是**注入的 `Callable[[TestCase], TestcaseResult]`**，
   工具层在生成期是死对象。因此 `run_testcase` / `run_candidate_test` /
   `create_test_candidate` 三项**保持 `NOT_WIRED`**，但指向由「M3」改为
   **「M4/M6（驱动设备的工具循环）」**：留着「等 M3」是**过时的承诺**，而
   `tests/unit/test_agent_tools.py::test_unwired_tool_fails_loud_with_the_task`
   会把错误消息里的任务号读给调用方——承诺过期就是错误信息过期。
   不接线不产生 F7 守卫缺口：`test_p3_matrix_m1.py:165` 的守卫只对**已接线**的
   `cmd_*` 生效（`AUTONOMOUS_COMMANDS` 里的 `"generate"` 在 M1 已登记，接线在 3.5）。
2. **迁移 002 建两张表**（用户拍板）：设计 §11 的 `test_candidates` 只表达
   「**入库的候选**」，装不下矩阵 #7/#8 的「**被拒/SKIP 的也要可查**」。给
   `test_candidates` 加 `review_status='SKIPPED'` 会把**生成器的判据**混进 F6 的
   **人工**审核状态机（`PENDING → ACCEPT/REJECT`），3.5 的审核列表就得额外过滤。
   > ⚠️ **命名偏离**：AskUserQuestion 的选项标签写作 `test_candidate_skips`，
   > 落地叫 `test_candidate_rejections`——它同时装 SKIP（`DUPLICATE`）与 REJECT
   > （`SCHEMA_INVALID` / `SEMANTIC_REJECTED`），叫 skips 是名实不符。列集不变。
3. **抽 `wire_device_stack` 且 `cmd_run` 改调它**（用户拍板）：设备装配只许有
   **一处实现**，否则 Dry Run（3.5 的 `mta generate`）与 `mta run` 必然漂移。
   本次是**逐段搬运**（fake-driver 桩类、真机四件套、`PipelineDeps` /
   `_step_runner` / `_lifecycle` / `_run_id` / `postcondition_checker` 回填），
   顺序与语义零改动；P1/P2 全量回归是这一步的硬闸门（见「实测」）。
4. **词表从 schema 派生，不手抄**：`GENERATOR_ACTIONS` =
   `get_args(ActionStep.action)`，`GENERATOR_CONDITIONS` =
   `get_args(WaitSpec.condition) ∪ get_args(AssertionSpec.condition)`。
   初版**手抄**，漏了 `active`（它只在 `WaitSpec` 里，不在 `AssertionSpec`）
   ——正是「手抄必然漂移」的实证。手抄漂移的方向是「模型按 prompt 编的动作
   过不了 schema」或「schema 收紧后 prompt 还在放行」，两种都要到很远的地方才炸。
   `tests/unit/test_llm_contract.py::test_generation_prompt_action_and_condition_vocab_match_schema`
   钉住同源关系。
5. **契约层只挡「模型编的词」，精确约束归 P1 schema**（F5）：`wait_for
   screen:X condition=visible` 被契约层**放行**（`visible` 是合法的
   `WaitSpec.condition`，对**元素**成立），由 `validate_schema` 的 lint 拒掉。
   在契约层重写一遍「屏/元素条件矩阵」就是第二份 schema 校验器。有一条正面
   测试（`test_contract_layer_defers_precise_rules_to_p1_schema`）把这个**刻意
   的分层**登记下来，免得日后有人当 bug 修。
6. **`result.broken` 在收尾处重读 `budget.broken`**：熔断可能发生在**最后一次**
   迭代的 `record_failure()` 里——那时循环已到末尾，`if not try_acquire()` 那一支
   再也轮不到，`result.broken` 会停在 `False` 而预算其实已熔断。「熔断了但报告说
   没熔断」正是本仓最防的那类「看起来正常」。

### 📌 口径登记 1：语义校验只拒「与**已知观测**矛盾」

**未知 Screen / 新元素一律标记 `unknown` 放行，交给 Dry Run 验证。**

`validate_semantic(tc, *, graph)` 的判据（三条，按序）：

| 情形 | 判定 | 理由 |
|---|---|---|
| 候选**声明不出转移**（到达点 < 2） | `SEMANTIC_UNKNOWN` | 「声明的转移全部有观测支持」在**空集上恒真**——判 `consistent` 是空话，会让审计以为「语义校验过了」 |
| 图对同一 `(from, trigger)` **零观测** | `SEMANTIC_UNKNOWN` | 新路径/新元素，正是我们要生成的用例；判「矛盾」会误杀 |
| 图**有观测**、且**没有一条**落在声明的 `to_screen` | `SEMANTIC_CONTRADICTED` | 结构性矛盾 |

理由：① Source Graph 的转移**恒为空**（`graph/builder.py` 模块 docstring：
metadata 无导航声明），拿它当判据会恒返回「无矛盾」——形式合法、语义等于没有；
② Runtime Graph 是**过去运行的采样**，不是真值。故语义校验只挡结构性错误，
真伪由 Dry Run 定（设计 6.1 的次序本就是这个意思）。

**推论（一并登记）**：这条判据是「与**已知观测**矛盾」，不是「与真值矛盾」——
**误拒是可能的**，但落在留痕表里（`reason` + `detail.observed`），人工看得见，不静默。
`from/trigger/to` 的推导**复用 `generator/coverage.py::_arrivals`**（覆盖推导只有
一份定义）。

### 📌 口径登记 2：`duplicate_of` 是 Task 3.3 那笔代价的兑付

Task 3.3 拍板 `precondition` **不进指纹**，代价是真语料**已有 2 对同指纹**用例
（`reset_logout_001` vs `login_001`、`search_001` vs `search_filter_001`）。
`docs/handover.md` §9 把「SKIP 留痕要带 `duplicate_of`」列为 3.4 开工前置 #1。
本任务兑付：矩阵 #8 的三条测试分别钉住「撞**已有用例**写它的 id」、
「撞**未决候选**写 `cand_*` id」、「**同语义不同命名**仍 SKIP」。
`_existing_fingerprints` 用 `setdefault`（同指纹多例保留第一条）——「留哪条」
只影响 `duplicate_of` 写哪个 id，**不影响是否 SKIP**。

### 📌 口径登记 3：留痕表的三个 `reason` 是「候选被拒」，不含契约层非法

`test_candidate_rejections.reason ∈ {SCHEMA_INVALID, SEMANTIC_REJECTED, DUPLICATE}`
——三者都是「**有一个候选**被拒」。而 `parse_candidate_steps` 判非法时**压根没有
候选**（步骤都解析不出来），它走 `notes` + `budget.record_failure()`，**不写留痕表**。
可见性由矩阵 #10 的 `budget.broken` + notes 承担。把「没有候选」也塞进
`candidate_id` 可空的留痕表会让「被拒的候选数」与「拒因分布」对不上账。

### 实测

- 全量 `pytest tests`（macOS）：**1794 passed / 0 failed**（exit 0）。
  HEAD（`c766caf`）基线 **1691 passed**（在 `git worktree` 里独立复跑确认）
  → 本任务 **+103**：新增文件 18 + 21 + 28 + 13 = **80**，既有文件
  `test_llm_contract.py` +22（47 → 69）、`test_knowledge_retrieval.py` +1（18 → 19）。
- `test_repo_hygiene.py`（写语句全部落在 `with self._write_tx()` 区间内）+
  `test_knowledge_retrieval.py`（`generator/**` 的 AST import 守卫）+
  `test_agent_store.py` → **37 passed**。
- **A/B 探针 6 组，全部真红**（每次改完还原 → 清 `__pycache__` → 核对 `md5 -q`
  与 `git diff --stat` 为空）：
  | 探针 | 变异 | 结果 |
  |---|---|---|
  | A | 去重命中恒置 `hit = None`（矩阵 #8） | **6 failed**（`test_p3_matrix_m3.py` ×3 + `test_generator_pipeline.py` ×3） |
  | B | `SEMANTIC_CONTRADICTED` → `CONSISTENT` | **1 failed**（`test_contradiction_with_known_observation_is_rejected`） |
  | B2 | 「到达点 < 2 → `UNKNOWN`」早退 → `CONSISTENT` | **1 failed**（`test_single_arrival_candidate_is_unknown_not_consistent`） |
  | C1 | Dry Run 的 `env_kind` 闸门 → `if False` | **7 failed**（`test_dry_run.py` ×4 + `test_p3_matrix_m3.py` ×3） |
  | C2 | Dry Run 的 `device_type` 闸门 → `if False` | **4 failed**（`test_dry_run.py` ×3 + `test_p3_matrix_m3.py` ×1） |

  探针 B2 是**本任务自查发现的真 bug**（见「教训」）。

### 教训（已并入 MEMORY §5）

- **「全部 X 都满足」在空集上恒真——判据必须显式处理空集**：初版
  `validate_semantic` 对「只声明 1 个到达点」的候选返回 `consistent`（因为
  `declared` 是空集，循环体一次都没跑，末尾无条件 `return CONSISTENT`）。
  那是**最坏的一类空转**：形式上「语义校验通过了」，实际什么都没校验，而审计
  会把 `consistent` 读成「有证据支持」。修法是**把空集当一等情形显式返回
  `UNKNOWN` + 说明**，并配一条专属测试（`test_single_arrival_candidate_is_unknown_not_consistent`）
  与探针 B2。与 MEMORY §5 已有的「`getattr` 静默兜底」「`if not v` 放过空串」
  同族：**判据的覆盖面比声称宽**。
- **手抄的词表必然漂移，且漂移方向不可预测**：`GENERATOR_CONDITIONS` 初版手抄，
  漏掉只在 `WaitSpec` 里的 `active`。改为 `get_args(...)` 从 schema 派生后，
  「prompt 说的」与「schema 认的」在构造上不可能不一致。**凡是「同一份事实的
  第二处字面量」，都该问一句「能不能从第一处算出来」。**
- **抽出 helper 时，`NameError` 只会暴露你**忘了改的**引用**，不会暴露语义漂移**：
  `executed_steps=lifecycle.steps_recorded` 在搬运后炸了（好抓），而
  `pipeline.deps.repo` 那条是**静默错的**（`SessionPipeline` 在装配前没有
  `deps` 属性）——靠读代码发现，不是靠测试。搬运类的重构，回归绿只是**下限**。

---

## Task 3.4 评审修订记录（round 1，2026-10-10）

**评审报告**：`review/review_p3_task34_2026-10-10.md` —— 结论 **不通过**：
**1×P0 + 2×P1 + 4×P2 + 4×P3**。35 组变异探针 + 12 组只读观察，全部在 worktree
副本上做（评审结束主仓零改动）。

### 修订 1（P0-1）：`cli/pipeline.py` 真机分支引用未导入的 `os` → `mta run` 真机路径 100% 死

**这是本次最重的一条，也是「守卫空转」的教科书形态。** 抽出 `wire_device_stack`
时漏了 `import os`（旧实现 `cli/main.py` 有），而 `os.environ.get("APPIUM_URL", …)`
在**真机分支**里。为什么全量绿没抓到：`test_r16_2_no_fake_driver_exits_3_not_fail`
是当时**唯一**触及真机分支的用例，它把 `DeviceSession.connect` 换成必抛异常——
装配在 `os.environ` **之前**就失败，而断言文案 `"设备会话建立失败" in out or
"build identity" in out` 又**恰好被 `NameError` 的报错文案命中** → 测试绿、真机
路径死。

- 修：补 `import os`。
- **加固（比修 bug 更重要）**：补 3 条**直接调 `wire_device_stack(fake_driver=False)`**
  的单测（stub 设备侧，让装配**跑到底**），逐项断言 `pipeline.deps.repo is repo` /
  `device_session` / `_run_id` / `_lifecycle` / `_step_runner` /
  `runner.postcondition_checker is not None` / `close()` 真的 quit。
  探针实测：**去掉 `import os` → 2 failed**（此前该形态 0 覆盖）。
- 这条同时核销 **P3-3**（真机分支零覆盖：评审的三条变异 `repo=None` / 删
  `postcondition_checker` / 删 `recovery.guard` 回填全部 0 red）。补测后
  `repo=None` 与 `postcondition_checker` 两条**不再是零覆盖**。
  > ⚠️ **更正（round 2 实测证伪）**：本条原文写「`repo=None` 与
  > `recovery.guard` 两条不再是零覆盖」——**对 `recovery.guard` 是错的**。
  > 新补的三条测试都构造 `SessionPipeline(...)` 而**没设 `recovery`**（默认
  > `None`），`if pipeline.recovery is not None:` 那一支**从未被进入**；把该行
  > 改成 `if False:` 全量 **1809 全绿 / 0 red**。这正是 P0-1 的**同族缺陷**
  > （声称已覆盖而实际没有），在修订里又犯了一次——**round 2 已补真钉子**
  > （见下方「Task 3.4 评审修订记录（round 2）」的修订 12）。

> **教训（已并入 MEMORY §5）**：**「唯一覆盖某分支的测试」若在该分支第一行之前
> 就退出，它守的是空气。** 断言写 `A or B` 的文案匹配尤其危险——它把「另一种
> 失败」也算成通过。判「某分支有没有覆盖」不能看「有没有测试提到它」，要看
> **测试是否真的走到了那一行**（本条的探针法：让装配跑到底再断言回填结果）。

### 修订 2（P1-1）：语义校验的短路 fail-open——已知矛盾被「零观测」转移掩蔽

初版按 `sorted(declared)` 逐条检查，**遇零观测即 `return unknown`**。于是结论
取决于**字典序**：一条候选只要**任意**一条零观测转移排在已知矛盾转移之前，矛盾
就整个不被检查 → 落进 `unknown`（放行）→ 推给 Dry Run 烧设备。评审实测：
两条候选声明的 `MidView->ProfileView@tap:MidView.x` 完全相同（图里实测落点是
`DetailView`，**已知矛盾**），只把第一条转移的屏名从 `HomeView` 换成 `ZHomeView`
（两者都零观测），判定就从**拒绝翻成放行**。

**生产影响（评审量化）**：真语料 20 例里 13 例有声明转移，其 `sorted-first`
**全是 `HomeView` 打头**——真实形态天然命中这条短路。

- 修：改成**先找矛盾、再谈未知**的全量扫描。优先级
  `contradicted（任一）> unknown（任一，且无矛盾）> consistent（全部有观测）`。
  矛盾时**列出全部**矛盾转移（不只第一条），人工一次看全。
- 补 2 条测试：`test_contradiction_is_not_masked_by_an_earlier_unknown_transition`
  （两种字典序都拦得住）+ `test_unknown_is_reported_only_when_nothing_contradicts`
  （反向：修订不能把放行也堵死）。探针：**还原短路 → 1 failed**。
- **口径登记的缺口一并补上**：原 docstring 的「⚠️ 推论」只登记了**误拒**方向，
  漏放方向没登记。现改为明写「**误拒方向已登记，漏放方向由 P1-1 修订关掉**」。

> **教训（已并入 MEMORY §5）**：**「逐条检查 + 遇情况即 return」的循环，把
> 「检查了哪些」交给了遍历顺序。** 只要判据是「任一/全部」的合取或析取，就必须
> **扫全量再定结论**——`for … if cond: return A` 的写法让**排序**成了判据的一部分，
> 而排序通常是无意的（`sorted()` 只为让输出确定）。与「空集上恒真」同族：
> 都是**判据的覆盖面被实现细节偷走**。

### 修订 3（P1-2）：`ignored_fields` 零消费 → H4「越权痕迹必须可见」空转

解析层正确算出 `ignored_fields`（模型声明的 `risk` / `idempotency` / `id` 等），
生成器**从不读它**——`risk: CRITICAL` 就这么静默消失。忽略它是对的（风险由
metadata + Guard 判，H4），但**人工审核时看不到「这条候选的 LLM 试图声明过
CRITICAL」**，而那正是最该看一眼的信号。对照 P2 侧：`planner` 把它落成审计行。

- 修：`generate` 在解析后把 `ignored_fields` 写进 `result.notes`（每条一行）。
- 补测试 `test_model_declared_risk_is_ignored_but_visible`：断言 YAML 里**没有**
  `CRITICAL`（确实被剥掉）**且** notes 里**有**（可见）。两个方向都钉。

### 修订 4（P2-1）：`save_candidate` 的「必有指纹」闸门不存在

迁移 002 的 docstring 声称「入库的候选必有指纹——那条由 `save_candidate` 的
Python 侧闸门保证」，评审实测**当时并不存在**（`fingerprint=None` 入库成功）。
后果不是「少个字段」：`generator.py` 建去重基准时写 `if cand.get("fingerprint")`，
一个 `NULL` 指纹的候选被**静默跳过** → **永久不参与去重** → 同一用例可反复生成
入库，而每一步都「看起来正常」。

- 修：`save_candidate` 加 `if not fingerprint: raise ValueError(...)`。
- 补测试 `test_save_candidate_refuses_a_missing_fingerprint`（`None` 与 `""` 都拒）。
  探针：**去掉闸门 → 1 failed**。

### 修订 5（P2-2）：`DryRunResult.detail` 不落库 → `FAIL` 旁边没有「为什么」

`dry_run.py` 的 docstring 声称 `detail` 进「`dry_run_status` 旁边的审计面」，
实际零消费——候选行只有 `dry_run_status='FAIL'`，`failure_type` 消失。人工审核
看到 FAIL 却不知道**为什么**失败，等于把一次设备执行的结论丢掉一半。

- 修：迁移 002 加列 `dry_run_detail_json TEXT NOT NULL DEFAULT '{}'`（设计 §11
  本无此列——**这是本次唯一的新增列**，理由写进迁移头部）；`save_candidate` /
  `_candidate_from_row` / `TestCandidate` 两侧同步；`generate` 传
  `dry_run_detail=dry.detail`。
- 补测试 `test_dry_run_detail_is_persisted_not_just_the_status`（含
  `failure_type` / `attempt` / `cleanup_status` 三项）+ 行模型往返。探针：
  **不传 detail → 1 failed**。

### 修订 6（P2-3）：装配失败泄漏 Appium 会话

`ds.connect()` **之后**的装配（`AppSession` / `EnvironmentManager` / `Executor` /
`StepRunner`）任一抛错都会绕过 `DeviceStack.close()`（后者只在成功返回后由调用方
的 `finally` 调到）。旧实现同形（非本次回归），但抽成「唯一实现」后它成了 3.5 的
`mta generate` 也会走的入口。

- 修：把 `connect()` 之后的四件套包进 `try/except`，失败时 `driver.quit()` 再重抛
  （**不吞原始异常**）。
- 补测试 `test_wire_device_stack_closes_the_session_when_assembly_fails`：
  stub `EnvironmentManager` 抛错 → 断言 `quit()` 恰好被调 1 次。

### 修订 7（P2-4）：嵌套 spec 的未知键不过滤 → H4 在嵌套层不成立

`_clean_step` 把模型的**原 dict** 直接 return，于是 `wait_for` / `assertion` /
`postcondition` 里的 `risk` **既不被忽略也不被记进 `ignored_fields`**。拦得住是
**巧合**（P1 schema 的 `extra="forbid"` 兜住），而「靠下游兜住」正是本仓反复吃亏
的形态。

- 修：抽 `_strip_unknown_spec_keys(spec, model)`，合法键从 `model.model_fields`
  **派生**（不手抄）；三个嵌套位点全部过滤，被剥的键以 `steps[i].<key>.<field>`
  路径进 `ignored_fields`（与顶层同款）。
- 补 2 条测试（观测步 + `postcondition` 分支）。探针：**关掉嵌套过滤 → 1 failed**。

### 修订 8（P3-1 / P3-2 / P3-4）：零消费 API 与撒谎的审计数字

- **P3-1**：删 3 处未用 import（`cli/main.py` 的 `PipelineDeps` / `Lifecycle` /
  `StepRunner`——随装配搬运而失效；`generator.py` 的 `DryRunResult`）；
  `validator.py` 的 `max_severity` re-export 注释改为如实（当前零消费，生成器用
  `schema_errors`）。
- **P3-2**：`utcnow()` 的 docstring 如实写「当前零生产调用方，给 3.5 落盘用」；
  `to_store_dict` 补**全键**断言（此前只看一个键）。
- **P3-4**：`generate` 入口先 `existing_cases = list(existing_cases or ())`——
  它要遍历两次（建索引 + 报条数），传一次性可迭代对象时第二次恒空 → note 写
  「正式用例 0 条」而实际 N 条。
  ⚠️ **这条的测试第一版是空转的**：我复用了 `_run` helper，而它自己写了
  `list(cases)`，把「一次性可迭代」这个被测形态**在 `generate` 看到之前就洗掉了**
  ——探针 0 red 当场抓出。改成直接调 `generate` 并传生成器后探针 1 red。
- 另补 `test_last_gap_contract_failure_still_reports_broken`：熔断**恰好发生在
  最后一条 gap**（`try_acquire` 那一支轮不到）——那是「收尾重读 `budget.broken`」
  唯一的用武之地，此前无独立测试。

### 未修订（显性登记，非本次范围）

- **AE（评审自创观察）**：reviewer ACCEPT 后重跑生成器 → `review_status` 保持
  `ACCEPT` 但 `testcase_yaml` **被新内容覆盖**（upsert 不区分该行是否已进人工
  决策）。按 3.5 的落盘设计（ACCEPT → `suites/<suite>/<id>.yaml`），落盘文件与
  库内容会**分叉**。**归 Task 3.5 核销时必须处理**（已进 handover §9 的下一步前置）。
- **AF（prompt 模板诱导注释）**：`build_generation_prompt` 用手写 JSON 片段做
  示例，照抄含注释即非法 JSON。非代码 bug，未改（prompt 已在 3.4 冻结，改动会
  影响已有测试的文案断言）——登记为 3.5 的 prompt 迭代输入。
- **S（`schema_errors` 缺 WARNING 不拒的正面钉子）**：判据本身正确（枚举身份
  比较），真语料元素 id 与 `NON_IDEMPOTENT_KEYWORDS` 零交集，构造合成仓库会绕过
  `Repository.from_dirs` 的真实路径。登记为 P3 缺钉子，非行为缺陷。
- 评审「未验证」第 1 条（父提交 1691 基线未独立复跑）：**主会话已用
  `git worktree` 独立复跑确认 1691 passed**，本条核销。

### 修订后实测

- 全量 `pytest tests` → **1807 passed / 0 failed**（exit 0）。修订前 1794 →
  **+13**（真机装配路径 3 + 语义 2 + 越权可见 3 + 存储 2 + 一次性可迭代 1 +
  最后一条熔断 1 + 全键断言 1）。
- **A/B 探针 9 组，全部真红**（每次改完还原 → 清 `__pycache__` → 核对 `md5 -q`
  与 `git diff --stat`）：

  | 探针 | 变异 | 结果 |
  |---|---|---|
  | P0-1 | 去掉 `import os` | **2 failed**（真机装配两条） |
  | P1-1 | 还原「遇零观测即 return」短路 | **1 failed** |
  | P2-1 | `save_candidate` 指纹闸门 → `if False` | **1 failed** |
  | P2-2 | `generate` 不传 `dry_run_detail` | **1 failed** |
  | P2-4 | 关掉嵌套 spec 过滤 | **1 failed** |
  | P3-4 | 不物化 `existing_cases` | **1 failed** |

- 探针后 `grep -rn MUTANT` 零命中；`git diff` 只剩本次修订的 11 个文件。

### 教训（已并入 MEMORY §5）

1. **「唯一覆盖某分支的测试」可能守的是空气**：断言在该分支**第一行之前**退出，
   或断言写成 `A or B` 的文案匹配（把另一种失败也算通过），都会让「绿」与
   「分支活着」脱钩。判覆盖要看**测试是否真的走到了那一行**。
2. **逐条检查 + 遇情况即 return，把「检查了哪些」交给了遍历顺序**：判据是
   「任一/全部」时，必须**扫全量再定结论**，否则 `sorted()` 这种只为确定性输出
   的细节会悄悄变成判据的一部分。
3. **抽 helper 的重构，「行为等价」必须由新写的直调测试钉住，不能靠全量回归**：
   全量回归只覆盖**旧测试走过的路**；被抽出的分支若原本就零覆盖，回归绿什么也
   证明不了（P0-1 正是如此——真机分支此前零覆盖，抽取前后都绿）。
4. **测试复用的 helper 会把被测形态洗掉**：`_run` 里的 `list(cases)` 让
   「一次性可迭代」这条测试**永远碰不到**被测行为，而它**照样绿**。
   **写「守卫某实现选择」的测试前，先用 mutant 确认它真能区分两种实现**（与
   Task 3.3 的教训同一条，这次栽在「helper 顺手规范化了输入」上）。

---

## Task 3.4 评审修订记录（round 2 / final，2026-10-10）

**复核报告**：`review/review_p3_task34_final_2026-10-10.md` —— 结论 **有条件通过**：
11 条中 **10 条经独立探针真核销**，1 条（P3-3 的 `recovery.guard`）**并未真正核销**
且审计记录**过度声称**；另发现 2 条新缺陷（1×P2 + 2×P3）。**全部已修订。**

### 修订 9（P2-new，本轮最重要）：`recovery.guard` 回填仍是零覆盖 + 审计过度声称

round 1 修订补的三条真机装配测试都构造 `SessionPipeline(suites_root=...)` 而
**没设 `recovery`**（默认 `None`）→ `if pipeline.recovery is not None:` 那一支
**从未被进入**。复核者探针实测：把该行改成 `if False:` → **全量 1809 全绿 / 0 red**。
即 round 1 审计里「`repo=None` 与 `recovery.guard` 两条不再是零覆盖」这句**对
`recovery.guard` 是错的**——这是 P0-1 的**同族缺陷**（声称已覆盖而实际没有），
**在修订里又犯了一次**。

为什么不是可有可无的装饰：`RecoveryEngine._make_guard_check` 在 `self.guard is
None` 时**直接 return None**（`agent/recovery.py:391`）→ **10.1 的 Guard 复检在
真机上静默关闭**（LLM 候选的风险门控没了），而没有任何东西会报错。生产可达：
`cli/main.py` 无条件构造 `RecoveryEngine(...)`（不传 guard），随后
`wire_device_stack` 才回填。

- 修：补 `test_wire_device_stack_shares_the_guard_with_recovery`——**真的设一个
  `RecoveryEngine()`**（并先断言 `engine.guard is None` 作为前提），装配后断言
  `pipeline.recovery.guard is stack.runner.guard`（**同一实例**，10.1 的要求）。
- 探针：**`if False:` → 2 failed**（该测试 + 迁移幂等测试的版本串），此前 0 red。

> **教训（已并入 MEMORY §5）**：**「补了测试」不等于「覆盖了分支」——测试的
> 前置条件本身就是覆盖的一部分。** 新测试若没构造出进入该分支所需的**状态**
> （这里是 `pipeline.recovery`），它测的是另一条路。**唯一可靠的判定是探针**：
> 把分支改成恒假，看它红不红。这与 P0-1 的教训是**同一条**——本轮在修订里
> 复现了它，说明「写测试」这个动作本身也需要被探针验证。

### 修订 10（P3-new）：迁移 002 被原地编辑 → 升级路径炸

round 1 修订把 `dry_run_detail_json` 直接加进**已落盘**的 002（`feff5ec`），而非
新开脚本。复核者实测：用旧版 002 建库 → 当前 store 打开 → 读 `IndexError` /
写 `OperationalError`（库不可用）。理由：迁移执行器按「已应用序号」跳过脚本，
原地加列对**已按旧版建过库**的人**完全不生效**（002 已记「跑过」）。

**本仓先例是反的**：`graph/migrations/002_graph_diff_sources.sql` 头部明写
「这是**需求后到**，所以走 graph 自己的迁移链加列，**不改 001**」；执行器
docstring 也写着「只向后追加」。

- 修：抽出 `agents/migrations/003_dry_run_detail.sql`（`ALTER TABLE ... ADD COLUMN`），
  002 还原为 `feff5ec` 的形态（只更正头部注释里那条曾经过时的说法）。
- 补 `test_002_alone_leaves_the_db_readable`：手工按「只跑 002」建库（**DDL 逐字
  取自 `feff5ec`**，不是读当前脚本——否则夹具会跟着实现漂移），断言当前 store
  能打开并读写。
- 探针：**复现 round 1 的错误做法（列塞回 002 + 删 003）→ 2 failed**
  （`table test_candidates has no column named dry_run_detail_json`）。
- `test_002_migration_is_idempotent` 改名 `test_migrations_are_idempotent`，
  版本串断言 `002_test_candidates` → `003_dry_run_detail`（**从链尾派生**，
  加脚本忘改常量也不会撒谎——这正是它该有的行为）。
- **定级说明**：round 2 定 P3（`agents/migrations/002` 从未进 `main`，
  `P3-1010` 未 push、无 remote；主仓也无真实 `out/agent.db`，已实测），故**没有
  已发布的库会踩到**。但**做法本身**违反本仓明文先例，故照先例改。

### 修订 11（P3-new）：`wire_device_stack` docstring 宽于实现

原文写「**顺序与语义与抽出前逐字相同**」，而修订 6（P2-3）**有意**新增了失败
路径的 `driver.quit()` 收尾——语义已变。→ 改为「**成功路径**与抽出前逐字相同」
并显式登记那处有意的变化。

### 修订 12（复核者「未验证 1」）：`test_r16_2` 的 `A or B` 断言仍是误绿源

复核者实测：往真机分支注入 `NameError`，`test_r16_2` **仍然绿**（该断言
`"设备会话建立失败" in out or "build identity" in out` 的第一项被任何装配异常
满足）。复核者判定「加固足够（新测试已变红）但该断言宜改精确」。

- 修：断言改为 `out.startswith("PREFLIGHT ERROR:")` + 四个**各自点名具体原因**
  的析取项（build identity 读取失败 / BUILD_METADATA_MISMATCH / 缺 bundle-id /
  本测试注入的 `unit-test: 无设备会话`）。
- **验证**：直接对「装配期 `NameError`」的文案做判据对照 —— 旧断言 `True`
  （误绿），新断言 `False`（堵住）。
- ⚠️ **诚实登记一处未验证**：本机无 `xcrun simctl`（未配 `DEVELOPER_DIR`），
  该测试实际在 **Build Identity 门**就退出，**走不到**装配期 `NameError`；故
  「注入 `NameError` → 红」这条探针在本机**复现不了**（0 red）。新断言的判别力
  用上面的**文案对照**证明，未在真实执行路径上端到端验。

### 数字对账（复核者独立复跑，全部一致）

| 声称 | 实测 | |
|---|---|---|
| round 1 后 1807 passed | 1807 | ✅ |
| 修订前 1794 / 父基线 1691 | 1794 / 1691（worktree `--collect-only`） | ✅ |
| 增量 +13 | `test_cli_run` +3 / `test_generator_pipeline` +8 / `test_candidate_store` +2 | ✅ |
| 探针 P0-1 2 failed | 2 | ✅ |
| 探针 P2-4 1 failed | **2** | ⚠️ round 1 审计少记 1（方向无害） |
| 「`recovery.guard` 不再是零覆盖」 | **仍 0 red** | ❌ → 本轮的修订 9 |

### 修订后实测

- 全量 `pytest tests` → **1809 passed / 0 failed**（exit 0）。round 1 后 1807
  → **+2**（`recovery.guard` 1 + 迁移升级路径 1；`test_002_migration_is_idempotent`
  改名不增数）。
- **本轮 A/B 探针 3 组，全部真红**（还原 → 清 `__pycache__` → 核对 `md5`）：

  | 探针 | 变异 | 结果 |
  |---|---|---|
  | 修订 9 | `if pipeline.recovery is not None:` → `if False:` | **2 failed**（此前 0 red） |
  | 修订 10 | 复现 round 1 做法（列塞回 002 + 删 003） | **2 failed** |
  | 修订 10' | 列塞回 002 但**保留** 003（半修） | **1 failed**（`duplicate column name`） |

### 教训（已并入 MEMORY §5）

- **「补了测试」不等于「覆盖了分支」——测试的前置状态是覆盖的一部分**：新测试
  没构造出进入该分支所需的状态（`pipeline.recovery`），测的就是另一条路。本轮
  在**修订自己**身上复现了 P0-1 的同族缺陷：**判覆盖的唯一可靠手段是探针**
  （把分支改成恒假，看红不红），不是「我写了测试」。
- **冻结历史形态的夹具不能用「读当前脚本」实现**：`test_002_alone_leaves_the_db_readable`
  若读当前 002 建库，将来有人再把列塞回 002，夹具会跟着「变对」→ 测不出升级
  路径炸掉。**夹具的 DDL 必须逐字取自历史提交**（`git show <sha>:path`）。
- **迁移脚本一旦落盘就只许向后追加**：执行器按「已应用序号」跳过，原地改对
  已建过库的人**完全不生效**——而失效形态是「读 IndexError / 写
  OperationalError」，即库不可用。本仓先例已明写（`graph/migrations/002` 头部）。

---

## Task 3.5 完成记录（P3-13 统一审核 F6 首个消费者 + `mta generate`，2026-10-10）

**M3 收口任务**。3.4 把候选「写下去」（`test_candidates` PENDING），本任务补
「**审核 + 落盘**」的后半段并给整条管线一个 CLI 出口：

```
mta generate --gap-from <plan_id> → generate() → 候选（PENDING）
mta candidate review accept <id>  → suites/<suite>/<id>.yaml + git commit → 入 P1 流程
mta candidate review reject <id> --note … → 留痕（迁移 004 的 review_note）
```

F6（统一审核）**首个消费者**在此落地：M4 的 Discovery Event 与 M5 的 Bug
Candidate 复用同一入口（`kind` 判别，本任务只接 `"test"`）。矩阵 **#7–#10**
至此全部可验。

### 交付

**Create**

| 文件 | 行数 | 内容 |
|---|---|---|
| `agents/migrations/004_review_note.sql` | 25 | `ALTER TABLE test_candidates ADD COLUMN review_note TEXT;`（决定 2） |
| `candidates/review.py` | 203 | `CandidateReviewError` + `decide_candidate(...)` + `export_candidate_patch(...)` |
| `phase0/verify_p3_m3.py` | 437 | M3 Gate（G1–G8，`out/p3_m3_gate/summary.json`，决定 1） |
| `tests/unit/test_candidate_review.py` | 335 | **16** 例（状态机 + 落盘 + 前置 + 回滚 + H15） |
| `tests/unit/test_cli_generate.py` | 417 | **15** 例（`generate` / `candidate review` 的 CLI 面） |

**Modify**

| 文件 | 改动 |
|---|---|
| `agents/storage.py` | +32：`decide_candidate(...)`（`review_status` 的**唯一写路径**）+ `save_candidate` 的已决不覆写闸门（AE 第 1 层）+ `_candidate_from_row` 读回 `review_note` |
| `generator/generator.py` | +24：AE 第 2 层（已决 gap 预过滤，在 `try_acquire` **之前**）+ 去重基准 notes |
| `cli/main.py` | +348：顶层 `generate` 子命令 + `cmd_generate`；`candidate → review → list/accept/reject` + `cmd_candidate` |
| `candidates/test_case.py` | +17：`review_note` 字段（行模型两侧）+ `utcnow()` docstring **如实改写** |
| `llm/prompt.py` | +2：AF（示例里的注解别抄进 JSON） |
| `candidates/__init__.py` | docstring 刷新（fingerprint / test_case / review 三块） |
| `tests/unit/test_candidate_store.py` | +51：版本串 → `"004_review_note"`；`decide_candidate` 盖章 + AE 第 1 层 + 全键集 += `review_note` |
| `tests/unit/test_generator_pipeline.py` | +53：AE 第 2 层的 ACCEPT / REJECT 两例 |
| `tests/unit/test_repo_hygiene.py` | `_MIN_WRITE_STATEMENTS` 21 → 22（实际 26） |

### 决策记录

1. **Gate 脚本本任务写**（用户拍板）：照 M2 先例——`checkpoint-p3-m3` 打 tag 前
   `phase0/verify_p3_m3.py` 必须 exit 0。gate 自建**自足工程**（自己的 `.git` +
   generated + suites + trace），建仓后断言 `rev-parse --show-toplevel == proj`，
   防 M2/M4 的「`rev-parse` 穿透到主仓库」事故复发。
2. **REJECT 的 note 走迁移 004 加列**（用户拍板）：`review_note TEXT` 可空
   （NULL = 未决/无理由），**只向后追加** 004，不原地改 002/003（round 2 修订 10
   的先例）。与 P2 `recovery_reviews` 的 note 字段对齐（F6 的字段命名一致）。
   **不进 `test_candidate_rejections`**：那是**生成器**的拒绝留痕（两条时间线、
   两种责任），而 `review_note` 是**人工**的拒绝理由。
3. **翻案通道不做**（用户拍板）：被 SKIP（DUPLICATE）的候选**无库行可 ACCEPT**，
   要翻案得先给生成器「允许重开」的语义——那是新能力不是补一个 flag。显性登记
   进 handover §9，归 **M4+**。
4. **ACCEPT 的顺序照 promoter，不照 `decide_review`**：`decide_review` 是
   「校验 → 改状态 → 副作用（建 Experience，**可幂等重放**）」；本处副作用是
   **git commit，不可幂等重放**。若先改状态后落盘，落盘失败时候选已是 ACCEPT
   且「不可二次决策」→ **永远卡死**。故 `approve_proposal:215-298` 的顺序：
   **全部校验（零副作用）→ 写文件 + git commit（失败回滚）→ 改状态**，失败时
   候选保持 PENDING，修复 git 后重试即续传。
5. **id 冲突 fail-loud，不学 promoter 的「并入」**：promoter 的同 id 是「并入
   strategies 链尾」（那是它的领域主流程），候选落盘是**新文件**，同 id = 真冲突
   （会遮蔽既有用例）。
6. **sandbox 硬前置在 `cmd_generate` 顶部**（矩阵 #9）：`run_candidate` 的闸门在
   生成循环**内部**抛 `DryRunRefused`，而 `generate` 不接它——不前置拦，staging
   下会先入库一部分候选再炸（**部分写入**正是本仓最防的形态）。

### 📌 口径登记 1：`cmd_generate` 用**两个库、两个身份**

| 库 | 谁写 | 用途 |
|---|---|---|
| `--db`（`trace.db`） | `TraceStore` | **给 `Lifecycle` 用**：`begin_testcase` 会调 `start_testcase`（`SQLiteAgentStore` 没这个方法） |
| `--agent-db`（`agent.db`） | `SQLiteAgentStore` | 候选入库 / plan 回查 |

二者**不是**同一个 store 的两种用法，是**两个 store**。`pipeline.store` 必须指向
`TraceStore`（`wire_device_stack` 用它建 Lifecycle），候选仍落 `agent_store`。
初次接线时 `pipeline.store = agent_store` → `AttributeError: 'SQLiteAgentStore'
object has no attribute 'start_testcase'`——**装配期炸，不是静默错**（好抓）。
run 行由 gate/CLI 侧 `start_run` / `end_run` 包住。

**推论（gate 里实测）**：`mta run` 与预置 trace **不可共用同一个 trace 库**——
run 不带 `--bundle-id` 时 `runs.app_bundle_id` 是空串，与播种的 `com.demo`
同库会凑出两个 `(app_id, build)` 范围 → `read_trace_steps` 多范围 fail-loud
（**这是正确的既有行为**：图必须限定单一范围）。gate 因此给 `mta run` 另开一个
`run_trace.db`，**不**为图源引入污染。

### 📌 口径登记 2：`graph_query` 的第一个参数是 **app_id（= `runs.app_bundle_id`）**

不是 app_build。`P2KnowledgeSources.graph_query(app_id)` 拿它与 trace 的
`app_bundle_id` 比对，不一致即 fail-loud（"trace 的 app_id 是 X，与查询的 Y 不一致"）。
`cmd_generate` 因此用 `app_id = args.bundle_id or ""`（真机路径 `--bundle-id`
必填，fake-driver 路径也要求 trace 的 app_id 与之一致）。同一 `app_id` 同时喂给
`graph_query` 与 `generate`（候选的 provenance）。

### 📌 口径登记 3：gap **一律现场重算**，`--gap-from` 只作 provenance

`planner/planner.py:102` 明文：`generated_gap` 任务**目前无生产者**（`mta plan`
只产 `existing`）。故 `cmd_generate` 不读 plan 的 tasks，直接
`coverage_gap(knowledge.graph_query(app_id), cases)`；`--gap-from <plan_id>` 的
作用是**必填的追溯锚点**（不存在的 plan_id → exit 3）。这是「参数存在」与
「功能存在」分开登记的同一手法（R18-4 语义）。

### 📌 口径登记 4：落盘目录必须在 git 根之内（`--suites-root` 与 `--repo-root` 同源）

`git add` 只吃**仓库内**的路径（promoter 的 E10 前提）。初版用绝对路径调
`git add` → 落盘目录在仓库外时提交失败；修成 `target.relative_to(repo_root)`
且**前置 fail-loud**（不先写文件再让 git 报错——那是「写了一半」的形态，正是
回滚逻辑要处理的那种）。`CandidateReviewError` 文案点名「`--suites-root` 与
`--repo-root` 必须同源」。

### 实测

- 全量 `pytest tests`（macOS）：**1844 passed / 0 failed**（exit 0）。
  Task 3.4 修订后基线 **1809** → 本任务 **+35**：新增 16 + 15 = **31**，既有文件
  `test_candidate_store.py` +3、`test_generator_pipeline.py` +2。
  > 预期变更（不算回归）：`test_migrations_are_idempotent` 的版本串随 004 变
  > `"004_review_note"`（该测试注释自陈「从链尾派生，加脚本跟着变」）；
  > `test_row_models_roundtrip` 全键集 += `review_note`；`_MIN_WRITE_STATEMENTS` 上调。
- **M3 Gate `phase0/verify_p3_m3.py` → exit 0**（`out/p3_m3_gate/summary.json`
  verdict=PASS），G1–G8 全绿：

  | 判据 | 结果 |
  |---|---|
  | G1 `generate --env-kind production` | exit 1 + 含 `F7` + **agent.db 未创建** |
  | G2 `--env-kind staging` | exit 3 + 点名 sandbox + **零候选入库** |
  | G3a `mta plan --json` | plan_id=plan_8f7f778380b2a746 |
  | G3b `mta generate` | exit 0 + `gaps=1（现场重算）` |
  | G3c 候选入库 | PENDING / dry=PASS / 有指纹 / `coverage_gap=['HomeView -> SearchView']` |
  | G4 重跑 | 候选仍 1 条 + DUPLICATE 留痕带 `duplicate_of` |
  | G5a accept | 文件 + commit + 库 ACCEPT + reviewer=gate |
  | G5b 入 P1 | `mta lint <file>` exit 0 + `mta run --case <id> --fake-driver` exit 0 |
  | G6 reject 无 note | exit 2 + 报错点名 `--note` |
  | G7 AE | yaml **逐字节不变** + 状态仍 ACCEPT + notes 有「已人工决策」 |
  | G8 全量 pytest | 1844 passed |

- **A/B 探针 5 组，全部真红**（每次改完还原 → 清 `__pycache__` → 核对 `md5 -q`
  一致 + `git diff --stat` 为空）：

  | 探针 | 变异 | 结果 |
  |---|---|---|
  | P1 | AE 第 1 层：`if existing … != "PENDING":` → `if False:` | **1 failed**（`test_save_candidate_refuses_overwriting_a_decided_candidate`） |
  | P2 | AE 第 2 层：`if skip_id in decided:` → `if False:` | **3 failed**（`test_generate_skips_a_decided_candidate_and_keeps_its_yaml` / `…_rejected_…` / CLI 的 `test_decided_candidate_is_not_overwritten_by_a_rerun`） |
  | P3 | 落盘失败不回滚（`unlink` → `pass`） | **1 failed**（`test_git_failure_rolls_back_the_file_and_keeps_pending`） |
  | P4 | id 冲突校验 → `if False:` | **1 failed**（`test_accept_with_conflicting_id_is_refused`） |
  | P5 | `cmd_generate` 的 sandbox 前置 → `if False:` | **1 failed**（`test_non_sandbox_is_refused_before_anything_is_written`） |

  P1/P2 是**两层各自独立可测**的实证（删任一层，另一层的测试都还红）——
  不是只测「总体没覆写」。

### 教训（已并入 MEMORY §5）

- **「照谁的范式」要分开问**：F6 说「与 P2 审核范式一致」，但 P2 的**两处先例
  照的是不同的部分**——`decide_review` 照**状态机与报错文案**（可记忆性），
  `approve_proposal` 照**落盘顺序**（可重放性）。判断依据是「副作用能不能幂等
  重放」：`decide_review` 建 Experience 可重放（所以先改状态），git commit 不可
  （所以最后改状态）。**照范式不等于照实现**。
- **同一命令里的两个 store 必须各自点名**：`cmd_generate` 的 `pipeline.store`
  （给 Lifecycle）与候选的 `agent_store`（给 `test_candidates`）不是一个对象的
  两种用法。装配期 `AttributeError` 是**运气好**——换成一个两者都有的方法名
  （比如都叫 `save`），错的就是「写进了错的库」，静默。
- **gate 工程的 `suites/` 必须在 git 根之内**：`git add` 只吃仓库内路径，这是
  promoter 的 E10 前提，也是本次 accept 落盘的硬约束。单测夹具最初把 `suites/`
  放在 git 根**之外**，第一反应是「修相对路径」，正确的是**前置 fail-loud**
  （口径登记 4）——「能在前置挡掉就不该走到回滚」。
- **`mta plan` 的改动范围写死 `HEAD~1..HEAD`**：单 commit 的自建仓库会
  `fatal: ambiguous argument 'HEAD~1..'` → exit 3。gate 工程必须给**两个** commit
  （M2 Gate 同款），而 metadata **写盘不提交**（提交了就多一个 commit，范围即漂）。

---

## Task 3.5 评审修订记录（round 1，2026-10-10）

**评审报告**：`review/review_p3_task35_2026-10-10.md` —— 结论 **不通过**：
**1×P1 + 4×P2 + 9×P3**。18 组变异探针 + 9 组只读观察，全部在 worktree 副本上
做（评审结束主仓零改动）。**核心事实经独立复跑全部成立**（F6 状态机、AE 双层、
迁移 004、M3 Gate exit 0 / 1844 passed 是复核者自己跑出来的数字），但
「零覆盖 / 过度声称」这两类本仓经典缺陷**这次仍有命中**。

### 修订 1（P1-1）：`--bundle-id` 缺省 → 未捕获 `ValueError`，工具契约破

`app_id = args.bundle_id or ""` 遇上 trace 里真实存在的 `com.demo`，`graph_query`
的 fail-loud `ValueError` **穿透成 traceback**（无 `generate: exit 3`）。这不是
边缘：`--fake-driver` 是本地冒烟最常用的路径，而真实工程里 `mta run` 写的 trace
**必有**非空 app_id；help 文本还写着「--fake-driver 时不需要」，等于平台自己
承诺了一个不存在的用法。测试漏它是因为夹具 `_argv` **恒**带 `--bundle-id`。

- 修：新增 `_trace_app_id(db)`（**只读**探测，库不存在直接返回空串，不
  `connect`——那会建库文件）；`app_id = args.bundle_id or _trace_app_id(args.db)`。
  与 trace 不符的 `--bundle-id` 同样由 `graph_query` 的 fail-loud 挡住，包成
  `PREFLIGHT ERROR … exit 3`。help 改写为「trace 图的范围键；不给则从 trace 取」。
- **修的过程中发现第二条**：trace 库**未初始化**（无 `runs` 表）时 `graph_query`
  也 fail-loud（`no such table: runs`）——但那是给「库被指错」的诊断，不是给
  「还没跑过」的。矩阵 #6 的语义是「没有历史观测 = 零 gap = exit 0」，故加
  `_trace_has_runs()`（只读探测）把两者分开：**空 → 空图 exit 0**；**坏 → exit 3**。
  这条是修 P1-1 时**派生**出来的，不在评审清单里。
- 钉子 3 条：不给 bundle-id 走通主路径（无 traceback）；给错 id → exit 3；
  空 trace → `gaps=0` + exit 0。

### 修订 2（P2-1）：ACCEPT 的**反向**卡死窗口（commit 已落、记账失败）

模块 docstring 花了一整段论证「照 promoter 的顺序是为了避免卡死」，却在反方向
复制了同一种卡死：`run_git commit` 成功、`store.decide_candidate` 失败 → 文件已
提交、库仍 PENDING、重试被「目标文件已存在」**永久挡死**（`修复后重试即续传`
这句对这条路径不成立）。已有的「git 失败回滚」测试只覆盖异常在 try 内的那一半。

- 修：commit 后的写库包 `try/except`，失败时 `git revert --no-edit <sha>`
  **真回滚**（只追加历史、不改写，与打印给用户的「撤销 = git revert」同一手法）；
  连 revert 都失败时异常**带上 sha**（最坏形态的第一手线索，不许静默）。
  两条路径都保持「候选 PENDING + 工作区干净 → 重试即续传」。
- 钉子 2 条：注入 DB 故障 → revert 生效 + 重试成功（续传语义真的成立）；
  revert 也失败 → 异常含 sha 与「人工处理」。

### 修订 3（P2-2）：「`review_status` 唯一写路径」是过度声称

`save_candidate` 仍接受并写 `review_status`（`INSERT` 分支），能产出
`ACCEPT` 但 `reviewer`/`reviewed_at` 全 `NULL` 的行（**看起来像已审核，实则
无人审核**），且绕过 `decide_candidate` 的全部领域校验（note 必填 / 不可二次
决策）。既有测试 `test_list_candidates_filters_pending_set` 就在用这条旁路。

- 修：**删掉形参**（不是加断言——断言仍留一个可绕过的入口），`INSERT` 里写
  死 `'PENDING'` 字面量。调用方再传 `review_status=` 会以 `TypeError` fail-loud。
  测试改用 `decide_candidate` 造已决行。
- 钉子 1 条：传 `review_status=` → `TypeError`；正常入库仍是 PENDING。

### 修订 4（P2-3）：F7 的 AST 守卫**可被 docstring 满足**（守卫空转）

判据是 `ast.get_source_segment(src, node)` 里含字符串 `_check_autonomous_env`
——docstring 与注释都在该范围内，而 `cmd_generate` 的 docstring 恰好提到这个名字。
实测：**删掉真调用（docstring 留着）→ 0 red；包进 `if False:` → 0 red**；只有连
docstring 提及一起删才红。功能侧有 CLI 级钉子兜底（删调用 → 1 red），但守卫
本身是**假绿**——这正是本仓反复出现的「守卫存在但永远轮不到」形态。

- 修：判据改为「**至少一个真的调用节点**」（`_gate_calls(node)` 非空），并追加
  `_dead_branches` / `_in_dead_branch`——真调用被包在恒假 `if` 里的同样算空转。
- 钉子就是守卫自身：探针实测两种架空形态（删调用留 docstring / 包 `if False:`）
  **都变红**（修前 0 red）。

### 修订 5（P2-4）：`to_store_dict` 的 docstring + 测试注释共同撒谎

两处都声称它是 `save_candidate(**...)` 的实参形状、且「Task 3.5 落盘要复用」。
实测 `save_candidate(**tc.to_store_dict())` → `TypeError`（`review_note` /
`reviewer` / `reviewed_at` 不在形参里），而 3.5 的 ACCEPT 落盘实际走
`dump_testcase_yaml(tc)`，**从没**消费它。

- 修：docstring 改为「行模型的**字段快照**」，并写明**为什么审核三列不在里面**
  （不是缺漏，是刻意的单向门——修订 3 让 `save_candidate` 只写 PENDING）；
  测试注释同改，并补一条**逆运算自洽**断言（行 → dict → 行，
  `exclude={"created_at"}`——那列归 DB 拥有，是唯一刻意缺席的字段）。

### 修订 6（P3-1）：从 promoter 抄来的「恢复分支」是**死代码**

前置 `original is not None` 已经 fail-loud，控制流永远进不了那个 `else`。
探针把该分支改成 `raise AssertionError` → **0 red**（铁证）。promoter 那边**是
活的**（它没有前置 fail-loud）——照抄形态而不照抄前提，就得到一条永不执行的路径。

- 修：删掉该分支（`original` 恒为 `None`），并把「回滚 = 删掉自己刚写的那一个」
  的理由写在原地。探针 R8（把死分支加回去）**0 red**——正是「删对了」的证明。

### 修订 7（P3-2 / P3-3 / P3-4 / P3-5）：CLI 的错误面与死分支

- **P3-2** reject 的错误映射零覆盖（`return 3` → `7` 曾 0 red）：补 2 条
  （候选不存在 / 已决候选）。
- **P3-3** 两处 `return 2` **不可达**（子解析器 `required=True` 先抛
  `SystemExit(2)`）：删掉，并把测试改测**真实拦截点** `SystemExit(2)`。
- **P3-4** `sqlite3.Error` **不是** `RuntimeError`——`--db <目录>` 时裸
  `OperationalError` 漏成 traceback：三处 except 纳入 `sqlite3.Error`，`list`
  与 store 构造各自包成 `CANDIDATE ERROR` + exit 3。
- **P3-5** `export_candidate_patch` 的裸 `yaml.safe_load` 与 ACCEPT 路径不一致
  （坏 YAML → `ParserError` 穿透）：复用同一处置（包成 `CandidateReviewError`）。
  钉子 1 条。

### 修订 8（P3-6 / P3-7 / P3-8 / P3-9）：文档一致性、口径登记、run 行收口

- **P3-6** gate 顶部 docstring 说 G6 是 exit 3，代码断言 2（argparse 的 required
  → 2，**代码对、docstring 错**）：改 docstring 为 exit 2。
- **P3-7** **口径登记**（新增，见下）：Dry Run 的观测写进**作为图源的同一 trace
  库**——这是刻意的（Dry Run 是真实执行），但审计原先只登记了「`--db` 给
  Lifecycle 用」，没登记「因此 candidate 的 Dry Run 会改写图源」。补钉子 1 条
  （`generate` 在图源库里留下自己的 `gen_*` run 行）。
- **P3-8** `end_run` 在 `try/finally` 的 finally **之外**，生成器抛异常时 trace
  里留下 `status='RUNNING'` / `end_time=NULL` 的持久化「跑了一半」痕迹：移入
  异常分支（ABORTED + exit 3），异常不再穿透成 traceback。钉子 1 条。
- **P3-9** `test_candidate_review.py` 的注释「落盘内容 = 库里的文本」只在夹具
  用规范 dump 时成立：改成「落盘 = `dump(parse(库文本))`，与库同源」，并补一条
  **非规范库文本**（flow-style + 注释）→ 落盘被规范化重排但**语义等价**的钉子。

### 📌 口径登记 5（新增）：Dry Run 的观测**进**图源库

`mta generate` 的 `--db` 同时是 coverage 图的来源与 Dry Run 的 `TraceStore`
落点。于是每一轮生成都会往图里补进刚跑过的候选观测。**刻意保留**（Dry Run 是
真实执行，观测理应进 trace；P1 的 `mta run` 同款），**并显式登记**：它意味着
「两次 generate 的 gap 可能不同」（前一轮的 Dry Run 覆盖掉了一部分 gap），
本仓 gate 的 G3/G4/G7 实测两次 generate 的 gap 稳定，但那是**数据**结论不是
**构造**保证。要改的话是「`--db` 拆成 `--db`（图源）+ `--gen-trace-db`（Dry Run
记录）」的立项，不是静默改行为。

**副作用一并登记**：`--db` 指向一个**尚不存在**的库时，本命令**会创建它**
（Dry Run 要写 run 行）——与「空 trace → exit 0」并不矛盾。

### 修订后实测

- 全量 `pytest tests`（macOS）：**1857 passed / 0 failed**（exit 0；修订前 1844，
  **+13**）。
- **M3 Gate `phase0/verify_p3_m3.py` → exit 0**，G1–G8 全绿（G8 复跑 1857 passed）。
- **A/B 探针 8 组**（本轮修订，全部自跑；还原后清 `__pycache__`、核对
  `git diff` 无残留）：

  | 探针 | 变异 | 结果 |
  |---|---|---|
  | R1 | commit 后失败的 `git revert` 去掉 | **2 failed** |
  | R2 | `save_candidate` 加回 `review_status` 形参 | **1 failed** |
  | R3 | `--bundle-id` 回到裸 `or ""` | **1 failed** |
  | R4 | `end_run` 移出异常分支 | **1 failed** |
  | R5 | reject 错误映射 `3→7` | **2 failed**（修前 0 red） |
  | R6 | 恢复不可达的 `return 0` 分支 | **0 red**（预期：不可达分支无语义） |
  | R7 | export 不包 YAML 解析错误 | **1 failed** |
  | R8 | 恢复死分支（`original` 恢复） | **0 red**（预期：同上） |
  | P2-3a | 删真调用、docstring 留名字 | **1 failed**（修前 0 red） |
  | P2-3b | 真调用包进 `if False:` | **1 failed**（修前 0 red） |

  R6 / R8 的 0 red 是**预期的**（把不可达/死代码加回去不改变任何可观测行为）
  ——它们是「删对了」的证明，不是漏测。

### 未修订（显性登记，非本次范围）

- **`review_note` 未过 `redact`**（评审「未验证 9」）：P2 的
  `tracer/storage.py::decide_review` 走 `redact(note)`，本处直存。实测
  `redact()` 对**字符串**入参是恒等（它只递归 dict/list 的敏感键），故 P2 那处
  本身也是空转——但两侧形态不一致仍是事实。**本轮不改**：改 `decide_candidate`
  的 SQL 参数只是把一个空转换成另一个空转，真正要做的是给 redactor 加
  「自由文本里的凭证模式」能力，那是独立立项（且属 P2 面的统一）。登记在此。
- 评审其余「未验证」项（真机路径无设备、P2-1 触发概率未量化、M4/M5 的 `kind`
  复用面、promoter E10 等价性只读比对）保持登记，均为环境/范围所限。

### 教训（已并入 MEMORY §5）

- **守卫的判据不能是「源码里出现过这个名字」**：`get_source_segment` 含
  docstring 与注释，于是「删掉真调用、只在文档里提一句」就能让守卫空转。
  判据要用**结构**（AST 调用节点），且要问一句「这条判据能不能被**不做事**的
  改动满足」。同族的还有「恒假分支里的调用」——`ast.walk` 找得到，但它不跑。
- **照抄形态而不照抄前提，就得到死代码**：`review.py` 的 `original` 恢复分支
  抄自 `promoter.py:283-285`，但 promoter 那边**没有**前置 fail-loud，本处有。
  前置一挡，恢复分支永不执行，而它读起来「很稳妥」。抽公共形态时，**前提**
  和**形态**要一起搬。
- **同一个卡死窗口有两个方向**：论证「顺序 A 避免了卡死」不等于「顺序 A 不会
  卡死」——反方向的窗口（副作用成了、记账没成）要用**反向**的撤销手段（git
  revert，而不是删文件）。凡「不可逆的外部副作用 + 可逆的本地状态」，两个
  方向都得有出口。
- **「唯一写路径」必须靠**删掉**旁路来成立，不是靠加断言**：断言留一个可绕过
  的入口（而且它读起来像是守住了），删形参让误用以 `TypeError` 当场炸。凡
  docstring 里出现「唯一/只/从不」，都该问「另一条路在哪、能不能删掉」。

---

## 「教训（已并入 MEMORY §5）」的逐条核对（2026-10-10，M3 收口前）

本文件共 **14 处**「教训（已并入 MEMORY §5）」。M3 收口前把这 14 处**逐条**
与 `.workbuddy-ai/memory/MEMORY.md` §5 对账（MEMORY 未被 git 跟踪，无法靠
`git log` 查它何时更新，只能现读）。结论：**10 处属实，4 处当时确实是假声明**——
教训只写在审计里，没进 MEMORY：

| 缺的块 | 缺的教训 | 现已并入 |
|---|---|---|
| Task 3.0/P3 Gate（L1617） | 「逐字节不变」这类强口径在加字段后必然失真 | §5.C 口径条目 |
| Task 3.3（L2045） | 对抗性 fixture 假对抗（naive-join mutant 20 passed 空转）；分支覆盖率自查只信探针 | §5.C ⑫ |
| Task 3.4 round 2（L2562） | 「唯一覆盖某分支的测试」守的是空气；抽 helper 的行为等价要新写直调测试；helper 会洗掉被测形态；逐条检查 + 遇情况即 return 把「检查了哪些」交给遍历顺序 | §5.C ⑬⑭ + §5.D |
| Task 3.4 round 2（L2683） | 「补了测试」≠「覆盖了分支」（前置状态是覆盖的一部分）；冻结历史形态的夹具不能用「读当前脚本」实现；迁移只许向后追加 | §5.C ⑬ + §5.D |
| Task 3.5 完成记录（L2848） | 「照谁的范式」要分开问；同一命令里的两个 store 必须各自点名；落盘路径必须在 git 根之内 | §5.D ×2 + §5.A |

**为什么记这一段**：这 14 处声称是**审计文档自己犯的审计失真**——而且我第一反应
（「14 处全是假的」）本身也是**过度概括**，实测只有 4 处假。两种错法（把没做的说成
做了 / 把没查的说成查了）都是同一族。**处置选「真并进去」而不是「把声称改成待并入」**，
因为这 4 块教训是真教训，降级措辞等于把可复用的经验留在审计里等下次重读。

顺手改掉 MEMORY §5.C 的一处**自身漂移**：小节标题写「守卫空转的**八种**形态」，
而正文形态已到 ⑪——那个计数本身就是一处漂移，现改为「形态清单」并注明不写死个数。

---

## Task 3.5 评审修订记录（round 2 / final，2026-10-10）

**复核报告**：`review/review_p3_task35_final_2026-10-10.md` —— 结论 **有条件通过**：
14 条修订中 **9 条真核销、5 条部分**；修订**引入 2 条新缺陷（2×P1）**、**暴露 2 条既有
缺陷（P2）**、另有 7 条 P3。复核者自跑 **34 组探针**，副本 `git status` 零改动。

**数字对账**：三个核心声称全部复现一致——全量 **1857 passed**（父提交副本实跑 **1844**
→ +13 ✅）、M3 Gate **exit 0**（G1–G8 全 PASS，G8 复跑 1857）、探针表 10 条中 9 条
数字一致。唯一不符：**R4 描述不严谨**（审计写「`end_run` 移到 finally = 1 failed」，
实测「移到 finally」是**等价变异 0 red**，「彻底删掉」才 1 red）——已在本段更正。

### 修订 1（P1-A，本轮引入）：`app_id` 的解析在 `try` 之外 → traceback 换路径复活

`cli/main.py`：`app_id = args.bundle_id or _trace_app_id(args.db)` 写在 `try` 之前。
P1-1 把「前置错误不再 traceback」修在 `graph_query` 那一侧，而 `_trace_app_id`
**自己就** `connect` + 查询——目录 / 非 sqlite 文件 / 多范围库三种坏输入全由它抛。
实测：同样三种输入**给了** `--bundle-id` 是 exit 3，**不给**就是 traceback rc=1。
即 P1-1 的声称只在一半输入上成立，而 `--fake-driver` + 本地库正是最常用的本地路径。

- **修**：`app_id = ...` 挪进 `try`；`except` 由 `sqlite3.DatabaseError` 换成
  **父类 `sqlite3.Error`**（P3-4 的同一条判据：「文件不是 sqlite 库」抛的是父类本身）。
- **钉子**：`test_bad_db_without_bundle_id_is_a_preflight_error_not_a_traceback`
  ——三种坏 `--db` × 不给 `--bundle-id`，逐形态断言 `rc == 3` + 无 traceback +
  候选零落库。**探针**（把 `app_id` 挪回 `try` 外）→ 1 red。

### 修订 2（P1-B，本轮引入）：promote commit 不带 pathspec → 连带提交/撤销别人的在途工作

`candidates/review.py`：`git add rel` 之后 `git commit -m ...` **不带路径**。
不带路径的 `git commit` 提交的是**整个暂存区**。我自己复现：别人
`git add others_work.py`（未提交）→ promote commit 的 `show --name-only`
**含 `others_work.py`**；DB 记账失败回滚时 `git revert` 又**把它一起删掉**
（文件消失、`git status` 干净——连「有东西被删了」都不留痕）。不可逆地动第三方
在途工作且无任何提示。讽刺的是同一个函数里 `target.relative_to(repo_root)`
那道 fail-loud 已经说明作者知道「只该动自己的文件」，只是没在 commit 上贯彻。

- **修**：`commit` 带 `-- rel`。撤销侧随之收窄（commit 里只有自己的文件）。
- **钉子**：`test_promote_commit_does_not_sweep_in_other_peoples_staged_work`
  （断言 `show --name-only` **恰等于**自己那一个路径）。**探针**（去掉 `-- rel`）→ 1 red。

### 修订 3（P2-C，本轮暴露）：`git revert` 在脏暂存区下被拒 → 建议本身执行不了

实测四种前置：工作区干净 / 未暂存的无关修改 → revert 成功；**暂存区有别人的新文件** /
**暂存了对既有文件的修改** → revert **直接被拒**
（`error: your local changes would be overwritten by revert`）。而「改到一半顺手审一个
候选」正是最常见状态。被拒本身不弄坏任何东西，却把仓库留在**最坏形态**（commit 已落 +
库还 PENDING），且原错误消息让人「先 `git revert` 再重试」——**那条建议自己就执行不了**。

- **修**：`_undo_promote(repo_root, rel, sha)`——首选 `git revert`（只追加历史，
  与打印给用户的「撤销 = git revert <sha>」同一手法）；被拒则退到**只作用于自己
  路径**的逆提交（`git rm -- rel` + `git commit -- rel`），P1-B 那条判据在撤销侧
  再贯彻一次；两条都断才报「需人工处理」，且人工步骤改为 `git rm` + 提交
  （**真能执行**的那条）。
- **钉子**：`test_rollback_survives_a_dirty_staging_area`（暂存区非空 → 撤销成功、
  别人的文件原样留着、历史是**追加**的 3 个 commit 而非 reset 改写）+
  `test_revert_failure_is_reported_with_the_commit_sha`（**两条**撤销路都断才报人工，
  且消息必须含 `git rm`）。**探针**（退路直接 `return False`）→ 1 red。

### 修订 4（P2-D，本轮暴露）：AST 守卫对 4 种规避形态仍空转

`_dead_branches` 只判 `test` 是字面量且为假的 `if`/`while`。复核实测 4 种形态
**0 red**：① 真调用搬进本函数内的嵌套函数（无人调）；② `if _GATE_DISABLED:`
（test 不是常量，恒假检测完全看不见）；③ 搬进不可达的 `except Exception:`；
④ 包进 `lambda`（无调用方）。其中 ②③ 当时有 `cmd_plan` 的功能测试兜住（2 red），
①④ 没有。

- **修**：判据收紧为「调用必须处于**无条件执行**的位置」——从调用节点上溯到函数体，
  每一层都必须是白名单里的「到了就一定求值」节点（裸 `Expr` / 赋值右值 / `return`
  / 调用实参…）。白名单**闭口**：新语法形态默认算条件位，守卫默认从严。
  它是恒假判据的**超集**（`if False:` 同样过不了），且一并覆盖 ①–④。
  原 `_dead_branches` / `_in_dead_branch` 删除（留着就是死代码）。
  现有五个自主命令全部满足（实测 plan / generate 的祖先链都只有 `Expr`）。
- **钉子**：同一条 `test_every_wired_autonomous_command_passes_the_gate`。
  **探针**：上述 ①–④ + `if False:` / `while 0:` / 删真调用共 7 种形态，**逐一 1 red**。

### 修订 5（P2-E，既有，本轮未修）：`from_store_dict` 的 `or "PENDING"` 静默兜底

`candidates/test_case.py`：`review_status=row.get("review_status") or "PENDING"`。
库里 `review_status=''` 会被静默读成 `PENDING`——而 `decide_candidate` 的门槛正是
`review_status != "PENDING"` 就拒绝，**兜一次等于开了一条重新决策的旁路**，绕过
领域层的全部校验（note 必填 / 不可二次决策）。与 P2-2「`review_status` 的唯一写路径」
严格性不对称：写侧不许有旁路，读侧也不许有默认值。

- **修**：字段收进词表——`review_status: Literal[*REVIEW_STATUSES]`（同一份
  `REVIEW_STATUSES`，不手抄第二份——3.4「同一事实的第二处字面量必然漂移」的教训），
  `from_store_dict` 改 `row["review_status"]`。**双闸**：空串/词表外的值 →
  `ValidationError`（构造时就炸）；缺键 → `KeyError`（该列 DDL 是
  `NOT NULL DEFAULT 'PENDING'`，正常行必然有值——缺了只可能是别处写坏了）。
  ⚠️ `dry_run_status` **刻意仍用** `or "PENDING"`：它不是审核状态、没有第二道门槛
  依赖它，兜成 PENDING 只是「还没跑过 Dry Run」这个中性语义。**两列宽严不同是刻意的**，
  已在 docstring 写明，不是漏改。
  （第一版只改成 `row["review_status"]` 是**不够的**：空串不是缺键，`KeyError`
  根本不抛，空串照样静默穿进模型——钉子跑了才发现，判据必须真的验证「空串会炸」。）
- **钉子**：`test_an_empty_review_status_is_not_silently_read_as_pending`
  （空串 + 缺键都 `KeyError`；同时钉住 `dry_run_status` 的宽松仍在）。

### 修订 6–12（P3 级）

| # | 发现 | 处置 |
|---|---|---|
| P3-F | `cmd_candidate` 的 5 处新 `except sqlite3.Error` 里只有 `list` 一分支有钉子，其余四处「改成任意别的值都不红」 | 补 4 条：store 构造（accept/reject 参数化）、`--export-patch` 撞坏库、accept/reject 候选不存在 |
| P3-G | `g6_reject_requires_note` 的 docstring 写「且**不留任何决策痕迹**」，但 `check()` 只断言 `returncode == 2 and "--note" in body`——**「不留痕迹」完全没有实现** | 真验：`review_status` 仍 PENDING 且 `reviewer`/`reviewed_at`/`review_note` 全空。**gate 的说明文字与判据之间没有自动一致性检查，同一句谎可以再撒一次** |
| P3-H | `LIKE 'gen_%'` 的 `_` 是 LIKE **通配符**，`genx_abc` 也匹配（0 red）——钉子比它声明的宽一格 | 改在 Python 侧 `startswith("gen_")`（两处） |
| P3-I | `phase0/verify_p3_m3.py::mta_candidate` 全仓零调用方（G5a/G6 都是裸 `sh(...)`） | 改为走 `mta_candidate(...)`（去重 + 消死代码） |
| P3-J | G1 的 F7 拒绝对 `generate` 是 **exit 1**（`SystemExit(str)`），G2 staging 是 **exit 3**；而 `cmd_generate` 注释自称「退出码契约只有 0/3」 | **不改代码**（`cmd_plan` 同款、非本轮引入）。登记为口径：自主命令的**环境闸**走 `SystemExit`（exit 1），**业务前置**走 exit 3——Gate 输出把两者都印出来，读者不要以为是同一个契约 |

### 复核者自陈未验证（显性登记）

① 真机路径未跑（无设备）：`--bundle-id` 必填分支、真机装配、`udid` 解析均无端到端验证；
② P2-3 的 4 种规避只在 `cmd_plan` 侧验证了功能兜底，`cmd_generate` 侧的 sandbox 兜底
是推断未逐形态实测；③ P2-1 触发概率未量化；④ 并发 accept / accept 期间别人正在
`git commit` 的冲突形态未测；⑤ 未复跑首轮 35 组探针全表（只跑与本 14 条相关的 34 组）；
⑥ `sqlite3.Error` 家族只验了 `OperationalError`/`DatabaseError`；
⑦ 迁移对既有行的回填只在新建库路径验证；⑧ `git revert` 在脏工作区的行为是
git 2.54 实测，其他版本可能不同。

### 修订后实测（2026-10-10，tag 前）

- 全量 `pytest tests`（macOS）：**1866 passed / 0 failed**（= 1857 + 本轮新增
  钉子 9 条：P1-A ×1、P1-B ×2、P2-C ×1、P2-E ×1、P3-F 参数化后 ×4）。过程中
  本仓自己的 `test_no_duplicate_top_level_definitions` 抓到一次真事故：
  `test_rollback_survives_a_dirty_staging_area` 被追加了两份（编辑工具重放）——
  **守卫确实在工作**（MEMORY §5.D 的教训不是白记的）。
- **M3 Gate `phase0/verify_p3_m3.py` → exit 0**，**11/11 PASS**；G6 的新判据
  （失败 reject 前后决策四列快照对比）真跑，G8 复跑全量 1866。
- **AST 守卫探针**（`out/probe_ast.py`，判红以 **pytest 退出码**为准）：7 种
  规避形态（嵌套未调用函数 / `if _GATE_DISABLED:` / 不可达 except / lambda /
  `if False:` / `while 0:` / 删真调用）**逐一 1 red**，基线与还原 0 red / OK。
  ⚠️ 过程教训：第一版探针按「输出里有 `N failed` 文本」判红，正则落空把**真红
  全部误报成 0 red**——判据要钉在机器可依赖的信号（退出码）上，不是输出措辞上。
- P1-A / P1-B / P2-C 的钉子各配 A/B 探针（还原修法 → 各 1 red），探针后
  `git diff --stat` 为空。
- **收口**：`docs/handover.md` §9 刷到 M3 收口态 → **tag `checkpoint-p3-m3`
  打在 `84c47c9`**（round 2 修订提交；tag 不可再动）。

### 教训（已并入 MEMORY §5）

- **修一条真缺陷时，要问「这条修法覆盖它声称的**全部输入**吗」**：P1-1 在
  `graph_query` 那一侧修好了「不再 traceback」，而 `app_id` 的解析自己也会抛——
  同一条契约在另一半输入上仍然是 traceback。**判据的位置要比声明的位置更靠前**：
  凡「前置错误 → exit 3」的声称，要把**所有**能抛的调用都圈进同一个 `try`。
- **不可逆副作用的**作用域**要显式收窄，不能靠"我只 add 了一个文件"**：`git commit`
  不带 pathspec 提交的是整个暂存区。同理 `git revert` 撤的是整个 commit。
  **外部副作用的半径必须写进命令本身**（`-- rel`），而不是指望前后文的意图。
- **错误消息里给的步骤，必须自己先执行一遍**：「先 `git revert` 再重试」在触发它的
  那个场景（脏暂存区）下 git 自己就拒绝执行。**给不出可执行步骤的失败消息，
  等于把最坏形态留给用户**。
- **守卫判据从"有这个东西"收紧到"它在无条件位置"**：`ast.walk` 找得到任何位置的
  调用，包括压根不会跑的。白名单要**闭口**（新形态默认算条件位），否则将来出现新的
  延迟/条件求值写法时守卫默认放行。
- **读侧不许有默认值**：写侧"唯一路径"收口了，读侧一个 `or "PENDING"` 就能把
  「已决」洗成「未决」，从而绕过全部领域校验。**同一列两侧的宽严必须对称**。

---

## Task 4.1 完成记录（P3-14 Explorer Loop 骨架 + Guard 接入，2026-10-11）

**M4 第一个任务**；也是 **M4∥M5 交错开发**的第一批（用户拍板：单会话交错——
无共享面的任务交错推进，共享面 migrations/review kind/CLI/storage 按序集中落，
迁移取 005/006，plan 写的 003/004 已被 M3 占用）。

### 交付

- `explorer/__init__.py` / `explorer/policy.py` / `explorer/agent.py`：
  observe → decide → guard → execute → verify 主循环，每步落 `agent_trace`
  （observation 带前后屏与指纹、decision 带风险预判、guard_result 带裁决）。
- `tests/unit/test_explorer_guard.py` **10 例**。

### 决策记录

1. **探索侧 LOW-only 闸的拦截点在 explorer 循环，不在 P1 Guard**：P1 Guard
   在 sandbox **放行 HIGH**（人工用例的合法空间）——初版探针实测
   `pay_button(HIGH)` 被 explorer 放进 toolkit 执行了。设计 7.1「LOW 才允许
   自动执行」的裁决点因此在探索侧（同一个 `effective_risk` 本体判定，F1）；
   **CRITICAL / blocked_targets / production 仍由 toolkit 内建 Guard 兜底**
   （探针里 CRITICAL 用例保持绿 = 分层成立）。两层各管一段，BLOCK 审计各自
   留痕：HIGH → explorer 步骤记录 1 条；CRITICAL → toolkit audit + 步骤记录
   2 条（口径登记）。
2. **复用断言三件**：`explorer.policy.effective_risk is executor.policy.
   effective_risk`（import 级）；explorer 包唯一放行的 `executor.*` import 是
   `executor.policy`（**AST 守卫**——判据钉结构不钉文本，P2-D「docstring 能
   满足守卫」教训的直接应用）；`Explorer` 构造只收 `AgentToolkit`（F12 类型
   断言），且 `agent_db`/`task_id` 必填（无审计通路的探索不许启动）。
3. **decider 是注入回调** `(observation, graph, visited) -> ProposedAction |
   None`：4.2 的 frontier 选择 = 换一个 decider 构造器，循环本体不动。
4. **终态词表 4.1 只有 `COMPLETED`**：FRONTIER_EMPTY / `*_BUDGET_EXCEEDED` /
   LOOP_DETECTED 由 4.2/4.3/4.4 各自增量加（不提前占词——每项有自己的测试
   与矩阵行）。
5. **verify 降级而非中断**：post-observe 失败不否定已执行的动作，记空后继续
   （pre-observe 失败才 `ExplorationError` fail-loud——没有观测就没有 decide
   的输入）。

### 实测

- 全量 pytest **1876 passed / 0 failed**（1866 + 10）。
- **A/B 探针**：架空 LOW-only 闸（`if False and …`）→ **1 red**（HIGH 用例；
  CRITICAL 由 toolkit Guard 独立兜住仍绿——两层判据各自可独立探针）；还原后
  10 passed + `git diff --stat` 空。
- 过程由测试抓到的真问题 1 处：初版把 HIGH 拦截寄托在 toolkit Guard 上，
  sandbox 下 Guard 规则不拦 HIGH（见决策 1）——先红后绿的流程当场暴露。

### 教训（已并入 MEMORY §5）

- **同一个 Guard 函数服务两种授权模型时，授权差异要落在各自入口**：P1 Guard
  的 sandbox HIGH 放行是**人工用例**的空间；自主 Agent 不能借这个口子。指望
  「Guard 会替我拦」而不核对 Guard 的实际规则，拦截就是假的。

---

## Task 4.2 完成记录（P3-15 Frontier 选择，2026-10-11）

### 交付

- `explorer/frontier.py`：`select_frontier`（F13 纯函数）+ `make_frontier_decider`
  （把 frontier 包成 4.1 的 decider 契约）。
- `explorer/agent.py` 扩展：`StopDecision`（带终态的结束）+ `VisitLog`
  （指纹/屏 id 双视图）+ 终态 `FRONTIER_EMPTY`。
- `tests/unit/test_frontier.py` **10 例**。

### 决策记录

1. **签名偏离登记**：plan 写 `select_frontier(graph, visited, current_screen)`
   单图入参，但「source 有/runtime 无」与「runtime visit_count」需要两面图，
   `RuntimeGraph` 图级 `source_of` 一致性校验禁止混面 → 拆显式
   `source` / `runtime` 两入参。
2. **unexplored 优先是双重加固**（组标志进排序键 + `_UNEXPLORED_SCORE=3.0`
   高于一切 explored 分值）——探针 round 1 实测：单把分值压到 0.5，排序
   纹丝不动（组标志独立兜住）；round 2 压到 0.1（组标志与分值倒挂）→
   `test_novelty` 1 red。novelty 分值的钉子为真，序的钉子为组标志。
3. **「jump/skip」语义**：`action_for` 返回 None = 这条翻不成动作 → 跳下一项
   （None 是「跳过」不是「结束」）；全部翻不成/frontier 空 →
   `StopDecision(FRONTIER_EMPTY)`。由 `test_frontier_decider_skips_items_...`
   钉住。
4. **transition 身份复用 `graph.diff.transition_key`**（F8 同一身份定义），
   不手写第二份三元组拼接。
5. 过程教训：skip 测试初版备 3 个提议但只有 2 条 frontier 项——第三个提议
   永远轮不到，测试当场红。**fixture 的数量要数一遍「会轮到几次」。**

### 实测

- 全量 pytest **1886 passed / 0 failed**（1876 + 10）。
- A/B 探针两轮：`_UNEXPLORED_SCORE` 0.5 → 0 red（双重加固，见决策 2）；
  0.1 → 1 red；还原后 10 passed + tracked 文件 diff 干净。

---

## Task 4.4 完成记录（P3-17 Loop Detection，2026-10-11）

### 交付

- `explorer/loop_detector.py::is_loop`：窗口内重复指纹计数的**纯计数函数**
  ——不做哈希、不做相似度（F8 红线），指纹来自 P2 `screen_fingerprint`。
- `explorer/agent.py` 接线：`loop_window` / `max_repeated_state` 构造参数
  （**必须成对**给出，单给一半 fail-loud）+ 终态 `LOOP_DETECTED` + 停止步
  留痕（trace `result="LOOP_DETECTED"`，审计能回答「为什么停」）。
- `tests/unit/test_loop_detector.py` **9 例**（纯函数 6 + 接线 3）。

### 决策记录

1. **None 指纹的两层防线**（open question #3 默认口径）：① 主循环的到达
   序列**不记录** None；② `is_loop` 对 None/空串直接 False。重复的 None
   是解析器的问题不是 App 在循环——把 None 计入会让「解析坏了」伪装成
   「探索完成了」。
2. **参数默认 None = 不启用**（偏离登记：plan 4.4 只列纯函数；矩阵 #12
   需要主循环消费它）——4.3 的 ExplorationBudget 统一接 policy 默认值，
   这里不造第二份默认值（同一事实两处字面量必漂移）。
3. **窗口/阈值非法 fail-loud**：静默的「不算 loop」会让人以为检测在跑、
   其实从没生效。

### 过程抓到的真 bug（卡死 → 15s 诊断脚本定位）

初版把「去重的 visited」直接喂给 `is_loop`——**去重让每个指纹永远只出现
一次，出现计数永远到不了阈值**，toggle 场景无限循环（pytest 120s 超时）。
用打桩诊断脚本（SIGALRM 15s + 打印每次 `is_loop` 的输入输出）实锤后修复：
**去重视图（novelty/frontier）与到达序列（loop 窗口）分成两个列表**——
`arrivals` 含重复逐次追加，`visited` 保持去重。两条数据混在一个列表里，
「见过哪些」和「到过几次」必有一个撒谎。

### 实测

- 全量 pytest **1895 passed / 0 failed**（1886 + 9）。
- A/B 探针：窗口切片改头部（`[:-window]`）→ **3 red**（ping-pong 识别 /
  窗口语义 / 接线停止步数 2→8），还原后 9 passed + tracked diff 干净。
- 过程教训：探针选型避开「检测永不触发」类变异（会挂死而非快红）——
  **先想清楚 mutant 的失败形态再动手**。




## Task 4.3 完成记录（P3-16 探索预算，2026-10-11）

### 交付

- `explorer/state.py::ExplorationBudget`：四维预算的**不可变值对象**
  （`max_steps` / `max_duration_seconds` / `max_llm_calls` /
  `max_repeated_state`）。`from_policy()` 取 `agents/policy_config.py` 的
  `EXPLORATION_BUDGET` 真值源；`from_file()` 是 `--budget <file>` 的读取口，
  **校验复用 `ExplorationPolicy` 同一模型**（未知键 / 错型 / 负值 fail-loud
  ——不写第二份校验）。`DEFAULT_LOOP_WINDOW = 10` 在此**单点**定义
  （设计 7.3 的 window 不在四维预算里，policy 也没有这个键）。
- `explorer/agent.py` 接线：新增 `STEPS_BUDGET_EXCEEDED` /
  `DURATION_BUDGET_EXCEEDED` / `LLM_BUDGET_EXCEEDED` 三个终态；
  `_budget_exceeded(steps, t0)` 在**每轮顶部**检查，超限即停 + 留痕
  （trace `result=<终态>`，含已用步数 / 耗时 / LLM 次数）；`budget` 与
  `llm_budget` 构造参数带**类型断言**；`self.llm_budget` 公开（decider
  扣配额、报告读用量）。
- `tests/unit/test_exploration_budget.py` **10 例**（值对象 4 + 主循环消费 6）。

### 决策记录

1. **LLM 预算底层就是 P1 `LLMBudget`（F4）**：不新写计数器。`budget` 给了
   而 `llm_budget` 没给 → 从 `max_llm_calls` 造一个 P1 实例；显式注入则
   **类型断言**（`isinstance(llm_budget, LLMBudget)`，注入错对象构造上不可达）。
2. **`max_repeated_state` 不设第四种 `*_BUDGET_EXCEEDED`**（口径登记）：
   四维之一**经 loop detector 执行**，终态是矩阵 #12 的 `LOOP_DETECTED`。
   `budget` 带出这一维时自动补 `loop_window = DEFAULT_LOOP_WINDOW`——
   调用方不必同时知道两个参数。测试
   `test_repeated_state_dimension_runs_through_loop_detector` 钉死这条口径。
3. **超限即停，不自动放宽重试**（F11）：`steps` 停在**恰好** `max` 处，
   不多跑一步（`test_steps_budget_exceeded_stops_exactly_at_max` 断言
   `steps == 2 and tap_calls == 2`）。
4. **检查点在每轮顶部**：duration 维度因此能在**第一个动作之前**生效
   （`max_duration_seconds=0` → 0 步、0 次设备动作）。

### 过程抓到的两处测试自身的问题

1. `_budget(**overrides)` 里显式传了四个字段又 `**overrides` ——
   `_budget(max_steps=2)` 直接 `TypeError: got multiple values`。改成
   `values.update(overrides)` 后传。
2. `test_budget_with_generous_limits_does_not_short_circuit` 初版用默认
   `max_repeated_state=3`，而它的 decider 反复点**同一个不改变状态**的按钮
   —— 于是第 2 步就被 loop detector 判 `LOOP_DETECTED`。**这不是预算层的
   问题，是 F11 第四维在正常工作**：把该用例的 `max_repeated_state` 调高，
   让「宽裕预算不短路」这条断言只覆盖 steps/duration/llm 三维，并在注释里
   指向 `test_repeated_state_dimension_runs_through_loop_detector`。

### 实测

- 全量 pytest **1905 passed / 0 failed**（1895 + 10）。
- A/B 探针（三轮，逐维）：
  - `steps >= max_steps` → `>`：**1 red**（steps 用例，`STEPS_BUDGET_EXCEEDED`
    变 `LOOP_DETECTED`）；
  - duration 分支 `elapsed >= max_duration_seconds` → `>`：**0 red**
    ——`max_duration_seconds=0` 时 elapsed 恒 `> 0`，边界两侧不可观测。
    **登记为测试强度缺口**：这一维只钉住了「0 就停」，没钉住「恰好到点就停」。
    改用「维度串台」mutant（duration 分支读 `max_steps`）→ **1 red**，
    证明该用例确实在检查 duration 分支本体。
  - `calls_used >= cap` → `>`：**1 red**（llm 用例）。
  - 三轮均还原到 md5 `5f38e0d8c2fa5dc22b7013cdc9fea3c5`，tracked diff 只剩
    本任务的在途改动，`__pycache__` 已清。
- 口径核对：`out/pt.sh` 里 `-q` 与 `pytest.ini` 的 `addopts = -q` 叠加会把
  汇总行吃掉（只留进度点）——**读通过数要去掉包装脚本那层 `-q`**。

## Task 5.1 完成记录（P3-20 诊断模型 + 上下文装配 + bug_candidates 表，2026-10-11）

### 交付

- `diagnosis/__init__.py`：包说明（F10 第一道防线 + F3 不直连数据源）。
- `diagnosis/models.py`：`EvidenceItem`（`kind` 必须是权重表键集，拼错当场报错）、
  `Hypothesis`（**空 evidence 拒绝创建**；`origin ∈ {rule_based, llm_hypothesis}`）、
  `BugCandidateDraft`（`attribution_hypothesis` 默认 `UNTRIAGED`）；
  三处词表 `EVIDENCE_KINDS`（**派生**自 `DIAGNOSIS_EVIDENCE_WEIGHTS`）/
  `HYPOTHESIS_ORIGINS` / `ATTRIBUTIONS`（刻意复制的第二处字面量，F3 拿不到
  `tracer.storage`，故加一致性测试）/ `BUG_STATUSES` / `BUG_SEVERITIES`。
- `diagnosis/context.py`：`DiagnosisContext`（值对象）+ `build_context`
  （**全部经注入的 `KnowledgeSources`**；`steps` 与 `history` 是两个字段，
  后者**剔除本 run**）。
- `agents/migrations/005_bug_candidates.sql`（设计 §11 字段 + 三处 CHECK 词表 +
  两个索引；**偏离登记**：plan 写的 004 已被 M3 占用，顺延 005）。
- `candidates/bug.py`：`BugCandidate` 行模型（dict 互转，SQL 不在本包）。
- `agents/storage.py`：`save_bug_candidate` / `get_bug_candidate` /
  `list_bug_candidates` / `_bug_from_row`（+2 条写语句，全部在 `_write_tx` 内）。
- `experience/knowledge.py`：`trace_history` **扩 `run_id` 过滤键**（Task 2.2
  从 2 键扩到 5 键是同一手法——扩键，不新建平行接口，F3）。
- `graph/models.py` + `graph/builder.py`：`TraceStep` **追加**
  `screenshot_path` / `ui_tree_path`（设计 §8.1 的「Screenshot/UI Tree」入口；
  P1 起就在 `steps` 表里，早先没读出来。追加而非重排，既有构造点不受影响）。
- 测试：`tests/unit/test_diagnosis_models.py`（22 例）、
  `tests/unit/test_bug_store.py`（11 例）。

### 决策记录

1. **F3 的实现选择：扩过滤键，不新增检索方法**。初版给
   `KnowledgeSources` 加了个 `trace_run(run_id)`——但 F3 的原文是「**不新建
   平行的检索接口**」，而 `test_build_knowledge_adds_no_retrieval_method`
   钉死了「公开面 == Protocol 四方法」。诊断要的「这一次失败现场的完整步骤
   序列」本来就落在 `trace_history` 的语义里（「trace 的 steps 即历史」），
   多一个范围键即可——**新方法会同时打破那条守卫与 F3 的字面**。
   对照：`test_p2_knowledge_sources_is_constructed_in_one_place` 要求
   `P2KnowledgeSources` 构造点唯一，所以 `_testcase_run_ids`（`run_id` 键的
   底座）也只能在本类里。
2. **`reproducible` 保持三态**（迁移 005 不写 `NOT NULL DEFAULT 0`）：
   `NULL` = 还没试过复现 / `0` = 试过但复现不了 / `1` = 复现了。压成两态会让
   「复现不了」被静默改写成「还没试过」。`candidates/bug.py::from_store_dict`
   与 `_bug_from_row` 两处都**显式判 `is None`**——`or None` 会把 `False`
   变成 `None`（False 是假值），那正是本仓最防的「看起来正常」。
3. **`status` 不是 `save_bug_candidate` 的形参**（与 `save_candidate` 的
   `review_status` 同款处置，review_p3_task35 P2-2）：只写 `'DISCOVERED'`。
   状态推进的唯一入口是 Task 5.6 的状态机——**形参删掉而不是加断言**，调用方
   传它会 `TypeError` fail-loud。已推进的行同样不可被诊断重跑覆写（AE 闸门）。
4. **`severity` 复用 `Risk` 的四档名**（设计 §11 只写了 `severity TEXT`，
   没给词表）：`CRITICAL/HIGH/MEDIUM/LOW`，默认 **`MEDIUM`** 不是 `HIGH`
   ——证据不足时的默认立场是「不知道多严重」，抬到 HIGH 会让每个未复核的候选
   都挤进高优先队列。
5. **`graph_diff` 由调用方算好传进来**：`diff_graphs` 需要「参照面」而参照面
   的取法取决于 `--base-build` 的语义（§12.3），那是 CLI 的输入装配职责
   （Task 5.6）。本模块只声明「上下文里有这份 diff」，保持纯装配、零 I/O。

### 顺带修掉的两处存量守卫缺口

1. **`test_repo_hygiene` 的 `_SOURCE_DIRS` 没扫 `explorer/`**（Task 4.1–4.3
   落地后一直空转）：新增的 `diagnosis` 一并加入，现在两个新包都在
   「同名顶层定义」守卫的扫描面里。
2. **`_MIN_WRITE_STATEMENTS` 已比真实值低 5**：实测真实计数是 **27**
   （`experience/store.py` 13 + `agents/storage.py` 8 + `graph/storage.py` 6，
   用与守卫逐字相同的 `_write_sql_calls` 判据数出来的），而历次「照此上调」
   只把它抬到 22 ——等于**静默容忍 5 条写语句消失**。按实测校准到 27。

### 实测

- 全量 pytest **1938 passed / 0 failed**（1905 + 33）。
- A/B 探针（五轮，逐个红）：
  - 撤掉 `Hypothesis._evidence_not_empty` → **1 red**（F10 第一道防线）；
  - `attribution_hypothesis` 默认改 `APP_DEFECT` → **1 red**（系统不自动归因）；
  - `history` 不剔除本 run → **1 red**（矩阵 #16 的证据会凭空多一条）；
  - 空现场静默放行 → **1 red**（fail-loud 前置）；
  - `run_id` 过滤不生效 → **3 red**（现场/历史/空现场三条同时红）。
  - 五轮均还原到原 md5，`__pycache__` 已清，tracked diff 只剩本任务的在途改动。

## Task 5.2 完成记录（P3-21 规则优先的假设引擎，2026-10-11）

### 交付

- `diagnosis/classifier.py`：`crash_detected(ctx)`（**新增判定**——底稿修订 #6：
  P1 不存在 APP_CRASH 判定，`APP_CRASH` 只是 `FAILURE_TYPES` 里的字符串）、
  `is_assertion_only_failure(ctx)`、`rule_based_hypotheses(ctx)` 三条规则
  （崩溃→APP_DEFECT / 5xx+UI 正常→ENVIRONMENT_DEFECT / 漂移记录或 diff 命中
  →UI_DRIFT）。
- `diagnosis/analyzer.py`：`analyze(ctx, *, llm, budget, prompt_builder, parser)`
  ——规则命中即返回（**LLM 零调用**）；断言值不符直接空手返回；规则空才调 LLM。
- `llm/prompt.py::build_diagnosis_prompt`（可信区/不可信区分区，运行时观测进
  `[UNTRUSTED …]`）；`llm/parser.py::parse_hypotheses`（fail-closed：类型不符
  的条目丢弃，confidence 夹进 [0,1]）。
- `tests/unit/test_classifier.py` **21 例**。

### 决策记录

1. **UI_DRIFT 读 P2 的 reconciliation 记录，不重算**（plan 明文）：判据是
   `steps.detail_json` 的 `recovery.outcome == "DRIFT"`——那是
   `agent/recovery.py` 在恢复流水线里跑 `reconcile_local` 后由
   `cli/pipeline.py` 写下的。诊断时运行时 `page_source` 早已不在，
   重算既不可能也引入第二个判据源。
2. **`crash_detected` 的 infra 事件要配失败步骤**：单独一条 `WDA_DEAD`
   不算「App 崩了」——WDA 重启后用例照常跑完是基础设施抖动，不是 App 缺陷。
   判据写成 `crash_event and bool(failure_steps)`。
3. **断言值不符是「不进诊断」而不是「规则没命中」**：`rule_based_hypotheses`
   对纯断言失败返回 `[]`，`analyze` 据此**跳过 LLM**（不是「规则空了所以问
   LLM」）。两者在代码里是两个分支——合成一个会让每次断言失败都白烧一次
   LLM 调用，而设计 §8.2 末条明说它「不走 Diagnosis」。
4. **LLM 假设必须挂 `llm_hypothesis_only` 证据**：`Hypothesis` 的空证据闸门
   对 LLM 路径同样生效——「LLM 觉得」不是证据，「LLM 基于这些观测给出的假设」
   才是（观测进 `EvidenceItem.source`）。探针 4 实测：撤掉这条证据 → 直接被
   `Hypothesis` 的校验挡下（`ValidationError`），说明闸门真的在起作用。
5. **5xx 规则要求「UI 侧无定位失败」**：`ELEMENT_NOT_FOUND` / `AMBIGUOUS_ELEMENT`
   在场时不判环境缺陷——设计 §8.2 的前提是「**UI 正常**」。
6. **LLM 门槛「两个都给」+ 预算 `try_acquire()` 无参形态**：照抄
   `planner.plan` 的理由（只给 `llm` 不静默造 budget 对象）。预算耗尽**不绕过
   计数直调 provider**（F4）。

### 实测

- 全量 pytest **1959 passed / 0 failed**（1938 + 21）。
- A/B 探针（四轮，逐个红）：
  - 规则命中不再短路（`if rules:` → `if False:`）→ **1 red**（矩阵 #14：crash
    场景的 LLM 零调用被破坏）；
  - `_has_drift_record` 恒 False（丢掉 P2 reconciliation 复用）→ **1 red**；
  - `is_assertion_only_failure` 恒 False → **1 red**（断言值不符会进诊断）；
  - LLM 假设不挂证据 → **1 red**（被 `Hypothesis` 的空证据闸门挡下）。
  - 均还原干净，`__pycache__` 已清。
- 教训：探针 3 的初版写法（`if False:` 替换 `if ...: return []` 而没保留
  return 体）造成 `IndentationError`——那是**语法坏掉**不是行为变异，收集阶段
  就炸，测不出任何东西。改成「函数体首行 `return False`」这种**保留语法**的
  变异才有意义（与 Task 4.4「先想清楚 mutant 的失败形态」同一条教训）。

## Task 5.3 完成记录（P3-22 Evidence Score，2026-10-11）

### 交付

- `diagnosis/evidence.py`（新）：`score_evidence(evidence, *, weights=None) -> int`、
  `rank_hypotheses(hypotheses, *, weights=None) -> [(Hypothesis, int)]`、
  `suggested_attribution(hypothesis, *, weights=None, threshold=None) -> str | None`
  ——全部 F13 纯函数（无 IO、无设备）。
- `tests/unit/test_evidence_score.py`（新，19 用例）。
- `diagnosis/__init__.py`（改）：模块图补 `classifier.py` / `analyzer.py` /
  `evidence.py` 三条（5.2 时漏登记，本任务一并补上）。
- 权重**不新写字面量**：真值源仍是 `agents/policy_config.py::DIAGNOSIS_EVIDENCE_WEIGHTS`
  （M1 已建段），阈值取 `ATTRIBUTION_THRESHOLD`（初版 10）。policy.yaml 未改。

### 决策记录

1. **口径登记——「按证据类别求和」而不是「按条目数累加」**。设计 §8.3 的
   权重表是「每类一项」的形状（六类六个权重），按类求和是字面读法；更硬的理由
   是 `same_issue_multiple_tests: 3`——若按条目累加，一个影响 100 个用例的根因
   会拿 300 分，分数随「有多少用例撞上」无界增长，跨候选就再也比不了大小。
   去重求和让分数只回答「**有哪些类**证据」，与生产者循环了几次无关。
   探针 3 实测：改成按条目累加 → 1 red。
2. **排序键 = `(-score, statement)`，`confidence` 明确不进键**（plan 的核心
   钉子）。同分按 `statement` 定序而不是保持输入序——输入序会让结果依赖调用方
   怎么拼列表。探针 1 实测：把 `-confidence` 插进排序键 → 1 red。
3. **`suggested_attribution` 收「假设」而不是收「score」**：分数在函数内部现算。
   两处各算一次就可能用上不同的 weights，得到一个自相矛盾的结论；测试用
   `inspect.signature` 钉住签名里没有 `score` 参数。
4. **方向映射写成表（`_KIND_TO_ATTRIBUTION`），不写 if 链**。权重表是校准点，
   方向优先级不该是第二处需要同步维护的事实——`max(mapped, key=weight)` 让
   校准一次生效两处。探针 4 实测：方向写死 `APP_DEFECT` → 1 red。
5. **两道闸门缺一不可**（矩阵 #16 前半）：① 分数 ≥ 阈值；② 证据里有方向明确的
   类别。方向词取 P1 `ATTRIBUTIONS`：崩溃 → `APP_DEFECT`、漂移 →
   `AUTOMATION_DEFECT`（UI 变了，失效的是脚本定位）、5xx → `ENVIRONMENT_DEFECT`
   （设计 §8.2 原文明写）。`reproducible_3_times` / `same_issue_multiple_tests` /
   `llm_hypothesis_only` **不映射**——它们说的是「多值得追」，不是「谁的责任」。
6. **跨任务口径登记（#14 × #16，本任务实测发现）**：矩阵 #14 的 crash 场景规则
   只产一条 `crash_detected`（5 分）< 阈值 10 → `suggested_attribution` 为 `None`。
   这是**正确**的：规则出的是假设，要凑到阈值（例如再来一条
   `reproducible_3_times`，Task 5.4 的产物）才给方向。已写成显式用例
   `test_crash_needs_corroboration_before_it_is_suggested`。
   **待办（M5 Gate，Task 5.6）**：`verify_p3_m5.py` 的演示必须让崩溃场景
   **带上复现证据**（Task 5.4 的 `reproducible_3_times`）才断言「给出归因建议」
   ——只注入一次崩溃而期望 `suggested_attribution` 非空，会撞上这条阈值口径。
7. **口径登记——方向闸门在初版权重下不可达，它是为校准留的防御**。三类无方向
   证据合计 5+3+1=9 < 10，所以「分数够但方向不明」当前构造不出来。用例
   `test_direction_gate_needs_calibration_to_be_reachable` 把这个关系**钉住**：
   谁把阈值降到 ≤9 或把无方向权重调高，它会红并要求同时补一条可达用例（同一用例
   里已示范校准后（`reproducible_3_times: 10`）闸门真的挡住）。探针 6 实测：
   撤掉方向闸门 → 1 red。

### 实测

- 全量 pytest **1978 passed / 0 failed**（1959 + 19）。
- A/B 探针（六轮，逐个红、逐个还原 md5 `a2df0998…` 一致、`__pycache__` 已清）：
  - 排序键加 `-confidence` → **1 red**（本任务核心钉子）；
  - 阈值闸门失效（`< limit` → `< 1`）→ **3 red**；
  - 按条目累加（去重求和失效）→ **1 red**；
  - 方向写死 `APP_DEFECT` → **1 red**；
  - 未知类别静默（`unknown = []`）→ **1 red**（变成 `KeyError`，仍是 fail-loud
    的另一种形态，但报错位置从「点名类别」退化为裸 `KeyError`）；
  - 撤掉方向闸门（无映射时兜底 `crash_detected`）→ **1 red**。
- **教训（探针 1 首轮假绿）**：第一版
  `test_same_evidence_set_orders_identically_whatever_the_confidence` 用两组各自
  单调的 confidence（0.05/0.05/0.05 与 0.95/0.95/0.95），组内 confidence 是
  **常数序**、与 statement 序同向，把 confidence 塞进排序键的 mutant 照样全绿
  ——钉子测试自己没钉住。改成**同一列表内 confidence 与 statement 反序**
  （a:0.1 b:0.5 c:0.9 期望次序 a,b,c；再翻成 a:0.9 b:0.5 c:0.1 次序不变）
  才真正钉住。**这条对后续所有「排序键不该包含 X」的钉子测试都适用：变异必须
  能让被禁的字段在组内产生与期望序相反的扰动。**

## Task 4.5 完成记录（P3-18 Discovery Event + 启动环境校验，2026-10-11）

### 交付

- `agents/migrations/006_discovery_events.sql`（新）：设计 §11 字段 + `kind` 四类
  CHECK + `review_status` 三态 CHECK + **新增三列** `reviewer`/`reviewed_at`/
  `review_note`（F6 审核留痕）+ 两个索引。
- `candidates/discovery.py`（新）：`DISCOVERY_KINDS` / `DISCOVERY_REVIEW_STATUSES`
  词表、`DiscoveryEvent` 行模型、`graph_registration(event)` 与
  `register(graph_store, event, …)` ——后两个是 **F13 纯函数**（图库注入，函数本身
  不碰文件/设备）。
- `agents/storage.py`（改）：`save_discovery_event`（只写 `PENDING`，`review_status`
  **不是形参**）、`get_discovery_event`、`list_discovery_events`、
  `decide_discovery_event`（唯一写路径）、`_discovery_from_row`。
- `candidates/review.py`（改）：`_SUPPORTED_KINDS` 扩到 `("test", "discovery")`；
  `repo_root`/`repository`/`secrets` 改为**可选**（只有 test 分支消费），新增
  `graph_store` 参数与 `_decide_discovery` 分支 + `_graph_scope`。
- `graph/models.py`（改）：新增证据常量 `EVIDENCE_DISCOVERY = "discovery"`。
- `agents/tools.py`（改）：接线 `create_discovery_event`（`NOT_WIRED` 11 → 10）。
- `explorer/agent.py`（改）：`discovery_enabled` 构造开关（默认关）+ 主循环
  `_record_discovery` + `_known_screens`。
- `cli/main.py`（改）：新增 `_check_exploration_env`（sandbox-only + simulator）。
- 测试：`tests/unit/test_discovery_event.py`（新，30 用例）+ 5 处存量钉子的链尾
  更新（`test_agent_store` / `test_agent_tools` / `test_bug_store` /
  `test_candidate_store` / `test_candidate_review`）+ `test_repo_hygiene` 写语句
  下界 27 → **29**（实测重新数过）。

### 决策记录

1. **迁移编号 006**（plan 写 003）——偏离在 005 时已登记，此处兑现（003/004 已被
   M3 占用，迁移只许向后追加）。
2. **发现进 Source 面而不是 Runtime 面**（口径登记，本任务最关键的一条）：设计
   §7.5 说「纳入 Source/Runtime Graph 的『已知』集合」，字面读像是「两面都行」。
   取 **source**（声明面）的理由：`runtime` 面的语义是「trace 实际到达过」的
   **观测记录**（`graph/builder.py` 只认 `wait_screen` / `recovery_observed` 两类
   证据），而发现事件**没有走过用例**——它只是被探索撞见、被人确认存在。
   写进 runtime 面会让「观测」与「确认」混成一体，设计 12.2 的 diff
   （runtime vs source）立刻失去意义（`ADDED` 会变成「探索到了」而不是「用例
   到了」）。探针 1 实测：把 `SOURCE` 换成 `RUNTIME` → **4 red**。
   登记条目 `visit_count=0` / `count=0`（发现不是访问，也不是走过）。
3. **`register` 是「读出 → 合并 → 写回」，不是 `upsert_graph` 一张新图**：
   `GraphStore.upsert_graph` 按 `(app_id, app_build, source_of)` **整体替换**
   （P2 的设计：重跑 `graph build` 必须幂等）。直接 upsert 一张只含新条目的图会
   把该范围里**别处已确认的发现擦掉**——那是静默数据丢失。
4. **`repo_root`/`repository`/`secrets` 改可选，但必填性没有消失**：它们的消费者
   只有 test 分支；做成全局必填会逼 discovery 的调用方伪造一堆用不上的参数。
   按 kind 分流后在 `_validated_testcase` 里显式挡（新加 `if repo_root is None`，
   否则 `Path(None)` 会以 `TypeError` 漏成 traceback，而 CLI 只接域错误与
   `RuntimeError`）。新用例 `test_test_kind_accept_still_requires_its_inputs`
   钉住「改可选 ≠ 变静默」。
5. **discovery 分支的顺序与 test 分支相反**（改状态 → 登记图）：test 的副作用
   （git commit）**不可幂等重放**，所以先落盘后改状态；discovery 的副作用
   （`register`）**幂等**，先改状态才安全。两处顺序不同是**刻意**的，各自的
   理由写在 `_decide_discovery` 的 docstring 里。⚠️ 但**校验仍全部在改状态之前**
   （`graph_store is None` 与范围歧义都先抛）——与 test 分支同一条纪律。
6. **`review_status` 不是 `save_discovery_event` 的形参**（F6；与 `save_candidate` /
   `save_bug_candidate` 同款）：接受形参会开一条绕过 `decide_candidate` 全部领域
   校验的旁路。探针 4 实测：加上形参 → **1 red**。
7. **`_check_exploration_env` 与 `_check_autonomous_env` 并存，不合并**（plan 的
   「口径必守」）：公共前置只做 production 一票否决、消息写着「sandbox 或
   staging」（对 plan/generate 准确）；探索要 sandbox-only，**staging 也拒**。
   两层是**与**的关系，判据集合本就不同。新用例
   `test_the_two_layers_are_separate_and_both_apply` 钉住分工（本函数**不复制**
   总开关判据）。探针 3 实测：把 `is not SANDBOX` 换成 `is PRODUCTION` →
   **2 red**。
8. **`discovery_enabled` 默认关**：4.1–4.4 的单测探索是构造场景，默认打开会把
   测试噪声记进人工审核队列。`mta explore`（Task 4.6）显式打开。
9. **`event_id` 由调用方给（explorer 用 `stable_hash`，F8 同一原语）**：工具层
   不生成随机 id——它必须确定性（同一输入同一输出），否则「重跑同一次探索得到
   同一批发现」这个 upsert 幂等的前提就没了。
10. **发现是副产品，落库失败不中断探索**：失败如实写一条 `DISCOVERY_FAILED`
    trace，主循环照常收尾。用例如实钉住这条（探针 5 之外的单测）。

### 实测

- 全量 pytest **2007 passed / 0 failed**（1978 + 30 新 − 1 条被改写）。
- A/B 探针（六轮，逐个红、逐个还原 md5 一致、`__pycache__` 已清）：
  - 登记进 runtime 面 → **4 red**（口径 #2）；
  - REJECT 也登记图 → **1 red**（`UnboundLocalError`，仍是红）；
  - 环境闸放行 staging → **2 red**（plan 的口径必守）；
  - `review_status` 变 save 形参 → **1 red**（F6 旁路）；
  - 已知屏也记发现 → **1 red**；
  - 登记不再幂等 → **1 red**（`IntegrityError`，仍是红）。
- ⚠️ **教训（探针 3 的还原写坏了，当场发现）**：该探针把 `_check_exploration_env`
  的判据改成了 `is EnvKind.PRODUCTION`，而**还原时按字符串替换命中了
  `_check_autonomous_env` 里那行**（两处文本在那一刻完全相同）——于是
  `_check_autonomous_env` 被静默改成 sandbox-only（一条**真实的**行为回归，不是
  探针残留）。靠 `git diff` 里出现了 `-    if kind is EnvKind.PRODUCTION:` 这行
  **删除**才发现（本任务预期 diff 是纯新增）。修法：**还原时用带消息文案的锚点**
  （两条消息文本不同），并复核 `git diff --stat` 只有插入没有删除。
  **后续探针纪律**：还原后除了 md5，还要看 `git diff` 的**删除行数**。
- 存量钉子的链尾更新（预期内的「迁移链变长」涟漪）：`test_agent_store`
  （`discovery_events` 现在存在了）、`test_agent_tools`（12 接线 / 10 未接线）、
  `test_bug_store`（改 `>=` 以免每加一个脚本就红）、`test_candidate_store`
  （== `006_discovery_events`）、`test_candidate_review`（`discovery` 现在是
  受支持 kind → 换用 `bug` 作未知 kind 的例子）。
- `test_repo_hygiene` 的 `_MIN_WRITE_STATEMENTS` 27 → **29**（用守卫自己的判据
  重新数：experience 13 + agents 10 + graph 6）。

## Task 5.4 完成记录（P3-23 Reproducibility Engine，2026-10-11）

### 交付

| 文件 | 变化 |
|---|---|
| `diagnosis/reproducer.py` | **新增**（`StepProbe` / `ReproResult` / `reproduce` / `MAX_REPRO_ATTEMPTS=3`） |
| `graph/models.py` | +10：`TraceStep` 追加 `failure_phase` / `effective_idempotency` / `effective_risk` 三字段（末尾，带默认值） |
| `graph/builder.py` | +8/−1：`read_trace_steps` 的 SELECT 与行构造带上这三列 |
| `diagnosis/__init__.py` | 模块图登记 `reproducer.py`（「复现 / 去重随各自任务落地」→ 只留去重） |
| `tests/unit/test_reproducer.py` | **新增** 34 例 |
| `tests/fault_injection/test_p3_matrix_m5.py` | **新增** 7 例（矩阵 #15） |

### 口径登记（五条，都写进了模块 docstring）

1. **风险闸是本引擎自己的，不在 `retry_decision` 里**。设计 §8.4 第一行的条件是
   `idempotent` **且** `effective_risk == LOW`；而 `retry_decision(phase,
   idempotency, has_postcondition)` **没有 risk 形参**——所以「POST_DISPATCH +
   幂等 + 非 LOW」这一格在 P1 本体里是 `allowed=True`。本引擎**再收一道**：不自动
   重跑，记「无法确认」。理由：「幂等」说的是重复不改变结果，不是重复没有代价
   （`pay` 幂等与否是**声明**，不是我们能验证的事实），而 `effective_risk` 是 P1
   对「这一步有多危险」的确定性推导（F1 的同一个函数）。自主**重复**一个 HIGH
   动作，与探索侧「LOW-only 才自动执行」（`explorer/policy.py::pre_judge`）是同
   一道闸。**与 Guard 不重叠**：重跑走的仍是同一条 Guard 通路（F12），执行层面
   HIGH 依然由 P1 Guard 判；本闸管的是「要不要主动再来一次」这个选择权。缺 risk
   时**按非 LOW 处理**（fail-closed，与 `experience/runtime_guard.py` 的
   「`None` 按最高风险处理」同款）。
2. **缺字段一律往「不重发」的方向兜（fail-closed）**。`steps` 表可能缺
   `failure_phase` / `effective_idempotency`（旧 run，或失败发生在 find 之前）。
   替换规则：缺幂等性 → `Idempotency.UNKNOWN`（7.4-3：「UNKNOWN 一律按
   NON_IDEMPOTENT 处理」——「我们不知道」正是 UNKNOWN 的语义）；缺阶段 →
   `FailurePhase.POST_DISPATCH`（H7 是硬约束，不知道动作发没发出时**假定已
   发出**才是不重发的方向）。
   > ⚠️ **这是本任务自我纠正的一处**：初版写的是「缺 phase → 当 PRE_DISPATCH
   > （动作未发出，重跑安全）」——那是 **fail-open** 的。它假定「动作从未发出」
   > 从而放行重跑，而真正的最坏情形（非幂等动作其实已经发出、结果未知）恰好
   > 会因此被重发一次。H7 的保守方向是「不重发」，缺字段不是放宽它的理由。
   > 替换后**照常走本体**（不新增分支 / mode），`assumed` 字段一路带进 `audit`
   > ——审计要能区分「真判据」与「保守替换」（探针 9/10 各钉住一侧）。
3. **`reproducible` 是三态，「不重跑」≠「复现不了」**。三类记 `None`：主动不重跑
   （H7 / 风险闸）、postcondition 不成立或无法判断、一次都没跑成。把它记成
   `False` 是**过度声称**——`bug_candidates.reproducible` 是三态列（迁移 005），
   `None` 正是为「不知道」留的。
4. **`llm_budget.try_acquire()` 是重跑的许可费**，与重跑内部自身的扣减是**两笔**
   ——本模块看不到内层计数，只保证「没有名额就不发起重跑」。本模块**不**调
   `record_success/record_failure`：熔断的连续失败计数归恢复流程（10.5「一次 LLM
   恢复尝试未以 RECOVERED 收尾」），复现引擎不知道重跑内部每次调用的收尾，代它
   记分只会污染熔断判据。
5. **`read_trace_steps` 补读三列**（本任务新增的**读取端**改动）。这三列 P1 起就
   在 `steps` 表里、`Lifecycle.record_step` 一直在写，只是读取端早先没 select
   ——**不补这一条，`reproduce` 对任何真 trace 都落进「缺字段 → 按 PRE_DISPATCH
   保守处理」，POST_DISPATCH 的两条分支在真实数据上永不可达**（单测的 `StepProbe`
   会掩盖这件事，故矩阵文件刻意走真 `TraceStore`）。与 Task 2.2 / 5.1 的两次追加
   同款处置：**追加而非重排**、都带默认值、放在末尾。
6. **`run_testcase` 目前 `NOT_WIRED`**（归 M6 的驱动设备工具循环，Task 3.4 决定 1）
   → 缺省通路必然得到「通道不可用」。故 `ToolError`（未接线 / 依赖没注入）**上抛**
   并转成 `_RerunChannelUnavailable`，中止重跑并记「无法确认」——若当普通异常吞掉，
   一次都没跑成的调用会得到 `reproducible=False`，**「没接线」伪装成「跑不出来」**。
   生产装配由 `mta diagnose`（Task 5.6）注入 `runner` 回调；toolkit 通路在 M6 接线后
   自动生效，本模块无需改动。

### 两处与 plan 不同的判断（都写进了代码注释）

1. **证据 `reproducible_3_times` 只在「每次重跑都复现」时给**。类别名就是它的字面
   意思（设计 §8.3 权重表，5 分）。判据取「没有观察到任何一次通过」而不是
   「`attempts == MAX_REPRO_ATTEMPTS`」——后者会让一个**与证据无关的配置项**
   （预算把上界掐到 2）静默清零最强的一档证据。`>= 2` 次是因为一次重跑只是一个
   数据点，构不成「复现模式」。flaky（有通过）仍记 `reproducible=True` +
   `reproduction_count=<失败次数>`，但**不领**这份证据（另有 note 说明）。
2. **跑满上界，不提前收工**。设计 §8.4 只说「最多重跑 3 次」，没说「一旦通过就停」。
   提前收工会让「fail/pass/fail」这种 flaky 形态与稳定复现在结果上**完全一样**
   （都是 `count=1` 的干净复现）——而区分这两者正是复现引擎存在的理由。

### 实测

- 全量 `pytest tests`（macOS）：**2048 passed / 0 failed**（exit 0；父提交 2007，
  +41 = 34 单测 + 7 矩阵）。
- A/B 探针（十轮，逐个红、逐个还原 md5 一致、`__pycache__` 已清）：
  | # | 变异 | 红数 |
  |---|---|---|
  | 1 | 去掉风险闸（POST_DISPATCH 幂等一律重跑） | **3** |
  | 2 | 阶段判定改成幂等性驱动（不调 `retry_decision`） | **4** |
  | 3 | postcondition 不成立也记 `False` | **5** |
  | 4 | `no_retry` / 风险闸记成「复现不了」 | **3** |
  | 5 | 一次通过就提前收工 | **2** |
  | 6 | flaky 也领 `reproducible_3_times` | **3** |
  | 7 | `read_trace_steps` 不读三列 | **7** |
  | 8 | 通道不可用当普通异常吞掉 | **1** |
  | 9 | 缺字段当 PRE_DISPATCH（fail-open 回归） | **4** |
  | 10 | `assumed` 不进审计 | **3** |
- 探针 2 的变异写法（手搓 `RetryDecision` 顶掉本体调用）刻意做成「幂等性驱动」，
  这正是 plan 修正 #9 要防的形态：**它同时红了 `test_the_plan_is_a_decision_not_a_
  re_derivation` 与矩阵的 PRE_DISPATCH 两行**——判据不是「结果看起来一致」。
- 探针 4 第一次跑时**没有红**（5 红全是探针 3 的残留）——`cp` 还原后立刻确认
  `md5` 与锚点计数（anchor count: 1），重做一次才拿到真红数 3。教训沿用 Task 4.5：
  **还原后先验状态再下结论**。
- 存量钉子的链尾更新：无（本任务不新增迁移，`AGENT_SCHEMA_VERSION` 不变）。
  `graph/models.TraceStep` 的字段追加是纯新增，无既有测试断言其字段全集。
- **两处自我纠正**（都由「写完之后自己再读一遍判据」发现，不是测试发现的）：
  ① 证据 `reproducible_3_times` 的判据从「跑满 3 次」改成「没有观察到任何一次
  通过」（见上）；② 缺字段的保守方向从 PRE_DISPATCH 改成 POST_DISPATCH
  （fail-open → fail-closed，见口径登记 2）。两者都补了探针（6 / 9）。
- **边界登记**：`SECURITY_BLOCKED` + LOW 风险（`blocked_targets` 命中一个低风险
  元素）的计划是 `rerun`——本引擎刻意不读 `failure_type`（只许一个判定来源），
  该重跑**无害**（Guard 确定性，会在动作发出之前再拦一次），且如实计为「确定性
  复现」。`test_a_low_risk_guard_block_reruns_harmlessly_and_is_documented` 把
  这个形态钉在明处，免得将来被误读成漏判。

## Task 5.5 完成记录（P3-24 Bug 去重，2026-10-11）

### 交付

| 文件 | 变化 |
|---|---|
| `diagnosis/deduplicator.py` | **新增**（`dedup_key` / `diff_tokens` / `merge_into` / `find_duplicate` / `DEDUP_PREFIX`） |
| `diagnosis/models.py` | +12：`BugCandidateDraft` 追加去重三元组 `screen` / `runtime_diff` / `source_change`（末尾，带默认值） |
| `diagnosis/__init__.py` | 模块图登记 `deduplicator.py`（「去重随 Task 5.5 落地」删除） |
| `tests/unit/test_bug_dedup.py` | **新增** 28 例 |

### 口径登记（三条）

1. **`runtime_diff` 分量必须排序**。`GraphDiff.entries` 的顺序由图的遍历顺序决定，
   而遍历顺序**不是**语义的一部分——不排序的话「同 runtime diff」会退化成「同
   diff 且恰好同序」，去重**静默**失效（库里 key 各不相同，看起来像「本来就没有
   重复」）。分量同时**去重**并**丢掉 `UNKNOWN`**（`GraphDiff.changed` 的判据就是
   「有非 UNKNOWN 的差异」；UNKNOWN 是「没判定」，把它算进去会让「这次没给 base
   build」变成另一条 Bug）。
2. **`source_change` 进 key ⇒ 同一症状在不同 build 上是两条 Bug**。设计 §8.5 明文
   要求它进 key，语义也对（build 变了意味着源码变了，可能是「还没修」与「修了但
   没修干净」两件事）。代价是跨 build 的长期跟踪需人工合（库里没有跨 build 的关联
   列）——登记，不假装完整。
3. **⚠️ 空 diff 分量无法表达「没跑过 diff」**（**探针 9 实测发现**）。
   `BugCandidateDraft.runtime_diff` 是 `list[str]`（pydantic 拒绝 `None`，默认
   `[]`），`diff_tokens(None)` 也返回 `[]`——「跑了 diff 没有差异」与「压根没跑
   diff」在本层得到同一个 key。**本层拿不到那个事实**（是调用方的知识），故登记给
   Task 5.6 的 `mta diagnose`：**没有 `--base-build` 时不应落 `dedup_key`**（留
   `NULL`），而不是落一个会误合的空分量 key。

### 为什么 `failure_type` 不进 key（本任务的核心判据）

设计 §8.5 的三元组是 **screen + runtime diff + source change**——`failure_type`
**刻意不在其中**。同一个根因在三条用例上常常表现为三种不同的失败
（`ELEMENT_NOT_FOUND` / `WAIT_TIMEOUT` / 断言炸），按失败类型去重会把它们记成
三个 Bug，而它们其实是一个。矩阵 #16 后半（「三个不同 testcase 失败但同一根因 →
合并为一个 Bug + `affected_tests` 累加」）正是这条的落点，
`test_the_key_ignores_failure_type` 与 `test_three_failing_testcases_become_one_bug_with_accumulated_tests`
各钉一侧（后者走真 `SQLiteAgentStore`）。

### 合并规则（`merge_into`，纯函数）

只增不减：`affected_tests` 保序并集、`reproduction_count` / `evidence_score` 取
max、`reproducible` 三态**单调**（`True` 支配 `False` 支配 `None`——一次跑不出来
不能抹掉已确认的复现）、`severity` 取更强、`title` / `attribution` 保留先到者。
返回的是 `save_bug_candidate(**…)` 的**实参形状**（不含 `status`——`save_bug_candidate`
根本不收它，状态推进的唯一入口是 Task 5.6 的状态机）；有一条测试用
`inspect.signature` 断言这个形状，防止「合并产物多带一个键 → 调用方 TypeError」。

### 实测

- 全量 `pytest tests`（macOS）：**2076 passed / 0 failed**（exit 0；父提交 2048，
  +28）。
- A/B 探针（十一轮，逐个红/绿、逐个还原 md5 一致、`__pycache__` 已清）：
  | # | 变异 | 红数 |
  |---|---|---|
  | 1 | `failure_type` 进 key | **2** |
  | 2 | diff 分量不排序 | **2** |
  | 3 | `UNKNOWN` 不丢 | **1** |
  | 4 | `affected_tests` 取先到者（不合并） | **2** |
  | 5 | `reproducible` 取先到者（非单调） | **1** |
  | 6 | `find_duplicate` 取最新（不是最早） | **1** |
  | 7 | `find_duplicate` 过滤掉终态 | **1** |
  | 8 | hash 原语换成本地 sha256 | **1** |
  | 9 | 空 diff 换成哨兵值 | **0**（**诚实记录**，见下） |
  | 10 | `severity` 取先到者（不取更强） | **1** |
  | 11 | `title` 被后到者覆写 | **1** |
- **探针 9 全绿是一次诚实的发现，不是漏网**：初版
  `test_an_empty_diff_is_a_valid_component` 的 docstring 声称「空与缺 diff 必须
  区分」，但 `runtime_diff` 在模型层**不可能**是 `None`（pydantic 拒绝），所以那个
  区分在本层根本不存在。修法不是补一条假测试，而是**把声称改成事实**：该测试改述
  为「空分量是稳定的、且与有差异不同」，并把「无法表达『没跑过 diff』」作为口径
  登记三交给调用方（Task 5.6）。**教训**：探针全绿有两种可能——测试没钉住实现，
  或者**声称本身是错的**；后者只能靠回头读声称发现（与 Task 5.4 的两处自我纠正同
  源）。

## Task 4.6 完成记录（P3-19 `mta explore` CLI + M4 Gate，2026-10-11）

### 交付

| 文件 | 变化 |
|---|---|
| `cli/main.py` | +426/-3：`explore` 子命令（注册 + `cmd_explore` + `_explore_action_for` + `_runtime_graph_for` + `_explore_audit`）；`cmd_run` 的 `bi_fields` 改 `update`（见下「顺带修掉的三处存量缺陷」） |
| `phase0/verify_p3_m4.py` | **新增**（591 行）：M4 Gate，离线半边 + 真机半边 |
| `tests/unit/test_cli_explore.py` | **新增** 31 例 |
| `tests/unit/test_cli_run.py` | +66：`test_real_device_run_records_app_build_scope` |

### 口径登记（四条）

1. **`explore` 在 staging 也拒，且是两层前置**（口径必守 review_p3_task12 §4）。
   `_check_autonomous_env`（公共：总开关 + production 一票否决）与
   `_check_exploration_env`（探索专属：sandbox-only + simulator）**依次调用、互不替代**
   ——公共前置的文案写「请在 sandbox 或 staging 下运行」，那句对 `plan`/`generate`
   准确、对 `explore` 不准确。有一条 AST 测试钉住两者的**存在与先后**
   （`test_the_two_preflight_layers_are_both_called_in_order`），而不是只断言拒绝文案
   （文案对了、调用删了，测试仍会绿——这是本仓记录在案的空转型守卫）。
2. **退出码契约**：0 = 探索结束（**含 `*_BUDGET_EXCEEDED` 与 `LOOP_DETECTED`**——
   F11/矩阵 #11/#12 的口径是「预算耗尽与循环是**结论**不是失败」，与矩阵 #6 的空
   Plan 同款）；3 = 前置错误（env / 政策 / 输入读不到 / 预算文件坏 / 设备装配失败 /
   App 起不来）。`LOOP_DETECTED` 与预算超限**不**产生 exit≠0。
3. **`--build` 缺省**不沿用 `_resolve_app_build` 的静默兜底。那条函数读不到 metadata
   时退回 `"local"`——对 `mta run` 无害（build 只是 E7 集合的一个元素），对 `explore`
   是**fail-open**：build 与声明面错位 → `load_graph` 查不到那一面 → frontier 恒空 →
   **零发现，而输出长得像「真的探完了」**。故 `cmd_explore` 自己读 metadata，
   读不到就 exit 3。探针 P7 钉住这条（把兜底加回去 → 1 red）。
4. **`_explore_action_for` 复用 `graph/impact.py::trigger_element`**，不手写第二份
   `<step_type>:<target_id>` 解析；`wait_for` / `assert_*` / `launch_app` / 无前缀
   trigger 一律返回 `None`（decider 的**跳过**语义，不是结束——「再 wait 一次」不驱动
   App 走任何新路）。

### ⚠️ 真机半边一开始是**全绿假象**：`FRONTIER_EMPTY steps=0`

首轮 Gate 的 G3b/G4/G5 全 FAIL，输出一致是
`outcome=FRONTIER_EMPTY steps=0 screens=1 discoveries=0`，而 G3c 从库里读回了播种的
声明面（`screens=['HomeView'] transitions=1`）——**声明面在、frontier 却是空的**。

逐层实测（`page_source` 原样打印）定位到根因**不在探索逻辑，而在起点**：

- 冷模拟器上 `activate_app` 之前 `page_source` 是 **springboard**（`bundleId=
  com.apple.springboard`，App 根本没起）→ `current_screen` 恒
  `CURRENT_SCREEN_UNKNOWN` / `screen=None` → `select_frontier(current_screen="")`
  匹配不到任何 `from_screen` → frontier 空 → 立刻 `StopDecision(FRONTIER_EMPTY)`。
- **只加 activate 还不够**：LoginDemo 冷启动落在 **LoginView**，而 Gate 的声明面
  （以及多数真实工程）登记的是登录后的屏 → 仍是 UNKNOWN。Gate 因此改用**生产路径**
  把 App 开到起点：跑一条 gate 自己的用例（terminate → launch → 填表 → 登录 →
  `wait_for screen:HomeView`）经 `mta run`，副产品是 `trace.db` 有了真实观测面。

修法两条：① `cmd_explore` 增加「把 App 带到前台」一步（无参 `AppSession.launch()`
走 `activate_app`，幂等——已在前台时不会重置调用方手工导航到的状态）；② Gate 用
`mta run` 建立起点。**这一步此前没有任何测试覆盖**：探针 P4（删掉 `app.launch()`）
1 red，补的正是 `test_explore_brings_the_app_to_the_front`。

这条是「配置错误伪装成功能退化」的又一例（review_p3_task12 P3-4 的同族）：**探索
一步没走，输出与「探完了」逐字相同**。真机 Gate 的价值就在这里——三个单测层
（frontier / loop / guard）全绿，而端到端一步都走不动。

### 顺带修掉的第一、二处**存量**缺陷（都是 Gate 现场暴露的）

1. **`runs.app_build` 在真机路径上被覆盖成 NULL**。`cmd_run` 的 Build Identity 段用
   `bi_fields = {...}` **整体赋值**，把上面刚写进去的 `app_build` 覆盖掉。同段注释
   声称该列「此前从未被写入（P1 遗留）……没有它所有真实 run 的图都会落进同一个空
   scope」——**注释说已修，代码没修**，而缺陷只在真机路径现形（`--fake-driver` 分支
   不走那段，所以既有的 `test_run_records_app_build_scope` 是**假绿**）。
   后果不止「少一列」：同库混进 `NULL` 与 `'local'` 两个范围 →
   `read_trace_steps` **必然 fail-loud** → `mta explore` 直接 exit 3。Gate 现场即
   此形态（explore 行 `'local'`、run 行 NULL）。修 `update`；补
   `test_real_device_run_records_app_build_scope`（探针 P8 → 1 red）。
2. **`discoveries=` 与 `discovery:` 行统计的是整库，不是本次任务**。
   `list_discovery_events()` 无过滤地返回全部历史事件 → 同一 agent.db 上重跑会打印
   **上一轮**的发现。Gate 现场：G4/G5 各自打印了 G3 那条 `disc_0731f877…`，而它们
   各自的探索根本没撞见新屏。审计输出必须是「这一轮做了什么」，不是「这个库里有什么」。
   按 `exploration_task_id` 收窄；补 `test_explore_reports_only_its_own_discoveries`
   （探针 P6 → 1 red）。

### 顺带修掉的第三处存量缺陷：成功路径的 `runs` 行没收口

`cmd_explore` 只在**异常**路径 `end_run(status="ABORTED")`，成功路径直接 return——
`runs` 里留下 `status='RUNNING'` / `end_time=NULL`。而 `TraceStore.__init__` 会把
**所有** RUNNING 行扫成 `FAIL`（那是给「进程异常中断」的兜底），于是**一次成功的
探索在库里显示为 FAIL**。M4 Gate 现场实测：同库的 explore 行全是
`status='FAIL' exit_code=None`。

这与 `cmd_generate` 的 review_p3_task35 P3-8 是**同一个缺陷**——那条修在 `generate`
上，`explore` 写的时候又漏了一遍（「已修过的模式会被复制」的又一例，方向相反：
这次是「已修过的缺陷被漏掉」）。终态词落 `PASS`：`*_BUDGET_EXCEEDED` /
`LOOP_DETECTED` 是 F11/矩阵 #11/#12 的**正常收束**，落 FAIL 会让「预算生效」在
报表上长得像「探索挂了」。补两条测试（成功路径 + 预算停止路径各一），探针 P9 → 2 red。

### Gate 自身的第三处修正：无设备时不许以 traceback 收场

`_scaffold` 在设备半边**之前**跑，而它要 `read_app_identity`（simctl 找 booted
设备）来写 metadata 的 build/git_commit。没有设备时那个异常直接冒出去——**离线
半边（G1 的三种 F7 拒绝）与 G7 全量回归都不跑**，而它们恰恰是任何机器上都该跑的
那部分。2026-10-11 实测（`MTA_SIM_UDID` 指一个不存在的设备）：Gate 以一个
`BuildIdentityError` traceback 收场，**一条判据都没报**。

这与本文件反复记的「守卫空转」同族，只是形态相反：不是假绿，是**假红到没有输出**。
修法两条：① 身份读不到就写占位 metadata（设备半边随后按 `pre_ok` 跳过）；
② 设备半边跳过时**显式报一条 FAIL 且 `ok=False`**——没有设备的 `exit 0` 会是一句
「M4 Gate 通过」的假话，但跳过**不阻断**离线半边与 G7。实测：无设备时
`exit 1 (device_half=SKIPPED_ENV)`，G1 三条与 G7 照常给出结论。

### 实测

- 全量 `pytest tests`（macOS）：**2108 passed / 0 failed**（exit 0；父提交 2094，
  +14：`test_cli_explore.py` 31 例 + `test_cli_run.py` 1 条）。
- `phase0/verify_p3_m4.py` **连续三轮 exit 0**（device_half=PASS；第三轮在
  两段 review 的修正之后重跑，逐项仍全 PASS），逐项：
  | 判据 | 结果 |
  |---|---|
  | PF_* 模拟器/Appium/App | PASS |
  | G1 production / staging / real_device 启动即拒 | PASS ×3（三者均 `wrote_db=False`，即**在写任何库之前**） |
  | G2 `max_steps:0` → `STEPS_BUDGET_EXCEEDED` exit 0 | PASS |
  | G2 坏预算文件 → exit 3 且不建库 | PASS |
  | G3 撞见 ProfileView → PENDING 事件；图不变 | PASS（`events=[('NEW_SCREEN','ProfileView')]` + `review=['PENDING']`，`screens=['HomeView']`） |
  | G4 HIGH 目标拦截：3 次 BLOCK、0 次执行、屏幕仍是 HomeView | PASS |
  | G5 `max_repeated_state:2` → `LOOP_DETECTED` exit 0 | PASS |
  | G6 gate 工程 git 干净 + 主仓库资产逐字节未变 | PASS |
  | G7 全量回归 | PASS |
- A/B 探针（十六轮，逐个红、逐个还原 md5 一致、`__pycache__` 已清）：

  | # | 变异 | 红数 |
  |---|---|---|
  | 1 | 删掉 `_check_exploration_env` 调用（只剩公共前置） | **3** |
  | 2 | `--budget` 文件被忽略（恒取 policy 段） | **3** |
  | 3 | `_explore_action_for` 永不补 `Screen.` 限定名 | **0 → 补测后 1** |
  | 4 | 删掉「把 App 带到前台」那一步 | **1** |
  | 5 | 不写 `agent_tasks` 任务行 | **10** |
  | 6 | 输出不过滤 `exploration_task_id` | **1** |
  | 7 | `--build` 缺省改回静默兜底 `"local"` | **1** |
  | 8 | `bi_fields` 改回整体赋值（真机路径丢 `app_build`） | **1** |
  | 9 | 成功路径不写 `end_run`（run 行留 RUNNING → 被扫成 FAIL） | **2** |
  | 10 | 删掉 `_budget_problem` 前置校验（`max_repeated_state:0` 跑到一半才炸） | **1** |
  | 11 | `_trace_has_runs` 移出 try（坏 trace 文件冒 traceback） | **1** |
  | 12 | 删掉陈旧 trace 范围对齐（`trace_build != app_build`） | **1** |
  | 13 | 删掉异常路径的 `EXPLORATION_FAILED` 收口（任务行停 `EXECUTING`） | **1** |
  | 14 | `agent_db` 提前到 `app_id` 校验之前（纯前置错误也建库） | **1** |
  | 15 | 删掉「App 起不来」路径的 `stack.close()`（泄漏 Appium 会话） | **1** |
  | 16 | 删掉 `_explore_action_for` 的空 `from_screen` 兜底 | **1** |

- **探针 3 全绿是一次诚实的覆盖缺口**（不是「实现正确所以没红」）：`_explore_action_for`
  的「短名补限定名」分支（`if "." not in target`）此前一行都没被覆盖，22 条测试里
  没有任何一条走短名 trigger。修法是补 `test_action_for_qualifies_a_bare_element_id_with_its_screen`
  （短名 → 限定名；已限定 → 原样透传），补后探针 3 转红。**教训与 Task 5.5 探针 9
  同源**：探针全绿的两种成因——测试没钉住实现，或**声称本身是错的**——这次是前者，
  且只在真机/跨屏同名场景才现形（`resolve` 的短名唯一性要求），属「单测构造不出的
  输入形状」。
- 现场卫生：十六轮探针 + 三轮 Gate 之后 `git status --porcelain` 只剩预期的
  `M cli/main.py` / `M tests/unit/test_cli_run.py` 与三个新文件；主仓库
  `repository/overrides`、`repository/generated`、`suites` 的 md5 快照逐字节未变
  （G6b）。

### 对抗性评审补的六个收口（2026-10-11，第一段 review）

评审对 `cli/main.py` 的**未提交 diff** 做对抗性复核，报了 8 条；逐条在真码上
复现后**采纳 6 条**（另 2 条经复核为误报，见下）。六条的共同形态是
**「失败路径上的收口不完整」**——正常路径完全看不出来，全部只在异常/前置
错误时现形，故前面 9 轮探针（都是正向路径）一条都没覆盖到。

| # | 缺陷 | 修法 | 探针 |
|---|---|---|---|
| 1 | `Explorer.run()` 中途抛错 → `agent_tasks` 永远停在 `EXECUTING`（`run()` 开头置位、只在正常返回写 `IDLE`）；审计根上挂一个永久「还在跑」的僵尸任务 | `except` 分支补 `update_task_state(..., "EXPLORATION_FAILED")`（与两条前置失败路径同款收口） | P13 |
| 2 | `_trace_has_runs(args.db)` **裸奔在 try 外**——它按契约不吞 `DatabaseError`，「文件不是 sqlite 库」会一路冒成 traceback 而非 exit 3 | 把探测移进那个 `(ValueError, sqlite3.Error)` 的 try（`cmd_generate` 本来就是两个调用同一个 try） | P11 |
| 3 | 预算里**模型层收不下**的取值没在启动期挡：`max_repeated_state: 0` 过 `ge=0` 校验，直到 `is_loop` 在探索第一步后才抛——先建库/建任务/起设备/拉 App，再以「探索失败」收场 | 新增 `_budget_problem()`：按 `is_loop` 的**同一口径**（阈值必须为正）在写任何东西之前 exit 3；解析也一并提到 `_load_repository` 之前 | P10 |
| 4 | 纯前置错误也留下新 `agent.db`——`SQLiteAgentStore.__init__` 会建库+跑迁移，而它排在 `app_id` 校验之前 | `SQLiteAgentStore(...)` 推迟到 `create_task` 之前（「失败的读不该产生新文件」纪律） | P14 |
| 5 | trace 里只有**一个陈旧范围**（`build=v2`）而本次 `--build local` 时不报错：`read_trace_steps` 的 fail-loud 只管「同库混多范围」；此时运行时图的 key 全来自旧 build，`select_frontier` 把**已走过的边**当成没走过 | 前置显式对齐 `trace_build != app_build` → exit 3（不静默挑一个） | P12 |
| 6 | 「App 起不来」路径早返回**不 `stack.close()`**——`wire_device_stack` 已 `connect()` 成功，Appium 会话被泄漏（P2-3 那个泄漏在被抬高一层处重犯）；且该路径 `finally` 覆盖不到 | 早返回前显式 `stack.close()` | P15 |

另补一条**契约兜底**（评审第 8 条，当前不可达）：`_explore_action_for` 在
`from_screen` 为空时会产出 `".elem"` → `Repository.resolve` 拆成
screen="" + elem → `AgentToolkit.call` 对 `ValueError` 子类**故意原样重抛**
→ 从 `Explorer.run()` 冒出去打死整场探索。`ScreenTransition.__post_init__`
拒绝空 `from_screen`，故当前构造器产不出这种值；按「纯函数不制造自己都知道
解析不了的值」返回 `None`，并用桩对象喂入补测。

**两条误报（复核后不改）**：

- 「`runtime_graph` 由**全部** build 的 trace 建成、与 `(app_id, app_build)`
  限定的 source 面错配」——机制描述不对：`read_trace_steps(app_build=None)`
  在库里混多范围时**已经** fail-loud（`scopes>1` 直接抛）。真正的缺口是
  「库里只有一个**别的** build」，已按上表第 5 条修（比原报更窄也更准）。
- 「build 对齐的注释描述了代码没做的事」——注释说的是「**声明面**（graph）
  的范围由 build 决定」，那部分代码是有的（`load_graph(app_id, app_build)`）；
  观测面的对齐是这次新加的第 5 条，两件事不要混。

评审同时确认**零发现**的类别（F1/F6/F7/F11 的 steps·duration·llm 三维/F12/
F13/E12，以及 `cmd_run` 的 `bi_fields` 修）逐条核过——特别是 F7 的两层前置
在 `1527/1529`（任何 DB/GraphStore 写之前）与「discoveries 收窄到本次任务」
都已落地。

### 第二段 review（M4 Gate + 测试的**假绿**专查）与随之修的三处 Gate 缺陷

第二段 review 只问一件事：**有没有假绿**。结论是**没有**——当前版本的每条
承重行为被删/被反之后，都至少有一条测试或 Gate 判据变红（它在 `/tmp` 的
副本上做了变异，**没有改动本仓**）：删 `_check_exploration_env`、把两处 F7
调用硬编码成 `EnvKind.SANDBOX`（docstring 声称「有守卫钉这条」——**证实有**）、
`select_frontier → []`（4 条红，防「空转型假绿」的钉子真在干活）、删探索侧
HIGH 拦截、删 5b 激活、删 discovery 输出收窄、删 run 行 `end_run(PASS)`、
回退 `bi_fields.update`、以及**本次新加的 6 条守卫**——逐一neutered 后各自
变红。Gate 的 G3/G4/G5 也各给了一条「怎么改能让它红」的构造，均成立。

它同时报了三处 **Gate 自身的**缺陷（都不是假绿，是「判据/注释宽于实现」），
已修：

1. **`_seed_source_face` 的证据词与注释打架**：docstring 说播种形态与
   `candidates/discovery.register` 的库形态「**逐列一致**（`evidence=discovery`）」，
   代码却写 `evidence=("source_declared",)`——恰好是它点名的那个 token 不一致。
   改为 `EVIDENCE_DISCOVERY`（`graph/models.py` 特意把「人工确认的发现」与
   「源码声明」分开记，混用会让人以为这条边是源码声明的）。
2. **`_seed_source_face` / `_device_home` 硬编码 `app_build="local"`**，而
   `_scaffold` 已改成**从装好的 App 读 build**。今天 App 的 `MTA_BUILD_ID` 就是
   `local` 故碰巧通过；若哪天不是，声明面会落在 `(BUNDLE,"local")` 而
   `cmd_explore` 用 metadata 的 build → `load_graph` 查不到那一面 → frontier
   恒空 → **G3b 假红**（响亮，不是假绿）。改为全程用 `g["build"]`，消除这个
   潜伏耦合。
3. **G3b 没断言 `review_status == "PENDING"`**，而 Gate docstring 把它列为判据。
   补上（无代码路径会写非 PENDING，但判据不该弱于声称）。

另两条**不改**：`test_the_two_preflight_layers_are_both_called_in_order` 是
AST-only（钉「都在且公共在前」，看不到 env_kind 来源）——review 确认
production/staging 两条测试独立覆盖了「硬编码 sandbox」的变异，故够用；
G6a 只看 **gate 工程**的 `git status`、G6b 只 md5 三个资产目录（主仓整体
`git status` 不查）——这是有意的收窄（Gate 要保护的正是那三处资产），
本次收口后主仓状态也逐字复核过。

---

## Task 5.6 / P3-25 — bug 状态机 + `mta diagnose` + M5 Gate 收口

**HEAD**：`2fb0215`（M4 checkpoint 之后）｜**基线**：2135 passed → **收口 2147 passed**
｜**Gate**：`phase0/verify_p3_m5.py`（G1–G10）

### 5.6.1 先做了两处真缺陷的修复（都是 M5 Gate 实测逼出来的）

这一轮最重要的发现：**离线单测全绿，真机上一条漂移都诊不出来**。两个缺陷都
属于「看起来正常」那一类——代码与单测都过，只有端到端跑一次真机才暴露。

**缺陷 A：`diagnosis/classifier.py` 只读恢复记录的一处落点。**

`rule_based_hypotheses` 的 UI_DRIFT 规则读 `steps.detail_json["recovery"]`。
但 P2 恢复**失败**时（元素真改名、无 LLM/经验可救——最常见的一种漂移），
`cli/pipeline.py` 写的是 `detail["recovery_attempted"]`，而 `reconcile` 只是
恢复流水线的一个**阶段**：DRIFT 结论在 `stages` 列表里
（`{"stage": "reconcile", "outcome": "DRIFT"}`），**不在**那段顶层的
`outcome`（后者是整条流水线的结论，漂移且无 LLM 时是 `"no_llm_engine"`）。
顶层的 `recovery` 段只在**恢复成功**时写。

后果：真机漂移现场 `rule_based_hypotheses` 返回空 → 规则「无结论」→ 白问一次
LLM → 仍无假设 → **不落 Bug 候选**。旧的 3 条漂移单测造的是**成功路径**的形状，
所以它们一直是绿的。

修：`_recovery_detail` 现在看 `recovery` 与 `recovery_attempted` 两处，两处的
**顶层与 `stages` 列表**都扫。新增 2 条钉子：一条造真机形态（`recovery_attempted`
+ `stages[reconcile].outcome=DRIFT`），一条**反钉**（`stages[reconcile].outcome=MATCH`
必须**不**算漂移——只看顶层会把任何恢复失败都判成漂移）。

**缺陷 B：复现证据永远进不了归因的证据集。**

`cmd_diagnose` 原来的顺序是「⑥ 算 `suggested_attribution` → ⑦ 跑复现 →
把 `_repro_score(repro)` 另加进 `evidence_score`」。于是哪怕真的重跑了三次，
落库的 `attribution_hypothesis` 仍是 `UNTRIAGED`，而 `evidence_score` 是 10——
**自相矛盾**（分数过线了，归因却说没依据）。这正是 handover §9「5.6 开工前置①」
预见的坑：崩溃/漂移这类最强规则证据单独都只值 5 / 3 分，低于阈值 10；设计 §8.3
让它们配 `reproducible_3_times`（5 分）才过线，**前提是复现证据进了同一个证据集**。

修：复现挪到**排序与归因之前**，产出经 `_with_extra_evidence` 并进**每条**假设
（按 `(kind, detail, source)` 去重），然后才 `rank_hypotheses` +
`suggested_attribution`。`_repro_score` 删除（它让同一份证据被记两遍）。
排序键不受影响：给每条假设加同一组证据等于加同一个常数。新增 2 条钉子：
一条断言 `crash(5)+repro(5)=10 → APP_DEFECT`，一条反钉「不重复记账」。

### 5.6.2 缺口 C：`same_issue_multiple_tests` 有权重、有文档、**没有生产者**

权重表（`agents/policy_config.py::DIAGNOSIS_EVIDENCE_WEIGHTS`，M1 建段）里
`same_issue_multiple_tests: 3` 一直在；`diagnosis/context.py` 的 docstring 也
明写「矩阵 #16 的 `same_issue_multiple_tests` 证据就从这里来」。但**全仓 grep
不到任何产出它的代码**——只在权重表、docstring、和几条手工拼证据列表的单测里
出现。

后果是真机上最常见的失败形态**永远过不了归因阈值**：漂移现场的证据上限是
`source_change_correlated`(3) + `reproducible_3_times`(5) = **8** < 阈值 10。
即 F10 的「系统给建议」在真机数据上**实际不可达**，退化成了「系统永远不给
建议」。`tests/unit/test_evidence_score.py` 里早就写着
「漂移 + 复现 + 多用例 = 11 → `AUTOMATION_DEFECT`」的断言——那个 11 分在真机
数据上永远走不到。

修：新增 `diagnosis/evidence.py::history_corroboration`（+ 拆出
`tests_hit_by_failure` 作为唯一事实源）。判据是**同一个 `failure_type` 命中了
≥ 2 个不同的 `testcase_id`**（现场 + 同类历史合起来算）。两道反钉子：
只失败过一个用例不给；历史里只有**同一个**用例的重跑也不给（那是
`reproducible_3_times` 的地盘，两条都给会把同一件事记两遍）。
生产者自己声明 `failure_type` 前提，不依赖 `ctx.history` 恰好被
`build_context` 过滤过（`DiagnosisContext` 也能被直接构造）。

**连带修的不一致**：`affected_tests` 原来只取 `ctx.steps[0].testcase_id`
（**第一个**用例），于是证据写「命中 2 个用例」而 bundle 里只列 1 个。改为
`tests_hit_by_failure(ctx)`——与证据同一个集合，两处不再各算一遍。

### 5.6.2b 缺口 D：`--db` 指向没初始化的库 → traceback 而不是前置错误

Gate 首跑抓到的（**第 4 个**）：G2a 用「run_id 不存在」验前置错误面，但
`--db` 指向一个还没有表的库时，`KnowledgeSources.trace_history` 抛
`ValueError`（「trace 库不可读（未初始化或无表？）」）——它**不在**
`build_context` 外面那个 `except DiagnosisContextError` 里，一路冒到 `main()`：
用户看到一屏 traceback 和 exit 1。

既有的 `test_unknown_run_id_is_a_preflight_error` 抓不到：它先
`_seed_crash_run` 把库建好，走的是「库在、run 不在」那条路。

修：`cmd_diagnose` 的上下文装配多捕一个 `ValueError` → `PREFLIGHT ERROR:
读不到 trace 现场：…` + exit 3。这是「指错了库」/「还没跑过 run」，是用户能
 corrective action 的前置错误，不该以 traceback 的面目出现。

### 5.6.3 M5 Gate 的设计前提被实测证伪：崩溃注入走不通

plan Task 5.6 写「漂移法失败 + **注入真实逻辑 bug**」，本 Gate 早先版本实现的
是后半句的一种读法：往 App 里加一个 `fatalError` 按钮 → 点它 → App 崩 →
WDA 断连 → `crash_detected` 判据成立。

**实测证伪**：点 `fatalError` 按钮，Appium 把这次 tap 记成 **SUCCESS**，用例
**照常 PASS**。补断言也一样——后续失败是 `ELEMENT_NOT_FOUND` /
`ASSERTION_TARGET_DRIFT`，`APP_CRASH` 与 `WDA_DEAD` 一个都不出现。根因是
**P1 根本没有崩溃判定逻辑**（底稿修订 #6：`APP_CRASH` 只是 `FAILURE_TYPES`
词表里的字符串），`crash_detected` 的第二条判据（infra 事件）在真机上拿不到。

于是 Gate 改用 plan 自己写的**前半句——漂移法**。这也是对的方向：漂移是 M5 在
真机上**唯一可达**的失败形态，而且正是这条路逼出了 5.6.1/5.6.2 的三个缺陷。

**漂移的形态必须是「元数据旧、App 新」**：`mta run` 的 lint 会拒掉「元数据里
不存在的 target」（`unknown_target` → exit 3）。所以**不能**先改名再重扫
metadata——那样用例根本跑不起来，那是配置错误不是漂移。正确顺序：
**先扫干净源码的 metadata，再改名重编重装，且不重扫**。

### 5.6.4 Gate 判据（`phase0/verify_p3_m5.py`）

| 判据 | 内容 |
|---|---|
| G1 | production **启动即拒**（含 `F7`），且**在写任何库之前**（agent.db 文件都不建） |
| G2a | 未知 run → exit 3（`PREFLIGHT ERROR:` 开头，无 traceback） |
| G2b | 纯断言失败 → exit 0 且**不落**候选（F10 空证据闸门） |
| G2c | **矩阵 #16 前半**：漂移单独 3 分且无法复现 → 候选照落但归因保持 `UNTRIAGED` |
| G3 | 主路径：漂移 run → 候选 `HUMAN_REVIEW`、**审核三列为空**、无 `--base-build` → 无 `dedup_key` |
| G4 | 矩阵 #14：规则命中 → LLM 零调用 |
| G5 | 矩阵 #16 前半的**反面**：漂移(3)+复现(5)+多用例(3)=**11** ≥ 10 → `AUTOMATION_DEFECT` |
| G5b | `affected_tests` 与证据对得上（证据说 2 个用例，bundle 就列 2 个） |
| G6 | 矩阵 #15：复现**真的重跑**（`attempts>=2`、每次都复现、`reproducible=True`） |
| G7 | 人工 accept → `CONFIRMED` + 审核三列落库 + Evidence Bundle（`bundle_version=1`） |
| G8 | 状态机：终态不可再推进（二次决策 fail-loud，状态不被改写） |
| G9 | 现场卫生：App 源码已还原 + gate 工程 git 干净 + 主仓三处资产逐字节未变 |
| G10 | 全量回归全绿 |

G5 一条同时钉住三件事：复现证据真进了同一个证据集（缺陷 B 的回归钉子）、
`same_issue_multiple_tests` 真有生产者（缺口 C 的回归钉子）、方向由
`source_change_correlated` 映射到 `AUTOMATION_DEFECT`（UI 变了，失效的是脚本
的定位，不是 App 功能——F10 的「建议」而非「结论」）。

### 5.6.5 测试增量

| 文件 | 变化 |
|---|---|
| `tests/unit/test_classifier.py` | 21 → 23（真漂移形态 + 反钉 MATCH 不算漂移） |
| `tests/unit/test_cli_diagnose.py` | 11 → 16（复现证据过阈值 + 不重复记账 + 多用例佐证 3 条） |
| `tests/unit/test_evidence_score.py` | +5（`history_corroboration` 生产者 4 条 + 「漂移路径因此过阈值」1 条） |
| `tests/unit/test_cli_diagnose.py` | 另 +1（trace 库不可读 → 干净 exit 3，缺口 D） |

合计 2135 → **2148 passed**。

---

## Task 6.1–6.5 完成记录（P3-26 … P3-30，2026-10-11）

M5 收口（`checkpoint-p3-m5` = `233ab1e`，2148 passed）之后开 M6。本段记录
**做出来的东西**、**登记的口径/偏离**，以及实测逼出来的真缺陷。

### 6.1.1 状态机是「形式化散文」，不是新发明（Task 6.1 / P3-26）

设计 §9.2 是一段线性草图（「PLANNING → EXECUTING → OBSERVING → …」）。
M6 把它落成一张转换表，**判据是既有三处写点必须零改动即合法**：

| 既有写点 | 转换 | 靠哪条边成立 |
|---|---|---|
| `explorer/agent.py::run()` 末尾写 IDLE | IDLE→EXECUTING→…→IDLE | `COMPLETE→IDLE`（从所有工作态可达）+ `EXECUTE` 从 IDLE |
| `cmd_explore` 的两条错误路径 | IDLE→ESCALATED | `ESCALATE` 从**任何非终态**可达 |
| `tests/unit/test_agent_store.py` | IDLE→PLANNING | `PLAN` 从 IDLE |

改完这三处仍然是 0 行 diff——这是转换表选型的**验收条件**，不是巧合。

**ESCALATED 是终态**（无出边，fail-loud）：它是设计明文的「系统认为需要人
介入」的正常退出路径，不是错误态；给它出边等于允许「升级完继续自己跑」，
与 §9.1 的「不重试不放宽」直接冲突。

**升级优先级钉死**（`should_escalate`，纯函数）：
`LLM_POLICY > CRITICAL_ACTION > GUARD_BLOCK_STREAK > LLM_INVALID_STREAK >
BUDGET_EXHAUSTED`。顺序不是实现自由——安全类（LLM 判 CRITICAL、§9.4 的
Security/Financial/Destructive/Ambiguous）必须在计数类之前，否则「已经
拦了 2 次、第 3 次才升级」会先消耗掉一次危险动作的机会。

### 6.1.2 `update_task_state` 强制走表（不写第二份判据）

`agents/storage.py` 只做**词表校验**，M6 加了**转换合法性**校验，判据直接
`from agents.state import successors`——两处各写一份转换规则是状态机项目里
最大的静默漂移源（改了一处、另一处还在放行，测试两边都绿）。

### 6.1.3 `EscalationPolicy` 增 `llm_invalid_before_escalate: 2`（登记）

设计只点名了「连续 Guard 拦截的 N 来自 policy」。LLM_INVALID_OUTPUT 的
连续阈值同为「初版值待校准」，放进同一段（校准只改 policy.yaml 一处）而不是
写死在代码里。

### 6.2.1 「同一个 Executor + Guard」的准确口径（Task 6.2 / P3-27）

F12 说的「三个 Subagent 落到同一个 Executor + Guard」= **依赖对象同一**
（`guard is guard` / `executor is executor`）；而 **toolkit 实例每个 Subagent
一个**——`task_id` 烤在 toolkit 里，共享一个 toolkit 会把三方的
`agent_trace` 全记到同一个 `task_id` 下，按任务归因直接失效（F12 的目的是
「统一工具层」，不是「统一审计行」）。

`Subagent.__init__` **没有** `executor` / `driver` / `device_session` 形参：
「直接注入执行器」在**构造层**不可达，不靠调用方自觉。

### 6.3.1 设计的 `(task, knowledge)` 两参签名不可实现（偏离 #3）

`graph_query` 的真签名要 `app_id`（范围第一段，fail-loud 不猜）、
`experience_lookup` 要 `screen_id` / `target_id`——设计的两参签名把它们藏进
`task` 里，可 `AgentTask` 没有这些字段。改为显式 kwargs 传入，**不从 goal
字符串里解析**。

### 6.3.2 `relevant_history` 不做屏级过滤（偏离 #2，**用户拍板 2026-10-11**）

trace 的 `steps` 表**不记 screen**（P1 schema 如此），而
`P2KnowledgeSources.trace_history` 对 `screen_id` 过滤键是**显式 fail-loud**
的——它自己的注释写着「静默接受一个过滤不了的键，等于把『想过滤没过滤』
伪装成『结果恰好都对』」。所以这不是「忘了实现」，是数据源缺那一列。

处置：用可用键（`app_build` + `limit`）取最近 N 步；测试钉住 builder
**绝不**把 `screen_id` 塞进 filters（塞进去当场 ValueError）。屏级收窄等
`steps` 补齐屏列——handover「待拍板三项」之一**保持登记，不闭**。

### 6.4.1 薄封装落在**中立包** `subagents/`，不是 `agents/`（偏离 #6）

plan 的 Files 段写 `agents/planner_agent.py`，本仓两条 import 级守卫把两个
方向都封死，且都不开豁免：

| 守卫 | 钉住什么 |
|---|---|
| `test_planner_models.py::test_agents_package_does_not_import_planner` | `agents/**` 不 import `planner.*`（反了就成包级环） |
| `test_knowledge_retrieval.py::test_planner_package_does_not_touch_data_sources_directly` | `planner/**` 不 import `agents`（`agents` 是数据源前缀之一，F3） |

薄封装同时要 import 两边（`agents.base.Subagent` + `planner.plan`），两个家
都不是它的，只能落中立层 `subagents/`。`TestManager` 仍在 `agents/manager.py`
且**不 import `subagents`**——`spawn(agent_cls, ...)` 由调用方给类，组装发生
在中立层。

处置原则：**让结构说话，不给守卫开后门**（放宽任何一条都要写理由进审计，
而这里没有正当需要）。

### 6.4.2 连续拦截的测试用真 Guard 的 `blocked_targets`，不是替身

第一版测试拿「SANDBOX + HIGH 元素」当连续拦截，实测**不拦**——
`Guard.check` 的规则 1 只拦 CRITICAL，HIGH 只在 PRODUCTION 拦（这条差异
在 M5 的 `test_explorer_guard` 里被 `_PageScriptExec` 的断言掩盖了）。
改用规则 3（`blocked_targets` 显式黑名单，任意环境生效）：造出的是**确定的**
连续 BLOCK，而不是赌一个 sandbox 例外。替身 guard 演出来的「拦截」测不到
真实闸门。

### 6.5.1 分母是指标的全部语义（Task 6.5 / P3-30）

| 指标 | 分母 | 为什么不是别的 |
|---|---|---|
| Duplicate Rate | 入库候选 + `SEMANTIC_REJECTED` + `DUPLICATE` | `SCHEMA_INVALID` 连候选形状都没解析出来，算成「生成了」会让重复率被 schema 噪声稀释 |
| Reproduction Rate | `reproducible IS NOT NULL` | 「还没跑复现」既不是真也不是假，进分母会把「没判」算成「复现不了」 |
| Duplicate Bug Rate | **全部** bug | 刻意与上面不同：dedup 合并是**判定后**的事，全量分母才反映重复占比 |

**Selection Precision / Diagnosis Accuracy 不在 `AgentMetrics` 上**——
依赖人工标注真值，库里没有。collect 假装能算就是自欺；它们只在 Benchmark
的标注集内计算（`agents/benchmark.py`，每类 case 自带预期）。测试用
`not hasattr` 钉住「字段不存在」。

### 6.5.2 迁移编号 007（偏离 #1）

plan 写 `005_benchmark_runs.sql`，005/006 已被 M5/M4 占用，迁移只能向后追加
→ **007_benchmark_runs.sql**。两处幂等测试原本**钉死链尾**
`== "006_discovery_events"`，按其自身注释（「版本串从链尾派生，加脚本后这里
跟着变——这正是它该有的行为」）更新为 007。plan 预测的「用 `>=` 断言不需改」
不成立。

### 6.5.3 CLI：`mta agent benchmark run|compare`

`"agent"` 早在 M1 就进了 `AUTONOMOUS_COMMANDS`，F7 启动期拒绝自动生效——
本任务只加 parser，不加第二道闸。`compare` 判回归 → **exit 1** +
`AGENT_REGRESSION` 供 CI。

### 6.6.1 本轮修掉的**真缺陷**（都是全量回归 / A/B 探针逼出来的）

| # | 缺陷 | 怎么发现的 | 修法 |
|---|---|---|---|
| E1 | `compute_agent_metrics` 用 `reproducible is True` 判复现 | 6.5 端到端测试：sqlite 没有布尔类型，回来是 **int 0/1**，「真复现」被判成「没复现」 | 改真值判断（纯 dict 喂进来的 bool 同样命中） |
| E2 | `AgentContext` 的空子集断言写成 `== []` | 6.3 单测 | dataclass 字段默认 `()`，断言改 `()` |
| E3 | `test_benchmark` 把两条回归判定用 `or` 合并断言 | **A/B 探针**：分别改坏两条分支，各跑都是 7 passed | 拆成两条独立断言 |
| E4 | 薄封装落点违反两条分层守卫 | 全量回归（`test_agents_package_does_not_import_planner` / `test_planner_package_does_not_touch_data_sources_directly`） | 移到中立包 `subagents/`（见 6.4.1） |
| E5 | 连续拦截测试的前提（SANDBOX 拦 HIGH）**实测证伪** | 6.4 单测 | 改用 `blocked_targets` 规则 3（见 6.4.2） |
| E6 | `verify_p3_final.py` f-string 引号不配对 → SyntaxError | Gate 首次运行 | 修正引号 |

E1 与 E3 属同一类：**测试全绿但断言比声称的弱**（sqlite 的类型、布尔合并
断言）。A/B 探针是唯一能把这类问题逼出来的手段——全量回归只告诉你「没红」。

### 6.6.2 A/B 探针：19 条，全部如实变红

因 Bash 分类器故障，M6 全程**没能先红后绿**（首轮 pytest 直到任务 6.6 才跑
得起来）。补做逐条变异探针：改坏一个语义 → 对应测试必须当场红 → 还原 →
md5 校验。**19/19 变红**，其中 3 条第一版**没变红**（探针本身无效或断言太
弱），据此补了 E3 的断言拆分与 `relevant_source` 的空子集断言。

| 任务 | 探针 |
|---|---|
| 6.1 | ESCALATED 变非终态 / COMPLETE→IDLE 撤边 / `update_task_state` 不校验转换 / llm_invalid 阈值不参与升级 |
| 6.2 | toolkit 不做类型断言 / 工具调用不留痕 / llm_budget 不做类型断言 |
| 6.3 | `trace_history` 传 screen_id / source 子集不按屏切 / current_screen 不按屏切 / history limit 写死 |
| 6.4 | 连续 Guard 拦截不计数 / 已升级任务可重跑 |
| 6.5 | SCHEMA_INVALID 进分母 / 未判定进复现率分母 / 造出依赖真值的字段 / benchmark 三条判定各改坏一条 |

### 6.6.3 测试增量

| 文件 | 用例数 |
|---|---|
| `tests/unit/test_agent_state.py` | 18 |
| `tests/unit/test_subagent_binding.py` | 7 |
| `tests/unit/test_agent_context.py` | 9 |
| `tests/integration/test_subagent_coordination.py` | 8 |
| `tests/unit/test_agent_metrics.py` | 9 |
| `tests/unit/test_benchmark.py` | 7 |

合计 2148 → **2206 passed / 0 failed**。

---

## Task 6.6 完成记录（P3-31 端到端自主回归闭环，2026-10-11）

Gate：`phase0/verify_p3_final.py` → **33/33 PASS，exit 0，78.2s**，
全量回归 **2207 passed / 0 failed**。演示留证见 `docs/p3_demo_record.md`。

### 6.6.1 Gate 的四段与两道矩阵

| 段 | 检查 | 环境 |
|---|---|---|
| OFF | G1 F7 production 拒绝（且不写库）/ G2 Benchmark 三段闭环 / G3 矩阵 #18 manager 级 | 无设备 |
| CHAIN | G4 plan → G5 generate（LLM 桩）→ G6 人工 accept → G7 run `--fake-driver` | gate 自足工程 A |
| DEV | G8 explore（真机 sandbox）→ 漂移注入 → G9 run 失败 → diagnose → 人工 accept → Evidence Bundle | gate 自足工程 B + 模拟器 |
| 收尾 | G10 矩阵 #17 资产零改动 / G11 全量回归 | — |

两个工程都在 `out/p3_final_gate/gate_env/` 下**自建 git 仓库**，并在建仓后
断言 `rev-parse --show-toplevel` == 工程根（M2 的 M4 事故：仓库根穿透到主仓库）。

### 6.6.2 Gate 自己被实测推翻的**六处**（全在「测试全绿但判据站不住」这一类）

| # | 症状 | 真相 | 修法 |
|---|---|---|---|
| G-F1 | 首次运行直接 `SyntaxError` | f-string 引号不配对（`f"…'`） | 修正 |
| G-F2 | 第二次运行 `IntegrityError: UNIQUE constraint failed: benchmark_runs.run_id` | Gate 固定 `run_id` 留可寻址槽位，`save_benchmark_run` 是裸 INSERT → **Gate 不可重入** | 改成 upsert（与 `save_candidate` 同款，附理由）+ 跑前清 `gate_env`；补单测 `test_rerunning_the_gate_does_not_crash_on_the_same_run_id` |
| G-F3 | G3c FAIL：planner/diagnosis 的 trace 是 0 行 | 断言写成了「三个 agent 都得有 trace 行」，但它们是**纯计算薄封装**，没有工具调用就没有痕 | 断言改成**归因**：每行记在自己名下 + 没有串号 + 只调过工具的那个有痕 |
| G-F4 | G8a PASS 但 `outcome=LLM_BUDGET_EXCEEDED steps=0 screens=0` | Gate 自己把 `max_llm_calls` 写成 **0** → 第 0 步熔断，而 `explore` 仍 exit 0 | 改 20（M4 Gate 同值）+ 判据从「exit 0」改成「**steps ≥ 1 且终态是有界停止**」 |
| G-F5 | 收紧后的 G8a FAIL：`outcome=FRONTIER_EMPTY steps=0 screens=1` | App 冷启动停在 **LoginView**，而声明面只播种了 `HomeView→ProfileView`——从 LoginView 出发 frontier 就是空的 | 补播 `LoginView→HomeView (tap:LoginView.login_button)`；结果 `LOOP_DETECTED steps=3 screens=2` |
| G-F6 | G10b FAIL：`git status = ''` | `mta candidate review accept` **自己会 git commit**，所以正常情况就是干净的；断言写成 `status_a and …`（要求非空），把「干净」判成 FAIL | 允许空（或只允许 `?? suites/`） |

**G-F4/G-F5 是 M4 Gate 那次 `FRONTIER_EMPTY steps=0` 假绿的同款复发**——
上一轮的教训没有落成断言，这次由 Gate 自己的预算配置重新演示了一遍。
处置：判据不写成「exit 0」，而写成「真的动过 + 终态在设计 §7.2/7.3 的
词表内，且唯独排除 `LLM_BUDGET_EXCEEDED`（那是配置错，不是有界停止）」。

### 6.6.3 两条**测试侧**的假绿（与 Gate 无关，A/B 探针逼出来的）

见 6.6.1 的 E1（sqlite 的 `reproducible` 是 int 不是 bool）与 E3
（`compare` 的两条判定被 `or` 合并断言）。两者都属于「全量回归只告诉你没红，
A/B 探针才告诉你断言够不够」。

### 6.6.4 M6 收口数字

| 项 | 值 |
|---|---|
| Gate | 33/33 PASS，exit 0，78.2s |
| 全量回归 | 2148 → **2207 passed**（M6 新增 59） |
| A/B 探针 | 19/19 如实变红 |
| 新增文件 | `agents/{state,base,context,manager,benchmark}.py`、`subagents/`（3 个薄封装）、`report/agent_metrics.py`、`agents/migrations/007_benchmark_runs.sql`、`phase0/verify_p3_final.py`、`docs/p3_demo_record.md` |
| 新增测试 | 6 个文件 / 59 条 |

---

## 7. Task 6.7 / P3-32 —— 工具层接线补齐（3 知识 + 2 提案）

**背景**：M1 建了 22 个工具，只接了 12 个。`NOT_WIRED` 表是「哪些还没有」
的可读清单，但它**点名等的是已完成的里程碑**——写「等 Task 2.2」「等 M5」
「等 M4/M6」时它们还没接线，指望它们交付是不成立的。这张表因此一直在说谎，
而说谎的代价是每次读它的人都得重新核实一遍（本次核实结论：那 10 个里
**5 个现在能接、3 个没有数据源、2 个不该接**）。

### 7.1 接线 5 个

| 工具 | 依赖 | 关键口径 |
|---|---|---|
| `get_relevant_experience` | `knowledge` | F3：走 `KnowledgeSources.experience_lookup`，测试用**计数替身**钉住（不是「结果对就算」） |
| `get_graph_neighbors` | `knowledge` | 同一张图，本地过滤出边+入边；`in_graph=False` 与「在图里没边」严格区分 |
| `get_relevant_source` | `source_metadata` | ⚠️ **不走** `KnowledgeSources`——源码元数据不是它的四查询之一，注入的映射（与 `agents/context.py` 同一形状，两处不会分叉口径） |
| `create_test_candidate` | `agent_db` + `repository` + `secrets` | F5：`generator/validator.py::validate_schema`（`mta lint` 同一实现）；ERROR 拒且留痕。**Dry Run 不在这里**（留 `PENDING`，闸门在 `generator/dry_run.py`；候选未经人工 ACCEPT 落不进 `suites/`，触达不了设备） |
| `create_bug_candidate` | `agent_db` | F10：`attribution_hypothesis` / `evidence_score` / `status` **都不是形参**——工具层只能写 `DISCOVERED` / `UNTRIAGED` / 0。`reproducible` / `reproduction_count` 是如实转述的**观测**，可传 |

`NOT_WIRED`：**10 → 5**（`tests/unit/test_agent_tools.py` 的人数钉同步
12/10 → 17/5——那行注释本来就说「每次接线一个工具都要改这一行」）。

### 7.2 剩下 5 个为什么不接（决策③「列出不猜」）

- **3 个诊断数据源**（`get_logs` / `get_network_summary` / `get_crash_info`）：
  本仓**没有任何采集代码**（已 grep 全仓确认）。`get_network_summary` 尤其
  是 `backend_evidence`（权重 2）至今**没有真实生产者**的原因。给它们写一个
  「读本地文件假装有日志」的实现，比留着 `ToolNotWired` 更糟：诊断会把
  「没采到」读成「App 没报错」，方向反了。**单独一批做**（决策②）。
- **2 个重跑入口**（`run_testcase` / `run_candidate_test`）：**不是没接线**，
  是**工具本体没有默认实现**。生产路径由调用方注入回调绕开本体——
  `diagnosis/reproducer.py::_run_once` 优先用注入的 `runner`（`cli/main.py::
  _run_reproduction` 注入真回调），只有没注入回调时才落到本体并 fail-loud。
  不猜「默认该跑哪条流水线」：run 的入口有 `manage_env` / lifecycle /
  `run_id`，工具层拿不到。

### 7.3 反钉（防止表退回去）

`test_every_not_wired_entry_names_a_real_remaining_debt` 断言每条理由
**不得出现 `Task 2.2` / `M4` / `M5` / `M6` / `Task 3.4`**，且长度 > 30
（说不清缺什么数据源就过不了）。这是把「历史误报」做成机械检查，而不是靠
下一个人记得。

### 7.4 测试侧的两处修正

1. **`create_bug_candidate(status=…)` 的静默吞参**：第一版实现留了 `**kw`，
   于是 `status="CONFIRMED"` 被 `**kw` 吃掉、**既不报错也不生效**——测试
   当场抓到。删掉 `**kw`（形参删掉而不是加断言，与 `agents/storage.py` 的
   `status` / `review_status` 处置同款）→ 现在以 `TypeError` fail-loud。
2. **测试污染主仓库**：一条测试用 `drift_repo(Path("."))` 建替身
   Repository，在仓库根下真写出了 `generated/local/**`。已改为 `tmp_path`
   fixture 并删除该目录。**教训**：替身工厂也吃路径参数，`Path(".")` 在
   测试里同样是「往工作目录里写」。

### 7.5 数字

| 项 | 值 |
|---|---|
| 新增测试 | `tests/unit/test_agent_tools_wired.py` / 17 条 |
| 全量回归 | 2207 → **2219 passed / 0 failed** |
| `NOT_WIRED` | 10 → 5（3 无数据源 + 2 无默认执行上下文） |

### 7.6 用户拍板四项的收口状态

M6 收口后向用户提的四个待拍板项，本批处置如下：

| # | 问题 | 拍板 | 状态 |
|---|---|---|---|
| ① | 本批接哪几个工具 | 3 知识 + 2 提案（5 个） | **已实施**（§7.1）。偏离登记：原 `NOT_WIRED` 的指路全是过期里程碑 |
| ② | 3 个诊断数据源何时建 | 单独一批（需真机 syslog / crash / 网络层） | **登记不闭**，`NOT_WIRED` 逐条写明缺什么采集器 |
| ③ | 坏引用的处置口径 | **列出不猜** | **已机械化为断言**（§7.3）：不为了表好看而给无数据源的工具编假实现 |
| ④ | 权重标定 | **登记不动，等真实数据** | **方法已写死**（`diagnosis/evidence.py` docstring 的「权重校准方法」节）：触发条件（≥30 条人工确认归因 + 每类都有生产者）、判据、不许动的三项、当前样本量 1 条 |

④ 顺带坐实了一件事：`backend_evidence`（权重 2）**至今没有真实生产者**——
它本来就是 §8.3 六类里唯一悬空的，而 M6 Gate 那次拿到 11 分的构成
（reproducible 5 + source_change_correlated 3 + same_issue_multiple_tests 3）
压根没用到它。这与 ② 是同一件事的两个侧面：**先把数据源建出来，权重才有
资格谈校准**。

---

## 8. Batch B 前置勘察（决策②的**探路**，2026-10-11）

决策②定的是「单独一批建真实数据源」。开工前先把**数据源到底存不存在**探明
——否则会写出一个「读本地文件假装有日志」的采集器（正是 §7.2 拒绝的形态）。
下面每条都是**本机实测**，不是推断。

### 8.1 `get_logs` —— ✅ 可行（模拟器）

```bash
xcrun simctl spawn <UDID> log show --last 5m --style compact \
    --predicate 'process == "LoginDemo"'
```

实测：**1.1s** 返回 143 行（App 冷启动的完整 syslog），进程名过滤有效。
即日志源是**有界、可重复、可断言**的。真机（非模拟器）需要
`idevicesyslog`（`libimobiledevice`）——本机**未安装**，故真机路径只能
fail-loud 而不是静默空。

### 8.2 `get_crash_info` —— ✅ 可行（模拟器），且**能拿到真正的崩溃报告**

- 崩溃报告落点：`~/Library/Logs/DiagnosticReports/<App>-<ts>.ips`（**宿主**目录，
  不是模拟器内部）。
- `.ips` 是**两段式**：第 1 行是 JSON 元数据，**第 2 段才是正文 JSON**（整文件
  不是合法 JSON，`json.load` 直接解析会失败——这是个真坑）。
- 实测 `kill -SEGV <模拟器进程 pid>` → 6~8s 后产出报告，字段可用：
  `bug_type=309`、`exception.signal=SIGSEGV`、`faultingThread`、`threads[i].frames`
  （17 帧，frame0 有 symbol）、`usedImages`（13 个镜像）。
- **模拟器进程在宿主 `ps` 里可见**（`simctl launch` 返回的 pid 直接 `kill -SEGV`
  生效），所以「模拟器上做崩溃注入」是可行的。

### 8.3 ⚠️ `crash_detected`（权重 5）**今天没有任何生产者**

勘察发现的一条**比工具没接线更严重**的事实：

| 事实 | 位置 |
|---|---|
| `crash_detected` 的判据是「步骤 `failure_type == "APP_CRASH"`」**或**「`infra_events` 里有 `WDA_DEAD` / `DEVICE_UNAVAILABLE` 且现场有失败步骤」 | `diagnosis/classifier.py` 的 `_CRASH_FAILURE_TYPES` / `_CRASH_INFRA_EVENTS` |
| 全仓 `"APP_CRASH"` 只出现在**测试**里（`tests/**`）；生产侧**没有一处**写入它 | grep 全仓：`tracer/storage.py:48` 只是**词表**（允许的枚举值），不是写入点 |
| App 崩溃时 `pipeline` 映射出来的是 **`WDA_FAILURE` + status `INFRA_FAILURE`** | `cli/pipeline.py` 的 `except InfraError` 分支 |

即：**App 真的崩了，落库的分类是 `WDA_FAILURE`**（基础设施失败），
`crash_detected` 返回 False。这条与 M5 那次「`fatalError` 按钮点下去 Appium 记
SUCCESS」是**同一个坑的两个面**：P1 压根没有「App 崩了」的判定。

→ **Batch B 的真正价值不在接线，而在补上这个判定**：崩溃报告采集器是它的
**唯一**可能数据源（`crash_detected` 的第二个判据要求 `infra_events` 事件，
而 `device_session._log_infra` 只写 `WDA_DEAD` / `WDA_RESTARTED`）。

### 8.4 ⚠️ `get_network_summary` —— **本技术栈上不可实现**（勘察结论）

- 没有代理层，也没有任何网络拦截代码（全仓 grep 确认）。
- **WDA 不暴露网络日志**：`/session/:id/` 下只有 UI 相关的端点。
- 模拟器有 `xcrun simctl io <UDID> recordVideo`，但**没有** `simctl net` 这类
  抓包子命令；模拟器流量走宿主网络栈，抓它需要宿主级代理。
- 宿主装了 `mitmproxy`，但走它意味着「跑回归要先起一个 MITM 代理并让 App 信任
  其根证书」——那是**改被测环境的运行方式**，与 F2/F7 的立场冲突（本仓的
  纪律是「不为了测试去改被测系统的形态」）。

→ 结论：`backend_evidence`（权重 2）**在本技术栈上没有可实现的真实生产者**。
这与 §7.6 的登记一致（那条说「没有生产者」，现在把**为什么**说清了：不是没做，
是做不了）。**要么接受它永远缺席，要么换被测技术栈**——这是个真问题，不是欠账。

### 8.5 由此得到的 Batch B 形状（**未开工，等拍板**）

1. `evidence/collectors.py`：**两个**真采集器（syslog / crash report）+ 端口
   （`LogCollector` / `CrashCollector`），全程**离设备可单测**（F13：采集器吃
   「一个返回原始文本的可调用对象」，测试喂假文本）；
2. `diagnosis` 侧：崩溃报告 → `failure_type=APP_CRASH` 的判定落点（`pipeline`
   的 `except InfraError` 分支要能分辨「WDA 死了」与「App 死了」）；
3. `get_logs` / `get_crash_info` 接线（`NOT_WIRED` 5 → 3）；
4. `get_network_summary` **维持不接线**，理由改写成 §8.4 的勘察结论
   （「本技术栈不可实现」≠「还没做」）。

### 8.6 拍板：`backend_evidence` **接受缺席，登记为设计缺口**（2026-10-11）

用户拍板：不改被测环境去够一个数据源。`get_network_summary` **维持不接线**，
理由从「还没做」改写成 §8.4 的勘察结论（**本技术栈不可实现**）——这两句话在
交接时的含义完全不同：前者是欠账，后者是缺口。

后果显性登记（两处）：`diagnosis/evidence.py` 的校准方法节（该类权重**永远
无法校准**，故第 2 条触发条件对它永不成立）+ 本节。Benchmark 的
`backend_failure` case 验证的是「规则在给定摘要下判得对」，**不是**「真能采到
网络证据」——`docs/p3_demo_record.md` §1.2-6 已按同款口径写明未覆盖。

---

## 9. Batch B —— 设备侧证据采集 + `APP_CRASH` 判定（用户拍板 ②）

**这一批的真正价值不是「接线」，是补上一个**没有任何生产者**的证据类别。**
`crash_detected` 权重 5，是 §8.3 六类里最重的两项之一，而它今天恒 False——
见 §8.3 的勘察。

### 9.1 交付

| 文件 | 内容 |
|---|---|
| `telemetry/collectors.py` | `SyslogCollector`（`simctl spawn … log show --style ndjson`）+ `CrashCollector`（宿主 `.ips`）+ 两个解析器 + `default_runner` |
| `telemetry/probe.py` | `CrashProbe`：「自 `since` 起该 bundle 崩过吗」，**有界等待**、失败即 `None` |
| `cli/pipeline.py` | `_crash_verdict` / `_infra_outcome` / `_infra_detail` 三个纯函数 + `except InfraError` 分支接入 + `crash_probe` 注入点 + `_run_started_at` |
| `cli/main.py` | `cmd_run` 真机路径注入 `CrashProbe`；`cmd_diagnose` 补 `infra_events` / `--logs` / `--crash-report` |
| `agents/tools.py` | `get_logs` / `get_crash_info` 接线（`NOT_WIRED` 5 → 3） |

**`telemetry/` 是独立包**，不是塞进 `diagnosis/`：后者有一条 import 级 F3 守卫
（`test_diagnosis_package_does_not_touch_data_sources_directly`），采集器要
`subprocess` + 读宿主目录，塞进去只能给守卫开「除了 subprocess 和 pathlib」的
后门——**让结构说话**（与 M6 把薄封装落到 `subagents/` 同一条纪律）。

### 9.2 判据：`APP_CRASH` 走 `FAIL` 不走 `INFRA_FAILURE`

| 情形 | status | failure_type | 退出码 |
|---|---|---|---|
| 探到崩溃报告 | **`FAIL`** | **`APP_CRASH`** | 1 |
| 没有报告 / 没探测 | `INFRA_FAILURE` | `WDA_FAILURE`（旧行为逐字保留） | 2 |

设计 P1 §8.2 对 `APP_CRASH` 的规定是「**不得当作** `ELEMENT_NOT_FOUND`」——
其精神同样适用于「不得当作基础设施故障」：App 崩了是 App 的账，记成环境问题
会让归因方向**反掉**。退出码从 2 变 1 是**有意的**：一次真崩溃不该读成
「环境不行」。

**App 崩溃时不置 `wda_died`**：`_wda_aftermath` 见它就会重启 WDA 再跑一遍
——对一个已经崩掉的 App，重启 WDA 不是处置（那是把 App 缺陷当抖动）。

### 9.3 三处**新发现**的缺口（都不是本批引入的）

1. **`cmd_diagnose` 从来没传过 `infra_events`**：`build_context` 的形参一直
   存在，调用点漏了。于是 `crash_detected` 的**第二条**判据（infra 崩溃事件）
   在真机上永远拿不到输入。⚠️ **单测看不见这类缺口**——单测自己传参当然绿，
   漏的是调用点。故补了**调用点级**的钉子
   （`test_cli_diagnose.py::test_diagnose_feeds_infra_events_into_the_context`，
   走真 CLI + monkeypatch 监听 `build_context` 的实参）。
2. **`DeviceSession._log_infra` 的 `record_infra` 契约不匹配**（P1 遗留）：
   `cli/pipeline.py:208` 早就记下「TraceStore 不做 recorder」，于是 `WDA_DEAD`
   只进 JSONL 调试副本，**从不落 `trace.db` 的 `infra_events`**。这意味着
   `crash_detected` 的第二条判据在真机上**同样拿不到数据**（不只是「没传」）。
   本批**未修**（改 recorder 契约会动 P1 的落库路径，超出本批范围）——**登记
   为已知缺口**，见 §9.5。
3. **`--logs` / `--crash-report` 是「调用方给」而不是自动采集**：`diagnose` 是
   **离线命令**，自动采集会让它隐式连设备。采集由 `mta run` 侧（`CrashProbe`）
   或人工给。

### 9.4 A/B 探针：14 条，**2 条第一版没变红**

探针改坏一处语义 → 对应测试必须当场红 → 还原 → md5 校验。**2 条没红**，
两条都是**真覆盖漏洞**（不是探针写错）：

| 探针 | 现象 | 根因 | 处置 |
|---|---|---|---|
| B2 `ndjson 遇坏 JSON 行就炸` | 全绿 | 现有噪声测试喂的是 `getpwuid_r …`，**不以 `{` 开头** → 走 `startswith("{")` 守卫，`except JSONDecodeError` 那条**从未被执行** | 补一条「以 `{` 开头但截断」的测试 |
| B6 `default_runner 非零退出返回空串` | 全绿 | 现有测试让 `subprocess.run` **抛** `CalledProcessError` → 走 `except SubprocessError` 分支；`returncode != 0` 那条**从未被执行**（而真实形态恰恰是 `check=False` 下正常返回非零） | 补一条 `run` 返回非零 `returncode` 的测试 |

补完后 **14/14 全部如实变红**。这是「全量回归只告诉你没红，A/B 探针才告诉你
断言够不够」的**第二次实证**（第一次见 §6.6.1 的 E1/E3）。

⚠️ 探针脚本第一版把汇总行全打成了 `??`——**`pytest.ini` 已有 `addopts = -q`，
探针又传了一个 `-q`**，`-qq` 把汇总行吞了。这正是 `.workbuddy-ai` 那份 SKILL
里写着的坑，我又踩了一次。**判据读不到时不能当「没红」**，重跑一遍拿到真读数
才发现 B2/B6 的问题。

### 9.5 登记：本批**未闭**的两项

1. **`infra_events` 的 recorder 契约**（§9.3 第 2 条）：`WDA_DEAD` 不落
   `trace.db`。改它要动 P1 的落库路径（`TraceStore.record_infra` 的签名与
   `DeviceSession._log_infra` 的调用形态），**单独立项**。
   ⚠️ 后果要说清：`crash_detected` 的**第二条**判据（infra 崩溃事件）在真机上
   仍拿不到数据；**第一条**判据（步骤 `failure_type == "APP_CRASH"`）本批已
   打通——但**当时引用的 §9.6 是函数级实测，已被 §10 推翻并重做**，
   真机端到端留证见 **§10.7**。即**该类别不再是「零生产者」**（这条结论
   经 §10 的端到端复验后依然成立，只是证据换了更硬的那一份）。
2. **`get_network_summary`**：按用户拍板维持不接线，理由已改写为
   「本技术栈不可实现」（§8.4/§8.6），`NOT_WIRED` 3 条。

### 9.6 端到端实测（真模拟器，非单测）

```text
1) syslog:  3 行（simctl spawn … log show --style ndjson，1.1s）
2) crash probe: HIT  071AECCD SIGSEGV mach_msg2_trap
   （kill -SEGV 真机进程 → CrashProbe 有界等待命中）
3) outcome: status=FAIL failure_type=APP_CRASH wda_died=False
4) crash_detected(ctx) = True
```

即 **「App 崩了 → 记成 APP_CRASH → 规则判出崩溃假设」这条链在真机上是通的**，
不再是「判据写了但没人产出」。

> ⚠️ **本节结论已被推翻，见 §10。** 上面这段是**函数级**实测（手工把
> `InfraError` 喂给 `_infra_outcome`）——它证明的是「判据对」，**不是**
> 「真机会走到那条判据」。同一批的 `mta run` 真机端到端实测记的是
> `CURRENT_SCREEN_UNKNOWN`：App 死了最常见的表现是普通步骤失败，
> **根本不经过 `except InfraError`**。这正是「函数级 E2E ≠ 端到端 E2E」。

### 9.7 数字

| 项 | 值 |
|---|---|
| 新增测试 | 5 个文件 / 40 条（`test_telemetry_collectors` 18 / `test_crash_probe` 7 / `test_pipeline_crash_verdict` 8 / `test_cli_run_crash_probe` 2 / `test_cli_diagnose` +5） |
| 全量回归 | 2219 → **2261 passed / 0 failed** |
| `NOT_WIRED` | 5 → **3**（19/22 已接线） |
| A/B 探针 | 14 条，补 2 条断言后 **14/14 红** |


---

## 10. Batch B 修正 —— 崩溃判定必须覆盖**所有**失败出口（2026-10-11）

§9 交付后，用 `mta run` 在真模拟器上做**端到端**验证（不是函数级），
当场打脸。本节记录缺陷、根因、修法与真机复验。

### 10.1 缺陷：真机崩了，记的却是普通步骤失败

```text
$ kill -SEGV <LoginDemo pid>   # 后台注入，run 进行中
$ mta run --case login_again_001 …
login_again_001: FAIL (CURRENT_SCREEN_UNKNOWN)
run exit=1

$ ls -t ~/Library/Logs/DiagnosticReports/LoginDemo*.ips | head -1
LoginDemo-2026-10-11-204403.ips   {'type': 'EXC_CRASH', 'signal': 'SIGSEGV'}
```

崩溃报告**在案**（时间窗内、bundle 对得上），管线却记 `CURRENT_SCREEN_UNKNOWN`。

**根因**：`_crash_verdict` 只挂在 `except InfraError` 分支上。而 **App 死了
最常见的表现不是 WDA 报错，而是「元素找不到 / 屏认不出来」这类普通步骤失败**
——`wait_for(screen:HomeView)` 超时走的是 `WaitStep` 的 `WaitTimeout` 分支，
与 `InfraError` 毫无关系。

**为什么单测看不见**：上一批 8 条 `test_pipeline_crash_verdict` 全部只喂
`InfraError`（因为「崩溃判定」这个词的直觉入口就是它）。**真机喂的是
`WaitTimeout`。** 与 §9.3 第 1 条同款：单测自己传参当然绿，漏的是**真实形态**。

### 10.2 判据方向：**排除表**而不是白名单

第一版修法想用白名单（「哪些症状可以改判」）——**被自己否掉了**：本批的缺口
正是「没想到的症状形态溜过了白名单」。白名单下，下次出现一个新的症状名，
同一条链会**再断一次**，而且是静默的。

故 `cli/pipeline.py::_CRASH_UNEXPLAINABLE` 是**排除表**：逐条列出「有别的账主、
崩溃解释不了」的失败类型，每条带一句理由（断言值不符归 P1、Guard 拦截在
分派前、环境/变量解析归环境……）；**其余一律改判**。

误报面被两件事夹住：
1. 必须有**时间窗内**的真实崩溃报告（权重 5 的强证据）；
2. 原症状留在 `detail.crash_reclassified.failure_type` 里（可审计、可回退）。

### 10.3 两处**必须同时**改（只改一处链还是断的）

| 落点 | 为什么 |
|---|---|
| `testcase_runs.failure_type` | 用例终态——报告/退出码读它 |
| **`steps.failure_type`** | ⚠️ `diagnosis/classifier.py::crash_detected` 的**第一条判据读的是步骤行**（`ctx.steps` 来自 `steps` 表）。只改用例行，规则引擎仍然看不见崩溃 |

新存储原语 `TraceStore.mark_steps_app_crash(tc_run_id, *, exclude, crash)`
（默认空排除表 = 全部改判，与管线侧同向；只动 `status='FAILED'` 的行）。

### 10.4 三处**新发现**的既存缺口（都不是本批引入）

1. **断言值不符的出口一行步骤都不落**：`cli/pipeline.py` 把
   `AssertionValueMismatch` 原样 re-raise 给外层 `except Exception`，那条分支
   不落步骤行。P3-1「失败步骤也落库」的纪律没覆盖到这个出口。
   **本批未修**（改的是落库路径，独立立项）；已用一条**钉住现状**的断言登记
   （`test_pipeline_crash_reclassify.py`，明写「这是既存缺口」）。
2. **`ACTION_FAILED` 不在 `FAILURE_TYPES` 词表里**：`cli/pipeline.py` 的
   App 级动作失败写的是 `"ACTION_FAILED"`，而 `tracer/storage.py::FAILURE_TYPES`
   没有这个值（词表里有的是 `APPIUM_FAILURE`）。写入未做词表校验所以不报错，
   但「词表是口径」这句话对它不成立。**登记，不在本批修。**
3. **套件路径的 precondition 崩在窗外**：`manage_env=False` 时 prepare 由
   `SuiteRunner` 在 `run_one` 之前做完，落在用例级时间窗之外。那条路径上
   prepare 抛异常根本不会变成 `TestcaseResult`，**没有行可改判**——所以是
   「本来就无从归因」，不是本改动丢的覆盖。已在 `_run_case_once` 就地登记。

### 10.5 时间窗的粒度：**用例级**（有意为之）

原实现是 run 级一处（`_run_started_at`）。本次改为**逐用例**设窗：
结论的粒度决定窗口的粒度——这次改判只改写**本条用例**的终态，run 级窗会让
「A 用例崩了、B 用例随后也失败」把 B 的失败记到 A 的崩溃账上（归因粒度比证据
粒度粗，是误报的常见来源）。

代价如实说：run 级窗能覆盖「App 在用例间隙崩了」，用例级覆盖不到。但那种崩溃
**归属本来就说不清**（没有失败步骤可改判），而它若在下一条用例里表现出来，
下一条自己的窗就会覆盖到。

**探测缓存**：`_crash_report()` 每用例至多探测一次（`CrashProbe` 是有界等待，
默认 12s）。两个消费点（`except InfraError` 与收尾改判）共用一次结果——各自
探测会让同一条用例多挂一倍，且两次结果可能不同（第二次才等到报告落盘），
同一条用例上出现两个互相矛盾的结论。

### 10.6 A/B 探针：14 条，**4 条第一版没变红**

| 探针 | 现象 | 根因 | 处置 |
|---|---|---|---|
| R7 探测不缓存 | 全绿 | 「至多探一次」那条测试只喂了 **wait 路径**（单消费点）——缓存坏了也看不出来 | 补「两个消费点共用一次」的断言（用 `InfraError` + 动作步，唯一走两次消费点的形态） |
| R12 收尾改判自己探一次 | 全绿 | 同上 | 同上 |
| R13 InfraError 分支自己探一次 | 全绿 | 同上 | 同上 |
| R4 调用点不再只挑 FAIL | 全绿 | **等价变异体**（已证）：`_run_case_once` 里每个非 FAIL 的 `status` 赋值的 `failure_type` 都在排除表里，去掉 `status == "FAIL"` 守卫行为不变 | **不改测试**——守卫是防御性的（新出口若配了可改判类型，它才生效）。用穷举脚本证明等价性，写进本节 |

补完后 **14/14 红**（R4 除外，且已证明它**不可能**红）。

### 10.7 真机端到端复验（这才是验收）

同一场景重跑（`kill -SEGV` 注入在第 6 步 `wait_for(screen:ProfileView)` 期间）：

```text
$ mta run --case login_again_001 …
login_again_001: FAIL (APP_CRASH) -- WAIT_TIMEOUT: target='ProfileView' …
run: exit 1

# trace
testcase_runs: status=FAIL failure_type=APP_CRASH
steps[6]:      status=FAILED failure_type=APP_CRASH
               detail.error = "WAIT_TIMEOUT: target='ProfileView' …"   ← 原症状留着
               detail.crash_reclassified.crash.path = …/LoginDemo-2026-10-11-213904.ips

# 诊断链（离线，读同一份 trace）
crash_detected(ctx) = True
hypothesis: App 在执行过程中崩溃（进程消失）  evidence=crash_detected
$ mta diagnose … → bug 候选 status=HUMAN_REVIEW score=5
  （5 < 阈值 10 → 归因建议不达标，保持 UNTRIAGED —— F10「假设不是事实」）
```

**「App 崩了 → 记成 APP_CRASH → 规则判出崩溃假设」这条链现在在真机上是通的**
——而且这次的证据是**端到端**的（§9.6 那条是函数级的，被本次实测推翻）。

### 10.8 数字

| 项 | 值 |
|---|---|
| 新增测试 | 1 个文件 / **50 条**（`test_pipeline_crash_reclassify.py`） |
| 全量回归 | 2261 → **2311 passed / 0 failed** |
| A/B 探针 | 14 条，补 3 条断言后 **14/14 红**（R4 已证等价，不在此列） |
| 真机复验 | `FAIL (APP_CRASH)` + 步骤行同步改判 + 诊断链闭合 |
| `NOT_WIRED` | **3**（不变——本批修的是判据覆盖面，不是工具接线） |
