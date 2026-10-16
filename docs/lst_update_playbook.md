# 乐速通（nfcPushOnline）版本更新接入手册

> 被测 App 会持续迭代。本文回答一个问题：**乐速通出新版本后，接入 mta 测试框架需要动哪些地方？**
>
> 结论先行：常规版本更新**通常零改动**——重跑三步流水线即可。需要动手的只有流水线撞上
> 「结构性变化」的时候，见 §3 排错表。

## 0. 固定信息（不随版本变）

| 项 | 值 |
|---|---|
| 源码路径 | `/Users/terrygmx/Desktop/workspace/ios`（`LST_SRC` 可覆盖） |
| 工程 | `nfcPushOnline.xcworkspace`（必须 workspace，不能 .xcodeproj） |
| Scheme / target | `nfcPushOnline` |
| Bundle id | `com.etc.nfcPushOnline` |
| 签名 | Automatic，Team `TSVS7C28BU`（王雪娇 Apple ID 登录在本机 Xcode，证书 `EC548F07CD9955E595DBFD214A1507626EE3CBAD`） |
| 测试机 | 「GuoMingxin」的 iPhone（iPhone 14 Pro Max），UDID `00008120-0018499A1450C01E` |
| 构建产物 | `build/lst-device/Build/Products/Debug-iphoneos/nfcPushOnline.app` |
| 身份注入 | `MTA_GIT_COMMIT` = 乐速通仓库 HEAD 短号；`MTA_BUILD_ID` = `local`（构建时自动写入） |

## 1. 常规更新流程（版本迭代后的标准三步）

```bash
# 第 1 步：拉新代码 + Pods 同步（Podfile.lock 变了就必须跑）
cd ~/Desktop/workspace/ios && git pull
pod install --repo-update        # 若 Podfile.lock 无变化可省 --repo-update

# 第 2 步：构建 + 注入 + 重签（一条命令，产物在 build/lst-device/...）
cd ~/PycharmProjects/Mobile_test_agent && make lst-build-device

# 第 3 步：重新生成 metadata（G8 门槛要求与注入的 commit 同源）
make lst-repo-generate LST_ENV=uat   # 内部即 repo generate <LST_SRC>/nfcPushOnline --build local-uat

# 第 4 步：装机 + 人工冒烟（USB 连接测试机）
make lst-install-device
```

**为什么常规更新零代码改动**：`MTA_GIT_COMMIT` 取的是乐速通仓库 HEAD（构建时实时
`git rev-parse`），metadata 由 `repo generate` 读同一个 HEAD——两边天然同源，G8 校验
自动通过。版本号、Pods、业务代码的变化都被流水线吸收，不需要改 mta 侧任何文件。

**验证冒烟**：装机后冷启动，确认能进首页。命令行验证（可选）：

```bash
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
xcrun devicectl device process launch --device 00008120-0018499A1450C01E \
  --console --terminate-existing com.etc.nfcPushOnline
```

console 里看到 `[MTA] scene willConnect` 且进程不退出即链路健康。

**真机前置条件（一次性；换 Xcode / WDA 版本后重做。缺一项 `mta run` 就在
「设备会话建立失败」上 exit 3，报错文案见 §3.10）**：

1. **RemoteXPC 隧道（必需）**。Appium 对 iOS 17+ 真机的 WDA 拉起/进程控制走
   RemoteXPC，本机必须先建 TUN/TAP 隧道。脚本要 root（建 TUN 设备），且
   **必须与 Appium server 同时保持运行**（另开一个终端，别关）：

   ```bash
   sudo appium driver run xcuitest tunnel-creation
   ```

   没建时的症状：`Unable to launch WebDriverAgent … RemoteXPC is not available
   for this session`，Appium 日志里同步有
   `Tunnel registry port not found. Please run the tunnel creation script first`。

2. **预建 WDA（iOS 27+ 必需）**。设备 OS 高于本机 SDK 时 Appium 自己编 WDA 必挂
   （code 65，见 §3.10），改用预建产物：Appium 只负责装机 + 拉起，**全程不调
   xcodebuild**：

   ```bash
   DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcodebuild build-for-test \
     -project ~/.appium/node_modules/appium-xcuitest-driver/node_modules/appium-webdriveragent/WebDriverAgent.xcodeproj \
     -scheme WebDriverAgentRunner \
     -destination "id=00008120-0018499A1450C01E" \
     -derivedDataPath out/wda-ddata
   ```

   产物 `out/wda-ddata/Build/Products/Debug-iphoneos/WebDriverAgentRunner-Runner.app`
   已在库里；WDA 版本或 Xcode 变了才需重跑。

