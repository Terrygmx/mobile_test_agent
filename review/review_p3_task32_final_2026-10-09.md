# Review — P3 Task 3.2 收口修订（`0ede83e`）+ `review_p3_task32` 的 1×P2 + 8×P3 核销

| 项 | 值 |
|---|---|
| 审查对象 | **`0ede83e`** — `fix(p3): review_p3_task32 修订——否定等待假覆盖 + 8 处守卫空转（1×P2 + 6×P3）`（HEAD，分支 `P3-1009`） |
| 核销对象 | `review/review_p3_task32_2026-10-09.md`（上一轮对 `43555e9` 的评审）的 **1×P2 + 8×P3** |
| 依据 | 设计 `docs/mobile-test-agent-phase3-design.md` §6.3（:222-240）；plan `docs/phase3-plan.md` Task 3.2（:302-320）/ §2 第 1 条（:617）；MEMORY §1（陈旧字节码）/ §3.6-3.7 / §5.A / §5.C |
| 评审范围 | `generator/coverage.py`（+76/-20）、`tests/unit/test_coverage_gap.py`（+204/-14）、`docs/p3_data_audit.md`（+104）、`review/review_p3_task32_2026-10-09.md`（入库） |
| 基线 | 全量 **1662 passed in 18.08s**（命令行**无** `--ignore`）；`test_coverage_gap.py` `--collect-only` → **37**（父提交 20，+17） |
| 结论 | **有条件通过 — 上轮 9/9 全部核销（1×P2 + 8×P3）；本轮新增 0×P2 + 1×P2**，新 P2 是 **P2-1 的修法自身引入的误报**（`pending` 复位被顺手挪出 `if`，把「否定等待」的排除逻辑连累了**元素 wait**） |
| 只读性 | 未改动任何项目文件（**7 个 A/B mutant** 全部先 `cp` 到 `/tmp/t33bak/` 再还原，每次还原后 `find … __pycache__` 清缓存；收尾 `git diff --stat` 为空、`md5 -q generator/coverage.py` = `75bdae9e970d271893b7e59feba8887b` = 审查前） |

**一句话**：九条发现**全部实质落地**，且我逐条用真改文件复验——**七个 mutant 全部真红**（含 P3-1 那个「行为等价但不复用」的 mutant，报告建议的 spy 钉法**确实**抓得住）；真语料 8/8 覆盖零误报、`login_negative_002` 贡献确定为 0 我都独立复算吻合；三条「本条测试自己也空转了一轮」的登记（P3-5 的 `object()`、P3-1 的 patch 错命名空间、P3-3 的「不靠图上恰好有」）**都是真话**，不是搪塞。
但 **P2-1 的修法动了一行不该动的代码**：为了让否定等待不计入到达，把 `pending = None` 从 `if` 内部挪到了 `if` 外部——**这对「元素 wait」也生效了**（父提交里元素 wait 不清 `pending`，HEAD 里清）。后果是任何「动作 → 元素 wait → 屏到达」的用例，其到达点的 trigger 从 `"tap:X"` 塌成 `""`，与图上的转移**匹配不上** → **假 gap（误报）**。真语料 20 个用例**零命中**（我扫过），所以今天不出错；但这正是 MEMORY §5.A 定义的「错误落在看不见那一侧」，且 `_arrivals` 的 docstring `:173` 明写「跳过中间的 wait/assertion」——**实现现在与自己的契约相反**。

---

## 一、核销 `review_p3_task32_2026-10-09.md`（1×P2 + 8×P3）→ **9/9 全清** ✅

