# Task 3.5 修订核销复核（round 2 / final）

- **复核对象**：`0e760fe`（round 1 修订），父 `2c6fb73`（本体）
- **复核方式**：独立只读子代理，在 `git worktree` 副本（`/tmp/rev35b` @ `0e760fe`、
  `/tmp/rev35a` @ `2c6fb73`）上做变异探针 + 新缺陷扫描。**34 组探针**全部自跑，
  结束副本 `git status` 零改动（仅 `.venv` 符号链接未跟踪）
- **未动主仓**：除本报告文件外对主仓零写入

## 结论

**有条件通过** —— 14 条中 **9 条真核销、5 条部分核销**（判据成立但钉子覆盖不全
或有未钉住的旁路）；修订**引入 2 条新缺陷（1×P1 + 1×P1）**、**暴露 2 条既有缺陷
（P2）**、另有 **7 条 P3 级文档/钉子缺口**。审计的三个核心数字（1857 / 1844+13 /
M3 Gate exit 0）**全部复现一致**，探针表 10 条中 **9 条数字一致、1 条描述不严谨**。

两条 P1 需要处置后才宜打 `checkpoint-p3-m3`：

1. **`_trace_app_id` 落在 try 之外** → 不给 `--bundle-id` 且 `--db` 是「目录 /
   非 sqlite 文件 / 多范围库」时，P1-1 想消灭的 traceback **换了一条路径复活**
   （rc=1，实测 3 种形态）。
2. **promote commit 不带 pathspec** → 正常路径下把**别人的暂存改动**一起提交；
   DB 失败回滚时 `git revert` 又**把别人的改动一起撤掉**（实测 `others_work.py`
   被提交、随后被删）。

## 逐条核销

| # | 首轮发现 | 核销 | 关键证据（我自跑的探针） |
|---|---|---|---|
| P1-1 | `--bundle-id` 缺省 → `graph_query` 穿透成 traceback | ⚠️ **部分** | `_trace_app_id` 恒空 → 1 red；去 `_trace_has_runs` 分离 → 1 red；去 `_trace_app_id` 的 exists 守卫 → 1 red；去 `_trace_has_runs` 的 exists 守卫 → 1 red；裸 `or ""` → 1 red。**钉住了。但 `_trace_app_id` 在 try 之外**（`cli/main.py:1237` vs try `:1238`）→ 见新发现 P1-A |
| P2-1 | commit 已落、记账失败 → 卡死窗口 | ✅ 真核销 | 去掉 `git revert` → **2 red**；revert 换 `reset --hard` → **1 red**；成功报错去 sha/`revert` 字样 → **1 red**；重试续传实测成功（库 ACCEPT、文件存在）。**但** revert **失败**报错去 sha → **0 red**（无钉子），见 P2-B |
| P2-2 | `save_candidate` 的 `review_status` 形参 = 「唯一写路径」名不副实 | ✅ 真核销 | 加回形参 → 1 red（`TypeError` 钉子）；INSERT 字面量改回变量 → **22 red**；DDL `DEFAULT` 去掉 → 8 red；`ON CONFLICT` 补 `review_status` → 0 red（设计正确）。全仓再无第二条 `UPDATE ... review_status=` 写路径 |
| P2-3 | AST 守卫空转（docstring 里留名字 / `if False:`） | ⚠️ **部分** | 删真调用 → 1 red ✅；`if False:` → 1 red ✅；`while False:` → 1 red；`if 0:` → 1 red。**但**嵌套未调用函数 / `if _GATE_DISABLED:` / 不可达 `except` / lambda → **AST 守卫 0 red**（plan 的功能测试兜住 2 red） |
| P2-4 | `to_store_dict` docstring 撒谎 + 无全键断言 | ⚠️ **部分** | 少 `review_note` 键 → 1 red；`review_status` 值造假 → 1 red（roundtrip 逆运算钉住）。**但** docstring 改回旧说法 → **0 red**（无钉子）；`from_store_dict` 的 `or "PENDING"` 静默兜底 → **0 red** |
| P3-1 | `original` 恢复分支是死代码 | ✅ 真核销 | `original` 仍在 `:121-122` 被「存在即 fail-loud」消费（不是死变量）；`git status` 与探针均确认删不掉 |
| P3-2 | reject 的错误映射零 CLI 覆盖 | ✅ 真核销 | reject 的 `3→7` → **2 red**（`test_candidate_reject_missing_candidate_is_exit_3` + `..._of_a_decided_candidate_...`） |
| P3-3 | 两处 `return 2` 不可达 | ✅ 真核销 | `cmd_candidate` 内两处均已删（`getattr(args,"candidate_cmd",...) != "review"` 与 `cmd not in (...)`）；恢复它们并改 `return 0`/`return 2` → **均 0 red**（不可达获证）。其余 `return 2` 属别的命令，父提交即存在 |
| P3-4 | `sqlite3.Error` 不是 `RuntimeError`，裸 `OperationalError` 漏成 traceback | ⚠️ **部分** | 5 处新增 `sqlite3.Error` 中**只有 `list` 分支有钉子**（去掉 → 1 red）；store 构造 / export / accept / reject 四处 → **各 0 red** |
| P3-5 | `export_candidate_patch` 裸 `yaml.safe_load` | ✅ 真核销 | 去掉 `try/except CandidateReviewError` → **1 red**（`test_candidate_export_patch_with_broken_yaml_is_exit_3`） |
| P3-6 | gate docstring 说 G6 是 exit 3、代码断言 2 | ✅ 真核销（**未钉住**） | docstring 已改 `exit 2`（`phase0/verify_p3_m3.py:21`），`g6_reject_requires_note` 断言 `returncode == 2`。**改回 `exit 3` → 0 red**（docstring 无钉子） |
| P3-7 | 「Dry Run 观测写进图源库」未登记 | ✅ 真核销（钉子偏宽） | 去掉 `start_run` → **2 red**；前缀改 `zz_` / `r_` → **各 2 red**。**但**改 `genx_` → **0 red**：`LIKE 'gen_%'` 的 `_` 是 LIKE 通配符，匹配范围比声明的宽 |
| P3-8 | `end_run` 在 finally 之外 → trace 留 `RUNNING` | ✅ 真核销 | 彻底不写 `end_run(ABORTED)` → **1 red**；实测异常路径 run 行 `[{'status':'ABORTED','end_time':...}]`、rc 3、无 traceback。**追问已答**：`stack.close()` 在异常路径**被调用 1 次**（`finally` 在 `return 3` 前执行，不泄漏）——临时探针实测 `stack.close called: 1` |
| P3-9 | 「落盘 = 库里的文本」只在规范文本下成立 | ✅ 真核销（钉子强度够） | 落盘改写真库文本 → **1 red**（`landed != flow`）；`dump` 把 `name` 置空 → **1 red**（语义等价断言）。两条都在**改实现**而非删测试的前提下变红 |

