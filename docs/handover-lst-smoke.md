# 交接：乐速通真机冒烟、tab 导航与登录链路（P4-1017-mta）

> 本文记录 **2026-10-18 畅行慧览 tab 与账号密码登录链路接入后的真实状态**。
> 平台整体设计见 `docs/handover.md`；真机接入与排错细节见
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

### 1.2 登录失败路径与登出自洽（2026-10-18，本轮补充）

在前述登录链路（§「第二项业务主链路」后的 1.1 登录段落）基础上补充：

**失败路径用例 `lst_login_negative_001`**（新文件）：

- 覆盖两条**不请求服务端、不会锁定账号**的客户端校验失败：
  A. 已填账号 + 密码为空 + 已勾协议 → HUD「密码不能为空」；
  B. 已填账号 + 未勾协议 → HUD「请先阅读并勾选协议」；
- 提示是 `SVProgressHUD+LST.m` 的 `showMessage:`，**消息文案在节点的
  label 上**（`name="SVProgressHUD"`，无独立 StaticText 子节点承载文案），
  `accessibility id` 查找 + `.text` 可直接断言——新增元素
  `login_toast_hud`（`repository/overrides/elements/LSTLoginViewController.yaml`）；
- **HUD 只显示约 2s**（`dismissWithDelay:2`），实测点击后 t+0.48s 出现；
  提交步**有意不带 postcondition**——带 postcondition 时该步耗时 3174ms
  （screen wait 后的 settle 判定），会吃光 2s 窗口，断言开始时 HUD 已消失
  （首跑 `run_a96835e4` 失败即因此）。去掉后提交步回到约 750ms，窗口够用；
  lint 因此报 `missing_postcondition` WARNING（exit 0），是 H7 非幂等动作
  需要 postcondition 与瞬时提示必须在窗口内观测的真实冲突，已如实记录；
- **路径 B 不填密码**（run_22277fb0 首跑失败的根因）：probe_pathb vs
  probe_pathb3 逐变量对照确认，untick 后再 input 密码真机上 HUD 不出现；
  保持密码为空时稳定弹出。附带安全收益：账号+密码+已勾选三者同时成立会
  **真实发起登录**（probe_pathb2 抓到 `Loading` 且 App 离开登录页），不填
  密码排除了误触真实登录的可能；
- 账号只走 `${TEST_USERNAME}`（本用例不需要密码），不落库。

**登出自洽前置修复**（`lst_login_001` / `lst_login_negative_001` 共同）：

- 未登录时设置页「退出登录」cell 被 App 移除（`LSTSetViewController.m:30`），
  optional tap 跳过后**设置页还开着**，直接 tap `mine_login_entry` 会
  ElementNotFound（`run_af079971` / `run_cc1fbf6c` / `run_35f5f9ab` 三次
  失败同一根因）；
- 修复：登出步之后加 `action: back`（`driver.back()`，probe_back.py 实测
  能把 ObjC UINavigationController 的设置页 pop 回我的页），再等
  `screen:LSTMineViewController`——wait 改为**非 optional**（登出成功或
  back 后都必须回到我的页，失败就该红）；
- 负用例补同款 optional 登出前置（batch 里紧跟 login_001 跑时 App 是
  已登录态，`mine_login_entry` 不存在，必须先登出）。

真机验证（prod 包，`run_...` 见 `out/lst-smoke.db`）：

| 用例 | 结果 | 备注 |
|---|---|---|
| `lst_login_001` | PASS 21/21 | 加 back 步后修复 |
| `lst_login_negative_001` | PASS 23/23 | 新用例（含登出自洽前置） |
| 全量 `lst_smoke` batch | **6/6 PASS**（`run: exit 0`） | 含上述两条（prod） |
| 全量单测 | **2366 passed / 0 failed**（70.5s） | 与基线一致，零新增测试 |

已知边界：错误密码路径**不测**（账号锁定风险，用户明确要求跳过）；未勾
协议时 App 端**有** toast（源码 `LSTLoginView.m:183`），修正了此前交接里
「未勾协议静默」的错误结论。

### 1.1 第四项 tab「畅行慧览」（2026-10-18，commit `159a282`）

真机探测（`out/lst-probe/probe_journey*.py`，两次 dump 交叉验证）：

- tab 栏共 4 个：首页 / 畅行慧览 / 服务 / 我的，第四个此前未覆盖；
- 「畅行慧览」tab 是 Button、name/label 与标题一致，但畅行慧览页自己的
  NavigationBar 标题也同名，故沿用 Button predicate 限定类型；
