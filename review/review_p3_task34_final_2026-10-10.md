# Task 3.4 修订核销复核（round 2 / final）

- **复核对象**：`b1e2b48`（round 1 修订），父 `feff5ec`（本体）
- **复核方式**：独立只读子代理，逐条探针核销 + 新缺陷扫描；12 组探针，全部在
  worktree 副本上做（结束零改动）

## 结论

**有条件通过** —— 11 条中 10 条经独立探针**真核销**；**P3-3 的 `recovery.guard`
一条并未真正核销**（仍 0 red）且审计记录**过度声称**；另发现 2 条新缺陷
（1×P2 + 2×P3）。**全部已修订**（见 `docs/p3_data_audit.md` 的 round 2 记录）。

## 逐条核销

| # | 首轮发现 | 核销 | 关键证据 |
|---|---|---|---|
| P0-1 | 真机分支引用未导入 `os` | ✅ | 直调装配完成、四件套逐项回填；删 `import os` → **2 failed**（修前 0 覆盖）；新测试**确实跑到底** |
| P1-1 | 语义校验短路掩蔽已知矛盾 | ✅ | 两种字典序均 `contradicted`；还原短路 → 1 failed；全零观测仍 `unknown`；多矛盾 `observed` **两条都在** |
| P1-2 | `ignored_fields` 零消费 | ✅ | notes 可见 + YAML 无 `CRITICAL`；关掉消费 → 1 failed |
| P2-1 | 指纹闸门不存在 | ✅ | `None`/`""` 都拒；去掉闸门 → 1 failed |
| P2-2 | `dry_run_detail` 不落库 | ✅ | 端到端落库可读；`to_store_dict`/`from_store_dict` **两侧都带**（首轮漏键已修）；不传 → 1 failed |
| P2-3 | 装配失败泄漏会话 | ✅ | `quit count: 1` 且**原始异常原样传播**（未被吞） |
| P2-4 | 嵌套 spec 未知键不过滤 | ✅ | 三处全过滤；**「从 schema 派生」为真**（运行时注入 `goto` → 自动放行）；关掉 → 2 failed |
| P3-1 | 未用 import + 注释不符 | ✅ | 首轮 4 处已不在未用集；剩余 2 处在父提交就未用（非本次引入） |
| P3-2 | `utcnow` 零消费 / 无全键断言 | ✅ | 非测试消费者确为 0；docstring 如实；删键 → 2 failed |
| P3-3 | 真机分支零覆盖 | ⚠️ **部分** | `repo=None` → 1 failed ✅；删 `postcondition_checker` → 1 failed ✅；**删 `recovery.guard` → 全量 0 red** ❌ |
| P3-4 | 一次性可迭代时审计数字撒谎 | ✅ | 确认**真的传了生成器**且不走会 `list()` 的 helper；不物化 → 1 failed |

## 新发现

### P2-new：`recovery.guard` 并未核销 + 审计过度声称
- 审计原话「补测后 `repo=None` 与 `recovery.guard` 两条**不再是零覆盖**」——**实测证伪**：
  该行改 `if False:` → 全量 **1807 全绿**。三条新单测都构造 `SessionPipeline(...)`
  而不设 `recovery`（默认 `None`），该分支**从未被进入**。
- **不是等价变异**：装配前 `guard=None`、装配后变成 `runner.guard`（同一对象）。
  该行被删 → `_make_guard_check` 因 `self.guard is None` 直接 `return None` →
  **10.1 的 Guard 复检在真机上静默关闭**。生产可达（`cli/main.py` 无条件构造
  `RecoveryEngine(...)`，`wire_device_stack` 随后调用）。
- 定级 P2：**P0-1 的同族缺陷**（声称已覆盖而实际没有），且修订**自陈已清**。
  `fault_injection/fi_support.py` 手工复刻了这句接线（说明它是被认定的生产语义），
  却没有一条测试走 `wire_device_stack` + `recovery` 的路径。

### P3-new：迁移 002 被原地编辑 → 升级路径炸
- 实测：旧版 002 建库 → 新 store 打开 → 读 `IndexError` / 写 `OperationalError`。
- 本仓先例相反（`graph/migrations/002` 头部：「需求后到 → 走迁移链加列，不改 001」）。
- 定级 P3：`agents/migrations/002` 从未进 `main`，`P3-1010` 未 push、无 remote，
  主仓也无真实 `out/agent.db` → 无已发布库会踩到。

### P3-new：`wire_device_stack` docstring 宽于实现
「顺序与语义与抽出前**逐字相同**」，而 P2-3 修订有意新增了失败路径的 `quit()` 收尾。

## 数字对账（全部一致，一处除外）

| 声称 | 实测 | |
|---|---|---|
| 1807 passed | 1807 | ✅ |
| 1794 / 1691 | 1794 / 1691 | ✅ |
| +13 拆解 | +3 / +8 / +2 | ✅ |
| 探针 P2-4 1 failed | **2** | ⚠️ 审计少记 1 |
| 「`recovery.guard` 不再是零覆盖」 | **0 red** | ❌ |

## 未验证 / 存疑（复核者自陈）

1. `test_r16_2` 的 `A or B` 断言**仍在**，实测两种「漏符号」变异下**都仍绿**
   ——本身依旧是误绿来源（但新测试已变红，防线已补）。
2. 迁移升级路径按「无已发布库」定 P3；若团队认为 `P3-1010` 上有共享 agent.db，
   应升级 P2 并补 003 + 升级测试。
3. `existing_cases` 物化对**大用例集性能**未测（20 条无影响）。
4. `recovery.guard` 分支在真机上的实际行为未端到端跑（无设备）。
5. 未复跑首轮 35 组探针全表，只跑了与本 11 条相关的 12 组。
6. 迁移 `NOT NULL DEFAULT` 对**既有行**的回填未验证（仅在新建库路径验证）。