## 新发现（本轮修订引入 / 暴露）

### P1-A（新，本轮引入）：`_trace_app_id` 在 try 之外，traceback 换路径复活
`cli/main.py:1237`：
```python
app_id = args.bundle_id or _trace_app_id(args.db)     # ← try 之外
try:
    graph = (knowledge.graph_query(app_id) if _trace_has_runs(args.db) ...)
except (ValueError, sqlite3.DatabaseError) as e:       # ← 接不住上一行
```
`_trace_app_id` 内部调 `read_trace_steps`（`sqlite3.connect` + 查询），会抛
`OperationalError` / `DatabaseError` / `ValueError`——**全在 try 之外**。
实测（给真 plan、不给 `--bundle-id`，执行确实走到该行）：

| 输入形态 | rc | 结果 |
|---|---|---|
| `--db` 指向目录 | **1** | `sqlite3.OperationalError: unable to open database file` → traceback |
| `--db` 指向非 sqlite 文件 | **1** | `sqlite3.DatabaseError: file is not a database` → traceback |
| `--db` 多范围（两个 app_id） | **1** | `ValueError: trace 里有多个 (app_id, app_build) 范围` → traceback |

对照：同样三种形态**给了** `--bundle-id` 时（走 try 内的分支）→ 均 rc 3 +
`PREFLIGHT ERROR`。即 P1-1 声称「不再 traceback」只在「给了 `--bundle-id`」
或「库恰好正常」时成立。定级 **P1**：与 P1-1 同一契约（前置错误 exit 3），
且 `--fake-driver` + 本地库路径正是文档自称「最常用的本地路径」。

### P1-B（新，本轮引入）：promote commit 不带 pathspec → 连带提交别人的暂存改动
`candidates/review.py:136-143`：`run_git(repo_root, "add", rel)` 后
`run_git(repo_root, "commit", "-m", ...)`——**commit 不带路径**。实测：

- 正常路径：别人 `git add others_work.py`（未提交）→ promote commit 的
  `show --name-only` **含 `others_work.py`**，库状态 ACCEPT。
- 回滚路径：DB 失败 → `git revert` 把 `others_work.py` **一起删掉**
  （`others_work.py 内容: <不存在>`，`status: ''`）。

