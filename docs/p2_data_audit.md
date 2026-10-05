# P2 Trace 数据审计报告

## 汇总

- runs：102（2026-09-24 ~ 2026-10-03）
- testcase_runs：90
- steps：249（含失败 1）
- runs.llm_enabled 为 NULL：101

## Locator Failure 重复出现（top）

| target_id | failure_type | 次数 | 跨 run 数 |
|---|---|---|---|
| username_field | ELEMENT_NOT_FOUND | 1 | 1 |

## Recovery 分布

| kind | result | accepted | 条数 |
|---|---|---|---|
| LLM |  | 1 | 30 |
| LLM |  | 0 | 12 |
| None |  | 1 | 7 |
| LLM | RECOVERED | 1 | 1 |

## Review 分布

| review_status | 条数 |
|---|---|
| ACCEPT | 1 |

## (screen, target) 去重对（Experience 主键维度）

| screen | expected | candidate | 条数 | 跨 run 数 |
|---|---|---|---|---|
| LoginView | username_field | username_field_v2 | 1 | 1 |

## 追溯链质量

- recoveries 总数：50
- step_id 悬空 dangling（关联不到 steps）：42
- 有 review 记录的 recovery：1

## 可做 Experience 种子的 ACCEPT 清单（E5）

| review_id | app_id | seed_run_id | seed_step_id | screen | target_id | candidate | seed_ready |
|---|---|---|---|---|---|---|---|
| 1 | com.phaset0.logindemo | run_e049fb5e | 246 | LoginView | username_field | username_field_v2 | True |

**seed_ready=True 的种子：1 条**（成功标准要求 50+ 条真实 Recovery 事件基线）

---

## Task 1.1 评审修订记录（review_p2_task11 收口，2026-10-03）

评审结论「不通过（打回补冒烟）」的四项必修 + 三项顺手，全部收口：

- **P1-1a 候选预登记**：seed 脚本新增 `register_candidate`——每轮把新名
  以别名元素写进 `out/p2_seed_overrides`（原 overrides 复制 + 追加，
  origin: manual + review_id 审计），run 带 `--overrides` 副本 +
  `--generated` 双源合并。漂移前提链补第 4 步（候选须过 9.3 全链）。
- **P1-1b 逐轮还原**：每轮分支（含 ACCEPTED）显式 `restore_src`——
  轮换表第 2 轮起不再全灭。
- **P1-1c CSV 字面量**：`$WARN_PASS/$FAILED/$NO_REVIEW` 未赋值变量
  改字面量（`set -u` 下曾必炸）。
- **P1-1d 解释器**：`PY="python"` → `.venv/bin/python`（M5 记账教训：
  系统 python 无 appium 依赖）。
- **P2-1 场景表**：只收 overrides risk=LOW 目标（username_field/
  go_search/go_profile/search_field/profile_title × 各 2 别名 = 8 场景），
  每场景自带 case_id（LOW 目标分散在多条用例）；MEDIUM 目标（login_button
  /password_field/logout_button）的漂移作为预期拒绝负例，不进种子产量
  ——别名声明 LOW 绕过 9.3-4 是风险作弊，禁止。
- **P3-1**：审计新增 `screen_target_pairs()`（Experience 主键维度）。
- **P3-2**：种子清单输出 app_id 列；`--out` 同时落机器可读 JSON
  （docs/p2_data_audit.json）；cmd_run 补 app_bundle_id 落 runs 行
  （Candidate 主键第一段此前恒 NULL）。
- **P3-3**：preflight 显式 DEVELOPER_DIR；Review 分布空表占位行；
  收尾提示用 $PY。
- **另**：脚本文件曾混入全角标点紧跟 `$VAR` 的污染（bash 将其并入
  变量名 → unbound variable），15 处已清（会话回显污染坑再+1）。

**冒烟（plan step 2 / Gate M1 硬项）**：`--rounds 1 --yes` 全链走通——
漂移注入（username_field→username_field_v2，重编重装）→ mta run exit 5
（RECOVERED）→ PENDING review #1 → accept → **seed_ready=True**
（app_id/screen/target 三段齐全，screen_target_pairs 首条）。审计：
runs=102 / recoveries=50 / reviews=1 / seed_ready=1（原 0）。

---

## Task 1.2 完成记录（P2-02 Experience 模型 + 库迁移，2026-10-03）

- **交付**：experience/models.py（Experience/ExperienceStatus/CandidateSeed/
  ExperienceRun/StateEvent/VerificationPolicy/VerificationDecision，
  strategy 复用 repository.loader.LocatorStrategy——单一真值源）+
  experience/migrations/002_experience_schema.sql（experiences/
  experience_runs/experience_state_events/promotion_proposals 四表 +
  idx_experiences_lookup 索引）+ experience/schema_migrations.py（泛化
  迁移执行器：目录 + 版本链，graph 侧 M5 零改动复用）。
- **关键决策落实**：库文件独立（默认 out/experience.db，trace 流水库 vs
  experience 可变状态库，E9 单写者按库划分）；graph 三表不进本链（M5
  落地、schema 归 graph/migrations 自己的版本链）。
- **模型侧闸门**：CandidateSeed 缺 seed 三件套任一 → ValidationError
  （E5；42 条悬空 P0 数据建不出来）；seed_step_id<=0 拒绝（P0 写 0 的
  教训）；validated_builds 重复拒绝（E7 集合语义）；promoted=True 必须
  带 promoted_commit 且与 status 无联动（9.4 两条时间线）；CHECK 约束
  DB 层兜底（status 四值/origin 单值/result 两值）。
- 实测：pytest 822 passed（+26：models 19 + schema 7）。

---

## Task 1.2 评审修订记录（review_p2_task12 收口，2026-10-04）

评审结论「通过（可收口 Task 1.2 / M1，tag 有效）」，5×P3 全部顺手清掉：

- **P3-1**：success_rate 构造期一致性校验（旁路 0.9 曾可建出并被 Store
  原样落库——与计数列矛盾；现在构造期 + record_sample 重算两处锁死）。
- **P3-2**：CandidateSeed.review_id ↔ seed_recovery_review_id 交叉校验
  （冗余字段恒同指，M4 冗余列「对不上」的坑在模型层堵死）。
- **P3-3**：EXPERIENCE_SCHEMA_VERSION 从链尾脚本名**派生**（加 003_*.sql
  忘 bump 常量也不会让 migrate() 返回值撒谎）；垃圾版本行 fail-loud
  （_applied_seq 此前静默跳过）。
- **P3-4**：002 SQL 与设计 11 节的差异清单补全（CHECK 加固四处 + DEFAULT
  + to_status NOT NULL + 「同名项」声明）。
- **P3-5**：DEFAULT_EXPERIENCE_DB 单点常量（experience/__init__ 导出，
  Task 2.2 的 --exp-db 装配直接 import，杜绝两处字面量漂移）。
- 实测：pytest 826 passed（+4）。

---

## Task 2.1 完成记录（P2-03 ExperienceStore 单写者，2026-10-04）

- **交付**：experience/store.py——`ExperienceStore` Protocol（设计 7.1
  接口真身）+ `SQLiteExperienceStore`（SQLite 实现）+ `EmptyExperienceStore`
  （P1 占位**过渡保留**，旧签名行为不变，Task 2.4 引擎接真 Store 后退役）。
- **接口**：lookup（按 app/screen/target 三键；REJECTED 不返回——人工判定
  不可用的策略不该被消费路径看见，行仍在库 list() 可见）/ create_candidate
  （E5 双重校验：模型 + Store 写入前；同键多次 ACCEPT 各自成行）/ record_run
  （追加样本 + 原子推进统计，**不触碰 validated_builds**）/ record_success_build
  （E7 唯一追加入口，集合语义幂等）/ get_runs（时序升序，limit 取最近 N——
  Verifier 滑动窗口语义）/ update_status（跳变写 experience_state_events，
  同状态 no-op 不产生事件）/ list。
- **E9 单写者**：进程内 threading.Lock 串行化全部写方法 + SQLite 层
  BEGIN IMMEDIATE / busy_timeout 5000（跨进程串行）；Reader 每调用独立
  连接可并发（并发写 8 线程 ×10 全量落账、3 读者与写并发不炸的测试钉住）。
  约定（设计 7.2）：产生写操作的套件 CI 串行执行——本类不替 CI 调度。
- **11.2 保留纪律**：无任何 delete 路径；DEGRADED/REJECTED 后 runs 全量
  可查（测试钉住）。
- **接线**：agent/context.py 删除 Protocol/Empty 定义改为 re-export
  （全仓唯一定义，防两套接口；旧稿「设计 20 节」引用更正为 P2 设计 7 节）；
  agent/recovery.py import 直连 experience.store，行为不变。
- 实测：pytest 842 passed（+16）。

---

## Task 2.1 评审修订记录（review_p2_task21 收口，2026-10-04）

评审结论「通过（可收口 Task 2.1）」，5×P3 全部顺手清掉：

- **P3-1**：设计 7.1 回填修订记录——REJECTED 由 Store 层排除，§5.1
  rank_experiences 只对可用候选排序（接口契约的单方面收紧改为有记录
  的语义收窄）。
- **P3-2**：busy_timeout 单点 5s（connect timeout=30 与 PRAGMA 5000
  曾意图不一致）。
- **P3-3**：①引擎过渡债显性化——Task 2.4 计划节补接线前置（lookup
  旧签名键语义不同、RecoveryContext 缺 app_id/screen 来源、Empty 随
  退役）；②recovery.py 两处残留「20 节」注释更正（context.py 已改、
  此处漏网）。
- **P3-4**：record_run 对 run.experience_id 与参数不一致显形（ValueError，
  不再静默以参数覆盖）。
- **P3-5**：测试死 walrus 表达式清理；并发读者 errors.append 无锁
  （CPython 原子）加注释防「好心修复」。
- 实测：pytest 842 passed。

---

## Task 2.2 完成记录（P2-04 Runtime Guard 共享实现 E1，2026-10-04）

- **交付**：experience/runtime_guard.py——`guard_candidate` 共享校验链
  （E1 红线：LLM 候选校验与 Experience Guard 的**唯一规则体**）+
  `GuardResult`（frozen，outcome/reason/record_as_sample）+ `RuntimeContext`
  + `experience_runtime_guard`（设计 5 节入口）+ `compute_effective_risk`
  （E2：executor.policy.effective_risk 的 re-export，单一真值源）+
  `GUARD_REASON_TO_LLM_FAILURE` 映射（recovery 消费）。
- **4.7 矩阵落成 record_as_sample**：SCREEN_UNKNOWN/SCREEN_MISMATCH/
  RISK_BLOCKED/SECURITY_BLOCKED → 不计样本（「不适用/不被允许」≠「用了
  但错了」）；TARGET_NOT_FOUND/AMBIGUOUS/TYPE_MISMATCH → 计失败；EXECUTE
  → 计样本。风险 None 按最高处理（fail-closed，9.3-4 同源）。
- **平行实现删除**：agent/recovery._llm_stage 的手工 count/type/screen/
  risk/guard 五段（~60 行）替换为一次 `guard_candidate` 调用 + reason
  映射；agent/risk.py 整文件删除（candidate_risk_allowed 的语义由链内
  risk 分支承接）；链序按设计 5 节改为 screen 先于设备操作（未登记
  fail-closed 不再消耗一次 find）。
- **E1 红线测试（双向）**：7 场景（EXECUTE/NOT_FOUND/AMBIGUOUS/
  TYPE_MISMATCH/SCREEN_MISMATCH/UNREGISTERED/RISK_BLOCKED）× 两路径
  （recovery._llm_stage 全链 vs experience_runtime_guard）——
  (outcome, reason, record_as_sample) 逐位一致 + failure_type 映射断言。
  红线测试当场抓到并修复一个重构缺陷：validate 段曾被 append 两次
  （拒绝路径 miss 再补一条无 outcome 的同段）——现全路径恰好一次。
- **接线**：agent/recovery.py import 直连 experience.runtime_guard；
  agent/context.py 的 re-export 维持（Task 2.1 已做）。
- 实测：pytest 863 passed（+21：runtime_guard 17 + 红线 7 − 删除 3）。

---

## Task 2.2 评审修订记录（review_p2_task22 收口，2026-10-04）

评审结论「通过（可收口 Task 2.2）」，5×P3 全部顺手清掉：

- **P3-1**：guard_candidate 入口显式断言 effective_risk 必须是 Risk 枚举
  （字符串 "LOW" 曾被 `is not Risk.LOW` 静默拦成 RISK_BLOCKED——不炸、
  不报错、只掉成功率；现在接线错误当场 TypeError 显形）+ 正向用例测试；
  Task 2.4 接线前置已记入 plan（含「真实 Risk 枚举流经全链 → EXECUTE」
  正向用例要求）。
- **P3-2**：P1 设计 9.3 修订记录回填——「链序以 P2 设计 5 节共享实现为
  准（Task 2.2 起）」，两份设计文档不再各说一个链序。
- **P3-3**：plan Task 2.4 接线前置补 Executor.find_all 缺口（生产 find
  异常语义 vs find_all 列表语义不同构——加方法或适配器二选一）。
- **P3-4**：`policy_check=(A and B and C or None)` 惯用法改显式条件
  （将来 _policy_check 变假值形态会静默跳过 10.1 Guard）。
- **P3-5**：phase0/verify_p1_m4.py 注释指向更新（candidate_risk_allowed
  → runtime_guard 共享链）。
- 实测：pytest 864 collected、全量绿。

---

## Task 2.3 完成记录（P2-05 review accept → create_candidate，2026-10-04）

- **交付**：`agent/review.py` 新增 `seed_candidate()`（ACCEPT → Candidate
  的唯一入口）+ `_resolve_seed_fields()`（E5 种子字段取+校验）；
  `decide_review()` 加 `experience_store` 参数并在 ACCEPT 分支串起
  「先校验 → 改状态 → create_candidate」，返回新建/命中的 Candidate；
  `cli/main.py` 的 `review accept` 加 `--exp-db`（默认
  `DEFAULT_EXPERIENCE_DB`，单一真值源 import，不写字面量）。