- 页面 marker 选 `附近服务`（`LSTDynamicNearServiceView` 分区标题，服务端
  文案）：首页存在同名节点但**恒 visible=false**（属首页「出行服务」宫格内
  的隐藏项），实测首页/服务/我的三页 visible 计数为 0、畅行慧览页为 1，
  因此 screen wait 只在目标页成立；
- ⚠️ 环境依赖：该 tab 由服务端开关 `sys_journey_tabbar_status` 控制
  （`LSTTabBarViewController.m:338`），关闭时 tab 整体消失，用例会红。

真机 `run_696bea68`（prod 包）：`lst_tab_journey_001` PASS，10/10 SUCCESS；
同批三条既有用例均 PASS，`run: exit 0`。

### 1.2 账号密码登录链路（2026-10-18，commit `057efd4`）

真机探测（`out/lst-probe/probe_login*.py`，生产包，账号由用户提供、经环境
变量注入、**不写入仓库**）：

| 节点 | 定位方式 | 说明 |
|---|---|---|
| 我的页登录入口 | predicate `StaticText name == "登录/注册"` | 头像 Cell 内叶子节点，点击透传到 Cell |
| 登录页 marker | accessibility id `LSTLoginView` | 系统导航栏暴露的 View 类名，登录成功后消失 |
| 用户名输入框 | predicate `TextField AND placeholderValue CONTAINS "请输入手机号"` | 无 accessibilityIdentifier，tag=100/101 对 XCTest 不可见 |
| 密码输入框 | predicate `SecureTextField AND placeholderValue CONTAINS "请输入密码"` | 同上 |
| 协议勾选框 | accessibility id `loginRememberPass0` | **必须勾选**，否则点登录无任何反应 |
| 登录按钮 | predicate `Button AND name == "立即登录"` | 与内部 StaticText 同名，必须限定类型 |

三个非显然结论（都写进用例注释，避免下次重踩）：

1. **不勾协议点登录是完全静默的**：只弹一个 `SVProgressHUD` toast，
   页面与状态 32s 无任何变化（`LSTLoginView.m:181` 的 agree 判断）。
2. **登录成功后 App 回落到首页 tab**，不是留在「我的」——wait 目标写
   `LSTMineViewController` 会 30s 超时（首轮真机失败即因此）。
3. **元素策略里的 `${TEST_USERNAME}` 不会被解析**：`cli/pipeline.py`
   的 `_resolve_secret_refs` 只作用于 `step.value`，仓库 YAML 的策略值
   原样下发。「登录成功」判据因此改用登录态独有的会员角标
   `mine staff new`（未登录时整个头像 Cell 只有「登录/注册」）。

两个 marker 踩坑：

- 设置页第一版用 NavigationBar `设置`，真机 wait 10s 全超时——同名节点
  有 2 个（NavigationBar + 内部标题 StaticText），唯一性校验判
  AmbiguousElement。与「服务」页当年选 `ETC发行` 同一类坑：**中文标题在
  NavBar 上必然成对出现**。改用分区首行 `更改读卡模式`（页内唯一）。
- 「退出登录」是 Cell 里的 StaticText，且**未登录时该 cell 被 App 移除**
  （`LSTSetViewController.m:30`），因此它本身就是「当前已登录」的判据，
  用例用 optional 步做「已登录则先登出」，天然自洽。

真机 `run_8088da28`（prod 包）：`lst_login_001` PASS，20/20 SUCCESS；
同批四条既有用例均 PASS，`run: exit 0`。全量单测 2366 passed / 0 failed。

账号密码运行方式（**不要写进任何文件**）：

```bash
TEST_USERNAME='<账号>' TEST_PASSWORD='<密码>'   .venv/bin/python -m cli.main lint --overrides repository/overrides     --generated repository/generated/local-prod suites/lst/login_001.yaml
```

未注入时 lint 报 `unknown_secret`（exit 3），明文也不会进 trace。

### 1.3 搜索输入与实时过滤（2026-10-22，遗留项 1）

补上了此前「搜索输入框没有独立锚点、只能覆盖进入/返回」的缺口。

**新增用例 `suites/lst/search_input_001.yaml`**（17 步）：

```text
launch → optional 关弹窗 → 等首页 → 点 home_search → 等搜索页
→ 断言空列表 → 输入 ETC → 等清除按钮出现 → 等空列表消失
→ 断言 ETC新办 出现 → 点清除文本 → 等空列表恢复
→ 断言 ETC新办 消失 → 点取消 → 等首页 → 断言 home_search
```

