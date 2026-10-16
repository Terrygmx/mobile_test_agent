test:
	@bash -c 'source .venv/bin/activate && python -m pytest tests'

# SwiftPM source scanner（P1-09 / 12.1）。swift-syntax 与工具链同代——
# 本机 Xcode 27 / Swift 6.4 对应 swift-syntax main 分支（release tag 509
# 实测不兼容）。release 构建供 CI；本地 .venv 走 debug 产物兜底。
scan:
	@bash -c 'export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer; \
	cd source/swift_scan && swift build -c release'

# Swift 侧单测（12.1/12.2 resolution 四档、UIKit 赋值、行号、容器归属）
scan-test:
	@bash -c 'export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer; \
	cd source/swift_scan && swift test'

# 12.3 metadata 生成 + 12.6 一致性 Gate（overrides vs generated）
repo-generate:
	@bash -c 'source .venv/bin/activate && \
	python -m cli.main repo generate ios_demo/LoginDemo --check'

# 乐速通 metadata 生成（外部源码）：git_commit 取自**乐速通仓库 HEAD**
# （source/metadata.py `_git_commit(source_root)`，2026-10-15 修：走 CWD 会把
# 平台自己的 commit 写进 metadata，与 lst-build-device 注入 App 的值永不相同 →
# mta run G8 fail-closed）。--build 必须与构建时的 LST_ENV 一致
# （local-<env>），且两次操作之间不要动乐速通仓库（commit 同源纪律）。
# 扫描范围只含 nfcPushOnline 自身源码（不含 Pods——pod 的 accessibilityIdentifier
# 会污染 screens；实测含 Pods 时多出 SVProgressHUD）。不加 --check：overrides
# 是 LoginDemo 的手写层，与被测产品无关，比了只会误报。
LST_META_SRC = $(LST_SRC)/nfcPushOnline

lst-repo-generate:
	@bash -c 'source .venv/bin/activate && \
	python -m cli.main repo generate "$(LST_META_SRC)" --build local-$(LST_ENV)'

# Build Identity 注入构建（12.5 / Task 3.3 第 1 步）。注入走 12.5「无 CI
# 兜底」允许的本地脚本 plist 注入：xcodebuild 后 PlistBuddy 写入
# MTA_GIT_COMMIT / MTA_BUILD_ID。（INFOPLIST_KEY_<自定义键> 实测不进生成的
# Info.plist——该机制只合并固定白名单键，clean rebuild 复现两次，不是增量
# 缓存问题。）G8：App/Metadata/commit 可关联——p1-build 之后必须重新
# `make repo-generate` 保持同 commit，否则 `mta run` 真机路径 fail-closed
# 拦截（exit 3）。项目无 shared scheme → -target 构建；-derivedDataPath 与
# -target 互斥（实测 Error 64），产物目录用 SYMROOT/OBJROOT 重定向。
P1_COMMIT = $(shell git rev-parse --short HEAD)
P1_BUILDDIR = $(CURDIR)/build/p1
P1_APP = $(P1_BUILDDIR)/Build/Products/Debug-iphonesimulator/LoginDemo.app

p1-build:
	@bash -c 'export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer; \
	xcodebuild -project ios_demo/LoginDemo.xcodeproj -target LoginDemo \
	  -configuration Debug -sdk iphonesimulator \
	  SYMROOT=$(P1_BUILDDIR)/Build/Products \
	  OBJROOT=$(P1_BUILDDIR)/Build/Intermediates.noindex \
	  build && \
	PLIST=$(P1_APP)/Info.plist; \
	/usr/libexec/PlistBuddy -c "Delete :MTA_GIT_COMMIT" $$PLIST 2>/dev/null; \
	/usr/libexec/PlistBuddy -c "Delete :MTA_BUILD_ID" $$PLIST 2>/dev/null; \
	/usr/libexec/PlistBuddy -c "Add :MTA_GIT_COMMIT string $(P1_COMMIT)" $$PLIST && \
	/usr/libexec/PlistBuddy -c "Add :MTA_BUILD_ID string local" $$PLIST && \
	echo "p1-build: injected MTA_GIT_COMMIT=$(P1_COMMIT) MTA_BUILD_ID=local"'

# 安装到 booted 模拟器（G8 / M4 矩阵 #21 真机验证前置；
# MTA_SIM_UDID 可显式指定，缺省取第一个 booted 设备）
p1-install:
	@bash -c 'export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer; \
	UDID=$${MTA_SIM_UDID:-$$(xcrun simctl list devices | grep Booted | head -1 | sed -E "s/.*[(]([A-F0-9-]+)[)].*/\1/")}; \
	echo "p1-install: $$UDID"; \
	xcrun simctl install "$$UDID" build/p1/Build/Products/Debug-iphonesimulator/LoginDemo.app'

