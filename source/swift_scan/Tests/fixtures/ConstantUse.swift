import SwiftUI

struct LoginView: View {
    var body: some View {
        Button(action: {}) {
            Text("登录")
        }
        .accessibilityIdentifier(A11y.loginButton)          // constant ✓
        .accessibilityIdentifier(A11y.usernameField)         // constant ✓
        .accessibilityIdentifier(A11y.ambiguous)            // 歧义 → dynamic
        .accessibilityIdentifier(A11y.count)                // 非字符串 → dynamic
        .accessibilityIdentifier(A11y.mutable)              // var → dynamic
        .accessibilityIdentifier(A11y.computed)             // 表达式 → dynamic
        .accessibilityIdentifier(someRuntimeVar)           // 运行时值 → unknown
    }
}