**新增元素**（`repository/overrides/elements/LSTDynamicSearchView.yaml`）：

| 元素 | 定位 | 说明 |
|---|---|---|
| `search_input` | `type == TextField` | 页内唯一；输入框**无** name/label/placeholderValue |
| `search_clear_text` | accessibility id `清除文本` | 输入非空才出现（系统本地化文案） |
| `search_empty_list` | `Table name="空列表"` | 未输入时的空态 |
| `search_result_etc` | `StaticText name="ETC新办"` | 输入 ETC 后的结果项（服务端文案） |

**关键技术事实**：

- 搜索框是 `LSTDynamicSearchViewController.searchTF`（`UITextField`，放在
  `navigationItem.titleView`），**没有任何可读身份**——name/label/
  placeholderValue 全空。页内 `TextField` 唯一，故用类型谓词；
  固定坐标 `x=76 y=64 w=278 h=34` 也在 dump 中确认，但未绑定（更耐布局微调）。
- 过滤是**纯客户端**的：`shouldChangeCharactersInRange` 逐字符过滤本地
  `menuArr`，**不发网络请求**——所以这条链路在断网时也能跑。
- `Executor.input` 是 `clear(); send_keys(value)` 整串下发；真机实测整串
  与逐字符都能生效（`send_keys` 本身约 1.9s，WDA 逐字符敲键盘），输入后
  `.text` 回读 `ETC`。
- 空态/结果判据是**互斥**的：未输入时 `空列表` 存在且 `ETC新办` 不存在；
  输入后反过来；点清除文本后回到初始态（probe_search11 四态实测）。
- 两条返回路径都已覆盖：本用例点「取消」，`home_search_back_001` 点
  `return`。

真机 `run_792715e2`（uat 包）：`lst_search_input_001` PASS，17/17 SUCCESS。

⚠️ **uat 包必须强制重建**（本轮踩到的坑）：`build/lst-device-uat` 里的
`LSTDynamicNavigationBarView.o` 时间戳是 10-17 13:52，而 `home_search`
锚点的 commit 是 13:56——增量构建复用了旧目标文件，导致 uat 包**没有**
`home_search` 节点，所有依赖它的用例都会红（`lst_smoke_launch_001`
`CURRENT_SCREEN_UNKNOWN`）。prod 包（10-18 15:41 构建）正常。
排查方式：`find build/lst-device-<env> -name '*.o' | xargs ls -la` 对比
源码 commit 时间；确认是旧产物后移走 build 目录强制全量重建即可。

**uat 环境账号差异**：`TerryOnPro` 在测试环境返回
`用户名不存在或密码错误`（probe_uat_login），因此 `lst_login_001` 在
uat 包上会红——这是**账号数据**问题，不是用例或 App 缺陷。登录链路请在
prod 包验证（prod 上 21/21 PASS）。

## 1. `optional` 修复

### 根因

当时仓库里有两套 runner（现已收敛为一套）：

- `runner/testcase_runner.py`：旧路径，已有 `optional`，但不在 `mta run` 主链路
  （**已于 2026-10-17 退役删除**）；
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

~~搜索输入框尚未暴露独立节点，因此本轮只覆盖进入与返回~~ —— 已于
2026-10-22 补齐输入链路，见 §1.3 `suites/lst/search_input_001.yaml`。

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

# 前置：Appium server。⚠️ 必须带 DEVELOPER_DIR 启动（2026-10-18 实测）：
# 不带时 Appium 只看到 CommandLineTools，模拟器/真机设备发现会报
# `Unknown device or simulator UDID`，看起来像设备坏了。
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
node ~/.local/bin/appium --allow-cors > out/appium.log 2>&1 &

# 改了 App 源码时：构建 + 装机
make lst-build-device LST_ENV=uat
make lst-install-device LST_ENV=uat