- **顺序纪律（本任务的关键决策）**：**先校验种子字段、再改 review 状态、
  最后建 Candidate**。反过来的话，种子不全时 review 已落 ACCEPT，二次
  accept 被「不可二次决策」挡住，Candidate 永远建不出来——fail-loud 必须
  发生在状态变更之前。悬空链（P0 遗留 `step_id=0`/关联不到 steps）与
  Task 1.1 审计的 `seed_ready=False` 同一判据：审计暴露缺口、消费方拒绝
  消费，两侧都不静默跳过（E5）。
- **幂等**：按 `seed_recovery_review_id` 判定，同一 review 二次 seeding
  返回既有行、不重复建（重放/重复触发安全）。REJECTED 行 `lookup` 看不见
  （Store 层语义），此时重建不视为重复。
- **trace 事件 `candidate_created`（设计 11.1）**：落点是 trace.db 的
  `infra_events`（P1 schema 0.1 没有独立 events 表，它是该库唯一的事件
  流）。新增 `TraceStore.record_experience_event()` 作为统一入口 +
  `EXPERIENCE_EVENT_TYPES`（设计 11.1 的 12 个事件名，**不在集合即拒绝**
  ——与 RECOVERY_KINDS 同款 fail-loud），Task 2.4 的
  lookup/hit/miss/guard_block/execution 直接复用。**不污染 P1 判据**：稳定性
  报告的 WDA 指标按 `action_taken='RESTART_WDA'` 过滤，本类事件
  `action_taken` 留空。
- **去重复**：`TraceStore.get_review_seed()` 与审计脚本的 `seedable_accepts`
  共用 `tracer.storage.SEED_SELECT`（同一 join 两处维护必然漂移——P2-04
  「两套校验合一」同款纪律）；审计脚本改为消费该片段，函数签名与
  **markdown 列**不变。**限定修正（review_p2_task23 P3-5）**：json 产物
  增 4 键（`note` / `recovery_id` / `review_status` / `seed_tc_run_id`）
  ——初版记录写成「输出列不变」只对 markdown 成立，已刷新
  `docs/p2_data_audit.json`。
- **决策记录**：`Experience.strategy.origin = "experience"`——P2 9.2 已把
  experience 纳入 origin 词汇（Task 4.1 扩 `loader.ORIGINS`）；本策略非
  人工手写（manual）也非源码生成（source）。Task 4.1 落地前它只存于
  experience.db、不经 Repository loader（其 ORIGINS 校验不含 experience），
  不构成拦截。策略类型固定 `accessibility_id`（LLM 候选给的是 identifier
  值），与 `export_overrides_patch` 同源。
- **P1 回归碰撞与处理**：`test_fi_02b`（H15 补丁导出）此前 accept 恒 exit 0
  ——P2 起 accept 默认建 Candidate，该行 run 无 `app_bundle_id` → E5 拦下。
  处理：①`fi_support.run_matrix` 加可选 `bundle_id`（默认 None，历史行为
  不变），本行补全追溯链（真实设备路径恒有 bundle_id，是测试替身的数据
  缺口）；②该行显式 `--exp-db` 隔离——**绝不写仓库的 out/experience.db**
  （测试污染真实经验库 = 污染数据）。H15 断言全部保留，另加一条「ACCEPT
  顺带建出 Candidate」的正向断言（测试因此变强而非变弱）。
- **已知后果（留待评审判断）**：`mta review accept` 现在对「种子链不全」
  的 review 一律 exit 3、状态保持 PENDING——H15 的补丁导出因此与该 run
  的追溯链完整性耦合。现存唯一 ACCEPT（review #1，Task 1.1 冒烟产物）在
  Task 2.3 之前就已 ACCEPT，故**未自动补种**；如需入种子库，可走
  `seed_candidate(trace_store, experience_store, 1)` 单独补种，或跑新一轮
  drift 产出新的 PENDING review 后 accept。
- 实测：pytest 874 passed（+10：test_review_seed.py 10 项）。

---

## Task 2.3 评审修订记录（review_p2_task23 收口，2026-10-04）

评审结论「有条件通过」，1×P2 + 7×P3 全部收口：

- **P2-1（必修）审计脚本调用面回归**：Task 2.3 给脚本加了
  `from tracer.storage import SEED_SELECT`，而本脚本的既有惯例是**直跑**
  （`python scripts/p2_audit_recoveries.py …`——`p2_seed_recoveries.sh` 收尾
  打印的复核命令、Task 1.1 review 记录的实跑方式都是这个形态）；直跑时
  `sys.path[0]=scripts/` → `ModuleNotFoundError`。修：脚本顶部
  `sys.path.insert(0, parents[1])` 补仓根，保留直跑惯例（不去改 shell 脚本
  与文档的既有命令）。测试侧补 `test_script_runs_directly_by_path`
  （subprocess 直跑断言 exit 0）——原有用例走包路径导入，天然测不到这条面。
- **P3-1 半状态无产品出口**：`create_candidate` 自身失败时 review 已落
  ACCEPT、Candidate 0 条，二次 accept 被「不可二次决策」挡死，唯一出路是
  直呼 `seed_candidate()`（无 CLI）。修：新增 `mta review reseed <id>`
  ——补种已 ACCEPT 的 review（幂等，已存在的 Candidate 直接返回）。
  这同时是现存真实数据（review #1 在 Task 2.3 之前就已 ACCEPT）的补种通道。
- **P3-2 两个消费闸门字段集不一致**：`_resolve_seed_fields` 与
  `export_overrides_patch` 各持一套必填集，seed 放行而 export 拒绝时状态已
  改、补丁再也导不出来。修：合并为**单一闸门** `validate_accept()`（种子
  字段 `_SEED_REQUIRED` + 补丁字段 `_PATCH_REQUIRED`），在状态变更**之前**
  一次跑完、零副作用；`export_overrides_patch` 改为消费同一常量，不再各写
  一套规则（P2-04 合一纪律的延续）。
- **P3-3 幂等是「过滤读的涌现属性」**：判重走 `lookup`（有意不返回
  REJECTED），置 REJECTED 后再 seed 会静默多出一行。修：Store 新增
  `find_by_seed_review()` **直查不过滤**，消费方（`_seed_from_row`）**显式**
  排除 REJECTED——「REJECTED 不算已种、可重新学习」从此是写明的语义，不是
  读见的巧合。
- **P3-4 `origin="experience"` 不在 `loader.ORIGINS`**：当前无路径喂给
  loader，不构成拦截；但 Task 4.1 的 promote 必须与 `ORIGINS` 扩值同批落地。
  已作为**接线前置**写进 plan Task 4.1（与 Task 2.4 的三条接线地雷并列）。
- **P3-5 审计 json 未重生成**：已重跑刷新 `docs/p2_data_audit.json`
  （自动生成段与 markdown 头部逐行比对一致，仅 json 增 4 键）；完成记录里
  的「输出列不变」已限定为「markdown 列不变」。
- **P3-6 失败路径留下空 `experience.db`**：旧实现先构造 store（触发
  migrate 建库）再校验。修：CLI 改用 `_LazyExperienceStore` 代理，构造推迟
  到首次真正使用（校验已通过）——失败路径不落文件，成功路径照常落库
  （两个方向都有测试钉住）。
- **P3-7 杂项**：①`_resolve_seed_fields` 重复调用 → 重构为「取行一次 +
  校验一次」，`decide_review` 与 `_seed_from_row` 共用同一行，不再重复查库；
  ②`seed_step_id == 0` 冗余分支删除，语义并入必填项说明文案；
  ③Experience 不带 element type（`candidate_type`）——Promotion 若需写完整
  override 要回查 recovery 行，已随 P3-4 一并记入 plan Task 4.1。
- 实测：pytest 881 passed（+7：修订项测试；test_review_seed.py 10 → 17）。



---

## Task 2.4 完成记录（P2-03/04/05 集成：Recovery 接真 Store + 结果语义，2026-10-04）

**Objective**：`try_experiences` 主循环落地 + `detail.kind` 细分。本任务是
Gate M2 的集成载体——P1 的 `EmptyExperienceStore` 占位到此退役，「知识
积累」第一次真的接上恢复引擎。

### 交付

- `agent/recovery.py`：`_try_experiences()`（设计 5.1 主循环：lookup →
  逐候选 Guard → 4.7 记样本 → EXECUTE 则执行 + postcondition → 成功返回
  `kind="experience"`；全部候选用尽回落 LLM，仍受 P1 Budget 控制）+
  `_try_one_experience()`（单候选）+ `_queue_sample()`（4.7 payload 的
  **唯一**落库内容决策点）+ `_FindAllAdapter`（数量观测端）+ `_recovered()`
  / `_current_screen_id()` / `_with_samples()` / `_unrecovered()` 四个收口
  辅助。`experience_store=None` 表达「没有经验库」，与「空库」在报告上
  可区分（`no_store` vs `miss`）。
- `experience/store.py`：`EmptyExperienceStore` **退役删除**；
  新增模块级 `record_sample_runs(store, samples, *, step_id)`——引擎产出
  payload、管线补真 `steps.id` 的两段式。
- `experience/runtime_guard.py`：新增 `experience_locator(exp)`——
  `LocatorStrategy` → Executor `Locator`（`list[dict]`）的**唯一**转换点，
  Guard 的 `find_all` 与执行端重发共用。
- `executor/executor.py`：新增 `find_all(locator)`（接线地雷 ③ 定案：
  **给 Executor 加方法**，而非写异常语义→计数的适配器）；顺带把
  `BY_MAP` / `_by_for()` 提成模块级，`find` 与新方法共用。
- `agent/context.py`：`RecoveryContext` 增 `find_all` / `app_id` / `run_id` /
  `step_index`；`RecoveryResult.detail` 记约定键 `recovered_kind` /
  `experience_runs`。
- `source/screen.py`：新增纯函数 `screen_fingerprint(page_source)`（E8 的
  观测面：可见元素 `name`/`label` 去重排序后 sha256 前 16 位；页面不可
  解析 → `None`，不猜）。
- `runner/result.py`：新增设计 10 的分类表 `RECOVERED_KIND_BY_MECHANISM`
  + `recovered_kind(mechanism, context=None)`；`context="assertion_target"`
  压过机制分类 → `RECOVERED_ASSERTION_TARGET`。
- `cli/pipeline.py`：`_write_experience_runs()`；动作步与 aux（wait/assert）
  的恢复块都盖 `recovered_kind` 并落样本；`_recovery_context` /
  `_aux_recover` 注入 `find_all` / `app_id` / `run_id` / `step_index`；
  `SessionPipeline.__init__` 增 `app_id`。
- `cli/main.py`：`run` 加 `--exp-db`；装配改 `RecoveryEngine(...,
  experience_store=_LazyExperienceStore(args.exp_db))`。
- `tracer/storage.py`：`RECOVERY_KINDS` 增 `"EXPERIENCE"`（P1 14.2 的枚举
  缺它，不补则 `_write_recovery_row` fail-loud 直接炸）。
- `report/junit.py` + `report/html.py`：明细增恢复分类（HTML 加「恢复分类」
  列）。聚合逻辑与退出码**不变**（仍 RECOVERED ≠ PASS / exit 5）。

### 接线地雷清账（plan Task 2.4 显性化的四条，全部落地）

1. **P1 旧签名 `lookup(ctx.app_build, ...)`**：换成真键
   `lookup(app_id, screen_id, target_id)`；`RecoveryContext` 补 `app_id`
   （来自 `--bundle-id`，与 `app_build` 严格区分）。键三段缺一即
   `incomplete_key` 如实报出，**不拿 app_build / 当前屏顶替**（顶替会查到
   别人家的经验）。
2. **`EmptyExperienceStore` 退役**：`experience_store=None` 表达「未接库」。
3. **生产 `Executor` 没有 `find_all`**：定案「加方法」。
4. **`effective_risk` 必须是 Risk 枚举**：`_aux_recover` 从登记元素 metadata
   取 `eff.risk`（E2：不信任 LLM 自报）。

### 本任务实锤的两个真 bug（都在 TDD 过程中被测试逮住）

- **① `_aux_recover` 的 `element_id` 是容器前缀引用**（
  `TargetRef.id` = `HomeView.login_button`），而 Experience Store 主键第三段
  与源 metadata 里都是**裸 id**。P1 时代 `element_id` 不进 Store 键，所以
  一直没显形；Task 2.4 一接真 Store，aux 路径（矩阵 #15）整条不可达——
  `lookup` 恒 miss。修法：与 `_run_action_step` 对齐，取解析后的 `eff.id`
  （解析失败才退回 `ref_id`）。**同一个概念在两条路径上各写一遍必然漂移**
  ——这正是本项目反复吃到的教训。
- **② 未恢复的结论被丢掉了 4.7 样本**。初版 `_try_experiences` 内部建局部
  `samples = []`，只在「已恢复」的返回里带出去；Guard 判定的失败样本
  （NOT_FOUND / AMBIGUOUS / TYPE_MISMATCH）与执行失败样本随未恢复结论一起
  被丢弃。**后果是失败率被系统性低估、成功率虚高**——E11/E4 级别的错误，
  不是记账瑕疵。修法：`samples` 提到 `recover()` 作用域，并让管线在
  **未恢复分支**也调 `_write_experience_runs`（动作步与两个 aux 分支共三处）。
  测试 `test_p2_02_not_found_is_failure_sample` 就是钉这一条的。

### 决策记录（有意为之，留待评审）

- **`ctx.effective_risk` 的归一**：Experience 分支不因 `effective_risk is
  None` 硬拦。理由——`None`（无可证明的风险）在 fake-driver / 未登记
  metadata 的语境里是常态，按最高风险 fail-closed 会**整条砍掉**这些场景
  的既有恢复能力；而登记目标若真高风险，步骤在执行前就已被 10.1 Guard
  拦成 `SECURITY_BLOCKED`，到不了恢复。**这是本任务最值得被 review 挑战
  的一处**（见「已知后果」）。
- **`step_index` 进 ctx 但不当 `steps.id` 用**：恢复发生在
  `Lifecycle.record_step()` **之前**，那一刻 `steps.id` 还不存在
  （AUTOINCREMENT）。拿 YAML 步序冒充是两个 id 空间互相撞——本项目在
  「build ≠ bundle」上吃过同款亏。故拆两段：引擎产出 payload，管线在
  `record_step()` 之后补真 id。