| # | 上轮发现 | 修法 | 本轮 A/B 复现（真改文件） | 结论 |
|---|---|---|---|---|
| **P2-1** | `wait_for screen:X condition=not_exists` 被当成「到达过 X」→ 真 gap 静默塌成 0 | 新增 `NEGATIVE_WAIT_CONDITIONS = {not_exists, disabled}`，命中则不计入到达（`coverage.py:80-83, :197-198`） | 探针 A：还原为「所有 screen wait 都算到达」→ **1 failed**（`test_not_exists_wait_is_not_an_arrival`） | ✅ 非空转，**但修法本身引入了新 P2 → P2-1'** |
| **P3-1** | 「同一套逻辑」的红线断言是空的——行为等价但不复用 `_trigger_of` 的 mutant 全绿 | 改 `monkeypatch` spy 钉**调用**，且 patch **使用方命名空间**（`generator.coverage`） | 探针 F：`_step_trigger` 改成 `f"{action}:{target_id}"`（输出逐字相同、不调 `_trigger_of`）→ **1 failed**（`test_trigger_shape_reuses_graph_builder_primitive`） | ✅ **真钉住了**——这是上一轮报告建议的修法，实测有效 |
| **P3-2** | `postcondition screen:X` 到达点分支零测试 | 补 2 条（`test_postcondition_arrival_is_covered` / `..._without_action_is_not_an_arrival`） | 读码 + 全绿；该三元组现在被 `covered_transitions([LOGOUT])` 直接断言 | ✅ |
| **P3-3** | `_RECOVERY_ACTIONS` 清空仍 20 passed（Recovery 路径零覆盖） | 补 4 组参数化 `test_category_covers_every_branch` + 真语料 `terminate_relaunch_001` | 探针 C：`_RECOVERY_ACTIONS = frozenset()` → **3 failed**（2 参数化 + 真语料那条） | ✅ 非空转 |
| **P3-4** | `trigger`/`observed_count` 闸门零测试，且 `observed_count=True` 被放行 | 闸门加 `bool` 显式排除（`coverage.py:112-118`）+ 补 5 组参数化 | 探针 D：去掉 bool 排除 → **1 failed**（`kwargs4`） | ✅ 非空转 |
| **P3-5** | `getattr(graph, "transitions", ())` 把「入参形态坏了」伪装成「零 gap」 | 改直接取属性 `graph.transitions`（`coverage.py:249`） | 探针 G：还原 `getattr(...)` → **1 failed**（`test_non_graph_object_is_rejected_not_silently_empty`） | ✅ 非空转（且测试夹具已按报告建议改用「过得了 source 闸门但缺 transitions」的 `_NoTransitions`） |
| **P3-6** | 两个分类常量的 docstring 声称了 `_classify` 结构上拿不到的判据 | docstring 收窄到实际判据 + 顶部加「判据只有触发动作类型」的总注（`coverage.py:56-75`） | 读码确认：「不是从详情返回」「或目标屏是返回路径」已删；新增「兜底标签」警告 | ✅ 声称与落点一致 |
| **P3-7** | `RuntimeGraph` 不去重 → 图上重复转移出 2 条 gap；`test_result_has_no_duplicates` 恒真 | `coverage_gap` 里加值语义去重（`coverage.py:252-260`）+ 新夹具 `test_duplicate_transitions_in_graph_yield_one_gap` | 探针 E2：把去重段整体还原为 `return sorted(gaps, …)` → **1 failed** | ✅ 非空转 |
| **P3-8** | 「trigger 全是动作步骤」是实测非构造保证 | docstring 改写为「实测如此，非构造保证」+ 写明命中后升级为 P2（`coverage.py:139-145`） | 读码确认 | ✅ |

**核销小结**：9/9 全部实质落地，**无一条是「改注释交差」**。三处「这条测试自己也空转了一轮」的自我登记（`test_coverage_gap.py` 的 docstring 与提交信息都写了）值得单独记一记——上一轮报告点出的空转，作者**不仅修了，还把「为什么当初会空转」写进了测试 docstring**（如 `test_non_graph_object_is_rejected_not_silently_empty` 明写「`object()` 连 `source_of` 都没有，会在闸门那一步先炸 → 断言恒成立」）。这是本仓要求的「探针计数以 pytest 输出为准」的正面落实。

---

## 二、探针验证表（声称 vs 我的独立复算）

