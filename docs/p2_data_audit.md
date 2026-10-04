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
  「两套校验合一」同款纪律）；审计脚本改为消费该片段，签名与输出列不变。
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

