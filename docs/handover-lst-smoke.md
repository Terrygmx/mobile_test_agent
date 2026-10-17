# 交接：乐速通真机冒烟与首页 / tab 导航（P4-1017-mta）

> 本文记录 **2026-10-17 首页搜索与 tab 导航链路接入后的真实状态**。平台整体
> 设计见 `docs/handover.md`；真机接入与排错细节见
> `docs/lst_update_playbook.md`。

## 0. 结论

上一轮 1–5 项已完成：

1. 把 `optional` 语义接进真实执行路径 `StepRunner`；
2. 全量单测通过；
3. 在本机重新构建的 `local-uat` 包上完成真机冒烟；
4. 提交 MTA 修复；
5. 提交乐速通测试支撑改动。

| 项目 | 结果 |
|---|---|
| MTA 分支 / 实现 commit | `P4-1015-mta` / `a52b322` |
| MTA 交接文档 | 本文件所在 commit |
| 乐速通分支 / commit | `dev` / `5196852c` |
| 全量单测 | **2361 passed，0 failed**（69.57s） |
| 本轮新增测试 | 7 条（2354 → 2361） |
| 真机包 | 版本 `4.2.30`、build `1`、`MTA_BUILD_ID=local-uat` |
| 真机身份 | App 与 metadata 均为 `fef2f1e8` |
| 真机冒烟 | **PASS**，`run_5b8b4a89` / `run_45c906b5` |
| 推送 | **未推送**（仅本地 commit） |

本轮在此基础上继续完成第一项业务主链路：

- 首页 → 搜索页 → 返回首页；
- 新增搜索页 screen/element override；
- 真机 8 步全部 `SUCCESS`。

| 本轮项目 | 结果 |
|---|---|
| MTA 分支 | `P4-1017-mta` |
| 新链路真机 run | `run_084b6924` / PASS |
| 新链路步骤 | 8/8 `SUCCESS` |
| 原冒烟回归 | `lst_smoke_launch_001` / PASS |
| 全量单测 | **2363 passed，0 failed**（约 72s） |
| App 源码改动 | 无；复用现有 `home_search` 与系统导航节点 |

随后完成第二项业务主链路：

- 首页 → 服务 → 我的 → 首页；
- 新增服务页 / 我的页 screen 与 tab 操作 override；
- 真机 13 步全部 `SUCCESS`。

| 本轮项目 | 结果 |
|---|---|
| 新链路真机 run | `run_24bf6348` / PASS |
| 新链路步骤 | 13/13 `SUCCESS` |
| 同批回归 | 首页搜索 / 冷启动冒烟均 PASS |
| App 源码改动 | 无；复用 `home_search`、`mineSet` 与系统 tab 节点 |

最近一次真机 trace 的三条用例全部为 `PASS`：

```text
lst_home_search_back_001: PASS
lst_smoke_launch_001: PASS
lst_tab_switch_back_001: PASS
```

## 1. `optional` 修复

### 根因

仓库里有两套 runner：

- `runner/testcase_runner.py`：旧路径，已有 `optional`，但不在 `mta run` 主链路；
- `runner/runner.py::StepRunner`：真实路径，之前完全没有接收或处理 `optional`。

因此 `cli/pipeline.py` 组装 `StepRunner` 时，即使用例写了 `optional: true`，
定位失败仍会直接返回 FAIL。

### 实现

- `RunStepContext` 新增 `optional: bool = False`；
- `cli/pipeline.py` 传入 `optional=step.optional`；
- `StepRunner` 只把以下两类**定位失败**转为跳过：
  - `ElementNotFound`（包括 find 返回 0 个）；
  - `AmbiguousElement`（多匹配，仍不取第一个）。
- 跳过结果返回 `ok=True`，并写入可审计信息：

```text
error = "optional step skipped (<Reason>)"
detail = {"optional_skipped": "<Reason>"}
```

- `perform`、Guard、设备/基础设施等非定位错误**仍然 FAIL**，没有扩大豁免范围。

### 更新弹窗竞态

只把 tap 设为 optional 仍不够：更新弹窗在冷启动后**异步入树**，立即 tap
可能在弹窗出现前就以 `ElementNotFound` 跳过，随后模态盖住首页，导致
`screen:LSTDynamicHomeViewController` 超时。三条用例现已统一改为：

```yaml
- wait_for:
    target: update_dialog_cancel
    condition: exists
    timeout: 15
  optional: true
- action: tap
  target: update_dialog_cancel
  optional: true
```

`WaitStep.optional` 只豁免 `WaitTimeout`；`cli/pipeline.py` 将超时记为
`SUCCESS`，并在 `detail_json` 留下 `optional_skipped=WaitTimeout`。

### 测试

上一轮新增 7 条测试，覆盖：

- 0 匹配 → 跳过；
- find 抛 `ElementNotFound` → 跳过；
- 多匹配 → 跳过；
- `perform` 异常 → 仍 FAIL；
- 未知 locator / 配置错误 → 仍 FAIL；
- 非 optional 的 0 匹配 → 仍 FAIL；
- `SessionPipeline` 到 `StepRunner` 的真实接线（防止再次只测影子实现）。