# —— 乐速通（nfcPushOnline）被测 App：外部源码的构建注入（12.5 同一契约）——
# 源码在平台仓库之外（CocoaPods workspace，LST_SRC 可覆盖），身份契约与
# p1-build 相同：xcodebuild 后 PlistBuddy 写入 MTA_GIT_COMMIT / MTA_BUILD_ID。
# 注入 commit 取**乐速通仓库自身**的 HEAD（非本仓库）——它必须与
# `mta repo generate $(LST_SRC)` 生成 metadata 用的 commit 同源，否则
# `mta run` 真机路径 G8 fail-closed（exit 3）。非 git 目录兜底 `external`。
# 注意：[CP] Check Pods Manifest.lock 阶段会在 Podfile.lock 与 Pods/Manifest.lock
# 失配时 fail-loud——先去 $(LST_SRC) 下 `pod install`。
# 广告 SDK（穿山甲/广点通/百度/快手）可能无模拟器 slice：链接失败即退真机路线。
# IPHONEOS_DEPLOYMENT_TARGET 在命令行覆盖为 15.0：本机 Xcode 27 最低支持
# 15.0（实测 12.0 直接 error），不改乐速通工程文件、只影响本次构建。
# ARCHS=arm64：Apple Silicon 模拟器默认只拉 arm64 slice；x86_64 链接实测
# 因穿山甲/自家 arm64-only 静态库缺 x86_64 而挂（LST_BLESDK/TclSdk undefined）。
# EXCLUDED_ARCHS= 清空：Pods/xcconfig 里焊着 `EXCLUDED_ARCHS[sdk=iphonesimulator*]=arm64`
# 的老 Rosetta hack（Podfile post_install 并没有它，属残留），不清空 Pods 侧
# 无法编 arm64。若重新 pod install 后 xcconfig 已无此行，这个覆盖也无害。
LST_SRC ?= /Users/terrygmx/Desktop/workspace/ios
LST_DEPLOYMENT_TARGET ?= 15.0
LST_ARCHS ?= arm64
LST_BUILDDIR = $(CURDIR)/build/lst
LST_APP = $(LST_BUILDDIR)/Build/Products/Debug-iphonesimulator/nfcPushOnline.app

lst-build:
	@bash -c 'set -e; \
	COMMIT=$$(git -C "$(LST_SRC)" rev-parse --short HEAD 2>/dev/null || echo external); \
	export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer; \
	xcodebuild -workspace "$(LST_SRC)/nfcPushOnline.xcworkspace" -scheme nfcPushOnline \
	  -configuration Debug -sdk iphonesimulator -derivedDataPath "$(LST_BUILDDIR)" \
	  IPHONEOS_DEPLOYMENT_TARGET=$(LST_DEPLOYMENT_TARGET) \
	  ARCHS=$(LST_ARCHS) ONLY_ACTIVE_ARCH=YES EXCLUDED_ARCHS= \
	  OTHER_LDFLAGS="$$(inherited) -Wl,-ld_classic" \
	  CODE_SIGNING_ALLOWED=NO build || { echo "lst-build: BUILD FAILED"; exit 1; }; \
	PLIST="$(LST_APP)/Info.plist"; \
	test -f "$$PLIST" || { echo "lst-build: 产物不存在：$$PLIST"; exit 1; }; \
	/usr/libexec/PlistBuddy -c "Delete :MTA_GIT_COMMIT" "$$PLIST" 2>/dev/null || true; \
	/usr/libexec/PlistBuddy -c "Delete :MTA_BUILD_ID" "$$PLIST" 2>/dev/null || true; \
	/usr/libexec/PlistBuddy -c "Add :MTA_GIT_COMMIT string $$COMMIT" "$$PLIST" && \
	/usr/libexec/PlistBuddy -c "Add :MTA_BUILD_ID string local" "$$PLIST" && \
	echo "lst-build: injected MTA_GIT_COMMIT=$$COMMIT MTA_BUILD_ID=local -> $(LST_APP)"'

# 安装乐速通模拟器产物到 booted 模拟器（MTA_SIM_UDID 可显式指定）
lst-install:
	@bash -c 'export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer; \
	UDID=$${MTA_SIM_UDID:-$$(xcrun simctl list devices | grep Booted | head -1 | sed -E "s/.*[(]([A-F0-9-]+)[)].*/\1/")}; \
	echo "lst-install: $$UDID"; \
	xcrun simctl install "$$UDID" "$(LST_APP)"'