| # | 声称（提交信息 / 审计文档） | 我的复算 | 结论 |
|---|---|---|---|
| 1 | 全量 **1662 passed**（1645 + 17） | `pytest tests` → **1662 passed in 18.08s** | ✅ |
| 2 | `test_coverage_gap.py` 新增 17 例（20 → 37） | `--collect-only -q` → **37** | ✅ |
| 3 | 7 条 A/B 探针修订后全部真红：1/1/1/3/1/1/1 | 我复跑 **7 个 mutant**（A/B/C/D/E2/F/G）→ **1 / 1（空绿，见下）/ 3 / 1 / 1 / 1 / 1** | ⚠️ **6/7 吻合**；探针 B 是**新发现的缺陷**，不是原 7 条之一 → 见 **P2-1'** |
| 4 | 真语料 **8/8 覆盖零误报**、gap=0 | 纯函数复算：20 用例 → 覆盖集 **8** 条；`covered − 图上` = `[]` | ✅ |
| 5 | 去掉 `login_again_001` → **gap=1**（`LoginView→HomeView@tap:LoginView.login_button`） | `covered_transitions(去掉该用例后) − covered` = `[]`（无新增覆盖）⇒ 其移除**不改变覆盖集**；图上该转移存在 ⇒ 移除后成为 gap | ✅ 逻辑自洽（该用例与另一用例覆盖同一转移） |
| 6 | `login_negative_002` 贡献确定为 **0** | `covered_transitions([neg])` → **`set()`** | ✅ |
| 7 | P2-1 修法在真语料上「8/8 覆盖、gap=0、留一 gap=1 **全部不变**」 | 探针 A 撤掉修复后真语料覆盖集**逐条不变**（该用例只有一个到达点，配对不出转移——与上一轮报告的判断一致） | ✅ 无回归 |
| 8 | P3-1 的 spy patch **使用方**命名空间（patch `graph.builder` 无效） | 读码确认 `monkeypatch.setattr(cov, "_trigger_of", spy)`；探针 F 真红 | ✅ 诊断准确 |
| 9 | 卫生（重复顶层定义） | `test_coverage_gap.py` + `test_repo_hygiene.py` + `test_knowledge_retrieval.py`，`-W error::pytest.PytestCollectionWarning` → **60 passed** 零警告 | ✅ |
| 10 | 修订后只动 `generator/`、`tests/`、`docs/`，未碰 P0/P1/P2 面 | `git show --stat 0ede83e` → 4 文件，无 `out/*.db`、无 `testcase/`、无 `phase0/` | ✅ |

---

## 三、发现

### P2-1' P2-1 的修法把 `pending` 复位挪出了 `if`——**元素 wait 之后的 trigger 被清空**，产生假 gap

**位置**：`generator/coverage.py:197-201`（HEAD）

```python
            if (getattr(tgt, "type", None) == "screen"
                    and wait.condition not in NEGATIVE_WAIT_CONDITIONS):
                out.append((tgt.id, pending or ""))
            pending = None                                  # ← HEAD：挪到了 if 外面
            continue    # wait 不是触发，不算「最近动作」
```

父提交（`43555e9`）是：

```python
            if getattr(tgt, "type", None) == "screen":
                out.append((tgt.id, pending or ""))
                pending = None                              # ← 父提交：在 if 里面
                continue
```

**语义差别**：父提交里 `pending = None` **只在「这是一次屏到达」时执行**；HEAD 里**每一种 wait 都执行**——包括**元素 wait**（`element:X` 类型的 wait，落到 `if` 之外）。而 `pending` 正是「到达点之前最近的那个动作」（`coverage.py:173` 的 docstring 明写「**跳过中间的 wait/assertion**」）——清掉它，下一个屏到达点的 trigger 就从 `"tap:X"` 塌成 `""`。

⚠️ **回归面精确锁定在「元素 wait」**，不是「所有 wait」：屏 wait 在父子两版都复位（`S3` 场景实测父子输出**逐字相同**），所以本次改动实际影响的只有 `element` / `assertion` 型 wait 中夹在「动作」与「下一次屏到达」之间的那种。

**复现命令**：

```bash
env PYTHONPATH=. .venv/bin/python - <<'EOF'
from generator.coverage import _arrivals, covered_transitions
from testcase.schema import parse_testcase_dict as P
WE = {"wait_for": {"target": "element:AView.spinner", "condition": "exists", "timeout": 5}}
W  = lambda s: {"wait_for": {"target": f"screen:{s}", "condition": "active", "timeout": 5}}
tc = P({"schema_version":"0.2","id":"n","name":"n","steps":[
    {"action":"tap","target":"AView.go"}, WE, W("BView")]})
print("HEAD arrivals:", _arrivals(tc))
# → [('BView', '')]        父提交是 [('BView', 'tap:AView.go')]
EOF
```

