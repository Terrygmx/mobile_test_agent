"""pytest 根配置：收集时自动把项目根插入 sys.path，
使所有测试文件直接 `from tracer... import ...`，无需各自 sys.path hack（review R4-1）。
"""