# —— 乐速通真机构建（推荐路线）——
# 模拟器路线实测死路：本工程全部私有/国产 SDK（UM、银联、极光、百度、穿山甲、
# LST_BLESDK/TclSdk）的 arm64 slice 均为真机版，ld_classic 只能把错误逐个降级
# 成下一家。真机构建零 hack；签名走工程内置 Automatic + Team TSVS7C28BU
# （与 b.sh/Ad-hoc profile 同 Team）。需要本机钥匙串有该 Team 的开发证书。
# 部署目标同样命令行抬到 15.0（真机 SDK 与模拟器 SDK 同下限）。
#
# 环境（LST_ENV）：dev / uat / prod，经编译期宏选择。注意 CommonMacro.h 的
# 条件是嵌套的：TESTING_MODES 是外层总开关，VERIFCATION_MODES 是其内的 uat
# 细分——dev=TESTING_MODES 单独；uat=两个都要；prod=都不定义（走最外层 #else）。
# ⚠️ 分支切换不切环境——dev 与 production 分支的 CommonMacro.h diff 为空，
# 环境只认宏。产物目录与 MTA_BUILD_ID 均按环境隔离（同一 commit 可出三环境包，
# 仅靠 commit 区分不开；metadata 侧用 `repo generate --build local-<env>` 对齐）。
LST_ENV ?= uat
ifeq ($(LST_ENV),dev)
LST_ENV_DEFS = TESTING_MODES=1
else ifeq ($(LST_ENV),uat)
LST_ENV_DEFS = TESTING_MODES=1 VERIFCATION_MODES=1
else ifeq ($(LST_ENV),prod)
LST_ENV_DEFS =
else
$(error LST_ENV 只能是 dev / uat / prod)
endif
LST_DEVICE_BUILDDIR = $(CURDIR)/build/lst-device-$(LST_ENV)
LST_DEVICE_APP = $(LST_DEVICE_BUILDDIR)/Build/Products/Debug-iphoneos/nfcPushOnline.app
# 乐速通的签名 team（playbook §0 固定信息）。WDA 工程不带 team，不给会被
# xcodebuild 判 requires a development team（code 65）。
LST_XCODE_ORG_ID ?= TSVS7C28BU
# 预建 WDA（iOS 27+ 必需，见 docs/lst_update_playbook.md §1 前置条件 2 /
# §3.10）：设备 OS 高于本地 SDK 时 Appium 自己编 WDA 必挂，改用预建产物。
LST_WDA_PREBUILT_APP = $(CURDIR)/out/wda-ddata/Build/Products/Debug-iphoneos/WebDriverAgentRunner-Runner.app

# 真机目标注入发生在签名之后：PlistBuddy 改 Info.plist 会破签名 → 注入完
# 必须立刻重签主 bundle（嵌套 framework 未动）。证书取钥匙串第一个
# Apple Development；entitlements 从既有签名原样保留（只改了 plist）。
lst-build-device:
	@bash -c 'set -e; \
	COMMIT=$$(git -C "$(LST_SRC)" rev-parse --short HEAD 2>/dev/null || echo external); \
	export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer; \
	echo "== lst-build-device: env=$(LST_ENV) commit=$$COMMIT"; \
	xcodebuild -workspace "$(LST_SRC)/nfcPushOnline.xcworkspace" -scheme nfcPushOnline \
	  -configuration Debug -sdk iphoneos -derivedDataPath "$(LST_DEVICE_BUILDDIR)" \
	  IPHONEOS_DEPLOYMENT_TARGET=$(LST_DEPLOYMENT_TARGET) \
	  ARCHS=$(LST_ARCHS) ONLY_ACTIVE_ARCH=YES \
	  GCC_PREPROCESSOR_DEFINITIONS="$$(inherited) MTA_SCENE_LIFECYCLE=1 $(LST_ENV_DEFS)" \
	  CODE_SIGN_STYLE=Automatic -allowProvisioningUpdates \
	  build || { echo "lst-build-device: BUILD FAILED"; exit 1; }; \
	PLIST="$(LST_DEVICE_APP)/Info.plist"; \
	test -f "$$PLIST" || { echo "lst-build-device: 产物不存在：$$PLIST"; exit 1; }; \
	/usr/libexec/PlistBuddy -c "Delete :MTA_GIT_COMMIT" "$$PLIST" 2>/dev/null || true; \
	/usr/libexec/PlistBuddy -c "Delete :MTA_BUILD_ID" "$$PLIST" 2>/dev/null || true; \
	/usr/libexec/PlistBuddy -c "Delete :UIApplicationSceneManifest" "$$PLIST" 2>/dev/null || true; \
	/usr/libexec/PlistBuddy -c "Add :UIApplicationSceneManifest dict" "$$PLIST" && \
	/usr/libexec/PlistBuddy -c "Add :UIApplicationSceneManifest:UIApplicationSupportsMultipleScenes bool false" "$$PLIST" && \
	/usr/libexec/PlistBuddy -c "Add :MTA_GIT_COMMIT string $$COMMIT" "$$PLIST" && \
	/usr/libexec/PlistBuddy -c "Add :MTA_BUILD_ID string local-$(LST_ENV)" "$$PLIST" && \
	IDENT=$$(security find-identity -v -p codesigning | awk "/Apple Development/{print \$$2; exit}"); \
	test -n "$$IDENT" || { echo "lst-build-device: 钥匙串无 Apple Development 证书"; exit 1; }; \
	codesign --force --sign "$$IDENT" --preserve-metadata=entitlements "$(LST_DEVICE_APP)" && \
	echo "lst-build-device: injected MTA_GIT_COMMIT=$$COMMIT MTA_BUILD_ID=local-$(LST_ENV) -> $(LST_DEVICE_APP) (resigned: $$IDENT)"'

