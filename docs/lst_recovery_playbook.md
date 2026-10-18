# 乐速通失败恢复演练（第三项）

## 0. 目的与边界

本演练验证真实恢复链路：

```text
ELEMENT_NOT_FOUND
  -> local reconciliation: DRIFT
  -> LLM candidate: return
  -> redispatch
  -> RECOVERED
  -> 人工 review accept
  -> Experience Candidate
  -> 再跑: RECOVERED_EXPERIENCE（LLM calls = 0）
```

不改乐速通 App 源码，也不把故意错误的定位放进正式
`repository/overrides`。故障定义隔离在：

```text
repository/fault_injection/lst_recovery/{elements,screens}/
suites/lst_recovery/recovery_drift_001.yaml
```

## 1. 故障设计

用例先正常进入搜索页，再点击：

```yaml
target: LSTDynamicSearchView.lst_recovery_search_back
```

`lst_recovery_search_back` 的 accessibility id 被故意写成不存在的值；
真实返回按钮仍登记为：

```yaml
id: search_back
type: button
strategies:
  - {type: accessibility_id, value: return, origin: manual}
```

LLM 从脱敏后的搜索页树中选出 `return`。候选必须按**定位策略值**解析到
登记元素 `search_back`，不能拿值直接当元素 id——两者在真实业务里未必同名。
`Repository.resolve_locator()` 是这条解析的唯一入口，LLM 校验链与
Experience Guard 复检共用它。

## 2. 首次运行

前置：

- RemoteXPC 隧道保持运行（需要 sudo，由用户执行）；
- Appium 在 `127.0.0.1:4723`；
- 手机已安装本机 `local-uat` 包；
- 手机已解锁，WDA 启动时按提示输入密码；
- 设置 `LLM_API_KEY`；非默认网关再设 `LLM_BASE_URL` / `LLM_MODEL`。

```bash
cd ~/PycharmProjects/Mobile_test_agent
export LLM_API_KEY='...'
make lst-recovery-device LST_ENV=uat
```

预期：

- 用例终态 `RECOVERED`，退出码 `5`；
- `make` 目标正常结束（`0`/`5` 都算成功；其他退出码才失败）；
- trace 步骤的 `detail_json.recovery.stages` 含
  `reconcile.outcome=DRIFT`、`llm.outcome=candidate`、
  `validate.outcome=EXECUTE`；
- `recoveries` 有一条 `kind=LLM`；
- `recovery_reviews` 有一条 `PENDING`。

2026-10-18 生产包实测（`local-prod`，App/metadata commit 均为
`5196852c`）：

```text
模型: group/auto-gpt-6-luna
run_6da4010e: RECOVERED / llm_calls=1 / exit 5
LLM candidate: return / confidence 0.98 / latency 4804.6 ms
recoveries: kind=LLM
review #1: PENDING
```

模型选择注意事项：`group/group` 在同一恢复 prompt 上仅 1/3 命中
（置信度 0.3/0.7/0.0），不适合作为本演练的默认模型。
`group/auto-gpt-6-luna` 实测 2/3 命中（0.98/0.0/0.93），本次成功。
`min_confidence=0.85` 是 fail-closed 安全阈值，不要为跑绿而调低。

## 3. 人工确认并生成 Experience

```bash
.venv/bin/python -m cli.main review list --db out/lst-recovery.db

.venv/bin/python -m cli.main review accept <review_id> \
  --db out/lst-recovery.db \
  --exp-db out/lst-recovery-experience.db \
  --out out/lst_recovery_patch.yaml \
  --note "乐速通搜索返回漂移真机验证"
```

`--out` 必须给：accept 会把补丁写到该路径，但**不会**自动修改正式
`repository/overrides`（H15）。本演练不需要把补丁合入正式 overrides；
Experience 已足以驱动第二次运行。

## 4. 第二次运行

```bash
make lst-recovery-device LST_ENV=uat
```

预期：

- 用例终态 `RECOVERED`，退出码 `5`；
- `make` 目标正常结束（`0`/`5` 都算成功）；
- `recovered_kinds == ["RECOVERED_EXPERIENCE"]`；
- `runs.llm_calls = 0`；
- `recoveries.kind = EXPERIENCE`，不再新建 review。

即使环境里没有 `LLM_API_KEY`，已有 Experience 时仍可走第二条路径；
此时 trace 会如实记录 `llm_enabled=0`。首次演练仍需配置 LLM；未配置时
恢复链会停在 `llm.outcome=disabled`，不会被误报为 LLM 已参与。

2026-10-18 实测：

```text
run_5d05c94a: RECOVERED / llm_calls=0 / exit 5
recovered_kind=RECOVERED_EXPERIENCE
recoveries: kind=EXPERIENCE
recovery_reviews: 仍只有 #1（未新增）

# Makefile exit 5 语义修复后，无 LLM_API_KEY 再跑两次：
run_4e7209b7 / run_2ba80509: RECOVERED / llm_enabled=0 / llm_calls=0
make 正常退出 0
Experience: sample=3, success=3, success_rate=1.0
```

## 4.1 升级 VERIFIED（4.4 资格 + 4.5 门槛）

自动升级门槛：`min_samples=10` / `min_success_rate>=0.95` /
`min_distinct_runs>=3`，且目标元素必须解析出 `idempotency=IDEMPOTENT` 与
`risk=LOW`（E4）。

真机补跑到 10 个不同 run 后（累计 `sample=10 / distinct=10 / rate=1.0`）：

