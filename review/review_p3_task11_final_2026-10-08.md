# Review — Phase 3 Task 1.1 收口修订（`3a6d5de`）+ `review_p3_task11` 的 2×P2 + 5×P3 核销

- 日期：2026-10-08 11:55（北京时间）
- 审查对象：提交 `3a6d5de`（`fix(p3): review_p3_task11 修订——trace_history 过滤键（P2）+ 单写者事务收敛（P2）+ P3 全清`，HEAD）
- **核销**对象：`review/review_p3_task11_2026-10-08.md`（上一轮对 `e55c119` 的评审）的 **2×P2 + 5×P3**
- 设计依据：design §11（数据模型）/ §19（知识四查询）；plan Task 1.1；**E9**（单写者）；`docs/p3_data_audit.md`
- 性质：只读 review，未修改任何项目文件（唯一产物是本报告）
- 结论：**通过** —— 上一轮 **7/7 全部核销、无遗留**；两处 P2 的修法都**优于评审建议**（P2-2 不是「挪一行」而是抽了公共实现，顺带补齐第三形态）。本轮新增 **0×P2 + 3×P3**，都是**新引入的收口面**（新模块的最后一层保护区间、被复制的旧模式、探针计数），**无回归**

---

## 0. 核销（`review_p3_task11_2026-10-08.md` 的 2×P2 + 5×P3）

| 项 | 核验 | 结果 |
|---|---|---|
| **P2-1** `trace_history` 的 `app_build` 过滤键被静默忽略（`4cea731` 引入） | `_read_steps(app_build=_UNSET)` **按次覆盖**（`knowledge.py:91-104`）；模块级哨兵 `_UNSET` 且写明**不能**用 `None` 兼作默认值；`app_build` 加**值类型闸门**。**A/B 实测**：`git checkout e55c119 -- experience/knowledge.py` → 该文件 **3 failed / 8 passed**；还原后 **11 passed** | ✅ |
| **P2-2** 单写者事务在异常路径泄漏进程锁 | 抽 `source/sqlite_tx.py::write_tx` 作**唯一实现**（67 行），三处 store 各留**两行转口**。**A/B 实测**：旧 `_Tx` 形状冲突后 `lock.locked() == True` 且后续写 1.5s 不返回；新形状 `False`；`connect()` 抛异常时旧形状同样泄漏、新形状正常 | ✅（修法**优于建议**） |
| **P3-1** `agents/` 三处 docstring 描述「P3 完成态」 | 三处全部落地：`DEFAULT_AGENT_DB` 常量单点 + `db_path` 默认值 + 「CLI `--agent-db` **尚未接线**，归 Task 2.4」；`__init__.py` 改成**目标**表述并标注归 Task 1.3；`models.py` 写明 CI 门禁归 Task 1.3、当前由 `test_agent_models.py` 兜住。**实测三个符号仍零落点** | ✅ |
| **P3-2** 记账更正（提交信息数字错） | 新建 `docs/p3_data_audit.md`（170 行），起头更正 **24→22**、**1243→1265** 并给出**可自验的算式** `1242+23=1265`，并补上此前缺失的 Task 1.1 完成记录 | ✅ |
| **P3-3** `state` 的 SQL CHECK 词表无测试钉住 | `test_state_check_constraint_matches_enum` 真解析 `sqlite_master` DDL + **双向差集都报**；`test_state_check_rejects_bad_value_written_by_raw_sql` 断言 `IntegrityError` + 锁不泄漏 + 坏值没落库。**探针④**：模拟「枚举加第 10 态」与「SQL 多一个词」**两种漂移都判红** | ✅ |
| **P3-4** `AgentTaskState` 名实不符 | `AgentTaskState = AgentState`（真别名）。**实测**：`is` 同一对象 True / `issubclass(..., Enum)` True / 可迭代 / `AgentTaskState('IDLE')` 可构造 | ✅ |
| **P3-5** 001 SQL 头注释未列与 design §11 的差异 | 补 8 行差异表（4 处 `NOT NULL`/`DEFAULT` + `state` 的 `CHECK` + `created_at NOT NULL` + 2 索引）+「其余字段与设计逐字一致」+ 说明 `start_time`/`end_time`/`outcome` 为何**保持可空** | ✅ |