3. **签名 team**：`export MTA_XCODE_ORG_ID=TSVS7C28BU`——WebDriverAgent 工程不带
   team，不给会被 xcodebuild 判 `requires a development team`（code 65）。
4. **手机保持解锁**（2026-10-16 实测）。锁屏时 iOS 会**拒绝拉起 WDA runner**，
   Appium 只报 `Failed to start the preinstalled WebDriverAgent in 60000 ms`
   + `Connection was refused to port 8100`，把人往「WDA 没构建好」上引；用
   `xcrun devicectl device process launch --console` 单独拉起才看得到真话：
   `Unable to launch … because the device was not, or could not be, unlocked`。
   跑长用例前把「设置 → 显示与亮度 → 自动锁定」设为「永不」，或让屏幕常亮。

**喂给 `mta run`（真机路线，2026-10-15 起可用）**：G8 身份校验走
`--app-path` 从**本地产物**读 Info.plist（物理设备没有 simctl 等价物），
不去设备上挖：

```bash
source .venv/bin/activate && python -m cli.main run \
  --suite lst_smoke --suites-root suites \
  --udid 00008120-0018499A1450C01E \
  --bundle-id com.etc.nfcPushOnline \
  --app-path build/lst-device-uat/Build/Products/Debug-iphoneos/nfcPushOnline.app \
  --overrides repository/overrides \
  --generated repository/generated/local-uat \
  --metadata repository/generated/local-uat/source_metadata.json
```

真机路线要在命令前带上前置条件的环境变量（WDA 签名 + 预建 WDA）：

```bash
export MTA_XCODE_ORG_ID=TSVS7C28BU
export MTA_WDA_PREBUILT_APP=$PWD/out/wda-ddata/Build/Products/Debug-iphoneos/WebDriverAgentRunner-Runner.app
```

- `--app-path` 指向 `lst-build-device` 的产物目录（同一环境）；
- `--metadata` / `--generated` 必须与 `lst-repo-generate LST_ENV=<env>` 的
  输出对齐（`local-<env>`）；⚠️ `--generated` 要指到 **build 目录本身**
  （`repository/generated/local-uat`，不是 `repository/generated`）；
- ⚠️ `--overrides` 和 `--generated` **要么都给、要么都不给**——只给一个不会
  按默认布局补另一个（默认布局仅在两者都缺省时生效），踩过：只给
  `--generated` 导致 overrides 整个不加载、全部 target unknown；
- 校验不过 → `PREFLIGHT ERROR: build identity …` + exit 3，重跑第 2、3 步，
  不要 `--allow-metadata-mismatch`。

## 1.5 多环境（dev / uat / prod）

**机制**：环境是**编译期宏**，不是分支。乐速通 `nfcPushOnline/Macro/CommonMacro.h`
顶部两个（注释态的）宏决定打哪个环境。**注意条件是嵌套的**：
`#ifdef TESTING_MODES`（外层总开关）内含 `#ifdef VERIFCATION_MODES`（uat 细分）：

| 环境 | 宏组合 | URL 特征 |
|---|---|---|
| dev（测试） | `TESTING_MODES=1` | `osplatpagetest` / `svipcarddev` |
| uat（验证） | `TESTING_MODES=1` **加** `VERIFCATION_MODES=1`（两个都要） | `*-uat` 路径 |
| prod（生产） | 都不定义（走最外层 #else） | 无 uat 后缀 |

⚠️ **切分支 ≠ 切环境**：`origin/dev` 与 `origin/production` 的 CommonMacro.h
内容完全一致（diff 为空），环境只认宏。⚠️ 教训（2026-10-13 实测）：第一次
uat 构建只注入了 `VERIFCATION_MODES`（漏了外层 `TESTING_MODES`），编译通过、
产物 URL 仍是生产——**环境必须用二进制 strings 验 URL，不能只看 BUILD
SUCCEEDED**（见 §3.9）。

**用法**（Makefile 默认 `LST_ENV=uat`）：

