import SwiftUI

// Phase 0 被测 App — 登录 Demo
// 元素 id 与 docs/mobile-test-agent-phase0-design.md 4.4 login_demo.yaml 一一对应。

struct LoginView: View {
    @State private var username: String = ""
    @State private var password: String = ""
    @State private var isLoggedIn: Bool = false
    @State private var errorMessage: String?

    var body: some View {
        if isLoggedIn {
            HomeView(username: username)
        } else {
            VStack(spacing: 16) {
                Text("登录")
                    .font(.largeTitle)

                TextField("用户名", text: $username)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityIdentifier("username_field")

                SecureField("密码", text: $password)
                    .textFieldStyle(.roundedBorder)
                    .accessibilityIdentifier("password_field")

                if let errorMessage {
                    Text(errorMessage)
                        .foregroundColor(.red)
                        .accessibilityIdentifier("error_message")
                }

                Button(action: login) {
                    Text("登录")
                        .frame(maxWidth: .infinity)
                        .padding()
                        .background(Color.blue)
                        .foregroundColor(.white)
                        .cornerRadius(8)
                }
                .accessibilityIdentifier("login_button")

                Spacer()
            }
            .padding()
            // 注意：accessibilityIdentifier 不能挂在含可交互子元素的容器上，
            // 否则 SwiftUI 会将子元素折叠进一个 accessibility 元素（name 全变成容器 id）。
        }
    }

    private func login() {
        // 测试环境：任意非空 username/password 即登录成功
        if username.isEmpty || password.isEmpty {
            errorMessage = "用户名或密码不能为空"
        } else {
            errorMessage = nil
            isLoggedIn = true
        }
    }
}

struct HomeView: View {
    let username: String

    var body: some View {
        VStack(spacing: 12) {
            Text("首页")
                .font(.largeTitle)
                .accessibilityIdentifier("home_page")
            Text("欢迎, \(username)")
                .accessibilityIdentifier("welcome_label")
            Spacer()
        }
        .padding()
        // id 不挂在容器上（会折叠子元素），挂在标题 Text 上作为页面标志
    }
}

@main
struct LoginDemoApp: App {
    var body: some Scene {
        WindowGroup {
            LoginView()
        }
    }
}