**核销小结**：7/7 全部实质落地，**无一项是「改注释交差」**。特别记两笔：

1. **P2-1 的修法把「怎么修」写进了代码里**：`_UNSET` 哨兵不只是实现细节，docstring 明写「`None` 的语义是『取全部 run』，不能兼作默认值」——这正是上一轮我担心的那处歧义，且**为「显式 None」单独补了测试**（`test_trace_history_without_scope_fails_loud_on_multiple_builds` 断言 `{"app_build": None}` 走同一条 fail-loud 路径）。评审提的是「按次覆盖」，交付的是「按次覆盖 + 哨兵语义 + 类型闸门 + 4 条测试」。
2. **P2-2 的修法比我建议的更好**：我建议「把 `BEGIN IMMEDIATE` 挪进 `try`」。实际交付抽了 `source/sqlite_tx.py` 作唯一实现，把 `graph/storage.py` 的**第三形态**（`with conn:`，**无进程锁、无 `BEGIN IMMEDIATE`**）一并补齐 —— 顺手还掉 `MEMORY.md` 登记的那笔欠账。这是「同一概念只许一处实现」纪律的正面示范。

---

## 1. 核验结果（探针实测，非仅代码阅读）

| 声称（commit message / 审计文档） | 实测 | 结果 |
|---|---|---|
| 全量 `1280 passed（+15）` | 排除 2 个在飞 TDD 红文件 → **1280 passed in 29.39s**；上一轮基线 **1265** + 15 = 1280 ✓；本提交新增 `def test_` **15 条**（knowledge 4 + sqlite_tx 5 + agent_store 5 + graph_builder 1；`test_agent_models.py` 是**加强**、不增计数） | ✅ |
| 「三处 store 各留两行转口」 | `experience/store.py:148` / `agents/storage.py:80` / `graph/storage.py:72` 三处都是 `return write_tx(self._write_lock, self._connect)`；`with self._write_tx() as conn` 调用点：experience **9** 处、agents **4** 处、graph **2** 处 | ✅ |
| 三处 store 的写**全部**走单点（无漏网） | **AST 核查**：写语句 `agents` 4/4、`graph` 6/6、`experience` 13/13 **全在 `_write_tx` 区块内**（`experience` 报出的 1 条「区外」是字符串 `"updated_at"` 的误报） | ✅ |
| `graph/storage.py` 原「无进程锁、无 `BEGIN IMMEDIATE`」（第三形态） | 现状 `_write_lock = threading.Lock()` + `write_tx`；实跑 `upsert_graph` 往返 / 幂等重放 / 源面隔离均正常（探针⑤） | ✅ |
| 新增测试是「**会红**」的钉子（不是装样子） | A/B 回退 `knowledge.py` → **3 failed**；A/B 复刻旧 `_Tx` 形状 → 新测试的 `not lock.locked()` 断言**会红** | ✅（计数见 P3-3） |
| `test_sqlite_tx.py` 5 例覆盖「成功提交 / 体异常回滚 / `connect` 抛异常 / `BEGIN IMMEDIATE` 冲突 / 并发 2×50」 | 逐条对上；冲突用例用真 `BEGIN EXCLUSIVE` 阻断 + **冲突解除后再写一次**（证明锁没被吃掉） | ✅ |
| `RuntimeGraph` 类型标注改到**定义处** | `experience/knowledge.py:34` 已是 `from graph.models import RuntimeGraph` | ✅ |
| 迁移幂等未回归 | `agents/storage.py` 构造仍复用 P2 泛化执行器；全量里 `test_migration_idempotent` 绿 | ✅ |
| 全仓无重复顶层定义 | 独立重扫 **183** 个 `.py` → **无重复**；`test_repo_hygiene.py` **3 passed** | ✅ |
| 测试没写仓库 `out/*.db` | 全量 1280 跑完后 **`out/agent.db` 不存在** ✓；唯一无参调用点 `SQLiteAgentStore()` 带 `monkeypatch.chdir(tmp_path)` | ✅ |
| `busy_timeout` 三处一致 | 实测 `PRAGMA busy_timeout`：agents **5000** / experience **5000** / graph **5000** ms —— **功能等价，但写法三种** → P3-2 | ⚠️ |
| 「回退修复后**两条**断言立刻红」 | 实测 **3 条**（多一条 `DID NOT RAISE ValueError`） → P3-3 | ⚠️ |