- **`not_dispatched`（aux 无 dispatch 语义）不写样本**：aux 的候选交调用方
  覆盖定位后重跑，执行结果此刻不可观测。猜一个 SUCCESS 会抬高
  `success_rate` 把 Candidate 推向 VERIFIED——E4 级别。同理
  `postcondition` 观测异常记 `UNKNOWN`，也不写样本、不声称恢复。
- **`EXPERIENCE_EVENT_TYPES`（设计 11.1 的 12 个事件名）仍无人消费**：
  定义在 `tracer/storage.py` 且 fail-loud，但 Task 2.4 没有接
  `experience_lookup/hit/miss/guard_block/execution` 的写入点。**有意不接**
  ——半接的埋点比不接更坏（报告上「有时有有时没有」）。已记入 plan
  Task 4.1 一并落地。
- **`Experience.as_dict` 单向辅助**：仅用于导出，不反向构造（避免出现
  第二个模型真值源）。

### Gate M2 真机验证（`phase0/verify_p2_m2.py`，2026-10-04 实跑全绿）

产物：`out/p2_m2_gate/summary.json`（verdict=PASS，device_half=PASS）。
判据逐条可机械复核（R13-3 的教训：只留文字、无产物 = 事后无法复核）。
下表数值为**收口后最终一次**实跑（elapsed 54.1s，G1 13 passed）：

| 项 | 判据 | 实测 |
|---|---|---|
| PF ×5 | 模拟器 booted / Appium 200 / LLM 网关 200 / **LLM completion 延迟** / SwiftUI 宏工具链可用 | 全 PASS（completion 1.2s < budget timeout 20s） |
| G1 | P2 矩阵 pytest 全绿 | `13 passed` |
| G0 | 基线重扫描 + 真实改名重编译安装 + 重扫描登记新 id | PASS |
| G2 | 第一次 run：`RECOVERED_LLM` / exit 5 / recoveries kind=LLM / PENDING review | `run_bd49c9fb`…（首跑样本 `run_44ca6e6a`，review #1） |
| G3 | 人工 ACCEPT → Candidate（E5 种子） | `exp_ba4305c523f6` CANDIDATE `(com.phaset0.logindemo, LoginView, username_field)` strategy=`user_field` |
| G4 | 第二次 run：`RECOVERED_EXPERIENCE` / exit 5 / **LLM calls = 0** | `run_31c07156`，`llm_calls=0`，LLM 行 0 条 |
| G5 | 4.7 记账：experience_runs 1 行 SUCCESS / step_id=真 steps.id / validated_builds | `step_id=3`（= 该 run 的 RECOVERED 步 id）、`run_id` 与 runs 一致、`sample_count=success_count=1`、`validated_builds=['local']` |
| G6 | 空库 + `--no-llm` 同场景仍 FAIL / exit 1（P1 行为保留） | exit 1，`llm_calls=0`，stages 见 `miss` + `llm: disabled` |

**这是「知识积累」唯一可观测的形状**：同一漂移，第一次靠 LLM（花 1 次
调用 + 人工确认），第二次起 LLM **零出手**。

### 环境依赖（写下来免得下次白排查）

- 设备半边的 G0/G2–G6 需要 `sandbox-exec` 可用：Swift 编译器在
  `sandbox-exec` 里拉起 `swift-plugin-server` 展开 SwiftUI 的 `@State` 宏，
  宿主禁止 `sandbox_apply` 时报
  `malformed response ... could not be found for macro 'State()'`，看起来
  像代码坏了。脚本已把它固化成 `PF_swift_macro_toolchain` 前置项，并在
  summary 里用 `device_half ∈ {PASS, FAIL, SKIPPED_ENV}` 三态区分
  「环境没准备好」与「跑了但判据没过」。
- 本机带 `HTTP_PROXY`，环回端口（4723/15721）的代理转发行为不稳定
  （实测 4723 被拒、15721 放行）。脚本 `_env()` 显式 `no_proxy` 豁免环回，
  自身探测也装无代理 opener。
- `--basetemp` 必须指到工作区内（受限环境拦系统临时目录写入 → 200+ 个
  `PermissionError: EEXIST` 假失败）。

- 实测：pytest **919 collected**（+38，父提交 `48a1a11` 为 **881**）：
  `test_recovery_experience.py` 24 项 + `test_p2_matrix_m2.py` 12 项 +
  `test_recovery_policy.py` 2 项。**订正**：本记录初稿写「+36 / 父 883」，
  经 `git worktree` 独立计数为 +38 / 父 881（Task 2.4 评审 P3-6）。

---

## Task 2.4 评审修订记录（review_p2_task24 收口，2026-10-04）

两段独立 review（spec 合规 → 代码质量，探针实测）结论：**有条件通过**——
主循环顺序、E1 共享 Guard、E11 失败样本落库（含作者自述修复的两处真 bug）、
E7/E3/主键语义/结果语义/`no_store` vs `miss`/P1 回归/无库污染均经独立复现
成立；遗留 2×P2 + 6×P3。完整报告：`review/review_p2_task24_2026-10-04.md`。

### 已修（收口提交）

- **P2-1 aux 命中永不落样本**（最实质的一条，直接削弱 P2 的「知识积累」
  目标）：aux（wait/assert）的执行结果引擎侧不可观测，但**调用方能观测**
  ——它在覆盖定位后重跑了一次断言/等待。原实现标 `not_dispatched` 后干脆
  不写样本，后果是**只经 aux 命中的 Candidate `sample_count` 恒为 0**，永远
  到不了 4.5 的 `min_samples`、永不能 VERIFIED，报告里还会读成「这条经验
  从未被使用」。修法：引擎产出 `result=None` + `pending_observation` 的
  **待定**样本，由调用方观测后经 `agent.recovery.resolve_deferred_sample()`
  回填——**翻译留在引擎模块（4.7 单点），调用方只说「成没成」**；重跑失败
  回填 FAILURE（`guard_reason=AUX_RERUN_FAILED`）并补落 steps 行（此前这条
  路径连 steps 行都没有，trace 上「这一步没跑过」）。
- **P3-1 10.1 复检的不对称**：`experience_runtime_guard` 原注释谎称
  「EXECUTE 后走正常 dispatch、其处自会过 10.1 Guard」——实测
  `StepRunner._dispatch_action` **不含任何 `guard.check`**，那个兜底不存在。
  已改正注释，并让 Experience 路径经 `RecoveryEngine._policy_check_for()`
  传入 `policy_check`（候选解析到登记元素后用**登记的** risk/screen/id 过
  Guard），与 LLM 路径同源。**更深一层**：生产 `cli/main.py` 装配
  `RecoveryEngine` 时**没传 `guard`**，所以两条路径的 `policy_check` 在生产
  中一直是 `None`、10.1 复检**从未生效**（LLM 路径自 P1 起即如此）——把
  guard 接进引擎会新拦下 CRITICAL / blocked_targets 候选、改变 P1 的 LLM
  路径行为，与「P1 行为保留」冲突，故列入延后项；本次先把形态铺好。
- **P3-2 误导性注释**：`--exp-db` 惰性构造那段原写「`mta run --no-llm` 之类
  不碰经验」，但 Experience 命中本来就不需要 LLM——`--no-llm` 恰恰会查经验
  库（G6 实测 stages 含 `miss`）。注释已按实际语义重写。
- **P3-3 静默丢样本**：`_queue_sample` 因缺 `run_id` 直接 `return`，无痕迹。
  已补 stage `{"stage": "experience_sample", "outcome": "no_run_id"}`。
- **P3-6 计数不准**：更正为 919（+38，父 881）。
- **Gate 脚本健壮性**（复跑时暴露）：新增 `PF_llm_completion_latency` 前置项
  ——`/models` 秒回 200 **不代表** completion 能在 budget 的 20s 超时内返回
  （网关后面挂的是推理模型，输出先走 `reasoning_content`）。实测撞过
  「四项探活全绿、G2 连续两次 `LLM_PROVIDER_ERROR: timed out`」的假绿；
  G2 的 provider 级重试由 2 次提到 4 次（仍只重试 provider 错误，校验链
  拒绝是确定性语义，重试只会掩盖真问题）。

### 显性延后（本次不修，需单独立项）

- **P2-2 `SCREEN_UNKNOWN` 在引擎层不可达**：`_current_screen_id` 在屏识别
  失败时回落 `ctx.screen_id`，于是 §5 的 `SCREEN_UNKNOWN → MISS` 分支对该
  路径是死代码；异常页面若恰好存在同名唯一元素，会在**未确认屏**的情况下
  执行 Experience。**不修的理由**：该回落继承自 P1，且两条路径共用本函数，
  收紧会**同时改变 P1 的 LLM 路径行为**——与 plan 的「P1 行为保留」硬约束
  直接冲突。本次已补 `test_screen_recognition_failure_falls_back_to_
  registered_screen` **钉住现状**：谁改这条口径，测试先红，逼他先处理那条
  约束。
- **P3-4 `validated_builds` 被硬编码 `"local"` 稀释**：`cli/pipeline.py` 两处
  `app_build="local"`（继承 P1）。E7 的 `validated_builds` 本应是「哪些真实
  build 验证过」的集合，恒为 `["local"]` 时集合语义退化、跨 build 有效性
  判断失去意义。本提交未引入，但正是它让 E7 首次真正生效 → 需把真实
  build id 贯通 `RecoveryContext.app_build`，单独立项。
- **P3-5 `EXPERIENCE_EVENT_TYPES` 半接**：§11.1 的 12 事件只接了
  `candidate_created`；recovery 期的 `experience_lookup/hit/miss/guard_block/
  execution` 无写入点。**有意不接**（半接的埋点比不接更坏），已挂
  plan Task 4.1。

### 复跑证据

修订后重跑 Gate M2 真机全绿：`verdict=PASS`、`device_half=PASS`、12 项判据
全 PASS、elapsed 54.1s、G1 `13 passed`。全量 pytest **925 passed**
（919 + 6：`resolve_deferred_sample` 3 项 + 10.1 复检 1 项 + 屏识别回落钉子
1 项 + aux 重跑失败落库 1 项）。

---

## Task 2.4 终审修订记录（review_p2_task24_final 收口，2026-10-04）

终审（`review/review_p2_task24_final_2026-10-04.md`）结论：**通过（可收口
Task 2.4 / M2）**，前置评审的 2×P2 + 6×P3 处置全部核销，遗留 0×P1/P2 + 6×P3。
按终审「建议动作（优先级序）」逐条处置：

### 1. 随手修（终审 P3-3 / P3-5）

- **P3-3 aux 重跑失败的 steps.detail 丢了 `recovery` 段**（成功路径有）：
  只写 `{"error": ...}` 时，trace 上看得出「这一步失败」+「有一条 FAILURE
  样本」，却看不出**试过哪条经验、候选值是什么、Guard 判了什么**——正是本次
  修订想消掉的那类盲区。修法：`_aux_rerun_failed` 把 `recovery_kind` /
  `recovery_context="aux_rerun_failed"` / `recovery`（含完整 stages）并进
  step detail。
  **不盖 `recovered_kind`**：该标签的语义是「被哪条机制救回」，而本步终态是
  FAILED——盖上去会污染报告的恢复分类口径。机制名（`recovery_kind`）是事实
  （引擎确实试了 Experience），照记。
- **P3-5 共享 `guard_candidate` 的 docstring 仍留着被证伪的那句话**：前置评审
  P3-1 要消灭的「执行路径 dispatch 处自会再过 Guard」在三处表述里只剩这一处
  （`inspect.getdoc` 取证）。已改成「⚠️ 不要以为执行路径会兜：
  `StepRunner.run_step` 的 `guard.check` 只对**原步骤的原元素**跑一次，恢复
  重发走的 `_dispatch_action` / `dispatch` 里没有任何 Guard」——三处表述一致。

### 2. 立项时一并处理（终审 P3-1 / P3-2）

- **P3-2 待定样本契约无防护 → 现在已加**：`experience.store.record_sample_runs`
  加前置检查，`result is None` 时**指名报错**
  （「deferred sample 未回填——先调 `agent.recovery.resolve_deferred_sample`」）。
  原实现漏回填会一路带到 `ExperienceRun` 的 `Literal["SUCCESS","FAILURE"]`
  校验才炸，报错指向「result 类型不对」，**看不出是「忘了回填」**。新增测试
  `test_unfilled_deferred_sample_fails_loud_at_store_boundary` 钉住
  「报错 + 不留半行 + 回填后正常落库」。
- **P3-1 10.1 复检的「终态 failure_type」不对称 → 本次不改（按终审建议）**：
  Experience 路径被 10.1 拦后对外 `failure_type` 仍是原症状
  （`ELEMENT_NOT_FOUND`），LLM 路径同候选给 `SECURITY_BLOCKED`
  （经 `GUARD_REASON_TO_LLM_FAILURE`）——映射表只有 LLM 侧一个消费点。
  触发条件有限（需「无 LLM 回落」才显形：`--no-llm` / 无 llm / 预算耗尽 /
  熔断），且**当前生产没把 guard 接进引擎 → 现在不可达**。
  **不修的理由（采纳终审）**：改它动的是 P1 的 `miss` 语义 →
  与「把 guard 接进引擎」同批处理（那时才显形），本次不越界。

### 3. 常量单点（终审 P3-6）

- **探活阈值与规模两处硬编码**：`_llm_latency_probe` 的判据阈值改为引用
  `llm.budget.BudgetConfig().timeout_seconds`（**单一真值源**，budget 默认值
  一变这里跟着变，不再悄悄漂）；docstring 与 detail 文本都写明
  **「这是最小 prompt 的下界，不是 G2 完整 prompt 的保证」**——探活用
  `max_tokens=8`，而 G2 跑的是几千 token 的 UI 树恢复 prompt，探活天然乐观
  （G2 侧靠 provider 级重试兜）。