```bash
make lst-build-device LST_ENV=uat    # 也接受 dev / prod（其他值直接报错）
make lst-install-device LST_ENV=uat
```

或用三环境脚本（内部即调上述 make 目标，**构建后自动做 strings 环境验证**，
验证不过直接 exit 1；末尾跟 `install` 参数则连装机一起做）：

```bash
bash scripts/lst_build_dev.sh           # dev：验证二进制含 tpark.etcstk.cn/osplatpagetest/
bash scripts/lst_build_uat.sh           # uat：验证二进制含 svipcarduat
bash scripts/lst_build_prod.sh install  # prod：正向验 www.bjetc.cn + 反向验 svipcarduat
```

**环境指纹实测结论**（2026-10-13，dev/prod 二进制 strings 差集）：
dev/uat 域名 `tpark.etcstk.cn`，prod 域名 `www.bjetc.cn`；
dev 独有 URL `https://tpark.etcstk.cn/osplatpagetest/`，uat 特征段 `svipcarduat`。
⚠️ `svipcarddev` **不能**当 dev 指纹——CommonMacro.h 里它只有注释行（dev 的
BASE_URL 实际是 `svipcard/`）；裸 `osplatpagetest` 也不能当 prod 反向指纹
（见下方 §3.9 的第二条例外）。

- 环境宏经命令行 `GCC_PREPROCESSOR_DEFINITIONS` 注入，**不改乐速通仓库文件**；
- 产物目录按环境隔离：`build/lst-device-<env>/`；
- `MTA_BUILD_ID` 注入为 `local-<env>`（同一 commit 可出三环境包，仅靠 commit
  区分不开），metadata 侧对应 `repo generate ... --build local-<env>` 落到
  `repository/generated/<build>/`；
- 设备上**同 bundle id 只能装一个环境**，换环境前先卸载旧包（设置里删 App 即可）；
- 切环境/切分支后宏变化会触发全量重编译，属预期。

## 2. 注意事项（做第 3 步之前先看）

- **metadata 的 commit 取自乐速通仓库**（2026-10-15 修）：`repo generate` 的
  `git_commit` 原来取 CWD（平台仓库），对外部源码会把平台 commit 写进
  metadata，与注入 App 的值永不相同 → `mta run` 被 G8 拦（exit 3）。现在
  `make lst-repo-generate` 内部按输入路径推导 source_root，从乐速通仓库取。
  验证：`repository/generated/local-<env>/source_metadata.json` 的
  `git_commit` 必须等于构建回显里的 `MTA_GIT_COMMIT`。
- **metadata 只扫 App 自身源码**：`lst-repo-generate` 只扫
  `$(LST_SRC)/nfcPushOnline`，**不含 Pods**（含了会多出 SVProgressHUD 等
  pod 元素，污染 screens）。也不加 `--check`——overrides 是 LoginDemo 的手写
  层，与被测产品无关。
- **乐速通的 accessibilityIdentifier 覆盖极低**：614 个 .m 原本只有 2 处
  （两个 CalendarView 的 `calendar`）。2026-10-15 为冒烟用例补了**首页锚点**：
  `LSTHomeViewController.m` 的 tableView 加 `accessibilityIdentifier =
  @"home_list"`（对用户不可见、不影响正常打包），mta 侧
  `repository/overrides/screens/LSTHomeViewController.yaml` 把 screen marker
  指向它。后续首页流用例可复用该锚点；新增 screen 时照此办理：
  **App 加一个真 accessibility id + overrides 声明 marker**（generated 的
  marker 是 `screen.<类名>`，永远匹配不到真机，见 `source/export.py` 注释）。
  （此前一个 schema 失配 bug 曾让 ObjC 分支静默输出 0 元素，已修并有
  round-trip 回归测试。）**坐标驱动/界面结构探测的用例不依赖这个表**，
  但按 accessibility id 定位的用例目前只有 `calendar` / `home_list` 可用。
- **含 mta 能力的包只在本项目构建**：App 同事的正常打包（b.sh 等）保持原样，
  **不需要**他们集成 scene 宏 / 身份注入——没有 `MTA_GIT_COMMIT`/`MTA_BUILD_ID`
  的包本来就无法喂 `mta run`（G8 fail-closed），这是设计而非缺陷。给 mta 用的
  包一律走本项目脚本（`lst_build_*.sh` / make 目标）。