---

## 2. 设计 / 纪律对照

| 纪律条款 | 实现情况 |
|---|---|
| **E9 单写者**（进程锁 + `BEGIN IMMEDIATE`） | 三处 store 收敛到单点；`write_tx` 的 `_connect()` 与 `BEGIN IMMEDIATE` **都在保护区间内** ✅ |
| 「**同一概念只许一处实现**」 | `write_tx` 成为唯一实现，三处两行转口；`graph` 的第三形态一并补齐 ✅ |
| 「**已修过的 P3 不得在新模块里复制**」 | ⚠️ `agents/storage.py::_connect` 复制了 experience **已修掉**的 `timeout=30` + `PRAGMA 5000` 模式 → **P3-2** |
| 「**提交信息里的数字必须与实测对账**」 | ✅ 1280 / +15 对账通过；⚠️ 但「回退后**两条**断言红」实为 3 → **P3-3** |
| 「docstring 不得描述完成态」 | ✅ 三处全部改成「尚未落地 + 归属任务号」 |
| 「测试绝不写仓库 `out/*.db`」 | ✅ 见上表 |
| 「`source_of` 图级隔离」（P2 教训） | ✅ 未被本次改动触碰，探针⑤复验通过 |

---

## 3. 发现

### P3-1 `source/sqlite_tx.py` 的 `finally` 把 `close()` 放在 `release()` **之前且无保护** → `close()` 一旦抛异常就泄漏进程锁

**现象**（`source/sqlite_tx.py:64-67`）：

```python
finally:
    if conn is not None:
        conn.close()          # ← 若这里抛，下一行永不执行
    lock.release()
```

这与 **P2-2 是同一个失败模式**（锁泄漏 → 该 store 此后所有写**永久挂死**、审计留痕整段丢失），只差一层：P2-2 修的是「`_connect()` / `BEGIN IMMEDIATE` 在保护区间外」，这条是「`close()` 在 `release()` 前且无保护」。而本模块的**全部存在意义**就是这条不变量，docstring 却把话说到最满：

> 「退出时——成功 `commit`、异常 `ROLLBACK` 后**原样重抛**——并**必然** `close()` + `release()`。」
> 「**任何早退路径都不得泄漏事务或锁**。」

**探针证据**（探针②）：用一个「真 sqlite 连接 + `close()` 抛」的替身工厂：

```
with 块抛出: unable to close due to unfinalized statements or unfinished backups
锁仍被持有 = True            ← 期望 False
冲突后还能写 = False  (线程存活=True)   ← 后续写线程 1.5s 不返回，永久挂死
```

**诚实边界**（探针③）：用**真** sqlite3（CPython 3.11.15）构造「未 finalize 语句」场景，`close()` **没有抛** —— `pysqlite` 走 `sqlite3_close_v2`（延迟关闭、返回 `SQLITE_OK`）。所以我**只能用替身复现**，这条不是「下一个任务必然踩」，故定 **P3 而非 P2**。

**为什么现在报**：这是本轮**新引入**的唯一一处「保护区间不完整」，位置恰好在「收敛后的单点」上——收敛的好处是「这类错误只有一处可能犯」，代价是**那一处犯错的爆炸半径变成全部三个库**。修法一行，成本最低的时点就是现在。

**修法**：

```python
finally:
    try:
        if conn is not None:
            conn.close()
    finally:
        lock.release()
```

（或先 `lock.release()` 再 `close()` —— 事务已 `commit`/`ROLLBACK` 完毕，连接不再持有库锁，顺序安全。）

---

### P3-2 `agents/storage.py::_connect` 复制了 experience **已修掉**的 `timeout=30` + `PRAGMA busy_timeout=5000`；`graph/storage.py::_connect` 是第三种形态

**现象**：

| 位置 | 写法 | 实际生效 |
|---|---|---|
| `agents/storage.py:65,67` | `sqlite3.connect(..., **timeout=30**)` + `PRAGMA busy_timeout=5000` | **5000 ms** —— `timeout=30` 是**死字面量** |
| `experience/store.py:133,135` | `sqlite3.connect(..., timeout=5)` + `PRAGMA busy_timeout = 5000` | 5000 ms |
| `graph/storage.py:65` | `sqlite3.connect(self._path)`（**不传 `timeout`、不设 PRAGMA**） | 5000 ms（默认值） |

