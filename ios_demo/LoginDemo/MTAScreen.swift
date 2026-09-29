import SwiftUI

/// Task 1.5（P1-04）：Screen marker 统一修饰符（设计 13.1 / 附录 A1）。
/// 方案经 P1-03 Spike 三场景（Tab/Nav/Sheet）验证可行（docs/p1_spike_screen_marker.md）：
/// `accessibilityElement(children: .contain)` + accessibilityIdentifier("screen.<Name>")
/// 挂页面根视图，marker 唯一可见且子元素 identifier 不被折叠。
/// 每个主要页面根视图必须 `.mtaScreen("<ViewName>")`，id 形如 `screen.HomeView`。
extension View {
    func mtaScreen(_ name: String) -> some View {
        self.accessibilityElement(children: .contain)
            .accessibilityIdentifier("screen.\(name)")
    }
}