# 安装真机产物到 USB 连接的设备（iOS 17+ 走 devicectl；MTA_DEVICE_UDID 可显式
# 指定，缺省取第一台 connected 的 physical 设备。devicectl 新版输出无 Available
# 分节（Name/Hostname/Identifier/State/Model/Reality 表头，Hostname 常为空导致
# 列错位），故按行内 physical+状态过滤后提取 UDID，不按列取）。State 的措辞
# 随 Xcode 版本变过（connected → available (paired)，2026-10-15 实测），故按
# 关键词放宽匹配，避免「设备明明在却过滤空」。
lst-install-device:
	@bash -c 'set -e; \
	export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer; \
	UDID=$${MTA_DEVICE_UDID:-$$(xcrun devicectl list devices 2>/dev/null | grep physical | grep -Ei "connected|available" | grep -oE "[0-9A-F]{8}-[0-9A-F]{12,16}" | head -1)}; \
	test -n "$$UDID" || { echo "lst-install-device: 未找到 USB 连接的 physical 设备（xcrun devicectl list devices 为空或 State 非 connected/available）"; exit 1; }; \
	echo "lst-install-device: $$UDID"; \
	xcrun devicectl device install app --device "$$UDID" "$(LST_DEVICE_APP)"'

# 真机冒烟一条龙（2026-10-16 首次真绿）。把「漏一个环境变量就白跑一小时」的
# 东西固化下来：WDA 签名 team + 预建 WDA 路线 + UDID 自动探测（同
# lst-install-device 的过滤逻辑）。
# 前置条件见 docs/lst_update_playbook.md §1：① RemoteXPC 隧道在跑
# （sudo appium driver run xcuitest tunnel-creation）；② 手机已解锁、没自动锁屏；
# ③ App 已装机（make lst-install-device LST_ENV=<env>）。
lst-smoke-device:
	@bash -c 'set -e; \
	export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer; \
	UDID=$${MTA_DEVICE_UDID:-$$(xcrun devicectl list devices 2>/dev/null | grep physical | grep -Ei "connected|available" | grep -oE "[0-9A-F]{8}-[0-9A-F]{12,16}" | head -1)}; \
	test -n "$$UDID" || { echo "lst-smoke-device: 未找到 USB 连接的 physical 设备"; exit 1; }; \
	test -d "$(LST_WDA_PREBUILT_APP)" || { echo "lst-smoke-device: 预建 WDA 不存在（$(LST_WDA_PREBUILT_APP)），构建命令见 docs/lst_update_playbook.md §1 前置条件 2"; exit 1; }; \
	echo "lst-smoke-device: $$UDID env=$(LST_ENV)"; \
	MTA_XCODE_ORG_ID=$(LST_XCODE_ORG_ID) \
	MTA_WDA_PREBUILT_APP="$(LST_WDA_PREBUILT_APP)" \
	$(CURDIR)/.venv/bin/python -m cli.main run \
	  --suite lst_smoke --suites-root suites \
	  --udid "$$UDID" --bundle-id com.etc.nfcPushOnline \
	  --app-path "$(LST_DEVICE_APP)" \
	  --overrides repository/overrides \
	  --generated repository/generated/local-$(LST_ENV) \
	  --metadata repository/generated/local-$(LST_ENV)/source_metadata.json \
	  --db out/lst-smoke.db'