**这是「已知问题的复制」，不是新问题**：`experience/store.py:131-133` 的注释原文就是

> `# busy_timeout 单点 5s（review P3-2：connect timeout=30 与 PRAGMA 5000 曾意图不一致，后设者胜靠阅读顺序）`

同一个模式当时被报为 P3 并**已修**（`timeout=30` → `timeout=5`）；而 P3-01 新写的 `agents/storage.py` 又抄了一遍，本提交重写了该文件的**写路径**却漏了 `_connect`。

**探针证据**（探针①）：三处 store 的实际 `PRAGMA busy_timeout` 都是 **5000 ms** —— 所以**功能等价、无错误结论**，故定 **P3**；但读者会以为 agents 是 30 s。

**为什么现在报**：项目已有明文纪律「同一概念只许一处实现」，且这个具体模式**已被判过一次 P3**。三处形态并存等于把「哪个才是权威值」交给阅读顺序——这正是那条注释警告的事。

**修法**：`agents/storage.py` 对齐 experience（`timeout=5` + 保留 PRAGMA 与同一句注释）；`graph/storage.py::_connect` 补同一行 `PRAGMA busy_timeout = 5000`，让三处字面量一致。

---

### P3-3 回退探针的**红灯数**：声称「两条」，实测 **3 条**

**现象**：提交信息与 `docs/p3_data_audit.md` 都写

> 「探针复现：回退修复后**两条**断言立刻红（`2 != 3`、`[...] != []`）」

**探针证据**（A/B）：`git checkout e55c119 -- experience/knowledge.py` 后跑 `tests/integration/test_knowledge_sources.py` →

```
FAILED test_trace_history_app_build_filter_is_honored            (2 != 3)
FAILED test_trace_history_unknown_build_is_empty_not_another_build ([...] != [])
FAILED test_trace_history_app_build_type_is_gated               (DID NOT RAISE ValueError)
3 failed, 8 passed
```

还原后 **11 passed**。

**判断**：第 3 条（类型闸门）的失败形态是 `DID NOT RAISE` 而非「断言值不等」，作者可能**有意**只数「断言红」——但「回退修复后…立刻红」读起来就是「有几条测试变红」。按项目既有惯例（P3-01 两处数字错就是这么定的）应更正。

**为什么现在报**：`docs/p3_data_audit.md` 是**可改的**（提交信息不可改），而且这份文档的定位就是「给后来者的实测真值源」——数字偏小会让后来者以为覆盖更弱。

**修法**：审计文档里「两条」改为「**三条**（含类型闸门那条 `DID NOT RAISE ValueError`）」，并把三条的失败形态都列出来。

---

## 4. 未验证 / 存疑

- **P3-1 的触发条件无法用真 sqlite3 复现**（探针③：`close()` 在未 finalize 语句时仍不抛，`sqlite3_close_v2` 语义）。触发需要未来把 `_connect` 换成返回**包装对象 / `Connection` 子类**，或 sqlite3 行为变化。**这一条我按 P3 报、并明确标注了复现手段是替身**，不主张它是活跃缺陷。
- **trace 库是第四个 / 第五个 SQLite 写者**：`tracer/storage.py::TraceStore`（单条 `commit()`、长连接）与 `tracer/recorder.py` 都**无进程锁、无 `BEGIN IMMEDIATE`**。但 `source/sqlite_tx.py` 的 docstring **明确把范围限定为「三个库」**（`experience.db` / `agent.db` / `graph.db`），**没有夸大**；trace.db 是 append-only 流水库（`MEMORY.md` 已登记）。属**存量**（P1 期），不在本提交范围——是否收敛到同一纪律应由后续任务显式决策，**不建议顺手改**。
- **新引入的脚枪面**：`db_path` 有默认值之后，任何未来「忘记传路径」的调用都会写 **CWD 的 `out/agent.db`**。仓库里 `out/experience.db` 就是这么落地的（mtime 2026-10-06 23:14，非测试所写）。当前唯一无参调用点有 `monkeypatch.chdir` 保护，但「测试绝不写仓库 `out/*.db`」这条从此**靠自觉**——建议 Task 2.4 接线 CLI 时一并想清楚（例如测试夹具统一显式传 `tmp_path`）。
- **未做真机 gate**：`phase0/verify_p3_*.py` 尚不存在，Gate M1 属 Task 1.2/1.3 之后；本轮只做库内与纯函数层验证。
- **工作区仍有 2 个在飞 TDD 红测试**（`tests/unit/test_policy_config.py`、`tests/fault_injection/test_p3_matrix_m1.py`），import 的 `agents.policy_config` / `cli.main._check_autonomous_env` 仍不存在 → 全量 pytest **收集期 2 errors**。**不在本提交范围**（Task 1.2 的失败测试先行），但 Gate M1 前必须收口。

