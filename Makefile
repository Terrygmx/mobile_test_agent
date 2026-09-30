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
