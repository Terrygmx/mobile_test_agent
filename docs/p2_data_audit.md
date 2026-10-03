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
