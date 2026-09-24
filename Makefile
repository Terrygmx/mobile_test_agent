scan:
	@bash -c 'export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer; \
	TOOLCHAIN=/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr; \
	cd source && mv -f swift_scan.swift /tmp/ 2>/dev/null; true; \
	[ -f source/main.swift ] || mv source/main.swift source/main.swift; \
	cd source && swiftc -o /tmp/swift_scan main.swift \
	  -I $$TOOLCHAIN/lib/swift/host -L $$TOOLCHAIN/lib/swift/host \
	  -Xlinker -rpath -Xlinker $$TOOLCHAIN/lib/swift/host'
