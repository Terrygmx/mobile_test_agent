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