---

## 5. 值得肯定

- **P2-2 的修法比评审建议更好**（本轮最值得记的一点）：建议是「挪一行」，交付是「抽 `source/sqlite_tx.py` 作唯一实现 + 补齐 `graph` 的第三形态」。**把「修一个 bug」升级成「消掉一整类 bug 的容身处」**——正是 `MEMORY.md` 里那笔挂账，借这次 P2 一次还清。
- **`_UNSET` 哨兵的语义被写成了「理由」而不只是「实现」**：`None` = 取全部 run（原语语义）、`_UNSET` = 调用方没给（落构造值）——并且**为「显式 None」补了测试**。上一轮的 P2-1 本质就是「两个语义混用一个值」，这次是从根上分开的。
- **新测试直指原实现的零覆盖区**：`test_lock_released_when_connect_raises` 对准「`_connect()` 在 `try` 之外」这个此前**没有任何测试**的分支；`test_state_check_constraint_matches_enum` 解析 `sqlite_master` DDL 且**双向差集都报**（不只报一个方向，漂移时能立刻看出是哪边多了/少了）。
- **审计文档把「更正」写成了可自验的算式**（`1242+23=1265`）而不是只给结论——后来者能自己验，不依赖信任。
- **A/B 回退探针被写进审计文档**，而不是「我手工跑过一次」。这一轮我能独立复现它的结论（虽然计数差 1，见 P3-3），说明它可复现。
- **修 `P3-1` 时没有顺手扩权**：`graph/storage.py` 的第三形态补齐了，但 `tracer` 的第四/第五形态**没有被顺手改**——docstring 也如实把范围写成「三个库」。边界诚实。

---

## 6. 建议动作（优先级序）

1. **修 P3-1**（一行）：`finally` 里 `try: conn.close() finally: lock.release()`。它就在 `write_tx` 的**存在意义**上，且现在是收敛后唯一的犯错点。
2. **修 P3-2**：`agents/storage.py::_connect` 的 `timeout=30` → `5`（对齐 experience 并抄同一句注释）；`graph/storage.py::_connect` 补 `PRAGMA busy_timeout = 5000`。
3. **修 P3-3**：`docs/p3_data_audit.md` 的「两条」→「三条」（提交信息不可改，文档可改）。
4. **进 Task 1.2/1.3**：把 `agents/policy_config.py` 与 `tests/unit/test_tool_allowlist_gate.py` 落地——工作区那两个 TDD 红测试目前让全量 pytest **收集期就报错**，Gate M1 之前必须收口（这也是 `agents/__init__.py` / `models.py` 里三处 docstring 指向的任务）。
5. （可选）把「`_write_tx` 是唯一写入口」固化成一条 AST 断言（本轮我用临时脚本核过 13/13、4/4、6/6）——若要做，请复用 `tests/unit/test_repo_hygiene.py` 的手法，别再开第二个体检入口。

---

—— review by 小巴（探针 **9 组**：①三处 store 的 `busy_timeout` 实值 ②`write_tx` 在 `close()` 抛时的锁状态与后续可写性 ③真 sqlite3 下 `close()` 行为（复现边界） ④CHECK 词表↔枚举的双向漂移判红 ⑤图写路径往返/幂等重放/源面隔离 ⑥`app_build` 哨兵语义矩阵 ⑦五类坏值的类型闸门 ⑧AST 核查写语句是否全在 `_write_tx` 内 ⑨A/B 回退（`knowledge.py` → 3 failed；旧 `_Tx` 形状 → 锁泄漏）；全量 **1280 passed**）
