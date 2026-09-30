import Testing
import SwiftSyntax
import SwiftParser

@testable import mta_source_scan

// 注意：这些测试直接驱动 public API（visitor + ConstantTableBuilder），不走
// subprocess——scanFile 的语种分派由集成层验证。

private func scanSwift(_ source: String) -> ScanResult {
    let tree = Parser.parse(source: source)
    let builder = ConstantTableBuilder()
    builder.walk(tree)
    let constants = builder.build()
    let visitor = SwiftUIVisitor(filePath: "Test.swift", constants: constants)
    visitor.walk(tree)
    let r = visitor.buildResult()
    // 与生产 scanFile 一致：Swift 文件同时跑 UIKit visitor（赋值形态只在
    // SwiftUI visitor 里不会被识别——两 visitor 覆盖互斥模式，不会双记）
    let ui = UIKitVisitor(filePath: "Test.swift", constants: constants)
    ui.walk(tree)
    let ru = ui.buildResult()
    return ScanResult(
        screens: Set(r.screens).union(ru.screens).sorted(),
        screenElements: r.screenElements + ru.screenElements)
}

private func elements(_ result: ScanResult, screen: String) -> [ScannedElement] {
    guard let s = result.screenElements.first(where: { $0.name == screen }) else {
        return []
    }
    return s.elements
}

@Test func literalIdentifierResolvedFromStringLiteral() throws {
    let r = scanSwift("""
    struct LoginView: View {
        var body: some View {
            Button(action: {}) { Text("登录") }
                .accessibilityIdentifier("login_button")
        }
    }
    """)
    let els = elements(r, screen: "LoginView")
    #expect(els.count == 1)
    #expect(els[0].id == "login_button")
    #expect(els[0].accessibilityId == "login_button")
    #expect(els[0].resolutionType == .literal)
    #expect(els[0].containerType == "LoginView")
    #expect(els[0].type == "button")
    #expect(els[0].label == "登录")
}

@Test func constantIdentifierResolvedViaTwoPassScan() throws {
    let r = scanSwift("""
    struct A11y { static let login = "login_button" }
    struct LoginView: View {
        var body: some View {
            Button(action: {}) { Text("登录") }
                .accessibilityIdentifier(A11y.login)
        }
    }
    """)
    let els = elements(r, screen: "LoginView")
    #expect(els.count == 1)
    #expect(els[0].resolutionType == .constant)
    #expect(els[0].id == "login_button")
}

@Test func ambiguousConstantDeclarationFallsBackToDynamic() throws {
    // 同名两处 → 剔除（不得猜）→ 引用落 dynamic（不是 unknown：引用在但值
    // 不可静态求值）
    let r = scanSwift("""
    struct A { static let dup = "one" }
    struct B { static let dup = "two" }
    struct LoginView: View {
        var body: some View {
            Text("x")
                .accessibilityIdentifier(A.dup)
        }
    }
    """)
    let els = elements(r, screen: "LoginView")
    #expect(els.count == 1)
    #expect(els[0].resolutionType == .dynamic)
    #expect(els[0].accessibilityId == nil)
    // id 是排障占位（带位置），不是可定位 id
    #expect(els[0].id.hasPrefix("UNKNOWN:"))
}
@Test func nonStringOrVarDeclarationFallsBackToDynamic() throws {
    let r = scanSwift("""
    struct A11y {
        static let count = 42
        static var mutable = "m_value"
        static let computed = "x" + "y"
    }
    struct LoginView: View {
        var body: some View {
            Text("x").accessibilityIdentifier(A11y.count)
            Text("y").accessibilityIdentifier(A11y.mutable)
            Text("z").accessibilityIdentifier(A11y.computed)
        }
    }
    """)
    let els = elements(r, screen: "LoginView")
    #expect(els.count == 3)
    #expect(els.allSatisfy { $0.resolutionType == .dynamic })
}

