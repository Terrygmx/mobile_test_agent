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