- 顺带把 **HTTP 4xx 与超时分开报**（终审 §4 存疑项 2）：`HTTPError` →
  「HTTP 4xx（model 不可用或凭据有误）」= 环境未配；其余异常 → 超时/拒连
  = 环境太慢。两者对排障的含义不同，混成一个「探活失败」会误导。

### 4. 有意口径显性化（终审 P3-4）

- **`recoveries` 表表达不了「失败的经验尝试」**：`_write_recovery_row` 写死
  `result="RECOVERED"` / `accepted=True`，只有成功路径调用它。
  已在 docstring **明写这是有意**：`recoveries` 的语义是「恢复动作记录 +
  9.5 review 的种子来源」（`create_review` 从这里取 rec_id），而 E5 规定
  **失败尝试不产种子**——记进来只会让 review 队列多出永远不该 ACCEPT 的行；
  失败尝试的痕迹已在 `experience_runs`（4.7 样本 + guard_reason）与 `steps`
  （终态 + `recovery` 段）。若将来要审计「尝试过但失败」的分布，正确做法是
  给该函数加 `result` 参数（列存在），而不是硬编码撒谎——但那是独立的数据
  口径变更，需先定档。矩阵测试加断言钉住（失败路径 `recoveries` 0 行）。

### 5. 里程碑与 M3 起步前置

- M2 收口：tag `checkpoint-p2-m2`。终审建议「M3 起步前先清 plan 里的延后项
  清单」，三条已被显性记录且各有钉子测试，**别让它们随任务滚动**：
  1. `SCREEN_UNKNOWN` 收紧（与「P1 行为保留」的边界）；
  2. `validated_builds` 贯通真实 build id（现恒为 `["local"]`）；
  3. `EXPERIENCE_EVENT_TYPES` 全接（§11.1 的 12 事件现只接 1 个）；
  4. 另加终审 P3-1：10.1 拦后终态 `failure_type` 的对称化（与「把 guard 接
     进引擎」同批）。

### 复跑证据

- 全量 pytest **926 passed**（925 + 1：`unfilled_deferred_sample` 那条）。
- Gate M2 真机复跑全绿：`verdict=PASS`、`device_half=PASS`、12 项判据全 PASS、
  elapsed 54.0s、G1 `13 passed`；新探活 detail 已带下界说明。

---

## Task 2.4 清延后项（2/2）——10.1 终态对称 + SCREEN_UNKNOWN 收紧（2026-10-04）

终审「建议动作」第 4 条：M3 起步前先清 plan 的延后项清单，别让它们随任务滚动。
本提交处理剩下两条（**都会改行为**，故与 (1/2) 的加法性改动分开）：

### 先更正一处错误论断（重要）

Task 2.4 评审修订记录里写过「生产 `cli/main.py` 装配 `RecoveryEngine` 时**没传
`guard`** → 两条路径的 `policy_check` 在生产中一直是 `None`、10.1 复检**从未
生效**」。**该论断是错的，已由探针证伪**：

- `cli/main.py:508` 有一句 `pipeline.recovery.guard = runner.guard`（在 runner
  装配之后），两条路径共用同一个 Guard 实例；
- 探针：`guard=None` 时 `_policy_check_for` 返回 None；`guard` 接上后返回可调用
  的 checker 且放行（无 violation）。

错因：只看了 `RecoveryEngine(...)` 的构造参数，**没看后续的属性注入**。
10.1 复检在生产中一直是生效的（LLM 路径自 P1 起、Experience 路径自本任务起）。
已同步更正 `review/review_p2_task24_2026-10-04.md` 与
`agent/recovery.py::_policy_check_for` 的 docstring。

**Item 4a（把 guard 接进引擎）因此不存在**——无需改动。

### Item 4b：10.1 拦后终态 `failure_type` 对称化（终审 P3-1）

- 症状：Experience 路径被 10.1 拦后对外 `failure_type` 仍是原症状
  （`ELEMENT_NOT_FOUND`），而 LLM 路径同候选给 `SECURITY_BLOCKED`——映射表只有
  LLM 侧一个消费点。触发面：**候选全被拦且无回落**（`--no-llm` / 未配 LLM）。
- 后果：CI 会把「被策略拦下」读成「元素漂移」，排障方向完全错；且丢掉
  BLOCKED/exit 4 的语义。
- 修法：`_try_experiences` 收集**安全/风险拦截**的 reason（
  `GUARD_POLICY_BLOCK_REASONS = {SECURITY_BLOCKED, RISK_BLOCKED}`，新增于
  `experience/runtime_guard.py`）；无回落时经新的
  `RecoveryEngine._terminal_failure_type` 用**同一张映射表**产出终态。多条候选
  取最严（SECURITY > RISK），且与候选顺序无关。
- **映射表改名** `GUARD_REASON_TO_LLM_FAILURE` → `GUARD_REASON_TO_FAILURE_TYPE`：
  现在有两个消费方，名字里的 LLM 前缀已不准。**值不动**（`LLM_` 前缀是既有报告
  契约值，改名换值会让历史报告的同一症状出现两种写法）。
- 边界（有意）：只有安全/风险拦截改写终态；`NOT_FOUND` / `AMBIGUOUS` /
  `TYPE_MISMATCH` 是普通失败，**不改**——否则用例集体变 BLOCKED，CI 分不清
  「漂移」与「被策略拦」。有回落时终态由 LLM 那一次决定（它是最后发生的症状）。
- 实测发现：两种 reason 在当前 Guard 顺序下**不会同时出现**（风险检查先于 10.1
  复检，而 `effective_risk` 是 ctx 级、对全部候选相同）→ 次序规则用纯函数直接
  测，并把「不会同时出现」也钉成一条实证测试。

### Item 1：SCREEN_UNKNOWN 收紧（终审 P2-2）

- 症状：`_current_screen_id` 在识别失败时回落 `ctx.screen_id`，使设计 §5/§4.7 的
  `SCREEN_UNKNOWN → MISS` 成为**死代码**；更严重的是让 Experience 路径的屏校验
  **自比自**——`current_screen` 与 `exp.screen_id` 都等于 `ctx.screen_id` → 恒等
  → Guard 第一环从不触发。于是「页面没有任何已登记 marker」时，只要恰好存在同名
  唯一元素，候选就会在**未确认屏**的情况下执行。
- **修法（关键是划对边界）**：两种输入必须分开——
  1. **识别跑过**（`screen_res is not None`）→ 只认观测结果；无 marker /
     多 marker 互斥 / 解析失败 → `None` → `SCREEN_UNKNOWN → MISS`（§5 落地）；
  2. **识别没跑**（`screen_res is None`：无 Repository——marker 表本身不存在；
     无页面；该动作未准入 LOCAL_RECONCILE）→ 沿用 P1 的**登记屏先验**。
     此时不是「证明失败」而是「无从判定」，任何屏校验都必然空转，收紧只掉能力
     不增安全。
- **实测边界**：先按「一律不回落」实现 → **29 个测试失败**（全在 P2 期单测
  fixture，`_engine(store)` 默认 `repo=None`），**P1 的 24 行矩阵与 LLM 矩阵全过**。
  29 个失败正说明「无 repo」那一类被回落承载着，而它在生产里对应的是
  `--fake-driver` 无 `--generated` 的模式。按上边界重写后只剩 1 个失败（钉住旧
  行为的钉子测试），P1 全绿。
- **对「P1 行为保留」的刻意偏离**（决策记录已进设计附录）：偏离面仅限「屏识别
  跑过且没结论」这一种 P1 从未规定的输入，方向是**收紧**（照常恢复 →
  fail-closed 跳过）；「识别没跑」的那条 P1 行为原样保留。回归底线（P1 全部
  tests + 真机 Gate）实测通过。

### 顺带修正（测试替身与生产接线的漂移）

`tests/fault_injection/fi_support.run_matrix` **没有复刻** `cli/main.py` 的
`pipeline.recovery.guard = runner.guard`，于是出现「生产会拦、矩阵不拦」的假绿
（本次实测踩到）。已补上，替身从此忠实反映生产接线。

### 复跑证据

- 全量 pytest **949 passed**（946 + 3：终态对称 2 + 屏识别边界 1）。
- Gate M2 真机复跑全绿：`verdict=PASS`、`device_half=PASS`、12 项判据全 PASS、
  G1 `18 passed`。
  （中间一次因网关连续 3 次 `LLM_PROVIDER_ERROR` + 后续 `wait ProfileView`
  超时而红，复跑即绿——环境抖动，非回归。）

---

## Task 3.1 完成记录（P2-06 ExperienceVerifier 纯函数状态机，2026-10-04）

**Objective**：设计 4.3–4.6 / 8.2——全部决策纯函数（E13），不依赖设备。

### 交付

- `experience/verifier.py`（新）：`evaluate`（4.3 状态机）+ 纯谓词
  `distinct_run_count` / `success_rate_of` / `sliding_window_degrade`（4.6）/
  `eligible_for_auto_verification`（4.4）/ `needs_revalidation`（E8）+
  应用区 `apply_outcome` / `revalidate` / `mark_revalidation_required`。
- `experience/sweeper.py`（新）：`StalenessPolicy` / `is_stale`（纯）/
  `sweep_stale_candidates`（8.2 的 90 天清理）。
- `experience/store.py`：新增三个能力——`record_screen_fingerprint`（E8 的观测
  更新）、`record_state_event`（**非跳变**时间线标记）、
  `has_state_event_since_transition`（标记幂等查询）；Protocol 同步。
- 测试：`tests/unit/test_verifier.py`（31 项）、`tests/unit/test_sweeper.py`
  （17 项）。

### 决策表（落地形态）

| 当前状态 | 条件 | 决策 | reason |
|---|---|---|---|
| REJECTED | —（终态） | `NO_CHANGE` | `TERMINAL` |
| CANDIDATE | 不满足 E4 资格 | `KEEP_CANDIDATE` | `NOT_ELIGIBLE` |
| CANDIDATE | 满足 4.5 门槛 | `PROMOTE_TO_VERIFIED` | `THRESHOLD_MET` |
| CANDIDATE | 其他 | `KEEP_CANDIDATE` | `THRESHOLD_NOT_MET` |
| VERIFIED | 滑动窗口触发 | `DEGRADE` | `SLIDING_WINDOW` |
| VERIFIED | 其他 | `NO_CHANGE` | `HEALTHY` |
| DEGRADED | —（只能显式 `revalidate`） | `NO_CHANGE` | `AWAITING_REVALIDATION` |

**DEGRADED 没有自动出口**：4.3 只允许「显式 revalidate 成功」→ VERIFIED。
不做「窗口恢复就自动转回」——那会让降级变成抖动（E6 的滑动窗口是安全阀，
不是健康探针）。

### 与设计伪代码的三处有意偏离（都写进了模块 docstring）

1. **`evaluate` 多一个必填关键字 `auto_verify_eligible`**。E4 的资格是**目标
   元素**的属性（idempotency / risk），而 `(exp, runs, policy)` 三者里都没有：
   `ExperienceRun.effective_risk` 恒为 LOW——4.7 表第 4 行规定「风险未过的
   尝试**不写样本**」，拿它判 E4 必然恒真（假绿）。故资格由调用方经
   `eligible_for_auto_verification(element)` 算好传入；**必填无默认值**：
   忘了传是 TypeError（fail-loud），不是「默默不升级」。做成默认 False 会让
   「忘了传」与「确实不合格」不可区分。
2. **`evaluate` 返回 `VerificationOutcome`（决策 + reason + 判据明细）**而不是
   裸 `VerificationDecision`：状态事件要写 reason、报告要展示判据，在调用方
   再推一遍等于把同一套规则实现两次。
3. **`success_rate` / `distinct_runs` 一律从传入的 runs 现算**，不读
   `Experience` 上的冗余列——冗余列是给查询用的，判定必须以历史为唯一依据
   （E11：判定只吃「实际被尝试」的样本，而 `experience_runs` 按 4.7 表只装
   被尝试的那些，从 runs 现算即等价）。**附带守卫**：`len(runs) !=
   exp.sample_count` 即 `ValueError`——传了截断的列表会把「10 个样本」算成
   「5 个」，门槛静默失真且报告上看不出原因。

### E8 的标记怎么落（一处需要说明的取舍）

E8 要求 fingerprint 变化时「标记 `REVALIDATION_REQUIRED`，**不**清空或拒绝」。
但 `experience_state_events.to_status` 是 **NOT NULL**（P2-02 定档：「跳变必须
有终态」），而这类标记本身没有终态。三个选项里选了最保守的：

- ✗ 放宽 schema（要迁移，plan Task 3.1 没这个预算，且会破坏「跳变必有终态」
  这个既有不变量）；
- ✗ 借用 §11.1 的 `experience_revalidated` 事件名（语义反了：那是「已验证过」，
  这是「需要再验证」）；
- ✓ **写 `experience_state_events` 且 from == to**：同值表达「此刻仍是这个
  状态，只是被标记了」，`to_status` NOT NULL 自然满足，不动 schema。

幂等按「**最近一次跳变之后**是否已有该 reason」判定（
`has_state_event_since_transition`）——不是「历史上有没有过」：状态变化后旧标记
作废，DEGRADED→VERIFIED 之后又变了指纹必须能重新标记。

### 矩阵对应

| 行 | 覆盖 |
|---|---|
| #5 | 总体 97%（≥ 门槛 0.95）但最近 5 次里 3 次失败 → `DEGRADE`，**窗口优先于总体率** |
| #8 | 非幂等 10/10 → `KEEP_CANDIDATE(NOT_ELIGIBLE)` |
| #9 | 风险 MEDIUM 100% → `KEEP_CANDIDATE(NOT_ELIGIBLE)` |
| #11 | 90 天无新样本 → `REJECTED(STALE)`，runs 不删除 |
| #7 | fingerprint 变化 → `REVALIDATION_REQUIRED` 标记（from==to），状态不变、证据不删；`revalidate` 通过后更新 fingerprint 观测 |

> 设计 #5 的原文数字「100 次 98 成功、最近 5 次 3 失败」自相矛盾（3 次失败
> 意味着总体最多 97%）。测试用**自洽的 97%**（仍 ≥ 门槛 0.95），所以「窗口
> 优先于总体成功率」是真的被考到了：只看总体它会一直 VERIFIED。

### 边界取值（有意）