全量结果：**2361 passed / 0 failed**。

本轮又新增 2 条测试：

- `test_optional_wait_skips_timeout_through_session_pipeline`
- `test_wait_step_optional_defaults_false_and_can_be_set`

定向结果 68 passed；全量结果 **2363 passed / 0 failed**。

## 2. 首页锚点

最终锚点位于
`nfcPushOnline/Modules/Dynamic/View/LSTDynamicPlaceholderView/LSTDynamicNavigationBarView.m`
搜索按钮内部的 `UILabel`：

```objc
label.accessibilityIdentifier = @"home_search";
```

最初标识符设在外层 `UIButton` 上。真机无障碍树里按钮节点存在，但 identifier
没有进入 XCUITest 查询结果。改到叶子 `UILabel` 后：

- 弹窗关闭前 `home_search=1`；
- 弹窗关闭后 `home_search=1`；
- `accessibility id` 查询唯一命中，元素 `visible=true`。

MTA 侧 screen 已改为 `LSTDynamicHomeViewController`，marker 为
`home_search`；旧死类 `LSTHomeViewController` 的两个 override 已删除。

## 3. 真机验证

用户指出设备上曾安装另一个验证环境版本。之后重新安装了本机构建包，并核对：

```text
版本: 4.2.30
build: 1
MTA_GIT_COMMIT: fef2f1e8
MTA_BUILD_ID: local-uat
二进制 mtime: 2026-10-17 13:52:13
```

随后执行：

```bash
make lst-smoke-device LST_ENV=uat
```

结果：

```text
lst_smoke_launch_001: PASS
run: exit 0
```

`out/lst-smoke.db` 中 `run_5b8b4a89` 与 `run_45c906b5` 都是 PASS，App/metadata
commit 均为 `fef2f1e8`，三个步骤全部 `SUCCESS`。

更早的 `run_9ca56017` 用于验证 `optional` 已生效：步骤 1 记录 SUCCESS 和
`optional_skipped=ElementNotFound`，随后在旧锚点上暴露了独立的
`home_search` 问题；该问题已由叶子 `UILabel` 方案修复。

### 3.1 首页 → 搜索页 → 返回

真机探测与最终运行（`run_084b6924`）确认：

| 节点 | 定位方式 | 结果 |
|---|---|---|
| 首页搜索入口 | `accessibility id = home_search` | 唯一命中 |
| 搜索页 marker | NavigationBar name `LSTDynamicSearchView` | 唯一可见 |
| 返回按钮 | `accessibility id = return` | 唯一命中 |
| 右上角「取消」 | predicate + Button 类型 | 唯一命中 |

用例 `lst_home_search_back_001` 的 8 步全部 `SUCCESS`：

```text
launch → optional 关弹窗 → 等首页 → 点搜索 → 等搜索页
→ 点 return → 等首页 → 断言 home_search 存在
```

真机同时跑原冷启动冒烟，两条均 PASS，`run: exit 0`。

⚠️ 真机合并 generated 与 overrides 后，`home_search` 短名有歧义，用例必须用
`LSTDynamicHomeViewController.home_search`。lint 也必须带
`--generated repository/generated/local-uat`。

搜索输入框尚未暴露独立节点，因此本轮只覆盖进入与返回，不覆盖输入。

### 3.2 WDA 手机端授权

本轮再次遇到 `Failed to start the preinstalled WebDriverAgent in 60000 ms`。
设备实际已解锁、WDA 也已安装，真因是手机端需要输入密码确认 XCUITest 授权。
完成确认后同一 Appium 会话立即恢复。遇到该报错时先检查手机是否有授权弹窗，
不要直接重建 WDA 或重启 Appium。

### 3.3 首页 → 服务 → 我的 → 首页

真机运行 `run_24bf6348` 确认 `lst_tab_switch_back_001` 的 13 步全部
`SUCCESS`：

```text
launch → optional wait/tap 关弹窗 → 等首页
→ 点服务 → 等服务页 → 断言首页入口不在
→ 点我的 → 等我的页 → 断言首页入口不在
→ 点首页 → 等首页 → 断言首页入口存在
```

定位要点：

| 节点 | 定位方式 | 说明 |
|---|---|---|
| 首页 tab | Button predicate `name == "首页"` | 限定类型，避免同名文本 |
| 服务 tab | Button predicate `name == "服务"` | 服务页 NavigationBar 同名 |
| 我的 tab | Button predicate `name == "我的"` | 唯一按钮 |
| 服务页 marker | accessibility id `ETC发行` | **临时方案**，依赖服务端菜单文案 |
| 我的页 marker | accessibility id `mineSet` | App 自有设置按钮，camelCase |

`screen wait` 的 marker 固定按 accessibility id 查询，不能直接使用 predicate。
最初把 `service_nav_bar` predicate 当 marker，导致
`run_8aad2967` 在服务页报 `CURRENT_SCREEN_UNKNOWN`；改成可查询的
`ETC发行` 后通过。

⚠️ 服务页 marker 仍有稳定性风险：`ETC发行` 是服务端下发的分区标题。后续应
在 App 服务页根节点补固定 `accessibilityIdentifier`，再把 screen marker 和
`service_nav_bar` 元素一起切换到稳定锚点。