- **`repo-generate` 目标保持 LoginDemo**：`make repo-generate` 仍是 demo 的
  一致性 Gate（12.6），乐速通走独立的 `make lst-repo-generate LST_ENV=<env>`
  （已切好，见上）。两者互不影响，别混用。
- **commit 同源纪律**：构建（第 2 步）与 `lst-repo-generate`（第 3 步）之间
  **不要**再动乐速通仓库，且两者的环境必须一致（`LST_ENV=uat` 构建 ↔
  `--build local-uat` 生成）。中间拉了新代码或换了环境 → 注入的身份与 metadata
  不一致 → `mta run` fail-closed（exit 3）。被拦了就重跑第 2、3 步，**不要**用
  `--allow-metadata-mismatch` 绕（它只豁免「可关联但不一致」，且 CI 下需第二开关）。
- **工作区脏树**：`AppDelegate.m` 的 `#ifdef MTA_SCENE_LIFECYCLE` 改动目前在
  乐速通仓库是未提交状态。建议尽快提交（需 App 同事评审），否则任何一次
  `git pull` 撞冲突或 `git checkout` 都会把它冲掉，构建会退化成启动闪退。

## 3. 版本更新可能撞上的结构性变化（排错表）

按「症状 → 根因 → 处理」组织。前两类最常见。

### 3.1 `pod install` 失败：找不到 Pod

- **症状**：`CocoaPods could not find compatible versions` / spec 源 404。
- **根因**：新版本引入了新 pod，其 spec 在非默认源。
- **处理**：检查 Podfile 顶部两个源（volcengine / bytedance）是否还在——历史上
  它们被注释过导致 RangersAppLog 拉不到。缺谁补谁；新源的 URL 问 App 同事。

### 3.2 构建失败：链接期 undefined symbols / 「built for iOS」

- **症状**：`Undefined symbols for architecture arm64`，或某 `.a`/`.framework`
  报 `built for 'iOS'`（模拟器构建才有后者）。
- **根因**：新引入的国产/私有 SDK 又只带真机 slice。
- **处理**：真机路线不受影响（我们只走 `-sdk iphoneos`）。若有人试图复活模拟器
  路线（`lst-build` 目标），当场劝退——全部 vendor SDK 真机版 slice 的问题是
  不可收敛的，已实测定性为死路。

### 3.3 构建失败：deployment target

- **症状**：`range of supported deployment target versions is 15.0 to 27.0.x`。
- **根因**：工程/某 pod 抬高了 `IPHONEOS_DEPLOYMENT_TARGET`，或 Xcode 升级后
  下限变化。
- **处理**：命令行覆盖已经写死在 Makefile（`LST_DEPLOYMENT_TARGET ?= 15.0`）。
  Xcode 大版本升级后若最低下限抬高，改这一个变量即可，不要去改工程文件。

### 3.4 构建失败：Pods Manifest.lock 失配

- **症状**：`The sandbox is not in sync with the Podfile.lock`。
- **根因**：拉了新代码但没跑 `pod install`。
- **处理**：回第 1 步。这是 fail-loud 设计，不要找绕过。

### 3.5 启动闪退 SIGTRAP：scene 生命周期

- **症状**：装机成功、启动即退；崩溃日志
  `UIApplicationEvaluateRuntimeIssueForNoSceneLifecycleAdoption`。
- **根因**：`MTA_SCENE_LIFECYCLE` 宏没生效——最常见是 §2 说的 AppDelegate.m
  改动被 git 操作冲掉了。
- **处理**：恢复 AppDelegate.m 的 ifdef 块（~50 行，独立 MTASceneDelegate +
  configurationForConnecting）。若 App 侧自己正式采纳了 scene 生命周期（他们升
  Xcode 27 打生产包时必然要做），**删掉**我们这段 ifdef 代码，避免两套并存。

### 3.6 安装失败：签名 / 描述文件

- **症状**：`ApplicationVerificationFailed` 或安装报 profile 相关错误。
- **根因**：证书过期（Apple Development 一年有效期）/ Apple ID 会话失效 /
  新测试机不在设备列表。
- **处理**：Xcode → Settings → Accounts 重新登录（找王雪娇要 2FA 配合）；
  新设备第一次构建时 `-allowProvisioningUpdates` 会自动注册设备。注意重签
  （`codesign --force --sign --preserve-metadata=entitlements`）是流水线内置
  步骤，手动装机不要跳过构建目标直接拷 .app。