- 门槛一律**闭区间**（`≥`）：恰好 10 样本 / 恰好 0.95 / 恰好 3 个 run → 升级。
  写成 `>` 会让刚好达标的经验永远卡在 CANDIDATE，且报告上看不出原因。
- STALE 是**严格大于** `max_idle_days`：恰好 90 天不清（配置值的字面语义是
  「超过这个天数」）。
- 零样本的 Candidate 按**创建时刻**算闲置——它正是 8.2 要清的「僵尸」；因为
  「没有 last run」就豁免会让建出来没人碰过的候选永远留在库里。
- 只清 CANDIDATE：VERIFIED / DEGRADED 有各自的失效路径，「很久没用到」不等于
  「没用」（低频但有效的经验会被闲置清理误杀）。

### 接线位置（本任务只交模块）

`apply_outcome` 是「决策 → 落库」的接缝。**运行流程里谁调用它**由 Task 3.4
（`mta experience verify` / `revalidate` / `sweep`）落地——plan 的 Task 3.1
Files 清单只含 verifier/sweeper，不在 `agent/recovery.py` 或 `cli/pipeline.py`
里改接线。

- 实测：pytest **997 passed**（+48：test_verifier 31 + test_sweeper 17）。

---

## Task 4.3 完成记录（P2 指标：Experience Resolution Rate，设计 17，2026-10-05）

**Objective**：核心指标可观测（G10：随运行次数上升）。

本任务是 M4 最后一项，且带着 plan Task 3.3「接线定档」留下的**两条前置**：
① 缓存接线（归 Task 4.3）；② `validated_builds` 裁决机制三选一必须先拍板。
两条都已清（见下）。

### A. 接线前置①：`validated_builds` 裁决机制拍板 = (a) 不检查

三选一（review_p2_task33 P3-2 挂账）的结论是 **(a)**，理由从矩阵 #6 自身推出来：

- **矩阵 #6 的预期是「不直接信任，需走一次完整 Guard+执行验证，成功后追加
  1026」**。(b)（检查 + BLOCK 不计样本）把 `validated_builds` 从**记录**变成
  **硬闸门**——集合里没有当前 build 就不能执行，于是「走一次完整验证」永远无法
  发生（首次使用即被拦），集合永远学不到新 build，成为死锁。而且「因 build 不在
  集合而拦」既不是 §4.7 的 MISS 语义也不是 FAILURE 语义，4.7 表里没有它的位置；
  强行加一行会让每次 build 变化后每条 Experience 首用都记失败样本，污染 E6 滑动
  窗口与成功率。(c)（放行不追记）直接违反 E7 原文「集合只在动作真正执行成功后
  才追加当前 build」。
- **(a) 下 §7.3 的「自然裁决」由 E1 + E7 组合实现**：E1 保证每次使用（不论
  CANDIDATE / VERIFIED）都重新走完整 Runtime Guard；E7 保证执行成功才追 build。
  新 build 上的首次使用天然就是一次完整验证——正是矩阵 #6。
- 落地：设计 §7.3 追加**修订记录**（三选一的理由写全）；`experience/cache.py`
  docstring 改准（删掉「Guard BLOCK 记失败样本」的旧措辞——那是错的）；plan
  Task 3.3 的定档条目改为「已拍板」，并写明**接线时不得新增 build 检查步骤**。

### B. 接线前置②：Recovery Cache 接进引擎

- `RecoveryEngine(cache=...)`；`cli/main.py` 每次 `mta run` 新建一个
  `RecoveryCache()`（run 级生命周期：缓存不是权威数据，进程重启即空）。
- key = `cache_key(app_id, screen_id, element_id, failure_type, fingerprint)`；
  指纹计算**上移**到 lookup 之前（它是 key 的分量，纯函数、位置无关）。
- **两条红线**（plan 原文）：
  ① 命中与未命中**共用同一段**逐候选 Guard + 记账代码——缓存只省 Store 的磁盘
  lookup；测试用「缓存里有候选但这次 Guard 判 0 匹配」证明命中照样过 Guard 并
  照常记 FAILURE 样本；
  ② 缓存存的是 **ranker 的输入候选集**，排序仍在 `rank_experiences` 里发生；
  测试比较「命中缓存 vs 直接查库」时**第一条被评估的候选 id** 一致。
- 空列表**不入缓存**（review_p2_task33 P3-1 的黑洞论证）：负缓存会让 Store 侧
  后增的候选在本进程内永久失明且无任何报错；测试断言「空库 miss 后新增候选，
  同进程内立刻可见」。
- **可观测面**：`experience_lookup` 事件新增 `source`（cache|store）与
  `latency_ms`——延迟梯度指标要能分辨这两段。缓存命中**也发**该事件（它同样是
  「解析出了候选集」）；Task 2.4 的「没查库不发事件」指 `no_store` /
  `incomplete_key` 那类**根本没尝试**，语义不冲突（已同步注释与测试）。
- `cache=None`（不传）时行为与接线前**逐位一致**——有专门测试钉住「无缓存 =
  每次真查库」，防止隐式缓存。

### C. 指标聚合（纯函数）+ 报告段

- 新增 `report/experience_metrics.py`（**偏离 plan Files 清单**：plan 说改
  `report/html.py`。指标聚合是纯函数、渲染是字符串拼接，塞进 270 行的
  `html.py` 会混淆两个职责；拆成独立模块，`html.py` 只注入一段）。
  - `compute_experience_metrics(...)`：**纯函数**，输入是「已经取出来的行」
    （steps 的 detail / `experience_*` 事件 / Experience 列表 / 状态事件），
    不读库不看时钟（E13 精神）。
  - `collect_experience_metrics(...)`：本模块唯一的 I/O 区，只搬数据不判定。
  - `render_experience_metrics_section(...)`：HTML 片段。
- `report/html.py`：`render_run_report(..., experience_metrics=None)`；不传则
  不带指标段（保持「纯函数、不读库」H18）。
- `cli/main.py`：`_collect_metrics_if_any(args, pipeline)`——**库文件不存在就
  不构造 Store**（那说明本次 run 从没碰过经验库），避免「报告顺手把空库建出来」
  的最小副作用问题（review_p2_task23 P3-6 同款纪律）。
- `report/junit.py` 的「明细带 `recovered_kind`」**Task 2.4 已落地**（system-out
  里的 `recovered_kind=`），本次只补一条钉子测试，不重复实现。

### 口径决策（三处必须写清的）

1. **分母 = 全部 Recovery Attempts，取 trace `steps` 的恢复标记，不取事件数。**
   `experience_lookup` 只覆盖「真的查了库」的尝试；`no_store` / `incomplete_key`
   / `no_page_source` / `no_find_all` 四类**根本没尝试**（Task 2.4 明文不发
   事件），拿事件当分母会**系统性高估**命中率。分子同为步骤级
   （`recovered_kind == RECOVERED_EXPERIENCE`），两边同源同单位。
   → **对 plan「数据源为 trace `experience_*` 事件 + experience 库」的扩展**：
   扩到 trace 的 `steps`（同一个库），理由是事件表在语义上无法表达「没查库的
   attempt」。
2. **revert 后的「预期恢复」与真回归要分得开**（review_p2_task42 建议动作 #2）：
   Promotion 被 `git revert` 后，被转正的策略不在 find 链上，每次 run 都稳定
   多走一次恢复——这不是新回归。可判定信号：`promoted=True` 且本期仍有**成功的**
   `experience_execution`（说明其策略没在 find 链生效）。单列
   `promoted_but_recovering` 计数，并在报告里**注明这是推断**（启发式，非事实）。
3. **延迟梯度只报能测的段**：`cache` / `store` 取 `experience_lookup.latency_ms`
   按 source 分组；`llm` 取新增的 `llm_call` stage 的 `latency_ms`（只测
   `complete()` 本身，与 cache/store 的单次操作同量级才谈得上梯度）。
   `deterministic`（纯进程内，无 I/O 未单独计时）与 `promoted`（P1 find 链上的
   普通 Locator，耗时在 `steps.latency_ms` 整步里，与恢复期内部单次操作不同
   量级）**标 N/A 并写明原因，不填 0**——填 0 会被读成「快到测不出」，那是另一
   种谎。无分母的比率一律渲染 **N/A**，不是 0.0%。

### 实测

- 全量 pytest **1111 passed**（Task 4.3 新增 32 项：`test_report_experience_metrics.py`
  24 + `test_recovery_cache_wiring.py` 8）。
- Gate M2 真机复跑全绿（12 项判据、G1 18 passed）——缓存接线是生产路径改动，
  必须过真机回归。
  （首跑因网关连续 3 次 `LLM_PROVIDER_ERROR` + 后续 `wait ProfileView` 超时红，
  复跑即绿；与 2026-10-04 那次同签名，属环境抖动。）

---

## 【回填】Task 3.2 – 4.2 完成记录（2026-10-05 补记）

> 这几段是**事后回填**：Task 3.2–4.2 当时只在 `review/` 留了记录、未按惯例追加
> 本文件（Task 4.3 评审 §6-9 点出的断档）。内容从各任务的 commit message 与
> `review/review_p2_task3x|4x_*.md` 摘取，**未重新验证**——原始证据以那些文件为准。

### Task 3.2（P2-07 ranker 多候选排序，`ba3282d` + 修订 `4905682`）

- 交付：`experience/ranker.py::rank_experiences` 纯函数（E13）——信任档位
  VERIFIED > DEGRADED > CANDIDATE > REJECTED（排尾）；同档零样本排尾、
  `success_rate` 降序、新近度新→旧、`experience_id` 确定性兜底；**返回新列表
  不 mutate 输入**。`agent/recovery.py::_try_experiences` 的 Store 默认序
  （`updated_at DESC`）换入 ranker。
- 决策/偏离（登记在设计 §5.1 修订记录 + 模块 docstring）：
  - **DEGRADED 档位**：设计 §5.1 只写了「VERIFIED 优先于 CANDIDATE」，未提
    DEGRADED。定为「VERIFIED > DEGRADED > CANDIDATE」——降级是「曾经可信、
    近期失效」，仍比从未验证过的候选可信。
  - **新近度用 `updated_at` 作代理**（偏离：设计说「最近一次成功时间」）：
    `updated_at` 是「最后一次写时刻」（`record_run` / `update_status` /
    指纹 / build 任一落库都推进），不等于「最后一次成功」。补 `last_success_at`
    列是 Schema 变更，撤偏离的条件已写明。
  - **零样本 VERIFIED 排最前**（9.5 人工 Promotion 路径）：语义是「人工判断
    > 统计」，已留痕。
  - 档位内排尾限定、REJECTED 防御性排尾、naive datetime → TypeError
    fail-loud 不兜底，全部登记。
- 实测：1020 collected（+10）；修订 `4905682` 为文档与注释修订，无行为变更。
- 评审：`review_p2_task32_2026-10-05.md`（通过，3×P3 全清）。

### Task 3.3（P2-08 Recovery Cache，`4d71b9c` + 修订 `a83882b`）

- 交付：`experience/cache.py::RecoveryCache` 进程内 LRU（`threading.Lock` +
  `OrderedDict`，与 Store 同款并发纪律）；`cache_key` 五元组 sha256。
- **E1 延伸做进 API 形状**：`get` 返回裸 `list[Experience]`，缓存里不存在
  「已验证 / 可跳过」标记——「绕过 Guard」在类型上不可表达（比「调用方记得先过
  Guard」的约定强一个量级）。
- 两处公式细化：`None` 段 → `''`（f-string 的字面 `'None'` 会与真值撞车）；
  段内含 `'|'` → fail-loud（分隔符混进段会让两个签名静默撞 key）。
- 其他决策：**入出皆深拷贝**（缓存正本不被运行时突变污染）；`app_build` 不在
  键里（无失效风暴）；**空候选列表不进缓存**（防负缓存黑洞：Store 侧后增数据
  进程内永久失明且无报错）；`clear()` 仅显式运维；`hits/misses` 计数供 Task 4.3
  的延迟梯度用。
- 接线定档（本任务只交模块）：引擎接线归 **Task 4.3**（延迟梯度指标需要缓存
  真实进入执行路径），两条红线写明（命中/未命中共用同一段 Guard+记账；缓存不得
  改变 ranker 输入集语义）。**任务号勘误 4.2→4.3**。
- 实测：1034 collected（+14）；修订 `a83882b` 1036（+2 钉子测试）。
- 评审：`review_p2_task33_2026-10-05.md`（通过，4×P3 全清）。
  **挂账**：`validated_builds` 裁决机制三选一 → 已在 Task 4.3 拍板为 (a)。

### Task 3.4（`mta experience` CLI + Gate M3，`808ac8c` + 修订 `2972563`）

- 交付：`cli/main.py::cmd_experience` 五个子命令（设计 13 节）——
  `list`（`--status` 过滤，含 REJECTED 审计视角）/ `show`（字段 + 样本历史 +
  状态时间线）/ `verify`（对全部非终态跑 Verifier，PROMOTE/DEGRADE 落库，
  决策报告带 eligible 与原因）/ `revalidate`（`--fingerprint` 证据必填，非
  DEGRADED → exit 3 + 下一步指引）/ `sweep`（STALE 清理，只改状态不删证据）。
- **E4 单点接线**（review_p2_task31 P3-7 验收项）：`verify` 的资格经 Repository
  解析元素 + `eligible_for_auto_verification`；解析不到 = 不合格（fail-closed），
  报告写明 `ELEMENT_UNRESOLVED` / `REPOSITORY_UNAVAILABLE`。
- `experience/store.py`：新增 `get_experience`（按 id 直查含 REJECTED）+
  `get_state_events`（状态时间线）。
- **登记偏离**：`revalidate` 的「Guard + 执行」自动化需要设备会话 → CLI 只提供
  留痕出口（`--fingerprint` 必填 + `--evidence-run-id` 把证据挂到真实 trace run，
  落 `experience_state_events.run_id`），输出明示「证据为操作者自报 —— operator
  留痕」。自动化留待 M4+ 真机路径。
