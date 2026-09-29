import SwiftUI

// Phase 0 被测 App — 登录 Demo
// 元素 id 与 docs/mobile-test-agent-phase0-design.md 4.4 login_demo.yaml 一一对应。
// Task 1.5（P1-04）：五屏（Login/Home/Search/Detail/Profile）根视图挂 mtaScreen()
// marker（设计 13.1/附录 A1），新增 Search→Detail 导航流与 Profile logout。
// R9-5：原 ContentView.swift 纯转发壳已删除，入口直接用 LoginView。

struct LoginView: View {
    @State private var username: String = ""
    @State private var password: String = ""
    @State private var isLoggedIn: Bool = false
    @State private var errorMessage: String?

    var body: some View {
        if isLoggedIn {
            HomeView(username: username, onLogout: { isLoggedIn = false })
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
            .mtaScreen("LoginView")
            // 注意：accessibilityIdentifier 不能挂在含可交互子元素的容器上，
            // 否则 SwiftUI 会将子元素折叠进一个 accessibility 元素（name 全变成容器 id）。
            // mtaScreen() 带 accessibilityElement(children: .contain)，Spike 已验证不折叠。
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
    var onLogout: () -> Void = {}

    // 附录 A3：列表项 identifier 用业务稳定键 cell_<key>，不用下标
    private let items: [(key: String, title: String)] = [
        ("alpha", "Alpha 项"),
        ("beta", "Beta 项"),
        ("gamma", "Gamma 项"),
    ]

    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                Text("首页")
                    .font(.largeTitle)
                    .accessibilityIdentifier("home_page")
                Text("欢迎, \(username)")
                    .accessibilityIdentifier("welcome_label")

                // 最小业务：列表 3 个 cell → Search 页 → Detail 页
                List(items, id: \.key) { item in
                    NavigationLink(value: item.key) {
                        Text(item.title)
                            .accessibilityIdentifier("cell_\(item.key)")
                    }
                }
                .accessibilityIdentifier("home_list")

                NavigationLink("搜索", value: "search")
                    .accessibilityIdentifier("go_search")

                NavigationLink("我的", value: "profile")
                    .accessibilityIdentifier("go_profile")

                Spacer()
            }
            .padding()
            .mtaScreen("HomeView")
            .navigationDestination(for: String.self) { value in
                if value == "search" {
                    SearchView()
                } else if value == "profile" {
                    ProfileView(onLogout: onLogout)
                } else {
                    DetailView(itemKey: value)
                }
            }
        }
    }
}

// MARK: - Task 1.5 新增：Search / Detail / Profile

struct SearchView: View {
    @State private var query: String = ""

    private let catalog: [(key: String, title: String)] = [
        ("alpha", "Alpha 项"),
        ("beta", "Beta 项"),
        ("gamma", "Gamma 项"),
    ]

    private var results: [(key: String, title: String)] {
        // 测试环境：空查询给全量（保证 cell 可静态定位），非空做包含匹配
        query.isEmpty ? catalog : catalog.filter { $0.title.localizedCaseInsensitiveContains(query) }
    }

    var body: some View {
        VStack(spacing: 12) {
            Text("搜索")
                .font(.largeTitle)
                .accessibilityIdentifier("search_title")

            TextField("关键词", text: $query)
                .textFieldStyle(.roundedBorder)
                .accessibilityIdentifier("search_field")

            List(results, id: \.key) { item in
                NavigationLink(value: item.key) {
                    Text(item.title)
                        .accessibilityIdentifier("cell_\(item.key)")
                }
            }
            .accessibilityIdentifier("search_results")

            Spacer()
        }
        .padding()
        .mtaScreen("SearchView")
    }
}

struct DetailView: View {
    let itemKey: String

    var body: some View {
        VStack(spacing: 12) {
            Text("详情")
                .font(.largeTitle)
                .accessibilityIdentifier("detail_title")
            Text("条目: \(itemKey)")
                .accessibilityIdentifier("detail_item_key")
            Spacer()
        }
        .padding()
        .mtaScreen("DetailView")
    }
}

struct ProfileView: View {
    var onLogout: () -> Void

    var body: some View {
        VStack(spacing: 12) {
            Text("我的")
                .font(.largeTitle)
                .accessibilityIdentifier("profile_title")

            Button(action: onLogout) {
                Text("退出登录")
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.red)
                    .foregroundColor(.white)
                    .cornerRadius(8)
            }
            .accessibilityIdentifier("logout_button")

            Spacer()
        }
        .padding()
        .mtaScreen("ProfileView")
    }
}

@main
struct LoginDemoApp: App {
    init() {
        // P1-03 Spike 入口：-UITestSpikeScreen 时展示三场景（Tab/Nav/Sheet），
        // 验证 mtaScreen() marker 方案；不带该参数走正常 LoginView 路径。
        _spikeRoot = State(initialValue: ProcessInfo.processInfo.arguments.contains("-UITestSpikeScreen"))
    }

    @State private var spikeRoot: Bool

    var body: some Scene {
        WindowGroup {
            if spikeRoot {
                SpikeScreenRoot()
            } else {
                LoginView()
            }
        }
    }
}