### 3.7 `mta run` 被拦：exit 3

- **症状**：`BUILD_METADATA_MISMATCH` / `app_missing_injection`。
- **根因**：commit 不同源（见 §2）或喂了未注入的包（比如 b.sh 出的原始 ipa）。
- **处理**：重跑第 2、3 步。`app_missing_injection` 不可豁免是 G8 的设计，
  说明包本身不对，换包而不是换门。

### 3.8 版本号 / 配置登记

- **症状**：用例报告里版本对不上，或未来产品级 config 生效后跑错版本。
- **根因**：`MARKETING_VERSION` 等随版本变化（当前 4.2.30）。
- **处理**：版本号本身不需要登记（G8 认 commit 不认版本号）。待产品级
  config（bundle id、默认 app 路径等）落地后，新版本更新时**检查一遍 config
  里的路径与 bundle id** 是否仍指向正确值即可。

### 3.9 环境宏编译通过了但打错环境

- **症状**：`LST_ENV=uat` 构建成功，但 App 连的还是生产接口。
- **根因**：CommonMacro.h 的条件是**嵌套**的（§1.5 表），uat 需要两个宏同时
  定义；只给一个会静默掉进生产分支——**编译期不报错**。
- **处理**：Makefile 已固化正确组合。验证纪律：构建后必须查二进制，
  `strings <app>/nfcPushOnline | grep svipcarduat`，uat 包应命中；不能只看
  BUILD SUCCEEDED（假绿同理：编译成功 ≠ 环境正确）。
- **已知例外**：`LSTHTTPManager.m:32/150` 硬编码 `svipcard-new/` 绕过环境宏
  （`BASE_URL_NEW_SVIPCARD` 宏存在但这两处没用）——uat 包的市场服务请求仍会
  打到生产路径。这是 App 侧问题，已报备待修；修掉前用例不应覆盖市场模块。
- **已知例外 2**：`LSTHomeViewController.m:368` 无条件硬编码内网 URL
  `http://172.20.113.33:8084/osplatpagetest/...`（不在任何宏里）——**连生产
  包的二进制都带这段测试地址**。因此 `osplatpagetest` 不能当环境指纹用
  （脚本里的 dev 指纹用的是完整 tpark URL）。该行在 prod 包会推一个打内网
  测试服务器的 webview，同样建议报备 App 同事。

### 3.10 真机 WDA 起不来（iOS 27+：caps / SDK / 隧道三重坑）

- **症状**：`mta run --suite lst_smoke` 在真机上 exit 3
  （`PREFLIGHT ERROR: 设备会话建立失败`），底下挂的具体错误可能是
  `xcodebuild … code 65`、`Failed to receive any data within the timeout: 5000`，
  或 `Unable to launch WebDriverAgent … RemoteXPC is not available`。