- `phase0/verify_p2_m3.py`：Gate M3 离线判据 8/8——S1 完整状态转换事件链、
  矩阵 #5（总体 0.97 仍被窗口降级）/ #6（新 build 不直接信任，成功后追加，E7
  集合语义）/ #7（fingerprint 变化 → 标记不拒绝）/ #8/#9（**经真 CLI** 非幂等与
  MEDIUM 10/10 不升级）/ #11（STALE 证据保留）/ #12（双线程 200 次并发写无丢失）
  + S8 CLI 冒烟；`out/p2_m3_gate/summary.json` verdict=PASS。
- 实测：1051 collected（+15）；修订 `2972563` 1054（+3 净增，Gate M3 重跑 8/8）。
- 评审：`review_p2_task34_2026-10-05.md`（通过，1×P2 + 3×P3 全清）。

### Task 4.1（P2-09 promoter，`f93d9f3` + 修订 `9284f6d`）

- 交付：`experience/promoter.py` 两段式 Promotion（设计 9.3）——
  `generate_proposal` 只落 proposal 表**不写文件**；`approve_proposal` 写
  `overrides/elements/<Screen>.yaml`（`origin: experience`）+ git commit +
  promoted 记账，返回 sha。
- 红线：非 VERIFIED 需 9.5 显式 `manual_override` + 理由；REJECTED 连人工路径
  也不开（终态，复活只走 E5）；已有同 id override fail-loud（不可静默覆盖）；
  `approve` 必须走 proposal，**无直写入口**。
- 9.4 两条时间线：`approve` 不改 `status`，`promoted`/`promoted_commit` 独立
  记账（`record_promotion` 落 `from==to` 事件 reason=PROMOTED）。
- 接线前置落地：`ORIGINS` 扩 `"experience"`（与写入**同批**，否则 loader 白名单
  一上线就 fail-loud）；`type` 字段定档——override 省略 type 沿用 generated（5.3）。
- resolver：experience 策略出现时链稳定为 `manual > source > experience`
  （**尝试顺序**、排链尾不丢弃、冲突记 `experience_strategy_conflict`）；
  无 experience 策略时**不做任何重排**（P1 行为保留）。
- 修订 `9284f6d`：**P2-1** 写文件与 git commit 之间补事务性（git 失败回滚文件写，
  proposal 保持 PENDING，重试即续传——不再把工具写了一半的文件误报成「人工
  override」）；**P2-2 定档 (c)**：promoter 产出的 override doc 带
  `mode: append`，§9.2「排链尾不丢弃」由**写入口**兑现（更正：上一条 commit 的
  该说法当时只在手写三源单文档成立，promoter 真实产物是 replace）；H15 注记；
  同 id 检查正则 `\s*$` → `\b`（容忍行尾注释）。
- 实测：1071 collected（+17）；修订 1074（+3）。
- 评审：`review_p2_task41_2026-10-05.md`（Task 3.4 核销 ✅；2×P2 + 4×P3 全清）。

### Task 4.2（Promotion 解耦 + 回滚 + Gate M4，`649b78f` + 修订 `06f6ea6`）

- 交付：`tests/fault_injection/test_p2_matrix_m4.py` 矩阵 #13/#14（FakeExecutor，
  **真 git 仓库不 mock**）——#13 promote 后注入连续失败 → DEGRADED 且 overrides
  **未被程序改动**（工作区 diff 干净，9.4）；#14 `git revert` → resolver 恢复
  source-only 链、Store 的 promoted 记账/状态/事件链/样本**全部原样**（9.6/E10）。
  外加 Gate M4 头条：promote 后正常 `find()` 命中、不进 Recovery。
- **promoter 同 id 定档修订**（Gate M4 真机首跑实锤的堵点）：漂移转正的主流程
  恰恰是目标元素在 overrides 已有 P1 登记——同 id 由 fail-loud 改为**并入既有
  文档 strategies 链尾**（单文档不产双 doc、git diff 可审计、resolver origin
  排序保链尾）；已含相同 experience 策略 → 重复 promote fail-loud。
- `phase0/verify_p2_m4.py`：Gate M4 真机判据（设计 18 步骤 9–10）——G1 矩阵
  pytest + G2 漂移首跑 LLM 救回 + accept → Candidate + G3 9.5 人工 promote
  （CLI 两段式）→ overrides 并入 + commit + G4 再跑 PASS/零恢复/零 LLM +
  G5 revert 后 `RECOVERED_EXPERIENCE`、Store 记账原样 + G6 空库 no-llm 仍 FAIL；
  `out/p2_m4_gate/summary.json` verdict=PASS（device_half=PASS）。
- **事故记录**：`proj` 无自己的 `.git` 时 `git -C` 会穿透到主仓库，
  `rev-parse` 判据不成立——已改为检查 `proj/.git` 存在性 + `toplevel` 断言双保险；
  首跑污染的主仓库已 `reset` 还原（源码/overrides 无损）。
- 修订 `06f6ea6`：**P2-1** `_merge_experience_strategy` 弃用
  `yaml.safe_dump_all` 整文件重序列化（手写 overrides 的头注释/行内注释会被
  **静默抹掉**——探针实锤），改**行级手术**（定位 `strategies:` 块尾插一行，其余
  字节不动），测试断言 `before in after`（全文逐字节保留）；**P3-1** 工作提交身份
  修复（`git -C proj config` 把 gate_m4 身份写进了主仓库 → 已清泄漏配置 +
  `amend --reset-author`，现 `guomingxin`，tag 重指）；P3-2 G5 判据
  附注 revert 后稳态；P3-3 docstring 注记 + G2 重试扩到环境级 `WAIT_TIMEOUT` 抖动。
- 实测：1078 collected（+4）；修订 1079（+1 注释保全测试），Gate M4 在 HEAD 重跑
  exit 0 / device_half=PASS。
- 评审：`review_p2_task42_2026-10-05.md`（通过，1×P2 + 3×P3 全清）。

---

## Task 4.3 评审修订记录（review_p2_task43 收口，2026-10-05）

评审结论「有条件通过」，2×P2 + 6×P3。**两条 P2 都是「指标口径与它自己的定义
不符」**，且评审明确建议「修完再开 M5」（M5 的端到端演示要拿 Resolution Rate
当「知识在积累」的证据）。逐条处置：

### P2-1 Promotion Rate 的分子未限定 VERIFIED（能渲染出 `300.0%`）

- 设计 §17 的定义是「Verified 中被 Promote 的比例」；实现是「全部
  `promoted=True` / VERIFIED 计数」。二者在 §9.4 的明文场景下必然分叉——
  **Promotion 后 DEGRADED 不自动撤销已进 Git 的策略**，那条经验仍在分子里却
  不在分母里。评审探针：`2 promoted / 1 VERIFIED` → `200%`；再加一条 REJECTED
  → **`300%`**。
- 修法：`promoted = sum(... if e.promoted and e.status is VERIFIED)`；dataclass
  注释写明该字段的语义就是「VERIFIED 中已 promote 的条数」（两个概念同名是这类
  口径错的高发形态）。补两条钉子测试（含渲染面：产物里不得出现 `200%/300%`）。

### P2-2 Resolution Rate 的分子用「上下文编码的标签」，整类漏计

- 分子原取 `recovered_kind == "RECOVERED_EXPERIENCE"`，但 `recovered_kind` 是
  §10 的分类标签，**会被上下文压过**：断言目标漂移的上下文
  （`context="assertion_target"`，`cli/pipeline.py` 的 aux 路径）把机制名盖成
  `RECOVERED_ASSERTION_TARGET`——而那条路径正是「aux 命中 Experience」（矩阵
  #15）。核心指标对**整整一类** Experience 恢复记 0。
- 修法：分子改按**机制名**判 ——
  `d.get("recovery_kind") == "experience" and "recovered_kind" in d`。
  第二个条件必需：`_aux_rerun_failed` 在**失败**步骤上也写 `recovery_kind`，
  只判机制名会把失败算成「已解决」。评审的六形状探针表逐条复核，**只有**
  「aux 断言·Experience 成功」由 0 变 1，其余五种不变。
- 连带：`by_recovered_kind` 仍按 §10 标签分组（一个答「谁救的」、一个答「对
  使用者意味着什么」），docstring 写明二者刻意不同。既有测试 fixture 里只写了
  `recovered_kind`、没写 `recovery_kind`——那是**生产不可能出现的形状**（真实
  恢复步骤两个键都写），已按管线真实形状补齐。

### P3-1 `revalidation_attempts` 被非跳变标记污染

- 判据 `to_status == "DEGRADED"` 会把 `mark_revalidation_required` 刻意写的
  `from == to` 标记行算成一次降级 → 真降级 1 次报成 2 次、成功率 100% 掉成 50%。
- 修法：新增 `experience/verifier.py::is_state_transition`（**「状态跳变」的
  唯一定义**，与 `has_state_event_since_transition` 的 SQL 同义），指标用它过滤。
  评审指出的「同一个概念在本仓已有权威判据、这里没用它是典型的『两套实现』」
  已通过把定义抽成具名谓词解决（SQL 版无法直接复用，已在两处 docstring 互相指认）。

### P3-2 `int()` 毫秒截断 → cache 段恒 0，梯度指标目的落空

- 三处计时都是 `int((time.monotonic() - t) * 1000)`，亚毫秒操作**一律记 0**
  （评审探针：200 次 dict.get 与内存 sqlite 的样本集都是 `{0}`）→ 设计 §17 的
  「Cache < Store」退化成 `0 < 0`；产物里已出现 `0.0ms`。
- 修法：新增 `agent/recovery.py::_ms_since`（单点）——`perf_counter()` +
  `round(delta * 1000, 3)` **浮点毫秒**；渲染层 `_ms` 在 <1ms 时保留三位小数，
  并注明「修订前的历史 trace 里 `0.000ms` 是旧截断残留」。补一条 200 次取均值的
  实证测试（均值 > 0 才说明没被截断）。
  ⚠️ 4.7 样本的 `latency_ms`（落 `experience_runs.latency_ms`，INTEGER 列）**不**
  改：那里含设备 I/O 属毫秒级，截断无实际影响，改它要动 Schema——已在
  `_ms_since` 的 docstring 写明这个边界。

### P3-3 测试文件 56 行逐行重复块 + 2 个被遮蔽的同名测试

- 实测确认评审的发现：`def test_` **26 个 / 唯一 24 个**（`test_cli_run_html_*`
  各定义两次），Python 后者静默覆盖前者——**一整块永不执行的死代码**。
  成因是我追加测试时的 `cat >>` 被重复执行（本项目已知的重复追加坑）。
- 修法：删掉重复块，追加内容改走「临时文件 + 程序化追加 + 追加后立即查重」
  （`def` 数与唯一名数对账），本次追加后 33/33 唯一。

### P3-4 渲染层不转义

- `by_recovered_kind` 的键等值原样进 HTML（评审探针：`<script>` 原样存在）。
  trace 库是**可写的外部输入**，而报告会被打开在浏览器里。
- 修法：`experience_metrics.py` 加 `_esc`（`html.escape`；`html.py` 有自己的
  同名函数但本模块被它 import，反向会成环——两处都是**同一个标准库调用**，
  不是两套实现），全部插值点套上。

### P3-5 事件采集用 `LIKE 'experience_%'`

- 12 个事件类型里有 4 个不带该前缀（`candidate_created` / `candidate_verified` /
  `promotion_proposed` / `promotion_approved`）→ **隐式白名单**，将来任何指标想
  用 `promotion_*` 事件都会静默取到 0；且 `LIKE` 里 `_` 是单字符通配符。
- 修法：改 `event_type IN (...)` 显式列举，列表由 `EXPERIENCE_EVENT_TYPES`
  生成（单一真值源，不手抄）。

### P3-6 测试名与断言矛盾 + 梯度测试的过度声称

- `..._are_absent_not_zero` 的断言其实是「键在、值为 None」→ 改名
  `..._are_none_not_zero`，docstring 写明「不在键里的是 `deterministic` /
  `promoted`（渲染层硬编码 N/A），不是这三个键」。
- 梯度测试补 docstring：`cache < store < llm` 是**自己注入的数据**，
  纯函数测试证明不了系统行为——真实梯度要等基线数据。

### 未处理（登记）

- 评审 §4 的四条「未验证/存疑」全部为**已登记的可接受项**：Gate M4 本次未复跑
  （Task 4.3 已复跑 Gate M2）、`promoted_but_recovering` 的假阳性面（报告已标
  「推断」，成因区分留待基线）、`collect_experience_metrics` 读全表 `steps` 的
  规模（当前无问题，未压测）、`status_counts` 对四态之外键的静默处理
  （`ExperienceStatus` 是四值枚举，不可达）。

### 实测

- 全量 pytest **1121 passed**（Task 4.3 首次交付 1111 → 修订后 +10）。
- 真机指标产物用修订后代码**重新生成**：`out/p2_m2_gate/experience_metrics_demo.html`
  （run1 `0.0%` → run2 `100.0%` 不变；run2 的 store 段仍显示 `0.000ms`——那是
  **修订前**的 trace 数据，页内已注明）。

---

## Task 5.1 完成记录（P2-11 Runtime Graph，设计 12.1，2026-10-05）

**Objective**：从 P1 Trace 构建实际到达的 Screen/Transition。

M5 的第一个任务，也是 `graph/` 包与 graph 库的落地（plan Task 1.2 的决策：
graph 三表**随 M5 实现定型**，独立库 `out/graph.db` + 独立版本链）。

### 交付

- `graph/models.py`：`ScreenNode` / `ScreenTransition` / `RuntimeGraph` /
  `TraceStep`（frozen dataclass——图节点是**纯值**，无跨字段一致性约束；
  与 `experience/models.py` 的 pydantic 定位不同，那里承载 E5/E7 的写入闸门）。
- `graph/builder.py`：`build_runtime_graph`（**纯函数**）+ `observed_screens` /
  `is_screen_wait` 谓词 + `read_trace_steps`（本模块唯一 I/O 区）。
- `graph/storage.py`：`GraphStore`（构造即迁移）+ `build_and_store`；
  `GRAPH_MIGRATIONS_DIR` / `GRAPH_SCHEMA_VERSION`。
- `graph/migrations/001_graph_schema.sql`：`screen_nodes` / `screen_transitions` /
  `graph_diffs` 三表 + 索引。
