import SwiftUI

struct A11y {
    static let loginButton = "login_button"
    static let usernameField = "username_field"
    // 同名两处 → 歧义，必须剔除（不得猜）
    static let ambiguous = "one"
}

struct Other {
    static let ambiguous = "two"
    // 非字符串初值 → 不收表
    static let count = 42
    static let computed: String = "x_" + "y"
    static let interpolated = "prefix_\(3)"
    // var 不收（可变无唯一值语义）
    static var mutable = "mutable_value"
}