- **根因链**（2026-10-15 实测：设备 iOS 27.2 + 本机 SDK 27.0，Appium 3.7.0 +
  xcuitest 12.13.2）。按踩坑顺序，**上一层修完才会露出下一层**：

  1. **caps 键名**。Appium 只认 camelCase；写成 snake_case（`platform_version` /
     `bundle_id` / `no_reset` / `device_name`）时它只提示
     `platform_version (did you mean 'platformVersion'?)` 然后**静默丢弃**——
     日志不报错，caps 等于没给。连锁后果：`platformVersion` 丢了 → Appium 退回
     设备真实 OS 版本 → 该值又变成 WDA 的 `IPHONEOS_DEPLOYMENT_TARGET` →
     设备 OS(27.2) > 本地 SDK(27.0) → `xcodebuild` code 65。
     mta 侧已在 `session/device_session.py` 全部改成 camelCase 并有单测钉住。
  2. **SDK 钳制**。`resolve_local_caps` 把 `platformVersion` 钳到本地 SDK 上限
     （27.2 → 27.0）。⚠️ 这只对「Appium 自己编 WDA」有效，钳不掉 driver.js 内部
     拿设备真实版本拼部署目标的那条路——所以有第 3 条。
  3. **iOS 27+ 不能让 Appium 自己编 WDA**。即使给了 `usePrebuiltWDA` +
     `derivedDataPath`，Appium 仍会跑 `xcodebuild test-without-building` 且仍会把
     设备版本拼进部署目标；而 devicectl 兜底启动在 27+ 已不可用（runner 被推前台
     后要合成 Home 键把自己切后台，Xcode 27 的 DDI/XCTest 栈忽略这次按键 →
     `Failed to background test runner`，Appium 在 27+ 直接禁用了该兜底）。
     故 27+ **必须走 `usePreinstalledWDA` + `prebuiltWDAPath`**：装好 .app 后由
     Appium 直接拉起，全程不碰 xcodebuild。
  4. **RemoteXPC 隧道**。预装路线的拉起走 RemoteXPC，本机必须先建隧道（root，
     见 §1 前置条件 1）。没建时报
     `Failed to launch the preinstalled WebDriverAgent via RemoteXPC …
     (RemoteXPC is not available for this session)`。
     ⚠️ 同一份日志里的 `Failed to create crash reports client … Skipping crash
     logs collection` **不是故障**——只是崩溃日志采集被跳过。
  5. **手机锁屏**（隧道建好之后才露出来，2026-10-16 实测）。iOS 拒绝拉起
     runner，Appium 只报 `Failed to start the preinstalled WebDriverAgent in
     60000 ms`——进程控制其实拿到了 PID，但 8100 始终没人监听，方向被指向
     「WDA 没构建好」。`devicectl` 单独拉起才给真话：
     `device was not, or could not be, unlocked`。mta 已在该错误后附提示
     （`cli/main.py` 的 `_session_failure_hint`），锁屏本身只能人工解。

- **处理**：按 §1「真机前置条件」四条（隧道 / 预建 WDA / 签名 team / 手机解锁）
  做一遍，`mta run` 带 `MTA_XCODE_ORG_ID` + `MTA_WDA_PREBUILT_APP`。装机链路
  本身是通的（AFC/lockdown，日志 `installation of
  'com.facebook.WebDriverAgentRunner.xctrunner' succeeded`），所以隧道是唯一的
  外部依赖。
  另一条备选 `MTA_WDA_DERIVED_DATA` → `usePrebuiltWDA`（仍要 xcodebuild，只是
  免编译）留给设备 OS ≤ 本地 SDK 的机器；⚠️ 该路径必须是**绝对路径**——Appium
  在自己的 CWD（WDA 包目录）里跑 xcodebuild，相对路径会解析到别处（实测仍报 65）。
- **已知 Appium 侧小毛病**：devicectl 兜底查进程用的 filter 字段
  `executable.path` 与当前 devicectl 不符（报 `Did you mean 'Executable
  Path'?`）。有隧道时走不到这条分支，不用管；别处撞到同款就是版本错配。

## 4. 各文件职责速查（接入相关）

| 位置 | 文件 | 职责 | 版本更新时动不动 |
|---|---|---|---|
| mta 仓库 | `Makefile` 的 `lst-repo-generate` | metadata 生成（commit 取乐速通仓库，扫 App 源码不含 Pods） | 否 |
| mta 仓库 | `source/build_identity.py` 的 `read_app_identity_from_bundle` | 真机身份路线：从本地 .app 工件读（`--app-path`），不碰设备 | 否 |
| mta 仓库 | `Makefile` 的 `lst-build-device` | 构建+注入 scene 宏/manifest+身份键+重签 | 否（自动化吸收） |
| mta 仓库 | `Makefile` 的 `lst-install-device` | devicectl 装机 | 否 |
| mta 仓库 | `session/device_session.py` 的 `resolve_local_caps` | 真机 caps 装配：camelCase 键名 / `platformVersion` 钳到本地 SDK / 预建 WDA 路线（见 §3.10） | 否 |
| mta 仓库 | `scripts/lst_build_dev.sh` / `lst_build_uat.sh` / `lst_build_prod.sh` | 三环境一键构建+strings 环境验证（+可选装机），内部调 make 目标 | 否（指纹失效时改 VERIFY_PATTERN） |
| 乐速通仓库 | `AppDelegate.m` ifdef 块 | iOS 27 scene 生命周期最小采纳 | 待 App 正式采纳后删除 |
| 乐速通仓库 | `Podfile` 顶部两个 spec 源 | pod 拉取 | 否（勿再注释） |
| 记忆 | `dut-nfcpushonline.md` | Claude 跨会话上下文 | 出新坑时更新 |