同批真机回归结果：

```text
lst_home_search_back_001: PASS
lst_smoke_launch_001: PASS
lst_tab_switch_back_001: PASS
run: exit 0
```

## 4. 提交与工作区

### MTA 仓库

```text
a52b322 fix(lst): wire optional through StepRunner and verify home_search smoke
```

包含：

- `cli/pipeline.py`
- `runner/runner.py`
- `tests/unit/test_runner_pipeline.py`
- `tests/unit/test_cli_run.py`
- `suites/lst/smoke_launch_001.yaml`
- `docs/lst_update_playbook.md`
- `repository/overrides/{screens,elements}/LSTDynamicHomeViewController.yaml`
- 删除旧 `LSTHomeViewController` overrides

### 乐速通仓库

```text
5196852c test(mta): expose home_search anchor and add scene lifecycle hook
```

只包含：

- `nfcPushOnline/AppDelegate.m`
- `nfcPushOnline/Modules/Dynamic/View/LSTDynamicPlaceholderView/LSTDynamicNavigationBarView.m`

`AppDelegate.m` 的 `MTA_SCENE_LIFECYCLE` 块只在 mta 测试构建定义该宏时参与
编译，是 iOS 27 设备启动所需；正常打包不受影响，但仍建议 App 同事评审。

以下既有脏改动**没有提交，也没有被回退**：

- `Podfile` / `Podfile.lock` / `Pods/*`
- `nfcPushOnline.xcodeproj/project.pbxproj`
- `.codegraph/`、`xcuserdata/`、`.DS_Store`

第二项提交（本文件所在 commit）：

```text
test(lst): add tab switch navigation smoke
```

包含 `WaitStep.optional`、更新弹窗竞态修复、服务页 / 我的页 / tab override、
tab 导航用例及本文件更新。本轮未修改乐速通 App 源码。

## 5. 复现命令

```bash
cd ~/PycharmProjects/Mobile_test_agent

# 前置：RemoteXPC 隧道（需 sudo，保持常驻）
sudo appium driver run xcuitest tunnel-creation

# 前置：Appium server（本轮未重启现有实例）
node ~/.local/bin/appium --allow-cors > out/appium.log 2>&1 &

# 改了 App 源码时：构建 + 装机
make lst-build-device LST_ENV=uat
make lst-install-device LST_ENV=uat

# 真机冒烟
make lst-smoke-device LST_ENV=uat
```

`make lst-build-device` 会打印 `bash: inherited: command not found`，但构建和
身份注入成功；这是 Makefile 里既有的无害告警。

## 6. 遗留项

- `LSTHTTPManager.m:32/150` 硬编码 `svipcard-new/`，uat 包的市场请求仍可能走
  生产路径；属于 App 侧问题，修前不覆盖市场模块。
- `source/export.py` 已按目标 build 清理 stale 的顶层 `elements/screens/*.yaml`
  （非 YAML、子目录和 build 外文件保留）；若曾手工改过 generated，仍需按 H16
  重新执行 `mta repo generate`，不要依赖手工清理。
- 乐速通 accessibilityIdentifier 覆盖仍很低（两个 `calendar` + `home_search`），
  后续页面大多仍需结构探测；搜索输入框也尚无独立锚点。
- 服务页 screen marker 临时使用服务端文案 `ETC发行`，运营改名会假红；需 App
  补稳定锚点后替换。
- 两套 runner 仍并存。本轮选择方案 A 修复真实路径；若要根治语义漂移，后续可
  迁移 `run_phase0_demo.py` 与旧 runner 测试后删除 `testcase_runner.py`。

## 7. 快速索引

| 想查什么 | 位置 |
|---|---|
| `optional` 实现 | `runner/runner.py`、`cli/pipeline.py` |
| 首页锚点与排错 | `docs/lst_update_playbook.md` §3.11 |
| 冒烟用例 | `suites/lst/smoke_launch_001.yaml` |
| 首页导航用例 | `suites/lst/home_search_back_001.yaml` |
| 首页 / 搜索页 override | `repository/overrides/{screens,elements}/LSTDynamic{HomeViewController,SearchView}.yaml` |
| tab 导航用例 | `suites/lst/tab_switch_back_001.yaml` |
| 服务页 override | `repository/overrides/{screens,elements}/LSTServiceMenuViewController.yaml` |
| 我的页 override | `repository/overrides/{screens,elements}/LSTMineViewController.yaml` |
| tab 操作元素 | `repository/overrides/elements/LSTTabBarViewController.yaml` |
| 真机 trace | `out/lst-smoke.db` |
| 真机构建/安装/冒烟 | `Makefile` 的 `lst-build-device` / `lst-install-device` / `lst-smoke-device` |
| 失败恢复演练 | `docs/lst_recovery_playbook.md`、`suites/lst_recovery/`、`repository/fault_injection/lst_recovery/` |

查 trace 时注意：`steps` 表用 `testcase_run_id`（没有 `run_id` 列），错误与跳过
说明在 `detail_json` 中。