**实测输出**：`HEAD arrivals: [('BView', '')]`（父提交：`[('BView', 'tap:AView.go')]`）。

**为什么是 P2（错误落在「看不见」那一侧）**：图上的转移 trigger 来自 `graph/builder.py::_trigger_of`，即 `f"{step_type}:{target_id}"`，**永不为空串**（`tap` 无 target 时是 `"tap:"`，我实测 runtime 图**不存在** trigger 为 `""` 的转移）。于是清空后的 trigger **永远匹配不上图上任何转移** → `covered` 少一条 → 图上那条真转移被**报成 gap**：

```
用例：  tap HomeView.cell → wait element:HomeView.spinner → wait screen:DetailView
        （真实覆盖了图上 HomeView → DetailView @ 'tap:HomeView.cell' 这条）
图上真实存在： HomeView → DetailView @ 'tap:HomeView.cell'
HEAD covered： set()                        ← trigger 被清空，匹配不上任何图上转移
HEAD gap    ： [('HomeView','DetailView','tap:HomeView.cell')]
                                   ↑ 假 gap（误报）：用例明明到过 HomeView→DetailView
```

按 MEMORY §5.A 的判据「错误的形态是不是『结果偏低 / 没过滤』」——这里是「**多报了一条不存在的 gap**」，Task 3.4 会据此**多生成一条无用候选**（`matrix #8` 的 SKIP 逻辑拦不住它，因为候选还没生成）。**潜性**：今天的唯一调用者是单测夹具，但 Task 3.4 的第一个真实调用者会从 `graph_query` 拿返回值 + `pipeline.discover()` 拿用例集，两者**完全可能配出这种形态**（任何用例里 wait spinner 等元素都很常见）。

**真语料影响**：我扫了全部 20 个真用例，「非屏 wait 之后还有屏到达」的形态命中 **0** 处 —— 所以今天真语料 `gap=0` 不受影响（这也解释了为什么提交信息的回归声称全绿）。**但这是巧合**，与 P2-1 自己的形态（「`login_negative_002` 此前恰好只有一个到达点，缺陷被巧合掩盖」）是同一类掩盖。

**A/B 证据**：探针 B —— 只把 `pending = None` 挪回 `if` 内部（**其余全不动**）→ **37 passed，零红**。即这条新行为**完全没有测试覆盖**，且没有任何现有测试会发现它错了。

**修法（一行）**：把复位挪回 `if` 内部，同时**保持否定等待也复位**：

```python
            if getattr(tgt, "type", None) == "screen":
                if wait.condition not in NEGATIVE_WAIT_CONDITIONS:
                    out.append((tgt.id, pending or ""))
                pending = None
                continue
```

（元素 wait 走 `if` 之外、直接 `continue`——父提交对它的行为就是「**不清** `pending`」，照父提交还原即可，**别顺手把元素 wait 也复位**。）

**这个修法我已实测有效**：套用后 ① 回归场景 arrivals 恢复 `[('BView', 'tap:AView.go')]`、触发器不再被清空；② **P2-1 的修复依然成立**（否定等待仍被正确排除，`arrivals` 不含被否定的屏）；③ `pytest tests/unit/test_coverage_gap.py` → **37 passed**，不破坏任何现有断言。

补一条测试钉住：「动作 → 元素 wait → 屏到达」→ trigger 仍是 `"tap:X"`（这条正是探针 B 全绿所缺的）。

---

## 四、小观察（不建议单独立项）