定级 **P1**：不可逆地把第三方的在途工作提交/撤销，且无任何提示。
（对比：`target.relative_to` 那道 fail-loud 说明作者知道「只该动自己的文件」，
只是没在 commit 上贯彻。）

### P2-C（本轮暴露）：`git revert` 在脏工作区下会撤掉别人的改动
实测三种前置（均 DB 失败触发回滚）：工作区干净 → revert 成功；
工作区有未提交改动 → revert **也成功**（git 2.54，`revert` 对该文件无冲突）；
暂存区有别人的新文件 → revert 成功并**删掉**该文件。
上一条的撤销侧即本条。定级 **P2**：比 P1-B 窄（只在回滚路径触发），
但同属「动别人的东西」。

### P2-D（本轮暴露）：AST 守卫对 4 种规避形态仍空转
`_dead_branches` 只判 `ast.Constant` 为假的 `if`/`while`。实测 **0 red**：

| 规避形态 | AST 守卫 | plan 功能测试 |
|---|---|---|
| 真调用搬进本函数内的嵌套函数（无人调） | 0 red | 2 red |
| `if _GATE_DISABLED:` | 0 red | 2 red（`_GATE_DISABLED` 未定义 → 21 red，是 NameError 不是守卫） |
| 搬进不可达 `except Exception:` | 0 red | 2 red |
| 包进 `lambda`（无调用方） | 0 red | 2 red |

`cmd_generate` 有 sandbox 后置兜底，`cmd_plan` 也实测有功能测试兜住，
故**当前无实际漏防**；但 P2-3 的判据仍能被「不做事」的改动满足（守卫空转族）。
定级 **P2**。

### P2-E（既有，本轮未修）：`from_store_dict` 的 `or "PENDING"` 静默兜底
`candidates/test_case.py:134`：`review_status=row.get("review_status") or "PENDING"`。
库里 `review_status=''` 会被静默读成 `PENDING`——与 P2-2「`review_status` 的
唯一写路径」的严格性不对称。探针（把 `or "PENDING"` 改成 `row.get(..., "")`）
**0 red**。定级 **P2**（属「静默兜底」雷区，非本轮引入）。

### P3-F：P3-4 的 5 处 `sqlite3.Error` 只有 1 处有钉子
`list` / store 构造 / export / accept / reject 五处，只有 `list` 一处
（`test_candidate_list_on_an_unreadable_db_is_exit_3`）能红。其余四处
「改成任意别的值都不红」。定级 **P3**。

### P3-G：P3-6 的 docstring 修订无钉子
把 G6 的 docstring 改回 `exit 3` → **0 red**。gate 的判据与它的说明文字之间
没有自动一致性检查，同一族的谎可以再撒一次。定级 **P3**。
**同族新增**：`g6_reject_requires_note` 的 docstring 写「且**不留任何决策痕迹**」，
但 `check(...)` 只断言 `returncode == 2 and "--note" in body`——**「不留痕迹」
完全没有实现**（代码读证）。

### P3-H：P3-7 钉子的 `LIKE 'gen_%'` 里 `_` 是通配符
`tests/unit/test_cli_generate.py:572`。改 run_id 前缀为 `genx_` → **0 red**
（`genx_abc` 匹配 `gen_%`）。钉子比它声明的「`gen_*` run 行」宽一格。
定级 **P3**（当前无害，但语义与文字不符）。

### P3-I：`phase0/verify_p3_m3.py:214 mta_candidate` 是死代码
全仓零调用方（`grep -rn mta_candidate --include "*.py"` 只命中定义本身）。
定级 **P3**。

### P3-J：同类前置错误在 `generate` 上两个出口码
G1（F7 / production）实测 **exit 1**（`SystemExit(str)`），G2（staging）实测
**exit 3**。而 `cmd_generate` 的注释自称「退出码契约只有 0/3」。
定级 **P3**：**非本轮引入**（`cmd_plan` 同款，`test_cli_plan.py` 也只断言
`SystemExit` 不断言码），但 Gate 把它印在输出里了，值得登记。

## 数字对账（审计 vs 我自跑）