# 真机冒烟
make lst-smoke-device LST_ENV=uat
```

`make lst-build-device` 会打印 `bash: inherited: command not found`，但构建和
身份注入成功；这是 Makefile 里既有的无害告警。

## 5.1 生产包验证路径（2026-10-18，测试/验证环境不可达时）

断网时 uat 包跑不动，但框架侧链路仍能验。改用 `prod` 走同一套流水线：

```bash
make lst-build-device LST_ENV=prod
make lst-repo-generate LST_ENV=prod
make lst-install-device LST_ENV=prod
make lst-smoke-device LST_ENV=prod
```

实测（`7edab59` 之后）：三条冒烟全 PASS，`run_de36423c` 的
`app_build=local-prod`、`metadata_build=local-prod`、`metadata_mismatch=0`，
App 与 metadata 同为 `5196852c`。完整步骤与边界见
`docs/lst_update_playbook.md` §1.6。

恢复演练的完整闭环仍需 `LLM_API_KEY`；没有 key 时用
`make lst-recovery-no-llm LST_ENV=prod` 只验故障注入（预期
`lst_recovery_drift_001 FAIL (ELEMENT_NOT_FOUND)`，且 trace 的
`reconcile.outcome=DRIFT`）。

### 5.2 失败恢复真机闭环（2026-10-18）

生产包上已完成首次 LLM 恢复、人工 ACCEPT、Experience 二次复用：

| 阶段 | run / 证据 | 结果 |
|---|---|---|
| 首跑 LLM 恢复 | `run_6da4010e` | `RECOVERED`，`llm_calls=1`，exit 5 |
| LLM 候选 | `return`，confidence `0.98` | `reconcile=DRIFT`，`validate=EXECUTE` |
| 人工审核 | review #1 | `ACCEPT`，生成 `exp_02c5849a5f91`（`CANDIDATE`） |
| 二跑 Experience | `run_5d05c94a` | `RECOVERED_EXPERIENCE`，`llm_calls=0` |
| 无 Key 复用 | `run_4e7209b7` / `run_2ba80509` | `llm_enabled=0`，仍恢复成功 |
| Experience 统计 | 同一 Candidate | `sample=3`，`success=3`，`rate=1.0` |

### 5.3 升级 VERIFIED 与人工 Promotion（2026-10-18）

| 阶段 | 命令 / 证据 | 结果 |
|---|---|---|
| 补跑样本 | 真机再跑 7 次（累计 10 个不同 run） | `sample=10`，`success=10`，`distinct=10` |
| 升级门槛 | `mta experience verify` | `PROMOTE_TO_VERIFIED/THRESHOLD_MET` → `VERIFIED` |
| E4 资格 | 正式 overrides 解析 `lst_recovery_search_back` | `risk=LOW`、`idempotency=IDEMPOTENT` |
| Proposal | `prop_6d2c291ab89e` | `PENDING`（不写文件），人工 review diff |
| Approve | commit `b82dd12` | 写 overrides（`origin: experience`）+ `promoted=True` |
| Promotion 后验证 | `make lst-recovery-promoted-device`，`run_745717d6` | `PASS`，`llm_calls=0`，`recoveries=0` |

两条状态时间线（9.4）保持独立：approve 后 `status` 仍是 `VERIFIED`，
`promoted/promoted_commit` 是 Experience 行上的独立字段；撤销走
`git revert b82dd12`（E10），不影响 Experience Store 历史。

E4 资格解析不到目标时 `experience verify` 会判 `ELEMENT_UNRESOLVED`
（fail-closed）——正式 `LSTDynamicSearchView.yaml` 因此登记了演练目标
同名元素，策略值仍是故障值，运行时演练仍加载隔离故障目录。

trace 现在记录**真正命中**的策略：`Executor.find` 在返回元素上标注
`_mta_strategy`，`StepRunner` 落 `steps.locator_strategy`（Appium by 串，
如 `accessibility id`）。Promotion 后链尾 experience 策略生效时，trace
不会再把它写成首条 manual 策略。新增钉子测试
`tests/integration/test_runner_fakedriver.py::test_locator_strategy_is_the_strategy_that_actually_matched`。

`group/group` 在同一恢复 prompt 上仅 1/3 命中，改用
`group/auto-gpt-6-luna` 完成实测（2/3 命中，本次 confidence 0.98）。
`min_confidence=0.85` 是安全阈值，不得为跑绿调低。

`make lst-recovery-device` 已修正退出码语义：`mta run` 的 exit 5 是
`RECOVERED` 的预期结果，Make 目标接受 0/5，其他退出码原样失败。
同时移除了“必须先有 `LLM_API_KEY`”的前置检查——已有 Experience 时无 Key
也能复用，trace 会如实记录 `llm_enabled=0`。

## 6. 遗留项

- `LSTHTTPManager.m:32/150` 硬编码 `svipcard-new/`，uat 包的市场请求仍可能走
  生产路径；属于 App 侧问题，修前不覆盖市场模块。
- `source/export.py` 已按目标 build 清理 stale 的顶层 `elements/screens/*.yaml`
  （非 YAML、子目录和 build 外文件保留）；若曾手工改过 generated，仍需按 H16
  重新执行 `mta repo generate`，不要依赖手工清理。
- 乐速通 accessibilityIdentifier 覆盖仍很低（两个 `calendar` + `home_search`），
  后续页面大多仍需结构探测。~~搜索输入框也尚无独立锚点~~ —— 输入链路已用
  「页内 TextField 唯一 + 类型谓词」补齐（§1.3），但仍是结构探测，App 若
  在搜索页再加第二个 TextField 就会歧义，需 App 补 identifier 才彻底稳。
- 服务页 screen marker 临时使用服务端文案 `ETC发行`，运营改名会假红；需 App
  补稳定锚点后替换。
- ~~两套 runner 并存~~ **已收口（2026-10-17）**：`runner/testcase_runner.py`
  删除，`run_phase0_demo.py` 与 `phase0/verify_stage5.py` 迁到新管线，旧 runner
  单测由 `tests/unit/test_cli_run.py` 与故障矩阵接管，并新增
  `tests/unit/test_legacy_runner_retired.py` 守卫其不得复活。
- **Phase 0 演示脚本的迁移遗留已修（`7edab59`）**：旧 runner 退役后第一次
  端到端实跑暴露三处——① `run_id` 硬编码 → 第二次跑
  `UNIQUE constraint failed`；② 漂移段不产生漂移（基础用例的 `REINSTALL`
  装回 DerivedData 旧包 + 漂移目标带 predicate 兜底），表现是「用例 PASS
  但没有 RECOVERED 步骤」的**假绿**；③ `verify_stage5.py` 的 SQL 列名歧义 +
  在 `end_run` 之前断言 `runs.status == PASS`。新增守卫
  `tests/unit/test_phase0_demo_drift.py` 钉住「漂移段必须有 RECOVERED 步骤」，
  A/B 探针 3/5 有效（漂移目标错 / 恢复引擎缺席 / 候选未登记都会红）。
- **失败恢复演练闭环已完成（2026-10-18）**：生产包上首跑
  `RECOVERED_LLM`（`run_6da4010e`，confidence 0.98）→ 人工 ACCEPT →
  二跑 `RECOVERED_EXPERIENCE`（`run_5d05c94a`，LLM 0 调用）；修复
  Makefile 的 exit 5 语义后，无 Key 再跑两次也均命中 Experience。详见 §5.2。

## 7. 快速索引

| 想查什么 | 位置 |
|---|---|
| `optional` 实现 | `runner/runner.py`、`cli/pipeline.py` |
| 首页锚点与排错 | `docs/lst_update_playbook.md` §3.11 |
| 冒烟用例 | `suites/lst/smoke_launch_001.yaml` |
| 首页导航用例 | `suites/lst/home_search_back_001.yaml` |
| 搜索输入用例 | `suites/lst/search_input_001.yaml`（输入/实时过滤/清空/取消） |
| 首页 / 搜索页 override | `repository/overrides/{screens,elements}/LSTDynamic{HomeViewController,SearchView}.yaml` |
| tab 导航用例 | `suites/lst/tab_switch_back_001.yaml` |
| 畅行慧览用例 / override | `suites/lst/tab_journey_001.yaml`、`repository/overrides/screens/LSTDynamicNearServiceViewController.yaml` |
| 服务页 override | `repository/overrides/{screens,elements}/LSTServiceMenuViewController.yaml` |
| 我的页 override | `repository/overrides/{screens,elements}/LSTMineViewController.yaml` |
| tab 操作元素（含 `tab_journey`） | `repository/overrides/elements/LSTTabBarViewController.yaml` |
| 登录用例 / override | `suites/lst/login_001.yaml`、`repository/overrides/{screens,elements}/LSTLoginViewController.yaml` |
| 设置页（登出）override | `repository/overrides/{screens,elements}/LSTSetViewController.yaml` |
| 真机探测脚本与 dump | `out/lst-probe/`（不入库） |
| 真机 trace | `out/lst-smoke.db`、`out/lst-probe.db` |
| 真机构建/安装/冒烟 | `Makefile` 的 `lst-build-device` / `lst-install-device` / `lst-smoke-device` |
| 失败恢复演练 | `docs/lst_recovery_playbook.md`、`suites/lst_recovery/`、`repository/fault_injection/lst_recovery/` |
| Promotion 后验证 | `make lst-recovery-promoted-device`、`suites/lst_recovery/recovery_promoted_001.yaml`、`out/lst-recovery-promoted.db` |

查 trace 时注意：`steps` 表用 `testcase_run_id`（没有 `run_id` 列），错误与跳过
说明在 `detail_json` 中。