```bash
.venv/bin/python -m cli.main experience verify \
  --exp-db out/lst-recovery-experience.db \
  --overrides repository/overrides \
  --generated repository/generated/local-prod
```

实测 `PROMOTE_TO_VERIFIED/THRESHOLD_MET`，状态事件
`CANDIDATE→VERIFIED`，operator=`cli-verify`。

⚠️ E4 资格经 Repository 解析目标元素。故障演练的
`lst_recovery_search_back` 只存在于隔离的
`repository/fault_injection/lst_recovery` 里，正式 overrides 没有同名
登记时 `experience verify` 会判 `ELEMENT_UNRESOLVED`（fail-closed）。
正式 `LSTDynamicSearchView.yaml` 因此登记了同名元素（策略值仍是那个
不存在的故障值），让资格判定拿到与真实 `search_back` 相同的
LOW/IDEMPOTENT 元数据；运行时演练仍用 `--overrides
repository/fault_injection/lst_recovery`，故障注入效果不变。

## 5. 离线回归

```bash
.venv/bin/python -m pytest \
  tests/fault_injection/test_lst_recovery_drift.py \
  tests/unit/test_repository.py -q
```

离线测试覆盖同一语义：LLM 首轮恢复、人工 ACCEPT、Experience 次轮恢复，
并断言次轮 LLM 零调用。

Promotion 与升级门槛的离线覆盖：

```bash
.venv/bin/python -m pytest \
  tests/fault_injection/test_p2_matrix_m4.py \
  tests/unit/test_promoter.py \
  tests/integration/test_runner_fakedriver.py -q
```

其中矩阵 #13/#14 覆盖「DEGRADED 不自动撤销 Promotion」与
「`git revert` 回滚后 Experience Store 历史不受影响」；runner 集成测试
钉住 `locator_strategy` 记录的是**真正命中**的策略（Promotion 后链尾
experience 策略生效时不能写成 manual）。

## 5.1 Promotion（9.1–9.3）与 Promotion 后验证

两段式，中间是人工：

```bash
# 第一段：只落 proposal 表，不写任何文件
.venv/bin/python -m cli.main experience promote exp_02c5849a5f91 \
  --exp-db out/lst-recovery-experience.db --operator <你的名字>

# 人工 review diff 与 evidence_summary 后，第二段落地
.venv/bin/python -m cli.main experience promote exp_02c5849a5f91 \
  --approve <proposal_id> \
  --exp-db out/lst-recovery-experience.db \
  --repo-root <git 仓库根> --operator <你的名字>
```

approve 写 `repository/overrides/elements/<Screen>.yaml`（同 id 并入
strategies 链尾，`origin: experience`）+ git commit + `promoted` 记账；
**不改** Experience.status（9.4 两条独立时间线）。撤销 = `git revert
<sha>`（E10）。

2026-10-18 实测：

```text
proposal: prop_6d2c291ab89e APPROVED
commit:   b82dd1266b3c3ca7d74633bba65ff16223215a6f
experience: status=VERIFIED, promoted=True, sample=10, rate=1.0
```

Promotion 后用 `make lst-recovery-promoted-device LST_ENV=prod`
（正式 overrides、不加载故障目录）验证 Gate M4：

```text
run_745717d6: PASS / llm_calls=0 / recoveries=0 / reviews=0
steps: 9/9 SUCCESS
steps.locator_strategy = {"strategy": "accessibility id", ...}
```

用例是 `PASS`（exit 0）而不是 `RECOVERED`——experience 策略已进正常
Locator 链，第二次（`return`）命中，不再触发 Recovery。命中的策略值
由 `Executor.find` 标注在返回元素上，StepRunner 落 trace；没有该标注
（测试替身）时回落首条策略，与 P1 口径一致。

## 6. 排错

| 症状 | 检查 |
|---|---|
| `TARGET_UNREGISTERED` | 候选值是否登记在真实 metadata / 隔离 overrides；确认走 `resolve_locator` |
| `LLM_TARGET_SCREEN_MISMATCH` | 当前屏 marker 是否识别为 `LSTDynamicSearchView` |
| `LLM_TARGET_TYPE_MISMATCH` | `search_back` 登记类型是否仍为 `button` |
| `LLM_PROVIDER_ERROR` | `LLM_BASE_URL` / `LLM_MODEL` / 网关连通性 |
| `WAIT_TIMEOUT` 返回首页 | 重试后是否真的点击了 `return`；trace 的 `validate` 与 `redispatch` 段 |
| Build Identity exit 3 | `--metadata` 必须仍指向 `repository/generated/local-uat/source_metadata.json` |

真机 trace 查法：

```bash
sqlite3 out/lst-recovery.db \
  "SELECT id, step_type, status, failure_type, detail_json FROM steps ORDER BY id;"
sqlite3 out/lst-recovery.db \
  "SELECT id, kind, expected_target, candidate_target, candidate_type, screen FROM recoveries;"
sqlite3 out/lst-recovery.db \
  "SELECT id, review_status FROM recovery_reviews;"
sqlite3 out/lst-recovery.db \
  "SELECT run_id, llm_enabled, llm_calls FROM runs ORDER BY start_time;"
```

## 7. 隔离与提交纪律

- `out/lst-recovery*.db`、导出的补丁与日志都是诊断产物，不提交；
- `repository/fault_injection` 只承载演练定义，不替代正式 overrides；
- 正式业务用例仍用 `repository/overrides` + `--generated`，避免故障定义
  影响日常回归；
- lint 故障用例时同时给 `--generated repository/generated/local-uat`，
  防止 generated 与隔离目录的同名元素造成短名歧义。