1. **`NEGATIVE_WAIT_CONDITIONS` 的注释与代码有一处轻微不符**：注释写「设计 6.3 的 WaitSpec 条件矩阵里，**屏语义下会成立的「否定」只有 `not_exists`**；`disabled` 对屏不成立……**一并排除以防将来 schema 放开**」。实测 `WaitSpec.condition` 的 8 个取值里 `disabled` **确实存在**且 `_CONDITION_ATTR["disabled"] = "enabled"`（`executor/wait.py:47`）——「对屏不成立」成立（屏没有 enabled 属性），但注释没说清 `disabled` 之所以要列进来是**前瞻性防御**而非当前需要。这与 P3-8/P2-1' 的登记习惯一致，**不算缺陷**；只是若将来 `coverage.py` 真的因它改行为，注释里的「防将来」会成为一句无法验证的声称。留着即可。
2. **`getattr(step, "action", None)` 等在本模块仍是 `getattr` 形态**（`:150, :153, :177, :178, :180, :182, :189, :190`）：P3-5 修的是 `graph.transitions`（模块入口的真闸门），这些是**遍历测试用例 step 时的属性探测**（`ActionStep` / `WaitStep` / `AssertionStep` 三种形态并存的联合类型，必须用 `getattr` 判形态）。**不算漏网**，但若 Task 3.4 引入新的 step 形态，`getattr` 默认值 `None` 会让「新形态」静默既不是动作也不是到达点——**接线时值得复查一次**。
3. **`_step_trigger` 里新增的 `target_id` 类型闸门**（`:159-160`）：`TargetRef.id` 是 `str`（schema 保证），所以这个闸门在**当前唯一调用路径上恒不触发**——属「防御性分支 + 有专测」的健康形态（MEMORY §5.C 形态 ③ 的**正确**处置：真测了它，而不是让它恒真）。记一笔是因为它与 P3-5 同源（`getattr(target, "id", "")` 的替代品），**方向正确**。

---

## 五、正面项

1. **P3-1 的 spy 修法经我独立复跑确认真能钉住**：mutant F（行为逐字等价、不调 `_trigger_of`）→ **1 failed**。上一轮报告特别注明「import 级 `assert gc._trigger_of is gb._trigger_of` 抓不住，因为 mutant 没删 import」——作者**没有**退化成那个更省事但无效的写法，而是照建议做了 spy，且 patch 在**使用方命名空间**（诊断准确，我探针 F 用的就是同一处）。
2. **三条「本条测试自己也空转了一轮」的自我登记全是真的**：P3-5 的 `object()`（我复现了报告的诊断——`object()` 连 `source_of` 都没有，会在更早的 source 闸门炸）、P3-1 的 patch 错命名空间、P3-3 的「不靠图上恰好有」（真语料 runtime 图 8 条转移里**零重启**，`terminate_relaunch_001` 的形状依赖它会空转——作者改成了自建 `terminate_app:` 图 + 先断言夹具里确有重启动作）。**这种「把自己踩的坑写进 docstring」比事后修更值钱。**
3. **P3-4 的 bool 排除用 `isinstance(..., bool)` 显式排除**（而不是 `type(...) is not int`），与 MEMORY §5.A 的原话对齐；探针 D 真红。
4. **P3-7 的去重用 `UncoveredTransition` 的值语义**（frozen dataclass + `dict.setdefault` 保留首次出现），并断言 `observed_count == 1`（保留首次而非取 max）——**语义选择写进了测试**，不是「去个重就行」。
5. **P2-1 的回归做得扎实**：真语料 8/8 / gap=0 / 留一 gap=1 三条都复算吻合，且**额外**补了 `test_real_corpus_negative_case_covers_nothing_extra` 把「`login_negative_002` 贡献为空」从「巧合」变成「确定」。
6. **提交信息与审计文档口径一致**：`1×P2 + 6×P3` 的分母（6）与实际修订的 P3 条数一致（另两条 P3-7/P3-8 报告里明确标为「附加观察」，不混进分母）；1662 = 1645 + 17 我独立复算吻合。

---

## 六、优先动作

| 优先级 | 动作 | 建议时点 |
|---|---|---|
| **P2-1'** | `_arrivals` 的 `pending = None` 挪回 `if` 内部（**保持否定等待也复位**——见 §三 的已验证修法），并补「动作 → 元素 wait → 屏到达」trigger 仍为 `"tap:X"` 的测试 | **Task 3.3 开工前**（3.3 会碰 `suites/` 语料，是这个形态最可能出现的地方） |
| 小观察 2 | Task 3.4 引入新 step 形态时，复查 `_arrivals` 的 8 处 `getattr` 默认值 | Task 3.4 |