- `graph/__init__.py`：包导出 + `DEFAULT_GRAPH_DB`（`out/graph.db`，与
  `DEFAULT_EXPERIENCE_DB` 同款单点定义，CLI `--graph-db` 直接 import）。
- `tests/unit/test_graph_builder.py`：23 例。

### 关键决策：什么算「到达了一个屏」（本任务最实质的一处判断）

只有**两类观测**算数（E12：不做自动探索，没有证据就没有节点）：

| 证据 | 来源 | 为什么算观测 |
|---|---|---|
| `wait_screen` | `wait_for screen:X` 步骤**成功** | wait 引擎校验过 X 的 marker——屏是被**验证**过的 |
| `recovery_observed` | 恢复期 `stage: screen / outcome: FOUND` | 引擎当场用 marker 识别出了屏 |

**刻意排除的一类**：`steps.target_id` 形如 `Screen.element` 时能读出「目标登记在
哪个屏」——但那是**元数据声明**，不是运行时观测。把它当 runtime 证据会让 Runtime
Graph 混进 Source Graph 的信息，设计 12.2 的 diff（runtime vs source）就失去意义。
（真机 trace 里 `wait_for LoginView.password_field` 正是这一形态，已有专测钉住
「不产生运行时节点」。）若将来确实需要，应作为第三类证据单独加入**并同步 diff
口径**。

其他口径：

- **超时的屏 wait 不算到达**（`status` 必须 SUCCESS/RECOVERED）——等待失败就是
  没到。
- **转移的 `trigger` = 紧邻「目标屏被观测到」之前的那个步骤**
  （`<step_type>:<target_id>`）。设计 12.2 的 `CHANGED`（「同一触发条件，目标
  Screen 变化」）要靠它做连接键，所以它**进唯一键**：同一对屏由不同动作触发是
  两条不同的转移（有专测）。
- **同屏连续观测不产生自环转移**（一个屏被连点三次 = 一次访问，但
  `visit_count` 照实累加）。
- **时间精度到 testcase_run**：`steps` 表**没有时间戳**（P1 schema 如此），
  `observed_at` 取所属 `testcase_run` 的 `end_time`（缺则 `start_time`）——可得
  的最细粒度，不假装更细。
- **输出排序**：节点按 `screen_id`、转移按 `(from, to, trigger)` 排序——确定性，
  diff 与落库都靠它稳定。

### 落库语义：按范围**整体替换**而不是累加

图的权威输入是 **trace 全量**（`steps` 表已累积所有 run），所以重跑 `graph build`
必须得到同一张图（**幂等**）。累加会让第二次 build 把 `visit_count` 翻倍，而图
本身没有「这次增量是哪几个 run」可用于去重。替换还有个好处：trace 里旧 run 被
清理后图会跟着收敛，不留幽灵节点。有专测钉住「重跑两次结果相同」。

### 迁移执行器泛化（plan Task 1.2 决策的兑现）

`experience/schema_migrations.py` 的目录从硬编码改为参数（`migrations_dir` /
`pkg`），experience 侧保持原默认值**零改动**（8 例既有测试全过）；
`graph/storage.py` 传自己的目录复用同一套幂等/向后追加/fail-loud 语义。
`graph → experience` 的 import 是**对通用工具**的依赖，不是主线耦合（graph 不读
experience 的任何模型或存储，设计 §14 的「独立主线 B」仍成立）——已在两侧
docstring 互相指认。

### 顺带清掉一条 Task 5.3 的接线前置：`runs.app_build` 从未被写入

- 现状：`mta run` 只记 `metadata_build`（metadata 声明的构建），**`runs.app_build`
  一直是 NULL**（P1 遗留，全仓无消费者）。
- 影响：M5 的 build-to-build diff（设计 12.3：`mta graph diff --build 1025
  --base-build 1024`）必须按 build 分图——这一列空着的话所有真实 run 的图都会落进
  同一个空 scope，diff 无从下手。
- 修法：`cmd_run` 里 `_resolve_app_build(args)` 算一次、**两个消费者共用**
  （pipeline 的 E7 + `runs.app_build`），两条路径（真机 / `--fake-driver`）都记。
  有两条专测（metadata 有 build → 记它；读不到 → 记 `local`）。
- 该列此前无任何消费者，写入它不改变任何既有行为。

### 实测

- 全量 pytest **1146 passed**（Task 5.1 新增 25：graph 23 + `runs.app_build` 2）。
- **真机 trace 建图验证**：用 Gate M2 的 `trace_run1.db` / `trace_run2.db` 建图，
  得 3 节点 2 转移——`LoginView`（`recovery_observed`）→`HomeView`
  （`tap:login_button` 触发）→`ProfileView`（`tap:go_profile` 触发），与用例实际
  路径一致。

---

## Task 5.1 评审修订记录（review_p2_task51 收口，2026-10-05）

评审「有条件通过」，1×P2 + 3×P3 + 两条文档小修。**P2-1 被明确要求「在开 Task 5.2
之前修」**——5.2 的交付物正是 source 图，而 `upsert_graph` 的 docstring 主动写着
「按 `(app_id, app_build, source_of)` 整体替换」，等于邀请复用；不修就踩雷。

### P2-1 `upsert_graph` 的 DELETE 硬编码 `source_of=RUNTIME` → 写 source 图会静默清空 runtime 图

- 症状：两条 DELETE 把 `source_of` 写死成常量 `RUNTIME`，与自身 docstring 承诺
  的「按 source_of 整体替换」矛盾。传入 `source_of=SOURCE` 的图时执行顺序是
  「先删掉同 scope 的 **runtime** 行 → 再插 source 行」——写**声明面**把**观察面**
  静默清空（探针实测 runtime 2→0）。这不是「覆盖了另一个范围」，是数据丢失。
- 修法：**给 `RuntimeGraph` 加图级 `source_of`**（`__post_init__` 校验它与每个
  节点/转移的 `source_of` 一致），DELETE 用 `graph.source_of`。
  为什么不用 `nodes[0].source_of`：**空图**也要能回答「我要替换的是哪一面」，
  靠首节点在空图上会退化成 RUNTIME——那正是本 bug 的根因形态。
- 探针复现（真 `GraphStore` + 真 SQLite）：写完 runtime（2 节点）→ 再写 source
  → `runtime=2, source=1`（修复前是 `runtime=0`）；再重写 runtime 幂等，
  source 面不受影响。补 3 条钉子测试（写 source 不动 runtime / 混来源 fail-loud /
  `load_graph` 带回自己的 `source_of`）。

### P3-1 测试重复定义（本轮 + 存量，共 5 处）

- 本轮：`test_cli_run.py` 的 2 个新测试各定义两次（4 def / 2 唯一；文件 43 def
  但 pytest 只收集 38）；`test_graph_builder.py` 的 3 个 P2-1 钉子测试各两次。
- 存量（**非本次引入**）：`test_resolve_app_build_reads_metadata_build` /
  `..._falls_back_without_metadata` / `..._falls_back_on_bad_json` 在 `de801ef`
  时就已是重复名。
- 这是**同一类缺陷第三次**发生（前两次：`test_report_experience_metrics.py`
  56 行 ×2、`test_cli_run.py` 的 CLI 块 ×2），根因都是「`cat >>` 追加被重复执行」。
- 修法：改用 `ast` 定位顶层 `FunctionDef` 并按名字去重（逐字节相同的删一份；
  不同的保留**后者**——那是 Python 实际生效的那份），连带删掉函数前的装饰器与
  尾随空行；辅助函数 `_ns` 的重复也一并清掉。
- **固化体检**：全仓 `tests/**.py` 跑一遍「`def` 名重复」扫描，现在**零重复**
  （四个重点文件 def 数与唯一数一致：graph 27/27、cli_run 38/38、cache_wiring
  9/9、experience_metrics 33/33）。

### P3-2 `visit_count` 语义未定义（同一步两类证据记 2 次）

- 症状：同一步对同一屏给出 `wait_screen` + `recovery_observed` 时，每条观测各
  `+1` → `visit_count=2`；而测试名 `..._dedupe` 与断言（`== 2`）相反。
- 修法：**把语义定死为「访问次数」**（写进 `ScreenNode` docstring）——人只到了一
  次，只是被两条途径看到；`observed_screens` 现在把**同一步同一屏**的证据合并成
  一条（`evidence` 取并集）。测试改名 `..._count_as_one_visit` 并断言 `== 1`。

### P3-3 一步观测到两个**不同**屏 → 凭空生成 `trigger` 为空的伪转移

- 症状：一步同时给出 `wait_screen A` 与 `recovery_observed B`（A≠B）时，`zip`
  据并排的两条观测产出一条 `A→B` 转移且 `trigger=""`——既进不了 §12.2 的 CHANGED
  （无从连接），也不是真实导航。
- 修法：**同一步内多屏时以 `wait_screen` 为准**（人不可能同时站在两个屏上，而
  `wait_screen` 是被 wait 引擎**校验过**的那一个），其余丢弃；多类恢复证据的
  退化情形取字典序首个（确定性）。补专测断言「只出 `wait_screen` 那一屏、
  无伪转移」。

### 文档小修

- `DEFAULT_GRAPH_DB` 的注释改成事实：标注「**当前零消费者**，`mta graph` 是
  Task 5.3 的交付物——本常量是预留的单点」（原文用现在时，读起来像已经接好）。
- `read_trace_steps` 与 plan Task 5.3 各补一条**操作提示**：2026-10-05 之前产生的
  trace 里 `runs.app_build` 是空串，与修复后的新 run 混库会**必然**触发
  「多个 (app_id, app_build) 范围」fail-loud——CLI help 要写明「显式传
  `app_build=` 或分库」。这是有意的安全行为，不是 bug。

### 未处理（登记，均为已接受项）

评审 §4 的五条存疑全部为已登记的可接受项：`graph_diffs` 零消费者（Task 5.3 才
用，plan 要求三表同批冻结）、旧-新 trace 混库 fail-loud（有意的安全行为，已加操作
提示）、`_min_ts/_max_ts` 的字典序比较假设同格式 UTC（docstring 已声明，实测当前
成立）、`is_screen_wait` 把 `RECOVERED` 计入成功（语义合理，设计无明文，按实现
解读）。

### 实测

- 全量 pytest **1150 passed**（Task 5.1 首次交付 1146 → 修订后 +4：3 条 P2-1 钉子
  + 1 条 P3-3 专测；P3-2 的断言改写不增减数量）。
- P2-1 探针复现通过（见上）；全仓测试文件重复定义扫描**零命中**。

---

## Task 5.2 完成记录（P2-12 Source Graph，设计 12.1 的另一半，2026-10-05）

**Objective**：从 `source_metadata.json` 构建声明式导航图。

### 交付

- `graph/builder.py`（Modify，plan Files 清单唯一一项）新增：
  `declared_screens(metadata)`（纯）、`build_source_graph(metadata, *, app_id,
  app_build=None)`（纯）、`read_source_metadata(path)`（I/O 入口）。
- `graph/__init__.py`：导出三个新符号。
- `tests/unit/test_graph_source.py`：18 例。

### 实测结论（决定了本任务的实际形状）

**当前 metadata 格式不含任何导航声明。** 全仓的 `source_metadata.json`（含 `out/` 工作副本，数量随时点波动）
逐关键词扫过：`nav` / `transition` / `goto` / `navigate` / `action` / `tap` /
`push` / `segue` **全部无命中**。顶层只有
`app_version / build / generated_at / git_commit / parser_version /
screen_elements / screens`。

所以源图的形状是：**节点 = 顶层 `screens`（声明列表）；转移如实为空**
——正是 plan step 1 明写的「metadata 无导航声明时如实为空，不推测」（E12）。

### 两处必须写清的判断

1. **节点取 `screens`，不取 `screen_elements[].name`。** 二者**不同名**：真机
   metadata 里 `screens` 含 `SpikeSheet`/`SpikeTab`，而 `screen_elements[].name`
   含 `SpikeScreenRoot`/`SpikeTabScreen`——后者在 `generated/<build>/elements/`
   下，是**元素组**不是屏（`screens/` 目录里没有它们）。`screens` 与 Repository
   的屏 id 同源：扫描器为每个条目写一份 `screens/<id>.yaml`（真机实测 10 个屏
   文件与 `screens` 列表逐项一致）。拿 `screen_elements[].name` 当节点会让源图与
   运行时图**不同名**，diff 全变 ADDED/NOT_OBSERVED——有专测钉住这条排除。
2. **`visit_count` 恒 0、`evidence=('source_declared',)`、`first/last_seen` 取
   `generated_at`。** `visit_count` 的语义是**访问**次数，声明面没有「访问」这回
   事——不为这个字段编一个别的含义（比如「元素个数」）；`generated_at` 回答的是
   「这份声明是什么时候生成的」，是可得且诚实的时间。

### 坏输入不吞（与「如实为空」的边界）

- **`screens` 键不存在** → `[]` + 空图（老/最小 metadata 对屏一无所知，如实为空，
  **不报错**）；
- **`screens` 存在但不是 list / 元素非字符串或空** → `ValueError`；
- **文件不存在 / 非法 JSON / 顶层非对象** → `FileNotFoundError` / `ValueError`。
  `mta graph build --from-source` 拿到坏文件时静默产出空图，会让「声明面没有屏」
  与「文件读坏了」在报告上长得一样。
- **空图也带对的 `source_of=SOURCE`**（Task 5.1 P2-1 的教训：替换范围靠图级字段，
  空图退化会让另一面被误删）——有专测。

### 真机端到端（源图 vs 运行时图，同一 `(app_id, build)`）

> ⚠️ **前提（review_p2_task52 P3-1 补记）**：这组数字要求两面**显式对齐到同一
> scope**——真 metadata 的 `build` 是 `"local"`，而 Gate M2 的 `trace_run1/2.db`
> 的 `runs.app_build` 是**空串**（早于 Task 5.1 的写入修复），所以建运行时图时
> 必须显式传 `app_build=""`。用默认值建两面会得到不同 scope（`'local'` vs
> `''`），差集就不成立。**该分叉本身已在本次修订里消除**（见下方 P3-1 记录）。

