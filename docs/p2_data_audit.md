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