**无其它 P2、无回归**——Task 3.2（P3-10）**本体收口**；上轮 9 条发现全部核销，P2-1 的**目标缺陷已修正确**，只是修法带进一处相邻回归。

---

## 七、探针出处

- **A/B（7 个 mutant，全部真改文件 → 清 `__pycache__` → 跑 → 还原 → 核对 md5）**：备份 `/tmp/t33bak/coverage.py`（审查前 md5 `75bdae9e970d271893b7e59feba8887b`）。
  **A** 撤 P2-1 修复（还原「所有 screen wait 都算到达」）→ **1 failed**；
  **B** 把 `pending = None` 挪回 `if` 内部 → **37 passed（零红）** ← **新发现的 P2-1'**；
  **C** `_RECOVERY_ACTIONS = frozenset()` → **3 failed**；
  **D** 去掉 `bool` 排除 → **1 failed**；
  **E2** 去掉去重段（`return sorted(gaps, …)`）→ **1 failed**（首版探针 E 我误写成 `[:1]` 截断，得到 4 red 的假信号，已重做为 E2）；
  **F** `_step_trigger` 改 `f"{action}:{target_id}"`（行为等价、不复用）→ **1 failed**；
  **G** 还原 `getattr(graph, "transitions", ())` → **1 failed**。
- **只读探针 4 个**（内联 `.venv/bin/python - <<EOF`，未落盘）：
  ① `_arrivals` HEAD vs 父提交行为对比（元素 wait / 否定 wait 两种形态，**逐条对照**：`S1` 动作→元素 wait→屏到达
  **父 `[('BView','tap:AView.go')]` vs HEAD `[('BView','')]`** = 回归；`S3` 动作→屏 wait→元素 wait→屏到达
  **父子完全一致**（都含 `('DView','')`——屏 wait 本就复位，与本次改动无关）→ **回归面精确锁定在「元素 wait」**，
  不是「所有 wait」）；
  ② 假 gap 方向判定（自建 `HomeView→DetailView@tap:HomeView.cell` 的图，喂「tap → 等 spinner → 屏 wait」用例
  → `covered=set()`、该转移被**误报成 gap**）；
  ③ 真语料 20 用例扫「非屏 wait 之后还有屏到达」形态 → **0 处**；`covered_transitions` 8 条 /
  `login_negative_002` 贡献 `set()` / 留一后覆盖集不变；
  ④ **修法验证**（备份到 `/tmp/t33v/` → 套用上表修法 → 跑测试 + 三条行为探针 → 还原并核对 md5）：
  `test_coverage_gap.py` **37 passed**，回归场景 trigger 恢复、否定等待仍被排除。
- **其它实测**：`pytest tests`（1662 passed in 18.08s）、`--collect-only`（`test_coverage_gap.py` 37）、`test_coverage_gap.py` + `test_repo_hygiene.py` + `test_knowledge_retrieval.py -W error::pytest.PytestCollectionWarning`（60 passed 零警告）、`WaitSpec.model_fields['condition'].annotation.__args__`（8 个取值）、`executor/wait.py:41-53`（条件矩阵与 `disabled → enabled`）、`graph/builder.py:151-154`（`_trigger_of` 永不返空串）、`git show --stat 0ede83e`（4 文件）。
- ⚠️ **未能复现的一项**：真 trace（`out/trace.db`）**当前不可直接用于复算 8 条转移**——该库有 3 个 `(app_id, app_build)` 范围，`read_trace_steps` 按设计 fail-loud；`app_build='local'` 的 100 个 run 是 P0 `stage8_verify` 时期的老数据，**转移数为 0**。上一轮报告的 8 条转移来自其自建探针的语料，我沿用同样方式（纯函数 + 自建图）复算了覆盖集的等价性质（**#4/#5/#6 三条全部吻合**），但**转移清单本身未逐条比对**。这不影响任何判定——#4/#5/#6 已是「覆盖集 vs 图」的口径，而图由夹具/自建给出。
- `out/_rv33` 与 7 个 mutant 的 basetemp 已删；`/tmp/t33bak/` 已删；工作区**仅剩** `?? .mcp.json` / `?? .zcodeignore` / `?? docs/handover.md`（三者均为既有未跟踪物，**未动**）与本文件。