用 Gate M2 的 `generated/local/source_metadata.json` + `trace_run2.db`（显式
`app_build=""` 对齐）：

| 面 | 节点 |
|---|---|
| 源图（声明） | 10 个：DetailView / HomeView / LoginView / ProfileView / SearchView / SpikeNavDetail / SpikeNavRoot / SpikeSheet / SpikeSheetHost / SpikeTab |
| 运行时（观测） | 3 个：LoginView / HomeView / ProfileView |
| **声明有、运行时没到**（→ Task 5.3 的 `NOT_OBSERVED`，矩阵 #16） | 7 个 |
| 运行时到了、声明没有（→ `ADDED`） | 0 个 |

落库两面互不干扰（`scope` 分别 `('...', '', 'runtime')` 与 `('...', 'local',
'source')`）——5.1 修的 P2-1 在真 builder 上再次验证。

### ⚠️ 给 Task 5.3 的提醒（写进 builder docstring 与 plan）

**源图没有转移** ⇒ 转移级的 `CHANGED` / `REMOVED` 在「扫描器开始输出导航声明」
之前**无数据可判**；diff 实际有数据的是节点级的 `ADDED` / `NOT_OBSERVED`（矩阵
#16 正是后者）。这是 metadata 格式的**能力边界**，不是实现缺口——如果 5.3 硬要
演示 `CHANGED`，需要先扩扫描器输出导航声明（独立立项），而不是在 diff 里编。

### 实测

- 全量 pytest **1168 passed**（Task 5.2 新增 18）。
- 全仓测试文件重复定义扫描**零命中**（连续第二次）。

---

## Task 5.2 评审修订记录（review_p2_task52 收口，2026-10-05）

评审「有条件通过」，**4×P3**（无 P2）。其中 P3-1 被要求「Task 5.3 接线前处理」、
P3-3 被标为「收益最高」。

### P3-1 build id 的兜底是**两套实现**：源图侧 `or ""` vs 运行时侧 `or "local"`

- 症状（同一概念两处实现、兜底值不同）：

  | metadata 输入 | 源图 `app_build`（旧） | 运行时侧 |
  |---|---|---|
  | `{"build": "1026"}` | `"1026"` | `"1026"` ✅ |
  | 无 `build` 键 / `None` / 空串 | `""` | `"local"` ❌ 分叉 |

  分叉时 `build_source_graph` docstring 承诺的「源图范围应与运行时图对齐」不成立
  → diff 找不到同一 scope 的两面。
- **同一个问题在真机上已经显形**：真 metadata 的 `build` 是 `"local"`，而 Gate M2
  的 `trace_run1/2.db` 的 `runs.app_build` 是空串 → 用默认值建两面会得到
  `('...','local')` vs `('...','')`。Task 5.2 声称的「真机同 scope 10 vs 3」只有在
  **显式传 `app_build=""`** 时才复现（该前提已补进本文件 Task 5.2 段）。
- 修法（单点化，三处）：
  1. `DEFAULT_APP_BUILD` 从 `cli/pipeline.py` 移到 **`source/build_identity.py`**
     （单一字面量；`cli/pipeline.py` re-export 以保住既有导入点）；
  2. 新增 `build_identity.resolve_app_build(metadata) -> str`——**build id 的唯一
     解析规则**（非空白字符串，否则兜底）；
  3. `build_source_graph` 与 `cli/main._resolve_app_build` **都调它**；
     顺带新增 `build_identity.read_metadata(path)` 作为**全仓唯一的 metadata JSON
     解析点**，`graph.read_source_metadata` 只翻异常类型（`BuildIdentityError`
     → `ValueError`），不再自己 `json.loads`。
- 补 2 条测试：源图与运行时侧对「无 build / None / 空串 / 纯空白」**取同值**；
  以及用 `inspect.getsource` 断言运行时侧确实调用共用入口（防有人改回去）。
- ⚠️ 留给 Task 5.3：`graph diff` 应在两面 scope 不同时 **fail-loud**（而不是安静地
  报满屏 `NOT_OBSERVED`）——已写进本记录，接线时落实。

### P3-2 `generated_at` 不做类型校验（同一函数里两套标准）

- 探针实测：`123` 被 SQLite 的 TEXT 亲和性**静默落库为 `'123'`**；`['a']` /
  `{'x': 1}` 直到 upsert 才炸 `ProgrammingError: Error binding parameter 7`——
  离现场（build）很远。而同一个函数对 `screens` 的校验很严。
- 修法：新增 `_declared_time(metadata)` 类型闸门——`str | None`，非字符串/空白即
  `ValueError`（在 build 现场炸）。补 3 条测试（五种坏类型 / `None` 与 ISO 串放行 /
  「错误在 build 就抛，库里什么都没写」）。

### P3-3 「固化体检」并未固化（收益最高的一条）

- 评审实测：仓库里**没有任何**重复定义守护测试（`tests/` 下 `import ast` /
  `FunctionDef` 零命中）——`aadb8b3` 的「固化体检」只是**手工跑过一次**。
- 修法：新增 `tests/unit/test_repo_hygiene.py`（3 例）：
  1. `test_no_duplicate_top_level_definitions`——扫仓库自己的 Python（`tests/` +
     各源码包，排除 `out/`/`build/` 等生成物），`ast` 取顶层 `FunctionDef` /
     `AsyncFunctionDef` / `ClassDef` 名，断言无重复；
  2. `test_test_files_collect_count_matches_definitions`——`tests/**/test_*.py` 的
     `def test_` 数与唯一名数对账（`--collect-only` 的 ast 等价物）；
  3. `test_hygiene_scan_actually_covers_the_repo`——**防空转**：扫到的文件数有下界、
     根级 `conftest.py` 与 `tests/` 必须覆盖到、`out/` 必须被排除。
- **探针验证它真的会红**：临时注入一个重复定义 → 两条断言同时失败并**指名报出**
  文件与重复名；移除后恢复绿。
- **它上线后立刻抓到了本次修订自己的重复追加**：`cat >>` 追加 P3-2/P3-4 的 4 条测试
  被重复执行（4 个名字各 2 次）——守护在第一次全量回归时就报了出来。这正是它存在的
  意义：**同类缺陷第四次发生时，不再靠人记得手工跑一遍**。

### P3-4 `declared_screens` 接受纯空白屏名

- 判据 `not item` 对 `"  "` 放行 → 会成为一个名叫两个空格的节点。
- 修法：改 `not item.strip()`，与措辞「非空字符串」严格一致；补 4 种空白形态测试。

### 文档小修（评审建议）

- 「全仓 165 个 `source_metadata.json`」→ 改成不带数字的措辞（`out/` 每次 run 都
  新增副本，数量随时点波动）。
- `screen_elements[].name` 的后果从「diff **全变** ADDED/NOT_OBSERVED」改成
  「真机 10 个名字里 8 个相同、**2 个错位**」——决定不变，但理由不能夸大（用一个
  夸大的后果支撑一个正确决定，将来会被当反例）。
- 删掉 `read_source_metadata` 里多余的局部 `import json`（改为委托共用解析点后
  自然消失）。
- Task 5.2 段的「真机同 scope 10 vs 3」补上前提（**显式 `app_build=""`**）。

### 实测

- 全量 pytest **1177 passed**（Task 5.2 首次交付 1168 → 修订后 +9：源图 6 + 卫生 3）。
- 全仓重复定义守护**已固化**（不再依赖手工）；本次修订期间的重复追加由它当场抓出
  并清除。

---

## Task 5.3 完成记录（P2-13 Graph Diff 五类 + build-to-build + `mta graph` CLI，2026-10-06）

**Objective**：设计 12.2 / 12.3：五类分类 + build-to-build + `mta graph` CLI。

### 交付

- `graph/diff.py`（新）：`diff_graphs`（**纯函数**）+ `DiffEntry` / `GraphDiff`
  值对象 + 五个 kind 常量 + `transition_key`。
- `graph/storage.py`：`record_diff`（按 `(app_id, base_build, build)` 整体替换）
  + `load_diff`——`graph_diffs` 表（Task 5.1 建）首次有消费者。
- `cli/main.py`：`mta graph` 子命令组（`build` / `diff` / `show`）+ `cmd_graph`。
- `tests/unit/test_graph_diff.py`：29 例（纯函数 20 + CLI 端到端 9）。

### 方向约定（五类的措辞都是站在「当前面」上说的）

`base` = **参照面**（Source 声明 / 旧 build），`new` = **当前面**（Runtime 观测 /
新 build）。同一个纯函数服务两种用法：

- **Runtime vs Source**（同 build）：`diff_graphs(source, runtime)` → 五类判定；
- **Build-to-Build**（设计 12.3）：`diff_graphs(old_runtime, new_runtime,
  allow_build_change=True)`。

### 三处口径（都从设计字面推出来）

1. **REMOVED 默认不可达——这是设计要的。** 设计明文「仅当 Source 明确标注过、
   **且** Runtime 多次尝试确认不存在」。后者需要**负证据**（「试过、确认不在」），
   而运行时图只记「到达了什么」（Task 5.1 口径：超时的屏 wait 不算到达）。故负
   证据做成显式入参 `absent_confirmations`（键 = 条目身份串，节点用 `screen_id`、
   转移用 `from->to@trigger`），阈值 `DEFAULT_REMOVAL_CONFIRMATIONS = 3`。
   **当前无生产者** ⇒ 真实数据上 REMOVED 不可达，正是「默认优先标 NOT_OBSERVED」
   （矩阵 #16）。要让它可达需扩运行时图记录负证据——**独立立项**，不在本任务顺手做。
2. **UNKNOWN = 参照面缺失/为空、无法判定。** 库里「没构建过」与「构建了但为空」
   不可区分（空图不留行），而两者结论相同：**参照面为空时，当前面独有的条目既可能
   是「新增」也可能是「参照缺失」**。所以参照面为空时**不产 ADDED**，全部标 UNKNOWN
   并写明原因。这顺带堵住一个静默误读——多数现成 metadata 没有 `screens`，源图本
   就为空，照常判 ADDED 会把运行时全部算成「未声明」。
3. **跨范围 fail-loud。** `app_id` 不同 → `ValueError`；`app_build` 不同而没显式
   `allow_build_change=True` → `ValueError`（否则 scope 不重叠会安静地报满屏
   `NOT_OBSERVED`）。

### CLI（设计 13 节）

```bash
mta graph build [--from-trace T] [--from-source M] [--bundle-id ID] [--build B] [--graph-db G]
mta graph diff  --build B [--base-build A] [--bundle-id ID] [--graph-db G] [--save]
mta graph show  [--bundle-id ID] [--build B] [--graph-db G]
```

- `build`：两个 `--from-*` 都不给 → **两面都建**（默认路径：一条命令备齐）。
  空图**必须说出来**（`GRAPH WARN`）——运行时图为空 = trace 里没有任何屏观测；
  源图为空 = metadata 没声明屏（后者在现成 metadata 里是多数）。
- `diff`：给 `--base-build` → build-to-build；否则「源图 vs 运行时图」。
  **退出码**：有真实变化（非 UNKNOWN）→ 1，CI 可用它判「图变了」；无变化或仅
  UNKNOWN → 0（UNKNOWN 是「没判定」，不是「有变化」）。
- **scope 对齐守卫**（review_p2_task52 P3-1 要求的接线前置）：要比较的那一面在
  请求的 build 上没有图、但**在别的 build 上有** → exit 3 并**点名那个 build**。
  此时安静地判 UNKNOWN（或满屏 NOT_OBSERVED）会把「scope 没对齐」伪装成「声明
  缺失」，而前者是可操作的配置问题。

### 冒烟中发现并修掉的一个真 bug

`read_trace_steps(app_build="")` 用 SQL 等值匹配，而库里旧 run 的 `app_build` 是
**NULL**（scope 值里的 `""` 是归一化后的形式）→ `WHERE app_build=''` 匹配不到任何
行，`mta graph build --build ""` **静默得到空图**。修：过滤改
`COALESCE(r.app_build,'')=?`。这是「归一化值与存储值不同域」的典型坑。

### 真机端到端（Gate M2 的 trace + metadata，显式对齐 `--build ""`）

```
NOT_OBSERVED  screen DetailView / SearchView / SpikeNavDetail / SpikeNavRoot /
                     SpikeSheet / SpikeSheetHost / SpikeTab        ← 7 个
ADDED         transition LoginView -> HomeView (trigger=tap:login_button)
ADDED         transition HomeView -> ProfileView (trigger=tap:go_profile)
合计：ADDED=2, REMOVED=0, CHANGED=0, NOT_OBSERVED=7, UNKNOWN=0   → exit 1
```

- **`NOT_OBSERVED = 7` 正是矩阵 #16**（Source 有、用例从未覆盖 → 不是 REMOVED）。
- `ADDED = 2` 是已登记的数据面边界：源图没有转移（metadata 无导航声明），所以
  运行时的转移全算「未声明」。**没有为了演示 CHANGED 去编数据**。
- scope 未对齐时（源图在 `1026`、运行时图在 `1025`）→ exit 3 + 点名 `1026`。

### 累积的接线前置（本任务一次性消费完）

| 前置 | 来源 | 状态 |
|---|---|---|
| `runs.app_build` 从未被写入 | Task 5.1 顺手修 | ✅ 消费 |
| `upsert_graph` 幂等替换（重跑不翻倍） | Task 5.1 P2-1 | ✅ 消费 |
| `--from-trace` = trace **全量**（不是单 run） | Task 5.1 定档 | ✅ 实现 |
| 源图无转移 ⇒ 转移级 `CHANGED`/`REMOVED` 无数据可判 | Task 5.2 实测 | ✅ 未编造，如实报 |
| 两面 scope 不同时 `graph diff` 要 fail-loud | Task 5.2 P3-1 | ✅ 实现（exit 3 + 点名） |
| 旧 trace（`app_build` 空串）与新 run 混库必 fail-loud | Task 5.1/5.2 文档 | ✅ 已写进 plan/CLI help |

### 实测

- 全量 pytest **1206 passed**（Task 5.3 新增 29）。
- 真机端到端见上；`graph_diffs` 落库 9 行，重跑按范围整体替换（幂等）。