| 声称 | 实测 | |
|---|---|---|
| 全量 `pytest tests` = **1857 passed** | **1857 passed**（30.55s / 复跑 32.03s） | ✅ |
| 修订前 1844，**+13** | 父提交 `2c6fb73` 副本实跑 **1844 passed** | ✅ |
| M3 Gate `verify_p3_m3.py` → **exit 0**，G1–G8 全绿 | **exit 0**，G1–G8 全 PASS（G8 复跑 1857） | ✅ |
| 探针 R1 = 2 failed | **2 red** | ✅ |
| R2 = 1 failed | **1 red** | ✅ |
| R3 = 1 failed | **1 red** | ✅ |
| R4（`end_run` 移出异常分支）= 1 failed | 「移到 `finally`」→ **0 red**；「彻底删掉」→ **1 red** | ⚠️ 描述不严谨（前者是等价变异） |
| R5 = 2 failed | **2 red** | ✅ |
| R6 = 0 red | **0 red**（`return 0` 与 `return 2` 两种恢复均为 0） | ✅ |
| R7 = 1 failed | **1 red** | ✅ |
| R8 = 0 red | **0 red** | ✅ |
| P2-3a = 1 failed | **1 red** | ✅ |
| P2-3b = 1 failed | **1 red** | ✅ |

R6 / R8 的 0 red 确认是「删对了」的证据，不是漏测——两条我都独立复现。

## 探针汇总（本轮共 34 组，全部在副本上做、结束已还原）

| 组 | 变异 | 红 |
|---|---|---|
| P1-1 组（5） | 裸 `or ""` / `_trace_app_id` 恒空 / 去 `_trace_has_runs` 分离 / 去 `_trace_app_id` exists 守卫 / 去 `_trace_has_runs` exists 守卫 | 1 / 1 / 1 / 1 / 1 |
| P1-1d | `_trace_has_runs` 去掉 `mode=ro` | 0（等价变异：文件已存在，无所谓建不建） |
| P1-A 组（4 形态） | 真 plan + 不给 `--bundle-id` + 坏 `--db`（目录/非sqlite/多范围/正常） | **rc 1 traceback ×3**，正常 0 |
| P2-1 组（4） | 去 revert / revert→reset / 失败报错去 sha / 成功报错去 sha | 2 / 1 / **0** / 1 |
| P2-1 场景（3） | 正常路径连带提交 / 回滚路径连带删除 / 脏工作区 revert | 实测：提交 ✅ / 删除 ✅ / 成功 ✅ |
| P2-2 组（4） | 加回形参 / INSERT 字面量 / DDL DEFAULT / ON CONFLICT | 1 / 22 / 8 / 0 |
| P2-3 组（8） | 删调用 / `if False` / `while False` / `if 0` / 嵌套函数 / 恒假变量 / 不可达 except / lambda | 1 / 1 / 1 / 1 / **0** / **0** / **0** / **0** |
| P2-4 组（4） | 缺键 / docstring 复撒谎 / 空串兜底 / 值造假 | 1 / **0** / **0** / 1 |
| P3-2 / P3-5 | reject `3→7` / export 不包 YAML 错误 | 2 / 1 |
| P3-4 组（5） | list / store 构造 / export / accept / reject 去掉 `sqlite3.Error` | 1 / **0** / **0** / **0** / **0** |
| P3-6 | gate docstring 改回 `exit 3` | **0** |
| P3-7 组（4） | 去 `start_run` / 前缀 `zz_` / `r_` / `genx_` | 2 / 2 / 2 / **0** |
| P3-8 组（2） | 彻底不写 ABORTED / 移进 `finally`；外加 `stack.close` 观测探针 | 1 / 0；**close 调用 1 次** |
| P3-9 组（2） | 落库文本 / `dump` 丢 `name` | 1 / 1 |
| R1–R8、P2-3a/b | 见上表 | 见上表 |

## 未验证 / 存疑（复核者自陈）

1. **真机路径未跑**（无设备）：`--bundle-id` 必填那条分支、真机装配、`udid`
   解析均无端到端验证。
2. **P2-3 的 4 种规避形态只在 `cmd_plan` 侧验证了功能兜底**；`cmd_generate`
   侧我推断有 sandbox 兜底但未逐形态实测。
3. **P2-1 的触发概率未量化**（DB 失败需 `database is locked` 等并发/磁盘故障）。
4. **并发场景未测**：两个 `mta candidate review accept` 同时跑、或 accept 期间
   别人正在 `git commit`，revert 的冲突形态未知。
5. **未复跑首轮 35 组探针全表**，只跑与本 14 条相关的 34 组。
6. **`sqlite3.Error` 家族覆盖未穷举**：只验了 `OperationalError` / `DatabaseError`，
   `IntegrityError` / `ProgrammingError` 未测。
7. **迁移对既有行的回填**未验证（只在新建库路径验证）。
8. **P3-J 的 exit 1/3 不一致**若团队视为契约问题，需另立条目统一
   （我按「非本轮引入」定 P3，未改任何代码）。
9. `git` 版本相关：`revert` 在脏工作区的行为是 git 2.54 实测，其他版本可能不同。