@Test func runtimeVariableReferenceIsUnknown() throws {
    let r = scanSwift("""
    struct LoginView: View {
        var body: some View {
            Text("x")
                .accessibilityIdentifier(someRuntimeVariable)
        }
    }
    """)
    let els = elements(r, screen: "LoginView")
    #expect(els.count == 1)
    #expect(els[0].resolutionType == .unknown)
}

@Test func interpolatedIdentifierIsDynamic() throws {
    let r = scanSwift("""
    struct HomeView: View {
        var body: some View {
            Text("x")
                .accessibilityIdentifier("cell_\\(item.key)")
        }
    }
    """)
    let els = elements(r, screen: "HomeView")
    #expect(els.count == 1)
    #expect(els[0].resolutionType == .dynamic)
}

@Test func customModifierPatternRecognized() throws {
    // 12.1：自定义 ViewModifier 的常见模式（.mtaID("x") 一类）
    let r = scanSwift("""
    struct LoginView: View {
        var body: some View {
            Text("x").mtaID("custom_id")
        }
    }
    """)
    let els = elements(r, screen: "LoginView")
    #expect(els.count == 1)
    #expect(els[0].id == "custom_id")
    #expect(els[0].resolutionType == .literal)
}

@Test func screenModifierRegistersScreenNotElement() throws {
    // 12.1：mtaScreen 是 Screen marker，不产出元素
    let r = scanSwift("""
    struct LoginView: View {
        var body: some View {
            VStack {
                Text("x")
            }
            .mtaScreen("LoginView")
        }
    }
    """)
    #expect(r.screens.contains("LoginView"))
    #expect(elements(r, screen: "LoginView").isEmpty)
}

@Test func elementTypesInferredFromViewConstructors() throws {
    let r = scanSwift("""
    struct LoginView: View {
        var body: some View {
            TextField("用户名", text: $u).accessibilityIdentifier("username_field")
            SecureField("密码", text: $p).accessibilityIdentifier("password_field")
            Text("标题").accessibilityIdentifier("title")
            List {}.accessibilityIdentifier("list_id")
        }
    }
    """)
    let els = elements(r, screen: "LoginView")
    let byID = Dictionary(uniqueKeysWithValues: els.map { ($0.id, $0) })
    #expect(byID["username_field"]?.type == "textfield")
    #expect(byID["password_field"]?.type == "securetextfield")
    #expect(byID["title"]?.type == "text")
    #expect(byID["list_id"]?.type == "list")
}

@Test func lineNumbersArePerExpressionNotChainHead() throws {
    // 多行 modifier 链：每个 identifier 的行号必须是它自己那一行（曾全报链首行）
    let r = scanSwift("""
    struct LoginView: View {
        var body: some View {
            Text("x")
                .accessibilityIdentifier("a")
            Text("y")
                .accessibilityIdentifier("b")
        }
    }
    """)
    let els = elements(r, screen: "LoginView")
    let byID = Dictionary(uniqueKeysWithValues: els.map { ($0.id, $0) })
    #expect(byID["a"]?.source.line == 4)
    #expect(byID["b"]?.source.line == 6)
    #expect(byID["a"]?.source.line != byID["b"]?.source.line)
}

@Test func uiKitAssignmentPatternRecognized() throws {
    let r = scanSwift("""
    class LoginViewController: UIViewController {
        func setup() {
            self.usernameField.accessibilityIdentifier = "username_field"
            self.loginBtn.accessibilityLabel = "登录"
        }
    }
    """)
    let els = elements(r, screen: "LoginViewController")
    #expect(els.count == 1)
    #expect(els[0].id == "username_field")
    #expect(els[0].containerType == "LoginViewController")
}

@Test func nestedStructContainerTracksInnermost() throws {
    let r = scanSwift("""
    struct OuterView: View {
        struct InnerView: View {
            var body: some View {
                Text("x").accessibilityIdentifier("inner_id")
            }
        }
    }
    """)
    let els = elements(r, screen: "InnerView")
    #expect(els.count == 1)
    #expect(els[0].containerType == "InnerView")
}
