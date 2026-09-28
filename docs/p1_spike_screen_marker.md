# P1-03 Spike：Screen marker 方案可行性验证

> 日期：2026-09-28　分支：`P1-0928`　判定标准：设计 13.3
> 结论先行：**`mtaScreen()` 方案可行，三场景全绿，Task 1.5 按此实现。**

## 方案

App 侧统一修饰符（`ios_demo/LoginDemo/SpikeScreen.swift`）：

```swift
extension View {
    func mtaScreen(_ name: String) -> some View {
        self.accessibilityElement(children: .contain)
            .accessibilityIdentifier("screen.\(name)")
    }
}
```

挂根视图，id 形如 `screen.SpikeTab`。通过启动参数 `-UITestSpikeScreen` 激活 spike 页，不影响正常用例路径。

## 验证结果（用户终端实测，`out/spike/spike_summary.json`）

| 场景 | marker 存在且唯一 | 子元素 id 不折叠 | 判定 |
|---|---|---|---|
| TabView | `screen.SpikeTab` ✓ | `tab_inner_title`、`tab_inner_button` ✓ | PASS |
| NavigationStack（push Detail） | `screen.SpikeNavDetail` ✓ | `nav_detail_text` ✓ | PASS |
| Sheet（presentationDetents） | `screen.SpikeSheet` ✓ | `sheet_inner_text` ✓ | PASS |

`SCRIPT_EXIT=0`，page_source 全文留档 `out/spike/page_source_*.xml`。

## 对设计 13.3 疑虑的裁决

疑虑：容器挂 `accessibilityElement(children: .contain)` + id 会不会吞掉子元素 identifier？
裁决：**不会**。三场景中子元素 `accessibilityIdentifier` 均可在 XCUITest 树中唯一定位。
Task 1.5 直接按此方案给五屏挂 marker，无需 overlay 层备选方案。

## 过程中发现的新坑（已记入 skill）

1. **tabItem 的 accessibilityIdentifier 不暴露进 XCUITest 树**：TabBar 按钮只暴露
   label name（其子 Image 的 name 是 SF Symbol 名）。自动化切 tab 不能依赖
   tabItem 上的 identifier——用 label `NAME`，或
   class chain `**/XCUIElementTypeTabBar/XCUIElementTypeButton[2]` 兜底
   （`phase0/spike_screen.py` 的 `TAB_STRATEGIES` 三级 fallback）。
2. **Appium iOS caps：`automation_name` 必须为 `"XCUITest"`**，写 `"Appium"`
   会报 driver 匹配失败（环境明明装了 xcuitest driver）。
3. xcodebuild 需 `export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer`；
   交互终端里不要用 `set -e` 粘贴多命令块（任一命令失败会关掉整个窗口）。

## 复现

```bash
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
xcodebuild -project ios_demo/LoginDemo.xcodeproj -scheme LoginDemo \
  -destination 'platform=iOS Simulator,name=iPhone 14' build
xcrun simctl install booted ~/Library/Developer/Xcode/DerivedData/LoginDemo-*/Build/Products/Debug-iphonesimulator/LoginDemo.app
xcrun simctl launch booted com.phaset0.logindemo -UITestSpikeScreen
.venv/bin/python phase0/spike_screen.py   # → out/spike/spike_summary.json
```
