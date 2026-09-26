# review P2-3：删除死代码（swift_scan.swift 搬移、恒假 no-op），保留真实构建链
scan:
	@bash -c 'export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer; \
	TOOLCHAIN=/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr; \
	cd source && swiftc -o /tmp/swift_scan main.swift \
	  -I $$TOOLCHAIN/lib/swift/host -L $$TOOLCHAIN/lib/swift/host \
	  -Xlinker -rpath -Xlinker $$TOOLCHAIN/lib/swift/host'
